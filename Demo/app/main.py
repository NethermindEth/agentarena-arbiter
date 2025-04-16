"""
Main FastAPI application for security findings management.
Provides API endpoints for submitting and managing security findings.
"""
from fastapi import FastAPI, HTTPException
from typing import Dict, Any, List
import os
from dotenv import load_dotenv
import httpx

from app.models.finding_input import FindingInput
from app.database.mongodb_handler import mongodb
from app.core.finding_deduplication import FindingDeduplication
from app.core.final_evaluation import FindingEvaluator

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI(
    title="Security Findings API",
    description="API for managing security findings and deduplication",
    version="1.0.0"
)

# Initialize handlers
deduplicator = FindingDeduplication()
evaluator = FindingEvaluator()

@app.on_event("startup")
async def startup():
    """Connect to MongoDB on startup."""
    await mongodb.connect()
    print("✅ Connected to MongoDB")

@app.on_event("shutdown")
async def shutdown():
    """Disconnect from MongoDB on shutdown."""
    await mongodb.close()
    print("✅ Disconnected from MongoDB")

@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": "Welcome to the Security Findings API"}

@app.post("/process_findings", response_model=Dict[str, Any])
async def process_findings(input_data: FindingInput):
    """
    Submit security findings for processing.
    Performs deduplication, stores the findings, and automatically evaluates new findings.
    
    Args:
        input_data: Batch of findings with task_id, agent_id and findings list
        
    Returns:
        Processing results with statistics and evaluation results
    """
    try:
        print(f"Processing findings for task_id: {input_data.task_id}")

        # 1. Process findings with deduplication
        dedup_results = await deduplicator.process_findings(input_data)
        
        # 1.5. Perform cross-agent comparison for newly added findings
        cross_comparison_results = {}
        if dedup_results['new'] > 0:
            # Get newly added findings
            new_findings = []
            for title in dedup_results.get('new_titles', []):
                finding = await mongodb.get_finding(input_data.task_id, title)
                if finding:
                    new_findings.append(finding)
            
            # Compare with findings from other agents
            if new_findings:
                cross_comparison_results = await evaluator.cross_comparison.compare_with_other_agents(
                    input_data.task_id,
                    input_data.agent_id,
                    new_findings
                )
        
        print(f"Cross comparison results: {cross_comparison_results}")

        # 2. Only perform evaluation if new findings were added and not marked as similar_valid
        evaluation_results = {}
        if dedup_results['new'] > 0:
            # Get pending findings (newly added ones that weren't marked as similar_valid)
            pending_findings = await evaluator.get_pending_findings(input_data.task_id)
            
            if pending_findings:
                # Evaluate all pending findings
                evaluation_results = await evaluator.evaluate_all_pending(input_data.task_id)
                
                # Generate summary
                summary_report = await evaluator.generate_summary_report(input_data.task_id)
                evaluation_results["summary"] = summary_report
        
        print(f"Evaluation results: {evaluation_results}")

        # 3. Combine results
        combined_results = {
            "deduplication": dedup_results,
            "cross_comparison": cross_comparison_results,
            "auto_evaluation": evaluation_results
        }

        # 4. Post results to another endpoint
        try:
            # Fetch all findings for this task to include in the payload
            findings = await mongodb.get_agent_findings(input_data.task_id, input_data.agent_id)
            
            # Format findings data for the external endpoint
            formatted_findings = []
            for finding in findings:
                formatted_findings.append({
                    "title": finding.title,
                    "description": finding.description,
                    "severity": finding.severity,
                    "status": finding.status,
                    "file_path": finding.file_path
                })
            
            # Prepare payload for external endpoint
            payload = {
                "task_id": input_data.task_id,
                "agent_id": input_data.agent_id,
                "findings": formatted_findings
            }
            
            # Post to external endpoint
            external_endpoint = os.getenv("BACKEND_FINDINGS_ENDPOINT")
            if external_endpoint:
                async with httpx.AsyncClient() as client:
                    response = await client.post(external_endpoint, json=payload)
                    print(f"Posted results to external endpoint: {response.status_code}")
            else:
                print("EXTERNAL_RESULTS_ENDPOINT not configured, skipping external post")
                
        except Exception as post_error:
            print(f"Error posting results to external endpoint: {str(post_error)}")
            # Continue execution even if posting fails
        
        print(f"Combined results: {combined_results}")
        
        return combined_results
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error processing findings: {str(e)}")
        print(f"Traceback: {error_trace}")
        raise HTTPException(status_code=500, detail=f"Error processing findings: {str(e)}")

@app.get("/tasks/{task_id}/findings", response_model=List[Dict[str, Any]])
async def get_task_findings(task_id: str):
    """
    Get all findings for a task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        List of findings for the task
    """
    try:
        findings = await mongodb.get_task_findings(task_id)
        return [finding.model_dump() for finding in findings]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving findings: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True) 