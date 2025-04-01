import asyncio
from app.database.mongodb_handler import mongodb
from app.models.finding_input import FindingInput
from app.core.finding_deduplication import FindingDeduplication

async def test_finding_deduplication():
    try:
        # Connect to MongoDB
        await mongodb.connect()
        print("✅ Connected to MongoDB")
        
        # Initialize deduplication handler
        deduplicator = FindingDeduplication()
        print("✅ Initialized deduplication handler")

        project_id = "test-dedup-project"
        
        # Clean test data
        collection = mongodb.get_collection_name(project_id)
        if collection in await mongodb.db.list_collection_names():
            await mongodb.db[collection].delete_many({})
            print(f"🧹 Cleaned collection {collection}")

        # SCENARIO 1: agent1 submits three findings, two of which are similar
        print("\n📋 Scenario 1: agent1 submits three findings (two are similar)")
        
        # Create three test findings - the first two are about SQL injection (clearly similar)
        # and the third is completely different (command injection)
        agent1_findings = [
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent1",
                finding_id="DEDUP-A1-1",
                title="SQL Injection in Login Form",
                description="The login form is vulnerable to SQL injection attacks. User input from the username field is directly concatenated into the SQL query without proper sanitization or parameterization.",
                severity="HIGH",
                recommendation="Use prepared statements and input validation to prevent SQL injection attacks.",
                code_references=["src/auth/login.py:42"]
            ),
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent1",
                finding_id="DEDUP-A1-2",
                title="SQL Injection Vulnerability in Authentication",
                description="The authentication system contains a SQL injection vulnerability where the login form's username parameter is vulnerable to injection attacks. This allows attackers to manipulate the database query.",
                severity="HIGH",
                recommendation="Implement parameterized queries instead of string concatenation for all database operations.",
                code_references=["src/auth/login.py:45"]
            ),
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent1",
                finding_id="DEDUP-A1-3",
                title="Command Injection in File Upload",
                description="The file upload functionality is vulnerable to command injection attacks. The application passes user-supplied filenames directly to a system command without proper validation.",
                severity="HIGH",
                recommendation="Validate filenames and use proper APIs instead of shell commands for file operations.",
                code_references=["src/upload/handler.py:78"]
            )
        ]
        
        # Process findings with deduplication
        results1 = await deduplicator.process_findings(project_id, "agent1", agent1_findings)
        print(f"✅ Processed agent1 findings:")
        print(f"  Total: {results1['total']}")
        print(f"  New: {results1['new']}")
        print(f"  Duplicates: {results1['duplicates']}")
        
        # Get and display all findings to check their status
        for finding_id in ["DEDUP-A1-1", "DEDUP-A1-2", "DEDUP-A1-3"]:
            finding = await mongodb.get_finding(project_id, finding_id)
            print(f"  {finding_id} - status: {finding.status}, agent: {finding.reported_by_agent}")
            if hasattr(finding, "evaluation_comment") and finding.evaluation_comment:
                print(f"    Comment: {finding.evaluation_comment[:100]}...")
        
        # SCENARIO 2: agent2 submits one finding with a completely different vulnerability type
        print("\n📋 Scenario 2: agent2 submits one finding (similar to agent1's SQL injection finding)")
        
        agent2_finding = FindingInput(
            project_id=project_id,
            reported_by_agent="agent2",
            finding_id="DEDUP-A2-1",
            title="SQL Injection in Authentication Page",
            description="The authentication page contains a SQL injection vulnerability. The login form username field allows SQL commands to be inserted and executed against the database due to improper input sanitization.",
            severity="HIGH",
            recommendation="Use parameterized queries and input validation to prevent SQL injection in the authentication system.",
            code_references=["src/auth/authenticate.py:38"]
        )
        
        # Process agent2's finding
        results2 = await deduplicator.process_findings(project_id, "agent2", [agent2_finding])
        print(f"✅ Processed agent2 finding:")
        print(f"  Total: {results2['total']}")
        print(f"  New: {results2['new']}")
        print(f"  Duplicates: {results2['duplicates']}")
        
        # Get and display agent2's finding
        finding = await mongodb.get_finding(project_id, "DEDUP-A2-1")
        print(f"  DEDUP-A2-1 - status: {finding.status}, agent: {finding.reported_by_agent}")
        if hasattr(finding, "evaluation_comment") and finding.evaluation_comment:
            print(f"    Comment: {finding.evaluation_comment[:100]}...")
        
        # Display summary of all findings
        print("\n📋 Final state of all findings:")
        for finding_id in ["DEDUP-A1-1", "DEDUP-A1-2", "DEDUP-A1-3", "DEDUP-A2-1"]:
            finding = await mongodb.get_finding(project_id, finding_id)
            print(f"  {finding_id} - status: {finding.status}, agent: {finding.reported_by_agent}")

    except Exception as e:
        print(f"❌ Error occurred: {str(e)}")
    finally:
        await mongodb.close()

if __name__ == "__main__":
    asyncio.run(test_finding_deduplication()) 