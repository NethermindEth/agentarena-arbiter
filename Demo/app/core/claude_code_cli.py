"""
Shared plumbing for driving Anthropic's Claude Code CLI as a subprocess.

Both the finding *classifier* (:class:`app.core.claude_code_detector.ClaudeCodeDetector`)
and the finding *aggregator* (:class:`app.core.claude_code_aggregator.ClaudeCodeAggregator`)
run the same ``claude`` CLI in non-interactive print mode against a repository checkout, feed a
rendered prompt on stdin, and read the answer from stdout. The only differences are the prompt,
the permission mode, and how the stdout is parsed — so the process-launching, secret-free
environment, and stderr-logging concerns live here and are shared, while each concrete service
owns its own prompt/parse logic.
"""


from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
from typing import List, Optional
from app.config import config


logger = logging.getLogger(__name__)


# The severity vocabulary the models may return (mirrors app.models.finding_input.Severity).
VALID_SEVERITIES = ("High", "Medium", "Low", "Info")


def _stderr_tail(stderr: str, limit: int = 800) -> str:
    """
    Return the last ``limit`` chars of ``stderr`` for logging a CLI failure.

    Errors are emitted at the end of the stream (after any startup banner), so we keep the
    tail rather than the head; a leading ellipsis marks truncation.
    """
    text = (stderr or "").strip()
    if not text:
        return "(empty)"
    return text if len(text) <= limit else "..." + text[-limit:]


def _normalize_severity(value: str, default: str = "Info") -> str:
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


class ClaudeCodeCLI:
    """
    Base class that launches the ``claude`` CLI in non-interactive print mode.

    The subprocess is fed untrusted third-party code, so its environment is stripped of this
    process's secrets (see :meth:`_child_env`); only the Anthropic API key is passed through.
    Concrete subclasses implement :meth:`argv` (choosing their permission mode via
    :meth:`_argv`) and their own prompt rendering / output parsing.

    NOTE: exact flags can vary by CLI version; override the binary via ``CLAUDE_CMD``.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        command: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.model = model if model is not None else config.claude_model
        self.command = command if command is not None else config.claude_command
        self.api_key = api_key if api_key is not None else config.claude_api_key

    # --- CLI setup ---------------------------------------------------------------------
    def argv(self) -> List[str]:
        """Return the command + flags used to run the CLI. Implemented by subclasses."""
        raise NotImplementedError

    def _argv(self, permission_mode: str) -> List[str]:
        """
        Build the common non-interactive print-mode argv with the given ``permission_mode``.

        ``permission_mode`` selects how the agent may act (e.g. ``acceptEdits`` to let it write
        a scratch file, ``plan`` for a strictly read-only pass).
        """
        argv = [self.command, "-p", "--output-format", "text", "--permission-mode", permission_mode]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def env(self) -> dict:
        """Return the extra environment (the API key) for the subprocess."""
        return {"ANTHROPIC_API_KEY": self.api_key or ""}

    # --- process execution -------------------------------------------------------------
    async def _run(self, rendered_prompt: str, repo_path: Path) -> str:
        """Run the CLI with the prompt on stdin, inside the repo checkout; return stdout."""
        environ = self._child_env()
        argv = self.argv()
        logger.debug("running %s (cwd=%s)", " ".join(argv), repo_path)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(repo_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environ,
        )
        stdout_b, stderr_b = await proc.communicate(rendered_prompt.encode("utf-8"))
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        # A non-zero exit (or empty stdout) usually means auth/config failure; the caller can
        # only see an unparseable response, so log the cause here where it's still visible. We
        # surface the *tail* of stderr, not the head, so a leading session banner can't hide
        # the real error.
        if proc.returncode != 0:
            logger.warning(
                "claude CLI exited %s; stderr (tail): %s",
                proc.returncode, _stderr_tail(stderr),
            )
        elif not stdout.strip():
            logger.warning("claude CLI produced empty stdout; stderr (tail): %s", _stderr_tail(stderr))
        return stdout

    def _child_env(self) -> dict:
        """
        Build a *minimal* environment for the subprocess.

        The subprocess runs an autonomous agent over untrusted, third-party repository code,
        so it must not inherit this process's secrets. We pass only a small allowlist needed
        to run the CLI (PATH, HOME for its own auth/config, locale) plus the Anthropic API key.
        """
        passthrough = (
            "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
            "TMPDIR", "USER", "SHELL", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
        )
        env = {k: os.environ[k] for k in passthrough if k in os.environ}
        env.update(self.env())  # only the Anthropic API key
        return env
