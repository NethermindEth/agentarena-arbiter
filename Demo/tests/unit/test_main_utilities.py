"""
Unit tests for main.py essential utility functions.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestRootEndpoint:
    """Test the root endpoint."""

    def test_root_endpoint(self, client):
        """Test that root endpoint returns welcome message."""
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Welcome to the ArbiterAgent API!"}


@pytest.mark.asyncio
class TestScheduleApprovedTasks:
    """Unit tests for scheduling functionality."""

    @patch("app.main.schedule_task_processing")
    @patch("app.main.fetch_approved_tasks", new_callable=AsyncMock)
    async def test_schedule_approved_tasks_no_backend_tasks(self, mock_fetch, mock_schedule):
        """Test scheduling when the backend returns no approved tasks."""
        from app import main as app_main

        mock_fetch.return_value = []

        await app_main.schedule_approved_tasks()

        mock_fetch.assert_called_once()
        mock_schedule.assert_not_called()

    @patch("app.main.fetch_approved_tasks", new_callable=AsyncMock)
    async def test_schedule_approved_tasks_backend_error(self, mock_fetch):
        """Test scheduling when the backend request fails."""
        from app import main as app_main

        mock_fetch.side_effect = Exception("Backend error")

        # Should not raise exception
        await app_main.schedule_approved_tasks()

    @patch("app.main.schedule_task_processing")
    @patch("app.main.fetch_approved_tasks", new_callable=AsyncMock)
    async def test_testtask_not_scheduled(self, mock_fetch, mock_schedule):
        """Test that TESTTASK doesn't trigger job scheduling."""
        from app import main as app_main
        from tests.conftest import create_sample_task

        mock_fetch.return_value = [create_sample_task(task_id="TESTTASK")]

        await app_main.schedule_approved_tasks()

        # Should not schedule TESTTASK
        mock_schedule.assert_not_called()


@pytest.mark.asyncio
class TestFetchApprovedTasks:
    """Unit tests for the backend approved-tasks client."""

    @patch("httpx.AsyncClient")
    async def test_fetch_approved_tasks_success(self, mock_client_class):
        """The endpoint is called with the API key and the body is parsed as tasks."""
        from app.backend_tasks import fetch_approved_tasks
        from tests.conftest import create_sample_task

        task = create_sample_task(task_id="task-1")
        config = Mock()
        config.backend_approved_tasks_endpoint = "http://test.com/api/arbiter/approved-tasks"
        config.backend_api_key = "test-key"

        mock_response = Mock()
        mock_response.json.return_value = [task.model_dump()]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        result = await fetch_approved_tasks(config)

        mock_client.get.assert_called_once_with(
            "http://test.com/api/arbiter/approved-tasks", headers={"X-API-Key": "test-key"}
        )
        mock_response.raise_for_status.assert_called_once()
        assert result == [task]

    @patch("httpx.AsyncClient")
    async def test_fetch_approved_tasks_invalid_body(self, mock_client_class):
        """A body that is not a list of tasks raises instead of scheduling garbage."""
        from pydantic import ValidationError

        from app.backend_tasks import fetch_approved_tasks

        config = Mock()
        config.backend_approved_tasks_endpoint = "http://test.com/api/arbiter/approved-tasks"
        config.backend_api_key = "test-key"

        mock_response = Mock()
        mock_response.json.return_value = [{"taskId": "missing-fields"}]
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(ValidationError):
            await fetch_approved_tasks(config)


@pytest.mark.asyncio
class TestFetchTask:
    """Unit tests for the backend single-task client."""

    @staticmethod
    def _setup(mock_client_class, status_code, body):
        config = Mock()
        config.backend_tasks_endpoint = "http://test.com/api/arbiter/tasks"
        config.backend_api_key = "test-key"
        mock_response = Mock()
        mock_response.status_code = status_code
        mock_response.json.return_value = body
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client
        return config, mock_client, mock_response

    @patch("httpx.AsyncClient")
    async def test_fetch_task_success(self, mock_client_class):
        """The task id is appended to the endpoint and the body is parsed as a task."""
        from app.backend_tasks import fetch_task
        from tests.conftest import create_sample_task

        task = create_sample_task(task_id="task-1")
        config, mock_client, mock_response = self._setup(mock_client_class, 200, task.model_dump())

        result = await fetch_task("task-1", config)

        mock_client.get.assert_called_once_with(
            "http://test.com/api/arbiter/tasks/task-1", headers={"X-API-Key": "test-key"}
        )
        mock_response.raise_for_status.assert_called_once()
        assert result == task

    @patch("httpx.AsyncClient")
    async def test_fetch_task_not_found(self, mock_client_class):
        """A 404 from the backend means the task does not exist."""
        from app.backend_tasks import fetch_task

        config, _, mock_response = self._setup(mock_client_class, 404, {"detail": "not found"})

        assert await fetch_task("missing", config) is None
        mock_response.raise_for_status.assert_not_called()

    @patch("httpx.AsyncClient")
    async def test_fetch_task_id_is_escaped(self, mock_client_class):
        """A task id cannot change the request path."""
        from app.backend_tasks import fetch_task

        config, mock_client, _ = self._setup(mock_client_class, 404, None)

        await fetch_task("../x", config)

        assert mock_client.get.call_args.args[0] == "http://test.com/api/arbiter/tasks/..%2Fx"
