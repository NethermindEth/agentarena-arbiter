"""
Main FastAPI application for security findings management.
Provides API endpoints for submitting and managing security findings.
"""

import asyncio
import logging
import os
import shutil
import sys
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.backend_api import new_client
from app.backend_findings import fetch_findings
from app.backend_tasks import fetch_approved_tasks, fetch_task
from app.config import config
from app.core.deduplication import FindingDeduplication
from app.core.evaluation import FindingEvaluator
from app.database.mongodb_handler import mongodb
from app.models.finding_db import FindingDB, Status
from app.task_utils import download_repository, read_and_concatenate_files, remove_tree
from app.types import TaskCache

# Initialize logger
logger = logging.getLogger(__name__)
logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

# Configure logging to both console and file
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format=log_format,
    handlers=[logging.StreamHandler()],
)

# Initialize handlers
deduplicator = FindingDeduplication()
evaluator = FindingEvaluator()

# Cache for TESTTASK to avoid re-downloading repository unnecessarily
test_task_cache: dict[str, Any] | None = None


# Initialize APScheduler for task processing jobs
scheduler = AsyncIOScheduler()

# Interval for refreshing task scheduling (in seconds)
REFRESH_INTERVAL_SECONDS = 1800  # 30 minutes

# Reference to refresh schedule background task so we can cancel it on shutdown
refresh_schedule_task = None


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
            logger.error(
                f"Invalid task timing for {task_id}: start time {start_time} is not before deadline {deadline}"
            )
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
            misfire_grace_time=300,  # Allow 5 minutes grace time if system is busy
        )

        logger.info(
            f"Scheduled task processing for task {task_id}: start={start_time.isoformat()}, deadline={deadline.isoformat()}"
        )

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
            logger.info(
                f"Task {task_id} was already processed at {processed_metadata.get('processed_at')}. Skipping."
            )
            return

        # A failed run leaves the task unmarked, so POST /tasks/{task_id}/process can retry it.
        if not await process_task(task_id):
            logger.error(f"Scheduled processing of task {task_id} failed; not marking it processed")
            return

        current_time = datetime.now(UTC)
        await mongodb.set_metadata(
            processed_key, {"processed_at": current_time, "scheduled_processing": True}
        )

        logger.info(f"Marked task {task_id} as processed via scheduled job")

    except Exception as e:
        logger.error(f"Error in scheduled task processing for task {task_id}: {str(e)}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    try:
        await mongodb.connect()
        logger.info("✅ Connected to MongoDB")

        # Start the scheduler
        scheduler.start()
        logger.info("✅ Started APScheduler for task processing jobs")

        try:
            # Initial scheduling of task processing jobs
            await schedule_approved_tasks()
        except Exception as e:
            logger.error(f"Error during initial task scheduling: {str(e)}")

        # --------------------------------------------------------------
        # Schedule periodic task scheduling refresh
        # --------------------------------------------------------------
        async def _periodic_task_scheduling_refresh():
            while True:
                try:
                    await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
                    await schedule_approved_tasks()
                except Exception as e:
                    logger.error(f"Error refreshing task scheduling: {str(e)}")

        # Start background task and store reference
        global refresh_schedule_task
        refresh_schedule_task = asyncio.create_task(_periodic_task_scheduling_refresh())

        yield

        # Shutdown
        # Cancel refresh schedule background task
        if refresh_schedule_task:
            refresh_schedule_task.cancel()
            try:
                await refresh_schedule_task
            except asyncio.CancelledError:
                pass  # Expected when cancelling
            except Exception as e:
                logger.error(f"Error during background task shutdown: {str(e)}")

        # Cancel scheduler
        try:
            scheduler.shutdown()
            logger.info("✅ Shut down APScheduler")
        except Exception as e:
            logger.error(f"Error shutting down scheduler: {str(e)}")

        # Close MongoDB connection
        await mongodb.close()
        logger.info("✅ Disconnected from MongoDB")

    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")
        sys.exit(1)  # Exit the application with a non-zero status to indicate an error


# Initialize FastAPI app after lifespan function definition
app = FastAPI(
    title="Security Findings API",
    description="API for managing security findings and deduplication",
    version="1.2.1",  # x-release-please-version
    lifespan=lifespan,
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


async def schedule_approved_tasks():
    """Fetch approved tasks metadata from the backend and schedule processing jobs."""
    logger.info("Fetching approved tasks from backend")
    try:
        tasks = await fetch_approved_tasks(config)
    except Exception as e:
        logger.error(f"Error fetching approved tasks from backend: {str(e)}")
        return

    if not tasks:
        logger.info("No approved tasks returned by backend; skipping task scheduling")
        return

    scheduled_count = 0
    for task in tasks:
        try:
            task_id = task.taskId
            if not task_id:
                logger.warning("Encountered task with missing taskId; skipping")
                continue

            # Parse timestamps for scheduling
            start_time = datetime.fromtimestamp(float(task.startTime), tz=UTC)
            deadline = datetime.fromtimestamp(float(task.deadline), tz=UTC)

            # Schedule processing for each task, except TESTTASK
            if task_id != "TESTTASK":
                await schedule_task_processing(task_id, start_time, deadline)
                scheduled_count += 1

        except (ValueError, TypeError) as te:
            logger.error(
                f"Invalid timestamp format for task {getattr(task, 'taskId', 'unknown')}: startTime={getattr(task, 'startTime', None)}, deadline={getattr(task, 'deadline', None)} - {str(te)}"
            )
            continue
        except Exception as e:
            logger.error(
                f"Error scheduling processing for task {getattr(task, 'taskId', 'unknown')}: {str(e)}"
            )
            continue

    logger.info(f"Scheduled processing jobs for {scheduled_count} task(s)")


def _store_repository(repo_dir: str, task_id: str) -> str:
    """Copy a downloaded repository into the data directory, replacing an earlier copy."""
    os.makedirs(config.data_dir, exist_ok=True)
    repo_storage_path = os.path.join(config.data_dir, f"repo_{task_id}")
    if os.path.exists(repo_storage_path):
        shutil.rmtree(repo_storage_path)
    shutil.copytree(repo_dir, repo_storage_path)
    return repo_storage_path


async def fetch_task_data(task_id: str) -> TaskCache | None:
    """
    Fetch task data from database and download repository.
    For TESTTASK, uses caching based on commitSha to avoid unnecessary re-downloads.

    Args:
        task_id: Task identifier

    Returns:
        TaskCache object with downloaded data or None if failed
    """
    task = None
    try:
        task = await fetch_task(task_id, config)
        if not task:
            logger.error(f"Task {task_id} not found in backend")
            return None

        selected_files = task.selectedFiles or []
        if not selected_files:
            logger.warning(f"No files selected for task {task_id}")
            return None

        # Special handling for TESTTASK - use cache if commitSha hasn't changed
        global test_task_cache
        if task_id == "TESTTASK":
            current_commit_sha = task.commitSha

            # Check if we have cached data with the same commitSha
            if (
                test_task_cache
                and test_task_cache.get("commitSha") == current_commit_sha
                and test_task_cache.get("task_cache")
            ):
                logger.info(f"Using cached data for TESTTASK (commitSha: {current_commit_sha})")
                return test_task_cache["task_cache"]

            logger.info(
                f"TESTTASK commitSha changed or no cache available. Re-downloading repository (commitSha: {current_commit_sha})"
            )

        # Download repository
        repo_dir, temp_dir = await download_repository(
            f"{config.backend_task_repository_endpoint}/{task_id}", config
        )

        if not repo_dir or not temp_dir:
            logger.error(f"Failed to download repository for task {task_id}")
            return None

        try:
            # Copying the tree is file I/O, kept off the event loop.
            repo_storage_path = await asyncio.to_thread(_store_repository, repo_dir, task_id)
            logger.info(f"Repository for task {task_id} stored at {repo_storage_path}")
        finally:
            # Always clean up temp directory
            await asyncio.to_thread(remove_tree, temp_dir)

        # Concatenate contract files
        concatenated_contracts = read_and_concatenate_files(repo_storage_path, selected_files)
        if not concatenated_contracts:
            logger.warning(f"No valid contracts content found for task {task_id}")
            return None

        # Concatenate documentation files
        selected_docs = task.selectedDocs or []
        concatenated_docs = (
            read_and_concatenate_files(repo_storage_path, selected_docs) if selected_docs else ""
        )
        if not concatenated_docs:
            logger.warning(f"No valid docs content found for task {task_id}")

        # Create TaskCache object
        task_cache = TaskCache(
            taskId=task_id,
            startTime=datetime.fromtimestamp(float(task.startTime), tz=UTC),
            deadline=datetime.fromtimestamp(float(task.deadline), tz=UTC),
            selectedFilesContent=concatenated_contracts,
            selectedDocsContent=concatenated_docs,
            additionalLinks=task.additionalLinks,
            additionalDocs=task.additionalDocs,
            qaResponses=task.qaResponses,
        )

        # Cache the result for TESTTASK
        if task_id == "TESTTASK":
            test_task_cache = {
                "commitSha": task.commitSha,
                "task_cache": task_cache,
                "cached_at": datetime.now(UTC),
            }
            logger.info(f"Cached TESTTASK data for commitSha: {task.commitSha}")

        logger.info(f"Successfully created task cache for task {task_id}")
        return task_cache

    except (ValueError, TypeError) as te:
        logger.error(
            f"Invalid timestamp format for task {task_id}: startTime={getattr(task, 'startTime', None)}, deadline={getattr(task, 'deadline', None)} - {str(te)}"
        )
        return None
    except Exception as e:
        logger.error(f"Error fetching task data for task {task_id}: {str(e)}")
        return None


def format_finding(finding: FindingDB) -> dict[str, Any]:
    return {
        "id": finding.str_id,
        "agent_id": finding.agent_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity,
        "status": finding.status,
        "file_paths": finding.file_paths,
        "duplicate_of": finding.duplicateOf if finding.duplicateOf else None,
        "deduplication_comment": finding.deduplication_comment
        if finding.deduplication_comment
        else None,
        "evaluated_severity": finding.evaluated_severity if finding.evaluated_severity else None,
        "evaluation_comment": finding.evaluation_comment if finding.evaluation_comment else None,
        "created_at": finding.created_at.isoformat(),
    }


async def process_task(task_id: str) -> bool:
    """
    Process all findings for a task that has ended.
    This function is called automatically when a task deadline passes.
    Uses the Gemini-based deduplication and batch evaluation system.

    Args:
        task_id: Task identifier for the ended task

    Returns:
        True when the run completed, posting the results included, or found nothing pending;
        False when it stopped early, so the caller must not mark the task processed
    """
    try:
        # One client for the whole run: both passes write once per finding.
        async with new_client() as client:
            return await _process_task(task_id, client)
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error during task processing for task_id: {task_id}: {str(e)}")
        logger.error(f"Traceback for task_id: {task_id}: {error_trace}")
        return False


async def _process_task(task_id: str, client: httpx.AsyncClient) -> bool:
    """Run process_task's steps with one backend client; see process_task."""
    logger.info(f"Processing task findings for task_id: {task_id}")

    # Get all pending task findings for this task
    pending_findings = await fetch_findings(task_id, config, status=Status.PENDING, client=client)

    if pending_findings is None:
        logger.error(f"Task {task_id} not found in backend; nothing to process")
        return False

    if not pending_findings:
        logger.info(f"No pending task findings found for task_id: {task_id}")
        return True

    logger.info(f"Found {len(pending_findings)} pending findings for task_id: {task_id}")

    # Fetch task data with repository download
    task_cache = await fetch_task_data(task_id)
    if not task_cache:
        logger.error(
            f"Failed to fetch task data for task_id: {task_id}. Evaluation cannot proceed without smart contract context."
        )
        return False

    # Step 1: Identify duplicates
    logger.info(f"Starting deduplication for task_id: {task_id}")

    dedup_results = await deduplicator.process_findings(
        task_id, pending_findings, task_cache, client=client
    )
    duplicate_relationships = dedup_results["deduplication"]["duplicate_relationships"]

    logger.info(
        f"Deduplication completed for task_id: {task_id}: "
        f"{dedup_results['summary']['originals_found']} originals, "
        f"{dedup_results['summary']['duplicates_found']} duplicates"
    )

    # Step 2: Re-fetch findings after deduplication to get updated statuses
    deduplicated_findings = await fetch_findings(task_id, config, client=client)
    if deduplicated_findings is None:
        logger.error(f"Task {task_id} disappeared from backend during processing")
        return False
    logger.info(
        f"Re-fetched {len(deduplicated_findings)} deduplicated findings for task_id: {task_id}"
    )

    # Step 3: Evaluate findings in batches, keeping duplicates together
    logger.info(f"Starting batch evaluation for task_id: {task_id}")

    evaluation_results = await evaluator.evaluate_all_findings(
        task_id, deduplicated_findings, duplicate_relationships, task_cache, client=client
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
        all_task_findings = await fetch_findings(task_id, config, client=client)

        if not all_task_findings:
            logger.info(f"No findings to sync with backend endpoint for task_id: {task_id}")
        else:
            logger.info(
                f"Syncing all {len(all_task_findings)} findings for task_id: {task_id} in one batch"
            )

            # Format findings data for the backend endpoint
            formatted_findings = [format_finding(finding) for finding in all_task_findings]

            # Prepare payload for backend endpoint
            payload = {"task_id": task_id, "findings": formatted_findings}

            # Post to backend endpoint
            backend_endpoint = config.backend_findings_endpoint
            if backend_endpoint:
                headers = {"X-API-Key": config.backend_api_key}
                response = await client.post(backend_endpoint, json=payload, headers=headers)
                logger.debug(f"Backend API response for task_id: {task_id}: {response.text}")
                logger.debug(
                    f"Backend API status code for task_id: {task_id}: {response.status_code}"
                )

                if response.status_code == 200:
                    logger.info(
                        f"Successfully posted {len(formatted_findings)} findings to backend for task_id: {task_id}"
                    )
                else:
                    logger.error(
                        f"Failed to post findings to backend. Status code: {response.status_code}, Response: {response.text}"
                    )
                    return False
            else:
                logger.warning(
                    f"BACKEND_FINDINGS_ENDPOINT not configured, skipping backend post for task_id: {task_id}"
                )

        logger.info(f"Task processing completed successfully for task_id: {task_id}")
        logger.info(
            f"Processing summary: "
            f"Duplicates: {dedup_results['summary']['duplicates_found']}, "
            f"Disputed: {evaluation_results['application_results']['disputed_count']}, "
            f"Failed: {evaluation_results['application_results']['failed_count']}, "
            f"Total: {len(pending_findings)}"
        )
        return True

    except Exception as post_error:
        logger.error(f"Error posting results to backend for task_id: {task_id}: {str(post_error)}")
        return False


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Welcome to the ArbiterAgent API!"}


@app.get("/tasks/{task_id}/findings", response_model=list[dict[str, Any]])
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

        findings = await fetch_findings(task_id, config)
        if findings is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        return [finding.model_dump() for finding in findings]
    except HTTPException:
        raise
    except Exception as e:
        # The error text can name the backend's internal URL, so it stays in the log.
        logger.error(f"Error retrieving findings for task {task_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error retrieving findings") from e


@app.post("/tasks/{task_id}/process")
async def trigger_task_processing(task_id: str, x_api_key: str = Header(..., alias="X-API-Key")):
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
            logger.info(
                f"Task {task_id} was already processed at {processed_metadata.get('processed_at')}"
            )
            return {
                "task_id": task_id,
                "status": "already_processed",
                "message": f"Task was already processed at {processed_metadata.get('processed_at')}",
                "processed_at": processed_metadata.get("processed_at"),
                "scheduled_processing": processed_metadata.get("scheduled_processing", False),
            }

        # Check if there are any pending findings
        pending_findings = await fetch_findings(task_id, config, status=Status.PENDING)

        if pending_findings is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        if not pending_findings:
            return {
                "task_id": task_id,
                "status": "no_pending_findings",
                "message": "No pending findings found for this task",
                "total_findings": 0,
            }

        logger.info(
            f"Manual task processing triggered for task: {task_id} with {len(pending_findings)} pending findings"
        )

        # A failed run leaves the task unmarked, so this endpoint can be called again.
        if not await process_task(task_id):
            raise HTTPException(
                status_code=502, detail=f"Processing task {task_id} failed; see the arbiter's log"
            )

        current_time = datetime.now(UTC)
        await mongodb.set_metadata(
            processed_key, {"processed_at": current_time, "scheduled_processing": False}
        )

        logger.info(f"Manual task processing completed for task: {task_id}")

        return {
            "task_id": task_id,
            "status": "processed",
            "message": "Task processing completed successfully",
            "processed_at": current_time.isoformat(),
            "total_pending_findings": len(pending_findings),
            "manual_trigger": True,
        }

    except HTTPException:
        raise
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error during manual task processing for task {task_id}: {str(e)}")
        logger.error(f"Traceback: {error_trace}")
        # The error text can name the backend's internal URL, so it stays in the log.
        raise HTTPException(status_code=500, detail="Error processing task") from e


@app.post("/schedule-task/{task_id}")
async def schedule_task(task_id: str, x_api_key: str = Header(..., alias="X-API-Key")):
    """
    Schedule a task for processing by fetching it from the database and scheduling it based on its deadline.

    Args:
        task_id: Task identifier for the task to schedule
        x_api_key: API key for authentication

    Returns:
        Scheduling status and details
    """
    try:
        if x_api_key != config.backend_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Validate task_id
        if not task_id:
            raise HTTPException(status_code=400, detail="Missing required field: task_id")

        try:
            task = await fetch_task(task_id, config)
        except Exception as e:
            logger.error(f"Error fetching task {task_id}: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Error retrieving task {task_id} from backend"
            ) from e

        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found in backend")

        # Parse timestamps
        try:
            start_time = datetime.fromtimestamp(float(task.startTime), tz=UTC)
            deadline = datetime.fromtimestamp(float(task.deadline), tz=UTC)
        except (ValueError, TypeError) as te:
            logger.error(
                f"Invalid timestamp format for task {task_id}: startTime={task.startTime}, deadline={task.deadline} - {str(te)}"
            )
            raise HTTPException(
                status_code=400, detail=f"Invalid timestamp format for task {task_id}"
            ) from te

        await schedule_task_processing(task_id, start_time, deadline)

        return {
            "task_id": task_id,
            "status": "scheduled",
            "message": f"Task {task_id} scheduled for processing",
            "start_time": start_time.isoformat(),
            "deadline": deadline.isoformat(),
            "scheduled_at": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error scheduling task: {str(e)}")
        logger.error(f"Traceback: {error_trace}")
        raise HTTPException(status_code=500, detail=f"Error scheduling task: {str(e)}") from e


@app.post("/tasks/{task_id}/post")
async def post_task_findings(task_id: str, x_api_key: str = Header(..., alias="X-API-Key")):
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
            raise HTTPException(status_code=503, detail="Backend findings endpoint not configured")

        # Get all findings for this task
        all_findings = await fetch_findings(task_id, config)

        if all_findings is None:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        if not all_findings:
            return {
                "task_id": task_id,
                "status": "no_findings",
                "message": "No findings found for this task",
                "total_findings": 0,
            }

        formatted_findings = [format_finding(finding) for finding in all_findings]

        # Prepare batched payload for backend endpoint
        payload = {"task_id": task_id, "findings": formatted_findings}

        try:
            # Post all findings to the backend endpoint
            async with httpx.AsyncClient() as client:
                headers = {"X-API-Key": config.backend_api_key}
                response = await client.post(
                    config.backend_findings_endpoint, json=payload, headers=headers
                )

                if response.status_code == 200:
                    logger.info(
                        f"Successfully posted {len(all_findings)} findings for task {task_id}"
                    )
                else:
                    logger.error(
                        f"Failed to post findings for task {task_id}. Status code: {response.status_code}, Response: {response.text}"
                    )

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
        # The error text can name the backend's internal URL, so it stays in the log.
        raise HTTPException(status_code=500, detail="Error posting findings") from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104  the local runner serves Docker and the LAN
        port=8004,
        reload=True,
        reload_dirs=["app"],
    )
