# This is a test submissions API without integration with the cross-agent comparison system.
# It is designed to accept vulnerability findings and validate the existence of the referenced project.
# The API is structured to allow for easy integration with a cross-agent comparison system in the future.
# The API uses MongoDB for data storage and retrieval, and Pydantic for data validation.


from fastapi import FastAPI, HTTPException, Body
from typing import Dict, Any, List
from pydantic import BaseModel
from enum import Enum
import os
from dotenv import load_dotenv
import re
from datetime import datetime
from pymongo import MongoClient

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(
    title="Smart Contract Vulnerability Submissions API",
    description="API for submitting vulnerability findings for security assessment",
    version="1.0.0"
)

# MongoDB connection for validating task existence
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = MongoClient(MONGO_URI)
db = client.get_database("smart_contract_db")
contracts_collection = db.get_collection("contracts")

# Define severity levels according to security standards
class Severity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"

# Pydantic model for vulnerability submission validation
class FindingInput(BaseModel):
    """Model representing a security finding submission"""
    project_id: str
    reported_by_agent: str
    finding_id: str
    title: str
    description: str
    severity: Severity
    recommendation: str
    code_references: List[str]

# API endpoints
@app.post("/api/vulnerabilities", response_model=Dict[str, Any], tags=["Vulnerabilities"])
async def submit_vulnerability(
    submission: FindingInput = Body(...),
):
    """
    Submit a vulnerability finding for processing.
    """
    # Verify the referenced project exists
    contract = contracts_collection.find_one(
        {"task_id": {"$regex": f"^{re.escape(submission.project_id)}$", "$options": "i"}})
    if not contract:
        raise HTTPException(
            status_code=404, detail=f"Contract with project ID {submission.project_id} not found")

    try:
        # In the actual implementation, this would call the arbiter agent's processing
        # For testing, we'll just return a mock response
        return {
            "message": "Vulnerability submitted and processed successfully",
            "processing_results": {
                "deduplication": {
                    "new_findings": 1,
                    "duplicates": 0,
                    "new_ids": [submission.finding_id]
                },
                "cross_comparison": {
                    "total": 1,
                    "similar_valid": 0,
                    "pending_evaluation": 1,
                    "already_reported": 0,
                    "similar_ids": [],
                    "pending_ids": [submission.finding_id]
                }
            }
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Processing error: {str(e)}"
        )

# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint to verify the API is running.
    """
    return {"status": "healthy"}

# Run the application with uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("test_api:app", host="0.0.0.0", port=8001, reload=True)