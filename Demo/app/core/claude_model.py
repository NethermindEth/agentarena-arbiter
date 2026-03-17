from typing import Optional, Dict, Any, List
from app.models.finding_db import FindingDB
from app.config import config
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field
from app.types import TaskCache
from app.core.prompt_utils import build_context_section


class FindingEvaluation(BaseModel):
    """Single finding evaluation result."""
    finding_id: str = Field(description="ID/title of the evaluated finding")
    is_valid: bool = Field(description="Whether the finding represents a valid security issue")
    severity: str = Field(description="Severity level: High, Medium, Low, or Info")
    comment: str = Field(description="Brief explanation of the evaluation (2-3 sentences maximum)")

class EvaluationResult(BaseModel):
    """Evaluation result."""
    results: List[FindingEvaluation] = Field(description="List of evaluation results")


# Schema for one-by-one validation
class ValidationStep(BaseModel):
    """Single step in the validation process."""
    reasoning: str = Field(description="Reasoning for this step")
    step_result: bool = Field(description="Result of this step (True = passed, False = failed)")

class DirectValidationResult(BaseModel):
    """Result of one-by-one validation."""
    steps: List[ValidationStep] = Field(description="List of validation steps with reasoning")
    final_result: bool = Field(description="Final decision: True = keep finding, False = discard")

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
        claude_config["model_name"] = model_name
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
    findings_batch: List[FindingDB],
    task_cache: TaskCache,
    related_findings: bool = False
) -> EvaluationResult:
    """
    Evaluate a batch of findings using structured output with different prompts based on batch type.
    
    Args:
        model_with_structured_output: Model configured with structured output
        findings_batch: List of findings to evaluate
        task_cache: Task context containing smart contract files and documentation
        related_findings: Whether the findings are related to each other
        
    Returns:
        Structured evaluation result with guaranteed format
    """
    
    if related_findings:
        prompt = _get_related_findings_prompt(findings_batch, task_cache)
    else:
        prompt = _get_individual_findings_prompt(findings_batch, task_cache)

    return await model_with_structured_output.ainvoke(prompt)

def _get_related_findings_prompt(findings_batch: List[FindingDB], task_cache: TaskCache) -> str:
    """Generate prompt for evaluating related/duplicate findings as a group."""
    
    # Build context section
    context_section = build_context_section(task_cache)
    
    return f"""
You are a smart contract security expert tasked with evaluating a batch of RELATED findings that refer to the same underlying vulnerability.

## BATCH CONTEXT
This batch contains multiple findings that are duplicates or variations of the same underlying security issue. They should be evaluated collectively as they represent different reports of the same vulnerability.

## EVALUATION APPROACH
Since these findings are related:
1. **Unified Assessment**: All findings should receive the same validity and severity rating
2. **Collective Analysis**: Use information from all findings to make the most comprehensive assessment
3. **Cross-Reference**: Look for complementary details across findings to build complete picture
4. **Consistency**: Apply the same evaluation criteria to all findings in the batch
5. **Context-Aware**: Use the smart contract context above to validate technical accuracy and assess real-world impact

## EVALUATION CRITERIA

**Validity Assessment:**
- Analyze the core vulnerability described across all findings
- Determine if this represents a legitimate security issue or false positive
- Consider the technical accuracy and potential impact of the underlying issue
- **Use the smart contract context to validate claims and assess if the vulnerability actually exists in the provided code**

**Severity Assessment:**
- **High**: High or critical impact on contract functionality, user funds, or security with feasible exploitation
- **Medium**: Moderate impact with some prerequisites or limited scope of exploitation  
- **Low**: Minor issues with minimal impact
- **Info**: Informational findings with no impact

## ANALYSIS GUIDELINES

**Technical Assessment:**
- Evaluate the technical accuracy across all finding descriptions **against the actual smart contract code provided**
- Assess feasibility and impact of exploitation in this smart contract context
- Consider mathematical correctness, logical soundness, and syntactic accuracy
- Determine exploitation difficulty and prerequisites
- **Cross-reference finding claims with the actual contract implementation**

**Contextual Assessment:**
- Consider the smart contract's intended purpose and design **based on the provided context**
- Assess if the issue contradicts intended functionality, is a false positive, or represents actual vulnerability which is applicable in this context
- Example: Pause functions disabling withdrawals is typically intentional design, not a bug
- **Use the documentation and Q&A responses to understand intended behavior**

## RETURN FORMAT
Return a JSON object with the following structure:
```json
{{
    "results": [
        {{
            "finding_id": "Finding ID",
            "is_valid": true/false,
            "severity": "High/Medium/Low/Info", 
            "comment": "Explanation focusing on the shared vulnerability"
        }}
    ]
}}
```

## SMART CONTRACT CONTEXT
{context_section}

## FINDINGS TO ANALYZE
{[finding.dump() for finding in findings_batch]}

## EVALUATION INSTRUCTIONS
1. **Identify Core Issue**: Determine the underlying vulnerability these findings share
2. **Validate Against Context**: Check if the vulnerability actually exists in the provided smart contract code
3. **Unified Evaluation**: Apply same validity and severity to all findings
4. **Comprehensive Comments**: Explain the shared vulnerability and why all findings receive the same rating
5. **Use Exact IDs**: Include evaluation for each finding using its exact ID

Provide one evaluation per finding, but ensure all evaluations are consistent since they refer to the same underlying issue. Base your analysis on the actual smart contract context provided.
"""

