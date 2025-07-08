"""
Main FastAPI application for security findings management.
Provides API endpoints for submitting and managing security findings.
"""
import traceback
from fastapi import FastAPI, HTTPException, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List
import httpx
import shutil
import os
from datetime import datetime
import asyncio  # NEW: for background refresh tasks

from app.models.finding_db import Status
from app.config import config, Settings
from app.models.finding_input import Finding, FindingInput
from app.database.mongodb_handler import mongodb
from app.core.finding_deduplication import FindingDeduplication
from app.core.final_evaluation import FindingEvaluator
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

# Initialize FastAPI
app = FastAPI(
    title="Security Findings API",
    description="API for managing security findings and deduplication",
    version="1.0.0"
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

# Initialize handlers
deduplicator = FindingDeduplication()
evaluator = FindingEvaluator()

# Initialize task cache
task_cache = TaskCache()
agents_cache = []

# Interval for refreshing caches (in seconds)
REFRESH_INTERVAL_SECONDS = 600  # 10 minutes

# Keep references to background tasks so we can cancel them on shutdown
refresh_tasks = []  # type: List[asyncio.Task]

@app.on_event("startup")
async def startup():
    """Connect to MongoDB on startup and fetch initial data."""
    try:
        await mongodb.connect()
        logger.info("✅ Connected to MongoDB")
        
        # Initial fetch and cache of data
        await set_task_cache(config)
        await set_agent_data(config)

        # -------------------------------------------------------------
        # Schedule periodic refreshers so caches stay reasonably fresh
        # -------------------------------------------------------------
        async def _periodic_task_cache_refresh():
            while True:
                try:
                    await set_task_cache(config)
                except Exception as e:
                    logger.error(f"Error refreshing task cache: {str(e)}")
                await asyncio.sleep(REFRESH_INTERVAL_SECONDS)

        async def _periodic_agent_cache_refresh():
            while True:
                try:
                    await set_agent_data(config)
                except Exception as e:
                    logger.error(f"Error refreshing agents cache: {str(e)}")
                await asyncio.sleep(REFRESH_INTERVAL_SECONDS)

        # Start background tasks and store references
        refresh_tasks.extend([
            asyncio.create_task(_periodic_task_cache_refresh()),
            asyncio.create_task(_periodic_agent_cache_refresh())
        ])
    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")
        import sys
        sys.exit(1)  # Exit the application with a non-zero status to indicate an error

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
    task_cache = TaskCache(
        taskId=config.task_id,
        selectedFilesContent=concatenated_contracts,
        selectedDocsContent=concatenated_docs,
        additionalLinks=task_details.additionalLinks,
        additionalDocs=task_details.additionalDocs,
        qaResponses=task_details.qaResponses
    )
    logger.info(f"Setting task cache for task {config.task_id}")

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

@app.on_event("shutdown")
async def shutdown():
    """Disconnect from MongoDB on shutdown."""
    # Cancel background refresher tasks
    for task in refresh_tasks:
        task.cancel()
    # Wait for cancellation (ignore errors)
    if refresh_tasks:
        await asyncio.gather(*refresh_tasks, return_exceptions=True)

    await mongodb.close()
    logger.info("✅ Disconnected from MongoDB")

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Welcome to the Security Findings API"}

async def process_submitted_findings(
    task_id: str,
    agent_id: str,
    findings: List[Finding]
):
    """
    Process submitted findings with deduplication and evaluation.
    
    Args:
        task_id: Task identifier
        agent_id: Agent identifier
        findings: List of findings to process
    """
    try:
        logger.info(f"Starting processing of findings for task_id: {task_id}, agent_id: {agent_id}")
        
        # 1. Process findings with deduplication
        dedup_results = await deduplicator.process_findings(task_id, agent_id, findings)
        logger.debug(f"Deduplication results for task_id: {task_id}, agent_id: {agent_id}: {dedup_results}")

        # 1.5. Perform cross-agent comparison for newly added findings
        cross_comparison_results = {}
        if dedup_results['new'] > 0:
            # Get newly added findings
            new_findings = []
            for title in dedup_results.get('new_titles', []):
                finding = await mongodb.get_finding(task_id, title)
                if finding:
                    new_findings.append(finding)
            
            # Compare with findings from other agents
            if new_findings:
                cross_comparison_results = await evaluator.cross_comparison.compare_with_other_agents(
                    task_id,
                    agent_id,
                    new_findings
                )
        
        logger.debug(f"Cross comparison results for task_id: {task_id}, agent_id: {agent_id}: {cross_comparison_results}")

        # 2. Only perform evaluation if new findings were added and not marked as similar_valid
        evaluation_results = {}
        if dedup_results['new'] > 0:
            # Get pending findings (newly added ones that weren't marked as similar_valid)
            pending_findings = await evaluator.get_pending_findings(task_id)
            
            if pending_findings:
                # Evaluate all pending findings
                evaluation_results = await evaluator.evaluate_all_pending(task_id)
                
                # Generate summary
                summary_report = await evaluator.generate_summary_report(task_id)
                evaluation_results["summary"] = summary_report
        
        logger.debug(f"Evaluation results for task_id: {task_id}, agent_id: {agent_id}: {evaluation_results}")

        # 3. Post results to backend endpoint
        try:
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
                return

            # Format findings data for the backend endpoint
            formatted_findings = []
            summary = { "valid": 0, "already_reported": 0, "disputed": 0 }
                
            current_sync_time = datetime.utcnow()
            
            for finding in latest_findings:
                formatted_findings.append({
                    "title": finding.title,
                    "description": finding.description,
                    "severity": finding.severity,
                    "status": finding.status,
                    "file_paths": finding.file_paths,
                    "created_at": finding.created_at.isoformat()
                })

                if finding.status == Status.UNIQUE_VALID or finding.status == Status.SIMILAR_VALID:
                    summary["valid"] += 1
                elif finding.status == Status.ALREADY_REPORTED:
                    summary["already_reported"] += 1
                elif finding.status == Status.DISPUTED:
                    summary["disputed"] += 1

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
            
            logger.info(f"Background processing completed successfully for task_id: {task_id}, agent_id: {agent_id}")
            
        except Exception as post_error:
            logger.error(f"Error posting results to backend for task_id: {task_id}, agent_id: {agent_id}: {str(post_error)}")
            
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error during processing of findings for task_id: {task_id}, agent_id: {agent_id}: {str(e)}")
        logger.error(f"Traceback for task_id: {task_id}, agent_id: {agent_id}: {error_trace}")

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
    background_tasks: BackgroundTasks,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    Submit security findings for processing.
    Validates input and queues findings for background processing with deduplication and evaluation.
    
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
            
        # 2. Verify API key and get agent_id from agents_cache
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
        
        # 3. Post submission to backend
        await post_submission(input_data.task_id, agent_id, len(input_data.findings))
        
        # 4. Queue background processing
        background_tasks.add_task(
            process_submitted_findings,
            input_data.task_id,
            agent_id,
            input_data.findings
        )
        
        # 5. Return immediate response
        return {
            "message": "Findings accepted and queued for processing",
            "task_id": input_data.task_id,
            "agent_id": agent_id,
            "findings_count": len(input_data.findings),
            "status": "processing"
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8004, reload=True) 