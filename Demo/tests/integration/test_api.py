"""
Integration tests for all API endpoints.
Comprehensive testing including success scenarios, error handling, and edge cases.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import httpx
import pytest

from app import task_runs

KEY = {"X-API-Key": "test-key"}


@pytest.fixture
async def api():
    """
    The app on the test's own event loop with the backend mocked, so the background runs it
    starts can be awaited (a TestClient runs each request on a loop that ends with it).
    """
    from app.main import app

    with (
        patch("app.main.config", backend_api_key="test-key"),
        patch("app.main.fetch_findings", new_callable=AsyncMock, return_value=[]) as findings,
        patch("app.main.fetch_processed_marker", new_callable=AsyncMock, return_value=None) as mk,
        patch(
            "app.main.mark_task_processed",
            new_callable=AsyncMock,
            return_value=datetime(2026, 1, 2, tzinfo=UTC),
        ) as mark,
    ):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield SimpleNamespace(
                http=http, fetch_findings=findings, fetch_marker=mk, mark_processed=mark
            )
        # A test that left a run going must not leak it, or its claim, into the next one.
        await task_runs.cancel_background_runs()


def _claimable(task_id: str) -> bool:
    """Whether no run holds the task's claim (the claim is taken and released to check)."""
    if not task_runs.try_claim(task_id):
        return False
    task_runs.release(task_id)
    return True


class TestTaskFindingsEndpoint:
    """Test /tasks/{task_id}/findings endpoint."""

    @patch("app.main.config")
    def test_get_task_findings_success(self, mock_config, client, sample_findings):
        """Test successful retrieval of task findings."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_findings.return_value = sample_findings

        response = client.get("/tasks/test-task-123/findings", headers={"X-API-Key": "test-key"})

        assert response.status_code == 200
        result = response.json()

        assert len(result) == len(sample_findings)
        assert isinstance(result, list)
        # Check first finding has expected title
        assert result[0]["title"] == "Reentrancy vulnerability in withdraw function"

    @patch("app.main.config")
    def test_get_task_findings_empty(self, mock_config, client):
        """Test retrieval of task findings when none exist."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_findings.return_value = []

        response = client.get("/tasks/test-task-123/findings", headers={"X-API-Key": "test-key"})

        assert response.status_code == 200
        result = response.json()

        assert result == []

    @patch("app.main.config")
    def test_get_task_findings_invalid_api_key(self, mock_config, client):
        """Test getting task findings with invalid API key."""
        mock_config.backend_api_key = "correct-key"

        response = client.get("/tasks/test-task/findings", headers={"X-API-Key": "wrong-key"})

        assert response.status_code == 401
        assert "Invalid API key" in response.text

    @patch("app.main.config")
    def test_get_task_findings_database_error(self, mock_config, client):
        """Test getting task findings when database error occurs."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_findings.side_effect = Exception("Database connection failed")

        response = client.get("/tasks/test-task/findings", headers={"X-API-Key": "test-key"})

        assert response.status_code == 500
        assert "Error retrieving findings" in response.text
        # The error text, which can name the backend's URL, stays in the log.
        assert "Database connection failed" not in response.text

    @patch("app.main.config")
    def test_get_task_findings_unknown_task(self, mock_config, client):
        """A task the backend does not know is a 404, not a server error."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_findings.return_value = None

        response = client.get("/tasks/missing/findings", headers={"X-API-Key": "test-key"})

        assert response.status_code == 404


