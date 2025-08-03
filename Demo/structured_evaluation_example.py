#!/usr/bin/env python3
"""
Example usage of structured output evaluation for security findings.
This demonstrates how findings are batched and evaluated together when they refer to the same vulnerability.
"""

import asyncio
from datetime import datetime, timezone
from app.core.evaluation import FindingEvaluator
from app.models.finding_db import FindingDB, Status, Severity
from app.models.finding_db import EvaluatedSeverity


async def example_structured_evaluation():
    """Example of using structured output for batch evaluation of related findings."""
    
    # Create sample findings that refer to the same vulnerability (reentrancy)
    sample_findings = [
        FindingDB(
            id="F001",
            title="Reentrancy vulnerability in withdraw function",
            description="The withdraw function makes external calls before updating user balances, allowing for reentrancy attacks.",
            severity=Severity.HIGH,
            file_paths=["contracts/Vault.sol"],
            agent_id="agent_alpha",
            status=Status.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        ),
        FindingDB(
            id="F002", 
            title="External call before state update in withdrawal",
            description="The withdrawal method calls external contracts before reducing the user's balance, creating reentrancy risk.",
            severity=Severity.HIGH,
            file_paths=["contracts/Vault.sol"],
            agent_id="agent_beta",
            status=Status.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        ),
        FindingDB(
            id="F003",
            title="Reentrancy attack possible",
            description="Vulnerable to reentrancy due to external call pattern in withdraw function at line 42.",
            severity=Severity.CRITICAL,
            file_paths=["contracts/Vault.sol"],
            agent_id="agent_gamma",
            status=Status.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
    ]
    
    # Define duplicate relationships (F002 and F003 are duplicates of F001)
    duplicate_relationships = [
        {
            "findingId": "F002",
            "duplicateOf": "F001",
            "explanation": "Both describe the same reentrancy vulnerability in the withdraw function"
        },
        {
            "findingId": "F003", 
            "duplicateOf": "F001",
            "explanation": "Same reentrancy issue, just with more specific line reference"
        }
    ]
    
    # Initialize the evaluator
    evaluator = FindingEvaluator(batch_size=10)
    
    print("🔍 Structured Output Evaluation Example\n")
    print("=== Sample Findings ===")
    for finding in sample_findings:
        print(f"• {finding.title} (Agent: {finding.agent_id})")
    
    print(f"\n=== Duplicate Relationships ===")
    for rel in duplicate_relationships:
        print(f"• {rel['findingId']} is duplicate of {rel['duplicateOf']}")
    
    # Group findings for evaluation
    finding_groups = evaluator.group_findings_for_evaluation(sample_findings, duplicate_relationships)
    
    print(f"\n=== Evaluation Batches ===")
    for i, group in enumerate(finding_groups, 1):
        print(f"Batch {i}: {len(group)} findings")
        for finding in group:
            print(f"  - {finding.title}")
    
    print(f"\n=== Batch Evaluation Results ===")
    
    # Show how the new apply_evaluation_results works
    print(f"\n=== Apply Evaluation Results (New Method) ===")
    print("✨ Key improvements:")
    print("• All related findings get the same status")
    print("• No more cross-comparison dependency") 
    print("• Direct database updates")
    print("• No category handling needed")
    
    # Simulate some evaluation results
    sample_evaluation_results = [
        {
            "finding_id": "F001",
            "is_valid": True,
            "evaluated_severity": EvaluatedSeverity.HIGH,
            "evaluation_comment": "Valid reentrancy vulnerability with high impact"
        }
    ]
    
    print(f"\n=== Status Propagation Example ===")
    print("If F001 is evaluated as VALID with HIGH severity:")
    print("• F001 (original) → Status.UNIQUE_VALID, EvaluatedSeverity.HIGH")
    print("• F002 (duplicate) → Status.UNIQUE_VALID, EvaluatedSeverity.HIGH") 
    print("• F003 (duplicate) → Status.UNIQUE_VALID, EvaluatedSeverity.HIGH")
    print("All get the same evaluation comment and severity!")
    
    # Evaluate each batch (this would normally call the LLM)
    for i, batch in enumerate(finding_groups, 1):
        print(f"\n--- Batch {i} Structure ---")
        
        try:
            # This would normally call the LLM and then apply results
            print(f"Batch contains {len(batch)} findings:")
            for finding in batch:
                print(f"  - {finding.id}: {finding.title}")
            
            print("✅ All findings in this batch will get consistent evaluation results")
                
        except Exception as e:
            print(f"❌ Evaluation failed: {e}")
            print("This is expected in demo mode without proper API keys")


async def demonstrate_key_features():
    """Demonstrate the key features of the new structured evaluation system."""
    
    print("\n🚀 Key Features of Structured Output Evaluation:")
    print("\n1. **Batch Grouping**: Findings that refer to the same vulnerability are evaluated together")
    print("   - Original finding + all its duplicates in one batch")
    print("   - Claude can see all related information to make the best assessment")
    
    print("\n2. **Structured Output**: No more JSON parsing errors")
    print("   - Guaranteed valid response format using Pydantic models")
    print("   - Automatic validation and type conversion")
    
    print("\n3. **Group-Aware Evaluation**: Claude understands context")
    print("   - Evaluates findings as a group referring to the same vulnerability")
    print("   - Consistent validity and severity across related findings")
    print("   - Better false positive detection using combined information")
    
    print("\n4. **Enhanced Batching Logic**:")
    print("   - Related findings (duplicates) stay together regardless of batch size")
    print("   - Remaining findings are batched normally")
    print("   - Each batch focuses on coherent vulnerability groups")


if __name__ == "__main__":
    print("🔐 Security Finding Evaluation with Structured Output\n")
    
    # Run the demonstration
    asyncio.run(demonstrate_key_features())
    print("\n" + "="*60)
    
    # Note: The actual evaluation requires API keys and would contact Claude
    print("\n⚠️  Note: To run the full evaluation example, ensure you have:")
    print("   - CLAUDE_API_KEY environment variable set")
    print("   - MongoDB connection configured") 
    print("   - All dependencies installed")
    
    print("\n💡 The example above shows the structure and batching logic.")
    print("   In production, Claude would provide detailed security assessments.") 