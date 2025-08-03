from typing import Optional, Dict, Any, List
from app.models.finding_db import FindingDB
from app.config import config
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
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

def create_deduplication_chain(
    model: Optional[ChatGoogleGenerativeAI] = None,
    prompt_template: Optional[str] = None
) -> LLMChain:
    """
    Create a LangChain for finding deduplication using Gemini.
    
    Args:
        model: Optional ChatGoogleGenerativeAI model (created from environment if not provided)
        prompt_template: Optional custom prompt template
        
    Returns:
        Configured LLMChain for deduplication
    """
    # Create model if not provided
    if not model:
        model = create_gemini_model()
    
    # Use default deduplication prompt if not provided
    if not prompt_template:
        prompt_template = """
        You are a security expert tasked with identifying duplicate findings among smart contract security vulnerabilities.

        Your task:
        1. Identify which findings are duplicates of each other
        2. For each group of duplicates, select the highest quality finding as the original
        3. Return ONLY the duplicate relationships, not the originals

        Quality criteria for selecting the original (highest quality):
        - Most detailed and clear description
        - Better technical explanation
        - More complete information
        - More accurate severity assessment

        Return your result as a JSON array of objects with this exact format:
        [
          {"findingId": "DUPLICATE_FINDING_ID", "duplicateOf": "ORIGINAL_FINDING_ID"},
          {"findingId": "ANOTHER_DUPLICATE_ID", "duplicateOf": "ORIGINAL_FINDING_ID"}
        ]

        Important rules:
        - Only include findings that are duplicates (the originals should NOT appear in the list)
        - Two findings are duplicates if they describe the same underlying security vulnerability
        - Findings with similar descriptions but different vulnerabilities are NOT duplicates
        - If no duplicates are found, return an empty array: []
        - Ensure the JSON is valid and properly formatted

        Below are all the pending findings that need to be analyzed for duplicates:

        {findings_list}
        """
    
    # Create prompt
    prompt = PromptTemplate(
        input_variables=["findings_list"],
        template=prompt_template
    )
    
    # Create and return chain
    return LLMChain(llm=model, prompt=prompt, output_key="deduplication_result") 

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
    # Create model if not provided
    if not model:
        model = create_gemini_model()
    
    # Create structured output model
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
    You are a security expert with deep expertise in smart contract vulnerabilities, tasked with identifying duplicate findings among security vulnerability reports.

    ## COMPREHENSIVE ANALYSIS TASK:
    
    ### Your Mission:
    1. **Identify duplicate findings** - Find reports that describe the same underlying security vulnerability
    2. **Select the highest quality original** - For each group of duplicates, choose the best report as the original
    3. **Provide detailed explanations** - Explain why each finding is considered a duplicate
    4. **Return structured results** - Output in the exact specified format

    ### QUALITY CRITERIA for selecting the original (highest quality finding):
    - **Technical accuracy**: Most precise vulnerability description
    - **Completeness**: Contains comprehensive details about the issue
    - **Clarity**: Clear, well-structured explanation
    - **Evidence**: Better code references, examples, or proof-of-concept
    - **Severity assessment**: More accurate risk evaluation
    - **Remediation guidance**: Includes fix suggestions

    ### CRITICAL RULES for duplicate identification:
    
    ✅ **THESE ARE DUPLICATES:**
    - Same vulnerability type in the same function/contract
    - Identical root cause with different descriptions
    - Same security risk with different wording
    - Reports pointing to the same code issue from different angles
    
    ❌ **THESE ARE NOT DUPLICATES:**
    - Similar vulnerability types in different locations
    - Different root causes even if descriptions seem similar
    - Same vulnerability pattern but affecting different contracts/functions
    - Different security risks that happen to use similar terminology
    
    ### ANALYSIS PROCESS:
    1. **Group similar findings** - Look for reports that might be related
    2. **Deep technical analysis** - Examine the actual vulnerability being reported
    3. **Root cause comparison** - Determine if they share the same underlying issue
    4. **Quality assessment** - Rank findings within each duplicate group
    5. **Explanation crafting** - Provide clear reasoning for duplicate relationships

    ### OUTPUT REQUIREMENTS:
    - **For each duplicate**: Include findingId, duplicateOf, and detailed explanation
    - **No originals in the list**: Only include the duplicates, not the original findings
    - **Empty list if no duplicates**: Return [] if no duplicate relationships exist
    - **Clear explanations**: Each explanation should be 2-3 sentences describing why it's a duplicate
    - **Use Finding IDs**: Make sure to use the exact Finding ID from the analysis

    ### EXAMPLE DUPLICATE SCENARIOS:
    - **Reentrancy**: Multiple reports about external calls before state updates in the same function
    - **Access Control**: Different descriptions of the same missing authorization check
    - **Integer Overflow**: Various reports about the same arithmetic operation vulnerability
    - **Logic Errors**: Different explanations of the same flawed business logic

    ---

    ## FINDINGS TO ANALYZE:

    {[finding.model_dump() for finding in findings]}

    ---

    ## ANALYSIS INSTRUCTIONS:
    
    Please analyze the above findings systematically:
    
    1. **First Pass**: Read through all findings to understand the scope
    2. **Grouping**: Identify potential duplicate groups based on vulnerability type and location
    3. **Technical Analysis**: For each group, determine if they describe the same underlying issue
    4. **Quality Ranking**: Within duplicate groups, select the highest quality finding as the original
    5. **Explanation**: Craft clear explanations for why findings are duplicates
    
    Remember: Be conservative - only mark findings as duplicates if you're confident they describe the same underlying security vulnerability. When in doubt, treat them as separate findings.
    """
    
    return model_with_structured_output.invoke(prompt) 