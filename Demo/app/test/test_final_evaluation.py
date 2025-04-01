"""
Test script for final evaluation functionality with real Claude API.
Tests the evaluation of findings and handling of similar findings.
"""
import os
import asyncio
import traceback
from datetime import datetime
from dotenv import load_dotenv

from app.database.mongodb_handler import mongodb
from app.models.finding_input import FindingInput, Severity
from app.models.finding_db import FindingDB, Status, EvaluatedSeverity
from app.core.final_evaluation import FindingEvaluator
from app.core.cross_agent_comparison import CrossAgentComparison

# Load environment variables
load_dotenv()

async def setup_test_data(project_id: str):
    """
    Setup test data for evaluation testing.
    Creates findings for smart contract vulnerabilities with pending status.
    
    Args:
        project_id: Project identifier for the test
    """
    # Clean existing data
    collection = mongodb.get_collection_name(project_id)
    await mongodb.db[collection].delete_many({})
    print(f"🧹 Cleaned collection {collection}")
    
    # First finding: Should be valid but severity downgraded from medium to low
    finding1 = FindingInput(
        project_id=project_id,
        reported_by_agent="agent1",
        finding_id="SC-A1-1",
        title="Unused Variables in Smart Contract",
        description="The smart contract contains several unused state variables that are initialized but never used in any function. This increases gas costs for deployment and potentially confuses readers of the code about the contract's functionality. While not directly exploitable, it represents poor code quality that could mask other issues.",
        severity=Severity.MEDIUM, # Reported as medium
        recommendation="Remove all unused variables from the contract or document why they are needed for future functionality. Consider implementing a more comprehensive test suite and static analysis tools in your development workflow.",
        code_references=["contracts/Token.sol:25-30", "contracts/Vault.sol:45-48"]
    )
    
    # Convert to FindingDB with pending status
    finding1_db = FindingDB(
        **finding1.model_dump(),
        submission_id=0,
        status="pending",  # Initial status is pending
    )
    
    # Insert into database
    await mongodb.create_finding(finding1_db)
    print(f"✅ Created first finding (should be downgraded): {finding1_db.finding_id}")
    
    # Second finding: Should be marked as disputed (invalid)
    finding2 = FindingInput(
        project_id=project_id,
        reported_by_agent="agent2",
        finding_id="SC-A2-1",
        title="Potential Reentrancy in Transfer Function",
        description="The transfer function in the contract might be vulnerable to reentrancy as it calls an external contract. However, after closer inspection, the function follows the checks-effects-interactions pattern correctly by updating balances before making external calls.",
        severity=Severity.HIGH,
        recommendation="No action needed as this is a false positive. The code already follows secure development patterns by updating state before making external calls. However, consider adding explicit nonReentrant modifiers for better readability.",
        code_references=["contracts/Exchange.sol:120-135"]
    )
    
    # Convert to FindingDB with pending status
    finding2_db = FindingDB(
        **finding2.model_dump(),
        submission_id=0,
        status="pending",  # Initial status is pending
    )
    
    # Insert into database
    await mongodb.create_finding(finding2_db)
    print(f"✅ Created second finding (should be disputed): {finding2_db.finding_id}")

async def test_final_evaluation():
    """
    Test the final evaluation functionality using actual Claude API.
    """
    try:
        # Connect to MongoDB
        await mongodb.connect()
        print("✅ Connected to MongoDB")
        
        # Check for Claude API key
        if not os.getenv("CLAUDE_API_KEY"):
            print("❌ CLAUDE_API_KEY environment variable not set. Cannot perform real evaluation.")
            return
            
        print("✅ CLAUDE_API_KEY found in environment variables")
        
        # Project ID for test
        project_id = "test-smart-contract-evaluation"
        
        # Setup test data
        await setup_test_data(project_id)
        
        # Create evaluator
        evaluator = FindingEvaluator()
        print("✅ Created FindingEvaluator with live Claude API")
        
        # Get all pending findings
        pending_findings = await evaluator.get_pending_findings(project_id)
        print(f"\nFound {len(pending_findings)} pending findings")
        
        # Process findings one by one to see individual results
        print("\n📋 Processing findings individually")
        
        # Process first finding (should be valid but downgraded)
        finding1 = pending_findings[0]
        print(f"\nEvaluating first finding: {finding1.finding_id} - {finding1.title}")
        print(f"Initial severity: {finding1.severity}")
        print("Calling Claude API for evaluation...")
        
        evaluation1 = await evaluator.evaluate_finding(finding1)
        print(f"\nAPI Evaluation result for first finding:")
        print(f"  Is Valid: {evaluation1['is_valid']}")
        print(f"  Category: {evaluation1['category']}")
        print(f"  Severity: {evaluation1['evaluated_severity']} (should be LOW)")
        print(f"  Comment: {evaluation1['evaluation_comment']}")
        
        # Apply the evaluation
        updated1 = await evaluator.apply_evaluation(
            project_id, 
            finding1.finding_id, 
            evaluation1
        )
        print(f"\nUpdated first finding:")
        print(f"  Status: {updated1['status']}")
        print(f"  Category: {updated1['category']}")
        print(f"  Category ID: {updated1['category_id']}")
        print(f"  Severity: {updated1['evaluated_severity']}")
        
        # Process second finding (should be disputed)
        finding2 = pending_findings[1]
        print(f"\nEvaluating second finding: {finding2.finding_id} - {finding2.title}")
        print(f"Initial severity: {finding2.severity}")
        print("Calling Claude API for evaluation...")
        
        evaluation2 = await evaluator.evaluate_finding(finding2)
        print(f"\nAPI Evaluation result for second finding:")
        print(f"  Is Valid: {evaluation2['is_valid']} (should be False)")
        print(f"  Category: {evaluation2['category']}")
        print(f"  Severity: {evaluation2['evaluated_severity']}")
        print(f"  Comment: {evaluation2['evaluation_comment']}")
        
        # Apply the evaluation
        updated2 = await evaluator.apply_evaluation(
            project_id, 
            finding2.finding_id, 
            evaluation2
        )
        print(f"\nUpdated second finding:")
        print(f"  Status: {updated2['status']} (should be DISPUTED)")
        print(f"  Category: {updated2['category']} (should be None)")
        print(f"  Category ID: {updated2['category_id']} (should be None)")
        print(f"  Severity: {updated2['evaluated_severity']} (should be None)")
        
        print("\n✅ Test completed. Data has been kept in the database for review.")
        
    except Exception as e:
        print(f"❌ Error occurred: {str(e)}")
        traceback.print_exc()
    finally:
        await mongodb.close()

if __name__ == "__main__":
    asyncio.run(test_final_evaluation())