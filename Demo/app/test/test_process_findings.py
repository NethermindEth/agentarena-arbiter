"""
Simplified test for process_findings API - tests core functionality with testing mode.
Clean, focused tests that work with the improved testing mode authentication bypass.
"""
import asyncio
import httpx
from app.models.finding_input import FindingInput, Finding, Severity
from app.database.mongodb_handler import mongodb
from app.config import config

# Test configuration
BASE_URL = "http://localhost:8004"
API_KEY = "test-api-key"  # Any key works in testing mode
TEST_TASK_ID = "test-simple"

async def setup_test():
    """Setup: Connect to MongoDB and clean test data."""
    await mongodb.connect()
    collection = mongodb.get_collection_name(TEST_TASK_ID)
    if collection in await mongodb.db.list_collection_names():
        await mongodb.db[collection].delete_many({})
    print("✅ Test setup complete")

async def teardown_test():
    """Cleanup: Close MongoDB connection."""
    await mongodb.close()
    print("✅ Test cleanup complete")

async def test_basic_submission():
    """Test 1: Basic submission works and returns expected format."""
    print("\n🧪 Test 1: Basic Submission")
    
    findings = [
        Finding(
            title="Reentrancy Vulnerability",
            description="Withdraw function vulnerable to reentrancy attacks.",
            file_paths=["contracts/Test.sol"],
            severity=Severity.HIGH
        ),
        Finding(
            title="Unchecked Return Value",
            description="External call return value not checked.",
            file_paths=["contracts/Test.sol"],
            severity=Severity.MEDIUM
        )
    ]
    
    input_data = FindingInput(task_id=TEST_TASK_ID, findings=findings)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{BASE_URL}/process_findings",
            headers={"X-API-Key": API_KEY},
            json=input_data.model_dump()
        )
        
        print(f"  Response status: {response.status_code}")
        if response.status_code != 200:
            print(f"  Response text: {response.text}")
            
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        result = response.json()
        
        # Basic response format check
        assert "valid" in result, "Response missing 'valid' field"
        assert "already_reported" in result, "Response missing 'already_reported' field"
        assert "disputed" in result, "Response missing 'disputed' field"
        assert result["already_reported"] == 0, "First submission should have no duplicates"
        
        print(f"  ✅ Response: {result}")

async def test_duplicate_detection():
    """Test 2: Duplicate detection works."""
    print("\n🧪 Test 2: Duplicate Detection")
    
    # Submit the same finding twice
    finding = Finding(
        title="Duplicate Test Finding",
        description="This finding will be submitted twice to test deduplication.",
        file_paths=["contracts/Test.sol"],
        severity=Severity.HIGH
    )
    
    input_data = FindingInput(task_id=TEST_TASK_ID, findings=[finding])
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # First submission
        response1 = await client.post(
            f"{BASE_URL}/process_findings",
            headers={"X-API-Key": API_KEY},
            json=input_data.model_dump()
        )
        assert response1.status_code == 200
        result1 = response1.json()
        print(f"  First submission: {result1}")
        
        # Second submission (duplicate)
        response2 = await client.post(
            f"{BASE_URL}/process_findings",
            headers={"X-API-Key": API_KEY},
            json=input_data.model_dump()
        )
        assert response2.status_code == 200
        result2 = response2.json()
        print(f"  Second submission: {result2}")
        
        # Should detect duplicate
        assert result2["already_reported"] > 0, "Duplicate should be detected"
        print("  ✅ Duplicate detection working")

async def test_max_findings_limit():
    """Test 3: Max findings limit enforcement."""
    print("\n🧪 Test 3: Max Findings Limit")
    
    # Create more findings than allowed
    max_allowed = config.max_findings_per_submission
    findings = []
    for i in range(max_allowed + 1):
        findings.append(Finding(
            title=f"Finding {i+1}",
            description=f"Test finding {i+1} to exceed limit.",
            file_paths=["contracts/Test.sol"],
            severity=Severity.MEDIUM
        ))
    
    input_data = FindingInput(task_id=TEST_TASK_ID, findings=findings)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{BASE_URL}/process_findings",
            headers={"X-API-Key": API_KEY},
            json=input_data.model_dump()
        )
        
        print(f"  Response status: {response.status_code}")
        if response.status_code != 400:
            print(f"  Response text: {response.text}")
        
        # Should be rejected with 400 error
        assert response.status_code == 400, f"Expected 400, got {response.status_code}"
        error_text = response.text
        assert f"Maximum allowed: {max_allowed}" in error_text, "Error should mention limit"
        
        print(f"  ✅ Limit enforced: {len(findings)} findings rejected")

async def test_testing_mode_bypass():
    """Test 4: Testing mode bypasses authentication."""
    print("\n🧪 Test 4: Testing Mode Authentication Bypass")
    
    # Test with a clearly fake API key
    fake_api_key = "definitely-not-a-real-api-key-12345"
    
    finding = Finding(
        title="Testing Mode Bypass Test",
        description="This tests that testing mode bypasses authentication.",
        file_paths=["contracts/Test.sol"],
        severity=Severity.LOW
    )
    
    input_data = FindingInput(task_id=TEST_TASK_ID, findings=[finding])
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{BASE_URL}/process_findings",
            headers={"X-API-Key": fake_api_key},
            json=input_data.model_dump()
        )
        
        print(f"  Using fake API key: {fake_api_key}")
        print(f"  Response status: {response.status_code}")
        
        # Should work because testing mode bypasses authentication
        assert response.status_code == 200, f"Testing mode should bypass auth, got {response.status_code}"
        result = response.json()
        print(f"  ✅ Authentication bypassed: {result}")

async def run_all_tests():
    """Run all simplified tests."""
    print("🚀 Starting Process Findings Tests (Testing Mode)")
    print("=" * 55)
    
    # Verify testing mode is enabled
    if not config.testing:
        print("❌ TESTING mode is not enabled!")
        print("   Set TESTING=true in environment variables")
        return
    
    print(f"✅ Testing mode enabled: {config.testing}")
    
    try:
        await setup_test()
        
        await test_basic_submission()
        await test_duplicate_detection() 
        await test_max_findings_limit()
        await test_testing_mode_bypass()
        
        print("\n" + "=" * 55)
        print("✅ All tests passed!")
        
    except Exception as e:
        print(f"\n❌ Test failed: {str(e)}")
        raise
    finally:
        await teardown_test()

if __name__ == "__main__":
    asyncio.run(run_all_tests())