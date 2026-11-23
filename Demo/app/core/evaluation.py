import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.claude_code_aggregator import ClaudeCodeAggregator
from app.core.claude_code_cli import json_block
from app.core.claude_code_triage import ClaudeCodeTriage, TriageUnit, TriageVerdict
from app.core.gemini_model import DuplicateFinding
from app.models.finding_db import FindingDB, Status
from app.models.finding_input import Severity
from app.types import QAPair, TaskCache

logger = logging.getLogger(__name__)

# Participant-written text is capped before it reaches any prompt (a huge description could
# otherwise make every agent of a unit fail).
MAX_TITLE_CHARS = 500
MAX_DESCRIPTION_CHARS = 30_000
# So is the task's own text: it goes into every agent prompt of every unit and into each merge,
# and an oversized task would fail on every retry. Sizes are of the JSON the prompt embeds.
MAX_TASK_DESCRIPTION_JSON_CHARS = 20_000
MAX_TASK_DOCS_JSON_CHARS = 40_000
MAX_QA_QUESTION_JSON_CHARS = 1_000
MAX_QA_ANSWER_JSON_CHARS = 5_000
MAX_QA_JSON_CHARS = 20_000
# A duplicate group's original reports go into every agent prompt next to the merged digest, so
# they get a tighter budget. Sizes are those of the JSON the prompt embeds (json_block escapes
# every non-ASCII character, so one character can take six).
MAX_MEMBER_REPORTS = 10
MAX_MEMBER_REPORT_JSON_CHARS = 8_000
MAX_MEMBER_REPORTS_JSON_CHARS = 60_000
# The aggregator sees every member it can, each shortened to an equal share of this budget.
MAX_AGGREGATION_JSON_CHARS = 200_000
MIN_AGGREGATION_MEMBER_JSON_CHARS = 2_000
_TRUNCATED = " ...(truncated by the arbiter)"
_SEVERITY_RANK = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + _TRUNCATED


def _fit_description(finding: dict[str, Any], limit: int) -> dict[str, Any]:
    """
    Return ``finding`` with its description shortened until its JSON fits in ``limit`` chars.

    A finding whose other fields alone exceed the limit keeps only the truncation marker.
    """
    if len(json_block(finding)) <= limit:
        return finding
    description = str(finding.get("description", "")).removesuffix(_TRUNCATED)
    kept = _longest_fitting_prefix(
        description,
        lambda prefix: len(json_block({**finding, "description": prefix + _TRUNCATED})) <= limit,
    )
    return {**finding, "description": kept + _TRUNCATED}


def _fit_text(text: str, limit: int) -> str:
    """``text``, shortened (with the truncation marker) until its JSON form fits in ``limit``."""
    if len(json_block(text)) <= limit:
        return text
    kept = _longest_fitting_prefix(
        text, lambda prefix: len(json_block(prefix + _TRUNCATED)) <= limit
    )
    return kept + _TRUNCATED


def _longest_fitting_prefix(text: str, fits: Callable[[str], bool]) -> str:
    """
    The longest prefix of ``text`` that ``fits`` accepts (``fits`` must accept every prefix of
    an accepted one). Searched rather than computed from the overflow: json_block spends one to
    six characters on each character, so the overflow does not say how much text to drop.
    """
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(text[:mid]):
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _capped_task(task_cache: TaskCache) -> TaskCache:
    """
    A copy of the task whose sponsor-written text fits the budgets above. Q&A pairs are kept
    in order while they fit; a final pair says how many were left out.
    """
    pairs = task_cache.qaResponses or []
    qa: list[QAPair] = []
    used = 0
    for pair in pairs:
        fitted = QAPair(
            question=_fit_text(pair.question, MAX_QA_QUESTION_JSON_CHARS),
            answer=_fit_text(pair.answer, MAX_QA_ANSWER_JSON_CHARS),
        )
        size = len(json_block(fitted.model_dump()))
        if used + size > MAX_QA_JSON_CHARS:
            break
        qa.append(fitted)
        used += size
    if omitted := len(pairs) - len(qa):
        qa.append(
            QAPair(
                question="(note from the arbiter)",
                answer=f"{omitted} more Q&A pair(s) were left out for length.",
            )
        )
    title, description, docs = task_cache.title, task_cache.description, task_cache.additionalDocs
    return task_cache.model_copy(
        update={
            "title": _cap(title, MAX_TITLE_CHARS) if title else title,
            "description": (
                _fit_text(description, MAX_TASK_DESCRIPTION_JSON_CHARS)
                if description
                else description
            ),
            "additionalDocs": _fit_text(docs, MAX_TASK_DOCS_JSON_CHARS) if docs else docs,
            "qaResponses": qa,
        }
    )


