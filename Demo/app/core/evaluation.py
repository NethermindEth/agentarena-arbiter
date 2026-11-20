import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.claude_code_aggregator import ClaudeCodeAggregator
from app.core.claude_code_triage import ClaudeCodeTriage, TriageUnit, TriageVerdict
from app.core.gemini_model import DuplicateFinding
from app.models.finding_db import FindingDB, Status
from app.models.finding_input import Severity
from app.types import TaskCache

logger = logging.getLogger(__name__)

# Participant-written text is capped before it reaches any prompt (a huge description could
# otherwise make every agent of a unit fail).
MAX_TITLE_CHARS = 500
MAX_DESCRIPTION_CHARS = 30_000


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " ...(truncated by the arbiter)"


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

    def preflight(self) -> bool:
        """
        Check the host can run the triage before any work starts.

        Returns whether PoC execution is enabled. Raises ``SandboxUnavailable`` (PoCs required
        but no sandbox) or ``ValueError`` (misconfiguration); callers must then leave the task
        unprocessed.
        """
        return self.triage.preflight()

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
        Group findings for batch evaluation based on duplicate relationships.
        Each batch contains an original finding and all its duplicates so they can be evaluated together.

        Args:
            findings: List of findings to group
            duplicate_relationships: List of duplicate relationships from deduplication

        Returns:
            List of finding groups (batches) for evaluation
        """
        # Create a mapping for quick finding lookup by id first
        finding_map = {f.str_id: f for f in findings}

        # Create a mapping of originals to their duplicates based on duplicate_relationships
        original_to_duplicates = {}

        for rel in duplicate_relationships:
            duplicate_id = rel.findingId
            original_id = rel.duplicateOf

            if duplicate_id not in finding_map:
                logger.warning(f"Duplicate ID {duplicate_id} not found in findings list")
                continue

            if original_id not in finding_map:
                logger.warning(f"Original ID {original_id} not found in findings list")
                continue

            if original_id not in original_to_duplicates:
                original_to_duplicates[original_id] = []
            original_to_duplicates[original_id].append(duplicate_id)

        related_findings_groups = []
        individual_findings_groups = []
        processed_finding_ids = set()
        group_of: dict[str, list[FindingDB]] = {}

        # Process original findings with their duplicates
        # This ensures each batch contains related findings that refer to the same vulnerability
        for original_id, duplicate_ids in original_to_duplicates.items():
            if original_id in processed_finding_ids:
                # Chained relationship (A dup of B, B dup of C): fold into B's existing group so
                # no finding is triaged twice.
                group = group_of[original_id]
                for dup_id in duplicate_ids:
                    if dup_id not in processed_finding_ids:
                        group.append(finding_map[dup_id])
                        processed_finding_ids.add(dup_id)
                        group_of[dup_id] = group
                continue
            group = [finding_map[original_id]]
            processed_finding_ids.add(original_id)
            group_of[original_id] = group

            for dup_id in duplicate_ids:
                if dup_id not in processed_finding_ids:
                    group.append(finding_map[dup_id])
                    processed_finding_ids.add(dup_id)
                    group_of[dup_id] = group

            if group:
                related_findings_groups.append(group)

        # Process remaining findings (those without duplicates) individually or in small batches
        remaining_findings = [f for f in findings if f.str_id not in processed_finding_ids]

        # Batch remaining findings according to batch_size
        for i in range(0, len(remaining_findings), self.batch_size):
            batch = remaining_findings[i : i + self.batch_size]
            if batch:
                individual_findings_groups.append(batch)

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
        representative = group[0]
        if len(group) == 1:
            return TriageUnit(
                key, self._finding_json(representative), self._severity_str(representative.severity)
            )
        merged = await self._aggregate(group, task_cache)
        finding = self._finding_json(merged)
        finding["member_reports"] = [self._finding_json(f) for f in group]
        return TriageUnit(key, finding, self._severity_str(merged.severity))

    async def _aggregate(self, findings_batch: list[FindingDB], task_cache: TaskCache) -> FindingDB:
        """
        Merge a group of duplicate findings into a single consolidated finding to triage.

        The returned object is a synthetic :class:`FindingDB` (a copy of the representative with
        merged title/description/severity/file_paths). A failed merge falls back to the
        representative finding, so the triage always has a finding to judge.
        """
        representative = findings_batch[0]
        payload = [
            {
                "title": finding.title,
                "description": finding.description,
                "severity": self._severity_str(finding.severity),
                "file_paths": list(finding.file_paths or []),
            }
            for finding in findings_batch
        ]
        merged = await self.aggregator.aggregate(
            findings=payload,
            task_title=task_cache.title or "",
            task_description=task_cache.description or "",
            default_severity=self._severity_str(representative.severity),
        )
        if merged is None:
            logger.warning(
                "Aggregation failed for a group of %d duplicates; falling back to the representative finding.",
                len(findings_batch),
            )
            return representative
        return representative.model_copy(
            update={
                "title": merged.title,
                "description": merged.description,
                "severity": self._normalize_severity(merged.severity),
                "file_paths": merged.file_paths,
            }
        )

    async def evaluate_groups(
        self, groups: list[list[FindingDB]], task_cache: TaskCache, *, poc_enabled: bool
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
        keys = [f"u{i:03d}" for i in range(len(groups))]
        sem = asyncio.Semaphore(self.triage.cfg.triage_max_parallel_agents)

        async def build(key: str, group: list[FindingDB]) -> TriageUnit:
            async with sem:
                return await self._build_unit(key, group, task_cache)

        units = await asyncio.gather(*(build(k, g) for k, g in zip(keys, groups, strict=True)))
        verdicts = await self.triage.run_task(task_cache, units, poc_enabled=poc_enabled)

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
        poc_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """
        Evaluate all findings, keeping duplicates together, and set the verdicts in memory.

        Args:
            findings: List of findings to evaluate
            duplicate_relationships: Duplicate relationships for grouping
            task_cache: Task context (checkout path, audit scope, docs/Q&A)
            poc_enabled: Result of :meth:`preflight` (run here when not given)

        Returns:
            Summary of evaluation results. ``failed_findings`` lists findings that could not be
            evaluated (they carry no verdict); the caller must not post the run's results then.

        Raises:
            SandboxUnavailable / ValueError from the preflight, or TriageInfrastructureError when
            nothing could be evaluated. No verdict is set in those cases.
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
        if poc_enabled is None:
            poc_enabled = self.preflight()

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

        all_evaluation_results, failed_findings = await self.evaluate_groups(
            groups, task_cache, poc_enabled=poc_enabled
        )

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
