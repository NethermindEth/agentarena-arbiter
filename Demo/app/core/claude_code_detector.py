"""
Claude Code detector — classify a finding by driving the ``claude`` CLI as a subprocess.

This is the arbiter's evaluation engine. Instead of embedding concatenated contract source
into a prompt (the previous LangChain approach), it runs Anthropic's Claude Code CLI *inside
the repository checkout*, so the agent explores the real files itself and returns a single
JSON verdict.

    claude -p --output-format text --permission-mode acceptEdits [--model <model>]

with the rendered prompt fed on stdin and only ``ANTHROPIC_API_KEY`` added to a minimal,
secret-free environment. Classification reads the checked-out repo and also writes a scratch
``finding_data.json`` (its per-criterion working memory) into the checkout, so
``--permission-mode acceptEdits`` is used: it auto-accepts file edits/writes while still gating
bash and other tools. The checkout is a disposable, re-downloaded-per-task copy, so edits to it
are throwaway; the authoritative verdict is always parsed from *stdout*, never from that file.

Ported from ``finding-validator-result``'s ``detector.py`` and extended to also return a
re-assessed ``severity`` alongside the ``approved`` / ``disapproved`` label.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.config import config

logger = logging.getLogger(__name__)

# The reviewer-verdict vocabulary. "approved" is the positive class (a valid finding);
# "disapproved" is the negative class (invalid / abstained).
POSITIVE_LABEL = "approved"
NEGATIVE_LABEL = "disapproved"

# The severity vocabulary the model may return (mirrors app.models.finding_input.Severity).
VALID_SEVERITIES = ("High", "Medium", "Low", "Info")

# Scratch working-memory file the prompt asks the agent to write into the checkout. It is
# removed after each classification so checkouts are not left littered between findings.
SCRATCH_FILENAME = "finding_data.json"

# Matches the first ```json ... ``` fenced block, else we fall back to a bare object.
_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_BARE = re.compile(r"\{.*\}", re.DOTALL)


def _json_block(obj: dict) -> str:
    """
    Serialize untrusted, attacker-influenced data for safe embedding in the prompt.

    ``ensure_ascii=True`` (the default) escapes quotes, backslashes, newlines *and every
    non-ASCII character* (including the U+2028/U+2029 line separators that JSON leaves raw
    otherwise). The result is therefore single-physical-line-per-value pure ASCII, so a value
    cannot terminate the surrounding ```json fence, start a new markdown heading, or otherwise
    escape the block it is placed in. ``indent=2`` only adds structural newlines between keys,
    never inside an escaped value.
    """
    return json.dumps(obj, indent=2)


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


@dataclass(frozen=True)
class Verdict:
    """A single classification outcome parsed from the detector's output."""

    label: str
    severity: str
    confidence: float
    rationale: str
    raw: str = ""


class ClaudeCodeDetector:
    """
    Drives Anthropic's Claude Code CLI in non-interactive print mode to judge one finding.

    The subprocess is fed untrusted third-party code, so its environment is stripped of this
    process's secrets (see :meth:`_child_env`). It runs with ``--permission-mode acceptEdits``
    so it can write its scratch ``finding_data.json`` into the checkout; for defence in depth,
    operators should still run it inside a disposable sandbox (no network beyond the Anthropic
    API, and only the throwaway per-task checkout mounted writable — nothing else).

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
        """
        Return the command + flags used to run Claude Code in non-interactive print mode.

        ``--permission-mode acceptEdits`` lets the agent write its scratch ``finding_data.json``
        working-memory file into the checkout while still gating bash and other tools; the
        verdict is read from stdout regardless.
        """
        argv = [self.command, "-p", "--output-format", "text", "--permission-mode", "acceptEdits"]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def env(self) -> dict:
        """Return the extra environment (the API key) for the subprocess."""
        return {"ANTHROPIC_API_KEY": self.api_key or ""}

    # --- shared behavior ---------------------------------------------------------------
    async def classify(
        self,
        prompt_text: str,
        *,
        finding_title: str,
        finding_description: str,
        finding_severity: str,
        finding_file_paths: List[str],
        task_title: str,
        task_description: str,
        repo_path: Path,
        in_scope_files: List[str],
        in_scope_docs: List[str]
    ) -> Verdict:
        """
        Render the prompt, run the detector in ``repo_path``, and parse its verdict.

        Retries once on an unparseable response; a persistent failure is treated as an
        abstention and counted as the negative class, keeping the finding's claimed severity.
        The agent's scratch ``finding_data.json`` is always removed from the checkout once the
        verdict is decided (see :meth:`_cleanup_scratch`).
        """

        rendered = self.render_prompt(
            prompt_text,
            finding_title=finding_title,
            finding_description=finding_description,
            finding_severity=finding_severity,
            finding_file_paths=finding_file_paths,
            task_title=task_title,
            task_description=task_description,
            repo_path=repo_path,
            in_scope_files=in_scope_files,
            in_scope_docs=in_scope_docs
        )
        default_severity = _normalize_severity(finding_severity)
        last_raw = ""
        try:
            for _ in range(2):
                last_raw = await self._run(rendered, repo_path)
                verdict = self._parse(last_raw, default_severity)
                if verdict is not None:
                    return verdict
            logger.warning("Claude Code produced an unparseable verdict; abstaining to %s.", NEGATIVE_LABEL)
            return Verdict(
                label=NEGATIVE_LABEL,
                severity=default_severity,
                confidence=0.0,
                rationale="unparseable detector output (abstained)",
                raw=last_raw,
            )
        finally:
            self._cleanup_scratch(repo_path)

    def _cleanup_scratch(self, repo_path: Path) -> None:
        """Remove the agent's scratch ``finding_data.json`` from the checkout, if present."""
        scratch = Path(repo_path) / SCRATCH_FILENAME
        try:
            scratch.unlink()
            logger.debug("removed scratch file %s", scratch)
        except FileNotFoundError:
            pass  # the agent may not have written it (e.g. an early CLI failure)
        except OSError as exc:
            logger.warning("could not remove scratch file %s: %s", scratch, exc)

    def render_prompt(
        self,
        prompt_text: str,
        *,
        finding_title: str,
        finding_description: str,
        finding_severity: str,
        finding_file_paths: List[str],
        task_title: str,
        task_description: str,
        repo_path: Path,
        in_scope_files: List[str],
        in_scope_docs: List[str]
    ) -> str:
        """
        Fill the template placeholders (see evaluation_prompt.py for the token list).

        The task metadata and finding are attacker-influenced (a participant controls the finding
        text; the sponsor controls the task metadata), so they are never spliced into the prompt as
        raw prose. They are injected only as JSON-serialized values via :func:`_json_block`, which
        escapes quotes, backslashes and — crucially — newlines. That confines each value to a single
        physical line and pure-ASCII output, so it cannot break out of its fenced block or introduce
        new markdown/instructions; the template's prose tells the model to treat those blocks as data.

        The substitution itself is a single regex pass with a *callable* replacement, so an injected
        placeholder token (e.g. a literal ``{{FINDING_JSON}}`` inside a description) is not re-expanded
        and a JSON value containing ``\\g<1>``-style text is not interpreted as a backreference.
        """
        task_json = _json_block(
            {
                "title": task_title or "(no title)",
                "description": task_description or "(no description)",
                "in_scope_files": list(in_scope_files) or "WHOLE REPOSITORY CODE",
                "in_scope_docs": list(in_scope_docs) or "WHOLE REPOSITORY DOCS",
            }
        )
        finding_json = _json_block(
            {
                "title": finding_title or "(no title)",
                "claimed_severity": finding_severity or "unknown",
                "referenced_files": list(finding_file_paths or []),
                "description": finding_description or "(no description)",
            }
        )
        replacements = {
            "{{REPO_PATH}}": str(repo_path),
            "{{TASK_JSON}}": task_json,
            "{{FINDING_JSON}}": finding_json,
            "{{POSITIVE_LABEL}}": POSITIVE_LABEL,
            "{{NEGATIVE_LABEL}}": NEGATIVE_LABEL,
            "{{VALID_SEVERITIES}}": ", ".join(VALID_SEVERITIES),
        }
        pattern = re.compile("|".join(re.escape(token) for token in replacements))
        return pattern.sub(lambda m: replacements[m.group(0)], prompt_text)

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
        Build a *minimal* environment for the detector subprocess.

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

    def _parse(self, raw: str, default_severity: str) -> Optional[Verdict]:
        """Extract and validate a JSON verdict from raw detector output."""
        match = _JSON_FENCE.search(raw) or _JSON_BARE.search(raw)
        if not match:
            return None
        blob = match.group(1) if match.re is _JSON_FENCE else match.group(0)
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return None

        label = self._normalize_label(str(data.get("label", "")))
        if label is None:
            return None
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return Verdict(
            label=label,
            severity=_normalize_severity(str(data.get("severity", "")), default_severity),
            confidence=max(0.0, min(1.0, confidence)),
            rationale=str(data.get("rationale", "")),
            raw=raw,
        )

    def _normalize_label(self, value: str) -> Optional[str]:
        """Map free-form label text onto the positive/negative label."""
        v = value.strip().lower()
        if not v:
            return None
        if v == POSITIVE_LABEL:
            return POSITIVE_LABEL
        if v == NEGATIVE_LABEL:
            return NEGATIVE_LABEL
        # Tolerate common synonyms so a slightly-off answer still resolves.
        positive_synonyms = {"valid", "true", "yes", "approve"}
        negative_synonyms = {"invalid", "false", "no", "reject", "rejected", "disapprove"}
        if v in positive_synonyms:
            return POSITIVE_LABEL
        if v in negative_synonyms:
            return NEGATIVE_LABEL
        return None