def _union_paths(*path_lists: list[str] | None) -> list[str]:
    """The distinct non-empty paths of all lists, in first-seen order."""
    return list(dict.fromkeys(p for paths in path_lists for p in (paths or []) if p))


class FindingEvaluation(BaseModel):
    """One finding's verdict, as set on the in-memory finding before the run's single post."""

    finding_id: str = Field(description="ID of the evaluated finding")
    is_valid: bool = Field(description="Whether the finding represents a valid security issue")
    severity: str = Field(description="Severity level: High, Medium, Low, or Info")
    comment: str = Field(description="Brief explanation of the evaluation")


class FindingEvaluator:
    """
    Final evaluation of security findings with the findings-triage pipeline.

    Each distinct issue (a finding, or a duplicate group merged into one digest) is triaged by a
    lead analyst with a PoC, two adversarial verifiers and a judge (see
    :mod:`app.core.claude_code_triage`); the verdict (validity + severity) is applied to every
    member of the group. Verdicts are set on the in-memory findings only; the caller writes them
    to the backend.
    """

    def __init__(self, batch_size: int = 10):
        """
        Initialize the finding evaluator.

        Args:
            batch_size: Maximum number of individual findings per grouping batch
        """
        self.triage = ClaudeCodeTriage()
        self.aggregator = ClaudeCodeAggregator()
        self.batch_size = batch_size

    def preflight(self) -> None:
        """
        Check the host can run the triage before any work starts.

        Raises ``SandboxUnavailable`` (no CLI or no PoC sandbox) or ``ValueError``
        (misconfiguration); callers must then leave the task unprocessed.
        """
        self.triage.preflight()

    def _normalize_severity(self, severity_text: str) -> Severity:
        """
        Normalize severity text to Severity enum.

        Args:
            severity_text: Severity as string

        Returns:
            Severity enum value
        """
        severity_lower = severity_text.lower().strip()

        if severity_lower == "info":
            return Severity.INFO
        elif severity_lower == "low":
            return Severity.LOW
        elif severity_lower == "medium":
            return Severity.MEDIUM
        elif severity_lower in ["high", "critical"]:
            return Severity.HIGH
        else:
            return Severity.LOW  # Default fallback

    def group_findings_for_evaluation(
        self, findings: list[FindingDB], duplicate_relationships: list[DuplicateFinding]
    ) -> tuple[list[list[FindingDB]], list[list[FindingDB]]]:
        """
        Group findings for evaluation based on duplicate relationships.

        Relationships are merged transitively, in any order: "A duplicates B" and "B duplicates
        C" make one group. Each group starts with its root original, the finding the others
        duplicate, and lists the rest in the order of ``findings``. Findings in no relationship
        are batched by ``batch_size``.

        Args:
            findings: List of findings to group
            duplicate_relationships: List of duplicate relationships from deduplication

        Returns:
            ``(related_groups, individual_batches)``
        """
        finding_map = {f.str_id: f for f in findings}
        # Union-find over finding ids: a duplicate's root is linked under its original's root.
        parent: dict[str, str] = {}

        def root(finding_id: str) -> str:
            while finding_id in parent:
                finding_id = parent[finding_id]
            return finding_id

        linked: set[str] = set()
        for rel in duplicate_relationships:
            duplicate_id, original_id = rel.findingId, rel.duplicateOf
            if duplicate_id not in finding_map:
                logger.warning(f"Duplicate ID {duplicate_id} not found in findings list")
                continue
            if original_id not in finding_map:
                logger.warning(f"Original ID {original_id} not found in findings list")
                continue
            if duplicate_id == original_id:
                continue
            linked.update((duplicate_id, original_id))
            duplicate_root, original_root = root(duplicate_id), root(original_id)
            if duplicate_root != original_root:
                parent[duplicate_root] = original_root

        groups: dict[str, list[FindingDB]] = {}
        for finding in findings:
            if finding.str_id in linked:
                groups.setdefault(root(finding.str_id), []).append(finding)
        related_findings_groups = [
            sorted(group, key=lambda f, r=root_id: f.str_id != r)  # stable: the root first
            for root_id, group in groups.items()
        ]

        remaining_findings = [f for f in findings if f.str_id not in linked]
        individual_findings_groups = [
            remaining_findings[i : i + self.batch_size]
            for i in range(0, len(remaining_findings), self.batch_size)
        ]

        logger.info(
            f"Created {len(related_findings_groups)} related findings groups and {len(individual_findings_groups)} individual findings groups from {len(findings)} findings"
        )

        return related_findings_groups, individual_findings_groups

    @staticmethod
    def _finding_json(finding: FindingDB) -> dict[str, Any]:
        """The untrusted finding fields shown to the triage agents."""
        return {
            "title": _cap(finding.title or "(no title)", MAX_TITLE_CHARS),
            "claimed_severity": FindingEvaluator._severity_str(finding.severity) or "unknown",
            "referenced_files": [_cap(str(p), 300) for p in (finding.file_paths or [])[:50]],
            "description": _cap(finding.description or "(no description)", MAX_DESCRIPTION_CHARS),
        }

    async def _build_unit(
        self, key: str, group: list[FindingDB], task_cache: TaskCache
    ) -> TriageUnit:
        """
        Turn one group into a triage unit.

        A duplicate group is merged into one digest finding (see :meth:`_aggregate`), and the
        original member reports are attached too, so the agents judge the strongest correct
        version of the issue instead of trusting the merge (or a planted duplicate's wording).
        """
        if len(group) == 1:
            return TriageUnit(key, self._finding_json(group[0]), self._highest_claim(group))
        merged = await self._aggregate(group, task_cache)
        return self._group_unit(key, group, merged)

    def _group_unit(self, key: str, group: list[FindingDB], merged: FindingDB) -> TriageUnit:
        """A duplicate group's unit: the merged finding plus the members' original reports."""
        finding = self._finding_json(merged)
        finding["member_reports"], finding["member_reports_omitted"] = self._member_reports(group)
        return TriageUnit(key, finding, self._highest_claim(group))

    def _highest_claim(self, group: list[FindingDB]) -> str:
        """
        The highest severity any of the group's reports claims. It decides whether the unit gets
        PoCs, so the merge cannot talk a group out of them.
        """
        claims = (self._normalize_severity(self._severity_str(f.severity)) for f in group)
        return max(claims, key=_SEVERITY_RANK.__getitem__).value

    def _member_reports(self, group: list[FindingDB]) -> tuple[list[dict[str, Any]], int]:
        """
        The members' reports for the agents, shortened and capped in number and total size.

        Returns ``(reports, omitted)``, where ``omitted`` counts the members left out.
        """
        reports: list[dict[str, Any]] = []
        used = 0
        for finding in group:
            if len(reports) >= MAX_MEMBER_REPORTS:
                break
            report = _fit_description(self._finding_json(finding), MAX_MEMBER_REPORT_JSON_CHARS)
            size = len(json_block(report))
            if used + size > MAX_MEMBER_REPORTS_JSON_CHARS:
                continue
            reports.append(report)
            used += size
        return reports, len(group) - len(reports)

    def _aggregation_payload(self, findings_batch: list[FindingDB]) -> list[dict[str, Any]]:
        """
        The group's findings for the aggregator, each shortened to its share of the budget.

        A group too large for every member to get the minimum share is cut at the budget.
        """
        share = max(
            MIN_AGGREGATION_MEMBER_JSON_CHARS, MAX_AGGREGATION_JSON_CHARS // len(findings_batch)
        )
        payload: list[dict[str, Any]] = []
        used = 0
        for finding in findings_batch:
            report = _fit_description(self._finding_json(finding), share)
            used += len(json_block(report))
            if used > MAX_AGGREGATION_JSON_CHARS:
                logger.warning(
                    "Aggregating %d of %d duplicates; the rest exceed the prompt budget",
                    len(payload),
                    len(findings_batch),
                )
                break
            payload.append(
                {
                    "title": report["title"],
                    "description": report["description"],
                    "severity": report["claimed_severity"],
                    "file_paths": report["referenced_files"],
                }
            )
        return payload

    async def _aggregate(self, findings_batch: list[FindingDB], task_cache: TaskCache) -> FindingDB:
        """
        Merge a group of duplicate findings into a single consolidated finding to triage.

        The returned object is a synthetic :class:`FindingDB` (a copy of the representative with
        merged title/description/severity/file_paths). A failed merge falls back to the
        representative finding, so the triage always has a finding to judge. Either way, the
        file paths of every member are kept, including members left out of the merge prompt.
        """
        representative = findings_batch[0]
        all_paths = _union_paths(*(f.file_paths for f in findings_batch))
        merged = await self.aggregator.aggregate(
            findings=self._aggregation_payload(findings_batch),
            task_title=task_cache.title or "",
            task_description=task_cache.description or "",
            default_severity=self._severity_str(representative.severity),
        )
        if merged is None:
            logger.warning(
                "Aggregation failed for a group of %d duplicates; falling back to the representative finding.",
                len(findings_batch),
            )
            return representative.model_copy(update={"file_paths": all_paths})
        return representative.model_copy(
            update={
                "title": merged.title,
                "description": merged.description,
                "severity": self._normalize_severity(merged.severity),
                "file_paths": _union_paths(merged.file_paths, all_paths),
            }
        )

    async def evaluate_groups(
        self, groups: list[list[FindingDB]], task_cache: TaskCache
    ) -> tuple[list[FindingEvaluation], list[FindingDB]]:
        """
        Triage every group (one unit per group).

        Returns ``(evaluations, failed)``: one evaluation per member of every group that was
        judged, and every member of each group that could not be evaluated (no verdict is
        recorded for them). Raises ``TriageInfrastructureError`` when no unit could be
        evaluated at all.
        """
        groups = [g for g in groups if g]
        if not groups:
            return [], []
        task_cache = _capped_task(task_cache)
        keys = [f"u{i:03d}" for i in range(len(groups))]
        # The triage's own limit, so merges and agents (of this run and of any concurrent one)
        # never exceed TRIAGE_MAX_PARALLEL_AGENTS together.
        sem = self.triage.semaphore

        async def build(key: str, group: list[FindingDB]) -> TriageUnit:
            async with sem:
                try:
                    return await self._build_unit(key, group, task_cache)
                except Exception:
                    # Merging is an aid, not a requirement: judge the unmerged representative.
                    logger.exception("building unit %s failed; triaging it unmerged", key)
                    if len(group) == 1:
                        raise
                    return self._group_unit(key, group, group[0])

        units = await asyncio.gather(*(build(k, g) for k, g in zip(keys, groups, strict=True)))
        verdicts = await self.triage.run_task(task_cache, units)

        results: list[FindingEvaluation] = []
        failed: list[FindingDB] = []
        for key, group in zip(keys, groups, strict=True):
            verdict: TriageVerdict = verdicts[key]
            if verdict.error:
                logger.warning(
                    "unit %s could not be evaluated (%s); its %d finding(s) get no verdict",
                    key,
                    verdict.comment,
                    len(group),
                )
                failed.extend(group)
                continue
            for finding in group:
                results.append(self._to_evaluation(finding, verdict))
        return results, failed

    def _to_evaluation(self, finding: FindingDB, verdict: TriageVerdict) -> FindingEvaluation:
        """Map a triage verdict onto a :class:`FindingEvaluation` for one finding."""
        return FindingEvaluation(
            finding_id=finding.str_id,
            is_valid=verdict.is_valid,
            severity=verdict.severity,
            comment=verdict.comment,
        )

    @staticmethod
    def _severity_str(severity: Any) -> str:
        """Coerce a Severity enum / string into its plain string value."""
        return severity.value if isinstance(severity, Severity) else str(severity)

    def apply_evaluation_results(
        self, findings: list[FindingDB], evaluation_results: list[FindingEvaluation]
    ) -> dict[str, Any]:
        """
        Set each evaluated finding's severity and comment, and dispute the invalid ones.

        The findings are changed in memory only; the run writes them to the backend once, after
        this step.

        Args:
            findings: The findings that were evaluated
            evaluation_results: List of evaluation results to apply

        Returns:
            Summary of applied changes; a result naming no finding of the list counts as failed
        """
        finding_map = {f.str_id: f for f in findings}
        valid_count = 0
        disputed_count = 0
        failed_count = 0

        for eval_result in evaluation_results:
            finding = finding_map.get(eval_result.finding_id)
            if finding is None:
                failed_count += 1
                logger.error(f"Evaluation names unknown finding {eval_result.finding_id}")
                continue

            if not eval_result.comment:
                logger.warning(f"evaluation_comment is empty for finding {eval_result.finding_id}")

            finding.evaluated_severity = self._normalize_severity(eval_result.severity)
            finding.evaluation_comment = eval_result.comment
            finding.updated_at = datetime.now(UTC)

            if eval_result.is_valid:
                valid_count += 1
                logger.info(f"Finding {eval_result.finding_id} is valid (no status change)")
            else:
                finding.status = Status.DISPUTED
                disputed_count += 1
                logger.info(f"Finding {eval_result.finding_id} is disputed")

        return {
            "total_evaluations": len(evaluation_results),
            "valid_count": valid_count,
            "disputed_count": disputed_count,
            "failed_count": failed_count,
        }

    async def evaluate_all_findings(
        self,
        findings: list[FindingDB],
        duplicate_relationships: list[DuplicateFinding],
        task_cache: TaskCache,
    ) -> dict[str, Any]:
        """
        Evaluate all findings, keeping duplicates together, and set the verdicts in memory.

        Run :meth:`preflight` first; the service does so before downloading the repository.

        Args:
            findings: List of findings to evaluate
            duplicate_relationships: Duplicate relationships for grouping
            task_cache: Task context (checkout path, audit scope, docs/Q&A)

        Returns:
            Summary of evaluation results. ``failed_findings`` lists findings that could not be
            evaluated (they carry no verdict); the caller must not post the run's results then.

        Raises:
            TriageInfrastructureError when nothing could be evaluated; no verdict is set then.
        """
        if not findings:
            return {
                "total_findings": 0,
                "batches_processed": 0,
                "evaluation_results": [],
                "failed_findings": [],
                "application_results": {
                    "total_evaluations": 0,
                    "valid_count": 0,
                    "disputed_count": 0,
                    "failed_count": 0,
                },
            }

        logger.info(f"Starting triage evaluation of {len(findings)} findings")

        # Group findings: each duplicate group is one unit; every other finding is its own unit.
        if duplicate_relationships:
            related_findings_groups, individual_findings_groups = (
                self.group_findings_for_evaluation(findings, duplicate_relationships)
            )
        else:
            individual_findings_groups = [
                findings[i : i + self.batch_size] for i in range(0, len(findings), self.batch_size)
            ]
            related_findings_groups = []
        groups = related_findings_groups + [
            [f] for batch in individual_findings_groups for f in batch
        ]

        all_evaluation_results, failed_findings = await self.evaluate_groups(groups, task_cache)

        # Apply all evaluation results
        apply_results = self.apply_evaluation_results(findings, all_evaluation_results)

        results = {
            "total_findings": len(findings),
            "batches_processed": len(groups),
            "evaluation_results": all_evaluation_results,
            "application_results": apply_results,
            "failed_findings": failed_findings,
        }

        disputed_count = apply_results["disputed_count"]
        valid_count = apply_results["valid_count"]
        failed_count = apply_results["failed_count"]

        if failed_count > 0:
            logger.warning(
                f"Completed triage evaluation: {valid_count} valid, {disputed_count} disputed, {failed_count} unmatched"
            )
        else:
            logger.info(
                f"Completed triage evaluation: {valid_count} valid, {disputed_count} disputed evaluations applied"
            )

        return results
