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
    claude_command: str = Field(
        "claude", alias="CLAUDE_CMD", description="Claude Code CLI binary name/path"
    )

    # Findings-triage evaluation pipeline (analyst -> refute + severity verifiers -> judge, each
    # writing/running PoCs in its own sandbox). See app/core/claude_code_triage.py.
    triage_poc_mode: Literal["required", "optional", "off"] = Field(
        "required",
        description=(
            "required: PoCs run only inside the Claude Code bubblewrap sandbox; if the sandbox is "
            "unavailable the task is not processed. optional: fall back to static analysis "
            "(no Bash) when the sandbox is unavailable. off: never give agents Bash."
        ),
    )
    triage_informational_policy: Literal["actionable", "valid", "disputed"] = Field(
        "actionable",
        description=(
            "How a finding the triage rules INFORMATIONAL (true, no security impact) is recorded: "
            "actionable = valid with severity Info only if the judge says a developer would "
            "change code because of it, otherwise disputed; valid = always valid/Info; "
            "disputed = always disputed."
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
    triage_agent_timeout_seconds: int = Field(
        1800, ge=60, description="Wall-clock limit for one triage agent"
    )
    triage_build_timeout_seconds: int = Field(
        2700, ge=60, description="Wall-clock limit for the dependency/build agent"
    )
    triage_agent_max_budget_usd: float = Field(
        10.0,
        ge=0,
        description=(
            "Per-agent spend cap passed to `claude --max-budget-usd` (0 disables the cap)"
        ),
    )
    triage_build_allowed_domains: str = Field(
        "github.com,codeload.github.com,objects.githubusercontent.com,raw.githubusercontent.com,"
        "registry.npmjs.org,registry.yarnpkg.com,binaries.soliditylang.org,"
        "crates.io,index.crates.io,static.crates.io,static.rust-lang.org",
        description=(
            "Comma-separated domains the build agent's sandbox may reach "
            "(finding agents get no network)"
        ),
    )
    triage_extra_read_paths: str = Field(
        "",
        description=(
            "Comma-separated paths re-allowed for reading inside the sandbox although they sit in "
            "a denied region (e.g. toolchains under the service user's HOME: ~/.foundry,~/.svm). "
            "Never list directories that contain credentials."
        ),
    )
    triage_keep_sandboxes: bool = Field(
        False, description="Keep per-agent sandboxes after a run (debugging)"
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

    @staticmethod
    def _split_csv(value: str) -> list[str]:
        return [item.strip() for item in (value or "").split(",") if item.strip()]

    @property
    def triage_build_domains(self) -> list[str]:
        """The domains of TRIAGE_BUILD_ALLOWED_DOMAINS, as a list."""
        return self._split_csv(self.triage_build_allowed_domains)

    @property
    def triage_extra_read(self) -> list[str]:
        """The paths of TRIAGE_EXTRA_READ_PATHS, as a list."""
        return self._split_csv(self.triage_extra_read_paths)


# Load environment variables
load_dotenv(override=True)

# Create a global settings instance
config = Settings()  # pyright: ignore[reportCallIssue]  the fields come from the environment
