from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # tolerate unrelated env vars (e.g. TASK_ID) present in .env
    )
    
    mongodb_url: str = Field(..., description="MongoDB connection URL")

    # Claude Code (CLI) configuration for evaluation. The evaluator drives the `claude` CLI
    # as a read-only subprocess inside the repository checkout, so it needs the binary name,
    # the model id, and the API key (passed to the subprocess as ANTHROPIC_API_KEY).
    claude_api_key: str = Field(..., description="Anthropic API key for the Claude Code subprocess")
    claude_model: str = Field("claude-opus-4-8", description="Claude model id passed to `claude --model`")
    claude_command: str = Field("claude", description="Claude Code CLI binary name/path")
    
    # Gemini configuration for deduplication
    gemini_api_key: str = Field(..., description="Gemini API key")
    gemini_model: str = Field("gemini-2.5-pro", description="Gemini model name")
    gemini_temperature: float = Field(0.0, description="Gemini temperature setting")
    gemini_max_tokens: int = Field(20000, description="Gemini max tokens")
    
    debug: bool = Field(False, description="Debug mode flag")
    log_level: str = Field("INFO", description="Logging level")

    backend_api_key: str = Field(..., description="Backend API key")
    backend_findings_endpoint: str = Field(..., description="Backend findings endpoint URL")
    backend_submissions_endpoint: str = Field(..., description="Backend submissions endpoint URL")
    backend_task_repository_endpoint: str = Field(..., description="Backend task repository endpoint URL")
    max_findings_per_submission: int = Field(20, description="Maximum findings per submission")
    data_dir: str = "task_data"  # Hardcoded value - helps with gitignore

# Load environment variables
load_dotenv(override=True)

# Create a global settings instance
config = Settings()
