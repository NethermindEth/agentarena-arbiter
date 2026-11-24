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

    # Claude configuration for evaluation
    claude_model: str = Field(
        "anthropic/claude-opus-4-8", description="Claude model key in the LiteLLM server"
    )
    claude_max_tokens: int = Field(60000, description="Claude max tokens")
    claude_temperature: float | None = Field(
        None,
        description="Optional Claude temperature; leave unset for thinking models (e.g. claude-opus-4-8)",
    )

    # Gemini configuration for deduplication
    gemini_model: str = Field(
        "gemini/gemini-3.8-flash", description="Gemini model key in the LiteLLM server"
    )
    gemini_max_tokens: int = Field(65536, description="Gemini max output tokens")
    gemini_thinking_level: str | None = Field(
        None,
        description="Optional Gemini thinking level (low/medium/high); only used for thinking models (gemini-3*)",
    )
    gemini_temperature: float | None = Field(
        None,
        description="Optional Gemini temperature; ignored for thinking models (gemini-3*)",
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
