"""
Integration tests for database operations.
These tests use an in-memory or test MongoDB instance.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.database.mongodb_handler import MongoDBHandler


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
        # Clean up metadata collection
        await handler.findings_db.drop_collection("metadata")

    except Exception as e:
        print(f"Warning: Error during test database cleanup: {e}")
    finally:
        await handler.close()


@pytest.mark.asyncio
class TestMongoDBIntegration:
    """Test MongoDB operations with a real database connection."""

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
