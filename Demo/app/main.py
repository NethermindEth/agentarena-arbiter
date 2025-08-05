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

from app.models.finding_db import Status
from app.config import config, Settings
from app.models.finding_input import FindingInput
from app.database.mongodb_handler import mongodb
from app.core.deduplication import FindingDeduplication
from app.core.evaluation import FindingEvaluator
from app.task_utils import fetch_task_details, download_repository, read_and_concatenate_files
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

# Initialize task cache
task_cache = TaskCache()
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
            "processed_at": current_time.isoformat(),
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
        
        # Initial fetch and cache of data
        await set_task_cache(config)
        await set_agent_data(config)

        # --------------------------------------------------------------
        # Schedule periodic refreshers, so caches stay reasonably fresh
        # --------------------------------------------------------------
        async def _periodic_task_cache_refresh():
            while True:
                try:
                    await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
                    await set_task_cache(config)
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

async def set_task_cache(config: Settings):
    """Fetch file contents from external API and cache in memory."""
    if not config.backend_task_details_endpoint or not config.backend_task_repository_endpoint or not config.backend_api_key or not config.task_id:
        logger.warning("BACKEND_TASK_DETAILS_ENDPOINT, BACKEND_TASK_REPOSITORY_ENDPOINT, BACKEND_API_KEY, or TASK_ID not configured, skipping file contents fetch")
        return

    # Construct the full endpoint URL with the taskId
    task_details_url = f"{config.backend_task_details_endpoint}/{config.task_id}"
    task_repository_url = f"{config.backend_task_repository_endpoint}/{config.task_id}"
    logger.info(f"Task details URL: {task_details_url}")
    logger.info(f"Task repository URL: {task_repository_url}")

    # Fetch task details to get selected files
    logger.info(f"Fetching task details for task {config.task_id}")
    task_details = await fetch_task_details(task_details_url, config)
    if not task_details:
        logger.error(f"Failed to get task details for task {config.task_id}")
        return

    selected_files = task_details.selectedFiles or []
    if not selected_files:
        logger.warning(f"No files selected for task {config.task_id}")
        return
        
    # Download and extract repository
    repo_dir, temp_dir = await download_repository(task_repository_url, config)
    if not repo_dir or not temp_dir:
        logger.error(f"Failed to download repository for task {config.task_id}")
        return
    
    # Store repository path for future use
    repo_storage_path = os.path.join(config.data_dir, f"repo_{config.task_id}")
    if not os.path.exists(config.data_dir):
        os.makedirs(config.data_dir, exist_ok=True)
        
    # If the repository already exists for this task, remove it and update
    if os.path.exists(repo_storage_path):
        shutil.rmtree(repo_storage_path)

    # Copy the extracted repository to a persistent location
    shutil.copytree(repo_dir, repo_storage_path)
    logger.info(f"Repository for task {config.task_id} stored at {repo_storage_path}")
    
    # Remove the temporary directory
    shutil.rmtree(temp_dir)

    # Read and concatenate selected files
    concatenated_contracts = read_and_concatenate_files(repo_storage_path, selected_files)
    if not concatenated_contracts:
        logger.warning(f"No valid contracts content found for task {config.task_id}")
        return
    
    # Read and concatenate selected docs
    selected_docs = task_details.selectedDocs or []
    concatenated_docs = read_and_concatenate_files(repo_storage_path, selected_docs)
    if not concatenated_docs:
        logger.warning(f"No valid docs content found for task {config.task_id}")
        # Continue anyway as docs are optional
    
    global task_cache
    try:
        task_cache = TaskCache(
            taskId=config.task_id,
            startTime=datetime.fromtimestamp(float(task_details.startTime), tz=timezone.utc),
            deadline=datetime.fromtimestamp(float(task_details.deadline), tz=timezone.utc),
            selectedFilesContent=concatenated_contracts,
            selectedDocsContent=concatenated_docs,
            additionalLinks=task_details.additionalLinks,
            additionalDocs=task_details.additionalDocs,
            qaResponses=task_details.qaResponses
        )
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid timestamp format for task {config.task_id}: startTime={task_details.startTime}, deadline={task_details.deadline} - {str(e)}")
        return

    logger.info(f"Task cache set successfully for task {config.task_id}")

    # Schedule task processing
    await schedule_task_processing(config.task_id, task_cache.startTime, task_cache.deadline)

