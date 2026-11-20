"""
Bookkeeping for task runs in progress: one run per task at a time, and the manual runs that
``POST /tasks/{task_id}/process`` starts in the background.

A manual run and the scheduled job share the task's repository copy (``task_data/repo_<id>``)
and would each post the verdicts, so a run claims its task until it ends. Everything here runs
on the event loop, and callers check and take a claim with no ``await`` in between.
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_claimed: set[str] = set()
# Strong references, so a running task is not garbage collected; also what shutdown cancels.
_background: set[asyncio.Task[None]] = set()


def try_claim(task_id: str) -> bool:
    """Claim the task for a run; returns False when a run of it is already in progress."""
    if task_id in _claimed:
        return False
    _claimed.add(task_id)
    return True


def release(task_id: str) -> None:
    """End the task's claim."""
    _claimed.discard(task_id)


def start_in_background(task_id: str, run: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    """
    Run a claimed task's coroutine in the background. The claim ends when the coroutine ends,
    whether it completes, fails or is cancelled, even before it started.
    """
    task = asyncio.create_task(run, name=f"process-task-{task_id}")
    _background.add(task)

    def finished(done: asyncio.Task[None]) -> None:
        _background.discard(done)
        release(task_id)

    task.add_done_callback(finished)
    return task


def background_runs() -> list[asyncio.Task[None]]:
    """The background runs still in progress."""
    return list(_background)


async def cancel_background_runs() -> None:
    """
    Cancel the background runs still in progress and wait until they stopped; a cancelled run
    kills its Claude Code agents before it ends.
    """
    runs = background_runs()
    for run in runs:
        run.cancel()
    if runs:
        await asyncio.gather(*runs, return_exceptions=True)
        logger.info("Cancelled %d task run(s) in progress at shutdown", len(runs))
