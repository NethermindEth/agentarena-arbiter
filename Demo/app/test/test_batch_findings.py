import asyncio
from app.database.mongodb_handler import mongodb
from app.models.finding_input import FindingInput

async def test_batch_findings():
    try:
        # Connect to MongoDB
        await mongodb.connect()
        print("✅ Connected to MongoDB")

        project_id = "test-batch-project"
        
        # Clean test data
        collection = mongodb.get_collection_name(project_id)
        if collection in await mongodb.db.list_collection_names():
            await mongodb.db[collection].delete_many({})
            print(f"🧹 Cleaned collection {collection}")

        # Create two findings from agent1 for first batch
        agent1_batch1_findings = [
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent1",
                finding_id="FIND-A1-1",
                title="SQL Injection Vulnerability",
                description="A potential SQL injection vulnerability was found in the login form. The application does not properly sanitize user input before using it in database queries.",
                severity="HIGH",
                recommendation="Implement proper input validation and use parameterized queries to prevent SQL injection attacks.",
                code_references=["src/auth/login.py:45"]
            ),
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent1",
                finding_id="FIND-A1-2",
                title="Insecure Password Storage",
                description="Passwords are not stored using a sufficiently strong hashing algorithm.",
                severity="MEDIUM",
                recommendation="Use bcrypt or Argon2 algorithms for password storage.",
                code_references=["src/auth/password.py:28"]
            )
        ]
        
        # Create one finding from agent1 for second batch
        agent1_batch2_finding = FindingInput(
            project_id=project_id,
            reported_by_agent="agent1",
            finding_id="FIND-A1-3",
            title="Insecure File Upload",
            description="The file upload functionality does not properly validate file types.",
            severity="HIGH",
            recommendation="Implement strict file type checking and validation.",
            code_references=["src/upload/handler.py:55"]
        )
        
        # Create two findings from agent2 for third batch
        agent2_batch1_findings = [
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent2",
                finding_id="FIND-A2-1",
                title="Cross-Site Scripting (XSS) Vulnerability",
                description="A potential XSS vulnerability was found in the user template. The application does not properly escape user input before rendering it in the template.",
                severity="MEDIUM",
                recommendation="Implement proper output escaping to prevent XSS attacks.",
                code_references=["src/templates/user.py:23"]
            ),
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent2",
                finding_id="FIND-A2-2",
                title="Missing Content Security Policy",
                description="The website has not implemented a Content Security Policy (CSP).",
                severity="LOW",
                recommendation="Add appropriate Content Security Policy headers.",
                code_references=["src/app.py:15"]
            )
        ]
        
        # SCENARIO 1: agent1 submits two findings in first batch
        print("\n📋 Scenario 1: agent1 submits two findings")
        result1 = await mongodb.create_finding_batch(agent1_batch1_findings)
        print(f"✅ Added agent1 first batch findings, count: {len(result1)}")
        
        # Get and display findings to check submission_id
        for finding_id in ["FIND-A1-1", "FIND-A1-2"]:
            finding = await mongodb.get_finding(project_id, finding_id)
            print(f"  {finding_id} - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")
        
        # SCENARIO 2: agent1 submits one more finding in second batch
        print("\n📋 Scenario 2: agent1 submits one more finding")
        result2 = await mongodb.create_finding(agent1_batch2_finding)
        print(f"✅ Added agent1 second batch finding: {result2}")
        
        # Get and display the new finding to check submission_id
        finding = await mongodb.get_finding(project_id, "FIND-A1-3")
        print(f"  FIND-A1-3 - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")
        
        # SCENARIO 3: agent2 submits two findings
        print("\n📋 Scenario 3: agent2 submits two findings")
        result3 = await mongodb.create_finding_batch(agent2_batch1_findings)
        print(f"✅ Added agent2 findings, count: {len(result3)}")
        
        # Get and display agent2's findings to check submission_id
        for finding_id in ["FIND-A2-1", "FIND-A2-2"]:
            finding = await mongodb.get_finding(project_id, finding_id)
            print(f"  {finding_id} - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")
        
        # SCENARIO 4: Same agents submit to a different project
        print("\n📋 Scenario 4: Submissions to a new project")
        
        # New project ID
        new_project_id = "test-batch-project-2"
        
        # Clean test data for new project
        new_collection = mongodb.get_collection_name(new_project_id)
        if new_collection in await mongodb.db.list_collection_names():
            await mongodb.db[new_collection].delete_many({})
            print(f"🧹 Cleaned collection {new_collection}")
        
        # Create findings for the new project
        new_project_findings = [
            # Agent1 findings for new project
            FindingInput(
                project_id=new_project_id,
                reported_by_agent="agent1",
                finding_id="FIND-B1-1",
                title="Authentication Bypass",
                description="A vulnerability in the authentication flow allows users to bypass login requirements.",
                severity="CRITICAL",
                recommendation="Fix the authentication logic to properly validate user credentials.",
                code_references=["src/auth/authentication.py:120"]
            ),
            # Agent2 findings for new project
            FindingInput(
                project_id=new_project_id,
                reported_by_agent="agent2",
                finding_id="FIND-B2-1",
                title="Insecure Direct Object Reference",
                description="The API endpoints don't properly validate object ownership before access.",
                severity="HIGH",
                recommendation="Implement proper authorization checks for all object access.",
                code_references=["src/api/resources.py:87"]
            )
        ]
        
        # Submit findings to new project
        result4 = await mongodb.create_finding_batch(new_project_findings)
        print(f"✅ Added findings to new project, count: {len(result4)}")
        
        # Get and display new project findings
        for finding_id in ["FIND-B1-1", "FIND-B2-1"]:
            finding = await mongodb.get_finding(new_project_id, finding_id)
            print(f"  {finding_id} - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}, project: {finding.project_id}")
        
        # Display summary of all findings
        print("\n📋 Final state of all findings in first project:")
        for finding_id in ["FIND-A1-1", "FIND-A1-2", "FIND-A1-3", "FIND-A2-1", "FIND-A2-2"]:
            finding = await mongodb.get_finding(project_id, finding_id)
            print(f"  {finding_id} - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")
            
        print("\n📋 Final state of all findings in second project:")
        for finding_id in ["FIND-B1-1", "FIND-B2-1"]:
            finding = await mongodb.get_finding(new_project_id, finding_id)
            print(f"  {finding_id} - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")

    except Exception as e:
        print(f"❌ Error occurred: {str(e)}")
    finally:
        await mongodb.close()

if __name__ == "__main__":
    asyncio.run(test_batch_findings()) 