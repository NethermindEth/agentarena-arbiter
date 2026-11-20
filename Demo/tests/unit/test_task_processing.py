"""
Unit tests for task processing functions.
Tests scheduling and processing logic without external dependencies.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest


@pytest.mark.asyncio
class TestScheduleTaskProcessing:
    """Test the schedule_task_processing function."""

    @patch("app.main.scheduler")
    async def test_schedule_task_processing_success(self, mock_scheduler):
        """Test successful task processing scheduling."""
        from app.main import schedule_task_processing

        task_id = "test-task-123"
        start_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        deadline = datetime(2025, 1, 2, 0, 0, 0, tzinfo=UTC)

        # Mock scheduler methods
        mock_scheduler.get_job.return_value = None  # No existing job
        mock_scheduler.add_job.return_value = None

        await schedule_task_processing(task_id, start_time, deadline)

        # Verify scheduler was called correctly
        mock_scheduler.get_job.assert_called_once_with("task_test-task-123")
        mock_scheduler.add_job.assert_called_once()

    @patch("app.main.scheduler")
    @patch("app.main.logger")
    async def test_schedule_task_processing_invalid_timing(self, mock_logger, mock_scheduler):
        """Test scheduling with invalid timing (start >= deadline)."""
        from app.main import schedule_task_processing

        task_id = "test-task-invalid"
        start_time = datetime(2025, 1, 2, 0, 0, 0, tzinfo=UTC)
        deadline = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)  # Before start time

        await schedule_task_processing(task_id, start_time, deadline)

        # Should log error and return early
        mock_logger.error.assert_called_once()

        # Should not call scheduler
        mock_scheduler.add_job.assert_not_called()

    @patch("app.main.scheduler")
    async def test_schedule_task_processing_removes_existing_job(self, mock_scheduler):
        """Test scheduling removes existing job first."""
        from app.main import schedule_task_processing

        task_id = "test-task-existing"
        start_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        deadline = datetime(2025, 1, 2, 0, 0, 0, tzinfo=UTC)

        # Mock existing job
        mock_existing_job = Mock()
        mock_scheduler.get_job.return_value = mock_existing_job
        mock_scheduler.remove_job.return_value = None
        mock_scheduler.add_job.return_value = None

        await schedule_task_processing(task_id, start_time, deadline)

        # Should remove existing job
        mock_scheduler.remove_job.assert_called_once_with("task_test-task-existing")

    @patch("app.main.scheduler")
    @patch("app.main.logger")
    async def test_schedule_task_processing_exception_handling(self, mock_logger, mock_scheduler):
        """Test exception handling in scheduling."""
        from app.main import schedule_task_processing

        task_id = "test-task-error"
        start_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        deadline = datetime(2025, 1, 2, 0, 0, 0, tzinfo=UTC)

        # Mock scheduler to raise exception
        mock_scheduler.get_job.side_effect = Exception("Scheduler error")

        await schedule_task_processing(task_id, start_time, deadline)

        # Should log error
        mock_logger.error.assert_called_once()


@pytest.mark.asyncio
class TestProcessTaskScheduled:
    """Test the process_task_scheduled function."""

    @patch("app.main.mark_task_processed", new_callable=AsyncMock)
    @patch("app.main.fetch_processed_marker", new_callable=AsyncMock, return_value=None)
    @patch("app.main.process_task", new_callable=AsyncMock, return_value=True)
    async def test_process_task_scheduled_success(
        self, mock_process_task, mock_fetch_marker, mock_mark
    ):
        """A completed run is marked processed, as a scheduled run."""
        from app.main import config, process_task_scheduled

        await process_task_scheduled("test-scheduled-task")

        mock_fetch_marker.assert_called_once_with("test-scheduled-task", config)
        mock_process_task.assert_called_once_with("test-scheduled-task")
        mock_mark.assert_called_once_with("test-scheduled-task", config, scheduled=True)

    @patch("app.main.mark_task_processed", new_callable=AsyncMock)
    @patch("app.main.fetch_processed_marker", new_callable=AsyncMock, return_value=None)
    @patch("app.main.process_task", new_callable=AsyncMock, return_value=False)
    async def test_process_task_scheduled_failure_is_not_marked(
        self, mock_process_task, _mock_fetch_marker, mock_mark
    ):
        """A failed run leaves the task unmarked, so it can be retried."""
        from app.main import process_task_scheduled

        await process_task_scheduled("test-failed-task")

        mock_process_task.assert_called_once_with("test-failed-task")
        mock_mark.assert_not_called()

    @patch("app.main.mark_task_processed", new_callable=AsyncMock)
    @patch(
        "app.main.fetch_processed_marker",
        new_callable=AsyncMock,
        return_value={"processed_at": "2025-01-01T00:00:00+00:00", "scheduled_processing": True},
    )
    @patch("app.main.process_task", new_callable=AsyncMock)
    async def test_process_task_scheduled_already_processed(
        self, mock_process_task, _mock_fetch_marker, mock_mark
    ):
        """A task already marked is skipped."""
        from app.main import process_task_scheduled

        await process_task_scheduled("test-already-processed")

        mock_process_task.assert_not_called()
        mock_mark.assert_not_called()

    @patch("app.main.mark_task_processed", new_callable=AsyncMock)
    @patch("app.main.fetch_processed_marker", new_callable=AsyncMock)
    @patch("app.main.process_task", new_callable=AsyncMock)
    async def test_process_task_scheduled_unknown_task(
        self, mock_process_task, mock_fetch_marker, mock_mark
    ):
        """A task the backend no longer knows is logged and neither processed nor marked."""
        from app.backend_api import TaskNotFoundError
        from app.main import process_task_scheduled

        mock_fetch_marker.side_effect = TaskNotFoundError("gone")

        await process_task_scheduled("gone")

        mock_process_task.assert_not_called()
        mock_mark.assert_not_called()

    @patch("app.main.mark_task_processed", new_callable=AsyncMock)
    @patch("app.main.fetch_processed_marker", new_callable=AsyncMock, return_value=None)
    @patch("app.main.process_task", new_callable=AsyncMock)
    @patch("app.main.logger")
    async def test_process_task_scheduled_exception_handling(
        self, mock_logger, mock_process_task, _mock_fetch_marker, mock_mark
    ):
        """An exception in the run is logged and leaves the task unmarked."""
        from app.main import process_task_scheduled

        mock_process_task.side_effect = Exception("Processing error")

        await process_task_scheduled("test-error-processing")

        mock_logger.error.assert_called_once()
        mock_mark.assert_not_called()

    @patch("app.main.mark_task_processed", new_callable=AsyncMock)
    @patch("app.main.fetch_processed_marker", new_callable=AsyncMock, return_value=None)
    @patch("app.main.process_task", new_callable=AsyncMock)
    async def test_process_task_scheduled_skips_a_task_in_progress(
        self, mock_process_task, mock_fetch_marker, mock_mark
    ):
        """A task a manual run is processing is skipped, without asking the backend."""
        from app import task_runs
        from app.main import process_task_scheduled

        assert task_runs.try_claim("busy")
        try:
            await process_task_scheduled("busy")
        finally:
            task_runs.release("busy")

        mock_fetch_marker.assert_not_called()
        mock_process_task.assert_not_called()
        mock_mark.assert_not_called()

    @patch("app.main.mark_task_processed", new_callable=AsyncMock)
    @patch("app.main.fetch_processed_marker", new_callable=AsyncMock, return_value=None)
    @patch("app.main.process_task", new_callable=AsyncMock)
    async def test_process_task_scheduled_holds_the_task_while_running(
        self, mock_process_task, _mock_fetch_marker, _mock_mark
    ):
        """The scheduled run claims its task for the run and releases it at the end."""
        from app import task_runs
        from app.main import process_task_scheduled

        claimed_during_run = []

        async def run(task_id):
            claimed_during_run.append(not task_runs.try_claim(task_id))
            raise RuntimeError("boom")

        mock_process_task.side_effect = run

        await process_task_scheduled("t1")

        assert claimed_during_run == [True]
        assert task_runs.try_claim("t1")
        task_runs.release("t1")


class TestTriagePreflight:
    """triage_preflight runs the host checks without blocking the event loop."""

    async def test_runs_in_a_worker_thread(self):
        import threading

        from app import main as app_main

        threads = []

        def preflight():
            threads.append(threading.get_ident())

        with patch.object(app_main.evaluator, "preflight", side_effect=preflight):
            assert await app_main.triage_preflight("t1") is True
        assert threads and threads[0] != threading.get_ident()

    @pytest.mark.parametrize("error", ["SandboxUnavailable", "ValueError"])
    async def test_a_failed_check_means_no_run(self, error):
        from app import main as app_main
        from app.core.triage_sandbox import SandboxUnavailable

        exc = SandboxUnavailable("no userns") if error == "SandboxUnavailable" else ValueError("x")
        with patch.object(app_main.evaluator, "preflight", side_effect=exc):
            assert await app_main.triage_preflight("t1") is False
