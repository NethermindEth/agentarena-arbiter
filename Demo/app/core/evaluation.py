import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone

from app.database.mongodb_handler import mongodb
from app.models.finding_db import FindingDB, Status, EvaluatedSeverity
from app.core.claude_model import (
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
    
    def _convert_structured_result_to_dict(self, structured_result: List[FindingEvaluation]) -> List[Dict[str, Any]]:
        """
        Convert structured evaluation result to the expected dictionary format.
        
        Args:
            structured_result: List[FindingEvaluation] from structured output
            
        Returns:
            List of evaluation results as dictionaries
        """
        normalized_results = []
        for evaluation in structured_result:
            normalized_result = {
                "finding_id": evaluation.finding_id,
                "is_valid": evaluation.is_valid,
                "evaluated_severity": self._normalize_severity(evaluation.severity),
                "evaluation_comment": evaluation.comment
            }
            normalized_results.append(normalized_result)
        
        return normalized_results
    
    def _normalize_severity(self, severity_text: str) -> Optional[EvaluatedSeverity]:
        """
        Normalize severity text to EvaluatedSeverity enum.
        
        Args:
            severity_text: Severity as string
            
        Returns:
            EvaluatedSeverity enum value or None if invalid
        """
        severity_lower = severity_text.lower().strip()
        
        if severity_lower in ["low", "trivial"]:
            return EvaluatedSeverity.LOW
        elif severity_lower == "medium":
            return EvaluatedSeverity.MEDIUM
        elif severity_lower in ["high", "critical"]:
            return EvaluatedSeverity.HIGH
        else:
            return EvaluatedSeverity.MEDIUM  # Default fallback
    
    def group_findings_for_evaluation(self, findings: List[FindingDB], duplicate_relationships: List[Dict[str, str]]) -> List[List[FindingDB]]:
        """
        Group findings for batch evaluation based on duplicate relationships.
        Each batch contains an original finding and all its duplicates so they can be evaluated together.
        
        Args:
            findings: List of findings to group
            duplicate_relationships: List of duplicate relationships from deduplication
            
        Returns:
            List of finding groups (batches) for evaluation
        """
        # Create a mapping of originals to their duplicates based on duplicate_relationships
        original_to_duplicates = {}
        duplicate_set = set()
        
        # Build the duplicate relationships mapping
        for rel in duplicate_relationships:
            duplicate_id = rel.get("findingId")
            original_id = rel.get("duplicateOf")
            
            if duplicate_id and original_id:
                duplicate_set.add(duplicate_id)
                if original_id not in original_to_duplicates:
                    original_to_duplicates[original_id] = []
                original_to_duplicates[original_id].append(duplicate_id)
        
        # Create a mapping for quick finding lookup by title
        finding_map = {f.id: f for f in findings}
        
        finding_groups = []
        processed_findings = set()
        
        # First, process original findings with their duplicates
        # This ensures each batch contains related findings that refer to the same vulnerability
        for original_id, duplicate_ids in original_to_duplicates.items():
            if original_id in processed_findings:
                continue
                
            # Create a group with the original and all its duplicates
            group = []
            
            # Add original if it exists in our findings list
            if original_id in finding_map:
                group.append(finding_map[original_id])
                processed_findings.add(original_id)
            
            # Add all duplicates to the same group
            for dup_id in duplicate_ids:
                if dup_id in finding_map and dup_id not in processed_findings:
                    group.append(finding_map[dup_id])
                    processed_findings.add(dup_id)
            
            # Add the group if it has findings
            if group:
                finding_groups.append(group)
        
        # Process remaining findings (those without duplicates) individually or in small batches
        remaining_findings = [f for f in findings if f.id not in processed_findings]
        
        # Batch remaining findings according to batch_size
        for i in range(0, len(remaining_findings), self.batch_size):
            batch = remaining_findings[i:i + self.batch_size]
            if batch:
                finding_groups.append(batch)
        
        logger.info(f"Created {len(finding_groups)} evaluation batches from {len(findings)} findings")
        
        return finding_groups
    
    async def evaluate_findings_batch(self, findings_batch: List[FindingDB]) -> List[Dict[str, Any]]:
        """
        Evaluate a batch of findings using structured output.
        
        Args:
            findings_batch: List of findings to evaluate (should be related to same vulnerability)
            
        Returns:
            List of evaluation results
        """
        if not findings_batch:
            return []
        
        evaluation_results: List[FindingEvaluation] = await evaluate_findings_structured(self.evaluation_model, findings_batch).results
        
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
                    "status": Status.DISPUTED,
                    "evaluated_severity": self._normalize_severity(eval_result.severity),
                    "evaluation_comment": eval_result.comment,
                    "updated_at": datetime.now(timezone.utc)
                }
                
                # Track validity for statistics
                if eval_result.is_valid:
                    valid_count += 1
                else:
                    disputed_count += 1

                await self.mongodb.update_finding(task_id, eval_result.finding_id, update_fields)

            except Exception as e:
                logger.error(f"Error applying evaluation for finding '{eval_result.finding_id}': {str(e)}")
                continue
        
        return {
            "total_evaluations": len(evaluation_results),
            "valid_count": valid_count,
            "disputed_count": disputed_count
        }
    
    async def evaluate_all_findings(self, task_id: str, findings: List[FindingDB], duplicate_relationships: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Evaluate all findings in batches, keeping duplicates together.
        
        Args:
            task_id: Task identifier
            findings: List of findings to evaluate
            duplicate_relationships: Optional duplicate relationships for grouping
            
        Returns:
            Summary of evaluation results
        """
        if not findings:
            return {
                "total_findings": 0,
                "batches_processed": 0,
                "evaluation_results": []
            }
        
        logger.info(f"Starting batch evaluation of {len(findings)} findings")
        
        # Group findings for evaluation
        if duplicate_relationships:
            finding_groups = self.group_findings_for_evaluation(findings, duplicate_relationships)
        else:
            # Simple batching without duplicate consideration
            finding_groups = [findings[i:i + self.batch_size] for i in range(0, len(findings), self.batch_size)]
        
        logger.info(f"Created {len(finding_groups)} batches for evaluation")
        
        all_evaluation_results = []
        
        # Process each batch
        for i, batch in enumerate(finding_groups):
            logger.info(f"Processing batch {i+1}/{len(finding_groups)} with {len(batch)} findings")
            
            batch_results = await self.evaluate_findings_batch(batch)
            all_evaluation_results.extend(batch_results)
        
        # Apply all evaluation results
        apply_results = await self.apply_evaluation_results(task_id, all_evaluation_results)
        
        results = {
            "total_findings": len(findings),
            "batches_processed": len(finding_groups),
            "evaluation_results": all_evaluation_results,
            "application_results": apply_results
        }
        
        logger.info(f"Completed batch evaluation: {apply_results['disputed_count']} disputed evaluations applied")
        
        return results
