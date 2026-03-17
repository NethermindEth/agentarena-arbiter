"""
Experimental validation test script.

This script tests the experimental validation features:
1. Extra files section - includes all files except those in file_paths
2. Enhanced SGR - confusion identification and retry logic

This is for testing purposes only. Compare results with test_with_metrics.py to evaluate improvements.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, List, Any
from sklearn.metrics import confusion_matrix

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import models and utilities
from app.models.finding_db import FindingDB, Status
from app.models.finding_input import Finding, Severity
from app.core.contract_grouping import ContractFile
from app.core.deduplication import FindingDeduplication
from app.core.critic_utils import assign_temporary_indexes
from app.core.evaluation_experimental import FindingEvaluatorExperimental
from app.types import TaskCache

# Import helper functions from test_with_metrics
import sys
sys.path.append(str(Path(__file__).parent))
from test_with_metrics import (
    convert_findings_from_json,
    load_contracts_from_info,
    prepare_context_from_info,
    convert_findings_to_findingdb,
    build_task_cache_from_info,
    load_original_findings,
    match_findings_with_ground_truth,
    calculate_metrics,
    get_project_paths,
    print_confusion_matrix,
)


async def test_experimental_pipeline(project: str = "lido", final_only: bool = False):
    """
    Test the experimental validation pipeline with enhanced features.
    
    Args:
        project: Project name ('lido' or 'uniswap'), defaults to 'lido'
        final_only: If True, only show final results (skip intermediate confusion matrix)
    """
    print("=" * 80)
    print(f"TESTING EXPERIMENTAL VALIDATION PIPELINE - {project.upper()}")
    print("=" * 80)
    print("\nEXPERIMENTAL FEATURES:")
    print("  1. Extra files section - includes all files except those in file_paths")
    print("  2. Enhanced SGR - confusion identification and retry logic")
    print("=" * 80)
    
    repo_root = Path(__file__).parent.parent.parent
    findings_json, info_json, repo_path = get_project_paths(project, repo_root)
    
    if not findings_json.exists():
        logger.error(f"Findings file not found: {findings_json}")
        return
    
    if not info_json.exists():
        logger.error(f"Info file not found: {info_json}")
        return
    
    if not repo_path.exists():
        logger.error(f"Repository path not found: {repo_path}")
        return
    
    logger.info(f"✓ Found findings file: {findings_json}")
    logger.info(f"✓ Found info file: {info_json}")
    logger.info(f"✓ Found repository: {repo_path}")
    
    # Load original findings for ground truth
    logger.info("\n" + "=" * 80)
    logger.info("LOADING GROUND TRUTH DATA")
    logger.info("=" * 80)
    original_map = load_original_findings(str(findings_json))
    logger.info(f"✓ Loaded {len(original_map)} original findings with ground truth")
    
    # Step 1: Convert findings
    logger.info("\n" + "=" * 80)
    logger.info("STEP 1: Converting Findings")
    logger.info("=" * 80)
    findings = convert_findings_from_json(str(findings_json))
    
    # Step 2: Load contracts
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: Loading Contracts")
    logger.info("=" * 80)
    contract_contents = load_contracts_from_info(str(info_json), str(repo_path))
    logger.info(f"✓ Loaded {len(contract_contents)} contract files")
    
    # Step 3: Prepare context
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: Preparing Context")
    logger.info("=" * 80)
    summary, dev_doc = prepare_context_from_info(str(info_json))
    
    # Step 4: Convert to FindingDB and assign indexes
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: Converting to FindingDB and Assigning Indexes")
    logger.info("=" * 80)
    finding_dbs = convert_findings_to_findingdb(findings, agent_id="test_agent_experimental")
    indexed_findings = assign_temporary_indexes(finding_dbs)
    logger.info(f"✓ Assigned indexes to {len(indexed_findings)} findings")
    
    # Step 5: Build TaskCache
    logger.info("\n" + "=" * 80)
    logger.info("STEP 5: Building TaskCache")
    logger.info("=" * 80)
    task_cache = await build_task_cache_from_info(str(info_json), str(repo_path), contract_contents)
    
    # Step 6: Test Deduplication (using standard deduplication)
    logger.info("\n" + "=" * 80)
    logger.info("STEP 6: Testing Deduplication (Hierarchical Batch Processing)")
    logger.info("=" * 80)
    deduplicator = FindingDeduplication()
    
    original_count = len(indexed_findings)
    logger.info(f"Starting deduplication with {original_count} findings...")
    
    try:
        # Pass contract_contents directly to ensure consistency
        dedup_results = await deduplicator.process_findings(
            task_id="TEST_TASK_EXPERIMENTAL",
            findings=indexed_findings,
            task_cache=task_cache,
            contract_contents=contract_contents
        )
        
        deduplicated_findings = dedup_results["deduplication"]["original_findings"]
        deduplicated_count = len(deduplicated_findings)
        removed_count = original_count - deduplicated_count
        
        logger.info(f"✓ Deduplication complete:")
        logger.info(f"  - Original: {original_count}")
        logger.info(f"  - After deduplication: {deduplicated_count}")
        logger.info(f"  - Removed: {removed_count}")
        
    except Exception as e:
        logger.error(f"✗ Deduplication failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 7: Test EXPERIMENTAL Validation
    logger.info("\n" + "=" * 80)
    logger.info("STEP 7: Testing EXPERIMENTAL Validation (One-by-One with Enhanced Features)")
    logger.info("=" * 80)
    evaluator = FindingEvaluatorExperimental()
    
    logger.info(f"Starting EXPERIMENTAL validation with {deduplicated_count} deduplicated findings...")
    logger.info("This may take longer than standard validation due to:")
    logger.info("  - Extra files being included in prompts")
    logger.info("  - Enhanced SGR with confusion identification and retry logic")
    logger.info("  - Additional LLM calls for retry attempts")
    
    # Initialize variables for confusion matrix calculation
    matched_initial = []
    cm_initial = None
    metrics_initial = None
    initial_validated_count = 0
    confusion_stats = {"total": 0, "resolved": 0, "persisted": 0}
    
    try:
        # Pass contract_contents, summary, and dev_doc directly to ensure consistency
        evaluation_results = await evaluator.evaluate_all_findings(
            task_id="TEST_TASK_EXPERIMENTAL",
            findings=deduplicated_findings,
            duplicate_relationships=[],
            task_cache=task_cache,
            contract_contents=contract_contents,
            summary=summary,
            dev_doc=dev_doc
        )
        
        # Get final findings (those that passed validation)
        evaluation_results_map = evaluation_results.get("evaluation_results", {})
        final_findings = []
        
        # Collect confusion statistics
        for f in deduplicated_findings:
            eval_result = evaluation_results_map.get(f.str_id) if isinstance(evaluation_results_map, dict) else None
            if eval_result:
                if (hasattr(eval_result, 'confusion_analysis') and 
                    eval_result.confusion_analysis and 
                    eval_result.confusion_analysis.strip() and 
                    eval_result.confusion_analysis != ""):
                    confusion_stats["total"] += 1
                    if "[RETRY ATTEMPT] Confusion resolved after retry" in eval_result.confusion_analysis:
                        confusion_stats["resolved"] += 1
                    elif "[CONSERVATIVE FALLBACK]" in eval_result.confusion_analysis:
                        confusion_stats["persisted"] += 1
                
                if hasattr(eval_result, 'final_result') and eval_result.final_result:
                    final_findings.append(f)
        
        validated_count = len(final_findings)
        disputed_count = deduplicated_count - validated_count
        
        logger.info(f"✓ EXPERIMENTAL Validation complete:")
        logger.info(f"  - After deduplication: {deduplicated_count}")
        logger.info(f"  - After validation: {validated_count}")
        logger.info(f"  - Disputed (removed): {disputed_count}")
        logger.info(f"\n  EXPERIMENTAL FEATURE STATISTICS:")
        logger.info(f"  - Findings with confusion: {confusion_stats['total']}")
        logger.info(f"  - Confusion resolved after retry: {confusion_stats['resolved']}")
        logger.info(f"  - Confusion persisted after retry: {confusion_stats['persisted']}")
        
        # Calculate Confusion Matrix AFTER EXPERIMENTAL VALIDATION
        logger.info("\n" + "=" * 80)
        logger.info("CALCULATING CONFUSION MATRIX AFTER EXPERIMENTAL VALIDATION")
        logger.info("=" * 80)
        
        matched_initial = match_findings_with_ground_truth(
            deduplicated_findings=deduplicated_findings,
            final_findings=final_findings,
            original_map=original_map
        )
        
        if matched_initial:
            y_true_initial = [m["ground_truth"] for m in matched_initial]
            y_pred_initial = [m["kept_by_validation"] for m in matched_initial]
            cm_initial = confusion_matrix(y_true_initial, y_pred_initial, labels=[False, True])
            metrics_initial = calculate_metrics(y_true_initial, y_pred_initial)
            
            logger.info(f"Matched findings: {len(matched_initial)}")
            logger.info(f"Experimental Validation Metrics:")
            logger.info(f"  Precision: {metrics_initial['precision']:.4f} ({metrics_initial['precision']*100:.2f}%)")
            logger.info(f"  Recall:    {metrics_initial['recall']:.4f} ({metrics_initial['recall']*100:.2f}%)")
            logger.info(f"  F1 Score:  {metrics_initial['f1']:.4f} ({metrics_initial['f1']*100:.2f}%)")
            logger.info(f"  Accuracy:  {metrics_initial['accuracy']:.4f} ({metrics_initial['accuracy']*100:.2f}%)")
        else:
            matched_initial = []
            cm_initial = None
            metrics_initial = None
            logger.warning("⚠ No findings matched with ground truth for experimental validation.")
        
    except Exception as e:
        logger.error(f"✗ EXPERIMENTAL Validation failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return
    
    # Print final results
    print("\n" + "=" * 80)
    print("EXPERIMENTAL PIPELINE TEST SUMMARY")
    print("=" * 80)
    print(f"Original findings: {original_count}")
    print(f"After deduplication: {deduplicated_count} (removed {removed_count})")
    print(f"After EXPERIMENTAL validation: {validated_count} (removed {disputed_count})")
    print(f"\nTotal reduction: {original_count} → {validated_count} ({original_count - validated_count} removed)")
    print(f"Reduction rate: {((original_count - validated_count) / original_count * 100):.1f}%")
    
    print("\n" + "-" * 80)
    print("EXPERIMENTAL FEATURE STATISTICS")
    print("-" * 80)
    print(f"Findings with confusion identified: {confusion_stats['total']}")
    print(f"  - Resolved after retry: {confusion_stats['resolved']}")
    print(f"  - Persisted after retry: {confusion_stats['persisted']}")
    if confusion_stats['total'] > 0:
        resolution_rate = (confusion_stats['resolved'] / confusion_stats['total']) * 100
        print(f"  - Resolution rate: {resolution_rate:.1f}%")
    
    # Show CONFUSION MATRIX (only if not final_only)
    if not final_only:
        print("\n" + "=" * 80)
        print("CONFUSION MATRIX (EXPERIMENTAL VALIDATION)")
        print("=" * 80)
        if matched_initial and cm_initial is not None:
            print(f"\nMatched findings with ground truth: {len(matched_initial)}")
            print_confusion_matrix(cm_initial, ["Invalid", "Valid"])
    
            print("\n" + "-" * 80)
            print("PERFORMANCE METRICS (EXPERIMENTAL VALIDATION)")
            print("-" * 80)
            print(f"Precision: {metrics_initial['precision']:.4f} ({metrics_initial['precision']*100:.2f}%)")
            print(f"  → Of findings we kept ({validated_count}), {metrics_initial['precision']*100:.2f}% were actually approved by reviewers")
            print(f"\nRecall:    {metrics_initial['recall']:.4f} ({metrics_initial['recall']*100:.2f}%)")
            print(f"  → Of findings approved by reviewers, we kept {metrics_initial['recall']*100:.2f}%")
            print(f"\nF1 Score:  {metrics_initial['f1']:.4f} ({metrics_initial['f1']*100:.2f}%)")
            print(f"  → Harmonic mean of precision and recall")
            print(f"\nAccuracy:  {metrics_initial['accuracy']:.4f} ({metrics_initial['accuracy']*100:.2f}%)")
            print(f"  → Overall correctness of our validation decisions")
    
    # Save results to JSON
    results_file = repo_root / f"{project}_experimental_validation_results.json"
    results_data = {
        "project": project,
        "original_count": original_count,
        "after_deduplication": deduplicated_count,
        "after_validation": validated_count,
        "removed_by_dedup": removed_count,
        "removed_by_validation": disputed_count,
        "confusion_stats": confusion_stats,
        "metrics": metrics_initial if metrics_initial else {},
        "confusion_matrix": {
            "tn": int(cm_initial[0][0]) if cm_initial is not None else 0,
            "fp": int(cm_initial[0][1]) if cm_initial is not None else 0,
            "fn": int(cm_initial[1][0]) if cm_initial is not None else 0,
            "tp": int(cm_initial[1][1]) if cm_initial is not None else 0,
        } if cm_initial is not None else {},
    }
    
    with open(results_file, 'w') as f:
        json.dump(results_data, f, indent=2)
    
    logger.info(f"\n✓ Results saved to: {results_file}")
    print(f"\n✓ Results saved to: {results_file}")
    print("\n" + "=" * 80)
    print("COMPARISON INSTRUCTIONS")
    print("=" * 80)
    print("To compare with standard validation, run:")
    print(f"  python test_with_metrics.py --project {project}")
    print(f"\nCompare the metrics and confusion matrices to evaluate if experimental")
    print("features improve validation accuracy.")
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test experimental validation pipeline")
    parser.add_argument(
        "--project",
        type=str,
        default="lido",
        choices=["lido", "uniswap"],
        help="Project to test (default: lido)"
    )
    parser.add_argument(
        "--final-only",
        action="store_true",
        help="Only show final results (skip intermediate confusion matrix)"
    )
    
    args = parser.parse_args()
    
    asyncio.run(test_experimental_pipeline(project=args.project, final_only=args.final_only))

