"""Orchestrator for Trusted Entity Analysis Pipeline.

This module orchestrates the complete trusted entity analysis:
1. Categorize validated findings
2. Run tri-cameral ensemble on trusted entity findings
3. Aggregate results
"""

from typing import List, Dict, Any
from app.models.finding_db import FindingDB
from app.types import TaskCache
from app.core.trusted_entity.categorization import categorize_trusted_entity_findings
from app.core.trusted_entity.tri_cameral import TriCameralEnsemble, TriCameralResult
import logging

logger = logging.getLogger(__name__)


class TrustedEntityAnalysisResult:
    """Result of complete trusted entity analysis."""
    
    def __init__(self):
        self.categorized_findings: List[Dict[str, Any]] = []
        self.trusted_entity_findings: List[FindingDB] = []
        self.tri_cameral_results: List[TriCameralResult] = []
        self.final_validated_findings: List[FindingDB] = []
        self.stats: Dict[str, Any] = {
            "total_findings": 0,
            "trusted_entity_count": 0,
            "valid_count": 0,
            "likely_valid_count": 0,
            "invalid_count": 0,
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary for serialization."""
        return {
            "stats": self.stats,
            "categorized_findings": self.categorized_findings,
            "tri_cameral_results": [
                {
                    "finding_id": r.finding_id,
                    "agent_lawyer": {
                        "is_valid": r.agent_lawyer.is_valid,
                        "reasoning": r.agent_lawyer.reasoning
                    },
                    "agent_mathematician": {
                        "is_valid": r.agent_mathematician.is_valid,
                        "reasoning": r.agent_mathematician.reasoning
                    },
                    "agent_safety": {
                        "is_valid": r.agent_safety.is_valid,
                        "reasoning": r.agent_safety.reasoning
                    },
                    "score": r.score,
                    "final_verdict": r.final_verdict,
                    "reasoning": r.reasoning
                }
                for r in self.tri_cameral_results
            ],
            "final_validated_findings": [
                {
                    "id": str(f.id),
                    "title": f.title,
                    "description": f.description,
                    "severity": f.severity,
                    "file_paths": f.file_paths
                }
                for f in self.final_validated_findings
            ]
        }


async def run_trusted_entity_analysis(
    validated_findings: List[FindingDB],
    task_cache: TaskCache,
    summary: str,
    dev_doc: str,
) -> TrustedEntityAnalysisResult:
    """
    Run complete trusted entity analysis pipeline.
    
    Args:
        validated_findings: Findings that passed initial validation
        task_cache: Task context
        summary: Project summary
        dev_doc: Developer documentation
        
    Returns:
        TrustedEntityAnalysisResult with all analysis results
    """
    result = TrustedEntityAnalysisResult()
    result.stats["total_findings"] = len(validated_findings)
    
    logger.info(f"Starting trusted entity analysis for {len(validated_findings)} validated findings")
    
    # Step 1: Categorize findings
    logger.info("Step 1: Categorizing findings for trusted entity relevance...")
    categorization_results = await categorize_trusted_entity_findings(
        findings=validated_findings,
        task_cache=task_cache
    )
    
    # Store categorization results
    for cat_result in categorization_results:
        result.categorized_findings.append({
            "finding_id": cat_result.finding_id,
            "is_trusted_entity": cat_result.is_trusted_entity,
            "category_type": cat_result.category_type,
            "reasoning": cat_result.reasoning
        })
    
    # Filter trusted entity findings
    trusted_entity_ids = {
        cat.finding_id
        for cat in categorization_results
        if cat.is_trusted_entity
    }
    
    result.trusted_entity_findings = [
        f for f in validated_findings
        if f.title in trusted_entity_ids
    ]
    result.stats["trusted_entity_count"] = len(result.trusted_entity_findings)
    
    logger.info(f"Found {len(result.trusted_entity_findings)} trusted entity findings")
    
    # Step 2: Run tri-cameral ensemble on trusted entity findings
    if result.trusted_entity_findings:
        logger.info("Step 2: Running tri-cameral ensemble validation...")
        ensemble = TriCameralEnsemble()
        
        # Process findings (can be parallelized if needed)
        for finding in result.trusted_entity_findings:
            tri_result = await ensemble.validate_finding(
                finding=finding,
                task_cache=task_cache,
                summary=summary,
                dev_doc=dev_doc
            )
            result.tri_cameral_results.append(tri_result)
            
            # Add to final validated findings based on verdict
            if tri_result.final_verdict == "VALID":
                result.final_validated_findings.append(finding)
                result.stats["valid_count"] += 1
            elif tri_result.final_verdict == "LIKELY_VALID":
                result.final_validated_findings.append(finding)
                result.stats["likely_valid_count"] += 1
            else:
                result.stats["invalid_count"] += 1
        
        logger.info(
            f"Tri-cameral results: {result.stats['valid_count']} VALID, "
            f"{result.stats['likely_valid_count']} LIKELY_VALID, "
            f"{result.stats['invalid_count']} INVALID"
        )
    else:
        logger.info("No trusted entity findings to validate with tri-cameral ensemble")
    
    # Add non-trusted-entity findings to final results (they passed validation)
    non_trusted_findings = [
        f for f in validated_findings
        if f.title not in trusted_entity_ids
    ]
    result.final_validated_findings.extend(non_trusted_findings)
    
    logger.info(
        f"Analysis complete. Final validated findings: {len(result.final_validated_findings)} "
        f"({len(non_trusted_findings)} non-trusted-entity + {len(result.final_validated_findings) - len(non_trusted_findings)} trusted-entity validated)"
    )
    
    return result

