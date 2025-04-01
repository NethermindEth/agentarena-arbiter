from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, List
import os
from dotenv import load_dotenv
from app.models.finding_input import FindingInput
from app.models.finding_db import FindingDB, Status, EvaluatedSeverity
from datetime import datetime

load_dotenv()

class MongoDBHandler:
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
    
    def get_collection_name(self, project_id: str) -> str:
        """Get collection name for a project"""
        return f"findings_{project_id}"
    
    async def connect(self):
        """Connect to MongoDB"""
        mongodb_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
        self.client = AsyncIOMotorClient(mongodb_url)
        self.db = self.client.arbiter_demo
        
    async def close(self):
        """Close MongoDB connection"""
        if self.client is not None:
            self.client.close()
    
    async def get_next_submission_id(self, project_id: str, agent_id: str) -> int:
        """
        Get the next available submission ID for a specific agent within a project.
        This is a sequential counter starting from 0, incremented per agent.
        
        Args:
            project_id: The project identifier
            agent_id: The agent identifier
        
        Returns:
            Next available submission ID as integer
        """
        if self.db is None:
            await self.connect()
        
        collection_name = self.get_collection_name(project_id)
        
        # Find the document with the highest submission_id for this agent
        pipeline = [
            {"$match": {"reported_by_agent": agent_id}},
            {"$sort": {"submission_id": -1}},
            {"$limit": 1},
            {"$project": {"submission_id": 1}}
        ]
        
        cursor = self.db[collection_name].aggregate(pipeline)
        results = await cursor.to_list(length=1)
        
        if results and "submission_id" in results[0]:
            return results[0]["submission_id"] + 1
        else:
            return 0  # Start with 0 if no existing documents
    
    # Finding specific methods
    async def create_finding(self, finding_input: FindingInput, submission_id: Optional[int] = None) -> str:
        """
        Create a new finding from input data
        Automatically adds system-managed fields (status, timestamps)
        
        Args:
            finding_input: The finding input data
            submission_id: Optional specific submission ID (otherwise auto-assigned)
        
        Returns:
            Inserted document ID
        """
        if self.db is None:
            await self.connect()
            
        # Check if this is already a FindingDB
        if isinstance(finding_input, FindingDB):
            # Use existing FindingDB but get a new submission_id if not already set
            finding_dict = finding_input.model_dump()
            if submission_id is not None:
                finding_dict["submission_id"] = submission_id
            elif "submission_id" not in finding_dict or finding_dict["submission_id"] == 0:
                # Only get next submission_id if not already set or is default (0)
                new_submission_id = await self.get_next_submission_id(
                    finding_input.project_id, 
                    finding_input.reported_by_agent
                )
                finding_dict["submission_id"] = new_submission_id
                
            finding_db = FindingDB(**finding_dict)
        else:
            # Get next submission ID if not provided
            if submission_id is None:
                submission_id = await self.get_next_submission_id(
                    finding_input.project_id, 
                    finding_input.reported_by_agent
                )
            
            # Convert input to DB model with submission ID
            finding_db = FindingDB(**finding_input.model_dump(), submission_id=submission_id)
        
        collection_name = self.get_collection_name(finding_db.project_id)
        result = await self.db[collection_name].insert_one(finding_db.model_dump())
        return str(result.inserted_id)
    
    async def create_finding_batch(self, findings: List[FindingInput]) -> List[str]:
        """
        Create multiple findings as a batch, with agent-specific submission IDs.
        All findings from the same agent get the same submission_id.
        
        Args:
            findings: List of finding inputs to create (can be FindingInput or FindingDB)
            
        Returns:
            List of inserted document IDs
        """
        if not findings:
            return []
        
        if self.db is None:
            await self.connect()
        
        # Group findings by agent
        findings_by_agent = {}
        for finding in findings:
            agent_id = finding.reported_by_agent
            if agent_id not in findings_by_agent:
                findings_by_agent[agent_id] = []
            findings_by_agent[agent_id].append(finding)
        
        all_ids = []
        project_id = findings[0].project_id  # Assuming all findings are for the same project
        
        # Process each agent's findings with a unique submission_id
        for agent_id, agent_findings in findings_by_agent.items():
            # Get next submission ID for this agent
            submission_id = await self.get_next_submission_id(project_id, agent_id)
            
            # Convert findings to DB models with the same submission_id
            db_findings = []
            for finding in agent_findings:
                # Check if this is already a FindingDB
                if isinstance(finding, FindingDB):
                    # Preserve existing fields but update submission_id
                    finding_dict = finding.model_dump()
                    finding_dict["submission_id"] = submission_id
                    finding_db = FindingDB(**finding_dict)
                else:
                    # Convert FindingInput to FindingDB
                    finding_db = FindingDB(**finding.model_dump(), submission_id=submission_id)
                
                db_findings.append(finding_db.model_dump())
            
            # Insert all findings for this agent
            collection_name = self.get_collection_name(project_id)
            result = await self.db[collection_name].insert_many(db_findings)
            all_ids.extend([str(id) for id in result.inserted_ids])
        
        return all_ids
        
    async def get_finding(self, project_id: str, finding_id: str) -> Optional[FindingDB]:
        """Get a finding by its ID"""
        if self.db is None:
            await self.connect()
        collection_name = self.get_collection_name(project_id)
        doc = await self.db[collection_name].find_one({"finding_id": finding_id})
        return FindingDB(**doc) if doc else None
    
    async def get_project_findings(self, project_id: str) -> List[FindingDB]:
        """Get all findings for a project"""
        if self.db is None:
            await self.connect()
        collection_name = self.get_collection_name(project_id)
        cursor = self.db[collection_name].find({})
        findings = await cursor.to_list(length=None)
        return [FindingDB(**doc) for doc in findings]
    
    async def update_finding(self, project_id: str, finding_id: str, finding: FindingDB) -> bool:
        """Update a finding"""
        if self.db is None:
            await self.connect()
        collection_name = self.get_collection_name(project_id)
        finding.updated_at = datetime.utcnow()
        result = await self.db[collection_name].update_one(
            {"finding_id": finding_id},
            {"$set": finding.model_dump()}
        )
        return result.modified_count > 0
    
    async def update_finding_status(self, project_id: str, finding_id: str, 
                                     status: Status, 
                                     category: Optional[str] = None,
                                     evaluated_severity: Optional[EvaluatedSeverity] = None,
                                     evaluation_comment: Optional[str] = None) -> bool:
        """
        Update a finding's evaluation data (status, category, severity, comment)
        """
        if self.db is None:
            await self.connect()
        collection_name = self.get_collection_name(project_id)
        
        update_data = {
            "status": status.value,
            "updated_at": datetime.utcnow()
        }
        
        if category is not None:
            update_data["category"] = category
            
        if evaluated_severity is not None:
            update_data["evaluated_severity"] = evaluated_severity.value
            
        if evaluation_comment is not None:
            update_data["evaluation_comment"] = evaluation_comment
            
        result = await self.db[collection_name].update_one(
            {"finding_id": finding_id},
            {"$set": update_data}
        )
        return result.modified_count > 0
    
    async def delete_finding(self, project_id: str, finding_id: str) -> bool:
        """Delete a finding"""
        if self.db is None:
            await self.connect()
        collection_name = self.get_collection_name(project_id)
        result = await self.db[collection_name].delete_one({"finding_id": finding_id})
        return result.deleted_count > 0
            
    # Generic methods
    async def insert_one(self, collection: str, document: dict):
        """Insert a single document"""
        if self.db is None:
            await self.connect()
        return await self.db[collection].insert_one(document)
    
    async def find_one(self, collection: str, query: dict):
        """Find a single document"""
        if self.db is None:
            await self.connect()
        return await self.db[collection].find_one(query)
    
    async def find(self, collection: str, query: dict = None):
        """Find multiple documents"""
        if self.db is None:
            await self.connect()
        cursor = self.db[collection].find(query or {})
        return await cursor.to_list(length=None)
    
    async def update_one(self, collection: str, query: dict, update: dict):
        """Update a single document"""
        if self.db is None:
            await self.connect()
        return await self.db[collection].update_one(query, {"$set": update})
    
    async def delete_one(self, collection: str, query: dict):
        """Delete a single document"""
        if self.db is None:
            await self.connect()
        return await self.db[collection].delete_one(query)

# Create a global MongoDB handler instance
mongodb = MongoDBHandler() 