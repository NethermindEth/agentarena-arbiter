import asyncio
from app.database.mongodb_handler import mongodb
from app.models.finding_input import FindingInput

async def test_batch_findings():
    try:
        # Connect to MongoDB
        await mongodb.connect()
        print("✅ Connected to MongoDB")

        project_id = "test-project"
        
        # Clean test data
        collection = mongodb.get_collection_name(project_id)
        if collection in await mongodb.db.list_collection_names():
            await mongodb.db[collection].delete_many({})
            print(f"🧹 Cleaned collection {collection}")

        # SCENARIO 1: agent1 submits one finding
        print("\n📋 Scenario 1: agent1 submits one finding")
        agent1_finding1 = FindingInput(
            project_id=project_id,
            reported_by_agent="agent1",
            finding_id="FIND-A1-1",
            title="SQL Injection Vulnerability",
            description="A potential SQL injection vulnerability was found in the login form.",
            severity="HIGH",
            recommendation="Implement proper input validation.",
            code_references=["src/auth/login.py:45"]
        )
        
        result1 = await mongodb.create_finding(agent1_finding1)
        print(f"✅ Added agent1 first finding: {result1}")
        
        finding = await mongodb.get_finding(project_id, "FIND-A1-1")
        print(f"  FIND-A1-1 - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")
        
        # SCENARIO 2: agent1 submits two more findings in a batch
        print("\n📋 Scenario 2: agent1 submits two more findings")
        agent1_batch2_findings = [
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent1",
                finding_id="FIND-A1-2",
                title="Insecure Password Storage",
                description="Passwords are not stored using a strong hashing algorithm.",
                severity="MEDIUM",
                recommendation="Use bcrypt or Argon2 algorithms.",
                code_references=["src/auth/password.py:28"]
            ),
            FindingInput(
                project_id=project_id,
                reported_by_agent="agent1",
                finding_id="FIND-A1-3",
                title="Insecure File Upload",
                description="The file upload functionality does not validate file types.",
                severity="HIGH",
                recommendation="Implement strict file type checking.",
                code_references=["src/upload/handler.py:55"]
            )
        ]
        
        result2 = await mongodb.create_finding_batch(agent1_batch2_findings)
        print(f"✅ Added agent1 second batch findings, count: {len(result2)}")
        
        for finding_id in ["FIND-A1-2", "FIND-A1-3"]:
            finding = await mongodb.get_finding(project_id, finding_id)
            print(f"  {finding_id} - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")
        
        # SCENARIO 3: agent2 submits one finding
        print("\n📋 Scenario 3: agent2 submits one finding")
        agent2_finding = FindingInput(
            project_id=project_id,
            reported_by_agent="agent2",
            finding_id="FIND-A2-1",
            title="Cross-Site Scripting (XSS) Vulnerability",
            description="A potential XSS vulnerability was found in the user template.",
            severity="MEDIUM",
            recommendation="Implement proper output escaping.",
            code_references=["src/templates/user.py:23"]
        )
        
        result3 = await mongodb.create_finding(agent2_finding)
        print(f"✅ Added agent2 finding: {result3}")
        
        finding = await mongodb.get_finding(project_id, "FIND-A2-1")
        print(f"  FIND-A2-1 - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")
        
        # SCENARIO 4: agent1 submits one finding to a new project
        print("\n📋 Scenario 4: agent1 submits to a new project")
        
        # New project ID
        new_project_id = "test-project-2"
        
        # Clean test data for new project
        new_collection = mongodb.get_collection_name(new_project_id)
        if new_collection in await mongodb.db.list_collection_names():
            await mongodb.db[new_collection].delete_many({})
            print(f"🧹 Cleaned collection {new_collection}")
        
        # Create finding for the new project
        agent1_new_project_finding = FindingInput(
            project_id=new_project_id,
            reported_by_agent="agent1",
            finding_id="FIND-B1-1",
            title="Authentication Bypass",
            description="A vulnerability in the authentication flow allows bypass.",
            severity="HIGH",
            recommendation="Fix the authentication logic.",
            code_references=["src/auth/authentication.py:120"]
        )
        
        # Submit finding to new project
        result4 = await mongodb.create_finding(agent1_new_project_finding)
        print(f"✅ Added finding to new project: {result4}")
        
        finding = await mongodb.get_finding(new_project_id, "FIND-B1-1")
        print(f"  FIND-B1-1 - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}, project: {finding.project_id}")
        
        # Display summary of all findings
        print("\n📋 Final state of all findings:")
        print("First project:")
        for finding_id in ["FIND-A1-1", "FIND-A1-2", "FIND-A1-3", "FIND-A2-1"]:
            finding = await mongodb.get_finding(project_id, finding_id)
            print(f"  {finding_id} - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")
            
        print("Second project:")
        finding = await mongodb.get_finding(new_project_id, "FIND-B1-1")
        print(f"  FIND-B1-1 - submission_id: {finding.submission_id}, agent: {finding.reported_by_agent}")

    except Exception as e:
        print(f"❌ Error occurred: {str(e)}")
    finally:
        await mongodb.close()

if __name__ == "__main__":
    asyncio.run(test_batch_findings()) 