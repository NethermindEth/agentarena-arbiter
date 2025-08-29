"""
Main FastAPI application for security findings management.
Provides API endpoints for submitting and managing security findings.
"""

import sys
from contextlib import asynccontextmanager
import traceback
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List
import httpx
import shutil
import os
from datetime import datetime, timezone
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from app.models.finding_db import FindingDB, Status
from app.config import config, Settings
from app.models.finding_input import FindingInput
from app.database.mongodb_handler import mongodb
from app.core.deduplication import FindingDeduplication
from app.core.evaluation import FindingEvaluator
from app.task_utils import fetch_submitted_tasks, download_repository, read_and_concatenate_files
import logging
from app.types import TaskCache

# Initialize logger
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

# Configure logging to both console and file
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format=log_format,
    handlers=[
        logging.StreamHandler()
    ]
)

# Initialize handlers
deduplicator = FindingDeduplication()
evaluator = FindingEvaluator()

# Initialize task cache map (task_id -> TaskCache)
task_cache_map: Dict[str, TaskCache] = {}
agents_cache = []

# Initialize APScheduler for task processing jobs
scheduler = AsyncIOScheduler()

# Interval for refreshing caches (in seconds)
REFRESH_INTERVAL_SECONDS = 600  # 10 minutes

# Keep references to background tasks so we can cancel them on shutdown
refresh_tasks = []  # type: List[asyncio.Task]

async def schedule_task_processing(task_id: str, start_time: datetime, deadline: datetime):
    """
    Schedule a job to process task findings when the deadline is reached.
    
    Args:
        task_id: Task identifier
        start_time: Task start time as datetime object
        deadline: Task deadline as datetime object
    """
    try:
        # Validate that start time is before deadline
        if start_time >= deadline:
            logger.error(f"Invalid task timing for {task_id}: start time {start_time} is not before deadline {deadline}")
            return
        
        # Create a unique job ID
        job_id = f"task_{task_id}"
        
        # Remove any existing job for this task
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            logger.info(f"Removed existing task processing job for task {task_id}")
        
        # Schedule the new job
        scheduler.add_job(
            process_task_scheduled,
            trigger=DateTrigger(run_date=deadline),
            args=[task_id],
            id=job_id,
            name=f"Process task {task_id}",
            misfire_grace_time=300  # Allow 5 minutes grace time if system is busy
        )
        
        logger.info(f"Scheduled task processing for task {task_id}: start={start_time.isoformat()}, deadline={deadline.isoformat()}")
        
    except Exception as e:
        logger.error(f"Error scheduling task processing job for task {task_id}: {str(e)}")

