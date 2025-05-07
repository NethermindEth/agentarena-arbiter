import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)

# MongoDB configuration
MONGODB_URL = os.getenv("MONGODB_URL")

# Claude API configuration
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL")
CLAUDE_TEMPERATURE = float(os.getenv("CLAUDE_TEMPERATURE", 0.0))
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", 20000))

# Application settings
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Finding deduplication settings
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", 0.8))

# Agent4rena Backend Configuration
BACKEND_FINDINGS_ENDPOINT = os.getenv("BACKEND_FINDINGS_ENDPOINT")
BACKEND_FILES_ENDPOINT = os.getenv("BACKEND_FILES_ENDPOINT")
BACKEND_AGENTS_ENDPOINT = os.getenv("BACKEND_AGENTS_ENDPOINT")
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY")

TASK_ID = os.getenv("TASK_ID")
