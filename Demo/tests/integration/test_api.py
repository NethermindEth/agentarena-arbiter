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
        client.mock_db.get_findings = AsyncMock(return_value=sample_findings)

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
        client.mock_db.get_findings = AsyncMock(return_value=[])

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
        client.mock_db.get_findings = AsyncMock(side_effect=Exception("Database connection failed"))

        response = client.get("/tasks/test-task/findings", headers={"X-API-Key": "test-key"})

        assert response.status_code == 500
        assert "Error retrieving findings" in response.text


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

    @patch("app.main.mongodb")
    @patch("app.main.config")
    def test_trigger_task_processing_already_processed(self, mock_config, mock_mongodb, client):
        """Test triggering processing for already processed task."""
        mock_config.backend_api_key = "test-key"
        mock_mongodb.get_metadata = AsyncMock(
            return_value={"processed_at": "2025-01-01T00:00:00Z", "scheduled_processing": True}
        )

        response = client.post("/tasks/test-task/process", headers={"X-API-Key": "test-key"})

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "already_processed"
        assert result["task_id"] == "test-task"

    @patch("app.main.mongodb")
    @patch("app.main.config")
    def test_trigger_task_processing_no_pending_findings(self, mock_config, mock_mongodb, client):
        """Test triggering processing when no pending findings exist."""
        mock_config.backend_api_key = "test-key"
        mock_mongodb.get_metadata = AsyncMock(return_value=None)
        mock_mongodb.get_findings = AsyncMock(return_value=[])

        response = client.post("/tasks/test-task/process", headers={"X-API-Key": "test-key"})

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "no_pending_findings"
        assert result["task_id"] == "test-task"
        assert result["total_findings"] == 0


class TestPostTaskFindingsEndpoint:
    """Test the /tasks/{task_id}/post endpoint."""

    @patch("app.main.config")
    def test_post_task_findings_invalid_api_key(self, mock_config, client):
        """Test posting findings with invalid API key."""
        mock_config.backend_api_key = "correct-key"

        response = client.post("/tasks/test-task/post", headers={"X-API-Key": "wrong-key"})

        assert response.status_code == 401
        assert "Invalid API key" in response.text

    @patch("app.main.config")
    def test_post_task_findings_no_backend_endpoint(self, mock_config, client):
        """Test posting findings when backend endpoint not configured."""
        mock_config.backend_api_key = "test-key"
        mock_config.backend_findings_endpoint = None

        response = client.post("/tasks/test-task/post", headers={"X-API-Key": "test-key"})

        assert response.status_code == 503
        assert "Backend findings endpoint not configured" in response.text

    @patch("app.main.config")
    def test_post_task_findings_no_findings(self, mock_config, client):
        """Test posting findings when no findings exist for task."""
        mock_config.backend_api_key = "test-key"
        mock_config.backend_findings_endpoint = "http://test.com/findings"
        client.mock_db.get_findings = AsyncMock(return_value=[])

        response = client.post("/tasks/test-task/post", headers={"X-API-Key": "test-key"})

        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "no_findings"
        assert result["task_id"] == "test-task"
        assert result["total_findings"] == 0