async def process_task_scheduled(task_id: str):
    """
    Wrapper function for scheduled task processing.
    Includes additional checks and metadata management.
    
    Args:
        task_id: Task identifier
    """
    try:
        logger.info(f"Scheduled task processing triggered for task {task_id}")
        
        # Check if this task has already been processed
        processed_key = f"task_{task_id}"
        processed_metadata = await mongodb.get_metadata(processed_key)
        
        if processed_metadata:
            logger.info(f"Task {task_id} was already processed at {processed_metadata.get('processed_at')}. Skipping.")
            return
        
        # Process the task findings
        await process_task(task_id)
        
        # Mark this task as processed
        current_time = datetime.now(timezone.utc)
        await mongodb.set_metadata(processed_key, {
            "processed_at": current_time,
            "scheduled_processing": True
        })
        
        logger.info(f"Marked task {task_id} as processed via scheduled job")
        
    except Exception as e:
        logger.error(f"Error in scheduled task processing for task {task_id}: {str(e)}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    try:
        await mongodb.connect()
        logger.info("✅ Connected to MongoDB")
        
        # Start the scheduler
        scheduler.start()
        logger.info("✅ Started APScheduler for task processing jobs")
        
        try:
            # Initial fetch and cache of data
            await set_task_caches(config)
            await set_agent_data(config)
        except Exception as e:
            logger.error(f"Error during initial cache setup: {str(e)}")

        # --------------------------------------------------------------
        # Schedule periodic refreshers, so caches stay reasonably fresh
        # --------------------------------------------------------------
        async def _periodic_task_cache_refresh():
            while True:
                try:
                    await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
                    await set_task_caches(config)
                except Exception as e:
                    logger.error(f"Error refreshing task cache: {str(e)}")
                

        async def _periodic_agent_cache_refresh():
            while True:
                try:
                    await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
                    await set_agent_data(config)
                except Exception as e:
                    logger.error(f"Error refreshing agents cache: {str(e)}")

        # Start background tasks and store references
        refresh_tasks.extend([
            asyncio.create_task(_periodic_task_cache_refresh()),
            asyncio.create_task(_periodic_agent_cache_refresh()),
        ])
        
        yield
        
        # Shutdown
        # Cancel background refresher tasks
        for task in refresh_tasks:
            task.cancel()
        # Wait for cancellation (ignore errors)
        if refresh_tasks:
            await asyncio.gather(*refresh_tasks, return_exceptions=True)

        await mongodb.close()
        logger.info("✅ Disconnected from MongoDB")
        
    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")
        sys.exit(1)  # Exit the application with a non-zero status to indicate an error

# Initialize FastAPI app after lifespan function definition
app = FastAPI(
    title="Security Findings API",
    description="API for managing security findings and deduplication",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

async def set_task_caches(config: Settings):
    """Fetch file contents for submitted tasks and cache per task_id."""
    if not config.backend_api_key or not config.backend_task_repository_endpoint or not config.backend_submitted_tasks_endpoint:
        logger.warning("BACKEND_API_KEY or BACKEND_TASK_REPOSITORY_ENDPOINT or BACKEND_SUBMITTED_TASKS_ENDPOINT not configured, skipping cache update")
        return

    logger.info(f"Fetching submitted tasks from {config.backend_submitted_tasks_endpoint}")
    tasks = await fetch_submitted_tasks(config.backend_submitted_tasks_endpoint, config)
    if not tasks:
        logger.warning("No submitted tasks returned; skipping cache update")
        return

    if not os.path.exists(config.data_dir):
        os.makedirs(config.data_dir, exist_ok=True)

    updated_map: Dict[str, TaskCache] = {}

    for task in tasks:
        try:
            task_id = task.taskId
            if not task_id:
                logger.warning("Encountered task with missing taskId; skipping")
                continue

            selected_files = task.selectedFiles or []
            if not selected_files:
                logger.warning(f"No files selected for task {task_id}; skipping")
                continue

            # Download and extract repository
            repo_dir, temp_dir = await download_repository(f"{config.backend_task_repository_endpoint}/{task_id}", config)
            if not repo_dir or not temp_dir:
                logger.error(f"Failed to download repository for task {task_id}")
                continue

            # Store repository in data directory
            repo_storage_path = os.path.join(config.data_dir, f"repo_{task_id}")
            if os.path.exists(repo_storage_path):
                shutil.rmtree(repo_storage_path)
            shutil.copytree(repo_dir, repo_storage_path)
            logger.info(f"Repository for task {task_id} stored at {repo_storage_path}")
            shutil.rmtree(temp_dir)

            concatenated_contracts = read_and_concatenate_files(repo_storage_path, selected_files)
            if not concatenated_contracts:
                logger.warning(f"No valid contracts content found for task {task_id}; skipping")
                continue

            selected_docs = task.selectedDocs or []
            concatenated_docs = read_and_concatenate_files(repo_storage_path, selected_docs) if selected_docs else ""
            if not concatenated_docs:
                logger.warning(f"No valid docs content found for task {task_id}")

            cache_entry = TaskCache(
                taskId=task_id,
                startTime=datetime.fromtimestamp(float(task.startTime), tz=timezone.utc),
                deadline=datetime.fromtimestamp(float(task.deadline), tz=timezone.utc),
                selectedFilesContent=concatenated_contracts,
                selectedDocsContent=concatenated_docs,
                additionalLinks=task.additionalLinks,
                additionalDocs=task.additionalDocs,
                qaResponses=task.qaResponses
            )

            updated_map[task_id] = cache_entry
        except (ValueError, TypeError) as te:
            logger.error(f"Invalid timestamp format for task {getattr(task, 'taskId', 'unknown')}: startTime={getattr(task, 'startTime', None)}, deadline={getattr(task, 'deadline', None)} - {str(te)}")
            continue
        except Exception as e:
            logger.error(f"Error building cache for task {getattr(task, 'taskId', 'unknown')}: {str(e)}")
            continue

    # Atomically replace
    global task_cache_map
    task_cache_map = updated_map
    logger.info(f"Set task caches for {len(task_cache_map)} task(s): {list(task_cache_map.keys())}")

    # Schedule processing for each task, except TESTTASK
    for task_id, task_cache in task_cache_map.items():
        if task_id != "TESTTASK":
            await schedule_task_processing(task_id, task_cache.startTime, task_cache.deadline)

async def set_agent_data(config: Settings):
    """Fetch agent data from external API and cache in memory."""
    if not config.backend_agents_endpoint or not config.backend_api_key:
        logger.warning("BACKEND_AGENTS_ENDPOINT or BACKEND_API_KEY not configured, skipping agent data fetch")
        return
    
    try:
        async with httpx.AsyncClient() as client:
            headers = {"X-API-Key": config.backend_api_key}
            response = await client.get(config.backend_agents_endpoint, headers=headers)
            
            if response.status_code == 200:
                global agents_cache
                agents_cache = response.json()
                logger.info(f"Agent data fetched and cached. Count: {len(agents_cache)}")
            else:
                logger.error(f"Failed to fetch agent data. Status code: {response.status_code}, Response: {response.text}")
    except Exception as e:
        logger.error(f"Error fetching agent data: {str(e)}")
        return

async def process_task(task_id: str):
    """
    Process all findings for a task that has ended.
    This function is called automatically when a task deadline passes.
    Uses the Gemini-based deduplication and batch evaluation system.
    
    Args:
        task_id: Task identifier for the ended task
    """
    try:
        logger.info(f"Processing task findings for task_id: {task_id}")
        
        # Get all pending task findings for this task
        pending_findings = await mongodb.get_findings(task_id=task_id, status=Status.PENDING)
        
        if not pending_findings:
            logger.info(f"No pending task findings found for task_id: {task_id}")
            return
        
        logger.info(f"Found {len(pending_findings)} pending findings for task_id: {task_id}")
        
        task_cache = task_cache_map.get(task_id)
        if not task_cache:
            logger.error(f"Task cache not available for task_id: {task_id}. Evaluation cannot proceed without smart contract context.")
            return
        
        # Step 1: Identify duplicates
        logger.info(f"Starting deduplication for task_id: {task_id}")

        dedup_results = await deduplicator.process_findings(task_id, pending_findings, task_cache)
        duplicate_relationships = dedup_results["deduplication"]["duplicate_relationships"]

        logger.info(
            f"Deduplication completed for task_id: {task_id}: "
            f"{dedup_results['summary']['originals_found']} originals, "
            f"{dedup_results['summary']['duplicates_found']} duplicates"
        )

        # Step 2: Re-fetch pending findings after deduplication to get updated statuses
        deduplicated_findings = await mongodb.get_findings(task_id=task_id)
        logger.info(f"Re-fetched {len(deduplicated_findings)} deduplicated findings for task_id: {task_id}")
        
        # Step 3: Evaluate findings in batches, keeping duplicates together
        logger.info(f"Starting batch evaluation for task_id: {task_id}")

        evaluation_results = await evaluator.evaluate_all_findings(
            task_id,
            deduplicated_findings,
            duplicate_relationships,
            task_cache
        )

        logger.info(
            f"Evaluation completed for task_id: {task_id}: "
            f"{evaluation_results['application_results']['valid_count']} valid, "
            f"{evaluation_results['application_results']['disputed_count']} disputed, "
            f"{evaluation_results['application_results']['failed_count']} failed to update"
        )
        
        # Step 4: Post results to backend endpoint
        try:
            # Get all findings for this task from all agents
            all_task_findings = await mongodb.get_findings(task_id=task_id)
            
            if not all_task_findings:
                logger.info(f"No findings to sync with backend endpoint for task_id: {task_id}")
            else:
                logger.info(f"Syncing all {len(all_task_findings)} findings for task_id: {task_id} in one batch")

                # Format findings data for the backend endpoint
                formatted_findings = []
                
                for finding in all_task_findings:
                    formatted_findings.append({
                        "id": finding.str_id,
                        "agent_id": finding.agent_id,
                        "title": finding.title,
                        "description": finding.description,
                        "severity": finding.severity,
                        "status": finding.status,
                        "file_paths": finding.file_paths,
                        "duplicate_of": finding.duplicateOf if finding.duplicateOf else None,
                        "created_at": finding.created_at.isoformat()
                    })

                # Prepare payload for backend endpoint
                payload = {
                    "task_id": task_id,
                    "findings": formatted_findings
                }
                
                # Post to backend endpoint
                backend_endpoint = config.backend_findings_endpoint
                if backend_endpoint:
                    async with httpx.AsyncClient() as client:
                        headers = {"X-API-Key": config.backend_api_key}
                        response = await client.post(backend_endpoint, json=payload, headers=headers)
                        logger.debug(f"Backend API response for task_id: {task_id}: {response.json()}")
                        logger.debug(f"Backend API status code for task_id: {task_id}: {response.status_code}")
                        
                        if response.status_code == 200:
                            logger.info(f"Successfully posted {len(formatted_findings)} findings to backend for task_id: {task_id}")
                        else:
                            logger.error(f"Failed to post findings to backend. Status code: {response.status_code}, Response: {response.text}")
                else:
                    logger.warning(f"BACKEND_FINDINGS_ENDPOINT not configured, skipping backend post for task_id: {task_id}")
            
            logger.info(f"Task processing completed successfully for task_id: {task_id}")
            logger.info(f"Processing summary: "
                        f"Duplicates: {dedup_results['summary']['duplicates_found']}, "
                        f"Disputed: {evaluation_results['application_results']['disputed_count']}, "
                        f"Failed: {evaluation_results['application_results']['failed_count']}, "
                        f"Total: {len(pending_findings)}")
            
        except Exception as post_error:
            logger.error(f"Error posting results to backend for task_id: {task_id}: {str(post_error)}")
            
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error during task processing for task_id: {task_id}: {str(e)}")
        logger.error(f"Traceback for task_id: {task_id}: {error_trace}")

async def process_task_for_agent(task_id: str, agent_id: str):
    """
    Process only the pending findings for a specific agent and task.
    Intended for the TESTTASK flow where submissions are processed immediately.
    """
    try:
        logger.info(f"Processing task findings for task_id: {task_id}, agent_id: {agent_id}")

        # Get pending task findings for this agent
        pending_findings = await mongodb.get_findings(task_id=task_id, agent_id=agent_id, status=Status.PENDING)

        if not pending_findings:
            logger.info(f"No pending findings for task_id: {task_id}, agent_id: {agent_id}")
            return

        logger.info(f"Found {len(pending_findings)} pending findings for task_id: {task_id}, agent_id: {agent_id}")

        task_cache = task_cache_map.get(task_id)
        if not task_cache:
            logger.error(
                f"Task cache not available for task_id: {task_id}. "
                f"Evaluation cannot proceed without smart contract context."
            )
            return

        # Deduplicate only this agent's pending findings
        logger.info(f"Starting deduplication for task_id: {task_id}, agent_id: {agent_id}")
        dedup_results = await deduplicator.process_findings(task_id, pending_findings, task_cache)
        duplicate_relationships = dedup_results["deduplication"]["duplicate_relationships"]
        logger.info(
            f"Deduplication completed for task_id: {task_id}, agent_id: {agent_id}: "
            f"{dedup_results['summary']['originals_found']} originals, "
            f"{dedup_results['summary']['duplicates_found']} duplicates"
        )

        # Re-fetch findings for this agent after deduplication
        deduplicated_findings = await get_latest_findings(task_id, agent_id)
        logger.info(
            f"Re-fetched {len(deduplicated_findings)} deduplicated findings for task_id: {task_id}, agent_id: {agent_id}"
        )

        # Evaluate only this agent's findings
        logger.info(f"Starting evaluation for task_id: {task_id}, agent_id: {agent_id}")
        evaluation_results = await evaluator.evaluate_all_findings(
            task_id,
            deduplicated_findings,
            duplicate_relationships,
            task_cache
        )
        logger.info(
            f"Evaluation completed for task_id: {task_id}, agent_id: {agent_id}: "
            f"{evaluation_results['application_results']['valid_count']} valid, "
            f"{evaluation_results['application_results']['disputed_count']} disputed, "
            f"{evaluation_results['application_results']['failed_count']} failed to update"
        )

        # Post only this agent's findings to backend, honoring last-sync for TESTTASK
        try:
            latest_findings = await get_latest_findings(task_id, agent_id)

            if not latest_findings:
                logger.info(
                    f"No new findings to sync with backend endpoint for task_id: {task_id}, agent_id: {agent_id}"
                )
                return

            formatted_findings = []
            current_sync_time = datetime.now(timezone.utc)
            for finding in latest_findings:
                formatted_findings.append({
                    "id": finding.str_id,
                    "agent_id": finding.agent_id,
                    "title": finding.title,
                    "description": finding.description,
                    "severity": finding.severity,
                    "status": finding.status,
                    "file_paths": finding.file_paths,
                    "duplicate_of": finding.duplicateOf if finding.duplicateOf else None,
                    "created_at": finding.created_at.isoformat()
                })

            payload = {
                "task_id": task_id,
                "findings": formatted_findings
            }

            backend_endpoint = config.backend_findings_endpoint
            if backend_endpoint:
                async with httpx.AsyncClient() as client:
                    headers = {"X-API-Key": config.backend_api_key}
                    response = await client.post(backend_endpoint, json=payload, headers=headers)
                    logger.debug(
                        f"Backend API response for task_id: {task_id}, agent_id: {agent_id}: {response.json()}"
                    )
                    logger.debug(
                        f"Backend API status code for task_id: {task_id}, agent_id: {agent_id}: {response.status_code}"
                    )

                    if response.status_code == 200:
                        last_sync_key = f"last_sync_{task_id}_{agent_id}"
                        await mongodb.set_metadata(last_sync_key, {"timestamp": current_sync_time})
                        logger.info(
                            f"Updated last sync timestamp to {current_sync_time} for task_id: {task_id}, agent_id: {agent_id}"
                        )
                    elif response.status_code != 200:
                        logger.error(
                            f"Failed to post findings to backend. Status code: {response.status_code}, Response: {response.text}"
                        )
            else:
                logger.warning(
                    f"BACKEND_FINDINGS_ENDPOINT not configured, skipping backend post for task_id: {task_id}, agent_id: {agent_id}"
                )

            logger.info(f"Task processing completed successfully for task_id: {task_id}, agent_id: {agent_id}")
            logger.info(f"Processing summary: "
                        f"Duplicates: {dedup_results['summary']['duplicates_found']}, "
                        f"Disputed: {evaluation_results['application_results']['disputed_count']}, "
                        f"Failed: {evaluation_results['application_results']['failed_count']}, "
                        f"Total: {len(pending_findings)}")
        except Exception as post_error:
            logger.error(
                f"Error posting results to backend for task_id: {task_id}, agent_id: {agent_id}: {str(post_error)}"
            )

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(
            f"Error during agent-specific task processing for task_id: {task_id}, agent_id: {agent_id}: {str(e)}"
        )
        logger.error(f"Traceback for task_id: {task_id}, agent_id: {agent_id}: {error_trace}")

async def get_latest_findings(task_id: str, agent_id: str) -> List[FindingDB]:
    last_sync_key = f"last_sync_{task_id}_{agent_id}"
    last_sync = await mongodb.get_metadata(last_sync_key)
    last_sync_time = last_sync.get("timestamp") if last_sync else None

    if last_sync_time:
        latest_findings = await mongodb.get_findings(
                        task_id=task_id,
                        agent_id=agent_id,
                        since_timestamp=last_sync_time
                    )
        logger.info(f"Found {len(latest_findings)} new findings since last sync at {last_sync_time} "
                    f"for task_id: {task_id}, agent_id: {agent_id}")
    else:
        latest_findings = await mongodb.get_findings(task_id=task_id, agent_id=agent_id)
        logger.info(f"No previous sync found. Fetched all {len(latest_findings)} findings for "
                    f"task_id: {task_id}, agent_id: {agent_id}")
        
    return latest_findings

@app.on_event("shutdown")
async def shutdown():
    """Disconnect from MongoDB on shutdown."""
    # Cancel background refresher tasks
    for task in refresh_tasks:
        task.cancel()
    # Wait for cancellation (ignore errors)
    if refresh_tasks:
        await asyncio.gather(*refresh_tasks, return_exceptions=True)

    # Shutdown the scheduler
    try:
        scheduler.shutdown()
        logger.info("✅ Shut down APScheduler")
    except Exception as e:
        logger.error(f"Error shutting down scheduler: {str(e)}")

    await mongodb.close()
    logger.info("✅ Disconnected from MongoDB")

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Welcome to the ArbiterAgent API!"}

async def post_submission(task_id: str, agent_id: str, findings_count: int):
    """
    Post the number of findings being processed to the backend endpoint.
    
    Args:
        task_id: Task identifier
        agent_id: Agent identifier  
        findings_count: Number of findings being processed
    """
    try:
        # Check if we have a submissions endpoint configured
        submissions_endpoint = config.backend_submissions_endpoint
        if not submissions_endpoint:
            logger.warning("BACKEND_SUBMISSIONS_ENDPOINT not configured, skipping submission post")
            return
            
        payload = {
            "task_id": task_id,
            "agent_id": agent_id,
            "findings_count": findings_count
        }
        
        async with httpx.AsyncClient() as client:
            headers = {"X-API-Key": config.backend_api_key}
            response = await client.post(submissions_endpoint, json=payload, headers=headers)
            
            if response.status_code == 200:
                logger.info(f"Successfully posted submission: {findings_count} findings for task {task_id}, agent {agent_id}")
            else:
                logger.error(f"Failed to post submission. Status code: {response.status_code}, Response: {response.text}")
                
    except Exception as e:
        logger.error(f"Error posting submission to backend: {str(e)}")

@app.post("/process_findings")
async def process_findings(
    input_data: FindingInput,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    Submit security findings for processing.
    Validates input and stores findings during submission timeframe.
    Findings are only processed after the task deadline.
    
    Args:
        input_data: Batch of findings with task_id and findings list
        background_tasks: FastAPI background tasks handler
        x_api_key: API key for authentication
        
    Returns:
        Processing confirmation
    """
    try:
        # 1. Basic validation
        if len(input_data.findings) > config.max_findings_per_submission:
            raise HTTPException(
                status_code=400, 
                detail=f"Submission contains too many findings. Maximum allowed: {config.max_findings_per_submission} findings per submission."
            )
            
        # 2. Check if we're within the submission timeframe
        task_cache = task_cache_map.get(input_data.task_id)
        if not task_cache:
            raise HTTPException(
                status_code=404,
                detail=f"Task {input_data.task_id} not found in cache"
            )
        
        # Check if submission is allowed
        current_time = datetime.now(timezone.utc)
        
        if current_time < task_cache.startTime:
            raise HTTPException(
                status_code=403,
                detail=f"Submission period has not started yet. Starts at: {task_cache.startTime.isoformat()}"
            )
        
        if current_time > task_cache.deadline:
            raise HTTPException(
                status_code=403,
                detail=f"Submission period has ended. Deadline was: {task_cache.deadline.isoformat()}"
            )
            
        # 3. Verify API key and get agent_id from agents_cache
        agent_id = None
        
        if not agents_cache:
            # If agents_cache is empty, reject the request
            raise HTTPException(status_code=503, detail="Agent service unavailable. No agents configured.")
        else:
            # Verify API key against known agents
            for agent in agents_cache:
                if agent.get("api_key") == x_api_key:
                    agent_id = agent.get("agent_id")
                    break

        if not agent_id:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        logger.info(f"Accepted findings submission for task_id: {input_data.task_id}, agent_id: {agent_id}")
        
        # 4. Delete any existing findings for this agent to allow only one submission
        deleted_count = await mongodb.delete_agent_findings(input_data.task_id, agent_id)
        if deleted_count > 0:
            logger.info(f"Deleted {deleted_count} existing findings for task_id: {input_data.task_id}, agent_id: {agent_id} (overriding previous submission)")

        # 5. Store findings as pending processing
        for finding in input_data.findings:
            await mongodb.create_finding(
                task_id=input_data.task_id,
                agent_id=agent_id,
                finding=finding,
                status=Status.PENDING
            )
        
        logger.info(f"Stored {len(input_data.findings)} findings for task_id: {input_data.task_id}, agent_id: {agent_id} - awaiting task end for processing")    

        # 6. Post submission to backend
        await post_submission(input_data.task_id, agent_id, len(input_data.findings))

        # 7. Return submission summary
        return {
            "task_id": input_data.task_id,
            "agent_id": agent_id,
            "total_findings": len(input_data.findings)
        }
        
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error processing findings: {str(e)}")
        logger.error(f"Traceback: {error_trace}")
        
        # Check if this is already an HTTPException so we preserve the status code
        if isinstance(e, HTTPException):
            raise e
        # Otherwise, wrap it in a 500 error
        raise HTTPException(status_code=500, detail=f"Error processing findings: {str(e)}")

@app.post("/test/process_findings")
async def test_process_findings(
    input_data: FindingInput,
    background_tasks: BackgroundTasks,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    Submit findings for test task and queue processing in the background.
    """
    try:
        # 1. Basic validation
        if input_data.task_id != "TESTTASK":
            raise HTTPException(status_code=400, detail="Invalid test task ID")
        
        if len(input_data.findings) > config.max_findings_per_submission:
            raise HTTPException(
                status_code=400,
                detail=f"Submission contains too many findings. Maximum allowed: {config.max_findings_per_submission} findings per submission."
            )

        # 2. Verify API key and resolve agent_id
        agent_id = None
        if not agents_cache:
            raise HTTPException(status_code=503, detail="Agent service unavailable. No agents configured.")
        else:
            for agent in agents_cache:
                if agent.get("api_key") == x_api_key:
                    agent_id = agent.get("agent_id")
                    break

        if not agent_id:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # 3. Store findings as pending
        for finding in input_data.findings:
            await mongodb.create_finding(
                task_id=input_data.task_id,
                agent_id=agent_id,
                finding=finding,
                status=Status.PENDING
            )

        # 4. Post submission count
        await post_submission(input_data.task_id, agent_id, len(input_data.findings))

        # 5. Queue processing in background for only this agent (do not await)
        logger.info(
            f"Queuing background processing for test task_id: {input_data.task_id}, agent_id: {agent_id}"
        )
        background_tasks.add_task(process_task_for_agent, input_data.task_id, agent_id)

        # 6. Response (immediate)
        return {
            "task_id": input_data.task_id,
            "agent_id": agent_id,
            "total_findings": len(input_data.findings),
            "queued": True
        }

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"[TEST] Error in immediate processing: {str(e)}")
        logger.error(f"Traceback: {error_trace}")
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Error in test processing: {str(e)}")

@app.get("/tasks/{task_id}/findings", response_model=List[Dict[str, Any]])
async def get_task_findings(task_id: str, x_api_key: str = Header(..., alias="X-API-Key")):
    """
    Get all findings for a task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        List of findings for the task
    """
    try:
        if x_api_key != config.backend_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

        findings = await mongodb.get_findings(task_id)
        return [finding.model_dump() for finding in findings]
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error retrieving findings: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving findings: {str(e)}")

@app.post("/tasks/{task_id}/process")
async def trigger_task_processing(
    task_id: str,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    Manually trigger task processing for all pending findings of a task.
    This endpoint allows immediate processing without waiting for the scheduled deadline.
    
    Args:
        task_id: Task identifier for the task to process
        x_api_key: API key for authentication
        
    Returns:
        Processing status and summary
    """
    try:
        if x_api_key != config.backend_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        # Check if this task has already been processed
        processed_key = f"task_{task_id}"
        processed_metadata = await mongodb.get_metadata(processed_key)
        
        if processed_metadata:
            logger.info(f"Task {task_id} was already processed at {processed_metadata.get('processed_at')}")
            return {
                "task_id": task_id,
                "status": "already_processed",
                "message": f"Task was already processed at {processed_metadata.get('processed_at')}",
                "processed_at": processed_metadata.get('processed_at'),
                "scheduled_processing": processed_metadata.get('scheduled_processing', False)
            }
        
        # Check if there are any pending findings
        pending_findings = await mongodb.get_findings(task_id=task_id, status=Status.PENDING)
        
        if not pending_findings:
            return {
                "task_id": task_id,
                "status": "no_pending_findings",
                "message": "No pending findings found for this task",
                "total_findings": 0
            }
        
        logger.info(f"Manual task processing triggered for task: {task_id} with {len(pending_findings)} pending findings")
        
        # Process the task findings
        await process_task(task_id)
        
        # Mark this task as processed
        current_time = datetime.now(timezone.utc)
        await mongodb.set_metadata(processed_key, {
            "processed_at": current_time,
            "scheduled_processing": False
        })
        
        logger.info(f"Manual task processing completed for task: {task_id}")
        
        return {
            "task_id": task_id,
            "status": "processed",
            "message": "Task processing completed successfully",
            "processed_at": current_time.isoformat(),
            "total_pending_findings": len(pending_findings),
            "manual_trigger": True
        }
        
    except HTTPException:
        raise
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error during manual task processing for task {task_id}: {str(e)}")
        logger.error(f"Traceback: {error_trace}")
        raise HTTPException(status_code=500, detail=f"Error processing task: {str(e)}")

@app.post("/tasks/{task_id}/post")
async def post_task_findings(
    task_id: str,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    Post all findings for a task to the backend database.
    This endpoint sends all findings for the specified task to the configured backend endpoint.
    
    Args:
        task_id: Task identifier for the task to post findings for
        x_api_key: API key for authentication
        
    Returns:
        Posting status and summary
    """
    try:
        if x_api_key != config.backend_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        
        # Check if backend endpoint is configured
        if not config.backend_findings_endpoint:
            raise HTTPException(
                status_code=503, 
                detail="Backend findings endpoint not configured"
            )
        
        # Get all findings for this task
        all_findings = await mongodb.get_findings(task_id=task_id)
        
        if not all_findings:
            return {
                "task_id": task_id,
                "status": "no_findings",
                "message": "No findings found for this task",
                "total_findings": 0
            }
        
        formatted_findings = []
        for finding in all_findings:
            formatted_findings.append({
                "id": finding.str_id,
                "agent_id": finding.agent_id,
                "title": finding.title,
                "description": finding.description,
                "severity": finding.severity,
                "status": finding.status,
                "file_paths": finding.file_paths,
                "duplicate_of": finding.duplicateOf if finding.duplicateOf else None,
                "created_at": finding.created_at.isoformat()
            })

        # Prepare batched payload for backend endpoint
        payload = {
            "task_id": task_id,
            "findings": formatted_findings
        }
        
        try:
            # Post all findings to the backend endpoint
            async with httpx.AsyncClient() as client:
                headers = {"X-API-Key": config.backend_api_key}
                response = await client.post(config.backend_findings_endpoint, json=payload, headers=headers)
                
                if response.status_code == 200:
                    logger.info(f"Successfully posted {len(all_findings)} findings for task {task_id}")
                else:
                    logger.error(f"Failed to post findings for task {task_id}. Status code: {response.status_code}, Response: {response.text}")
                    
        except Exception as batch_error:
            logger.error(f"Error posting findings batch: {str(batch_error)}")
        
        # Summary
        return {
            "task_id": task_id,
            "status": "completed",
            "message": f"Posted {len(all_findings)} findings to backend database",
        }
        
    except HTTPException:
        raise
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error posting task findings for task {task_id}: {str(e)}")
        logger.error(f"Traceback: {error_trace}")
        raise HTTPException(status_code=500, detail=f"Error posting findings: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8004, reload=True)
