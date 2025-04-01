"""
Test script for cross-agent comparison functionality.
Tests the detection of similar findings across different agents.
"""
import os
import asyncio
import traceback
from datetime import datetime
from dotenv import load_dotenv

from app.database.mongodb_handler import mongodb
from app.models.finding_input import FindingInput
from app.models.finding_db import FindingDB, Status, EvaluatedSeverity
from app.core.finding_deduplication import FindingDeduplication
from app.core.cross_agent_comparison import CrossAgentComparison

# Load environment variables
load_dotenv()

async def setup_test_data(project_id: str):
    """
    Setup test data for cross-agent comparison testing.
    Creates findings from agent1, marking them as unique_valid with categories.
    
    Args:
        project_id: Project identifier for the test
    """
    # Clean existing data
    collection = mongodb.get_collection_name(project_id)
    await mongodb.db[collection].delete_many({})
    print(f"🧹 Cleaned collection {collection}")
    
    # Agent 1 - Create first evaluated finding (unique_valid)
    agent1_finding1 = FindingInput(
        project_id=project_id,
        reported_by_agent="agent1",
        finding_id="CROSS-A1-1",
        title="SQL Injection in Login Form",
        description="The login form is vulnerable to SQL injection attacks due to improper input validation.",
        severity="HIGH",
        recommendation="Use parameterized queries and input validation.",
        code_references=["auth/login.py:45-50", "models/user.py:120-125"]
    )
    
    # Convert to FindingDB and mark as evaluated
    agent1_db1 = FindingDB(
        **agent1_finding1.model_dump(),
        submission_id=0,
        status=Status.UNIQUE_VALID,
        category="Injection",
        category_id="CAT-SQLI-01",
        evaluated_severity=EvaluatedSeverity.HIGH,
        evaluation_comment="Confirmed SQL injection vulnerability with high impact.",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    # Insert into database
    await mongodb.create_finding(agent1_db1)
    print(f"✅ Created first evaluated finding for agent1: {agent1_db1.finding_id}")
    
    # Agent 1 - Create second evaluated finding (unique_valid)
    agent1_finding2 = FindingInput(
        project_id=project_id,
        reported_by_agent="agent1",
        finding_id="CROSS-A1-2",
        title="Insecure Direct Object Reference",
        description="The application allows direct access to objects via user-supplied IDs without proper authorization.",
        severity="MEDIUM",
        recommendation="Implement proper access control checks for all object references.",
        code_references=["controllers/user_controller.py:78-85", "routes/api.py:30-40"]
    )
    
    # Convert to FindingDB and mark as evaluated
    agent1_db2 = FindingDB(
        **agent1_finding2.model_dump(),
        submission_id=1,
        status=Status.UNIQUE_VALID,
        category="Access Control",
        category_id="CAT-IDOR-01",
        evaluated_severity=EvaluatedSeverity.MEDIUM,
        evaluation_comment="Confirmed IDOR vulnerability with medium impact.",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    # Insert into database
    await mongodb.create_finding(agent1_db2)
    print(f"✅ Created second evaluated finding for agent1: {agent1_db2.finding_id}")

async def test_cross_agent_comparison():
    """
    Test the cross-agent comparison functionality.
    """
    try:
        # Connect to MongoDB
        await mongodb.connect()
        print("✅ Connected to MongoDB")
        
        # Check Claude API key
        claude_api_key = os.getenv("CLAUDE_API_KEY")
        if not claude_api_key:
            print("❌ CLAUDE_API_KEY is not set in environment variables")
            return
        
        # Project ID for test
        project_id = "test-cross-agent-project"
        
        # Setup test data (agent1's findings)
        await setup_test_data(project_id)
        
        # Create test findings for agent2
        print("\n📋 SCENARIO: Agent2 submits two findings, one similar to agent1's SQL injection finding")
        
        # Agent2 finding similar to agent1's SQL injection
        agent2_similar = FindingInput(
            project_id=project_id,
            reported_by_agent="agent2",
            finding_id="CROSS-A2-1",
            title="SQL Injection Vulnerability in Authentication",
            description="The authentication endpoint is vulnerable to SQL injection in the username parameter.",
            severity="HIGH",
            recommendation="Implement prepared statements for database queries.",
            code_references=["auth/authenticate.py:30-35", "models/user.py:118-122"]
        )
        
        # Agent2 finding that is unique (not similar to any agent1 finding)
        agent2_unique = FindingInput(
            project_id=project_id,
            reported_by_agent="agent2",
            finding_id="CROSS-A2-2",
            title="Cross-Site Scripting in Profile Page",
            description="The user profile page is vulnerable to stored XSS attacks when displaying user-provided content.",
            severity="MEDIUM",
            recommendation="Implement proper output encoding for user-generated content.",
            code_references=["views/profile.py:50-60", "templates/user_profile.html:25-30"]
        )
        
        # Create a list of agent2's findings
        agent2_findings = [
            agent2_similar,
            agent2_unique
        ]
        
        # Process the findings through cross-agent comparison
        cross_comparison = CrossAgentComparison()
        results = await cross_comparison.process_new_findings(project_id, "agent2", agent2_findings)
        
        # Print results
        print("\nResults from agent2's submission:")
        print(f"  Deduplication - Total: {results['deduplication']['total']}")
        print(f"  Deduplication - New: {results['deduplication']['new']} - {results['deduplication']['new_ids']}")
        print(f"  Cross-Comparison - Similar to other agents: {results['cross_comparison']['similar_valid']} - {results['cross_comparison']['similar_ids']}")
        print(f"  Cross-Comparison - Pending evaluation: {results['cross_comparison']['pending_evaluation']} - {results['cross_comparison']['pending_ids']}")
        
        # Get all findings for review
        all_findings = await mongodb.get_project_findings(project_id)
        print(f"\n📋 Final status of all findings (total: {len(all_findings)}):")
        
        # Group findings by status
        findings_by_status = {}
        for finding in all_findings:
            status = finding.status
            if status not in findings_by_status:
                findings_by_status[status] = []
            findings_by_status[status].append(finding)
        
        # Print findings by status
        for status, findings in findings_by_status.items():
            print(f"\n{status} Findings ({len(findings)}):")
            for finding in findings:
                print(f"\nFinding {finding.finding_id}:")
                print(f"  Agent: {finding.reported_by_agent}")
                print(f"  Title: {finding.title}")
                print(f"  Status: {finding.status}")
                
                # Print category info if available
                category = getattr(finding, 'category', None)
                if category:
                    print(f"  Category: {category}")
                    category_id = getattr(finding, 'category_id', None)
                    if category_id:
                        print(f"  Category ID: {category_id}")
                        
                # Print severity info if available
                evaluated_severity = getattr(finding, 'evaluated_severity', None)
                if evaluated_severity:
                    print(f"  Evaluated Severity: {evaluated_severity}")
                
                # Print comment if available
                comment = getattr(finding, 'evaluation_comment', None)
                if comment:
                    print(f"  Evaluation: {comment[:100]}..." if len(comment) > 100 else f"  Evaluation: {comment}")
        
        print("\n✅ Test completed successfully.")
        
    except Exception as e:
        print(f"❌ Error occurred: {str(e)}")
        traceback.print_exc()
    finally:
        await mongodb.close()

if __name__ == "__main__":
    # Ensure Claude API key is set
    if not os.getenv("CLAUDE_API_KEY"):
        print("❌ CLAUDE_API_KEY not set in environment variables")
    else:
        asyncio.run(test_cross_agent_comparison()) 