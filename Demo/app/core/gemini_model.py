from typing import Optional, Dict, Any, List
from app.models.finding_db import FindingDB
from app.config import config
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field

"""
Gemini model configuration and initialization module.
Provides functions to create and configure Gemini models with parameters from environment variables.
"""

class DuplicateFinding(BaseModel):
    """Single duplicate finding relationship."""
    findingId: str = Field(description="ID of the finding that is a duplicate")
    duplicateOf: str = Field(description="ID of the original finding")
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
        google_api_key=gemini_config["google_api_key"]
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

def find_duplicates_structured(
    model_with_structured_output: any,
    findings: List[FindingDB]
) -> DeduplicationResult:
    """
    Find duplicate findings using structured output to ensure JSON format.
    Uses a comprehensive prompt that combines detailed analysis with structured output.
    
    Args:
        model_with_structured_output: Model configured with structured output
        findings_list: String containing all findings to analyze for duplicates
        
    Returns:
        Structured deduplication result with guaranteed JSON format
    """
    prompt = f"""
    You are a security expert with deep expertise in Solidity smart contract vulnerabilities, tasked with identifying duplicate findings among security vulnerability reports.

    ## TASK:
    1. **Identify duplicate findings** - Find vulnerabilities that describe the same underlying security issue
    2. **Select the highest quality original** - For each group of duplicates, choose the best finding as the original
    3. **Provide detailed explanations** - Explain why each finding is considered a duplicate

    ## QUALITY CRITERIA for selecting the original:
    - **Technical accuracy**: Most precise vulnerability description
    - **Completeness**: Contains comprehensive details about the issue
    - **Evidence**: Better code references, examples, or proof-of-concept

    ## DUPLICATE IDENTIFICATION RULES:
    
    ✅ **THESE ARE DUPLICATES:**
    - Same underlying issue affecting the same function or code section
    - Identical root cause with different descriptions
    - Same security risk and attack vector with different wording
    
    ❌ **THESE ARE NOT DUPLICATES:**
    - Similar issue affecting different functions or code sections
    - Different root causes, even if descriptions are similar
    - Different security risks or consequences that happen to use similar terminology

    ## OUTPUT REQUIREMENTS:
    - **For each duplicate**: Include findingId, duplicateOf, and detailed explanation
    - **Only duplicates in results**: Don't include the original findings
    - **Empty list if no duplicates**: Return [] if no duplicate relationships exist
    - **Clear explanations**: Each explanation should be 2-3 sentences describing why it's a duplicate
    - **Use exact Finding IDs**: Make sure to use the exact Finding ID from the analysis

    ## EXAMPLE DUPLICATE SCENARIOS:
    - **Reentrancy**: Multiple findings about external calls before state updates in the same function
    - **Access Control**: Different descriptions of the same missing authorization check
    - **Integer Overflow**: Various reports about the same arithmetic operation vulnerability

    ## FINDINGS TO ANALYZE:

    {[finding.model_dump() for finding in findings]}

    Analyze systematically: group similar findings, examine the actual vulnerability, compare root causes, and rank quality within duplicate groups. Be conservative - only mark findings as duplicates if you're confident they describe the same underlying security vulnerability.
    """
    
    return model_with_structured_output.invoke(prompt)
