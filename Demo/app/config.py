from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # An empty variable, as `KEY=` or a compose `${KEY}` with KEY unset, counts as unset, so the
    # field keeps its default instead of failing validation on "". A `.env` key with no field, such
    # as one left from a removed setting, is ignored as an unknown environment variable would be.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    # Claude configuration for evaluation
    claude_api_key: str = Field(..., description="Claude API key")
    claude_model: str = Field("claude-sonnet-4-20250514", description="Claude model name")
    claude_max_tokens: int = Field(60000, description="Claude max tokens")
    claude_temperature: float | None = Field(
        None,
        description="Optional Claude temperature; leave unset for thinking models (e.g. claude-opus-4-7)",
    )

    # Gemini configuration for deduplication
    gemini_api_key: str = Field(..., description="Gemini API key")
    gemini_model: str = Field("gemini-3.5-flash", description="Gemini model name")
    gemini_max_tokens: int = Field(
        65536, description="Gemini max output tokens (model cap for gemini-3.5-flash)"
    )
    gemini_thinking_level: str | None = Field(
        None,
        description="Optional Gemini thinking level (low/medium/high); only used for thinking models (e.g. gemini-3.5-flash)",
    )
    gemini_temperature: float | None = Field(
        None,
        description="Optional Gemini temperature; ignored for thinking models (e.g. gemini-3.5-flash)",
    )

    debug: bool = Field(False, description="Debug mode flag")
    log_level: str = Field("INFO", description="Logging level")

    backend_api_key: str = Field(..., description="Backend API key")
    backend_findings_endpoint: str = Field(..., description="Backend findings endpoint URL")
    backend_task_repository_endpoint: str = Field(
        ..., description="Backend task repository endpoint URL"
    )
    backend_approved_tasks_endpoint: str = Field(
        ..., description="Backend approved tasks endpoint URL (/api/arbiter/approved-tasks)"
    )
    backend_tasks_endpoint: str = Field(
        ...,
        description="Backend task endpoint base URL (/api/arbiter/tasks); the task id is appended",
    )
    data_dir: str = "task_data"  # Hardcoded value - helps with gitignore


# Load environment variables
load_dotenv(override=True)

# Create a global settings instance
config = Settings()  # pyright: ignore[reportCallIssue]  the fields come from the environment
