"""
Enhanced test script with detailed logging and confusion matrix calculation.
Tests the complete pipeline and generates metrics.
Supports both Lido and Uniswap projects.
"""
import sys
import os
import json
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timezone
from bson import ObjectId
from typing import Dict, List, Any
from sklearn.metrics import confusion_matrix, precision_score, recall_score, f1_score, accuracy_score

# Ensure we're using the right Python path
if '/home/mahavir/Desktop/Auditagent/ai-auditor/agentarena-arbiter/Demo' not in sys.path:
    sys.path.insert(0, '/home/mahavir/Desktop/Auditagent/ai-auditor/agentarena-arbiter/Demo')

# Import built-ins first
import logging

# Configure detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('test_pipeline.log')
    ]
)
logger = logging.getLogger(__name__)

# Now import app modules
from app.core.deduplication import FindingDeduplication
from app.core.evaluation import FindingEvaluator
from app.core.critic_utils import assign_temporary_indexes
from app.core.contract_grouping import ContractFile
from app.models.finding_input import Finding, Severity
from app.models.finding_db import FindingDB, Status
from app.types import TaskCache, QAPair
from app.core.trusted_entity.orchestrator import run_trusted_entity_analysis


def load_original_findings(json_path: str) -> Dict[str, Dict]:
    """Load original findings and create a map by title+description."""
    with open(json_path, "r") as f:
        data = json.load(f)
    
    findings_map = {}
    for item in data:
        # Create a unique key from title and description
        key = f"{item.get('title', '')}|||{item.get('description', '')[:100]}"
        
        # Convert reviewer_rating to boolean
        reviewer_rating = item.get("reviewer_rating")
        if reviewer_rating:
            if isinstance(reviewer_rating, str):
                reviewer_rating = reviewer_rating.lower().strip() == "approved"
            elif isinstance(reviewer_rating, bool):
                pass  # Already boolean
            else:
                reviewer_rating = None
        
        findings_map[key] = {
            "title": item.get("title", ""),
            "description": item.get("description", ""),
            "severity": item.get("severity", ""),
            "file_paths": item.get("file_paths", []),
            "reviewer_rating": reviewer_rating,  # True/False/None
        }
    
    return findings_map


def infer_category(title: str, description: str):
    """Infer category from finding content."""
    from app.core.category_utils import CategoryEnum
    text = (title + " " + description).lower()
    
    if "unchecked" in text and ("return" in text or "call" in text):
        return CategoryEnum.UNCHECKED_CALL
    elif "reentrancy" in text or "re-entrant" in text:
        return CategoryEnum.REENTRANCY
    elif "denial" in text or "dos" in text or "unfillable" in text:
        return CategoryEnum.DENIAL_OF_SERVICE
    elif "access control" in text or "unauthorized" in text:
        return CategoryEnum.ACCESS_CONTROL
    elif "centralization" in text:
        return CategoryEnum.CENTRALIZATION_RISK
    elif "overflow" in text or "underflow" in text:
        return CategoryEnum.INTEGER_OVERFLOW_UNDERFLOW
    elif "precision" in text or "rounding" in text:
        return CategoryEnum.PRECISION_LOSS
    elif "front" in text and "run" in text:
        return CategoryEnum.FRONT_RUNNING
    elif "business logic" in text:
        return CategoryEnum.BUSINESS_LOGIC
    else:
        return CategoryEnum.OTHER


def convert_findings_from_json(json_path: str) -> list[Finding]:
    """Convert JSON findings to Finding objects with category inference."""
    logger.info(f"Loading findings from {json_path}")
    with open(json_path, "r") as f:
        data = json.load(f)
    
    findings = []
    for item in data:
        severity = Severity(item.get("severity", "Medium"))
        
        # Infer category
        category = infer_category(
            item.get("title", ""),
            item.get("description", "")
        )
        
        finding = Finding(
            title=item.get("title", "Untitled Finding"),
            description=item.get("description", ""),
            severity=severity,
            file_paths=item.get("file_paths", [])
        )
        # Store category as attribute (FindingDB will need this)
        finding.category = category
        findings.append(finding)
    
    logger.info(f"✓ Converted {len(findings)} findings with categories")
    return findings


