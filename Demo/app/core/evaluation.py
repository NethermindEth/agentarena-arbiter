import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.models.finding_input import Severity
from app.types import TaskCache
from app.core.gemini_model import DuplicateFinding
from app.database.mongodb_handler import mongodb
from app.models.finding_db import FindingDB, Status
from app.core.claude_code_aggregator import ClaudeCodeAggregator
from app.core.claude_code_triage import ClaudeCodeTriage, TriageUnit, TriageVerdict

logger = logging.getLogger(__name__)

# Participant-written text is capped before it reaches any prompt (a huge description could
# otherwise make every agent of a unit fail).
MAX_TITLE_CHARS = 500
MAX_DESCRIPTION_CHARS = 30_000


def _cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " ...(truncated by the arbiter)"


class FindingEvaluation(BaseModel):
    """Single finding evaluation result (the verdict applied to the database)."""
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
    member of the group.
    """

    def __init__(self, mongodb_client=None, batch_size: int = 10):
        """
        Initialize the finding evaluator.

        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
            batch_size: Maximum number of individual findings per grouping batch
        """
        self.mongodb = mongodb_client or mongodb  # Use global instance if none provided
        self.triage = ClaudeCodeTriage()
        self.aggregator = ClaudeCodeAggregator()
        self.batch_size = batch_size

    def preflight(self) -> bool:
        """
        Check the host can run the triage before any finding is touched.

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
            return Severity.LOW # Default fallback

    def group_findings_for_evaluation(self, findings: List[FindingDB], duplicate_relationships: List[DuplicateFinding]) -> Tuple[List[List[FindingDB]], List[List[FindingDB]]]:
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
        group_of: Dict[str, List[FindingDB]] = {}

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
            batch = remaining_findings[i:i + self.batch_size]
            if batch:
                individual_findings_groups.append(batch)

        logger.info(f"Created {len(related_findings_groups)} related findings groups and {len(individual_findings_groups)} individual findings groups from {len(findings)} findings")

        return related_findings_groups, individual_findings_groups

    @staticmethod
    def _finding_json(finding: FindingDB) -> Dict[str, Any]:
        """The untrusted finding fields shown to the triage agents."""
        return {
            "title": _cap(finding.title or "(no title)", MAX_TITLE_CHARS),
            "claimed_severity": FindingEvaluator._severity_str(finding.severity) or "unknown",
            "referenced_files": [_cap(str(p), 300) for p in (finding.file_paths or [])[:50]],
            "description": _cap(finding.description or "(no description)", MAX_DESCRIPTION_CHARS),
        }

    async def _build_unit(self, key: str, group: List[FindingDB], task_cache: TaskCache) -> TriageUnit:
        """
        Turn one group into a triage unit.

        A duplicate group is merged into one digest finding (see :meth:`_aggregate`), and the
        original member reports are attached too, so the agents judge the strongest correct
        version of the issue instead of trusting the merge (or a planted duplicate's wording).
        """
        representative = group[0]
        if len(group) == 1:
            return TriageUnit(key, self._finding_json(representative), self._severity_str(representative.severity))
        merged = await self._aggregate(group, task_cache)
        finding = self._finding_json(merged)
        finding["member_reports"] = [self._finding_json(f) for f in group]
        return TriageUnit(key, finding, self._severity_str(merged.severity))

    async def _aggregate(self, findings_batch: List[FindingDB], task_cache: TaskCache) -> FindingDB:
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
        return representative.model_copy(update={
            "title": merged.title,
            "description": merged.description,
            "severity": self._normalize_severity(merged.severity),
            "file_paths": merged.file_paths,
        })

    async def evaluate_groups(
        self, groups: List[List[FindingDB]], task_cache: TaskCache, *, poc_enabled: bool
    ) -> Tuple[List[FindingEvaluation], List[FindingDB]]:
        """
        Triage every group (one unit per group).

        Returns ``(evaluations, failed)``: one evaluation per member of every group that was
        judged, and every member of each group that could not be evaluated (to be retried; no
        verdict is recorded for them). Raises ``TriageInfrastructureError`` when no unit could be
        evaluated at all.
        """
        groups = [g for g in groups if g]
        if not groups:
            return [], []
        keys = [f"u{i:03d}" for i in range(len(groups))]
        sem = asyncio.Semaphore(self.triage.cfg.triage_max_parallel_agents)

        async def build(key: str, group: List[FindingDB]) -> TriageUnit:
            async with sem:
                return await self._build_unit(key, group, task_cache)

        units = await asyncio.gather(*(build(k, g) for k, g in zip(keys, groups)))
        verdicts = await self.triage.run_task(task_cache, units, poc_enabled=poc_enabled)

        results: List[FindingEvaluation] = []
        failed: List[FindingDB] = []
        for key, group in zip(keys, groups):
            verdict: TriageVerdict = verdicts[key]
            if verdict.error:
                logger.warning("unit %s could not be evaluated (%s); its %d finding(s) will be retried",
                               key, verdict.comment, len(group))
                failed.extend(group)
                continue
            for finding in group:
                results.append(self._to_evaluation(finding, verdict))
        return results, failed

    def _to_evaluation(self, finding: FindingDB, verdict: TriageVerdict) -> FindingEvaluation:
        """Map a triage verdict onto the DB-facing :class:`FindingEvaluation`."""
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

    async def apply_evaluation_results(self, task_id: str, evaluation_results: List[FindingEvaluation]) -> Dict[str, Any]:
        """
        Apply evaluation results to findings in the database.

        Args:
            task_id: Task identifier
            evaluation_results: List of evaluation results to apply

        Returns:
            Summary of applied changes
        """
        valid_count = 0
        disputed_count = 0
        failed_count = 0

        for eval_result in evaluation_results:
            try:
                update_fields = {
                    "evaluated_severity": self._normalize_severity(eval_result.severity),
                    "evaluation_comment": eval_result.comment,
                    "updated_at": datetime.now(timezone.utc)
                }

                if not eval_result.comment:
                    logger.warning(f"evaluation_comment is empty for finding {eval_result.finding_id}")

                # Set status to DISPUTED for invalid findings
                if not eval_result.is_valid:
                    update_fields["status"] = Status.DISPUTED

                success = await self.mongodb.update_finding(task_id, eval_result.finding_id, update_fields)

                if success:
                    if eval_result.is_valid:
                        valid_count += 1
                        logger.info(f"Successfully updated valid finding {eval_result.finding_id} (no status change)")
                    else:
                        disputed_count += 1
                        logger.info(f"Successfully updated finding {eval_result.finding_id} with status {update_fields['status']}")
                else:
                    failed_count += 1
                    logger.error(f"Failed to update finding {eval_result.finding_id} in database")

            except Exception as e:
                failed_count += 1
                logger.error(f"Error applying evaluation for finding '{eval_result.finding_id}': {str(e)}")
                logger.error(f"eval_result data: is_valid={eval_result.is_valid}, severity={eval_result.severity}, comment={eval_result.comment}")
                continue

        return {
            "total_evaluations": len(evaluation_results),
            "valid_count": valid_count,
            "disputed_count": disputed_count,
            "failed_count": failed_count
        }

    async def evaluate_all_findings(
        self,
        task_id: str,
        findings: List[FindingDB],
        duplicate_relationships: List[DuplicateFinding],
        task_cache: TaskCache,
        poc_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate all findings, keeping duplicates together, and apply the verdicts.

        Args:
            task_id: Task identifier
            findings: List of findings to evaluate
            duplicate_relationships: Duplicate relationships for grouping
            task_cache: Task context (checkout path, audit scope, docs/Q&A)
            poc_enabled: Result of :meth:`preflight` (run here when not given)

        Returns:
            Summary of evaluation results. ``failed_findings`` lists findings that could not be
            evaluated (no verdict applied); the caller must make them retryable.

        Raises:
            SandboxUnavailable / ValueError from the preflight, or TriageInfrastructureError when
            nothing could be evaluated. Nothing is written to the database in those cases.
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
                    "failed_count": 0
                }
            }

        logger.info(f"Starting triage evaluation of {len(findings)} findings")
        if poc_enabled is None:
            poc_enabled = self.preflight()

        # Group findings: each duplicate group is one unit; every other finding is its own unit.
        if duplicate_relationships:
            related_findings_groups, individual_findings_groups = self.group_findings_for_evaluation(findings, duplicate_relationships)
        else:
            individual_findings_groups = [findings[i:i + self.batch_size] for i in range(0, len(findings), self.batch_size)]
            related_findings_groups = []
        groups = related_findings_groups + [[f] for batch in individual_findings_groups for f in batch]

        all_evaluation_results, failed_findings = await self.evaluate_groups(groups, task_cache, poc_enabled=poc_enabled)

        # Apply all evaluation results
        apply_results = await self.apply_evaluation_results(task_id, all_evaluation_results)

        results = {
            "total_findings": len(findings),
            "batches_processed": len(groups),
            "evaluation_results": all_evaluation_results,
            "application_results": apply_results,
            "failed_findings": failed_findings,
        }

        disputed_count = apply_results['disputed_count']
        valid_count = apply_results['valid_count']
        failed_count = apply_results['failed_count']

        if failed_count > 0:
            logger.warning(f"Completed batch evaluation: {valid_count} valid, {disputed_count} disputed, {failed_count} failed to update")
        else:
            logger.info(f"Completed batch evaluation: {valid_count} valid, {disputed_count} disputed evaluations applied")

        return results
