"""
MongoDB database handler for the arbiter's metadata (the processed-task markers).
Findings are read and written through the backend (app/backend_findings.py).
"""

import os
from typing import Any

import motor.motor_asyncio

from app.config import config


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


# Global MongoDB handler instance
mongodb = MongoDBHandler()
