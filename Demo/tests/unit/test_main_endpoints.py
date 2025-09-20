"""
Unit tests for main.py functions and endpoints.
These tests focus on individual functions and simpler endpoint scenarios.
"""
import pytest
from unittest.mock import AsyncMock, patch, Mock
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.models.finding_input import FindingInput, Finding


class TestRootEndpoint:
    """Test the root endpoint."""
    
    def test_root_endpoint(self):
        """Test that root endpoint returns welcome message."""
        # Import here to avoid lifespan issues
        from app.main import app
        client = TestClient(app)
        
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Welcome to the ArbiterAgent API!"}


class TestPostSubmission:
    """Test the post_submission utility function."""
    
    @pytest.mark.asyncio
    @patch('app.main.config')
    @patch('httpx.AsyncClient')
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
            json={
                "task_id": "test-task",
                "agent_id": "test-agent", 
                "findings_count": 5
            },
            headers={"X-API-Key": "test-key"}
        )
    
    @pytest.mark.asyncio
    @patch('app.main.config')
    async def test_post_submission_no_endpoint_configured(self, mock_config):
        """Test submission posting when endpoint is not configured."""
        from app.main import post_submission
        
        # Setup config with no endpoint
        mock_config.backend_submissions_endpoint = None
        
        # Should not raise exception, just skip posting
        await post_submission("test-task", "test-agent", 5)
    
    @pytest.mark.asyncio
    @patch('app.main.config')  
    @patch('httpx.AsyncClient')
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
    @patch('app.main.config')
    @patch('httpx.AsyncClient')
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
    
    @patch('app.main.mongodb', new_callable=AsyncMock)
    @pytest.mark.asyncio
    async def test_get_latest_findings_with_last_sync(self, mock_mongodb, sample_findings):
        """Test getting findings with existing last sync timestamp."""
        from app.main import get_latest_findings
        
        # Setup mock for last sync
        last_sync_time = datetime.now(timezone.utc)
        mock_mongodb.get_metadata.return_value = {"timestamp": last_sync_time}
        mock_mongodb.get_findings.return_value = sample_findings[:1]  # Only one new finding
        
        result = await get_latest_findings("test-task", "test-agent")
        
        # Verify the correct call was made
        mock_mongodb.get_metadata.assert_called_once_with("last_sync_test-task_test-agent")
        mock_mongodb.get_findings.assert_called_once_with(
            task_id="test-task",
            agent_id="test-agent", 
            since_timestamp=last_sync_time
        )
        
        assert result == sample_findings[:1]
    
    @patch('app.main.mongodb', new_callable=AsyncMock)
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
            task_id="test-task",
            agent_id="test-agent"
        )
        
        assert result == sample_findings
    
    @patch('app.main.mongodb', new_callable=AsyncMock)
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
            task_id="test-task",
            agent_id="test-agent"
        )
        
        assert result == []


