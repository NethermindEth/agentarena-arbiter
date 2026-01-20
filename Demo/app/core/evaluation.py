"""
Finding evaluation module using one-by-one validation with 3-step process.
"""
import json
import asyncio
import logging
from typing import List, Dict, Any, Tuple, Optional

from app.models.finding_db import FindingDB, Status
from app.models.finding_input import Severity
from app.types import TaskCache
from app.core.contract_grouping import ContractFile
from app.core.critic_utils import get_finding_contract_code
from app.core.prompts import VALIDATION_PROMPT
from app.core.claude_model import (
    DirectValidationResult,
    ValidationStep,
)
from app.core.openai_model import (
    send_prompt_to_openai_async,
    is_web_search_model,
    is_thinking_model,
)
from app.database.mongodb_handler import mongodb
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class FindingEvaluator:
    """
    Handles final evaluation of security findings using one-by-one validation.
    Uses 3-step validation process: Technical Validity → Contextual Validity → Contextual Legitimacy.
    """
    
    def __init__(self, mongodb_client=None):
        """
        Initialize the finding evaluator.
        
        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
        """
        self.mongodb = mongodb_client or mongodb
        self.evaluation_model = self._setup_structured_evaluation_model()
    
    def _setup_structured_evaluation_model(self) -> any:
        """
        Setup structured output model for one-by-one finding validation.
        Using OpenAI O3 with thinking mode.
        
        Returns:
            None (we'll use send_prompt_to_openai_async directly)
        """
        # We'll use send_prompt_to_openai_async directly, so no model object needed
        return None
    
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
    
    
    async def _validate_single_finding(
        self,
        finding: FindingDB,
        contract_contents: Dict[str, ContractFile],
        contract_language: str,
        summary: str,
        dev_doc: str,
        context_store_paths=None,
    ) -> Optional[DirectValidationResult]:
        """
        Validates a single finding by formatting a detailed prompt and querying the LLM.
        
        Args:
            finding: The finding to validate
            contract_contents: Dictionary mapping contract names to ContractFile objects
            contract_language: The contract's programming language
            summary: The project summary
            dev_doc: The developer documentation
            
        Returns:
            The DirectValidationResult from the LLM, or None on error
        """
        try:
            # 1. Get contract code using simple lookup (NO file search)
            finding_contract_code = get_finding_contract_code(finding, contract_contents)
            
            # 2. Try to retrieve relevant docs from knowledge graph, fallback to dev_doc
            retrieved_doc = dev_doc  # Default fallback
            if context_store_paths:
                try:
                    from app.core.retrieval.retrieval_context import build_retrieved_doc_for_finding
                    retrieved = await build_retrieved_doc_for_finding(
                        finding=finding,
                        context_store_paths=context_store_paths,
                        max_iterations=5
                    )
                    if retrieved:
                        retrieved_doc = retrieved
                        logger.debug(f"[VALIDATION] Retrieved {len(retrieved)} chars of relevant docs for finding {finding.str_id}")
                except Exception as e:
                    logger.warning(f"[VALIDATION] Retrieval failed, using fallback doc: {e}") 
            
            # 3. Get category metadata using CategoryUtils 
            from app.core.category_utils import CategoryUtils, CategoryEnum
            
            # Get category from finding (should be set during conversion)
            finding_category = getattr(finding, 'category', None)
            if finding_category is None:
                # Fallback: try to infer from title/description
                from app.core.category_utils import infer_category
                finding_category = infer_category(finding.title, finding.description)
            
            # Validate and get category-specific metadata
            cat_enum = CategoryUtils.validate(finding_category)
            cat_description = CategoryUtils.get_category_description(cat_enum)
            cat_mitigation = CategoryUtils.get_category_mitigation(cat_enum)
            category = cat_enum.value  # String value for prompt
            
            # 4. Get optional context: hypothesis and prior reasoning steps
            hypothesis = "{}"  # Default empty - FindingDB doesn't have Property field
            reasoning_steps = "[]"  # Default empty - FindingDB doesn't have ValidationReasoning field
            
            # 5. Format the finding for the prompt 
            # Extract contracts from file_paths 
            from pathlib import Path
            contracts = [Path(fp).stem for fp in finding.file_paths] if finding.file_paths else []
            
            finding_dict = {
                "Issue": finding.title,
                "Description": finding.description,
                "Severity": finding.severity.value if hasattr(finding.severity, 'value') else str(finding.severity),
                "Contracts": contracts,  # Use contract names (stems), not full paths
                "Category": category,
                "index": getattr(finding, 'index', None),
            }
            formatted_finding = json.dumps(finding_dict, indent=2)
            
            # 6. Format the prompt with all context 
            prompt = VALIDATION_PROMPT.format(
                vulnerability_type=category,
                vulnerability_type_description=cat_description,
                vulnerability_type_mitigation=cat_mitigation,
                hypothesis=hypothesis,
                summary=summary,  # Can be "None Given" or empty 
                dev_doc=retrieved_doc,  # Can be empty string 
                contract_language=contract_language,
                contract_code=finding_contract_code,
                vulnerabilities=formatted_finding,
                reasoning_steps=reasoning_steps,
            )
            
            # 7. Call LLM for validation using OpenAI O3 
            from app.config import config
            
            # Determine web search 
            model_type = config.openai_validation_model
            web_search = is_web_search_model(model_type)
            thinking = True  # Always use thinking for O3
            
            # Call OpenAI with retry logic 
            llm_response = await send_prompt_to_openai_async(
                model_type=model_type,
                messages=prompt,
                response_model=DirectValidationResult,
                thinking=thinking,
                web_search=web_search,
                max_retries=2,  
            )
            
            if not llm_response:
                logger.warning(f"[VALIDATION] Empty response for finding {finding.str_id}, keeping by default")
                return DirectValidationResult(
                    steps=[],
                    final_result=True  # Conservative: keep if validation fails
                )
            
            return llm_response
            
        except Exception as e:
            logger.exception(f"Error validating finding {finding.str_id}: {e}. Keeping it by default.")
            # Conservative: keep finding if validation fails
            return DirectValidationResult(
                steps=[],
                final_result=True
            )
    
    async def validate_findings_one_by_one(
        self,
        findings: List[FindingDB],
        contract_contents: Dict[str, ContractFile],
        contract_language: str = "solidity",
        summary: str = "",
        dev_doc: str = "",
        context_store_paths=None,
    ) -> Tuple[List[FindingDB], Dict[str, DirectValidationResult]]:
        """
        Validate findings one-by-one using a step-by-step LLM analysis.
        
        This process iterates through each finding, gathers all relevant context
        (source code, project documentation), and uses an LLM to perform a
        structured, step-by-step validation to determine if the finding is a true positive.
        All findings are processed concurrently.
        
        Args:
            findings: List of findings to validate
            contract_contents: Dictionary mapping contract names to ContractFile objects
            contract_language: The programming language of the contracts
            summary: The project summary
            dev_doc: The developer documentation
            
        Returns:
            A tuple containing:
            - A list of findings that have been validated and kept
            - A dictionary mapping finding IDs to their validation results
        """
        if not findings:
            return [], {}
        
        logger.info(f"[VALIDATION] Validating {len(findings)} findings one-by-one...")
        logger.debug(f"[VALIDATION] Findings to validate: {[f.title[:50] for f in findings[:5]]}...")
        
        # Process all findings concurrently
        tasks = [
            self._validate_single_finding(
                finding=f,
                contract_contents=contract_contents,
                contract_language=contract_language,
                summary=summary,
                dev_doc=dev_doc,
                context_store_paths=context_store_paths,
            )
            for f in findings
        ]
        
        results = await asyncio.gather(*tasks)
        
        validated_findings = []
        validation_results_map: Dict[str, DirectValidationResult] = {}
        
        for i, finding in enumerate(findings):
            llm_response = results[i]
            if llm_response:
                validation_results_map[finding.str_id] = llm_response
                if llm_response.final_result:
                    validated_findings.append(finding)
                else:
                    logger.info(
                        f"[VALIDATION] Discarding finding '{finding.title}' (ID: {finding.str_id}) based on validation."
                    )
        
        logger.info(
            f"[VALIDATION] Validation complete: {len(validated_findings)}/{len(findings)} findings kept"
        )
        logger.debug(f"[VALIDATION] Kept findings: {[f.title[:50] for f in validated_findings[:5]]}...")
        logger.debug(f"[VALIDATION] Removed findings: {[f.title[:50] for f in findings if f not in validated_findings][:5]}...")
        
        return validated_findings, validation_results_map
    
    async def apply_evaluation_results(
        self, task_id: str, findings: List[FindingDB], evaluation_results: Dict[str, DirectValidationResult]
    ) -> Dict[str, Any]:
        """
        Apply evaluation results to findings in the database.
        
        Args:
            task_id: Task identifier
            findings: List of findings that were evaluated
            evaluation_results: Dictionary mapping finding IDs to validation results
            
        Returns:
            Summary of applied changes
        """
        valid_count = 0
        disputed_count = 0
        failed_count = 0
        
        # Create a map for quick lookup
        findings_map = {f.str_id: f for f in findings}
        
        for finding_id, eval_result in evaluation_results.items():
            try:
                finding = findings_map.get(finding_id)
                if not finding:
                    logger.warning(f"Finding {finding_id} not found in findings list")
                    failed_count += 1
                    continue
                
                # Build evaluation comment from steps
                step_summaries = []
                for i, step in enumerate(eval_result.steps, 1):
                    step_summaries.append(f"Step {i}: {step.reasoning[:100]}... ({'Pass' if step.step_result else 'Fail'})")
                
                evaluation_comment = f"Validation completed. Steps: {len(eval_result.steps)}. Final: {'Valid' if eval_result.final_result else 'Invalid'}.\n" + "\n".join(step_summaries)
                
                update_fields = {
                    "evaluation_comment": evaluation_comment,
                    "updated_at": datetime.now(timezone.utc)
                }
                
                # Set status to DISPUTED for invalid findings
                if not eval_result.final_result:
                    update_fields["status"] = Status.DISPUTED
                
                # Try to update in database, but don't fail if it's not there (e.g., during testing)
                try:
                    success = await self.mongodb.update_finding(task_id, finding_id, update_fields)
                    
                    if success:
                        if eval_result.final_result:
                            valid_count += 1
                            logger.info(f"Successfully updated valid finding {finding_id}")
                        else:
                            disputed_count += 1
                            logger.info(f"Successfully updated finding {finding_id} with status DISPUTED")
                    else:
                        # Update failed, but still count as processed (finding might not exist in DB)
                        if eval_result.final_result:
                            valid_count += 1
                        else:
                            disputed_count += 1
                        logger.warning(f"Finding {finding_id} not found in database, but validation completed")
                except Exception as db_error:
                    # Database update failed, but validation was successful
                    # This can happen during testing when findings aren't in the database
                    if eval_result.final_result:
                        valid_count += 1
                    else:
                        disputed_count += 1
                    logger.warning(f"Could not update finding {finding_id} in database (may not exist): {str(db_error)}")
                    
            except Exception as e:
                failed_count += 1
                logger.error(f"Error applying evaluation for finding '{finding_id}': {str(e)}")
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
        duplicate_relationships: List[Any],  # For compatibility, not used in new approach
        task_cache: TaskCache = None,
        contract_contents: Dict[str, ContractFile] = None,  # Allow direct passing
        summary: str = None,  # Allow direct passing
        dev_doc: str = None,  # Allow direct passing
    ) -> Dict[str, Any]:
        """
        Evaluate all findings using one-by-one validation.
        
        Args:
            task_id: Task identifier
            findings: List of findings to evaluate
            duplicate_relationships: Duplicate relationships (for compatibility, not used)
            task_cache: Task context containing smart contract files and documentation
            
        Returns:
            Summary of evaluation results
        """
        if not findings:
            return {
                "total_findings": 0,
                "batches_processed": 0,
                "evaluation_results": {},
                "application_results": {
                    "total_evaluations": 0,
                    "valid_count": 0,
                    "disputed_count": 0,
                    "failed_count": 0
                }
            }
        
        logger.info(f"Starting one-by-one evaluation of {len(findings)} findings")
        
        # Use provided contract_contents or build from task_cache
        if contract_contents is None:
            if task_cache:
                contract_contents = self._build_contract_contents_from_task_cache(task_cache)
            else:
                logger.error("No contract_contents or task_cache provided")
                return {
                    "total_findings": len(findings),
                    "batches_processed": 0,
                    "evaluation_results": {},
                    "application_results": {
                        "total_evaluations": 0,
                        "valid_count": 0,
                        "disputed_count": 0,
                        "failed_count": 0
                    }
                }
        
        # Extract summary and dev_doc (use provided values or extract from task_cache)
        if summary is None or dev_doc is None:
            if task_cache:
                if summary is None:
                    summary = ""  # Would come from task description in practice
                if dev_doc is None:
                    # Prefer formatted_docs (includes link summaries) over raw concatenation
                    if task_cache.formatted_docs and task_cache.formatted_docs != "None Given":
                        dev_doc = task_cache.formatted_docs
                        logger.debug("[EVALUATION] Using formatted_docs from TaskCache")
                    else:
                        # Fallback to raw concatenation if formatted_docs not available
                    dev_doc_parts = []
                    if task_cache.selectedDocsContent:
                        dev_doc_parts.append(task_cache.selectedDocsContent)
                    if task_cache.additionalDocs:
                        dev_doc_parts.append(task_cache.additionalDocs)
                    if task_cache.qaResponses:
                        qa_text = "\n\n".join([f"Q: {qa.question}\nA: {qa.answer}" for qa in task_cache.qaResponses])
                        dev_doc_parts.append(qa_text)
                    dev_doc = "\n\n".join(dev_doc_parts) if dev_doc_parts else ""
                        logger.debug("[EVALUATION] Using raw concatenated docs (formatted_docs not available)")
            else:
                if summary is None:
                    summary = ""
                if dev_doc is None:
                    dev_doc = ""
        
        # Get context storage paths from task_cache if available
        context_store_paths = None
        if task_cache and hasattr(task_cache, 'context_store_paths') and task_cache.context_store_paths:
            from app.core.context_storage.schema import ContextStoragePaths
            try:
                context_store_paths = ContextStoragePaths(**task_cache.context_store_paths)
            except Exception as e:
                logger.warning(f"Failed to parse context_store_paths: {e}")
        
        # Validate findings one-by-one
        validated_findings, validation_results = await self.validate_findings_one_by_one(
            findings=findings,
            contract_contents=contract_contents,
            contract_language="solidity",
            summary=summary,
            dev_doc=dev_doc,
            context_store_paths=context_store_paths,
        )
        
        # Apply evaluation results to database
        apply_results = await self.apply_evaluation_results(task_id, findings, validation_results)
        
        results = {
            "total_findings": len(findings),
            "batches_processed": 1,  # One "batch" for one-by-one processing
            "evaluation_results": validation_results,
            "application_results": apply_results
        }
        
        disputed_count = apply_results['disputed_count']
        valid_count = apply_results['valid_count']
        failed_count = apply_results['failed_count']
        
        if failed_count > 0:
            logger.warning(f"Completed evaluation: {valid_count} valid, {disputed_count} disputed, {failed_count} failed to update")
        else:
            logger.info(f"Completed evaluation: {valid_count} valid, {disputed_count} disputed evaluations applied")
        
        return results
    
    def _build_contract_contents_from_task_cache(self, task_cache: TaskCache) -> Dict[str, ContractFile]:
        """
        Build contract_contents dictionary from task_cache.
        Parses selectedFilesContent to extract individual contract files.
        
        Args:
            task_cache: Task cache with contract content
            
        Returns:
            Dictionary mapping contract names (stems) to ContractFile objects
        """
        contract_contents = {}
        
        if task_cache and task_cache.selectedFilesContent:
            # Parse selectedFilesContent to extract individual contracts
            # Format: "//--- File: ContractName ---\n...code..."
            content = task_cache.selectedFilesContent
            parts = content.split("//--- File: ")
            
            for part in parts[1:]:  # Skip first empty part
                if "---" in part:
                    lines = part.split("\n", 1)
                    if len(lines) >= 2:
                        contract_name = lines[0].split("---")[0].strip()
                        contract_code = lines[1].split("//--- File:")[0].strip()  # Remove next file marker
                        token_count = len(contract_code.split())
                        # Use stem as key 
                        from pathlib import Path
                        contract_stem = Path(contract_name).stem
                        contract_contents[contract_stem] = ContractFile(
                            content=contract_code,
                            token_count=token_count
                        )
            
            # If no contracts were parsed, create a single entry with all content
            if not contract_contents:
                contract_contents["contract"] = ContractFile(
                    content=content,
                    token_count=len(content.split())
                )
        else:
            # If no content, create empty contract
            contract_contents["contract"] = ContractFile(content="", token_count=0)
        
        return contract_contents

