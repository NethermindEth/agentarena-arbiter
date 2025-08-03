#!/usr/bin/env python3
"""
Example usage of structured output with Claude and Gemini models.
This demonstrates how to get guaranteed JSON output from both models.
"""

from app.core.claude_model import (
    create_structured_similarity_model,
    compare_findings_structured,
    SimilarityResult,
    SimilarityAnalysis
)
from app.core.gemini_model import (
    create_structured_deduplication_model,
    find_duplicates_structured,
    DuplicateFinding
)


def example_similarity_comparison():
    """Example of using structured output for similarity comparison with Claude."""
    
    # Sample findings
    finding1 = """
    Title: Reentrancy vulnerability in withdraw function
    Description: The withdraw function allows external calls before updating balances
    File: contracts/Vault.sol:45
    """
    
    finding2 = """
    Title: Reentrancy attack possible in withdrawal
    Description: External call made before balance reduction in withdraw method
    File: contracts/Vault.sol:47
    """
    
    # Create structured output model
    similarity_model = create_structured_similarity_model(use_detailed_analysis=False)
    
    # Get structured result - guaranteed to be valid JSON/Pydantic object
    result: SimilarityResult = compare_findings_structured(
        similarity_model, finding1, finding2
    )
    
    print("=== Similarity Comparison (Simple) ===")
    print(f"Similarity Score: {result.similarity_score}")
    print(f"Explanation: {result.explanation}")
    print(f"Result as JSON: {result.model_dump_json(indent=2)}")
    
    # Example with detailed analysis
    detailed_model = create_structured_similarity_model(use_detailed_analysis=True)
    detailed_result: SimilarityAnalysis = compare_findings_structured(
        detailed_model, finding1, finding2, use_detailed_analysis=True
    )
    
    print("\n=== Similarity Comparison (Detailed) ===")
    print(f"Title Similarity: {detailed_result.title_similarity}")
    print(f"Description Similarity: {detailed_result.description_similarity}")
    print(f"File Reference Similarity: {detailed_result.file_reference_similarity}")
    print(f"Overall Similarity: {detailed_result.overall_similarity}")
    print(f"Explanation: {detailed_result.explanation}")


def example_deduplication():
    """Example of using structured output for deduplication with Gemini."""
    
    # Create sample FindingDB objects instead of string
    from app.models.finding_db import FindingDB, Status
    from app.models.finding_input import Severity
    from datetime import datetime, timezone
    
    sample_findings = [
        FindingDB(
            id="F001",
            title="Reentrancy in withdraw function",
            description="External call before balance update",
            severity=Severity.HIGH,
            file_paths=["contracts/Vault.sol"],
            agent_id="agent_alice",
            status=Status.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        ),
        FindingDB(
            id="F002",
            title="Missing access control",
            description="Function lacks proper authorization",
            severity=Severity.MEDIUM,
            file_paths=["contracts/Access.sol"],
            agent_id="agent_bob",
            status=Status.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        ),
        FindingDB(
            id="F003",
            title="Reentrancy vulnerability in withdraw",
            description="Balance not updated before external call",
            severity=Severity.HIGH,
            file_paths=["contracts/Vault.sol"],
            agent_id="agent_charlie",
            status=Status.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        ),
        FindingDB(
            id="F004",
            title="Integer overflow in calculation",
            description="Arithmetic operation can overflow",
            severity=Severity.LOW,
            file_paths=["contracts/Math.sol"],
            agent_id="agent_dave",
            status=Status.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
    ]
    
    # Create structured output model
    dedup_model = create_structured_deduplication_model()
    
    # Get structured result - guaranteed to be valid JSON/Pydantic object
    result: List[DuplicateFinding] = find_duplicates_structured(
        dedup_model, sample_findings
    )
    
    print("\n=== Deduplication Analysis ===")
    print(f"Number of duplicates found: {len(result)}")
    
    if result:
        for duplicate in result:
            print(f"Finding {duplicate.findingId} is a duplicate of {duplicate.duplicateOf}")
            print(f"  Explanation: {duplicate.explanation}")
            print()
    else:
        print("No duplicates found!")
    
    # Convert to JSON for demonstration
    result_dicts = [dup.model_dump() for dup in result]
    import json
    print(f"Result as JSON: {json.dumps(result_dicts, indent=2)}")


def example_error_handling():
    """Example of how structured output handles validation errors."""
    
    try:
        # This would work even if the model returns slightly malformed output
        # because Pydantic will validate and coerce the data
        similarity_model = create_structured_similarity_model()
        
        result = compare_findings_structured(
            similarity_model,
            "Simple finding A",
            "Simple finding B"
        )
        
        # The result is guaranteed to have the expected structure
        assert isinstance(result.similarity_score, float)
        assert 0.0 <= result.similarity_score <= 1.0
        assert isinstance(result.explanation, str)
        
        print("\n=== Error Handling Demo ===")
        print("✅ Structured output provides automatic validation!")
        print(f"Score: {result.similarity_score}")
        
    except Exception as e:
        print(f"❌ Validation failed: {e}")


if __name__ == "__main__":
    print("🚀 Structured Output Examples\n")
    
    try:
        example_similarity_comparison()
        example_deduplication()
        example_error_handling()
    except Exception as e:
        print(f"Error running examples: {e}")
        print("Make sure your API keys are configured in the environment!") 