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

    # OpenAI configuration for validation
    openai_api_key: str = Field(..., description="OpenAI API key")
    openai_validation_model: str = Field("o3-2025-04-16", description="OpenAI validation model (O3)")
    openai_temperature: float = Field(0.0, description="OpenAI temperature setting")
    openai_max_tokens: int = Field(20000, description="OpenAI max tokens")
    
    # Claude configuration for evaluation (fallback)
    claude_api_key: str = Field(..., description="Claude API key")
    claude_model: str = Field("claude-sonnet-4-20250514", description="Claude model name")
    claude_temperature: float = Field(0.0, description="Claude temperature setting")
    claude_max_tokens: int = Field(20000, description="Claude max tokens")
    
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
