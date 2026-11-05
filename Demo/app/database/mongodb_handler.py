"""
MongoDB database handler for security findings.
Reads findings and keeps metadata with native MongoDB Motor operations; findings are written
through the backend (app/backend_findings.py).
"""

import os
from datetime import datetime
from typing import Any

import motor.motor_asyncio

from app.config import config
from app.models.finding_db import FindingDB, Status


class MongoDBHandler:
    """
    MongoDB database handler using Motor for async operations.
    Uses Pydantic models for data validation and serialization.
    """

    def __init__(self, connection_string: str | None = None):
        """
        Initialize MongoDB handler with a connection string.

        Args:
            connection_string: MongoDB connection string
        """
        self.client: motor.motor_asyncio.AsyncIOMotorClient | None = None
        self._findings_db: motor.motor_asyncio.AsyncIOMotorDatabase | None = None

        # Automatically select MongoDB URL based on environment
        is_docker = os.path.exists("/.dockerenv")  # Docker environment detection
        default_mongo_url = "mongodb://mongodb:27017" if is_docker else "mongodb://localhost:27017"

        self.connection_string = connection_string or config.mongodb_url or default_mongo_url
        self.findings_db_name = "security_findings"
        self.metadata_collection = "metadata"

    async def connect(self):
        """Connect to the MongoDB database."""
        self.client = motor.motor_asyncio.AsyncIOMotorClient(self.connection_string)
        self._findings_db = self.client[self.findings_db_name]

    async def close(self):
        """Close MongoDB connection."""
        if self.client:
            self.client.close()

    @property
    def findings_db(self) -> motor.motor_asyncio.AsyncIOMotorDatabase:
        """The arbiter's own database; connect() must have run."""
        if self._findings_db is None:
            raise RuntimeError("MongoDB is not connected; call connect() first")
        return self._findings_db

    def get_findings_collection_name(self, task_id: str) -> str:
        """
        Get findings collection name for a task.

        Args:
            task_id: Task identifier

        Returns:
            Findings collection name for the task
        """
        return f"findings_{task_id}"

    async def get_metadata(self, key: str) -> dict[str, Any] | None:
        """
        Get metadata from the metadata collection.

        Args:
            key: Metadata key

        Returns:
            Metadata value if found, None otherwise
        """
        # Query database
        doc = await self.findings_db[self.metadata_collection].find_one({"key": key})

        return doc

    async def set_metadata(self, key: str, value: dict[str, Any]) -> bool:
        """
        Set metadata in the metadata collection.

        Args:
            key: Metadata key
            value: Metadata value

        Returns:
            True if operation was successful
        """
        # Add the key to the value dictionary
        value["key"] = key

        # Upsert the document (insert if not exists, update if exists)
        result = await self.findings_db[self.metadata_collection].update_one(
            {"key": key}, {"$set": value}, upsert=True
        )

        return result.acknowledged

    async def get_findings(
        self,
        task_id: str,
        agent_id: str | None = None,
        status: Status | None = None,
        since_timestamp: datetime | None = None,
    ) -> list[FindingDB]:
        """
        Get all findings for a task with optional agent, status, and since_timestamp filters.

        Args:
            task_id: Task identifier
            agent_id: Agent identifier (optional)
            status: Status of the findings (optional)
            since_timestamp: Only include findings created after this timestamp (optional)
        Returns:
            List of all findings matching the filters
        """
        collection_name = self.get_findings_collection_name(task_id)
        collection = self.findings_db[collection_name]

        # Query database for task findings
        query = {}
        if agent_id:
            query["agent_id"] = agent_id
        if status:
            query["status"] = status
        if since_timestamp:
            query["created_at"] = {"$gt": since_timestamp}

        cursor = collection.find(query)
        findings = []

        async for doc in cursor:
            findings.append(FindingDB(**doc))

        return findings


# Global MongoDB handler instance
mongodb = MongoDBHandler()
