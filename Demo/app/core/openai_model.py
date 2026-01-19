"""
OpenAI model integration for validation.
Uses OpenAI Responses API with structured output, thinking mode, and web search.
"""
import asyncio
from typing import Optional, TypeVar, Type, Union
from pydantic import BaseModel
from openai import AsyncOpenAI
from app.config import config
import logging

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Web search configuration
OPENAI_WEB_SEARCH_CONFIG = {
    "type": "web_search_preview",
    "search_context_size": "high",
}

def is_thinking_model(model: str) -> bool:
    """Check if model supports thinking mode."""
    return (
        model.startswith("o1")
        or model.startswith("o3")
        or model.startswith("o4")
        or model.startswith("gpt-5")
    )

def is_web_search_model(model: str) -> bool:
    """Check if model supports web search."""
    return (
        model.startswith("o3")
        or model.startswith("o4")
        or model.startswith("gpt-5")
    )


def _normalize_parts(content: Union[str, list], role: str) -> list[dict]:
    """
    Normalize message content parts for Responses API format.
    Converts content to Responses API format with type and text fields.
    """
    parts: list[dict] = []
    seg_type_for_role = "output_text" if role == "assistant" else "input_text"
    
    if isinstance(content, str):
        return [{"type": seg_type_for_role, "text": content}]
    
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                parts.append({"type": seg_type_for_role, "text": str(part)})
                continue
            p_type = part.get("type")
            # Map legacy/common types into Responses types
            if p_type in (None, "text"):
                text_val = part.get("text") or part.get("content") or ""
                parts.append({"type": seg_type_for_role, "text": text_val})
            elif p_type in ("input_text", "output_text"):
                parts.append(part)
        return parts
    
    # Unknown content shape, stringify
    return [{"type": seg_type_for_role, "text": str(content)}]


def convert_responses_input(messages: list) -> list[dict]:
    """
    Convert messages to OpenAI Responses API format.
    
    Args:
        messages: List of message dicts or objects with role/content attributes
        
    Returns:
        Normalized list of message dicts
    """
    normalized: list[dict] = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            cont = msg.get("content", "")
            normalized.append(
                {
                    "role": role,
                    "content": _normalize_parts(cont, role),
                }
            )
            continue
        
        # Support simple object with attributes
        if hasattr(msg, "role") and hasattr(msg, "content"):
            role = msg.role or "user"
            cont = msg.content
            normalized.append(
                {
                    "role": role,
                    "content": _normalize_parts(cont, role),
                }
            )
            continue
        
        raise TypeError(f"Unrecognised message shape: {msg!r}")
    
    return normalized


async def send_prompt_to_openai_async(
    model_type: str,
    messages: str | list[dict],
    response_model: Type[T],
    thinking: bool = True,
    web_search: bool = False,
    system_prompt: Optional[str] = None,
    max_retries: int = 2,
) -> Optional[T]:
    """
    Send prompt to OpenAI with structured output.
    
    Args:
        model_type: OpenAI model name (e.g., "o3-2025-04-16")
        messages: Prompt string or list of message dicts
        response_model: Pydantic model for structured output
        thinking: Enable thinking mode for reasoning models
        web_search: Enable web search
        system_prompt: Optional system prompt
        max_retries: Maximum retry attempts
        
    Returns:
        Parsed response model or None on error
    """
    if not config.openai_api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")
    
    client = AsyncOpenAI(api_key=config.openai_api_key)
    
    # Convert string to message format
    if isinstance(messages, str):
        messages_list = [{"role": "user", "content": messages}]
    else:
        messages_list = messages
    
    # Normalize input using convert_responses_input
    normalized_input = convert_responses_input(messages_list)
    
    # Build parameters for Responses API
    params = {
        "model": model_type,
        "input": normalized_input,
    }
    
    if system_prompt:
        params["instructions"] = system_prompt
    
    # Set thinking mode for reasoning models
    if thinking and is_thinking_model(model_type):
        params["reasoning"] = {"effort": "high"}
    elif not thinking and not is_thinking_model(model_type):
        params["temperature"] = config.openai_temperature
    
    # Add web search if enabled
    if web_search and is_web_search_model(model_type):
        params["tools"] = [OPENAI_WEB_SEARCH_CONFIG]
    
    # Set structured output format
    if response_model:
        params["text_format"] = response_model
    
    # Retry logic
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            logger.debug(f"[OpenAI] Calling {model_type} (attempt {attempt + 1}/{max_retries + 1})")
            
            # Use Responses API
            response = await client.responses.parse(**params)
            
            # Extract parsed response
            parsed_response = getattr(response, "output_parsed", None)
            
            if parsed_response:
                logger.debug(f"[OpenAI] Successfully parsed response from {model_type}")
                return parsed_response
            else:
                logger.warning(f"[OpenAI] No parsed response from {model_type}")
                return None
                
        except Exception as e:
            last_error = e
            logger.warning(f"[OpenAI] Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries:
                # Exponential backoff
                await asyncio.sleep(2 ** attempt)
            else:
                logger.error(f"[OpenAI] All {max_retries + 1} attempts failed for {model_type}")
    
    # If all retries failed, return None (caller should handle gracefully)
    logger.error(f"[OpenAI] Failed to get response from {model_type} after {max_retries + 1} attempts: {last_error}")
    return None