async def set_agent_data(config: Settings):
    """Fetch agent data from external API and cache in memory."""
    if not config.backend_agents_endpoint or not config.backend_api_key:
        logger.warning("BACKEND_AGENTS_ENDPOINT or BACKEND_API_KEY not configured, skipping agent data fetch")
        return
        
    async with httpx.AsyncClient() as client:
        headers = {"X-API-Key": config.backend_api_key}
        response = await client.get(config.backend_agents_endpoint, headers=headers)
        
        if response.status_code == 200:
            global agents_cache
            agents_cache = response.json()
            logger.info(f"Agent data fetched and cached. Count: {len(agents_cache)}")
        else:
            logger.error(f"Failed to fetch agent data. Status code: {response.status_code}, Response: {response.text}")

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
        pending_findings = await mongodb.get_pending_task_findings(task_id)
        
        if not pending_findings:
            logger.info(f"No pending task findings found for task_id: {task_id}")
            return
            
        logger.info(f"Pending findings: {pending_findings}")

        logger.info(f"Found {len(pending_findings)} pending findings for task_id: {task_id}")
        
        # Step 1: Identify duplicates
        logger.info(f"Starting deduplication for task_id: {task_id}")

        dedup_results = await deduplicator.process_findings(task_id, pending_findings)
        duplicate_relationships = dedup_results["deduplication"]["duplicate_relationships"]

        logger.info(f"Deduplication completed for task_id: {task_id}: {dedup_results['summary']['originals_found']} originals, {dedup_results['summary']['duplicates_found']} duplicates")
        
        # Step 2: Re-fetch pending findings after deduplication to get updated statuses
        deduplicated_findings = await mongodb.get_task_findings(task_id)
        logger.info(f"Re-fetched {len(deduplicated_findings)} pending findings for task_id: {task_id}")
        
        # Step 3: Evaluate findings in batches, keeping duplicates together
        logger.info(f"Starting batch evaluation for task_id: {task_id}")
        evaluation_results = await evaluator.evaluate_all_findings(
            task_id,
            deduplicated_findings,
            duplicate_relationships
        )
        logger.info(f"Evaluation completed for task_id: {task_id}: {evaluation_results['application_results']['valid_count']} valid, {evaluation_results['application_results']['disputed_count']} disputed")
        
        # Step 4: Post results to backend endpoint (existing logic)
        try:
            # Get all agents who had findings processed
            agent_ids = set(finding.agent_id for finding in pending_findings)
            
            for agent_id in agent_ids:
                # Get the timestamp of the last sync (will be None if not found)
                last_sync_key = f"last_sync_{task_id}_{agent_id}"
                last_sync = await mongodb.get_metadata(last_sync_key)
                last_sync_time = last_sync.get("timestamp") if last_sync else None
                
                # Fetch findings created after the last sync time
                if last_sync_time:
                    # Get findings created after the last sync
                    latest_findings = await mongodb.get_agent_findings_since(
                        task_id, 
                        agent_id, 
                        last_sync_time
                    )
                    logger.info(f"Found {len(latest_findings)} new findings since last sync at {last_sync_time} for task_id: {task_id}, agent_id: {agent_id}")
                else:
                    # If there's no last sync time, get all findings
                    latest_findings = await mongodb.get_agent_findings(task_id, agent_id)
                    logger.info(f"No previous sync found. Syncing all {len(latest_findings)} findings for task_id: {task_id}, agent_id: {agent_id}")

                # Skip if there are no findings to sync
                if not latest_findings:
                    logger.info(f"No new findings to sync with backend endpoint for task_id: {task_id}, agent_id: {agent_id}")
                    continue

                # Format findings data for the backend endpoint
                formatted_findings = []
                current_sync_time = datetime.now(timezone.utc)
                
                for finding in latest_findings:
                    formatted_findings.append({
                        "title": finding.title,
                        "description": finding.description,
                        "severity": finding.severity,
                        "status": finding.status,
                        "file_paths": finding.file_paths,
                        "created_at": finding.created_at.isoformat()
                    })


                # Prepare payload for backend endpoint
                payload = {
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "findings": formatted_findings
                }
                
                # Post to backend endpoint
                backend_endpoint = config.backend_findings_endpoint
                if backend_endpoint:
                    async with httpx.AsyncClient() as client:
                        headers = {"X-API-Key": config.backend_api_key}
                        response = await client.post(backend_endpoint, json=payload, headers=headers)
                        logger.debug(f"Backend API response for task_id: {task_id}, agent_id: {agent_id}: {response.json()}")
                        logger.debug(f"Backend API status code for task_id: {task_id}, agent_id: {agent_id}: {response.status_code}")
                        
                        # If successful, update the last sync timestamp
                        if response.status_code == 200:
                            await mongodb.set_metadata(last_sync_key, {"timestamp": current_sync_time})
                            logger.info(f"Updated last sync timestamp to {current_sync_time} for task_id: {task_id}, agent_id: {agent_id}")
                        else:
                            logger.error(f"Failed to post findings to backend. Status code: {response.status_code}, Response: {response.text}")
                else:
                    # If external API is not configured but in testing mode, still update timestamp
                    if config.testing:
                        await mongodb.set_metadata(last_sync_key, {"timestamp": current_sync_time})
                        logger.info(f"TESTING MODE: No backend API configured, but updated sync timestamp to {current_sync_time} for task_id: {task_id}, agent_id: {agent_id}")
                    else:
                        logger.warning(f"BACKEND_FINDINGS_ENDPOINT not configured, skipping backend post for task_id: {task_id}, agent_id: {agent_id}")
            
            logger.info(f"Task processing completed successfully for task_id: {task_id}")
            logger.info(f"Processing summary - Original findings: {dedup_results['summary']['originals_found']}, Duplicates: {dedup_results['summary']['duplicates_found']}, Evaluated: {evaluation_results['application_results']['disputed_count']} disputed")
            
        except Exception as post_error:
            logger.error(f"Error posting results to backend for task_id: {task_id}: {str(post_error)}")
            
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error during task processing for task_id: {task_id}: {str(e)}")
        logger.error(f"Traceback for task_id: {task_id}: {error_trace}")

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
        global task_cache
        if not task_cache or task_cache.taskId != input_data.task_id:
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
        
        # Check if testing mode is enabled and agents_cache is empty
        if config.testing and not agents_cache:
            agent_id = "test-agent"
            logger.info(f"Testing mode enabled, using test agent_id: {agent_id}")
        elif not agents_cache:
            # If not in testing mode and agents_cache is empty, reject the request
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
        
        # TODO: 4. Validate findings

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

@app.get("/tasks/{task_id}/findings", response_model=List[Dict[str, Any]])
async def get_task_findings(task_id: str):
    """
    Get all findings for a task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        List of findings for the task
    """
    try:
        findings = await mongodb.get_task_findings(task_id)
        return [finding.model_dump() for finding in findings]
    except Exception as e:
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
        pending_findings = await mongodb.get_pending_task_findings(task_id)
        
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
            "processed_at": current_time.isoformat(),
            "scheduled_processing": False,
            "manual_trigger": True
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8004, reload=True)
