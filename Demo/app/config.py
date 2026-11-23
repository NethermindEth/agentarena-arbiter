from typing import Literal

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

    # Claude Code (CLI) configuration. Evaluation (the findings-triage pipeline) and duplicate
    # aggregation both drive the `claude` CLI as hardened subprocesses; see
    # app/core/claude_code_cli.py. The API key is passed to the subprocess as ANTHROPIC_API_KEY.
    # Use a dedicated, spend-limited key: agents execute untrusted repository code (sandboxed).
    claude_api_key: str = Field(..., description="Anthropic API key for the Claude Code subprocess")
    claude_model: str = Field(
        "claude-opus-5-5", description="Claude model id passed to `claude --model`"
    )
    claude_effort: str = Field(
        "high", description="Effort level passed to `claude --effort` (low|medium|high|xhigh|max)"
    )

    # Findings-triage evaluation pipeline (analyst -> refute + severity verifiers -> judge, each
    # writing/running PoCs in its own sandbox). See app/core/claude_code_triage.py.
    triage_poc_scope: Literal["all", "high_medium"] = Field(
        "high_medium",
        description=(
            "Which units get PoCs (Bash in the bubblewrap sandbox): all, or high_medium = only "
            "units whose reports claim High or Medium severity (a duplicate group: the highest "
            "any member claims); the rest are analysed statically. The sandbox is required either "
            "way: a host without it leaves the task unprocessed."
        ),
    )
    triage_work_dir: str = Field(
        "",
        description=(
            "Root for per-run sandboxes and PoC evidence (default: <tmp>/arbiter-triage-<uid>)"
        ),
    )
    triage_max_parallel_agents: int = Field(
        4, ge=1, description="Max concurrent Claude Code agents (each may compile)"
    )
    triage_agent_max_budget_usd: float = Field(
        10.0,
        ge=0,
        description=(
            "Per-agent spend cap passed to `claude --max-budget-usd` (0 disables the cap)"
        ),
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

    debug: bool = Field(
        False,
        description=(
            "Debug mode: keep each triage agent's sandbox (a full repository copy) after a run"
        ),
    )
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
