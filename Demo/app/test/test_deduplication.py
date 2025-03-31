"""
Test script for finding deduplication functionality using batch submission patterns.
"""
import asyncio
import os
import traceback
from app.core.finding_deduplication import FindingDeduplication
from app.database.mongodb_handler import mongodb
from app.models.finding_input import FindingInput
from app.models.finding_db import FindingDB
from dotenv import load_dotenv

async def test_deduplication_with_batches():
    """
    Test the finding deduplication functionality with batch submissions:
    - agent1 first submits one finding
    - agent1 then submits two more findings (one similar to the first, one new)
    - agent2 submits one finding similar to agent1's first finding
    """
    try:
        # Debug environment variables
        print("\n🔍 Checking environment variables:")
        try:
            api_key = os.environ.get("CLAUDE_API_KEY", "")
            print(f"Raw ENV value: {api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else f"Raw ENV value: {api_key}")
        except Exception as e:
            print(f"Error accessing ENV: {str(e)}")
        
        # Force reload .env file
        load_dotenv(override=True)
        
        # Check again after reload
        try:
            api_key = os.environ.get("CLAUDE_API_KEY", "")
            print(f"After reload: {api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else f"After reload: {api_key}")
            
            # Set it manually if needed
            if not api_key or not api_key.startswith("sk-ant-"):
                # Read directly from file as a fallback
                with open(".env", "r") as f:
                    for line in f:
                        if line.startswith("CLAUDE_API_KEY="):
                            api_key = line.split("=", 1)[1].strip()
                            os.environ["CLAUDE_API_KEY"] = api_key
                            print(f"Manually set from file: {api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else f"Manually set: {api_key}")
                            break
        except Exception as e:
            print(f"Error reloading ENV: {str(e)}")
            
        # Connect to MongoDB
        await mongodb.connect()
        print("✅ Connected to MongoDB")
        
        # Initialize the deduplication handler
        deduplicator = FindingDeduplication(similarity_threshold=0.75)
        
        # Setup test findings
        project_id = "test-dedup-project"
        
        # Clear any existing test data
        collection = mongodb.get_collection_name(project_id)
        if collection in await mongodb.db.list_collection_names():
            await mongodb.db[collection].delete_many({})
            print(f"🧹 Cleaned collection {collection}")
        
        print("\n📋 SCENARIO 1: agent1's first submission with one finding")
        # agent1's first finding (original finding)
        agent1_finding1 = FindingInput(
            project_id=project_id,
            reported_by_agent="agent1",
            finding_id="FIND-A1-1",
            title="SQL Injection Vulnerability in Login Form",
            description="A SQL injection vulnerability was found in the login form. The application does not properly sanitize user input before using it in database queries.",
            severity="HIGH",
            recommendation="Implement proper input validation and use parameterized queries to prevent SQL injection attacks.",
            code_references=["src/auth/login.py:45", "src/database/connection.py:78"]
        )
        
        # Process agent1's first submission (one finding)
        results1 = await deduplicator.process_findings(
            project_id=project_id,
            agent_id="agent1",
            new_findings=[agent1_finding1]
        )
        
        print(f"Results from agent1's first submission:")
        print(f"  Total: {results1['total']}")
        print(f"  New: {results1['new']} - {results1['new_ids']}")
        print(f"  Duplicates: {results1['duplicates']} - {results1['duplicate_ids']}")
        
        # Check submission ID of first finding
        finding1 = await mongodb.get_finding(project_id, "FIND-A1-1")
        print(f"  First finding submission_id: {finding1.submission_id}\n")
        
        print("\n📋 SCENARIO 2: agent1's second submission with two findings")
        # agent1's second batch: one similar to first finding, one new
        agent1_finding2 = FindingInput(
            project_id=project_id,
            reported_by_agent="agent1",
            finding_id="FIND-A1-2",
            title="SQL Injection in Authentication Form",
            description="The authentication form is vulnerable to SQL injection attacks because user input is not properly sanitized before being used in database queries.",
            severity="HIGH",
            recommendation="Use parameterized queries and input validation to prevent SQL injection.",
            code_references=["src/auth/login.py:45", "src/database/connection.py:80"]
        )
        
        agent1_finding3 = FindingInput(
            project_id=project_id,
            reported_by_agent="agent1",
            finding_id="FIND-A1-3",
            title="Insecure File Upload",
            description="The file upload functionality does not properly validate file types, allowing potential execution of malicious files.",
            severity="MEDIUM",
            recommendation="Implement strict file type checking and validation. Store files outside web root.",
            code_references=["src/upload/handler.py:55", "src/config/security.py:28"]
        )
        
        # Process agent1's second batch
        results2 = await deduplicator.process_findings(
            project_id=project_id,
            agent_id="agent1",
            new_findings=[agent1_finding2, agent1_finding3]
        )
        
        print(f"Results from agent1's second submission:")
        print(f"  Total: {results2['total']}")
        print(f"  New: {results2['new']} - {results2['new_ids']}")
        print(f"  Duplicates: {results2['duplicates']} - {results2['duplicate_ids']}")
        
        # Check submission IDs of second batch
        finding2 = await mongodb.get_finding(project_id, "FIND-A1-2")
        finding3 = await mongodb.get_finding(project_id, "FIND-A1-3")
        print(f"  Second finding submission_id: {finding2.submission_id}")
        print(f"  Third finding submission_id: {finding3.submission_id}\n")
        
        print("\n📋 SCENARIO 3: agent2's submission with one finding similar to agent1's first finding")
        # agent2's finding similar to agent1's first finding
        agent2_finding1 = FindingInput(
            project_id=project_id,
            reported_by_agent="agent2",
            finding_id="FIND-A2-1",
            title="SQL Injection in User Login Page",
            description="The login page has a SQL injection vulnerability. User-supplied input is directly concatenated into SQL queries.",
            severity="HIGH",
            recommendation="Use parameterized queries and validate all user input before using in database operations.",
            code_references=["src/auth/login.py:47", "src/database/connection.py:75"]
        )
        
        # NEW: second finding for agent2 that is similar to the first
        agent2_finding2 = FindingInput(
            project_id=project_id,
            reported_by_agent="agent2",
            finding_id="FIND-A2-2", 
            title="SQL Injection Vulnerability in Authentication Form",
            description="The authentication form contains a SQL injection vulnerability because user inputs are concatenated directly into SQL queries without proper sanitization.",
            severity="HIGH",
            recommendation="Use parameterized queries and validate all user input before using in database operations.",
            code_references=["src/auth/login.py:50", "src/database/connection.py:72"]
        )
        
        # Process agent2's submission with TWO findings (one similar to agent1, one similar to itself)
        results3 = await deduplicator.process_findings(
            project_id=project_id,
            agent_id="agent2",
            new_findings=[agent2_finding1, agent2_finding2]
        )
        
        print(f"Results from agent2's submission:")
        print(f"  Total: {results3['total']}")
        print(f"  New: {results3['new']} - {results3['new_ids']}")
        print(f"  Duplicates: {results3['duplicates']} - {results3['duplicate_ids']}")
        
        # Check submission IDs of agent2's findings
        finding4 = await mongodb.get_finding(project_id, "FIND-A2-1")
        finding5 = await mongodb.get_finding(project_id, "FIND-A2-2")
        print(f"  Agent2's first finding submission_id: {finding4.submission_id}")
        print(f"  Agent2's second finding submission_id: {finding5.submission_id}\n")
        
        # Display summary of all findings
        print("\n📋 Final status of all findings:")
        for finding_id in ["FIND-A1-1", "FIND-A1-2", "FIND-A1-3", "FIND-A2-1", "FIND-A2-2"]:
            finding = await mongodb.get_finding(project_id, finding_id)
            if finding:
                print(f"\nFinding {finding_id}:")
                print(f"  Title: {finding.title}")
                print(f"  Agent: {finding.reported_by_agent}")
                print(f"  Submission ID: {finding.submission_id}")
                print(f"  Category: {finding.category}")
                if finding.evaluation_comment:
                    print(f"  Evaluation: {finding.evaluation_comment}")
        
        print("\n✅ Test completed. Data has been kept in the database for review.")
        print(f"You can now use MongoDB Compass to examine the {collection} collection.")
        
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
        asyncio.run(test_deduplication_with_batches()) 