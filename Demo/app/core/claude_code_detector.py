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

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.core.claude_code_cli import ClaudeCodeCLI, VALID_SEVERITIES, _normalize_severity

logger = logging.getLogger(__name__)

# The reviewer-verdict vocabulary. "approved" is the positive class (a valid finding);
# "disapproved" is the negative class (invalid / abstained).
POSITIVE_LABEL = "approved"
NEGATIVE_LABEL = "disapproved"

# Scratch working-memory file the prompt asks the agent to write into the checkout. It is
# removed after each classification so checkouts are not left littered between findings.
SCRATCH_FILENAME = "finding_data.json"

# Matches the first ```json ... ``` fenced block, else we fall back to a bare object.
_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_BARE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class Verdict:
    """A single classification outcome parsed from the detector's output."""

    label: str
    severity: str
    confidence: float
    rationale: str
    raw: str = ""


class ClaudeCodeDetector(ClaudeCodeCLI):
    """
    Drives Anthropic's Claude Code CLI in non-interactive print mode to judge one finding.

    The subprocess is fed untrusted third-party code, so its environment is stripped of this
    process's secrets (see :meth:`ClaudeCodeCLI._child_env`). It runs with
    ``--permission-mode acceptEdits`` so it can write its scratch ``finding_data.json`` into the
    checkout; for defence in depth, operators should still run it inside a disposable sandbox (no
    network beyond the Anthropic API, and only the throwaway per-task checkout mounted writable —
    nothing else).

    NOTE: exact flags can vary by CLI version; override the binary via ``CLAUDE_CMD``.
    """

    # --- CLI setup ---------------------------------------------------------------------
    def argv(self) -> List[str]:
        """
        Return the command + flags used to run Claude Code in non-interactive print mode.

        ``--permission-mode acceptEdits`` lets the agent write its scratch ``finding_data.json``
        working-memory file into the checkout while still gating bash and other tools; the
        verdict is read from stdout regardless.
        """
        return self._argv("acceptEdits")

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
        """Fill the template placeholders (see evaluation_prompt.py for the token list)."""
        scope_files = (
            "\n".join(f"- {p}" for p in in_scope_files)
            if in_scope_files
            else "(whole repository code)"
        )
        scope_docs = (
            "\n".join(f"- {p}" for p in in_scope_docs)
            if in_scope_docs
            else "(whole repository docs)"
        )
        replacements = {
            "{{TASK_TITLE}}": task_title or "(no title)",
            "{{TASK_DESCRIPTION}}": task_description or "(no description)",
            "{{IN_SCOPE_FILES}}": scope_files,
            "{{IN_SCOPE_DOCS}}": scope_docs,
            "{{REPO_PATH}}": str(repo_path),
            "{{FINDING_TITLE}}": finding_title,
            "{{FINDING_DESCRIPTION}}": finding_description,
            "{{FINDING_SEVERITY}}": finding_severity or "unknown",
            "{{FINDING_FILE_PATHS}}": ", ".join(finding_file_paths) or "(none listed)",
            "{{POSITIVE_LABEL}}": POSITIVE_LABEL,
            "{{NEGATIVE_LABEL}}": NEGATIVE_LABEL,
            "{{VALID_SEVERITIES}}": ", ".join(VALID_SEVERITIES),
        }
        rendered = prompt_text
        for token, value in replacements.items():
            rendered = rendered.replace(token, value)
        return rendered

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