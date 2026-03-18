import logging
from typing import Optional, Dict, Any, List
from app.types import TaskCache
from app.models.finding_db import FindingDB
from app.config import config
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from app.core.prompt_utils import build_context_section


logger = logging.getLogger(__name__)

"""
Gemini model configuration and initialization module.
Provides functions to create and configure Gemini models with parameters from environment variables.
"""

class DuplicateFinding(BaseModel):
    """Single duplicate finding relationship."""
    findingId: str = Field(description="ID of the finding that is a duplicate", min_length=1)
    duplicateOf: str = Field(description="ID of the original finding", min_length=1)
    explanation: str = Field(description="Explanation of why the finding is a duplicate")

class DeduplicationResult(BaseModel):
    """Result of deduplication analysis."""
    results: List[DuplicateFinding] = Field(description="List of duplicate relationships")

def get_gemini_config() -> Dict[str, Any]:
    """
    Get Gemini model configuration from environment variables.
    
    Returns:
        Dictionary with model configuration parameters
    """
    return {
        "model": config.gemini_model,
        "temperature": config.gemini_temperature,
        "max_output_tokens": config.gemini_max_tokens,
        "google_api_key": config.gemini_api_key
    }

def create_gemini_model(
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None
) -> ChatGoogleGenerativeAI:
    """
    Create a Gemini model instance with specified parameters or from environment variables.
    
    Args:
        model_name: Optional model name override
        temperature: Optional temperature override
        max_tokens: Optional max tokens override
        api_key: Optional API key override
        
    Returns:
        Configured ChatGoogleGenerativeAI instance
        
    Raises:
        ValueError: If API key is not provided and not in environment variables
    """
    # Get config from environment variables
    gemini_config = get_gemini_config()
    
    # Override with any provided parameters
    if model_name:
        gemini_config["model"] = model_name
    if temperature is not None:
        gemini_config["temperature"] = temperature
    if max_tokens:
        gemini_config["max_output_tokens"] = max_tokens
    if api_key:
        gemini_config["google_api_key"] = api_key
        
    # Validate API key
    if not gemini_config["google_api_key"]:
        raise ValueError("GEMINI_API_KEY environment variable is not set")
    
    # Return configured model
    return ChatGoogleGenerativeAI(
        model=gemini_config["model"],
        temperature=gemini_config["temperature"],
        max_output_tokens=gemini_config["max_output_tokens"],
        api_key=gemini_config["google_api_key"]
    )

def create_structured_deduplication_model(
    model: Optional[ChatGoogleGenerativeAI] = None
) -> any:
    """
    Create a Gemini model with structured output for deduplication.
    
    Args:
        model: Optional ChatGoogleGenerativeAI model (created from environment if not provided)
        
    Returns:
        Configured model with structured output for deduplication
    """
    if not model:
        model = create_gemini_model()
    
    return model.with_structured_output(DeduplicationResult)

async def find_duplicates_structured(
    model_with_structured_output: any,
    findings: List[FindingDB],
    task_cache: TaskCache
) -> DeduplicationResult:
    """
    Find duplicate findings using structured output to ensure JSON format.
    Uses a comprehensive prompt that combines detailed analysis with structured output.
    
    Args:
        model_with_structured_output: Model configured with structured output
        findings: List of findings to analyze for duplicates
        task_context: Task context containing smart contract files and documentation
        
    Returns:
        Structured deduplication result with guaranteed JSON format
    """
    
    # Build context section
    context_section = build_context_section(task_cache)
    
    prompt = f"""
You are a security expert with deep expertise in Solidity smart contract vulnerabilities, tasked with identifying duplicate findings among security vulnerability reports.

## TASK:
1. **Group duplicate findings** - Identify vulnerabilities that describe the same underlying security issue affecting the same function and code section
2. **Select the best finding in each group** - For each group of duplicates, choose the highest quality finding as the original
3. **Provide explanation** - Explain why each finding is considered a duplicate of the original

## DUPLICATE IDENTIFICATION - Four-Step Validation:
1. **Same contract file**: Do findings reference the same contract?
2. **Same function**: Do findings reference the exact same function name?
3. **Same code section**: Do findings reference the same or overlapping code lines?
4. **Same root cause**: Is the underlying vulnerability mechanism identical?

✅ **THESE ARE DUPLICATES:**
- Same vulnerability type and attack vector in the exact same function with different descriptions
- Identical root cause affecting the exact same code lines with different wording

❌ **THESE ARE NOT DUPLICATES:**
- Similar vulnerability types in different functions (e.g., reentrancy in different functions)
- Similar descriptions but different contract files or code sections
- Different root causes, attack vectors, or consequences despite similar terminology

## CRITICAL RULES TO PREVENT ERRORS:

🚫 **NO CIRCULAR RELATIONSHIPS:**
- Each duplicate group has exactly ONE original that others point to
- If Finding A is duplicate of Finding B, then Finding B CANNOT be duplicate of Finding A
- No finding can appear as both original and duplicate

🚫 **NO OVER-GROUPING:**
- Findings affecting different functions or code sectionsare never duplicates, even if vulnerability types are similar
- Example: "Missing access control in withdraw()" vs "Missing access control in deposit()" = NOT duplicates
- Example: "Reentrancy in claimReward()" vs "Reentrancy in processRefund()" = NOT duplicates

## QUALITY CRITERIA for selecting the original:
- **Technical accuracy**: Most precise vulnerability description
- **Completeness**: Contains comprehensive details about the issue
- **Clarity**: Easy to understand and identify
- **Evidence**: Better code references, examples, or proof-of-concept

## OUTPUT REQUIREMENTS:
- **For each duplicate**: Include findingId, duplicateOf, and detailed explanation
- **Only duplicates in results**: Don't include the original findings in the results
- **Empty list if no duplicates**: Return [] if no duplicate relationships exist
- **Clear explanations**: Each explanation should be 2-3 sentences describing why it's a duplicate
- **Use exact Finding IDs**: Make sure to use the exact Finding ID from the analysis

## RETURN FORMAT
Return a JSON object with the following structure:
```json
{{
    "results": [
        {{
            "findingId": "Finding ID that is a duplicate",
            "duplicateOf": "Original Finding ID",
            "explanation": "Explanation including specific code section match and reason why it's a duplicate"
        }}
    ]
}}
```

## SMART CONTRACT CONTEXT
{context_section}

## FINDINGS TO ANALYZE
{[finding.dump() for finding in findings]}

Analyze systematically: group similar findings, examine each vulnerability against the smart contract context above, compare affected functions, code sections and root causes, and rank quality within duplicate groups. Be conservative - only mark findings as duplicates if you're confident they describe the same underlying security vulnerability in the same function and code section.
"""

    return await model_with_structured_output.ainvoke(prompt)
