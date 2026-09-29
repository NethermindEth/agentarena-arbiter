from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )
    
    mongodb_url: str = Field(..., description="MongoDB connection URL")

    # Claude configuration for evaluation
    claude_api_key: str = Field(..., description="Claude API key")
    claude_model: str = Field("claude-sonnet-4-20250514", description="Claude model name")
    claude_max_tokens: int = Field(60000, description="Claude max tokens")
    claude_temperature: Optional[float] = Field(None, description="Optional Claude temperature; leave unset for thinking models (e.g. claude-opus-4-7)")
    
    # Gemini configuration for deduplication
    gemini_api_key: str = Field(..., description="Gemini API key")
    gemini_model: str = Field("gemini-3.5-flash", description="Gemini model name")
    gemini_max_tokens: int = Field(65536, description="Gemini max output tokens (model cap for gemini-3.5-flash)")
    gemini_thinking_level: Optional[str] = Field(None, description="Optional Gemini thinking level (low/medium/high); only used for thinking models (e.g. gemini-3.5-flash)")
    gemini_temperature: Optional[float] = Field(None, description="Optional Gemini temperature; ignored for thinking models (e.g. gemini-3.5-flash)")

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
