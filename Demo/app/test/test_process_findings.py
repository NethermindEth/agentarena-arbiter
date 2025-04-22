"""
Test script for the process_findings API endpoint functionality.
Tests the complete vulnerability processing pipeline including deduplication and automatic evaluation.
"""
import asyncio
import json
import httpx
import traceback
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

        task_id = "test-process-findings"
        
        # Clean test data
        collection = mongodb.get_collection_name(task_id)
        if collection in await mongodb.db.list_collection_names():
            await mongodb.db[collection].delete_many({})
            print(f"🧹 Cleaned collection {collection}")
        
        # SCENARIO 1: First submission - all new findings
        print("\n📋 SCENARIO 1: First submission - all new findings")
        
        # Create test findings
        findings_batch1 = [
            Finding(
                title="Reentrancy Vulnerability in Withdraw Function",
                description="The withdraw() function does not follow the checks-effects-interactions pattern and is vulnerable to reentrancy attacks, potentially allowing attackers to drain funds from the contract.",
                file_path="contracts/Contract.sol",
                severity=Severity.HIGH
            ),
            Finding(
                title="Unsafe External Call without Return Value Check",
                description="The contract makes external calls without checking return values, which could lead to silent failures and unintended consequences in the contract's execution flow.",
                file_path="contracts/Contract.sol",
                severity=Severity.MEDIUM
            )
        ]
        
        # Create FindingInput
        input_batch1 = FindingInput(
            task_id=task_id,
            findings=findings_batch1
        )
        
        # Process findings via API
        print("\n📊 Processing first batch of findings")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                # Process findings
                print(f"Sending POST request to {BASE_URL}/process_findings")
                print(f"Request data: {input_batch1}")
                
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    json=input_batch1.model_dump()
                )
                
                # Check response
                print(f"Response status code: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ First batch processed successfully")
                    
                    # Verify deduplication results
                    dedup_results = result.get("deduplication", {})
                    assert dedup_results.get("total", 0) == 2, "Should have 2 total findings"
                    assert dedup_results.get("new", 0) == 2, "Should have 2 new findings"
                    assert dedup_results.get("duplicates", 0) == 0, "Should have 0 duplicates"
                    
                    print(f"  Total findings: {dedup_results.get('total', 'N/A')}")
                    print(f"  New findings: {dedup_results.get('new', 'N/A')}")
                    
                    # Verify auto evaluation results
                    auto_eval = result.get("auto_evaluation", {})
                    if auto_eval:
                        print(f"\n✅ Automatic evaluation completed")
                        print(f"  Total pending: {auto_eval.get('total_pending', 'N/A')}")
                        print(f"  Evaluated as valid: {auto_eval.get('evaluated_as_valid', 'N/A')}")
                        print(f"  Evaluated as disputed: {auto_eval.get('evaluated_as_disputed', 'N/A')}")
                        
                        # Print each evaluation
                        print("\n📋 Evaluation results:")
                        for eval_entry in auto_eval.get('evaluations', []):
                            print(f"  {eval_entry['title']}:")
                            print(f"    Status: {eval_entry['status']}")
                            if 'evaluated_severity' in eval_entry:
                                print(f"    Severity: {eval_entry.get('evaluated_severity', 'N/A')}")
                            if 'category' in eval_entry:
                                print(f"    Category: {eval_entry.get('category', 'N/A')}")
                            print(f"    Comment: {eval_entry['evaluation_comment'][:50]}..." if len(eval_entry['evaluation_comment']) > 50 else f"    Comment: {eval_entry['evaluation_comment']}")
                    else:
                        print(f"❌ Automatic evaluation not performed: {auto_eval.get('message', 'No reason provided')}")
                else:
                    print(f"❌ Failed to process first batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except httpx.RequestError as exc:
            print(f"❌ HTTP Request failed: {exc!r}")
            print(f"Request details: {exc.request.url} - {exc.request.method}")
            return
        except Exception as e:
            print(f"❌ Unexpected error during first batch: {str(e)}")
            traceback.print_exc()
            return
                
        # SCENARIO 2: Second submission - mix of duplicate and new findings
        print("\n📋 SCENARIO 2: Second submission - mix of duplicate and new findings")
        
        # Create findings with one duplicate and one new
        findings_batch2 = [
            Finding(
                title="Reentrancy Vulnerability in Withdraw Function",  # Duplicate title
                description="The withdraw function is susceptible to reentrancy attacks due to state changes after external calls.",
                severity=Severity.HIGH,
                file_path="contracts/Contract.sol"
            ),
            Finding(
                title="Integer Overflow in Token Transfer",  # New finding
                description="The token transfer function doesn't use SafeMath or Solidity 0.8+ built-in overflow checks, potentially allowing attackers to manipulate balances.",
                severity=Severity.HIGH,
                file_path="contracts/Contract.sol"
            )
        ]
        
        # Create FindingInput
        input_batch2 = FindingInput(
            task_id=task_id,
            findings=findings_batch2
        )
        
        # Process findings via API
        print("\n📊 Processing second batch of findings")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                # Process findings
                print(f"Sending POST request to {BASE_URL}/process_findings")
                print(f"Request data: {input_batch2}")
                
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    json=input_batch2.model_dump()
                )
                
                # Check response
                print(f"Response status code: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ Second batch processed successfully")
                    
                    # Verify deduplication results
                    dedup_results = result.get("deduplication", {})
                    assert dedup_results.get("total", 0) == 2, "Should have 2 total findings"
                    assert dedup_results.get("new", 0) == 1, "Should have 1 new finding"
                    assert dedup_results.get("duplicates", 0) == 1, "Should have 1 duplicate"
                    
                    print(f"  Total findings: {dedup_results.get('total', 'N/A')}")
                    print(f"  New findings: {dedup_results.get('new', 'N/A')}")
                    print(f"  Duplicates: {dedup_results.get('duplicates', 'N/A')}")
                    print(f"  Duplicate titles: {dedup_results.get('duplicate_titles', 'N/A')}")
                    
                    # Verify auto evaluation results (should only evaluate the 1 new finding)
                    auto_eval = result.get("auto_evaluation", {})
                    if auto_eval:
                        print(f"\n✅ Automatic evaluation completed")
                        assert auto_eval.get("total_pending", 0) == 1, "Should evaluate exactly 1 new finding"
                        
                        print(f"  Total pending: {auto_eval.get('total_pending', 'N/A')}")
                        print(f"  Evaluated as valid: {auto_eval.get('evaluated_as_valid', 'N/A')}")
                        print(f"  Evaluated as disputed: {auto_eval.get('evaluated_as_disputed', 'N/A')}")
                    else:
                        print(f"❌ Automatic evaluation not performed: {auto_eval.get('message', 'No reason provided')}")
                else:
                    print(f"❌ Failed to process second batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except httpx.RequestError as exc:
            print(f"❌ HTTP Request failed: {exc!r}")
            print(f"Request details: {exc.request.url} - {exc.request.method}")
            return
        except Exception as e:
            print(f"❌ Error occurred: {str(e)}")
            print("Detailed error information:")
            traceback.print_exc()
            return
                
        # SCENARIO 3: Findings from a different agent
        print("\n📋 SCENARIO 3: Findings from a different agent")
        
        # Create findings for a different agent with similar content
        findings_batch3 = [
            Finding(
                title="Withdraw Function Reentrancy Issue",  # Similar to existing but different title
                description="I found a reentrancy vulnerability in the withdraw function that allows attackers to repeatedly withdraw funds.",
                severity=Severity.MEDIUM,  # Different severity
                file_path="contracts/Contract.sol"
            ),
            Finding(
                title="SQL Injection in Contract Data Storage",  # New finding that should be disputed
                description="The smart contract is vulnerable to SQL injection attacks when storing user input in its database, potentially allowing attackers to execute arbitrary SQL commands.",
                severity=Severity.HIGH,
                file_path="contracts/Contract.sol"
            )
        ]
        
        # Create FindingInput
        input_batch3 = FindingInput(
            task_id=task_id,
            findings=findings_batch3
        )
        
        # Process findings via API
        print("\n📊 Processing third batch of findings (different agent)")
        
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                # Process findings
                print(f"Sending POST request to {BASE_URL}/process_findings")
                print(f"Request data: {input_batch3}")
                
                response = await client.post(
                    f"{BASE_URL}/process_findings",
                    json=input_batch3.model_dump()
                )
                
                # Check response
                print(f"Response status code: {response.status_code}")
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ Third batch processed successfully")
                    
                    # Verify deduplication results
                    dedup_results = result.get("deduplication", {})
                    
                    print(f"  Total findings: {dedup_results.get('total', 'N/A')}")
                    print(f"  New findings: {dedup_results.get('new', 'N/A')}")
                    print(f"  Duplicates: {dedup_results.get('duplicates', 'N/A')}")
                    
                    # Verify auto evaluation results
                    auto_eval = result.get("auto_evaluation", {})
                    if auto_eval:
                        print(f"\n✅ Automatic evaluation completed")
                        print(f"  Total pending: {auto_eval.get('total_pending', 'N/A')}")
                        print(f"  Evaluated as valid: {auto_eval.get('evaluated_as_valid', 'N/A')}")
                        print(f"  Evaluated as disputed: {auto_eval.get('evaluated_as_disputed', 'N/A')}")
                    else:
                        print(f"❌ Automatic evaluation not performed: {auto_eval.get('message', 'No reason provided')}")
                else:
                    print(f"❌ Failed to process third batch: {response.status_code}")
                    print(f"Response text: {response.text}")
                    return
        except httpx.RequestError as exc:
            print(f"❌ HTTP Request failed: {exc!r}")
            print(f"Request details: {exc.request.url} - {exc.request.method}")
            return
        except Exception as e:
            print(f"❌ Error occurred: {str(e)}")
            print("Detailed error information:")
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
                severity_counts = {}
                
                for finding in findings:
                    # Count by status
                    status = finding['status']
                    status_counts[status] = status_counts.get(status, 0) + 1
                    
                    # Count by agent
                    agent = finding['agent_id']
                    agent_counts[agent] = agent_counts.get(agent, 0) + 1
                    
                    # Count by severity (for valid findings)
                    if status in ['unique_valid', 'similar_valid']:
                        severity = finding.get('evaluated_severity')
                        if severity:
                            severity_counts[severity] = severity_counts.get(severity, 0) + 1
                
                print(f"  Status distribution: {status_counts}")
                print(f"  Agent distribution: {agent_counts}")
                print(f"  Severity distribution (valid findings): {severity_counts}")
                
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
        print("Detailed error information:")
        traceback.print_exc()
    finally:
        await mongodb.close()
        print("\n✅ Test completed")

if __name__ == "__main__":
    asyncio.run(test_process_findings()) 