def load_contracts_from_info(info_json_path: str, repo_path: str) -> dict[str, ContractFile]:
    """Load contracts based on selectedFiles from info JSON."""
    logger.info(f"Loading contracts from {info_json_path}")
    with open(info_json_path, "r") as f:
        info_data = json.load(f)
    
    info = info_data[0] if isinstance(info_data, list) else info_data
    selected_files = info.get("selectedFiles", [])
    
    repo_path_obj = Path(repo_path)
    contract_contents = {}
    
    for file_path in selected_files:
        full_path = repo_path_obj / file_path
        
        if not full_path.exists():
            logger.warning(f"File not found: {full_path}")
            continue
        
        with open(full_path, "r") as f:
            content = f.read()
        
        filename_stem = full_path.stem
        token_count = len(content.split())
        
        contract_file = ContractFile(content=content, token_count=token_count)
        contract_contents[filename_stem] = contract_file
        logger.info(f"  ✓ Loaded {full_path.name} ({token_count} tokens) -> key: {filename_stem}")
    
    logger.info(f"✓ Loaded {len(contract_contents)} contract files")
    return contract_contents


def prepare_context_from_info(info_json_path: str) -> tuple[str, str]:
    """Extract summary and dev_doc from info JSON."""
    logger.info(f"Preparing context from {info_json_path}")
    with open(info_json_path, "r") as f:
        info_data = json.load(f)
    
    info = info_data[0] if isinstance(info_data, list) else info_data
    
    summary = info.get("description", "None Given")
    
    # Dev doc = additionalDocs + qaResponses
    additional_docs = info.get("additionalDocs", "")
    qa_responses = info.get("qaResponses", [])
    
    # Format Q&A responses
    qa_text = "\n\n## Q&A Responses:\n"
    for qa in qa_responses:
        qa_text += f"\n**Q: {qa.get('question', '')}**\n"
        qa_text += f"A: {qa.get('answer', '')}\n"
    
    # Assemble dev_doc (can be empty string)
    dev_doc = additional_docs + qa_text if additional_docs else qa_text
    
    logger.info(f"✓ Summary: {len(summary)} chars, Dev doc: {len(dev_doc)} chars")
    return summary, dev_doc


