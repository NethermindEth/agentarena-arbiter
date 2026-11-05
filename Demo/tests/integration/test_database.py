"""
Integration tests for database operations.
These tests use an in-memory or test MongoDB instance.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.database.mongodb_handler import MongoDBHandler
from app.models.finding_db import Status


@pytest_asyncio.fixture
async def db_handler():
    """Create and setup MongoDB handler for testing with isolated test database."""

    # Create test database names
    handler = MongoDBHandler()
    handler.findings_db_name = "test_security_findings"

    await handler.connect()

    # Clean up any existing test data at start
    await handler.findings_db.drop_collection("test_security_findings")

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


async def _insert_finding(
    handler: MongoDBHandler, task_id: str, agent_id: str, title: str, status: Status
) -> None:
    """Seed a finding as the backend stores it; the arbiter only writes through the backend."""
    now = datetime.now(UTC)
    await handler.findings_db[handler.get_findings_collection_name(task_id)].insert_one(
        {
            "title": title,
            "description": f"{title} description",
            "severity": "High",
            "file_paths": ["contracts/Test.sol"],
            "agent_id": agent_id,
            "status": status.value,
            "created_at": now,
            "updated_at": now,
        }
    )


@pytest.mark.asyncio
class TestMongoDBIntegration:
    """Test MongoDB operations with a real database connection."""

    async def test_get_findings(self, db_handler: MongoDBHandler):
        """Findings stored in a task's collection are read back as FindingDB."""
        task_id = "test-task-db"
        await _insert_finding(db_handler, task_id, "test-agent-db", "Test Issue", Status.PENDING)

        retrieved_findings = await db_handler.get_findings(task_id=task_id)

        assert len(retrieved_findings) == 1
        assert retrieved_findings[0].title == "Test Issue"
        assert retrieved_findings[0].agent_id == "test-agent-db"
        assert retrieved_findings[0].status == Status.PENDING
        assert retrieved_findings[0].id is not None

    async def test_get_findings_by_status_and_agent(self, db_handler: MongoDBHandler):
        """The status and agent filters narrow the findings read."""
        task_id = "test-status-filter"
        await _insert_finding(db_handler, task_id, "agent-a", "Pending Finding", Status.PENDING)
        await _insert_finding(db_handler, task_id, "agent-a", "Valid Finding", Status.UNIQUE_VALID)
        await _insert_finding(db_handler, task_id, "agent-b", "Other Agent", Status.PENDING)

        pending = await db_handler.get_findings(task_id=task_id, status=Status.PENDING)
        valid = await db_handler.get_findings(task_id=task_id, status=Status.UNIQUE_VALID)
        agent_a = await db_handler.get_findings(task_id=task_id, agent_id="agent-a")

        assert {f.title for f in pending} == {"Pending Finding", "Other Agent"}
        assert [f.title for f in valid] == ["Valid Finding"]
        assert {f.title for f in agent_a} == {"Pending Finding", "Valid Finding"}

    async def test_metadata_operations(self, db_handler: MongoDBHandler):
        """Test metadata storage and retrieval."""
        key = "test_metadata_key"
        value = {"timestamp": datetime.now(UTC), "processed": True, "count": 42}

        # Set metadata
        await db_handler.set_metadata(key, value)

        # Get metadata
        retrieved = await db_handler.get_metadata(key)

        assert retrieved is not None
        assert retrieved["processed"] is True
        assert retrieved["count"] == 42

        # Test non-existent key
        non_existent = await db_handler.get_metadata("non_existent_key")
        assert non_existent is None
