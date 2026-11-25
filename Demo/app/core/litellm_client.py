import logging

import httpx
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import config

logger = logging.getLogger(__name__)

# The LiteLLM proxy authenticates clients with this header instead of `Authorization`.
LITELLM_API_KEY_HEADER = "X-LiteLLM-API-Key"

# Reaching the proxy should be quick; only the reply itself may take long.
CONNECT_TIMEOUT_SECONDS = 10.0


def create_litellm_chat_model(
    model: str,
    max_tokens: int,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """
    Create a chat model served by the LiteLLM proxy through its OpenAI-compatible API.

    Args:
        model: Model key as registered in the LiteLLM server (e.g. `gemini/gemini-3.8-flash`)
        max_tokens: Maximum output tokens
        temperature: Optional temperature; omitted from the request when None
        reasoning_effort: Optional reasoning effort (low/medium/high); LiteLLM translates it to
            the provider's thinking setting
        api_key: Optional LiteLLM API key override

    Returns:
        Configured ChatOpenAI instance pointed at the LiteLLM server

    Raises:
        ValueError: If the LiteLLM server URL or API key is missing.
    """
    key = api_key or config.litellm_api_key
    if not config.litellm_server_url:
        raise ValueError("LITELLM_SERVER_URL environment variable is not set")
    if not key:
        raise ValueError("LITELLM_API_KEY environment variable is not set")

    # Temperature and reasoning effort default to None in ChatOpenAI, which leaves them out of
    # the request, so passing None here is the same as not passing them.
    return ChatOpenAI(
        model=model,
        max_completion_tokens=max_tokens,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        base_url=config.litellm_server_url.rstrip("/"),
        # The OpenAI client always sends `Authorization: Bearer <api_key>`; the proxy reads the
        # key from LITELLM_API_KEY_HEADER, so both carry the same LiteLLM key.
        api_key=SecretStr(key),
        default_headers={LITELLM_API_KEY_HEADER: key},
        # Without an explicit timeout the OpenAI client waits forever on a stalled proxy. Replies
        # are not streamed, so the read timeout bounds the wait for the whole reply. Each retry
        # repeats the full call, so the worst case is (retries + 1) times the timeout.
        # Deployment requirement, not enforced here: the LiteLLM server's request_timeout for
        # both model keys must sit slightly below this setting, so the proxy times out and ends
        # its upstream call first. LiteLLM's default (6000 s) is above this setting's default
        # (1800 s); left unchanged, the client gives up first and a retry can overlap a reply
        # the proxy is still generating, billing that generation twice.
        timeout=httpx.Timeout(config.litellm_timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS),
        max_retries=config.litellm_max_retries,
    )


def model_base_name(model: str) -> str:
    """Return a LiteLLM model key without its provider prefix (`gemini/x` becomes `x`)."""
    return model.rsplit("/", 1)[-1]
