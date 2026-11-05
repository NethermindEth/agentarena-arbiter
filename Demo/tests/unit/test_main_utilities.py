"""
Unit tests for main.py essential utility functions.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestRootEndpoint:
    """Test the root endpoint."""

    def test_root_endpoint(self, client):
        """Test that root endpoint returns welcome message."""
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Welcome to the ArbiterAgent API!"}


class TestPostSubmission:
    """Test the post_submission utility function."""

    @pytest.mark.asyncio
    @patch("app.main.config")
    @patch("httpx.AsyncClient")
    async def test_post_submission_success(self, mock_client_class, mock_config):
        """Test successful submission posting to backend."""
        from app.main import post_submission

        # Setup config
        mock_config.backend_submissions_endpoint = "http://test.com/submissions"
        mock_config.backend_api_key = "test-key"

        # Setup httpx client mock
        mock_response = Mock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Call function
        await post_submission("test-task", "test-agent", 5)

        # Verify call was made
        mock_client.post.assert_called_once_with(
            "http://test.com/submissions",
            json={"task_id": "test-task", "agent_id": "test-agent", "findings_count": 5},
            headers={"X-API-Key": "test-key"},
        )

    @pytest.mark.asyncio
    @patch("app.main.config")
    async def test_post_submission_no_endpoint_configured(self, mock_config):
        """Test submission posting when endpoint is not configured."""
        from app.main import post_submission

        # Setup config with no endpoint
        mock_config.backend_submissions_endpoint = None

        # Should not raise exception, just skip posting
        await post_submission("test-task", "test-agent", 5)

    @pytest.mark.asyncio
    @patch("app.main.config")
    @patch("httpx.AsyncClient")
    async def test_post_submission_error_response(self, mock_client_class, mock_config):
        """Test submission posting with error response."""
        from app.main import post_submission

        # Setup config
        mock_config.backend_submissions_endpoint = "http://test.com/submissions"
        mock_config.backend_api_key = "test-key"

        # Setup httpx client mock with error response
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Should not raise exception, just log error
        await post_submission("test-task", "test-agent", 5)

    @pytest.mark.asyncio
    @patch("app.main.config")
    @patch("httpx.AsyncClient")
    async def test_post_submission_network_error(self, mock_client_class, mock_config):
        """Test submission posting with network error."""
        from app.main import post_submission

        # Setup config
        mock_config.backend_submissions_endpoint = "http://test.com/submissions"
        mock_config.backend_api_key = "test-key"

        # Setup httpx client mock to raise exception
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Network error")
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Should not raise exception, just log error
        await post_submission("test-task", "test-agent", 5)


class TestGetLatestFindings:
    """Test the get_latest_findings utility function."""

    @patch("app.main.mongodb", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_get_latest_findings_with_last_sync(self, mock_mongodb, sample_findings):
        """Test getting findings with existing last sync timestamp."""
        from app.main import get_latest_findings

        # Setup mock for last sync
        last_sync_time = datetime.now(UTC)
        mock_mongodb.get_metadata.return_value = {"timestamp": last_sync_time}
        mock_mongodb.get_findings.return_value = sample_findings[:1]  # Only one new finding

        result = await get_latest_findings("test-task", "test-agent")

        # Verify the correct call was made
        mock_mongodb.get_metadata.assert_called_once_with("last_sync_test-task_test-agent")
        mock_mongodb.get_findings.assert_called_once_with(
            task_id="test-task", agent_id="test-agent", since_timestamp=last_sync_time
        )

        assert result == sample_findings[:1]

    @patch("app.main.mongodb", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_get_latest_findings_no_last_sync(self, mock_mongodb, sample_findings):
        """Test getting findings without previous sync timestamp."""
        from app.main import get_latest_findings

        # Setup mock for no last sync
        mock_mongodb.get_metadata.return_value = None
        mock_mongodb.get_findings.return_value = sample_findings

        result = await get_latest_findings("test-task", "test-agent")

        # Verify the correct call was made
        mock_mongodb.get_metadata.assert_called_once_with("last_sync_test-task_test-agent")
        mock_mongodb.get_findings.assert_called_once_with(
            task_id="test-task", agent_id="test-agent"
        )

        assert result == sample_findings

    @patch("app.main.mongodb", new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_get_latest_findings_empty_last_sync(self, mock_mongodb):
        """Test getting findings with empty last sync data."""
        from app.main import get_latest_findings

        # Setup mock for empty last sync
        mock_mongodb.get_metadata.return_value = {}  # Empty dict, no timestamp key
        mock_mongodb.get_findings.return_value = []

        result = await get_latest_findings("test-task", "test-agent")

        # Should call get_findings without since_timestamp when timestamp is missing
        mock_mongodb.get_findings.assert_called_once_with(
            task_id="test-task", agent_id="test-agent"
        )

        assert result == []


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
