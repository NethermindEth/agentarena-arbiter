"""
Claude Code aggregator — merge a group of duplicate findings into a single digest finding.

When deduplication groups several findings as the same underlying issue, evaluating each in turn
is wasteful and loses information: each reviewer typically emphasized a different consequence or
affected asset. This service asks Claude (via the same ``claude`` CLI plumbing the detector uses)
to fold the whole group into ONE consolidated finding — a shared root cause with the distinct
consequences unioned together — which the evaluator then classifies as a single finding.

It runs read-only (``--permission-mode plan``): the merge reasons purely over the finding texts
supplied in the prompt, so the agent needs to write nothing into the checkout.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.core.aggregation_prompt import AGGREGATION_PROMPT
from app.core.claude_code_cli import (
    ClaudeCodeCLI,
    VALID_SEVERITIES,
    json_block,
    normalize_severity,
    render_template,
)

logger = logging.getLogger(__name__)

# Matches the first ```json ... ``` fenced block, else we fall back to a bare object.
_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_BARE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class AggregatedFinding:
    """The consolidated finding produced by merging a group of duplicates."""

    title: str
    description: str
    severity: str
    file_paths: List[str] = field(default_factory=list)
    rationale: str = ""
    raw: str = ""


class ClaudeCodeAggregator(ClaudeCodeCLI):
    """Drives the ``claude`` CLI to merge a group of duplicate findings into one digest finding."""

    # --- CLI setup ---------------------------------------------------------------------
    def argv(self) -> List[str]:
        """
        Run the CLI in a strictly read-only pass.

        Aggregation reasons only over the finding texts embedded in the prompt, so the agent
        writes nothing; ``--permission-mode plan`` keeps it from editing the checkout.
        """
        return self._argv("plan")

    # --- shared behavior ---------------------------------------------------------------
    async def aggregate(
        self,
        *,
        findings: List[Dict[str, object]],
        task_title: str,
        task_description: str,
        repo_path: Optional[str],
        default_severity: str,
    ) -> Optional[AggregatedFinding]:
        """
        Merge ``findings`` (dicts of title/description/severity/file_paths) into one finding.

        Retries once on an unparseable response; returns ``None`` if the merge cannot be parsed
        so the caller can fall back to the representative finding. The referenced file paths of
        every input are always unioned into the result so no evidence is dropped even if the
        model omits some.
        """

        rendered = self.render_prompt(
            findings=findings,
            task_title=task_title,
            task_description=task_description,
        )
        # The merge is text-only; run inside the checkout if we have one, else the CWD.
        cwd = Path(repo_path) if repo_path and Path(repo_path).is_dir() else Path(".")

        for _ in range(2):
            last_raw = await self._run(rendered, cwd)
            parsed = self._parse(last_raw, default_severity, findings)
            if parsed is not None:
                return parsed
        logger.warning(
            "Claude Code produced an unparseable merged finding for a group of %d; "
            "caller will fall back to the representative finding.",
            len(findings),
        )
        return None

    def render_prompt(
        self,
        *,
        findings: List[Dict[str, object]],
        task_title: str,
        task_description: str,
    ) -> str:
        """
        Fill the template placeholders (see aggregation_prompt.py for the token list).

        The task metadata and every finding are attacker-influenced, so they are never spliced into
        the prompt as raw prose: they are injected only as JSON-serialized values via
        :func:`json_block` (which escapes quotes, backslashes and newlines, confining each value to
        a single physical line) and substituted in a single regex pass via :func:`render_template`
        (so an injected placeholder token inside a value is not re-expanded). See the detector's
        ``render_prompt`` for the same hardening on the classification side.
        """
        task_json = json_block(
            {
                "title": task_title or "(no title)",
                "description": task_description or "(no description)",
            }
        )
        findings_json = json_block(
            [
                {
                    "title": finding.get("title", ""),
                    "claimed_severity": finding.get("severity", "unknown"),
                    "referenced_files": [str(p) for p in (finding.get("file_paths") or [])],
                    "description": finding.get("description", ""),
                }
                for finding in findings
            ]
        )
        replacements = {
            "{{TASK_JSON}}": task_json,
            "{{FINDINGS_JSON}}": findings_json,
            "{{VALID_SEVERITIES}}": ", ".join(VALID_SEVERITIES),
        }
        return render_template(AGGREGATION_PROMPT, replacements)

    def _parse(
        self,
        raw: str,
        default_severity: str,
        findings: List[Dict[str, object]],
    ) -> Optional[AggregatedFinding]:
        """
        Extract and validate the merged finding JSON from raw output.
        """

        match = _JSON_FENCE.search(raw) or _JSON_BARE.search(raw)
        if not match:
            return None

        blob = match.group(1) if match.re is _JSON_FENCE else match.group(0)
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return None

        title = str(data.get("title", "")).strip()
        description = str(data.get("description", "")).strip()
        # A merge with no title or body is useless; treat it as unparseable so we retry/fall back.
        if not title or not description:
            return None

        severity = normalize_severity(str(data.get("severity", "")), default_severity)
        file_paths = self._union_file_paths(data.get("file_paths"), findings)
        return AggregatedFinding(
            title=title,
            description=description,
            severity=severity,
            file_paths=file_paths,
            rationale=str(data.get("rationale", "")),
            raw=raw,
        )

    @staticmethod
    def _union_file_paths(
        model_paths: object,
        findings: List[Dict[str, object]],
    ) -> List[str]:
        """
        Union the model's file paths with every input finding's, preserving first-seen order.
        """

        ordered: List[str] = []
        seen = set()

        def add(paths: object) -> None:
            if not isinstance(paths, (list, tuple)):
                return
            for p in paths:
                text = str(p).strip()
                if text and text not in seen:
                    seen.add(text)
                    ordered.append(text)

        add(model_paths)
        for finding in findings:
            add(finding.get("file_paths"))
        return ordered
