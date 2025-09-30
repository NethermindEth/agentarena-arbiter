"""
Integration tests for database operations.
These tests use an in-memory or test MongoDB instance.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from app.database.mongodb_handler import MongoDBHandler
from app.models.finding_db import Status


@pytest_asyncio.fixture
async def db_handler():
    """Create and setup MongoDB handler for testing with isolated test database."""
    
    # Create test database names
    handler = MongoDBHandler()
    handler.findings_db_name = "test_security_findings"
    handler.agent_arena_db_name = "test_agent_arena"
    
    await handler.connect()
    
    # Clean up any existing test data at start
    await handler.findings_db.drop_collection("test_security_findings") 
    await handler.agent_arena_db.drop_collection("test_agent_arena")
    
    yield handler
    
    # Complete cleanup after tests
    try:
        # Drop all test collections to ensure clean state
        collections = await handler.findings_db.list_collection_names()
        for collection_name in collections:
            if collection_name.startswith("findings_"):
                await handler.findings_db.drop_collection(collection_name)
        
        # Clean up metadata collection
        await handler.findings_db.drop_collection("metadata")
        
    except Exception as e:
        print(f"Warning: Error during test database cleanup: {e}")
    finally:
        await handler.close()


@pytest.mark.asyncio
class TestMongoDBIntegration:
    """Test MongoDB operations with a real database connection."""
    
    async def test_create_and_get_finding(self, db_handler: MongoDBHandler):
        """Test creating and retrieving a finding."""
        from app.models.finding_input import Finding, Severity as InputSeverity
        
        test_finding = Finding(
            title="Test Security Issue",
            description="This is a test security finding",
            severity=InputSeverity.HIGH,
            file_paths=["contracts/Test.sol"]
        )
        
        task_id = "test-task-db"
        agent_id = "test-agent-db"
        
        # Clean any existing data
        await db_handler.delete_agent_findings(task_id, agent_id)
        
        # Create finding
        created_finding = await db_handler.create_finding(
            task_id=task_id,
            agent_id=agent_id,
            finding=test_finding,
            status=Status.PENDING
        )
        
        assert created_finding is not None
        assert created_finding.title == test_finding.title
        assert created_finding.agent_id == agent_id
        assert created_finding.status == Status.PENDING
        
        # Retrieve findings
        retrieved_findings = await db_handler.get_findings(task_id=task_id)
        
        assert len(retrieved_findings) == 1
        assert retrieved_findings[0].title == test_finding.title
        assert retrieved_findings[0].agent_id == agent_id
    
    async def test_update_finding_status(self, db_handler: MongoDBHandler):
        """Test updating finding status."""
        from app.models.finding_input import Finding, Severity as InputSeverity
        
        test_finding = Finding(
            title="Test Update Finding",
            description="This finding will be updated",
            severity=InputSeverity.MEDIUM,
            file_paths=["contracts/Update.sol"]
        )
        
        task_id = "test-update-task"
        agent_id = "test-update-agent"
        
        # Clean any existing data
        await db_handler.delete_agent_findings(task_id, agent_id)
        
        # Create finding
        created_finding = await db_handler.create_finding(
            task_id=task_id,
            agent_id=agent_id,
            finding=test_finding,
            status=Status.PENDING
        )
        
        # Update status
        await db_handler.update_finding(
            task_id,
            created_finding.str_id,
            {"status": Status.UNIQUE_VALID}
        )
        
        # Retrieve and verify update
        updated_findings = await db_handler.get_findings(task_id=task_id)

        assert len(updated_findings) == 1
        assert updated_findings[0].status == Status.UNIQUE_VALID
    
    async def test_get_findings_by_status(self, db_handler: MongoDBHandler):
        """Test filtering findings by status."""
        from app.models.finding_input import Finding, Severity as InputSeverity
        
        task_id = "test-status-filter"
        agent_id = "test-status-agent"
        
        # Clean any existing data
        await db_handler.delete_agent_findings(task_id, agent_id)
        
        # Create findings with different statuses
        finding1 = Finding(
            title="Pending Finding",
            description="This will stay pending",
            severity=InputSeverity.LOW,
            file_paths=["test1.sol"]
        )
        
        finding2 = Finding(
            title="Valid Finding", 
            description="This will become valid",
            severity=InputSeverity.HIGH,
            file_paths=["test2.sol"]
        )
        
        # Create both findings
        created1 = await db_handler.create_finding(
            task_id, agent_id, finding1, Status.PENDING
        )
        created2 = await db_handler.create_finding(
            task_id, agent_id, finding2, Status.PENDING
        )
        
        # Update one to be valid
        await db_handler.update_finding(
            task_id,
            created2.str_id,
            {"status": Status.UNIQUE_VALID}
        )
        
        # Test filtering
        pending_findings = await db_handler.get_findings(
            task_id=task_id, status=Status.PENDING
        )
        valid_findings = await db_handler.get_findings(
            task_id=task_id, status=Status.UNIQUE_VALID  
        )
        
        assert len(pending_findings) == 1
        assert len(valid_findings) == 1
        assert pending_findings[0].title == "Pending Finding"
        assert valid_findings[0].title == "Valid Finding"
    
    async def test_metadata_operations(self, db_handler: MongoDBHandler):
        """Test metadata storage and retrieval."""
        key = "test_metadata_key"
        value = {
            "timestamp": datetime.now(timezone.utc),
            "processed": True,
            "count": 42
        }
        
        # Set metadata
        await db_handler.set_metadata(key, value)
        
        # Get metadata
        retrieved = await db_handler.get_metadata(key)
        
        assert retrieved is not None
        assert retrieved["processed"] == True
        assert retrieved["count"] == 42
        
        # Test non-existent key
        non_existent = await db_handler.get_metadata("non_existent_key")
        assert non_existent is None
    
    async def test_create_findings_batch_empty(self, db_handler: MongoDBHandler):
        """Test creating a batch with empty findings list."""
        from app.models.finding_input import FindingInput
        
        # Empty findings batch
        empty_input = FindingInput(task_id="test-empty", findings=[])
        
        result = await db_handler.create_findings_batch("test-agent", empty_input)
        
        assert result == []
    
    async def test_create_findings_batch_success(self, db_handler: MongoDBHandler):
        """Test creating a batch of findings successfully."""
        from app.models.finding_input import FindingInput, Finding, Severity as InputSeverity
        
        findings = [
            Finding(
                title="Batch Finding 1",
                description="First finding in batch",
                severity=InputSeverity.HIGH,
                file_paths=["test1.sol"]
            ),
            Finding(
                title="Batch Finding 2", 
                description="Second finding in batch",
                severity=InputSeverity.MEDIUM,
                file_paths=["test2.sol"]
            )
        ]
        
        batch_input = FindingInput(task_id="test-batch", findings=findings)
        
        # Clean any existing data
        await db_handler.delete_agent_findings("test-batch", "batch-agent")
        
        result = await db_handler.create_findings_batch("batch-agent", batch_input)
        
        # Should return titles of created findings
        assert len(result) == 2
        assert "Batch Finding 1" in result
        assert "Batch Finding 2" in result
        
        # Verify findings were actually created
        created_findings = await db_handler.get_findings("test-batch")
        assert len(created_findings) == 2
    
    async def test_update_finding_with_finding_object(self, db_handler: MongoDBHandler):
        """Test updating finding with FindingDB object instead of dict."""
        from app.models.finding_input import Finding, Severity as InputSeverity
        from app.models.finding_db import FindingDB, Status
        
        # Create a test finding
        test_finding = Finding(
            title="Update Test Finding",
            description="Finding to be updated",
            severity=InputSeverity.LOW,
            file_paths=["update.sol"]
        )
        
        task_id = "test-update-object"
        agent_id = "test-update-agent"
        
        # Clean and create
        await db_handler.delete_agent_findings(task_id, agent_id)
        created = await db_handler.create_finding(task_id, agent_id, test_finding)
        
        # Create FindingDB object for update
        update_finding = FindingDB(
            title="Updated Finding Title",
            description="Updated description", 
            severity=InputSeverity.HIGH,
            file_paths=["updated.sol"],
            agent_id=agent_id,
            status=Status.UNIQUE_VALID
        )
        
        # Update with FindingDB object (not dict)
        success = await db_handler.update_finding(task_id, created.str_id, update_finding)
        
        assert success == True
        
        # Verify update
        updated_findings = await db_handler.get_findings(task_id)
        assert len(updated_findings) == 1
        assert updated_findings[0].title == "Updated Finding Title"
        assert updated_findings[0].status == Status.UNIQUE_VALID
    
    async def test_update_finding_invalid_id(self, db_handler: MongoDBHandler):
        """Test updating finding with invalid ObjectId."""
        
        # Try to update with invalid ID
        success = await db_handler.update_finding(
            "test-task",
            "invalid-object-id", 
            {"title": "Should not work"}
        )
        
        assert success == False