def convert_findings_to_findingdb(findings: list[Finding], agent_id: str = "test_agent") -> list[FindingDB]:
    """Convert Finding objects to FindingDB objects."""
    logger.info(f"Converting {len(findings)} findings to FindingDB objects")
    finding_dbs = []
    for finding in findings:
        finding_db = FindingDB(
            id=ObjectId(),
            agent_id=agent_id,
            title=finding.title,
            description=finding.description,
            severity=finding.severity,
            file_paths=finding.file_paths,
            status=Status.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        finding_dbs.append(finding_db)
    logger.info(f"✓ Converted to {len(finding_dbs)} FindingDB objects")
    return finding_dbs


def build_task_cache_from_info(
    info_json_path: str,
    repo_path: str,
    contract_contents: dict[str, ContractFile]
) -> TaskCache:
    """Build TaskCache from info JSON and contract contents."""
    logger.info("Building TaskCache")
    with open(info_json_path, "r") as f:
        info_data = json.load(f)
    
    info = info_data[0] if isinstance(info_data, list) else info_data
    
    all_contract_content = "\n\n".join([
        f"//--- File: {name} ---\n{cf.content}"
        for name, cf in contract_contents.items()
    ])
    
    docs_parts = []
    selected_docs = info.get("selectedDocs", [])
    if selected_docs:
        repo_path_obj = Path(repo_path)
        for doc_path in selected_docs:
            full_path = repo_path_obj / doc_path
            if full_path.exists():
                with open(full_path, "r") as f:
                    docs_parts.append(f.read())
    
    docs_content = "\n\n".join(docs_parts) if docs_parts else ""
    
    qa_responses = []
    for qa in info.get("qaResponses", []):
        qa_responses.append(QAPair(
            question=qa.get("question", ""),
            answer=qa.get("answer", "")
        ))
    
    task_cache = TaskCache(
        taskId="TEST_TASK",
        selectedFilesContent=all_contract_content,
        selectedDocsContent=docs_content,
        additionalDocs=info.get("additionalDocs"),
        additionalLinks=info.get("additionalLinks", []),
        qaResponses=qa_responses
    )
    
    logger.info(f"✓ TaskCache built with {len(task_cache.selectedFilesContent)} chars of contract content")
    return task_cache


def normalize_text(text: str) -> str:
    """Normalize text for matching."""
    return " ".join(text.lower().strip().split())


def match_findings_with_ground_truth(
    deduplicated_findings: List[FindingDB],
    final_findings: List[FindingDB],
    original_map: Dict[str, Dict]
) -> List[Dict[str, Any]]:
    """Match deduplicated findings with original findings for ground truth."""
    logger.info("Matching findings with ground truth")
    
    # Create a set of final finding IDs for quick lookup
    final_finding_ids = {f.str_id for f in final_findings}
    
    matched = []
    for finding in deduplicated_findings:
        # Try exact match first
        key = f"{finding.title}|||{finding.description[:100]}"
        
        original = None
        if key in original_map:
            original = original_map[key]
        else:
            # Try fuzzy match by normalizing title
            finding_title_norm = normalize_text(finding.title)
            for orig_key, orig_data in original_map.items():
                orig_title = orig_data.get("title", "")
                if normalize_text(orig_title) == finding_title_norm:
                    original = orig_data
                    break
        
        if original:
            reviewer_rating = original.get("reviewer_rating")
            
            # Only include findings with valid ground truth (True or False, not None)
            if reviewer_rating is not None and isinstance(reviewer_rating, bool):
                # Check if this finding was kept by validation
                kept_by_validation = finding.str_id in final_finding_ids
                
                matched.append({
                    "finding_id": finding.str_id,
                    "title": finding.title,
                    "ground_truth": reviewer_rating,  # True = approved, False = disapproved
                    "kept_by_validation": kept_by_validation,  # True = kept, False = removed
                })
    
    logger.info(f"✓ Matched {len(matched)} findings with valid ground truth")
    return matched


def calculate_metrics(y_true: List[bool], y_pred: List[bool]) -> Dict[str, float]:
    """Calculate precision, recall, F1, and accuracy."""
    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    accuracy = accuracy_score(y_true, y_pred)
    
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
    }


def print_confusion_matrix(cm, labels):
    """Print confusion matrix in a readable format."""
    print("\n" + "=" * 60)
    print("CONFUSION MATRIX")
    print("=" * 60)
    print(f"\n{'':<20} {'Predicted: Invalid':<25} {'Predicted: Valid':<25}")
    print("-" * 70)
    print(f"{'Actual: Invalid':<20} {cm[0][0]:<25} {cm[0][1]:<25}")
    print(f"{'Actual: Valid':<20} {cm[1][0]:<25} {cm[1][1]:<25}")
    print("-" * 70)
    
    tn, fp, fn, tp = cm[0][0], cm[0][1], cm[1][0], cm[1][1]
    print(f"\nTrue Negatives (TN):  {tn:>4} - Invalid findings correctly removed")
    print(f"False Positives (FP): {fp:>4} - Invalid findings incorrectly kept")
    print(f"False Negatives (FN): {fn:>4} - Valid findings incorrectly removed")
    print(f"True Positives (TP):  {tp:>4} - Valid findings correctly kept")


def get_project_paths(project: str, repo_root: Path) -> tuple[Path, Path, Path]:
    """
    Get file paths for the specified project.
    
    Args:
        project: Project name ('lido' or 'uniswap')
        repo_root: Root directory of the repository
        
    Returns:
        Tuple of (findings_json, info_json, repo_path)
    """
    if project.lower() == "lido":
        return (
            repo_root / "lido_findings.json",
            repo_root / "lido_information.json",
            repo_root / "lido_analysis" / "lido-earn"
        )
    elif project.lower() == "uniswap":
        return (
            repo_root / "uniswap_findings.json",
            repo_root / "uniswap_information.json",
            repo_root / "Uniswap_analysis" / "uniswapx"
        )
    else:
        raise ValueError(f"Unknown project: {project}. Must be 'lido' or 'uniswap'")


