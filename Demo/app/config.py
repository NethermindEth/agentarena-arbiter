from pydantic import Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    mongodb_url: str = Field(..., env="MONGODB_URL")
    claude_api_key: str = Field(..., env="CLAUDE_API_KEY")
    claude_model: str = Field("claude-3-7-sonnet-20250219", env="CLAUDE_MODEL")
    claude_temperature: float = Field(0.0, env="CLAUDE_TEMPERATURE")
    claude_max_tokens: int = Field(20000, env="CLAUDE_MAX_TOKENS")
    
    # Gemini configuration for deduplication
    gemini_api_key: str = Field(..., env="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-pro", env="GEMINI_MODEL")
    gemini_temperature: float = Field(0.0, env="GEMINI_TEMPERATURE")
    gemini_max_tokens: int = Field(20000, env="GEMINI_MAX_TOKENS")
    
    debug: bool = Field(False, env="DEBUG")
    backend_findings_endpoint: str = Field(..., env="BACKEND_FINDINGS_ENDPOINT")
    backend_submitted_tasks_endpoint: str = Field(..., env="BACKEND_SUBMITTED_TASKS_ENDPOINT")
    backend_task_repository_endpoint: str = Field(..., env="BACKEND_TASK_REPOSITORY_ENDPOINT")
    backend_agents_endpoint: str = Field(..., env="BACKEND_AGENTS_ENDPOINT")
    backend_submissions_endpoint: str = Field(..., env="BACKEND_SUBMISSIONS_ENDPOINT")
    backend_api_key: str = Field(..., env="BACKEND_API_KEY")
    max_findings_per_submission: int = Field(20, env="MAX_FINDINGS_PER_SUBMISSION")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    data_dir: str = "task_data"  # Hardcoded value - helps with gitignore

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# Load environment variables
load_dotenv(override=True)

# Create a global settings instance
config = Settings()
