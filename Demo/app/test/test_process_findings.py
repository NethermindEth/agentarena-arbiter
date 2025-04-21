"""
Test script for the process_findings API endpoint functionality.
Tests the complete vulnerability processing pipeline including deduplication and automatic evaluation.
"""
import asyncio
import json
import httpx
import traceback
import uuid
from datetime import datetime
from app.database.mongodb_handler import mongodb
from app.models.finding_input import FindingInput, Finding, Severity
from app.models.finding_db import Status

# API base URL
BASE_URL = "http://localhost:8000"

async def test_process_findings():
    """Test the complete process_findings functionality including deduplication and automatic evaluation."""
    try:
        # Connect to MongoDB for cleanup
        await mongodb.connect()
        print("✅ Connected to MongoDB")

        # Use a fixed task_id for consistent testing
        task_id = "test_project_new"
        print(f"🔑 Using task ID: {task_id}")
        
        # Clean test data to ensure a fresh start
        collection = mongodb.get_collection_name(task_id)
        if collection in await mongodb.db.list_collection_names():
            await mongodb.db[collection].delete_many({})
            print(f"🧹 Cleaned collection {collection}")
        
        # SCENARIO 1: First submission - all new findings
        print("\n📋 SCENARIO 1: First submission - all new findings")
        
        # Create test findings
        findings_batch1 = [
            Finding(
                title="Access Control Bypass in transferOwnership Function",
                description="The transferOwnership function lacks proper access control checks, allowing any user to call it and take ownership of the contract. This could lead to complete control of contract funds and operations by malicious actors.",
                severity=Severity.HIGH
            ),
            Finding(
                title="Unprotected Self-Destruct Mechanism",
                description="The contract contains a self-destruct function that is not properly protected by access controls or time locks. An attacker could potentially destroy the contract and force ETH to be sent to an attacker-controlled address.",
                severity=Severity.HIGH
            )
        ]
        
        # Create FindingInput
        input_batch1 = FindingInput(
            task_id=task_id,
            agent_id="agent1",
            findings=findings_batch1
        )
        
        # Process findings via API
        print("\n📊 Processing first batch of findings")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Process findings
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    json=input_batch1.model_dump()
                )
                
                # Check response
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ First batch processed successfully")
                    
                    # Get the deduplication results
                    dedup_results = result.get("deduplication", {})
                    
                    # Print summary
                    print(f"  Total findings: {dedup_results.get('total', 'N/A')}")
                    print(f"  New findings: {dedup_results.get('new', 'N/A')}")
                    print(f"  Duplicates: {dedup_results.get('duplicates', 'N/A')}")
                    
                    # Check auto evaluation 
                    auto_eval = result.get("auto_evaluation", {})      
                    if auto_eval:
                        print(f"\n✅ Automatic evaluation completed")
                        print(f"  Total evaluated: {auto_eval.get('total_pending', 'N/A')}")
                        
                        # Verification: at least one finding should be evaluated
                        assert auto_eval.get("total_pending", 0) > 0, "Should have at least one finding evaluated"
                    else:
                        print(f"❌ Automatic evaluation not performed")
                else:
                    print(f"❌ Failed to process first batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except Exception as e:
            print(f"❌ Unexpected error during first batch: {str(e)}")
            traceback.print_exc()
            return
                
        # SCENARIO 2: Second submission - duplicate finding test
        print("\n📋 SCENARIO 2: Second submission - duplicate finding test")
        
        # Create finding with known duplicate title
        findings_batch2 = [
            Finding(
                title="Access Control Bypass in transferOwnership Function",  # Duplicate title
                description="The transferOwnership function can be called by any address due to missing access control, which would allow attackers to take control of the contract and its assets.",
                severity=Severity.HIGH
            )
        ]
        
        # Create FindingInput
        input_batch2 = FindingInput(
            task_id=task_id,
            agent_id="agent1",
            findings=findings_batch2
        )
        
        # Process findings via API
        print("\n📊 Processing second batch of findings")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Process findings
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    json=input_batch2.model_dump()
                )
                
                # Check response
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ Second batch processed successfully")
                    
                    # Get deduplication results
                    dedup_results = result.get("deduplication", {})
                    
                    # Print summary
                    print(f"  Total findings: {dedup_results.get('total', 'N/A')}")
                    print(f"  New findings: {dedup_results.get('new', 'N/A')}")
                    print(f"  Duplicates: {dedup_results.get('duplicates', 'N/A')}")
                    
                    # Verification: all should be duplicates
                    assert dedup_results.get("total", 0) == dedup_results.get("duplicates", 0), "All findings should be duplicates"
                    assert "Access Control Bypass in transferOwnership Function" in dedup_results.get("duplicate_titles", []), "Access Control finding should be marked as duplicate"
                else:
                    print(f"❌ Failed to process second batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except Exception as e:
            print(f"❌ Error occurred: {str(e)}")
            traceback.print_exc()
            return
                
        # SCENARIO 3: Findings from a different agent
        print("\n📋 SCENARIO 3: Findings from a different agent")
        
        # Create findings for a different agent with similar content
        findings_batch3 = [
            Finding(
                title="Missing Access Control in Owner Transfer Function",  # Similar to existing but different title
                description="The function for transferring contract ownership lacks proper authorization checks, allowing any address to become the contract owner and gain full control.",
                severity=Severity.HIGH  # Different severity
            ),
            Finding(
                title="Timestamp Manipulation Vulnerability",  # Different type of vulnerability
                description="The contract uses block.timestamp as a source of randomness, which can be manipulated by miners within a certain range. This can lead to predictable outcomes in functions that rely on this value.",
                severity=Severity.MEDIUM
            )
        ]
        
        # Create FindingInput
        input_batch3 = FindingInput(
            task_id=task_id,
            agent_id="agent2",  # Different agent
            findings=findings_batch3
        )
        
        # Process findings via API
        print("\n📊 Processing third batch of findings (different agent)")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Process findings
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    json=input_batch3.model_dump()
                )
                
                # Check response
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ Third batch processed successfully")
                    
                    # Get results
                    dedup_results = result.get("deduplication", {})
                    cross_results = result.get("cross_comparison", {})
                    
                    # Print summary
                    print(f"  Total findings: {dedup_results.get('total', 'N/A')}")
                    print(f"  Agent deduplication:")
                    print(f"    New: {dedup_results.get('new', 'N/A')}")
                    print(f"    Duplicates: {dedup_results.get('duplicates', 'N/A')}")
                    
                    if cross_results:
                        print(f"  Cross-agent comparison:")
                        print(f"    Similar valid: {cross_results.get('similar_valid', 'N/A')}")
                        
                    # Verification: at least one finding should be recognized as similar to another agent's finding
                    if cross_results:
                        assert cross_results.get("similar_valid", 0) > 0, "Should detect similar findings across agents"
                else:
                    print(f"❌ Failed to process third batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except Exception as e:
            print(f"❌ Error occurred: {str(e)}")
            traceback.print_exc()
            return
                
        # Verify final state
        print("\n📊 Retrieving all findings to verify final state")
        
        async with httpx.AsyncClient() as client:
            # Get all findings
            response = await client.get(
                f"{BASE_URL}/tasks/{task_id}/findings"
            )
            
            if response.status_code == 200:
                findings = response.json()
                print(f"✅ Retrieved {len(findings)} total findings")
                
                # Count by status
                status_counts = {}
                agent_counts = {}
                
                for finding in findings:
                    # Count by status
                    status = finding['status']
                    status_counts[status] = status_counts.get(status, 0) + 1
                    
                    # Count by agent
                    agent = finding['agent_id']
                    agent_counts[agent] = agent_counts.get(agent, 0) + 1
                
                print(f"  Status distribution: {status_counts}")
                print(f"  Agent distribution: {agent_counts}")
                
                # Verify no pending findings remain
                assert "pending" not in status_counts, "All findings should be processed, no PENDING status should remain"
                print(f"✅ All findings processed successfully - no pending findings remain")
            else:
                print(f"❌ Failed to retrieve findings: {response.status_code}")
                print(f"Response text: {response.text}")
    
    except AssertionError as e:
        print(f"❌ Test assertion failed: {str(e)}")
        traceback.print_exc()
    except Exception as e:
        print(f"❌ Error occurred: {str(e)}")
        traceback.print_exc()
    finally:
        await mongodb.close()
        print("\n✅ Test completed")

if __name__ == "__main__":
    asyncio.run(test_process_findings()) 