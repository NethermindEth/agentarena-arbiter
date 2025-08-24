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
    """Create and setup MongoDB handler for testing."""
    # Use a test database
    handler = MongoDBHandler()
    await handler.connect()
    
    yield handler
    
    # Cleanup
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
