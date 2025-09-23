"""
MongoDB database handler for security findings.
Handles storage and retrieval of findings using native MongoDB Motor operations.
"""
from typing import List, Dict, Any, Optional
import motor.motor_asyncio
from datetime import datetime, timezone
import os
from bson import ObjectId

from app.models.finding_input import FindingInput, Finding
from app.models.finding_db import FindingDB, Status
from app.config import config
from app.types import Task

class MongoDBHandler:
    """
    MongoDB database handler using Motor for async operations.
    Uses Pydantic models for data validation and serialization.
    """
    
    def __init__(self, connection_string: str = None):
        """
        Initialize MongoDB handler with a connection string.
        
        Args:
            connection_string: MongoDB connection string
        """
        self.client = None
        self.findings_db = None
        self.agent_arena_db = None
        
        # Automatically select MongoDB URL based on environment
        is_docker = os.path.exists("/.dockerenv")  # Docker environment detection
        default_mongo_url = "mongodb://mongodb:27017" if is_docker else "mongodb://localhost:27017"
        
        self.connection_string = connection_string or config.mongodb_url or default_mongo_url
        self.findings_db_name = "security_findings"
        self.agent_arena_db_name = "agent_arena"
        self.metadata_collection = "metadata"
    
    async def connect(self):
        """Connect to MongoDB databases."""
        self.client = motor.motor_asyncio.AsyncIOMotorClient(self.connection_string)
        self.findings_db = self.client[self.findings_db_name]
        self.agent_arena_db = self.client[self.agent_arena_db_name]
    
    async def close(self):
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
    
    def get_findings_collection_name(self, task_id: str) -> str:
        """
        Get findings collection name for a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Findings collection name for the task
        """
        return f"findings_{task_id}"
    
    async def create_finding(self, task_id: str, agent_id: str, finding: Finding, status: Status = Status.PENDING) -> FindingDB:
        """
        Create a new finding in the database.
        
        Args:
            task_id: Task identifier
            agent_id: Agent identifier
            finding: The finding to create
            status: Status of the finding (defaults to PENDING)
            
        Returns:
            FindingDB object of the created finding
        """
        # Create FindingDB from Finding
        finding_db = FindingDB(
            **finding.model_dump(exclude_unset=True),
            agent_id=agent_id,
            status=status,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        
        collection_name = self.get_findings_collection_name(task_id)
        collection = self.findings_db[collection_name]
        
        # Convert to dict and insert
        doc_dict = finding_db.model_dump(by_alias=True, exclude_unset=True)
        if doc_dict.get('_id') is None:
            doc_dict.pop('_id', None)
        
        result = await collection.insert_one(doc_dict)
        
        # Set the ID from the insert result
        finding_db.id = result.inserted_id
        
        # Return the finding with proper ID set
        return finding_db
    
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
        
        collection_name = self.get_findings_collection_name(task_id)
        collection = self.findings_db[collection_name]
        
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
        collection_name = self.get_findings_collection_name(task_id)
        collection = self.findings_db[collection_name]
        
        if isinstance(update_fields, FindingDB):
            update_fields = update_fields.model_dump(by_alias=True, exclude_unset=True)

        # Ensure updated_at is set
        if "updated_at" not in update_fields:
            update_fields["updated_at"] = datetime.now(timezone.utc)
        
        # Convert string id to ObjectId
        try:
            object_id = ObjectId(id)
        except Exception:
            return False
        
        # Update in database
        result = await collection.update_one(
            {"_id": object_id},
            {"$set": update_fields}
        )
        
        return result.modified_count > 0
        
    async def delete_agent_findings(self, task_id: str, agent_id: str) -> int:
        """
        Delete all findings for a specific agent and task.
        Used when an agent makes a new submission to override the previous one.
        
        Args:
            task_id: Task identifier
            agent_id: Agent identifier
            
        Returns:
            Number of findings deleted
        """
        collection_name = self.get_findings_collection_name(task_id)
        collection = self.findings_db[collection_name]
        
        # Delete all findings for this agent and task
        result = await collection.delete_many({"agent_id": agent_id})
        
        return result.deleted_count

    async def get_metadata(self, key: str) -> Optional[Dict[str, Any]]:
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
        
    async def set_metadata(self, key: str, value: Dict[str, Any]) -> bool:
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
            {"key": key},
            {"$set": value},
            upsert=True
        )
        
        return result.acknowledged

    async def get_findings(self, task_id: str,
                           agent_id: Optional[str] = None,
                           status: Optional[Status] = None,
                           since_timestamp: Optional[datetime] = None) -> List[FindingDB]:
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

    async def get_agent_id(self, api_key: str) -> str:
        """
        Get agent ID from the agent_arena database.
        
        Args:
            api_key: Agent API key
            
        Returns:
            Agent ID if agent is found and valid
        """
        users_collection = self.agent_arena_db["users"]
        user = await users_collection.find_one({"api_key": api_key})
        
        if not user:
            raise ValueError(f"User with API key {api_key} not found")
        
        if user.get("role") not in ["AgentBuilder", "Admin"] or user.get("status") != "active":
            raise ValueError(f"Invalid role or status for user with API key {api_key}")
        
        if user.get("agent_name") is None:
            raise ValueError(f"Agent name not found for user with API key {api_key}")
        
        return user.get("agent_name")
        
        # TODO: The agent ID is the user ID for now
        return str(user.get("_id"))

    async def get_submitted_tasks(self) -> List[Task]:
        """
        Get all active tasks with status "submitted" from agent_arena database.
        
        Returns:
            List of submitted tasks
        """
        tasks_collection = self.agent_arena_db["tasks"]
        cursor = tasks_collection.find({"status": "submitted"})
        tasks = []
        
        async for doc in cursor:
            task = Task.model_validate(doc)
            tasks.append(task)
            
        return tasks

    async def get_task(self, task_id: str) -> Task:
        """
        Get a specific task by taskId from agent_arena database.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Task object
        """
        tasks_collection = self.agent_arena_db["tasks"]
        doc = await tasks_collection.find_one({"taskId": task_id})
        
        if not doc:
            raise ValueError(f"Task {task_id} not found")
        
        return Task.model_validate(doc)

# Global MongoDB handler instance
mongodb = MongoDBHandler()
