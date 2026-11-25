from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mongodb_url: str = Field(..., description="MongoDB connection URL")

    # LiteLLM proxy serving both models below
    litellm_server_url: str = Field(..., description="LiteLLM server base URL (http/https)")
    litellm_api_key: str = Field(..., description="LiteLLM API key, sent as X-LiteLLM-API-Key")
    litellm_timeout_seconds: float = Field(
        1800.0,
        gt=0,
        description=(
            "Seconds to wait for one model reply; set it slightly above the LiteLLM server's"
            " request_timeout, so the proxy times out, and cancels the upstream call, first"
        ),
    )
    litellm_max_retries: int = Field(
        3, ge=0, description="Retries after a timeout, connection error, 429 or 5xx from LiteLLM"
    )

    # Configuration for evaluation
    evaluation_model: str = Field(
        "anthropic/claude-opus-4-8", description="Evaluation model key in the LiteLLM server"
    )
    evaluation_max_tokens: int = Field(60000, description="Evaluation model max tokens")
    evaluation_temperature: float | None = Field(
        None,
        description="Optional evaluation model temperature; leave unset for thinking models (e.g. claude-opus-4-8)",
    )

    # Configuration for deduplication
    deduplication_model: str = Field(
        "gemini/gemini-3.8-flash", description="Deduplication model key in the LiteLLM server"
    )
    deduplication_max_tokens: int = Field(
        65536, description="Deduplication model max output tokens"
    )
    deduplication_thinking_level: str | None = Field(
        None,
        description="Optional deduplication model thinking level (low/medium/high); only used for thinking models (gemini-3*)",
    )
    deduplication_temperature: float | None = Field(
        None,
        description="Optional deduplication model temperature; ignored for thinking models (gemini-3*)",
    )

    debug: bool = Field(False, description="Debug mode flag")
    log_level: str = Field("INFO", description="Logging level")

    backend_api_key: str = Field(..., description="Backend API key")
    backend_findings_endpoint: str = Field(..., description="Backend findings endpoint URL")
    backend_submissions_endpoint: str = Field(..., description="Backend submissions endpoint URL")
    backend_task_repository_endpoint: str = Field(
        ..., description="Backend task repository endpoint URL"
    )
    max_findings_per_submission: int = Field(20, description="Maximum findings per submission")
    data_dir: str = "task_data"  # Hardcoded value - helps with gitignore


# Load environment variables
load_dotenv(override=True)

# Create a global settings instance
config = Settings()  # pyright: ignore[reportCallIssue]  the fields come from the environment
