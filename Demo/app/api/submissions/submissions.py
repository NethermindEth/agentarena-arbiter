from fastapi import FastAPI, HTTPException, Body
from typing import Dict, Any, List
from pydantic import BaseModel
from enum import Enum
import os
from dotenv import load_dotenv
import re
from pymongo import MongoClient

from app.core.cross_agent_comparison import CrossAgentComparison

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
    """
    Model representing a security finding submission with all required fields
    for vulnerability assessment and processing.
    """
    project_id: str          # Unique identifier for the audited project
    reported_by_agent: str   # Identifier of the agent reporting the vulnerability
    finding_id: str          # Unique identifier for this finding
    title: str               # Short descriptive title of the vulnerability
    description: str         # Detailed explanation of the vulnerability
    severity: Severity       # Severity rating (HIGH or MEDIUM)
    recommendation: str      # Suggested fix or mitigation
    code_references: List[str]  # List of affected code locations

# API endpoints


@app.post("/api/vulnerabilities", response_model=Dict[str, Any], tags=["Vulnerabilities"])
async def submit_vulnerability(
    submission: FindingInput = Body(...),
):
    """
    Submit a vulnerability finding for processing and storage.

    This endpoint:
    1. Validates that the referenced project exists
    2. Processes the finding through the cross-agent comparison system
    3. Returns detailed results of deduplication and validation

    The finding is checked against previously reported issues to identify
    duplicates and similar vulnerabilities from other agents.
    """
    # Verify the referenced project exists before accepting the submission
    contract = contracts_collection.find_one(
        {"task_id": {"$regex": f"^{re.escape(submission.project_id)}$", "$options": "i"}})
    if not contract:
        raise HTTPException(
            status_code=404, detail=f"Contract with project ID {submission.project_id} not found")

    try:
        # Initialize the cross-agent comparison module
        # This handles deduplication, similarity checks, and status determination
        comparison = CrossAgentComparison()

        # Process the new finding
        # This will:
        # - Check for duplicates within this agent's submissions
        # - Compare with findings from other agents
        # - Assign appropriate status (unique, duplicate, similar)
        # - Store the finding with proper metadata
        result = await comparison.process_new_findings(
            project_id=submission.project_id,
            agent_id=submission.reported_by_agent,
            # Pass as a list since the method expects multiple findings
            new_findings=[submission]
        )

        return {
            "message": "Vulnerability submitted and processed successfully",
            "processing_results": result
        }

    except Exception as e:
        # Handle unexpected errors during processing
        raise HTTPException(
            status_code=500,
            detail=f"Processing error: {str(e)}"
        )

# Health check endpoint


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Health check endpoint to verify the API is running.

    Returns a simple status indicator that can be used by monitoring tools
    to confirm the service is operational.
    """
    return {"status": "healthy"}

# Run the application with uvicorn when executed directly
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("vulnerability_submissions:app",
                host="0.0.0.0", port=8001, reload=True)