def _get_individual_findings_prompt(findings_batch: List[FindingDB], task_cache: TaskCache) -> str:
    """Generate prompt for evaluating individual unrelated findings separately."""
    
    # Build context section
    context_section = build_context_section(task_cache)
    
    return f"""
You are a smart contract security expert tasked with evaluating a batch of INDIVIDUAL findings that describe different vulnerabilities within the same protocol or smart contract.

## BATCH CONTEXT
This batch contains unrelated findings that must be evaluated independently. Each finding represents a potentially different type of vulnerability or issue and should be assessed on its own merits.

## EVALUATION APPROACH
Since these findings are unrelated:
1. **Independent Assessment**: Evaluate each finding separately without influence from others
2. **Individual Context**: Consider each finding within its specific context and scope
3. **Separate Ratings**: Each finding may have different validity and severity ratings
4. **Focused Analysis**: Analyze each finding's specific claims and evidence
5. **Context-Aware**: Use the smart contract context above to validate each finding's technical accuracy

## EVALUATION CRITERIA

**Validity Assessment:**
- Analyze the core vulnerability described in each finding
- Determine if this represents a legitimate security issue or false positive
- Consider the technical accuracy and potential impact of the underlying issue
- **Use the smart contract context to validate claims and assess if each vulnerability actually exists in the provided code**

**Severity Assessment:**
- **High**: High or critical impact on contract functionality, user funds, or security with feasible exploitation
- **Medium**: Moderate impact with some prerequisites or limited scope of exploitation  
- **Low**: Minor issues with minimal impact
- **Info**: Informational findings with no impact

## ANALYSIS GUIDELINES

**Technical Assessment:**
- Evaluate the technical accuracy for each finding description **against the actual smart contract code provided**
- Assess feasibility and impact of exploitation in the smart contract context
- Consider mathematical correctness, logical soundness, and syntactic accuracy
- Determine exploitation difficulty and prerequisites
- **Cross-reference each finding's claims with the actual contract implementation**

**Contextual Assessment:**
- Consider the smart contract's intended purpose and design **based on the provided context**
- Assess if the finding contradicts intended functionality, is a false positive, or represents actual vulnerability which is applicable in this context
- Example: Pause functions disabling withdrawals is typically intentional design, not a bug
- **Use the documentation and Q&A responses to understand intended behavior for each specific finding**

## RETURN FORMAT
Return a JSON object with the following structure:
```json
{{
    "results": [
        {{
            "finding_id": "Finding ID",
            "is_valid": true/false,
            "severity": "High/Medium/Low/Info",
            "comment": "Individual explanation specific to this finding"
        }}
    ]
}}
```

## SMART CONTRACT CONTEXT
{context_section}

## FINDINGS TO ANALYZE
{[finding.dump() for finding in findings_batch]}

## EVALUATION INSTRUCTIONS
1. **Separate Analysis**: Evaluate each finding independently without cross-influence
2. **Validate Against Context**: Check if each vulnerability actually exists in the provided smart contract code
3. **Individual Merit**: Base validity and severity solely on each finding's specific claims and the actual code context
4. **Targeted Comments**: Provide 2-3 sentences explaining the evaluation for each specific finding
5. **Use Exact IDs**: Include one evaluation per finding using its exact ID
6. **Varied Results**: It's expected and appropriate for findings to have different validity and severity ratings

Analyze each finding on its individual merits and provide separate, independent evaluations based on the actual smart contract context provided.
"""