class TestProcessFindingsErrorScenarios:
    """Test error scenarios in the process_findings endpoint."""
    
    def test_process_findings_invalid_agent_authentication(self, sample_task, client):
        """Test process_findings when agent authentication fails."""
        findings_data = FindingInput(
            task_id="test-task-123",
            findings=[
                Finding(
                    title="Test Finding",
                    description="Test description",
                    severity="High",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        # Mock get_agent_id to raise ValueError (invalid API key)
        client.mock_db.get_agent_id = AsyncMock(side_effect=ValueError("Invalid API key"))
        client.mock_db.get_task = AsyncMock(return_value=sample_task)

        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "invalid-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 401
        assert "Invalid API key" in response.text
    
    def test_process_findings_task_not_found(self, client):
        """Test process_findings when task is not found in database."""
        findings_data = FindingInput(
            task_id="nonexistent-task",
            findings=[
                Finding(
                    title="Test Finding",
                    description="Test description",
                    severity="High",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        client.mock_db.get_task = AsyncMock(return_value=None)
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 404
        assert "not found" in response.text
    
    def test_process_findings_submission_before_start_time(self, client):
        """Test submission before task start time."""
        from app.types import Task
        
        # Task with future start time
        future_task = Task(
            taskId="test-task-123",
            projectRepo="https://example.com/repo.git",
            title="Test Task",
            description="Test",
            bounty=None,
            status="Open",
            startTime=str(int(datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())),  # Future
            deadline="1893456000",
            selectedBranch="main",
            selectedFiles=["contracts/Vault.sol"],
            selectedDocs=[],
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[],
            commitSha="abc123"
        )
        
        findings_data = FindingInput(
            task_id="test-task-123",
            findings=[
                Finding(
                    title="Early Submission",
                    description="This submission is too early",
                    severity="Medium",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        client.mock_db.get_task = AsyncMock(return_value=future_task)
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 403
        assert "Submission period has not started yet" in response.text
    
    def test_process_findings_submission_after_deadline(self, client):
        """Test submission after task deadline."""
        from app.types import Task
        
        # Task with past deadline
        past_task = Task(
            taskId="test-task-123",
            projectRepo="https://example.com/repo.git",
            title="Test Task",
            description="Test",
            bounty=None,
            status="Open",
            startTime="1000000000",  # Past
            deadline=str(int(datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())),  # Past
            selectedBranch="main",
            selectedFiles=["contracts/Vault.sol"],
            selectedDocs=[],
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[],
            commitSha="abc123"
        )
        
        findings_data = FindingInput(
            task_id="test-task-123",
            findings=[
                Finding(
                    title="Late Submission",
                    description="This submission is too late",
                    severity="Low",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        client.mock_db.get_task = AsyncMock(return_value=past_task)
           
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 403
        assert "Submission period has ended" in response.text
    
    @patch('app.main.post_submission')
    def test_process_findings_database_error(self, mock_post_sub, sample_task, client):
        """Test process_findings when database operations fail."""
        # Setup mocks
        client.mock_db.delete_agent_findings = AsyncMock(return_value=0)
        client.mock_db.create_finding = AsyncMock(side_effect=Exception("Database error"))
        mock_post_sub.return_value = AsyncMock()
        
        findings_data = FindingInput(
            task_id="test-task-123",
            findings=[
                Finding(
                    title="Database Error Test",
                    description="This should cause a database error",
                    severity="Medium",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        client.mock_db.get_task = AsyncMock(return_value=sample_task)
        client.mock_db.delete_agent_findings = AsyncMock(return_value=0)
        client.mock_db.create_finding = AsyncMock(side_effect=Exception("Database error"))
            
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 500
        assert "Error processing findings" in response.text


class TestBackgroundProcessingErrorScenarios:
    """Test error scenarios in the background processing endpoint."""
    
    def test_background_processing_invalid_agent_authentication(self, client):
        """Test background processing when agent authentication fails."""
        findings_data = FindingInput(
            task_id="TESTTASK",
            findings=[
                Finding(
                    title="Test Finding",
                    description="Test description",
                    severity="High",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        # Mock get_agent_id to raise ValueError (invalid API key)
        client.mock_db.get_agent_id = AsyncMock(side_effect=ValueError("Invalid API key"))
            
        response = client.post(
            "/test/process_findings",
            headers={"X-API-Key": "invalid-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 401
        assert "Invalid API key" in response.text
    
    @patch('app.main.config')
    def test_background_processing_max_findings_exceeded(self, mock_config, client):
        """Test background processing with too many findings."""

        # Set low limit for this test
        mock_config.max_findings_per_submission = 1
        
        # Create more findings than allowed
        findings = []
        for i in range(2):
            findings.append(Finding(
                title=f"Test Finding {i+1}",
                description=f"Description {i+1}",
                severity="Medium",
                file_paths=["test.sol"]
            ))
        
        findings_data = FindingInput(
            task_id="TESTTASK",
            findings=findings
        )
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
            
        response = client.post(
            "/test/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 400
        assert "Maximum allowed: 1" in response.text
    
    @patch('app.main.post_submission')
    def test_background_processing_database_error(self, mock_post_sub, client):
        """Test background processing when database operations fail."""
        mock_post_sub.return_value = AsyncMock()
        
        findings_data = FindingInput(
            task_id="TESTTASK",
            findings=[
                Finding(
                    title="Database Error Test",
                    description="This should cause a database error",
                    severity="Medium",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        # Setup mongodb mock to raise exception
        client.mock_db.create_finding = AsyncMock(side_effect=Exception("Database error"))
            
        response = client.post(
            "/test/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 500
        assert "Error in test processing" in response.text


class TestTriggerTaskProcessingEndpoint:
    """Test the trigger_task_processing endpoint basic scenarios."""
    
    @patch('app.main.config')
    def test_trigger_task_processing_invalid_api_key(self, mock_config, client):
        """Test trigger processing with invalid API key."""
        mock_config.backend_api_key = "correct-key"

        response = client.post(
            "/tasks/test-task/process",
            headers={"X-API-Key": "wrong-key"}
        )
            
        assert response.status_code == 401
        assert "Invalid API key" in response.text
    
    def test_trigger_task_processing_missing_api_key(self, client):
        """Test trigger processing without API key header."""
        response = client.post("/tasks/test-task/process")
        
        assert response.status_code == 422  # Missing required header
    
    @patch('app.main.mongodb')
    @patch('app.main.config')
    def test_trigger_task_processing_already_processed(self, mock_config, mock_mongodb):
        """Test triggering processing for already processed task."""
        from app.main import app
        
        mock_config.backend_api_key = "test-key"
        mock_mongodb.get_metadata = AsyncMock(return_value={
            "processed_at": "2025-01-01T00:00:00Z",
            "scheduled_processing": True
        })
        
        client = TestClient(app)
        response = client.post(
            "/tasks/test-task/process",
            headers={"X-API-Key": "test-key"}
        )
        
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "already_processed"
        assert result["task_id"] == "test-task"
    
    @patch('app.main.mongodb') 
    @patch('app.main.config')
    def test_trigger_task_processing_no_pending_findings(self, mock_config, mock_mongodb):
        """Test triggering processing when no pending findings exist."""
        from app.main import app
        
        mock_config.backend_api_key = "test-key"
        mock_mongodb.get_metadata = AsyncMock(return_value=None)
        mock_mongodb.get_findings = AsyncMock(return_value=[])
        
        client = TestClient(app)
        response = client.post(
            "/tasks/test-task/process", 
            headers={"X-API-Key": "test-key"}
        )
        
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "no_pending_findings" 
        assert result["task_id"] == "test-task"
        assert result["total_findings"] == 0


class TestPostTaskFindingsEndpoint:
    """Test the post_task_findings endpoint basic scenarios."""
    
    @patch('app.main.config')
    def test_post_task_findings_invalid_api_key(self, mock_config, client):
        """Test posting findings with invalid API key."""
        
        mock_config.backend_api_key = "correct-key"

        response = client.post(
            "/tasks/test-task/post",
            headers={"X-API-Key": "wrong-key"}
        )
            
        assert response.status_code == 401
        assert "Invalid API key" in response.text
    
    @patch('app.main.config')
    def test_post_task_findings_no_backend_endpoint(self, mock_config, client):
        """Test posting findings when backend endpoint not configured."""

        mock_config.backend_api_key = "test-key" 
        mock_config.backend_findings_endpoint = None
        
        response = client.post(
            "/tasks/test-task/post",
            headers={"X-API-Key": "test-key"}
        )
        
        assert response.status_code == 503
        assert "Backend findings endpoint not configured" in response.text
    
    @patch('app.main.config') 
    def test_post_task_findings_no_findings(self, mock_config, client):
        """Test posting findings when no findings exist for task."""

        mock_config.backend_api_key = "test-key"
        mock_config.backend_findings_endpoint = "http://test.com/findings"
        client.mock_db.get_findings = AsyncMock(return_value=[])
        
        response = client.post(
            "/tasks/test-task/post",
            headers={"X-API-Key": "test-key"}
        )
        
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "no_findings"
        assert result["task_id"] == "test-task"
        assert result["total_findings"] == 0


class TestGetTaskFindingsEndpoint:
    """Test additional scenarios for get_task_findings endpoint."""
    
    @patch('app.main.config')
    def test_get_task_findings_invalid_api_key(self, mock_config, client):
        """Test getting task findings with invalid API key."""
        
        mock_config.backend_api_key = "correct-key"
        
        response = client.get(
            "/tasks/test-task/findings", 
            headers={"X-API-Key": "wrong-key"}
        )
        
        assert response.status_code == 401
        assert "Invalid API key" in response.text
    
    @patch('app.main.config')
    def test_get_task_findings_database_error(self, mock_config, client):
        """Test getting task findings when database error occurs."""
        
        mock_config.backend_api_key = "test-key"
        client.mock_db.get_findings = AsyncMock(side_effect=Exception("Database connection failed"))
        
        response = client.get(
            "/tasks/test-task/findings",
            headers={"X-API-Key": "test-key"}
        )
        
        assert response.status_code == 500
        assert "Error retrieving findings" in response.text
