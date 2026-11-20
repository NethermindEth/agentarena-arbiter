from typing import List, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # tolerate unrelated env vars (e.g. TASK_ID) present in .env
        populate_by_name=True  # allow fields to be set by name as well as by alias
    )
    
    mongodb_url: str = Field(..., description="MongoDB connection URL")
    mongodb_name: str = Field("agent_arena", description="The name of the database")

    # Claude Code (CLI) configuration. Evaluation (the findings-triage pipeline) and duplicate
    # aggregation both drive the `claude` CLI as hardened subprocesses; see
    # app/core/claude_code_cli.py. The API key is passed to the subprocess as ANTHROPIC_API_KEY.
    # Use a dedicated, spend-limited key: agents execute untrusted repository code (sandboxed).
    claude_api_key: str = Field(..., description="Anthropic API key for the Claude Code subprocess")
    claude_model: str = Field("claude-opus-5-5", description="Claude model id passed to `claude --model`")
    claude_effort: str = Field("high", description="Effort level passed to `claude --effort` (low|medium|high|xhigh|max)")
    claude_command: str = Field("claude", alias="CLAUDE_CMD", description="Claude Code CLI binary name/path")

    # Findings-triage evaluation pipeline (analyst -> refute + severity verifiers -> judge, each
    # writing/running PoCs in its own sandbox). See app/core/claude_code_triage.py.
    triage_poc_mode: Literal["required", "optional", "off"] = Field(
        "required",
        description=(
            "required: PoCs run only inside the Claude Code bubblewrap sandbox; if the sandbox is "
            "unavailable the task is not processed. optional: fall back to static analysis (no Bash) "
            "when the sandbox is unavailable. off: never give agents Bash."
        ),
    )
    triage_informational_policy: Literal["actionable", "valid", "disputed"] = Field(
        "actionable",
        description=(
            "How a finding the triage rules INFORMATIONAL (true, no security impact) is recorded: "
            "actionable = valid with severity Info only if the judge says a developer would change "
            "code because of it, otherwise disputed; valid = always valid/Info; disputed = always disputed."
        ),
    )
    triage_work_dir: str = Field("", description="Root for per-run sandboxes and PoC evidence (default: <tmp>/arbiter-triage-<uid>)")
    triage_max_parallel_agents: int = Field(4, ge=1, description="Max concurrent Claude Code agents (each may compile)")
    triage_agent_timeout_seconds: int = Field(1800, ge=60, description="Wall-clock limit for one triage agent")
    triage_build_timeout_seconds: int = Field(2700, ge=60, description="Wall-clock limit for the dependency/build agent")
    triage_agent_max_budget_usd: float = Field(
        10.0, ge=0, description="Per-agent spend cap passed to `claude --max-budget-usd` (0 disables the cap)"
    )
    triage_build_allowed_domains: str = Field(
        "github.com,codeload.github.com,objects.githubusercontent.com,raw.githubusercontent.com,"
        "registry.npmjs.org,registry.yarnpkg.com,binaries.soliditylang.org,"
        "crates.io,index.crates.io,static.crates.io,static.rust-lang.org",
        description="Comma-separated domains the build agent's sandbox may reach (finding agents get no network)",
    )
    triage_extra_read_paths: str = Field(
        "",
        description=(
            "Comma-separated paths re-allowed for reading inside the sandbox although they sit in a "
            "denied region (e.g. toolchains under the service user's HOME: ~/.foundry,~/.svm). "
            "Never list directories that contain credentials."
        ),
    )
    triage_keep_sandboxes: bool = Field(False, description="Keep per-agent sandboxes after a run (debugging)")

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

    @staticmethod
    def _split_csv(value: str) -> List[str]:
        return [item.strip() for item in (value or "").split(",") if item.strip()]

    @property
    def triage_build_domains(self) -> List[str]:
        return self._split_csv(self.triage_build_allowed_domains)

    @property
    def triage_extra_read(self) -> List[str]:
        return self._split_csv(self.triage_extra_read_paths)

# Load environment variables
load_dotenv(override=True)

# Create a global settings instance
config = Settings()
