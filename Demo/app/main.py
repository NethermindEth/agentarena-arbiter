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
from fastapi.responses import JSONResponse

from app.backend_api import TaskNotFoundError, new_client
from app.backend_findings import fetch_findings, post_results
from app.backend_metadata import fetch_processed_marker, mark_task_processed
from app.backend_tasks import fetch_approved_tasks, fetch_task
from app.config import config
from app.core.deduplication import FindingDeduplication
from app.core.evaluation import FindingEvaluator
from app.core.triage_sandbox import SandboxUnavailable, harden_service_process
from app.models.finding_db import FindingDB, Status
from app.task_runs import cancel_background_runs, release, start_in_background, try_claim
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
    Skips a task already marked processed in the backend's task metadata or being processed by a
    manual run, and marks one whose run completes.

    Args:
        task_id: Task identifier
    """
    if not try_claim(task_id):
        logger.warning(f"Task {task_id} is already being processed; skipping the scheduled run")
        return
    try:
        logger.info(f"Scheduled task processing triggered for task {task_id}")

        marker = await fetch_processed_marker(task_id, config)
        if marker:
            logger.info(
                f"Task {task_id} was already processed at {marker.get('processed_at')}. Skipping."
            )
            return

        # A failed run leaves the task unmarked, so POST /tasks/{task_id}/process can retry it.
        if not await process_task(task_id):
            logger.error(f"Scheduled processing of task {task_id} failed; not marking it processed")
            return

        await mark_task_processed(task_id, config, scheduled=True)

        logger.info(f"Marked task {task_id} as processed via scheduled job")

    except Exception as e:
        logger.error(f"Error in scheduled task processing for task {task_id}: {str(e)}")
    finally:
        release(task_id)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifespan events."""
    # Startup
    try:
        # Triage agents run untrusted code as this user; keep our /proc (whose environment holds
        # the Claude, Gemini and backend keys) unreadable to them.
        harden_service_process()

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

        # Manual runs: cancelling one kills its agents; the task stays unmarked for a retry.
        await cancel_background_runs()

        # Cancel scheduler
        try:
            scheduler.shutdown()
            logger.info("✅ Shut down APScheduler")
        except Exception as e:
            logger.error(f"Error shutting down scheduler: {str(e)}")

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

        # The triage copies the checkout at repoPath into its sandboxes and scopes its review to
        # selectedFiles; the concatenated content is for the Gemini deduplication.
        task_cache = TaskCache(
            taskId=task_id,
            title=task.title,
            description=task.description,
            startTime=datetime.fromtimestamp(float(task.startTime), tz=UTC),
            deadline=datetime.fromtimestamp(float(task.deadline), tz=UTC),
            repoPath=repo_storage_path,
            selectedFiles=selected_files,
            selectedDocs=selected_docs,
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


async def triage_preflight(task_id: str) -> bool:
    """
    Check the evaluation host before a run downloads or judges anything.

    Returns False when the task must not be processed now (no PoC sandbox, or misconfiguration);
    the task then stays unmarked and can be retried once the host is fixed.
    """
    try:
        # The bubblewrap probe runs subprocesses for up to 40 s; keep it off the event loop.
        await asyncio.to_thread(evaluator.preflight)
    except (SandboxUnavailable, ValueError) as e:
        logger.error(f"Triage preflight failed for task_id: {task_id}; not processing it: {e}")
        return False
    return True


async def process_task(task_id: str) -> bool:
    """
    Process all findings for a task that has ended.
    This function is called automatically when a task deadline passes.
    Uses the Gemini-based deduplication and the findings-triage evaluation.

    Args:
        task_id: Task identifier for the ended task

    Returns:
        True when the run completed, posting the results included, or the task has no findings;
        False when it stopped early, so the caller must not mark the task processed
    """
    try:
        # One client for the run's read of the findings and its final post.
        async with new_client() as client:
            return await _process_task(task_id, client)
    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error during task processing for task_id: {task_id}: {str(e)}")
        logger.error(f"Traceback for task_id: {task_id}: {error_trace}")
        return False


def _clear_verdicts(findings: list[FindingDB]) -> None:
    """Return each finding to pending with no verdict, so a run judges the whole set afresh."""
    for finding in findings:
        finding.status = Status.PENDING
        finding.duplicateOf = None
        finding.deduplication_comment = None
        finding.evaluated_severity = None
        finding.evaluation_comment = None


async def _process_task(task_id: str, client: httpx.AsyncClient) -> bool:
    """Run process_task's steps with one backend client; see process_task."""
    logger.info(f"Processing task findings for task_id: {task_id}")

    # Both stages work on these objects in memory; post_results is the run's only write, so a run
    # that fails before it leaves the backend unchanged and a retry starts over.
    findings = await fetch_findings(task_id, config, client=client)

    if findings is None:
        logger.error(f"Task {task_id} not found in backend; nothing to process")
        return False

    if not findings:
        logger.info(f"No findings found for task_id: {task_id}")
        return True

    logger.info(f"Found {len(findings)} findings for task_id: {task_id}")
    # Verdicts left by an earlier arbiter version, which wrote them finding by finding, would
    # otherwise steer the same-agent check in deduplication.
    _clear_verdicts(findings)

    # Make sure the triage can run before downloading the repository or calling any model.
    if not await triage_preflight(task_id):
        return False

    # Fetch task data with repository download
    task_cache = await fetch_task_data(task_id)
    if not task_cache:
        logger.error(
            f"Failed to fetch task data for task_id: {task_id}. Evaluation cannot proceed without smart contract context."
        )
        return False

    # Step 1: Identify duplicates
    logger.info(f"Starting deduplication for task_id: {task_id}")

    dedup_results = await deduplicator.process_findings(task_id, findings, task_cache)
    duplicate_relationships = dedup_results["deduplication"]["duplicate_relationships"]

    logger.info(
        f"Deduplication completed for task_id: {task_id}: "
        f"{dedup_results['summary']['originals_found']} originals, "
        f"{dedup_results['summary']['duplicates_found']} duplicates"
    )

    # Step 2: Triage the findings, keeping duplicates together
    logger.info(f"Starting triage evaluation for task_id: {task_id}")

    evaluation_results = await evaluator.evaluate_all_findings(
        findings, duplicate_relationships, task_cache
    )

    failed_findings = evaluation_results["failed_findings"]
    if failed_findings:
        # Those findings carry no verdict, and the post closes the task, so nothing is posted;
        # the retry judges the whole task again.
        logger.error(
            f"{len(failed_findings)} finding(s) of task_id: {task_id} could not be evaluated; "
            "nothing posted, the task stays unmarked for a retry"
        )
        return False

    logger.info(
        f"Evaluation completed for task_id: {task_id}: "
        f"{evaluation_results['application_results']['valid_count']} valid, "
        f"{evaluation_results['application_results']['disputed_count']} disputed, "
        f"{evaluation_results['application_results']['failed_count']} unmatched"
    )

    # Step 3: Write every finding with its verdict in one call
    try:
        await post_results(task_id, findings, config, client=client)
    except Exception as post_error:
        logger.error(f"Error posting results to backend for task_id: {task_id}: {str(post_error)}")
        return False

    logger.info(f"Posted {len(findings)} findings to backend for task_id: {task_id}")
    logger.info(
        f"Processing summary: "
        f"Duplicates: {dedup_results['summary']['duplicates_found']}, "
        f"Disputed: {evaluation_results['application_results']['disputed_count']}, "
        f"Unmatched evaluations: {evaluation_results['application_results']['failed_count']}, "
        f"Total: {len(findings)}"
    )
    return True


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


async def _process_task_in_background(task_id: str) -> None:
    """Run a manually triggered task to its end, and mark it processed if the run completes."""
    try:
        # A failed run leaves the task unmarked, so the endpoint can be called again.
        if not await process_task(task_id):
            logger.error(f"Manual processing of task {task_id} failed; not marking it processed")
            return
        await mark_task_processed(task_id, config, scheduled=False)
        logger.info(f"Manual task processing completed for task: {task_id}")
    except asyncio.CancelledError:
        logger.warning(f"Manual processing of task {task_id} was cancelled; it stays unmarked")
        raise
    except Exception as e:
        logger.error(f"Error during manual task processing for task {task_id}: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")


@app.post("/tasks/{task_id}/process")
async def trigger_task_processing(task_id: str, x_api_key: str = Header(..., alias="X-API-Key")):
    """
    Manually trigger task processing for all findings of a task.
    This endpoint allows immediate processing without waiting for the scheduled deadline.

    A run takes hours, so it is started in the background and the answer is 202; the run posts
    the verdicts and marks the task processed when it completes, and a failure only shows in the
    arbiter's log (the task then stays unmarked, so it can be triggered again). A task already
    being processed, manually or by its scheduled job, is a 409.

    Args:
        task_id: Task identifier for the task to process
        x_api_key: API key for authentication

    Returns:
        Processing status and summary
    """
    try:
        if x_api_key != config.backend_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

        if not try_claim(task_id):
            raise HTTPException(
                status_code=409, detail=f"Task {task_id} is already being processed"
            )
        started = False
        try:
            try:
                marker = await fetch_processed_marker(task_id, config)
            except TaskNotFoundError as e:
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found") from e

            if marker:
                logger.info(f"Task {task_id} was already processed at {marker.get('processed_at')}")
                return {
                    "task_id": task_id,
                    "status": "already_processed",
                    "message": f"Task was already processed at {marker.get('processed_at')}",
                    "processed_at": marker.get("processed_at"),
                    "scheduled_processing": marker.get("scheduled_processing", False),
                }

            findings = await fetch_findings(task_id, config)

            if findings is None:
                raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

            if not findings:
                return {
                    "task_id": task_id,
                    "status": "no_findings",
                    "message": "No findings found for this task",
                    "total_findings": 0,
                }

            logger.info(
                f"Manual task processing triggered for task: {task_id} with {len(findings)} findings"
            )
            start_in_background(task_id, _process_task_in_background(task_id))
            started = True
        finally:
            # Once started, the background run holds the claim until it ends.
            if not started:
                release(task_id)

        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "accepted",
                "message": "Task processing started; the verdicts are posted when it completes",
                "total_findings": len(findings),
                "manual_trigger": True,
            },
        )

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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104  the local runner serves Docker and the LAN
        port=8004,
        reload=True,
        reload_dirs=["app"],
    )
