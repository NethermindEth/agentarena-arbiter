"""
Integration tests for all API endpoints.
Comprehensive testing including success scenarios, error handling, and edge cases.
"""

from unittest.mock import AsyncMock, patch


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

    @patch("app.main.process_task", new_callable=AsyncMock, return_value=False)
    @patch("app.main.config")
    def test_trigger_task_processing_failure_is_not_marked(
        self, mock_config, _mock_process, client, sample_findings
    ):
        """A failed run answers 502 and leaves the task unmarked, so it can be retried."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_findings.return_value = sample_findings

        response = client.post("/tasks/test-task/process", headers={"X-API-Key": "test-key"})

        assert response.status_code == 502
        client.mock_mark_processed.assert_not_called()

    @patch("app.main.process_task", new_callable=AsyncMock, return_value=True)
    @patch("app.main.config")
    def test_trigger_task_processing_success_is_marked(
        self, mock_config, _mock_process, client, sample_findings
    ):
        """A completed run marks the task processed, as a manual run."""
        mock_config.backend_api_key = "test-key"
        client.mock_fetch_findings.return_value = sample_findings

        response = client.post("/tasks/test-task/process", headers={"X-API-Key": "test-key"})

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "processed"
        assert result["processed_at"] == "2026-01-02T00:00:00+00:00"
        client.mock_mark_processed.assert_called_once_with(
            "test-task", mock_config, scheduled=False
        )
