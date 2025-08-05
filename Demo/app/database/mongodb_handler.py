"""
MongoDB database handler for security findings.
Handles storage and retrieval of findings in MongoDB using Motor with Beanie models.
"""
from typing import List, Dict, Any, Optional
from bson import ObjectId
import motor.motor_asyncio
from datetime import datetime, timezone
import os
from pydantic import BaseModel
from beanie import init_beanie, PydanticObjectId

from app.models.finding_input import FindingInput, Finding
from app.models.finding_db import FindingDB, Status
from app.config import config

class MongoDBHandler:
    """
    MongoDB database handler using Motor for thread-safe operations.
    Uses Beanie models for data validation and serialization.
    """
    
    def __init__(self, connection_string: str = None):
        """
        Initialize MongoDB handler with a connection string.
        
        Args:
            connection_string: MongoDB connection string
        """
        self.client = None
        self.db = None
        
        # Automatically select MongoDB URL based on environment
        is_docker = os.path.exists("/.dockerenv")  # Docker environment detection
        default_mongo_url = "mongodb://mongodb:27017" if is_docker else "mongodb://localhost:27017"
        
        self.connection_string = connection_string or config.mongodb_url or default_mongo_url
        self.database_name = "security_findings"
    
    async def connect(self):
        """Connect to MongoDB database and initialize Beanie."""
        self.client = motor.motor_asyncio.AsyncIOMotorClient(self.connection_string)
        self.db = self.client[self.database_name]
        
        # Initialize Beanie with the database and document models
        await init_beanie(database=self.db, document_models=[FindingDB])
    
    async def close(self):
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
    
    def get_collection_name(self, task_id: str) -> str:
        """
        Get collection name for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Collection name for the task
        """
        return f"findings_{task_id}"
    
    async def create_finding(self, task_id: str, agent_id: str, finding: Finding, status: Status = Status.PENDING) -> str:
        """
        Create a new finding in the database.
        
        Args:
            task_id: Task identifier
            agent_id: Agent identifier
            finding: The finding to create
            status: Status of the finding (defaults to PENDING)
            
        Returns:
            Title of the created finding
        """
        # Create FindingDB from Finding
        finding_db = FindingDB(
            **finding.model_dump(exclude_unset=True),
            agent_id=agent_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        collection_name = self.get_collection_name(task_id)
        collection = self.db[collection_name]
        
        # Convert to dict and insert
        doc_dict = finding_db.model_dump(by_alias=True, exclude_unset=True)
        if doc_dict.get('_id') is None:
            doc_dict.pop('_id', None)
        
        await collection.insert_one(doc_dict)
        
        # Return the finding title
        return finding.title
    
    async def create_findings_batch(self, agent_id: str, input_data: FindingInput) -> List[str]:
        """
        Create multiple findings in batch from a FindingInput.
        
        Args:
            agent_id: Agent identifier
            input_data: FindingInput containing task_id and a list of findings
            
        Returns:
            List of created finding titles
        """
        if not input_data.findings:
            return []
            
        # Extract task_id
        task_id = input_data.task_id
        current_time = datetime.now(timezone.utc)
        
        # Create FindingDB objects
        finding_dbs = []
        for finding in input_data.findings:
            finding_db = FindingDB(
                **finding.model_dump(exclude_unset=True),
                agent_id=agent_id,
                created_at=current_time,
                updated_at=current_time
            )
            finding_dbs.append(finding_db)
        
        collection_name = self.get_collection_name(task_id)
        collection = self.db[collection_name]
        
        # Convert to dicts and insert
        docs = []
        for finding_db in finding_dbs:
            doc_dict = finding_db.model_dump(by_alias=True, exclude_unset=True)
            if doc_dict.get('_id') is None:
                doc_dict.pop('_id', None)
            docs.append(doc_dict)
        
        if docs:
            await collection.insert_many(docs)
            
        # Return the titles
        return [finding.title for finding in input_data.findings]
    
    async def update_finding(self, task_id: str, id: str, update_fields: Dict[str, Any]) -> bool:
        """
        Update specific fields of an existing finding.
        
        Args:
            task_id: Task identifier
            id: Finding ID as string
            update_fields: Dictionary of fields to update
            
        Returns:
            True if update was successful, False otherwise
        """
        collection_name = self.get_collection_name(task_id)
        collection = self.db[collection_name]
        
        if isinstance(update_fields, FindingDB):
            update_fields = update_fields.model_dump(by_alias=True, exclude_unset=True)

        # Ensure updated_at is set
        if "updated_at" not in update_fields:
            update_fields["updated_at"] = datetime.now(timezone.utc)
        
        # Convert string id to ObjectId using PydanticObjectId
        try:
            object_id = PydanticObjectId(id)
        except Exception:
            return False
        
        # Update in database
        result = await collection.update_one(
            {"_id": object_id},
            {"$set": update_fields}
        )
        
        return result.modified_count > 0
    
    async def get_task_findings(self, task_id: str) -> List[FindingDB]:
        """
        Get all findings for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            List of findings for the task
        """
        collection_name = self.get_collection_name(task_id)
        collection = self.db[collection_name]
        
        # Query database
        cursor = collection.find({})
        findings = []
        
        async for doc in cursor:
            findings.append(FindingDB(**doc))
        
        return findings
    
    async def get_agent_findings(self, task_id: str, agent_id: str) -> List[FindingDB]:
        """
        Get all findings for an agent in a task.
        
        Args:
            task_id: Task identifier
            agent_id: Agent identifier
            
        Returns:
            List of findings for the agent in the task
        """
        collection_name = self.get_collection_name(task_id)
        collection = self.db[collection_name]
        
        # Query database
        cursor = collection.find({"agent_id": agent_id})
        findings = []
        
        async for doc in cursor:
            findings.append(FindingDB(**doc))
        
        return findings
        
    async def get_agent_findings_since(self, task_id: str, agent_id: str, since_timestamp: datetime) -> List[FindingDB]:
        """
        Get all findings for an agent in a task created after a specific timestamp.
        
        Args:
            task_id: Task identifier
            agent_id: Agent identifier
            since_timestamp: Only include findings created after this timestamp
            
        Returns:
            List of findings for the agent in the task since the specified timestamp
        """
        collection_name = self.get_collection_name(task_id)
        collection = self.db[collection_name]
        
        # Query database for findings created after the specified timestamp
        cursor = collection.find({
            "agent_id": agent_id,
            "created_at": {"$gt": since_timestamp}
        })
        findings = []
        
        async for doc in cursor:
            findings.append(FindingDB(**doc))
        
        return findings
        
    async def get_metadata(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata from the metadata collection.
        
        Args:
            key: Metadata key
            
        Returns:
            Metadata value if found, None otherwise
        """
        # Use a separate collection for metadata
        metadata_collection = "metadata"
        
        # Query database
        doc = await self.db[metadata_collection].find_one({"key": key})
        
        return doc
        
    async def set_metadata(self, key: str, value: Dict[str, Any]) -> bool:
        """
        Set metadata in the metadata collection.
        
        Args:
            key: Metadata key
            value: Metadata value
            
        Returns:
            True if operation was successful
        """
        # Use a separate collection for metadata
        metadata_collection = "metadata"
        
        # Add the key to the value dictionary
        value["key"] = key
        value["updated_at"] = datetime.utcnow()
        
        # Upsert the document (insert if not exists, update if exists)
        result = await self.db[metadata_collection].update_one(
            {"key": key},
            {"$set": value},
            upsert=True
        )
        
        return result.acknowledged

    async def get_pending_task_findings(self, task_id: str) -> List[FindingDB]:
        """
        Get all pending task findings for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            List of all pending task findings for the task
        """
        collection_name = self.get_collection_name(task_id)
        collection = self.db[collection_name]
        
        # Query database for all pending task findings
        cursor = collection.find({"status": Status.PENDING})
        findings = []
        
        async for doc in cursor:
            findings.append(FindingDB(**doc))
        
        return findings

# Global MongoDB handler instance
mongodb = MongoDBHandler()
