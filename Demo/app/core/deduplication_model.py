import logging
from typing import Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.config import config
from app.core.litellm_client import create_litellm_chat_model, model_base_name
from app.core.prompt_utils import build_context_section
from app.models.finding_db import FindingDB
from app.types import TaskCache

logger = logging.getLogger(__name__)


class DuplicateFinding(BaseModel):
    """Single duplicate finding relationship."""

    findingId: str = Field(description="ID of the finding that is a duplicate", min_length=1)
    duplicateOf: str = Field(description="ID of the original finding", min_length=1)
    explanation: str = Field(description="Explanation of why the finding is a duplicate")


class DeduplicationResult(BaseModel):
    """Result of deduplication analysis."""

    results: list[DuplicateFinding] = Field(description="List of duplicate relationships")


def get_deduplication_config() -> dict[str, Any]:
    """
    Get the deduplication model configuration from environment variables.

    Returns:
        Dictionary with model configuration parameters
    """
    return {
        "model": config.deduplication_model,
        "max_output_tokens": config.deduplication_max_tokens,
        "temperature": config.deduplication_temperature,
        "thinking_level": config.deduplication_thinking_level,
    }


# Model name prefixes (without the LiteLLM provider prefix) that accept a thinking level and
# ignore temperature: every Gemini 3 model thinks.
_THINKING_MODEL_PREFIXES = ("gemini-3", "gemini-4")

# How the deduplication schema is requested through LiteLLM. json_schema becomes Gemini's native
# response schema, which is what Gemini used before the proxy.
# tests/live/test_litellm_structured_output.py checks both methods.
STRUCTURED_OUTPUT_METHOD: Literal["function_calling", "json_schema"] = "json_schema"


def create_deduplication_model(
    model_name: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    thinking_level: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """
    Create the deduplication model served by the LiteLLM proxy, from parameters or the environment.

    For thinking models (see _THINKING_MODEL_PREFIXES), `thinking_level` is sent as the
    reasoning effort and `temperature` is ignored (with a warning). For other Gemini models,
    `temperature` is used and `thinking_level` is ignored (with a warning).

    Args:
        model_name: Optional LiteLLM model key override
        max_tokens: Optional max tokens override
        temperature: Optional temperature override; ignored for thinking models
        thinking_level: Optional thinking level override (low/medium/high); ignored for non-thinking models
        api_key: Optional LiteLLM API key override

    Returns:
        Configured ChatOpenAI instance pointed at the LiteLLM server

    Raises:
        ValueError: If the LiteLLM server URL or API key is not configured
    """
    deduplication_config = get_deduplication_config()

    if model_name:
        deduplication_config["model"] = model_name
    if max_tokens:
        deduplication_config["max_output_tokens"] = max_tokens
    if temperature is not None:
        deduplication_config["temperature"] = temperature
    if thinking_level:
        deduplication_config["thinking_level"] = thinking_level

    model = deduplication_config["model"]
    is_thinking_model = model_base_name(model).startswith(_THINKING_MODEL_PREFIXES)
    effective_temperature: float | None = None
    reasoning_effort: str | None = None

    if is_thinking_model:
        if deduplication_config["temperature"] is not None:
            logger.warning(
                "Ignoring temperature=%s for model '%s' (thinking models use thinking_level instead).",
                deduplication_config["temperature"],
                model,
            )
        reasoning_effort = deduplication_config["thinking_level"]
    else:
        if deduplication_config["thinking_level"] is not None:
            logger.warning(
                "Ignoring thinking_level=%s for model '%s' (non-thinking models use temperature instead).",
                deduplication_config["thinking_level"],
                model,
            )
        effective_temperature = deduplication_config["temperature"]

    return create_litellm_chat_model(
        model=model,
        max_tokens=deduplication_config["max_output_tokens"],
        temperature=effective_temperature,
        reasoning_effort=reasoning_effort,
        api_key=api_key,
    )


def create_structured_deduplication_model(model: ChatOpenAI | None = None) -> Any:
    """
    Create the deduplication model with structured output.

    Args:
        model: Optional ChatOpenAI model (created from environment if not provided)

    Returns:
        Configured model with structured output for deduplication
    """
    if not model:
        model = create_deduplication_model()

    return model.with_structured_output(
        DeduplicationResult, method=STRUCTURED_OUTPUT_METHOD, include_raw=True
    )


async def find_duplicates_structured(
    model_with_structured_output: Any, findings: list[FindingDB], task_cache: TaskCache
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
"""  # noqa: S608  an LLM prompt, not a SQL query

    response = await model_with_structured_output.ainvoke(prompt)

    raw = response.get("raw")
    parsed = response.get("parsed")
    parsing_error = response.get("parsing_error")

    usage = getattr(raw, "usage_metadata", None) if raw is not None else None
    if usage:
        logger.info(
            "Deduplication token usage: input=%s, output=%s, total=%s, output_details=%s",
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("total_tokens"),
            usage.get("output_token_details"),
        )
    else:
        logger.warning("Deduplication: no usage_metadata on raw response")

    if parsing_error is not None or parsed is None:
        raise ValueError(f"Failed to parse the deduplication response: {parsing_error}")

    return parsed
