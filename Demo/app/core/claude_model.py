from typing import Optional, Dict, Any, List
from app.config import config
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field


class FindingEvaluation(BaseModel):
    """Single finding evaluation result."""
    finding_id: str = Field(description="ID/title of the evaluated finding")
    is_valid: bool = Field(description="Whether the finding represents a valid security issue")
    severity: str = Field(description="Severity level: low, medium, high, or critical")
    comment: str = Field(description="Brief explanation of the evaluation (2-3 sentences maximum)")

class EvaluationResult(BaseModel):
    """Evaluation result."""
    results: List[FindingEvaluation] = Field(description="List of evaluation results")

def get_model_config() -> Dict[str, Any]:
    """
    Get Claude model configuration from environment variables.
    
    Returns:
        Dictionary with model configuration parameters
    """
    return {
        "model_name": config.claude_model,
        "temperature": config.claude_temperature,
        "max_tokens": config.claude_max_tokens,
        "anthropic_api_key": config.claude_api_key
    }

def create_claude_model(
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None
) -> ChatAnthropic:
    """
    Create a Claude model instance with specified parameters or from environment variables.
    
    Args:
        model_name: Optional model name override
        temperature: Optional temperature override
        max_tokens: Optional max tokens override
        api_key: Optional API key override
        
    Returns:
        Configured ChatAnthropic instance
        
    Raises:
        ValueError: If API key is not provided and not in environment variables
    """
    # Get config from environment variables
    claude_config = get_model_config()
    
    # Override with any provided parameters
    if model_name:
        claude_config["model_name"] = config.claude_model
    if temperature is not None:
        claude_config["temperature"] = temperature
    if max_tokens:
        claude_config["max_tokens"] = max_tokens
    if api_key:
        claude_config["anthropic_api_key"] = api_key
        
    # Validate API key
    if not claude_config["anthropic_api_key"]:
        raise ValueError("CLAUDE_API_KEY environment variable is not set")
    
    # Return configured model
    return ChatAnthropic(
        model=claude_config["model_name"],
        temperature=claude_config["temperature"],
        max_tokens_to_sample=claude_config["max_tokens"],
        anthropic_api_key=claude_config["anthropic_api_key"]
    )

def create_structured_evaluation_model(
    model: Optional[ChatAnthropic] = None
) -> any:
    """
    Create a Claude model with structured output for finding evaluation.
    
    Args:
        model: Optional ChatAnthropic model (created from environment if not provided)
        
    Returns:
        Configured model with structured output for batch evaluation
    """
    # Create model if not provided
    if not model:
        model = create_claude_model()
    
    # Create structured output model
    return model.with_structured_output(EvaluationResult)

async def evaluate_findings_structured(
    model_with_structured_output: any,
    findings_batch: List[any]
) -> EvaluationResult:
    """
    Evaluate a batch of related findings using structured output to ensure proper format.
    
    Args:
        model_with_structured_output: Model configured with structured output
        findings_batch: List of findings to evaluate (may be related to same vulnerability or unique)
        
    Returns:
        Structured evaluation result with guaranteed format
    """

    prompt = f"""
    You are a blockchain security expert tasked with evaluating the validity and severity of smart contract vulnerabilities.
    
    ## BATCH CONTEXT
    You will receive a batch of findings to evaluate. This batch may contain:
    - **Related findings**: Multiple findings that refer to the same underlying vulnerability (duplicates)
    - **Single unique finding**: One finding that doesn't have duplicates
    
    ## EVALUATION CRITERIA
    
    For each finding in this batch, determine:
    1. **Validity**: Is this a valid security issue or a false positive? Consider the technical accuracy and potential impact.
    2. **Severity**: What is the appropriate severity level? (Low, Medium, High)
    3. **Reasoning**: Provide clear explanations for your evaluations.
    
    ## ANALYSIS GUIDELINES
    
    **Technical Assessment:**
    - Evaluate the technical accuracy and feasibility in the context of the given smart contract
    - Consider the potential impact on contract funds, operations, and users
    - Evaluate the mathematical correctness, logical soundness, and syntactic accuracy
    - Assess the exploitation difficulty and prerequisites

    **Contextual Assessment:**
    - Consider the original purpose and design of the smart contract
    - Assess whether the finding is applicable in this context
    - Identify whether the finding misinterprets the intended functionality
    - Example: If pausing a contract disables withdrawals, this is likely by design, not a malfunctional problem, since this is the whole point of a pause function
    
    **For Related Findings (if multiple in batch):**
    - Since these findings are related, they should have the same validity and severity
    - Use all available information across findings to make the most accurate assessment
    - If the findings are likely false positives based on the combined information, mark ALL as invalid (not valid)
    
    **Severity Guidelines:**
    - **High**: Significant impact on contract functionality or user funds with feasible exploitation
    - **Medium**: Moderate impact with some prerequisites or limited scope
    - **Low**: Minor issues or informational findings with minimal impact

    {[finding.model_dump() for finding in findings_batch]}
    
    ## EVALUATION INSTRUCTIONS
    
    1. **Individual Assessment**: Evaluate each finding individually but consider group context if multiple findings are related
    2. **Consistent Assessment**: For related findings, apply the same validity and severity unless there are clear technical differences
    3. **False Positive Detection**: If findings appear to be false positives, mark them as invalid
    4. **Clear Explanations**: Provide 2-3 sentence explanations for each evaluation
    5. **Use Finding IDs**: Make sure to use the exact finding ID from the analysis
    
    Remember: Provide one evaluation result per finding in the batch, using the finding's ID.
    """
    
    # Invoke the model with structured output
    result = await model_with_structured_output.ainvoke(prompt)
    return result 
