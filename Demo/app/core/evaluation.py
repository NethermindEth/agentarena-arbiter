import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.models.finding_input import Severity
from app.types import TaskCache
from app.core.gemini_model import DuplicateFinding
from app.database.mongodb_handler import mongodb
from app.models.finding_db import FindingDB, Status
from app.core.claude_code_detector import ClaudeCodeDetector, Verdict, POSITIVE_LABEL
from app.core.evaluation_prompt import EVALUATION_PROMPT

logger = logging.getLogger(__name__)


class FindingEvaluation(BaseModel):
    """Single finding evaluation result (the verdict applied to the database)."""
    finding_id: str = Field(description="ID of the evaluated finding")
    is_valid: bool = Field(description="Whether the finding represents a valid security issue")
    severity: str = Field(description="Severity level: High, Medium, Low, or Info")
    comment: str = Field(description="Brief explanation of the evaluation")

class FindingEvaluator:
    """
    Handles final evaluation of security findings.
    Analyzes findings content to determine validity, categorize, and assess severity.
    Supports batch evaluation for efficiency.
    """
    
    def __init__(self, mongodb_client=None, batch_size: int = 10):
        """
        Initialize the finding evaluator.

        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
            batch_size: Maximum number of individual findings to group per evaluation pass
        """
        self.mongodb = mongodb_client or mongodb  # Use global instance if none provided
        self.detector = ClaudeCodeDetector()
        self.batch_size = batch_size

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
        
        # Process original findings with their duplicates
        # This ensures each batch contains related findings that refer to the same vulnerability
        for original_id, duplicate_ids in original_to_duplicates.items():
            group = [finding_map[original_id]]
            processed_finding_ids.add(original_id)

            for dup_id in duplicate_ids:
                if dup_id not in processed_finding_ids:
                    group.append(finding_map[dup_id])
                    processed_finding_ids.add(dup_id)
            
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
    
    async def evaluate_findings_batch(self, findings_batch: List[FindingDB], task_cache: TaskCache, related_findings: bool = False) -> List[FindingEvaluation]:
        """
        Evaluate a batch of findings by driving the Claude Code CLI over the repository checkout.

        The detector runs one autonomous, read-only Claude Code pass *per distinct issue*:
        - When ``related_findings`` is True the batch is a group of duplicates describing the
          same vulnerability, so a single pass judges the representative finding and the same
          verdict (validity + severity) is propagated to every member — mirroring the previous
          "unified assessment for duplicates" behavior.
        - Otherwise each finding is judged independently on its own merits.

        Args:
            findings_batch: List of findings to evaluate
            task_cache: Task context (checkout path, audit scope, docs/Q&A)
            related_findings: Whether the findings are duplicates of the same underlying issue

        Returns:
            List of evaluation results, one per finding in the batch
        """
        if not findings_batch:
            return []

        if related_findings:
            # Duplicates share one verdict: evaluate the representative, propagate to the group.
            verdict = await self._classify(findings_batch[0], task_cache)
            return [self._to_evaluation(finding, verdict) for finding in findings_batch]

        results: List[FindingEvaluation] = []
        for finding in findings_batch:
            verdict = await self._classify(finding, task_cache)
            results.append(self._to_evaluation(finding, verdict))
        return results

    async def _classify(self, finding: FindingDB, task_cache: TaskCache) -> Verdict:
        """Run the Claude Code detector for a single finding against the task's checkout."""
        repo_path = task_cache.repoPath
        default_severity = self._severity_str(finding.severity)
        if not repo_path or not Path(repo_path).is_dir():
            logger.error(
                "No repository checkout available for finding '%s' (repoPath=%s); abstaining.",
                finding.title, repo_path,
            )
            return Verdict(
                label="disapproved",
                severity=default_severity,
                confidence=0.0,
                rationale="repository checkout unavailable (abstained)",
            )

        return await self.detector.classify(
            EVALUATION_PROMPT,
            finding_title=finding.title,
            finding_description=finding.description,
            finding_severity=default_severity,
            finding_file_paths=list(finding.file_paths or []),
            task_title=task_cache.title or "",
            task_description=task_cache.description or "",
            repo_path=Path(repo_path),
            in_scope_files=list(task_cache.selectedFiles or []),
            in_scope_docs=list(task_cache.selectedDocs or [])
        )

    def _to_evaluation(self, finding: FindingDB, verdict: Verdict) -> FindingEvaluation:
        """Map a detector :class:`Verdict` onto the DB-facing :class:`FindingEvaluation`."""
        return FindingEvaluation(
            finding_id=finding.str_id,
            is_valid=(verdict.label == POSITIVE_LABEL),
            severity=verdict.severity,
            comment=verdict.rationale,
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
    
    async def evaluate_all_findings(self, task_id: str, findings: List[FindingDB], duplicate_relationships: List[DuplicateFinding], task_cache: TaskCache) -> Dict[str, Any]:
        """
        Evaluate all findings in batches, keeping duplicates together.
        
        Args:
            task_id: Task identifier
            findings: List of findings to evaluate
            duplicate_relationships: Duplicate relationships for grouping
            task_cache: Task context containing smart contract files and documentation
            
        Returns:
            Summary of evaluation results
        """
        if not findings:
            return {
                "total_findings": 0,
                "batches_processed": 0,
                "evaluation_results": [],
                "application_results": {
                    "total_evaluations": 0,
                    "valid_count": 0,
                    "disputed_count": 0,
                    "failed_count": 0
                }
            }
        
        logger.info(f"Starting batch evaluation of {len(findings)} findings")
        
        # Group findings for evaluation
        if duplicate_relationships:
            related_findings_groups, individual_findings_groups = self.group_findings_for_evaluation(findings, duplicate_relationships)
        else:
            # Simple batching without duplicate consideration
            individual_findings_groups = [findings[i:i + self.batch_size] for i in range(0, len(findings), self.batch_size)]
            related_findings_groups = []

        all_evaluation_results = []
        
        # Process each batch
        for i, batch in enumerate(related_findings_groups):
            logger.info(f"Processing related findings group {i+1}/{len(related_findings_groups)} with {len(batch)} findings")
            
            batch_results = await self.evaluate_findings_batch(batch, task_cache, True)
            all_evaluation_results.extend(batch_results)

        for i, batch in enumerate(individual_findings_groups):
            logger.info(f"Processing individual findings group {i+1}/{len(individual_findings_groups)} with {len(batch)} findings")

            batch_results = await self.evaluate_findings_batch(batch, task_cache, False)
            all_evaluation_results.extend(batch_results)
        
        # Apply all evaluation results
        apply_results = await self.apply_evaluation_results(task_id, all_evaluation_results)
        
        results = {
            "total_findings": len(findings),
            "batches_processed": len(related_findings_groups) + len(individual_findings_groups),
            "evaluation_results": all_evaluation_results,
            "application_results": apply_results
        }
        
        disputed_count = apply_results['disputed_count']
        valid_count = apply_results['valid_count']
        failed_count = apply_results['failed_count']
        
        if failed_count > 0:
            logger.warning(f"Completed batch evaluation: {valid_count} valid, {disputed_count} disputed, {failed_count} failed to update")
        else:
            logger.info(f"Completed batch evaluation: {valid_count} valid, {disputed_count} disputed evaluations applied")
        
        return results