class TestTriggerTaskProcessingEndpoint:
    """Test the /tasks/{task_id}/process endpoint."""

    @patch("app.main.config")
    def test_trigger_task_processing_invalid_api_key(self, mock_config, client):
        """Test trigger processing with invalid API key."""
        mock_config.backend_api_key = "correct-key"

        response = client.post("/tasks/test-task/process", headers={"X-API-Key": "wrong-key"})

        assert response.status_code == 401
        assert "Invalid API key" in response.text

    def test_trigger_task_processing_missing_api_key(self, client):
        """Test trigger processing without API key header."""
        response = client.post("/tasks/test-task/process")

        assert response.status_code == 422  # Missing required header

    @patch("app.main.config")
    def test_trigger_task_processing_already_processed(self, mock_config, client):
        """Test triggering processing for already processed task."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_marker.return_value = {
            "processed_at": "2025-01-01T00:00:00+00:00",
            "scheduled_processing": True,
        }

        response = client.post("/tasks/test-task/process", headers={"X-API-Key": "test-key"})

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "already_processed"
        assert result["task_id"] == "test-task"
        assert result["processed_at"] == "2025-01-01T00:00:00+00:00"
        assert result["scheduled_processing"] is True
        client.mock_fetch_findings.assert_not_called()

    @patch("app.main.config")
    def test_trigger_task_processing_no_findings(self, mock_config, client):
        """Test triggering processing when the task has no findings."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_findings.return_value = []

        response = client.post("/tasks/test-task/process", headers={"X-API-Key": "test-key"})

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "no_findings"
        assert result["task_id"] == "test-task"
        assert result["total_findings"] == 0

    @patch("app.main.config")
    def test_trigger_task_processing_unknown_task(self, mock_config, client):
        """A task the backend does not know, by its metadata, is a 404."""
        from app.backend_api import TaskNotFoundError

        mock_config.backend_api_key = "test-key"
        client.mock_fetch_marker.side_effect = TaskNotFoundError("missing")

        response = client.post("/tasks/missing/process", headers={"X-API-Key": "test-key"})

        assert response.status_code == 404
        client.mock_mark_processed.assert_not_called()

    @patch("app.main.config")
    def test_trigger_task_processing_unknown_task_by_findings(self, mock_config, client):
        """A task the backend does not know, by its findings, is a 404 and is not marked."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_findings.return_value = None

        response = client.post("/tasks/missing/process", headers={"X-API-Key": "test-key"})

        assert response.status_code == 404
        client.mock_mark_processed.assert_not_called()


class TestBackgroundProcessing:
    """POST /tasks/{task_id}/process starts the run in the background, one run per task."""

    async def test_accepted_run_marks_the_task_processed(self, api, sample_findings):
        """A completed background run marks the task processed, as a manual run."""
        api.fetch_findings.return_value = sample_findings
        with patch("app.main.process_task", new_callable=AsyncMock, return_value=True) as run:
            response = await api.http.post("/tasks/t1/process", headers=KEY)
            assert response.status_code == 202
            result = response.json()
            assert result["status"] == "accepted"
            assert result["total_findings"] == len(sample_findings)
            await asyncio.gather(*task_runs.background_runs())

        run.assert_awaited_once_with("t1")
        api.mark_processed.assert_awaited_once_with("t1", ANY, scheduled=False)
        assert _claimable("t1")

    @pytest.mark.parametrize(
        "outcome", [{"return_value": False}, {"side_effect": RuntimeError("boom")}]
    )
    async def test_failed_run_is_not_marked(self, api, sample_findings, outcome):
        """A failed or crashed run only logs; the task stays unmarked and can be triggered again."""
        api.fetch_findings.return_value = sample_findings
        with patch("app.main.process_task", new_callable=AsyncMock, **outcome):
            response = await api.http.post("/tasks/t1/process", headers=KEY)
            assert response.status_code == 202
            await asyncio.gather(*task_runs.background_runs())

        api.mark_processed.assert_not_called()
        assert _claimable("t1")

    async def test_task_in_progress_is_a_conflict(self, api, sample_findings):
        """A second trigger while the first run is going gets 409 and starts nothing."""
        api.fetch_findings.return_value = sample_findings
        release = asyncio.Event()

        async def slow_run(task_id):
            await release.wait()
            return True

        with patch("app.main.process_task", side_effect=slow_run) as run:
            first = await api.http.post("/tasks/t1/process", headers=KEY)
            second = await api.http.post("/tasks/t1/process", headers=KEY)
            other = await api.http.post("/tasks/t2/process", headers=KEY)
            assert (first.status_code, second.status_code, other.status_code) == (202, 409, 202)
            release.set()
            await asyncio.gather(*task_runs.background_runs())

        assert run.await_count == 2
        assert api.mark_processed.await_count == 2
        assert _claimable("t1") and _claimable("t2")

    async def test_scheduled_run_in_progress_is_a_conflict(self, api):
        """A task its scheduled job is processing is a 409; the backend is not even asked."""
        assert task_runs.try_claim("t1")
        try:
            response = await api.http.post("/tasks/t1/process", headers=KEY)
        finally:
            task_runs.release("t1")
        assert response.status_code == 409
        api.fetch_marker.assert_not_called()

    @pytest.mark.parametrize("case", ["already_processed", "no_findings", "unknown", "error"])
    async def test_early_answers_release_the_task(self, api, case):
        """Answers that start no run leave the task free for the next trigger."""
        if case == "already_processed":
            api.fetch_marker.return_value = {"processed_at": "2025-01-01T00:00:00+00:00"}
        elif case == "unknown":
            api.fetch_findings.return_value = None
        elif case == "error":
            api.fetch_findings.side_effect = RuntimeError("backend down")

        response = await api.http.post("/tasks/t1/process", headers=KEY)

        expected = {"already_processed": 200, "no_findings": 200, "unknown": 404, "error": 500}
        assert response.status_code == expected[case]
        assert task_runs.background_runs() == []
        assert _claimable("t1")

    async def test_shutdown_cancels_running_runs(self, api, sample_findings):
        """Shutdown cancels a run in progress; the task stays unmarked and is released."""
        api.fetch_findings.return_value = sample_findings
        started = asyncio.Event()

        async def endless_run(task_id):
            started.set()
            await asyncio.Event().wait()

        with patch("app.main.process_task", side_effect=endless_run):
            assert (await api.http.post("/tasks/t1/process", headers=KEY)).status_code == 202
            await started.wait()
            await task_runs.cancel_background_runs()

        assert task_runs.background_runs() == []
        api.mark_processed.assert_not_called()
        assert _claimable("t1")
