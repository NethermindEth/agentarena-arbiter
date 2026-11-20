"""
Claude Code aggregator — merge a group of duplicate findings into a single digest finding.

When deduplication groups several findings as the same underlying issue, evaluating each in turn
is wasteful and loses information: each reviewer typically emphasized a different consequence or
affected asset. This service asks Claude (through the same hardened ``claude`` CLI plumbing the
triage agents use) to fold the whole group into ONE consolidated finding — a shared root cause
with the distinct consequences unioned together — which the evaluator then triages as a unit.

The merge reasons purely over the finding texts in the prompt, so it runs with **no tools at
all**, in an empty temporary directory (never inside the untrusted repository checkout).
"""

from __future__ import annotations

import logging
import tempfile
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

AGGREGATION_TIMEOUT_SECONDS = 600

AGGREGATION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "severity": {"type": "string", "enum": list(VALID_SEVERITIES)},
        "file_paths": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["title", "severity", "file_paths", "description", "rationale"],
}


@dataclass(frozen=True)
class AggregatedFinding:
    """The consolidated finding produced by merging a group of duplicates."""

    title: str
    description: str
    severity: str
    file_paths: List[str] = field(default_factory=list)
    rationale: str = ""


class ClaudeCodeAggregator:
    """Drives the ``claude`` CLI to merge a group of duplicate findings into one digest finding."""

    def __init__(self, cli: Optional[ClaudeCodeCLI] = None) -> None:
        self.cli = cli or ClaudeCodeCLI()

    async def aggregate(
        self,
        *,
        findings: List[Dict[str, object]],
        task_title: str,
        task_description: str,
        default_severity: str,
    ) -> Optional[AggregatedFinding]:
        """
        Merge ``findings`` (dicts of title/description/severity/file_paths) into one finding.

        Retries once; returns ``None`` if no valid merge comes back so the caller can fall back
        to the representative finding. The referenced file paths of every input are always
        unioned into the result so no evidence is dropped even if the model omits some.
        """
        rendered = self.render_prompt(findings=findings, task_title=task_title, task_description=task_description)
        for attempt in range(2):
            with tempfile.TemporaryDirectory(prefix="arbiter-aggregate-") as tmp:
                root = Path(tmp)
                for sub in ("cwd", "home", "tmp"):
                    (root / sub).mkdir()
                res = await self.cli.run_agent(
                    rendered,
                    cwd=root / "cwd", home=root / "home", tmpdir=root / "tmp",
                    tools=[],
                    settings={"permissions": {"deny": ["WebFetch", "WebSearch"]}},
                    json_schema=AGGREGATION_SCHEMA,
                    timeout_s=AGGREGATION_TIMEOUT_SECONDS,
                    label=f"aggregate (attempt {attempt + 1})",
                )
            parsed = self._parse(res.structured, default_severity, findings) if res.ok else None
            if parsed is not None:
                return parsed
        logger.warning(
            "Claude Code produced no valid merged finding for a group of %d; "
            "caller will fall back to the representative finding.",
            len(findings),
        )
        return None

    def render_prompt(self, *, findings: List[Dict[str, object]], task_title: str, task_description: str) -> str:
        """
        Fill the template placeholders (see aggregation_prompt.py for the token list).

        The task metadata and every finding are attacker-influenced, so they are injected only as
        JSON-serialized values via :func:`json_block` (escaped quotes/backslashes/newlines: each
        value stays on one physical line inside its fence) and substituted in a single regex pass
        via :func:`render_template` (an injected placeholder token is not re-expanded). Escaping
        prevents structural breakout only; the prompt still tells the model to treat the blocks as
        data, and the downstream triage verifies every claim against the code.
        """
        task_json = json_block({"title": task_title or "(no title)", "description": task_description or "(no description)"})
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
        data: Optional[Dict[str, object]],
        default_severity: str,
        findings: List[Dict[str, object]],
    ) -> Optional[AggregatedFinding]:
        """Validate the structured merge result."""
        if not isinstance(data, dict):
            return None
        title = str(data.get("title", "")).strip()
        description = str(data.get("description", "")).strip()
        # A merge with no title or body is useless; treat it as a failure so we retry/fall back.
        if not title or not description:
            return None
        return AggregatedFinding(
            title=title,
            description=description,
            severity=normalize_severity(str(data.get("severity", "")), default_severity),
            file_paths=self._union_file_paths(data.get("file_paths"), findings),
            rationale=str(data.get("rationale", "")),
        )

    @staticmethod
    def _union_file_paths(model_paths: object, findings: List[Dict[str, object]]) -> List[str]:
        """Union the model's file paths with every input finding's, preserving first-seen order."""
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
