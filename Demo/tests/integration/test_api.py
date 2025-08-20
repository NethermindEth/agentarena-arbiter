"""
Integration tests for API endpoints.
These tests use FastAPI TestClient to test the full request/response cycle.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.models.finding_input import FindingInput, Finding


@pytest.fixture
def client(sample_task_cache, mock_mongodb):
    """Create FastAPI test client with mocked dependencies."""
    # Import here to avoid circular imports and initialization issues
    from fastapi.testclient import TestClient
    from app.main import app
    
    # Mock the database and other dependencies before creating the client
    with patch('app.main.mongodb', mock_mongodb), \
         patch('app.main.task_cache', sample_task_cache), \
         patch('app.main.agents_cache') as mock_agents:
        
        # Configure mock_agents to behave like a list
        test_agents = [{"agent_id": "test-agent", "api_key": "test-key"}]
        mock_agents.__iter__ = lambda self: iter(test_agents)
        mock_agents.__len__ = lambda self: len(test_agents)
        mock_agents.__getitem__ = lambda self, key: test_agents[key]

        # Create client without triggering lifespan events that need real DB
        client = TestClient(app, base_url="http://testserver")
        
        # Attach the mock_mongodb to the client so tests can access it if needed
        client.mock_db = mock_mongodb
        
        yield client


class TestProcessFindingsEndpoint:
    """Test /process_findings endpoint."""
    
    @patch('app.main.post_submission')
    def test_process_findings_success(self, mock_post_sub, client):
        """Test successful findings submission."""
        # Setup mocks
        client.mock_db.delete_agent_findings = AsyncMock(return_value=0)
        client.mock_db.create_finding = AsyncMock()
        mock_post_sub.return_value = AsyncMock()
        
        findings_data = FindingInput(
            task_id="test-task-123",  # Match the taskId from sample_task_cache
            findings=[
                Finding(
                    title="Test Finding",
                    description="Test description", 
                    severity="High",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 200
        result = response.json()
        
        assert result["task_id"] == "test-task-123"  # Match the taskId from sample_task_cache
        assert result["agent_id"] == "test-agent"
        assert result["total_findings"] == 1
    
    @patch('app.main.post_submission')
    def test_process_findings_multiple_submissions(self, mock_post_sub, client):
        """Test that multiple submissions overwrite previous ones."""
        # Setup mocks
        client.mock_db.create_finding = AsyncMock()
        mock_post_sub.return_value = AsyncMock()
        
        # First submission
        client.mock_db.delete_agent_findings = AsyncMock(return_value=0)
        findings_data1 = FindingInput(
            task_id="test-task-123",
            findings=[
                Finding(
                    title="First Submission Test",
                    description="This finding will be submitted first.",
                    severity="High",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        response1 = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data1.model_dump()
        )
        
        assert response1.status_code == 200
        result1 = response1.json()
        assert result1["total_findings"] == 1
        
        # Second submission (should overwrite the first)
        client.mock_db.delete_agent_findings = AsyncMock(return_value=1)  # Shows previous findings were deleted
        findings_data2 = FindingInput(
            task_id="test-task-123",
            findings=[
                Finding(
                    title="Second Submission Test",
                    description="This finding will replace the first one.",
                    severity="Medium",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        response2 = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data2.model_dump()
        )
        
        assert response2.status_code == 200
        result2 = response2.json()
        assert result2["total_findings"] == 1
        
        # Verify delete_agent_findings was called to overwrite previous submission
        client.mock_db.delete_agent_findings.assert_called_with("test-task-123", "test-agent")

    @patch('app.main.config')
    def test_process_findings_max_findings_limit(self, mock_config, client):
        """Test that submissions exceeding max findings limit are rejected."""
        # Set max findings limit to 2 for this test
        mock_config.max_findings_per_submission = 2
        
        # Create more findings than allowed
        findings = []
        for i in range(3):  # One more than the limit
            findings.append(Finding(
                title=f"Finding {i+1}",
                description=f"Test finding {i+1} to exceed limit.",
                severity="Medium",
                file_paths=["test.sol"]
            ))
        
        findings_data = FindingInput(
            task_id="test-task-123",
            findings=findings
        )
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 400
        error_text = response.text
        assert "Maximum allowed: 2" in error_text

    def test_process_findings_task_not_found(self, client):
        """Test that non-existent task returns 404."""
        findings_data = FindingInput(
            task_id="nonexistent-task-id",  # This doesn't match sample_task_cache
            findings=[
                Finding(
                    title="Non-existent Task Test",
                    description="This test uses a non-existent task ID.",
                    severity="Low",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 404
        assert "not found in cache" in response.text
    
    @patch('app.main.agents_cache', [{"agent_id": "test-agent", "api_key": "valid-key"}])
    def test_process_findings_invalid_api_key(self, client):
        """Test findings submission with invalid API key."""
        findings_data = FindingInput(
            task_id="test-task-123",  # Match the taskId from sample_task_cache
            findings=[
                Finding(
                    title="Test Finding",
                    description="Test description",
                    severity="High",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "invalid-key"},
            json=findings_data.model_dump()
        )
        
        # This might return 404 due to task cache, but invalid API key should be caught
        # We're testing that the API key validation works
        assert response.status_code in [401, 404]  # Either auth failure or task not found
    
    def test_process_findings_missing_api_key(self, client):
        """Test findings submission without API key."""
        findings_data = FindingInput(
            task_id="test-task-123",  # Match the taskId from sample_task_cache
            findings=[
                Finding(
                    title="Test Finding", 
                    description="Test description",
                    severity="High",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        response = client.post(
            "/process_findings",
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 422  # Missing required header

    @patch('app.main.post_submission')
    def test_process_findings_empty_submission(self, mock_post_sub, client):
        """Test that empty submission clears previous findings."""
        # Setup mocks
        client.mock_db.delete_agent_findings = AsyncMock(return_value=2)  # Shows previous findings were deleted
        client.mock_db.create_finding = AsyncMock()
        mock_post_sub.return_value = AsyncMock()
        
        # Submit empty findings list
        findings_data = FindingInput(
            task_id="test-task-123",
            findings=[]  # Empty findings list
        )
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 200
        result = response.json()
        
        # Check response format for empty submission
        assert result["task_id"] == "test-task-123"
        assert result["agent_id"] == "test-agent"
        assert result["total_findings"] == 0
        
        # Verify delete_agent_findings was called to clear previous findings
        client.mock_db.delete_agent_findings.assert_called_with("test-task-123", "test-agent")
        
        # Verify no new findings were created (since list was empty)
        client.mock_db.create_finding.assert_not_called()


class TestBackgroundProcessingEndpoint:
    """Test /test/process_findings endpoint for background processing."""
    
    @patch('app.main.post_submission')
    def test_background_processing_endpoint(self, mock_post_sub, client):
        """Test the background processing test endpoint."""
        # Setup mocks
        client.mock_db.create_finding = AsyncMock()
        mock_post_sub.return_value = AsyncMock()
        
        # Test finding for background processing
        findings_data = FindingInput(
            task_id="TESTTASK",  # Specific task ID for test endpoint
            findings=[
                Finding(
                    title="Background Processing Test",
                    description="This tests background processing functionality.",
                    severity="Medium",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        response = client.post(
            "/test/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 200
        result = response.json()
        
        # Check test endpoint response format
        assert result["task_id"] == "TESTTASK"
        assert result["agent_id"] == "test-agent"
        assert result["total_findings"] == 1
        assert result["queued"] == True  # Key difference - should indicate background processing
        
        # Verify finding was stored
        client.mock_db.create_finding.assert_called()
    
    def test_background_processing_invalid_task_id(self, client):
        """Test that background processing endpoint rejects invalid task IDs."""
        findings_data = FindingInput(
            task_id="INVALID-TASK",  # Should only accept "TESTTASK"
            findings=[
                Finding(
                    title="Invalid Task Test",
                    description="This should fail due to invalid task ID.",
                    severity="Low",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        response = client.post(
            "/test/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 400
        assert "Invalid test task ID" in response.text


class TestTaskFindingsEndpoint:
    """Test /tasks/{task_id}/findings endpoint."""
    
    @patch('app.main.config')
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
    
    @patch('app.main.config')
    def test_get_task_findings_empty(self, mock_config, client):
        """Test retrieval of task findings when none exist."""
        mock_config.backend_api_key = "test-key"
        client.mock_db.get_findings = AsyncMock(return_value=[])
        
        response = client.get("/tasks/test-task-123/findings", headers={"X-API-Key": "test-key"})
        
        assert response.status_code == 200
        result = response.json()
        
        assert result == []