async def test_pipeline(project: str = "lido", final_only: bool = False):
    """
    Test the complete pipeline with detailed logging and metrics.
    
    Args:
        project: Project name ('lido' or 'uniswap'), defaults to 'lido'
    """
    print("=" * 80)
    print(f"TESTING VALIDATION PIPELINE - {project.upper()}")
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
    
    # Step 3: Prepare context
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: Preparing Context")
    logger.info("=" * 80)
    summary, dev_doc = prepare_context_from_info(str(info_json))
    
    # Step 4: Convert to FindingDB and assign indexes
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: Converting to FindingDB and Assigning Indexes")
    logger.info("=" * 80)
    finding_dbs = convert_findings_to_findingdb(findings, agent_id="test_agent")
    indexed_findings = assign_temporary_indexes(finding_dbs)
    logger.info(f"✓ Assigned indexes to {len(indexed_findings)} findings")
    
    # Step 5: Build TaskCache
    logger.info("\n" + "=" * 80)
    logger.info("STEP 5: Building TaskCache")
    logger.info("=" * 80)
    task_cache = build_task_cache_from_info(str(info_json), str(repo_path), contract_contents)
    
    # Step 6: Test Deduplication
    logger.info("\n" + "=" * 80)
    logger.info("STEP 6: Testing Deduplication (Hierarchical Batch Processing)")
    logger.info("=" * 80)
    deduplicator = FindingDeduplication()
    
    original_count = len(indexed_findings)
    logger.info(f"Starting deduplication with {original_count} findings...")
    
    try:
        # Pass contract_contents directly to ensure consistency
        dedup_results = await deduplicator.process_findings(
            task_id="TEST_TASK",
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
        logger.info(f"  - Duplicate relationships: {len(dedup_results['deduplication']['duplicate_relationships'])}")
        
    except Exception as e:
        logger.error(f"✗ Deduplication failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 7: Test Validation
    logger.info("\n" + "=" * 80)
    logger.info("STEP 7: Testing Validation (One-by-One 3-Step Process)")
    logger.info("=" * 80)
    evaluator = FindingEvaluator()
    
    logger.info(f"Starting validation with {deduplicated_count} deduplicated findings...")
    logger.info("This may take several minutes as we validate each finding individually...")
    
    # Initialize variables for confusion matrix calculation
    matched_initial = []
    cm_initial = None
    metrics_initial = None
    initial_validated_count = 0
    
    try:
        # Pass contract_contents, summary, and dev_doc directly to ensure consistency
        evaluation_results = await evaluator.evaluate_all_findings(
            task_id="TEST_TASK",
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
        for f in deduplicated_findings:
            eval_result = evaluation_results_map.get(f.str_id) if isinstance(evaluation_results_map, dict) else None
            if eval_result and hasattr(eval_result, 'final_result') and eval_result.final_result:
                final_findings.append(f)
        
        validated_count = len(final_findings)
        disputed_count = deduplicated_count - validated_count
        
        logger.info(f"✓ Validation complete:")
        logger.info(f"  - After deduplication: {deduplicated_count}")
        logger.info(f"  - After validation: {validated_count}")
        logger.info(f"  - Disputed (removed): {disputed_count}")
        
        # Calculate Confusion Matrix AFTER INITIAL VALIDATION
        logger.info("\n" + "=" * 80)
        logger.info("CALCULATING CONFUSION MATRIX AFTER INITIAL VALIDATION")
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
            logger.info(f"Initial Validation Metrics:")
            logger.info(f"  Precision: {metrics_initial['precision']:.4f} ({metrics_initial['precision']*100:.2f}%)")
            logger.info(f"  Recall:    {metrics_initial['recall']:.4f} ({metrics_initial['recall']*100:.2f}%)")
            logger.info(f"  F1 Score:  {metrics_initial['f1']:.4f} ({metrics_initial['f1']*100:.2f}%)")
            logger.info(f"  Accuracy:  {metrics_initial['accuracy']:.4f} ({metrics_initial['accuracy']*100:.2f}%)")
        else:
            matched_initial = []
            cm_initial = None
            metrics_initial = None
            logger.warning("⚠ No findings matched with ground truth for initial validation.")
        
    except Exception as e:
        logger.error(f"✗ Validation failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 8: Trusted Entity Analysis
    logger.info("\n" + "=" * 80)
    logger.info("STEP 8: Trusted Entity Analysis (Categorization + Tri-Cameral Ensemble)")
    logger.info("=" * 80)
    
    te_result = None
    try:
        logger.info(f"Starting trusted entity analysis for {validated_count} validated findings...")
        te_result = await run_trusted_entity_analysis(
            validated_findings=final_findings,
            task_cache=task_cache,
            summary=summary,
            dev_doc=dev_doc
        )
        
        logger.info(f"✓ Trusted Entity Analysis complete:")
        logger.info(f"  - Total findings analyzed: {te_result.stats['total_findings']}")
        logger.info(f"  - Trusted entity findings: {te_result.stats['trusted_entity_count']}")
        logger.info(f"  - Valid (score >= 3.0): {te_result.stats['valid_count']}")
        logger.info(f"  - Likely valid (score >= 1.5): {te_result.stats['likely_valid_count']}")
        logger.info(f"  - Invalid (score < 1.5): {te_result.stats['invalid_count']}")
        logger.info(f"  - Final validated findings: {len(te_result.final_validated_findings)}")
        
        # Update final_findings to use trusted entity analysis results
        final_findings = te_result.final_validated_findings
        validated_count = len(final_findings)
        
    except Exception as e:
        logger.error(f"✗ Trusted Entity Analysis failed: {str(e)}")
        import traceback
        traceback.print_exc()
        logger.warning("Continuing with validation results only...")
        # Continue with original final_findings if trusted entity analysis fails
    
    # Calculate Confusion Matrix AFTER TRUSTED ENTITY ANALYSIS
    logger.info("\n" + "=" * 80)
    logger.info("STEP 9: Calculating Final Confusion Matrix and Metrics (After Trusted Entity Analysis)")
    logger.info("=" * 80)
    
    matched_final = match_findings_with_ground_truth(
        deduplicated_findings=deduplicated_findings,
        final_findings=final_findings,
        original_map=original_map
    )
    
    if not matched_final:
        logger.warning("⚠ No findings matched with ground truth. Cannot generate confusion matrix.")
        return
    
    # Prepare labels for FINAL results
    y_true_final = [m["ground_truth"] for m in matched_final]
    y_pred_final = [m["kept_by_validation"] for m in matched_final]
    
    # Calculate confusion matrix for FINAL results
    cm_final = confusion_matrix(y_true_final, y_pred_final, labels=[False, True])
    
    # Calculate metrics for FINAL results
    metrics_final = calculate_metrics(y_true_final, y_pred_final)
    
    # Extract values for FINAL
    tn_final = int(cm_final[0][0])
    fp_final = int(cm_final[0][1])
    fn_final = int(cm_final[1][0])
    tp_final = int(cm_final[1][1])
    
    # Add to metrics
    metrics_final["tp"] = tp_final
    metrics_final["tn"] = tn_final
    metrics_final["fp"] = fp_final
    metrics_final["fn"] = fn_final
    
    # Print results
    print("\n" + "=" * 80)
    print("PIPELINE TEST SUMMARY")
    print("=" * 80)
    print(f"Original findings: {original_count}")
    print(f"After deduplication: {deduplicated_count} (removed {removed_count})")
    initial_validated_count = len([f for f in deduplicated_findings 
                                   if (isinstance(evaluation_results_map, dict) and 
                                       evaluation_results_map.get(f.str_id) and 
                                       hasattr(evaluation_results_map.get(f.str_id), 'final_result') and 
                                       evaluation_results_map.get(f.str_id).final_result)])
    print(f"After initial validation: {initial_validated_count}")
    if te_result:
        print(f"After trusted entity analysis: {validated_count}")
        print(f"\nTrusted Entity Analysis:")
        print(f"  - Trusted entity findings identified: {te_result.stats['trusted_entity_count']}")
        print(f"  - Validated by tri-cameral: {te_result.stats['valid_count'] + te_result.stats['likely_valid_count']}")
        print(f"  - Rejected by tri-cameral: {te_result.stats['invalid_count']}")
    print(f"\nTotal reduction: {original_count} → {validated_count} ({original_count - validated_count} removed)")
    print(f"Reduction rate: {((original_count - validated_count) / original_count * 100):.1f}%")
    
    # Show CONFUSION MATRIX AFTER INITIAL VALIDATION (only if not final_only)
    if not final_only:
    print("\n" + "=" * 80)
        print("CONFUSION MATRIX AFTER INITIAL VALIDATION")
    print("=" * 80)
        if matched_initial and cm_initial is not None:
            print(f"\nMatched findings with ground truth: {len(matched_initial)}")
            print_confusion_matrix(cm_initial, ["Invalid", "Valid"])
    
            print("\n" + "-" * 80)
            print("PERFORMANCE METRICS (After Initial Validation)")
            print("-" * 80)
            print(f"Precision: {metrics_initial['precision']:.4f} ({metrics_initial['precision']*100:.2f}%)")
            print(f"  → Of findings we kept ({initial_validated_count}), {metrics_initial['precision']*100:.2f}% were actually approved by reviewers")
            print(f"\nRecall:    {metrics_initial['recall']:.4f} ({metrics_initial['recall']*100:.2f}%)")
            print(f"  → We kept {metrics_initial['recall']*100:.2f}% of all approved findings")
            print(f"\nF1 Score:  {metrics_initial['f1']:.4f} ({metrics_initial['f1']*100:.2f}%)")
            print(f"\nAccuracy:  {metrics_initial['accuracy']:.4f} ({metrics_initial['accuracy']*100:.2f}%)")
        else:
            print("⚠ No matched findings for initial validation confusion matrix")
    
    # Show CONFUSION MATRIX AFTER TRUSTED ENTITY ANALYSIS
    print("\n" + "=" * 80)
    print("CONFUSION MATRIX AFTER TRUSTED ENTITY ANALYSIS (FINAL)")
    print("=" * 80)
    print(f"\nMatched findings with ground truth: {len(matched_final)}")
    print_confusion_matrix(cm_final, ["Invalid", "Valid"])
    
    print("\n" + "-" * 80)
    print("PERFORMANCE METRICS (After Trusted Entity Analysis - FINAL)")
    print("-" * 80)
    print(f"\nPrecision: {metrics_final['precision']:.4f} ({metrics_final['precision']*100:.2f}%)")
    print(f"  → Of findings we kept ({validated_count}), {metrics_final['precision']*100:.2f}% were actually approved by reviewers")
    print(f"\nRecall:    {metrics_final['recall']:.4f} ({metrics_final['recall']*100:.2f}%)")
    print(f"  → We kept {metrics_final['recall']*100:.2f}% of all approved findings")
    print(f"\nF1 Score:  {metrics_final['f1']:.4f} ({metrics_final['f1']*100:.2f}%)")
    print(f"  → Harmonic mean of precision and recall")
    print(f"\nAccuracy:  {metrics_final['accuracy']:.4f} ({metrics_final['accuracy']*100:.2f}%)")
    print(f"  → Overall correctness: {metrics_final['accuracy']*100:.2f}%")
    
    # Display Trusted Entity Analysis Details
    if te_result and te_result.tri_cameral_results:
        print("\n" + "=" * 80)
        print("TRUSTED ENTITY ANALYSIS DETAILS")
        print("=" * 80)
        print(f"\nTotal trusted entity findings: {len(te_result.tri_cameral_results)}")
        print("\nTri-Cameral Ensemble Scores:")
        print("-" * 80)
        for tri_result in te_result.tri_cameral_results:
            print(f"\nFinding: {tri_result.finding_id}")
            print(f"  Category: {next((cat['category_type'] for cat in te_result.categorized_findings if cat['finding_id'] == tri_result.finding_id), 'N/A')}")
            print(f"  Agent A (Lawyer):     {'✓ Valid' if tri_result.agent_lawyer.is_valid else '✗ Invalid'} - {tri_result.agent_lawyer.reasoning[:100]}...")
            print(f"  Agent B (Mathematician): {'✓ Valid' if tri_result.agent_mathematician.is_valid else '✗ Invalid'} - {tri_result.agent_mathematician.reasoning[:100]}...")
            print(f"  Agent C (Safety):     {'✓ Valid' if tri_result.agent_safety.is_valid else '✗ Invalid'} - {tri_result.agent_safety.reasoning[:100]}...")
            print(f"  Score: {tri_result.score:.1f}")
            print(f"  Final Verdict: {tri_result.final_verdict}")
            print(f"  Reasoning: {tri_result.reasoning}")
        
        # Summary statistics
        valid_scores = [r.score for r in te_result.tri_cameral_results if r.final_verdict == "VALID"]
        likely_valid_scores = [r.score for r in te_result.tri_cameral_results if r.final_verdict == "LIKELY_VALID"]
        invalid_scores = [r.score for r in te_result.tri_cameral_results if r.final_verdict == "INVALID"]
        
        print("\n" + "-" * 80)
        print("Score Distribution:")
        if valid_scores:
            print(f"  VALID (score >= 3.0): {len(valid_scores)} findings, avg score: {sum(valid_scores)/len(valid_scores):.2f}")
        if likely_valid_scores:
            print(f"  LIKELY_VALID (1.5 <= score < 3.0): {len(likely_valid_scores)} findings, avg score: {sum(likely_valid_scores)/len(likely_valid_scores):.2f}")
        if invalid_scores:
            print(f"  INVALID (score < 1.5): {len(invalid_scores)} findings, avg score: {sum(invalid_scores)/len(invalid_scores):.2f}")
    
    print("\n" + "=" * 80)
    print("FINAL RESULTS SUMMARY")
    print("=" * 80)
    print(f"\nResults (After Initial Validation):")
    if metrics_initial:
        print(f"  - Precision: {metrics_initial['precision']*100:.2f}%")
        print(f"  - Recall:    {metrics_initial['recall']*100:.2f}%")
        print(f"  - F1 Score:  {metrics_initial['f1']*100:.2f}%")
        print(f"  - Accuracy:  {metrics_initial['accuracy']*100:.2f}%")
    
    print(f"\nResults (After Trusted Entity Analysis - FINAL):")
    print(f"  - Precision: {metrics_final['precision']*100:.2f}%")
    print(f"  - Recall:    {metrics_final['recall']*100:.2f}%")
    print(f"  - F1 Score:  {metrics_final['f1']*100:.2f}%")
    print(f"  - Accuracy:  {metrics_final['accuracy']*100:.2f}%")
    
    # Save results to file
    results_file = Path(__file__).parent / "test_results.json"
    results_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_count": original_count,
            "after_deduplication": deduplicated_count,
        "after_initial_validation": initial_validated_count,
        "after_trusted_entity_analysis": validated_count,
            "removed_by_dedup": removed_count,
            "removed_by_validation": disputed_count,
        "initial_validation": {
            "matched_findings": len(matched_initial) if matched_initial else 0,
            "metrics": metrics_initial if metrics_initial else {},
            "confusion_matrix": {
                "tn": int(cm_initial[0][0]) if cm_initial is not None else 0,
                "fp": int(cm_initial[0][1]) if cm_initial is not None else 0,
                "fn": int(cm_initial[1][0]) if cm_initial is not None else 0,
                "tp": int(cm_initial[1][1]) if cm_initial is not None else 0,
            } if cm_initial is not None else {}
        },
        "final_validation": {
            "matched_findings": len(matched_final),
            "metrics": metrics_final,
            "confusion_matrix": {
                "tn": tn_final,
                "fp": fp_final,
                "fn": fn_final,
                "tp": tp_final,
            }
        }
    }
    
    # Add trusted entity analysis results if available
    if te_result:
        results_data["trusted_entity_analysis"] = {
            "stats": te_result.stats,
            "categorized_findings": te_result.categorized_findings,
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
                for r in te_result.tri_cameral_results
            ]
        }
    
    with open(results_file, "w") as f:
        json.dump(results_data, f, indent=2)
    
    logger.info(f"\n✓ Results saved to {results_file}")
    print("\n" + "=" * 80)
    print("✓ TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the complete pipeline with metrics")
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
        help="Show only the final confusion matrix (after trusted entity analysis)"
    )
    args = parser.parse_args()
    
    asyncio.run(test_pipeline(project=args.project, final_only=args.final_only))

