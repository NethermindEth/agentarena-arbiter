"""
Shared plumbing for driving Anthropic's Claude Code CLI as a hardened subprocess.

Every Claude Code run in the arbiter (the triage agents in
:mod:`app.core.claude_code_triage` and the duplicate aggregator in
:mod:`app.core.claude_code_aggregator`) goes through :meth:`ClaudeCodeCLI.run_agent`, which
owns the security-relevant launch decisions in one place:

* **No ambient configuration.** ``--bare`` skips hooks, plugins, CLAUDE.md auto-discovery,
  auto-memory and keychain/OAuth reads; ``--restricted`` ignores user/project/local settings
  files (so a ``.claude/settings.json`` with hooks inside the untrusted repository is never
  loaded), confines the file tools to the working directory and refuses bypassPermissions;
  ``--strict-mcp-config`` loads no MCP servers; ``--disable-slash-commands`` loads no skills.
  The only settings that apply are the ones passed with ``--settings``.
* **Deny by default.** ``--permission-prompts none`` auto-denies anything that would prompt;
  the caller's ``--settings`` grant exactly the tools each role needs, and ``--tools`` removes
  every other built-in tool (WebFetch, WebSearch, subagents...).
* **No secrets in the environment.** The child gets a minimal allowlisted environment, a
  private ``HOME``/``TMPDIR`` (never the service user's real home), and only the Anthropic API
  key. ``CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1`` additionally scrubs credentials from the
  environment of commands the agent runs.
* **Bounded.** Wall-clock timeout (the whole process group is killed), optional
  ``--max-budget-usd`` cap, no session persistence, no auto-updater, no non-essential traffic.
* **Structured output.** ``--output-format json`` + ``--json-schema``: the answer is read from
  the CLI's validated ``structured_output`` field, never scraped from free text.

The command sandbox itself (bubblewrap: filesystem + network confinement for the agent's Bash
commands) is configured by the caller through ``settings["sandbox"]``; see
:mod:`app.core.triage_sandbox`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import config

logger = logging.getLogger(__name__)


# The severity vocabulary the models may return (mirrors app.models.finding_input.Severity).
VALID_SEVERITIES = ("High", "Medium", "Low", "Info")

# Environment variables inherited from the service process. Everything else (the backend and
# Gemini keys, ...) is withheld from the agent. RUSTUP_HOME only locates a Rust
# toolchain installed outside HOME; every other tool cache lives in the agent's private HOME.
_ENV_PASSTHROUGH = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "RUSTUP_HOME")

# Grace period between SIGTERM and SIGKILL when a run times out.
_KILL_GRACE_SECONDS = 10


def json_block(obj) -> str:
    """
    Serialize untrusted, attacker-influenced data for safe embedding in a prompt.

    ``ensure_ascii=True`` (the default) escapes quotes, backslashes, newlines *and every
    non-ASCII character* (including the U+2028/U+2029 line separators that JSON leaves raw
    otherwise). The result is therefore single-physical-line-per-value pure ASCII, so a value
    cannot terminate the surrounding ```json fence, start a new markdown heading, or otherwise
    escape the block it is placed in. ``indent=2`` only adds structural newlines between keys,
    never inside an escaped value.
    """
    return json.dumps(obj, indent=2)


def render_template(template: str, replacements: dict[str, str]) -> str:
    """
    Substitute ``{{TOKEN}}`` placeholders in a *single* left-to-right regex pass.

    A single pass (rather than a per-token ``str.replace`` loop) means a value inserted for one
    token is never re-scanned for another token, so untrusted content that happens to contain a
    placeholder string cannot be re-expanded. The callable replacement also stops any ``\\g<1>``-
    style text inside a value from being interpreted as a regex backreference.
    """
    if not replacements:
        return template
    pattern = re.compile("|".join(re.escape(token) for token in replacements))
    return pattern.sub(lambda m: replacements[m.group(0)], template)


def _tail(text: str, limit: int = 800) -> str:
    """Return the last ``limit`` chars of ``text`` (errors are printed last)."""
    text = (text or "").strip()
    if not text:
        return "(empty)"
    return text if len(text) <= limit else "..." + text[-limit:]


def normalize_severity(value: str, default: str = "Info") -> str:
    """Map free-form severity text onto the canonical vocabulary, falling back to ``default``."""
    v = (value or "").strip().lower()
    if v in ("high", "critical"):
        return "High"
    if v == "medium":
        return "Medium"
    if v == "low":
        return "Low"
    if v in ("info", "informational", "none"):
        return "Info"
    return default


@dataclass(frozen=True)
class AgentResult:
    """Outcome of one Claude Code run."""

    ok: bool
    structured: dict[str, Any] | None  # schema-validated output (when a schema was given)
    text: str  # the CLI's final ``result`` text
    cost_usd: float
    duration_s: float
    error: str = ""  # why the run is not ``ok`` (timeout, exit code, CLI error, no output)


class ClaudeCodeCLI:
    """Launches hardened, non-interactive Claude Code runs. See the module docstring."""

    def __init__(
        self,
        model: str | None = None,
        command: str | None = None,
        api_key: str | None = None,
        effort: str | None = None,
    ) -> None:
        self.model = model if model is not None else config.claude_model
        self.command = command if command is not None else config.claude_command
        self.api_key = api_key if api_key is not None else config.claude_api_key
        self.effort = effort if effort is not None else config.claude_effort

    # --- launch ------------------------------------------------------------------------
    def build_argv(
        self,
        *,
        tools: Sequence[str],
        settings: dict[str, Any],
        json_schema: dict[str, Any] | None = None,
        max_budget_usd: float | None = None,
    ) -> list:
        """Return the full argv for one run (the prompt is fed on stdin)."""
        argv = [
            self.command,
            "-p",
            "--bare",
            "--restricted",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--permission-prompts",
            "none",
            "--output-format",
            "json",
            # "" means no built-in tools at all.
            "--tools",
            ",".join(tools),
            "--settings",
            json.dumps(settings, separators=(",", ":")),
        ]
        if self.model:
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        if json_schema is not None:
            argv += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
        if max_budget_usd:
            argv += ["--max-budget-usd", f"{max_budget_usd:g}"]
        return argv

    def child_env(self, home: Path, tmpdir: Path) -> dict[str, str]:
        """
        Build the *minimal* environment for the subprocess.

        The agent runs over untrusted repository code, so it must not inherit this process's
        secrets or the service user's real HOME (credentials, CLI config, plugins, memory).
        """
        env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
        env.update(
            {
                "HOME": str(home),
                "TMPDIR": str(tmpdir),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "SHELL": "/bin/bash",
                "ANTHROPIC_API_KEY": self.api_key or "",
                # Strip credentials from the environment of every command the agent runs.
                "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1",
                "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            }
        )
        return env

    async def run_agent(
        self,
        prompt: str,
        *,
        cwd: Path,
        home: Path,
        tmpdir: Path,
        tools: Sequence[str],
        settings: dict[str, Any],
        json_schema: dict[str, Any] | None = None,
        timeout_s: float,
        max_budget_usd: float | None = None,
        label: str = "agent",
    ) -> AgentResult:
        """Run one agent with ``prompt`` on stdin inside ``cwd`` and parse its JSON result."""
        argv = self.build_argv(
            tools=tools, settings=settings, json_schema=json_schema, max_budget_usd=max_budget_usd
        )
        logger.debug(
            "[%s] launching claude (cwd=%s, tools=%s)", label, cwd, ",".join(tools) or "none"
        )
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.child_env(home, tmpdir),
            # Own process group, so a timeout kills the agent *and* every command it spawned.
            start_new_session=True,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")), timeout=timeout_s
            )
        except TimeoutError:
            await self._kill_group(proc)
            logger.warning(
                "[%s] claude timed out after %ss; process group killed", label, timeout_s
            )
            return AgentResult(
                False, None, "", 0.0, time.monotonic() - started, f"timeout after {timeout_s}s"
            )
        except BaseException:
            # Cancellation (shutdown) or anything unexpected: never leave an agent running.
            await self._kill_group(proc)
            raise
        finally:
            # A finished CLI may still have stray descendants in its group; clean them up.
            self._signal_group(proc, signal.SIGKILL)

        duration = time.monotonic() - started
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return self._parse(
            label, proc.returncode, stdout, stderr, duration, json_schema is not None
        )

    # --- result parsing ----------------------------------------------------------------
    @staticmethod
    def _parse(
        label: str,
        returncode: int | None,
        stdout: str,
        stderr: str,
        duration: float,
        want_structured: bool,
    ) -> AgentResult:
        data: Any = None
        try:
            data = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict) or data.get("type") != "result":
            # e.g. the sandbox refused to start (sandbox.failIfUnavailable) or auth failed:
            # the CLI prints the reason on stderr and nothing parseable on stdout.
            error = f"exit {returncode}; stderr: {_tail(stderr)}"
            logger.warning("[%s] claude produced no result: %s", label, error)
            return AgentResult(False, None, "", 0.0, duration, error)

        cost = float(data.get("total_cost_usd") or 0.0)
        text = str(data.get("result") or "")
        structured = data.get("structured_output")
        if data.get("is_error") or returncode != 0:
            error = f"exit {returncode}; {data.get('subtype') or 'error'}: {_tail(text, 400)}"
            logger.warning("[%s] claude run failed: %s", label, error)
            return AgentResult(False, None, text, cost, duration, error)
        if want_structured and not isinstance(structured, dict):
            logger.warning("[%s] claude returned no structured output", label)
            return AgentResult(False, None, text, cost, duration, "no structured output")
        logger.info("[%s] claude finished in %.0fs (cost $%.2f)", label, duration, cost)
        return AgentResult(
            True, structured if isinstance(structured, dict) else None, text, cost, duration
        )

    # --- process cleanup ---------------------------------------------------------------
    @staticmethod
    def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError, PermissionError:
            pass

    async def _kill_group(self, proc: asyncio.subprocess.Process) -> None:
        self._signal_group(proc, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
        except TimeoutError:
            pass
        self._signal_group(proc, signal.SIGKILL)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_SECONDS)
        except TimeoutError:
            logger.error("claude process %s did not exit after SIGKILL", proc.pid)
