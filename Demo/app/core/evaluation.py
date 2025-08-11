import logging
from typing import List, Dict, Any, Tuple
from datetime import datetime, timezone

from app.models.finding_input import Severity
from app.types import TaskCache
from app.core.gemini_model import DuplicateFinding
from app.database.mongodb_handler import mongodb
from app.models.finding_db import FindingDB, Status
from app.core.claude_model import (
    EvaluationResult,
    create_structured_evaluation_model,
    evaluate_findings_structured,
    FindingEvaluation
)

logger = logging.getLogger(__name__)

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
            batch_size: Maximum number of findings to evaluate in a single batch
        """
        self.mongodb = mongodb_client or mongodb  # Use global instance if none provided
        self.evaluation_model = self._setup_structured_evaluation_model()
        self.batch_size = batch_size
    
    def _setup_structured_evaluation_model(self) -> any:
        """
        Setup structured output model for batch finding evaluation.
        
        Returns:
            Claude model configured with structured output for evaluation
        """
        return create_structured_evaluation_model()
    
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
        Evaluate a batch of findings using structured output.
        
        Args:
            findings_batch: List of findings to evaluate (should be related to same vulnerability)
            related_findings: Whether the findings are related to each other
            task_cache: Task context containing smart contract files and documentation
            
        Returns:
            List of evaluation results
        """
        if not findings_batch:
            return []
        
        eval_result: EvaluationResult = await evaluate_findings_structured(self.evaluation_model, findings_batch, task_cache, related_findings)
        evaluation_results: List[FindingEvaluation] = eval_result.results
        
        # Ensure we have results for all findings in the batch
        if len(evaluation_results) != len(findings_batch):
            logger.warning(f"Evaluation returned {len(evaluation_results)} results for {len(findings_batch)} findings")
        
        return evaluation_results
    
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
        
        for eval_result in evaluation_results:
            try:
                update_fields = {
                    "evaluated_severity": self._normalize_severity(eval_result.severity),
                    "evaluation_comment": eval_result.comment,
                    "updated_at": datetime.now(timezone.utc)
                }
                
                if not eval_result.comment:
                    logger.warning(f"evaluation_comment is empty for finding {eval_result.finding_id}")
                
                # Track validity for statistics
                if eval_result.is_valid:
                    valid_count += 1
                    logger.info(f"Updating valid finding {eval_result.finding_id} (no status change)")
                else:
                    update_fields["status"] = Status.DISPUTED
                    disputed_count += 1
                    logger.info(f"Updating finding {eval_result.finding_id} with status {update_fields['status']}")
                
                success = await self.mongodb.update_finding(task_id, eval_result.finding_id, update_fields)
                logger.info(f"Updated finding {eval_result.finding_id}: {success}")

            except Exception as e:
                logger.error(f"Error applying evaluation for finding '{eval_result.finding_id}': {str(e)}")
                logger.error(f"eval_result data: is_valid={eval_result.is_valid}, severity={eval_result.severity}, comment={eval_result.comment}")
                continue
        
        return {
            "total_evaluations": len(evaluation_results),
            "valid_count": valid_count,
            "disputed_count": disputed_count
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
                    "disputed_count": 0
                }
            }
        
        logger.info(f"Starting batch evaluation of {len(findings)} findings")
        
        # Group findings for evaluation
        if duplicate_relationships:
            related_findings_groups, individual_findings_groups = self.group_findings_for_evaluation(findings, duplicate_relationships)
        else:
            # Simple batching without duplicate consideration
            individual_findings_groups = [findings[i:i + self.batch_size] for i in range(0, len(findings), self.batch_size)]
        
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
        
        logger.info(f"Completed batch evaluation: {apply_results['disputed_count']} disputed evaluations applied")
        
        return results
