"""
Integration tests for all API endpoints.
Comprehensive testing including success scenarios, error handling, and edge cases.
"""
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone

from app.models.finding_input import FindingInput, Finding


class TestProcessFindingsEndpoint:
    """Test /process_findings endpoint."""
    
    @patch('app.main.post_submission')
    def test_process_findings_success(self, mock_post_sub, sample_task, client):
        """Test successful findings submission."""
        # Setup mocks
        client.mock_db.delete_agent_findings = AsyncMock(return_value=0)
        client.mock_db.create_finding = AsyncMock()
        mock_post_sub.return_value = AsyncMock()
        
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
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        client.mock_db.get_task = AsyncMock(return_value=sample_task)
        client.mock_db.delete_agent_findings = AsyncMock(return_value=0)
        client.mock_db.create_finding = AsyncMock()

        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 200
        result = response.json()
        
        assert result["task_id"] == "test-task-123"
        assert result["agent_id"] == "test-agent"
        assert result["total_findings"] == 1
    
    @patch('app.main.post_submission')
    def test_process_findings_multiple_submissions(self, mock_post_sub, sample_task, client):
        """Test that multiple submissions overwrite previous ones."""
        # Setup mocks
        client.mock_db.create_finding = AsyncMock()
        mock_post_sub.return_value = AsyncMock()
        
        # First submission
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
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        client.mock_db.get_task = AsyncMock(return_value=sample_task)
        client.mock_db.delete_agent_findings = AsyncMock(return_value=0)
        client.mock_db.create_finding = AsyncMock()
            
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
        
        client.mock_db.delete_agent_findings = AsyncMock(return_value=1)  # Shows previous findings were deleted
        
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
    def test_process_findings_max_findings_limit(self, mock_config, sample_task, client):
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
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        client.mock_db.get_task = AsyncMock(return_value=sample_task)
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 400
        assert "Maximum allowed: 2" in response.text

    def test_process_findings_task_not_found(self, client):
        """Test that non-existent task returns 404."""
        findings_data = FindingInput(
            task_id="nonexistent-task-id",  # This task won't exist in database
            findings=[
                Finding(
                    title="Non-existent Task Test",
                    description="This test uses a non-existent task ID.",
                    severity="Low",
                    file_paths=["test.sol"]
                )
            ]
        )
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        # Mock task not found
        client.mock_db.get_task = AsyncMock(return_value=None)
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 404
        assert "not found" in response.text
    
    def test_process_findings_invalid_api_key(self, client):
        """Test findings submission with invalid API key."""
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
        
        client.mock_db.get_agent_id = AsyncMock(side_effect=ValueError("Invalid API key"))
        
        response = client.post(
            "/process_findings",
            headers={"X-API-Key": "invalid-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 401
        assert "Invalid API key" in response.text
    
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
    def test_process_findings_empty_submission(self, mock_post_sub, sample_task, client):
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
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        client.mock_db.get_task = AsyncMock(return_value=sample_task)
        client.mock_db.delete_agent_findings = AsyncMock(return_value=2)
        client.mock_db.create_finding = AsyncMock()
        
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
        
        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        
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

        client.mock_db.get_agent_id = AsyncMock(return_value="test-agent")
        
        response = client.post(
            "/test/process_findings",
            headers={"X-API-Key": "test-key"},
            json=findings_data.model_dump()
        )
        
        assert response.status_code == 400
        assert "Invalid test task ID" in response.text
    
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


class TestTriggerTaskProcessingEndpoint:
    """Test the /tasks/{task_id}/process endpoint."""
    
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
    def test_trigger_task_processing_already_processed(self, mock_config, mock_mongodb, client):
        """Test triggering processing for already processed task."""
        mock_config.backend_api_key = "test-key"
        mock_mongodb.get_metadata = AsyncMock(return_value={
            "processed_at": "2025-01-01T00:00:00Z",
            "scheduled_processing": True
        })
        
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
    def test_trigger_task_processing_no_pending_findings(self, mock_config, mock_mongodb, client):
        """Test triggering processing when no pending findings exist."""
        mock_config.backend_api_key = "test-key"
        mock_mongodb.get_metadata = AsyncMock(return_value=None)
        mock_mongodb.get_findings = AsyncMock(return_value=[])
        
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
    """Test the /tasks/{task_id}/post endpoint."""
    
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
