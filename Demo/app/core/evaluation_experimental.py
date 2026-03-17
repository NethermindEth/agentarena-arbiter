"""
Experimental finding evaluation module with enhanced features:
1. Extra files section for additional context
2. Enhanced SGR (Self-Generated Reasoning) with confusion identification and retry

This is an experimental version for testing purposes only.
"""
import json
import asyncio
import logging
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path

from app.models.finding_db import FindingDB, Status
from app.models.finding_input import Severity
from app.types import TaskCache
from app.core.contract_grouping import ContractFile
from app.core.critic_utils import get_finding_contract_code
from app.core.prompts_experimental import VALIDATION_PROMPT_EXPERIMENTAL, VALIDATION_PROMPT_EXPERIMENTAL_NO_EXTRA
from app.core.claude_model import (
    create_claude_model,
    DirectValidationResult,
    ValidationStep,
)
from app.config import config
import anthropic
from app.database.mongodb_handler import mongodb
from datetime import datetime, timezone
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ValidationStepExperimental(BaseModel):
    """Single step in the validation process (experimental version)."""
    reasoning: str = Field(description="Reasoning for this step")
    step_result: bool = Field(description="Result of this step (True = passed, False = failed)")


class DirectValidationResultExperimental(BaseModel):
    """Result of one-by-one validation (experimental version with confusion analysis)."""
    steps: List[ValidationStepExperimental] = Field(description="List of validation steps with reasoning")
    final_result: bool = Field(description="Final decision: True = keep finding, False = discard")
    confusion_analysis: Optional[str] = Field(
        default="",
        description="Optional field for confusion identification and resolution attempt. Only populated if confusion was encountered. Leave empty if no confusion."
    )


class FindingEvaluatorExperimental:
    """
    Experimental finding evaluator with enhanced features:
    - Extra files context
    - Enhanced SGR with confusion identification and retry
    """
    
    def __init__(self, mongodb_client=None):
        """
        Initialize the experimental finding evaluator.
        
        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
        """
        self.mongodb = mongodb_client or mongodb
    
    def _get_extra_files_code(
        self,
        finding: FindingDB,
        contract_contents: Dict[str, ContractFile],
    ) -> str:
        """
        Get extra files code (all files except those in finding's file_paths).
        
        Args:
            finding: The finding being validated
            contract_contents: Dictionary mapping contract names to ContractFile objects
            
        Returns:
            Concatenated string of extra files code, or empty string if no extra files
        """
        # Get file_paths from finding
        file_paths = getattr(finding, 'file_paths', [])
        
        # Extract contract names (stems) from finding's file_paths
        finding_contract_stems = set()
        if file_paths:
            finding_contract_stems = {Path(fp).stem for fp in file_paths}
        else:
            # Fallback to Contracts field if available
            contracts = getattr(finding, 'Contracts', [])
            if contracts:
                finding_contract_stems = {Path(c).stem for c in contracts}
        
        # Get all other contracts (not in finding's file_paths)
        extra_files_code = ""
        extra_count = 0
        
        for contract_name, contract_file in contract_contents.items():
            contract_stem = Path(contract_name).stem
            if contract_stem not in finding_contract_stems:
                extra_files_code += f"//--- Extra File: {contract_name} ---\n"
                extra_files_code += contract_file.content
                extra_files_code += "\n\n"
                extra_count += 1
        
        if extra_count == 0:
            return "No extra files available."
        
        logger.debug(f"[VALIDATION-EXPERIMENTAL] Added {extra_count} extra files for finding {finding.str_id}")
        return extra_files_code
    
    async def _validate_single_finding_with_retry(
        self,
        finding: FindingDB,
        contract_contents: Dict[str, ContractFile],
        contract_language: str,
        summary: str,
        dev_doc: str,
        context_store_paths=None,
    ) -> Optional[DirectValidationResultExperimental]:
        """
        Validates a single finding with enhanced SGR (confusion identification and retry).
        
        Args:
            finding: The finding to validate
            contract_contents: Dictionary mapping contract names to ContractFile objects
            contract_language: The contract's programming language
            summary: The project summary
            dev_doc: The developer documentation
            context_store_paths: Optional context storage paths for retrieval
            
        Returns:
            The DirectValidationResultExperimental from the LLM, or None on error
        """
        try:
            # 1. Get contract code using simple lookup
            finding_contract_code = get_finding_contract_code(finding, contract_contents)
            
            # 2. Get extra files code (all files except those in file_paths)
            extra_files_code = self._get_extra_files_code(finding, contract_contents)
            
            # 2.5. Check token limit: if total context (file_paths + extra files) > 10k tokens, remove extra files
            from app.core.docs_formatter import count_tokens
            
            total_code_tokens = count_tokens(finding_contract_code) + count_tokens(extra_files_code)
            use_extra_files = total_code_tokens <= 10000
            
            if not use_extra_files:
                logger.info(f"[VALIDATION-EXPERIMENTAL] Total code context ({total_code_tokens} tokens) exceeds 10k limit. Removing extra files, keeping only file_paths code.")
                extra_files_code = ""  # Remove extra files, keep only file_paths code
            else:
                logger.debug(f"[VALIDATION-EXPERIMENTAL] Total code context: {total_code_tokens} tokens (within 10k limit, including extra files)")
            
            # 3. Try to retrieve relevant docs from knowledge graph, fallback to dev_doc
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
                        logger.debug(f"[VALIDATION-EXPERIMENTAL] Retrieved {len(retrieved)} chars of relevant docs for finding {finding.str_id}")
                except Exception as e:
                    logger.warning(f"[VALIDATION-EXPERIMENTAL] Retrieval failed, using fallback doc: {e}") 
            
            # 4. Get category metadata using CategoryUtils 
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
            
            # 5. Get optional context: hypothesis and prior reasoning steps
            hypothesis = "{}"  # Default empty - FindingDB doesn't have Property field
            reasoning_steps = "[]"  # Default empty - FindingDB doesn't have ValidationReasoning field
            
            # 6. Format the finding for the prompt 
            # Extract contracts from file_paths 
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
            
            # 7. Format the experimental prompt - use appropriate template based on token limit
            if use_extra_files:
                # Use prompt with extra files section
                prompt = VALIDATION_PROMPT_EXPERIMENTAL.format(
                    hypothesis=hypothesis,
                    summary=summary,  # Can be "None Given" or empty 
                    dev_doc=retrieved_doc,  # Can be empty string 
                    contract_language=contract_language,
                    contract_code=finding_contract_code,
                    vulnerabilities=formatted_finding,
                    reasoning_steps=reasoning_steps,
                    extra_files_code=extra_files_code,  # Extra files section included
                )
            else:
                # Use prompt without extra files section (cleaner, no extra files instructions)
                prompt = VALIDATION_PROMPT_EXPERIMENTAL_NO_EXTRA.format(
                    hypothesis=hypothesis,
                    summary=summary,  # Can be "None Given" or empty 
                    dev_doc=retrieved_doc,  # Can be empty string 
                    contract_language=contract_language,
                    contract_code=finding_contract_code,
                    vulnerabilities=formatted_finding,
                    reasoning_steps=reasoning_steps,
                )
            
            # 8. Call Claude Opus 4.5 for validation with tools
            logger.debug(f"[VALIDATION-EXPERIMENTAL] Validating finding {finding.str_id} with Claude Opus 4.5")
            
            # Create Claude model with Opus 4.5
            claude_model = create_claude_model(
                model_name="claude-opus-4-5-20251101",
                temperature=config.claude_temperature,
                max_tokens=config.claude_max_tokens,
                api_key=config.claude_api_key
            )
            
            # Use Anthropic client directly for better tool support
            client = anthropic.AsyncAnthropic(api_key=config.claude_api_key)
            
            # Define tools for code execution and web search
            tools = [
                {"type": "code_execution"},
                {"type": "web_search"}
            ]
            
            try:
                # Call Claude with tools and high effort
                response = await client.messages.create(
                    model="claude-opus-4-5-20251101",
                    max_tokens=config.claude_max_tokens,
                    temperature=config.claude_temperature,
                    tools=tools,
                    effort="high",
                    messages=[
                        {"role": "user", "content": prompt}
                    ]
                )
                
                # Parse response to structured format
                # Extract content from response
                content = response.content[0].text if response.content else ""
                
                # Try to parse as JSON (structured output)
                try:
                    # Remove markdown code blocks if present
                    json_str = content.strip()
                    if json_str.startswith("```json"):
                        json_str = json_str[7:]
                    if json_str.startswith("```"):
                        json_str = json_str[3:]
                    if json_str.endswith("```"):
                        json_str = json_str[:-3]
                    json_str = json_str.strip()
                    
                    parsed = json.loads(json_str)
                    llm_response = DirectValidationResultExperimental(**parsed)
                except (json.JSONDecodeError, Exception) as e:
                    logger.warning(f"[VALIDATION-EXPERIMENTAL] Failed to parse JSON response: {e}, content: {content[:200]}")
                    # Fallback: try with structured output via langchain
                    structured_model = claude_model.with_structured_output(
                        DirectValidationResultExperimental
                    )
                    llm_response = await structured_model.ainvoke(prompt)
            except Exception as e:
                logger.warning(f"[VALIDATION-EXPERIMENTAL] Error calling Claude with tools: {e}, trying without tools")
                # Fallback: try without tools using langchain
                structured_model = claude_model.with_structured_output(
                    DirectValidationResultExperimental
                )
                llm_response = await structured_model.ainvoke(prompt)
            
            if not llm_response:
                logger.warning(f"[VALIDATION-EXPERIMENTAL] Empty response for finding {finding.str_id}, keeping by default")
                return DirectValidationResultExperimental(
                    steps=[],
                    final_result=True,  # Conservative: keep if validation fails
                    confusion_analysis=""
                )
            
            # Check if confusion was identified - if so, retry with confusion context
            has_confusion = llm_response.confusion_analysis and llm_response.confusion_analysis.strip() and llm_response.confusion_analysis != ""
            if has_confusion:
                logger.info(f"[VALIDATION-EXPERIMENTAL] Confusion identified for finding {finding.str_id}, attempting retry with confusion context")
                logger.debug(f"[VALIDATION-EXPERIMENTAL] Confusion analysis: {llm_response.confusion_analysis[:200]}...")
                
                # Create retry prompt with confusion context (reuse the original prompt format)
                # Use the same prompt template (with or without extra files) as the original
                if use_extra_files:
                    retry_prompt = VALIDATION_PROMPT_EXPERIMENTAL.format(
                        hypothesis=hypothesis,
                        summary=summary,
                        dev_doc=retrieved_doc,
                        contract_language=contract_language,
                        contract_code=finding_contract_code,
                        vulnerabilities=formatted_finding,
                        reasoning_steps=reasoning_steps,
                        extra_files_code=extra_files_code,
                    )
                else:
                    retry_prompt = VALIDATION_PROMPT_EXPERIMENTAL_NO_EXTRA.format(
                        hypothesis=hypothesis,
                        summary=summary,
                        dev_doc=retrieved_doc,
                        contract_language=contract_language,
                        contract_code=finding_contract_code,
                        vulnerabilities=formatted_finding,
                        reasoning_steps=reasoning_steps,
                    )
                
                # Add confusion context to retry prompt
                retry_prompt += f"\n\n## **RETRY CONTEXT - Confusion Identified:**\n"
                retry_prompt += f"You previously identified the following confusion during validation:\n\n"
                retry_prompt += f"```\n{llm_response.confusion_analysis}\n```\n\n"
                retry_prompt += f"**Please attempt to resolve this confusion by:**\n"
                retry_prompt += f"1. Re-examining the contract code more carefully, especially the areas related to your confusion\n"
                retry_prompt += f"2. Looking for additional context in the extra files section that might clarify the issue\n"
                retry_prompt += f"3. Re-evaluating the finding description against the code with the confusion in mind\n"
                retry_prompt += f"4. Considering alternative interpretations of the code behavior\n"
                retry_prompt += f"5. Making a more confident decision based on your re-analysis\n\n"
                retry_prompt += f"After resolving the confusion, provide your updated validation with a clear final_result."
                
                # Retry with confusion context
                try:
                    logger.debug(f"[VALIDATION-EXPERIMENTAL] Retry validation attempt for finding {finding.str_id}")
                    retry_response = await client.messages.create(
                        model="claude-opus-4-5-20251101",
                        max_tokens=config.claude_max_tokens,
                        temperature=config.claude_temperature,
                        tools=tools,
                        effort="high",
                        messages=[
                            {"role": "user", "content": retry_prompt}
                        ]
                    )
                    
                    # Parse retry response
                    retry_content = retry_response.content[0].text if retry_response.content else ""
                    try:
                        retry_json_str = retry_content.strip()
                        if retry_json_str.startswith("```json"):
                            retry_json_str = retry_json_str[7:]
                        if retry_json_str.startswith("```"):
                            retry_json_str = retry_json_str[3:]
                        if retry_json_str.endswith("```"):
                            retry_json_str = retry_json_str[:-3]
                        retry_json_str = retry_json_str.strip()
                        
                        retry_parsed = json.loads(retry_json_str)
                        retry_llm_response = DirectValidationResultExperimental(**retry_parsed)
                        
                        # Update confusion_analysis to include retry outcome
                        # Check if confusion persists after retry
                        confusion_persists = retry_llm_response.confusion_analysis and retry_llm_response.confusion_analysis.strip() and retry_llm_response.confusion_analysis != ""
                        
                        if confusion_persists:
                            # Conservative fallback: if confusion persists after retry, keep the finding
                            updated_confusion = f"{llm_response.confusion_analysis}\n\n[RETRY ATTEMPT] {retry_llm_response.confusion_analysis}\n\n[CONSERVATIVE FALLBACK] Confusion persisted after retry. Keeping finding as conservative default to avoid false negatives."
                            final_result_after_retry = True  # Keep finding if still confused
                            logger.info(f"[VALIDATION-EXPERIMENTAL] Retry completed for finding {finding.str_id}, confusion persisted. Using conservative fallback: keeping finding.")
                        else:
                            # Confusion resolved, use model's decision
                            updated_confusion = f"{llm_response.confusion_analysis}\n\n[RETRY ATTEMPT] Confusion resolved after retry."
                            final_result_after_retry = retry_llm_response.final_result
                            logger.info(f"[VALIDATION-EXPERIMENTAL] Retry completed for finding {finding.str_id}, confusion resolved. final_result: {final_result_after_retry}")
                        
                        return DirectValidationResultExperimental(
                            steps=retry_llm_response.steps,
                            final_result=final_result_after_retry,
                            confusion_analysis=updated_confusion
                        )
                    except (json.JSONDecodeError, Exception) as e:
                        logger.warning(f"[VALIDATION-EXPERIMENTAL] Failed to parse retry JSON response: {e}, using langchain fallback")
                        # Fallback: try with structured output via langchain
                        structured_model = claude_model.with_structured_output(
                            DirectValidationResultExperimental
                        )
                        retry_llm_response = await structured_model.ainvoke(retry_prompt)
                        
                        if retry_llm_response:
                            # Check if confusion persists after retry
                            confusion_persists = retry_llm_response.confusion_analysis and retry_llm_response.confusion_analysis.strip() and retry_llm_response.confusion_analysis != ""
                            
                            if confusion_persists:
                                # Conservative fallback: if confusion persists after retry, keep the finding
                                updated_confusion = f"{llm_response.confusion_analysis}\n\n[RETRY ATTEMPT] {retry_llm_response.confusion_analysis}\n\n[CONSERVATIVE FALLBACK] Confusion persisted after retry. Keeping finding as conservative default to avoid false negatives."
                                final_result_after_retry = True  # Keep finding if still confused
                                logger.info(f"[VALIDATION-EXPERIMENTAL] Retry (langchain fallback) completed for finding {finding.str_id}, confusion persisted. Using conservative fallback: keeping finding.")
                            else:
                                # Confusion resolved, use model's decision
                                updated_confusion = f"{llm_response.confusion_analysis}\n\n[RETRY ATTEMPT] Confusion resolved after retry."
                                final_result_after_retry = retry_llm_response.final_result
                                logger.info(f"[VALIDATION-EXPERIMENTAL] Retry (langchain fallback) completed for finding {finding.str_id}, confusion resolved. final_result: {final_result_after_retry}")
                            
                            return DirectValidationResultExperimental(
                                steps=retry_llm_response.steps,
                                final_result=final_result_after_retry,
                                confusion_analysis=updated_confusion
                            )
                except Exception as e:
                    logger.warning(f"[VALIDATION-EXPERIMENTAL] Retry failed for finding {finding.str_id}: {e}, using original response")
                    # If retry fails, return original response
                    return llm_response
            
            # No confusion identified, return original response
            return llm_response
            
        except Exception as e:
            logger.exception(f"Error validating finding {finding.str_id}: {e}. Keeping it by default.")
            # Conservative: keep finding if validation fails
            return DirectValidationResultExperimental(
                steps=[],
                final_result=True,
                confusion_analysis=f"Exception during validation: {str(e)}"
            )
    
    async def validate_findings_one_by_one(
        self,
        findings: List[FindingDB],
        contract_contents: Dict[str, ContractFile],
        contract_language: str = "solidity",
        summary: str = "",
        dev_doc: str = "",
        context_store_paths=None,
    ) -> Tuple[List[FindingDB], Dict[str, DirectValidationResultExperimental]]:
        """
        Validate findings one-by-one using experimental enhanced validation.
        
        This process iterates through each finding, gathers all relevant context
        (source code, project documentation, extra files), and uses an LLM to perform a
        structured, step-by-step validation with enhanced SGR (confusion identification and retry).
        All findings are processed concurrently.
        
        Args:
            findings: List of findings to validate
            contract_contents: Dictionary mapping contract names to ContractFile objects
            contract_language: The programming language of the contracts
            summary: The project summary
            dev_doc: The developer documentation
            context_store_paths: Optional context storage paths for retrieval
            
        Returns:
            A tuple containing:
            - A list of findings that have been validated and kept
            - A dictionary mapping finding IDs to their validation results (experimental version)
        """
        if not findings:
            return [], {}
        
        logger.info(f"[VALIDATION-EXPERIMENTAL] Validating {len(findings)} findings one-by-one with experimental features...")
        logger.debug(f"[VALIDATION-EXPERIMENTAL] Findings to validate: {[f.title[:50] for f in findings[:5]]}...")
        
        # Process all findings concurrently
        tasks = [
            self._validate_single_finding_with_retry(
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
        validation_results_map: Dict[str, DirectValidationResultExperimental] = {}
        confusion_count = 0
        
        for i, finding in enumerate(findings):
            llm_response = results[i]
            if llm_response:
                validation_results_map[finding.str_id] = llm_response
                if llm_response.confusion_analysis and llm_response.confusion_analysis.strip() and llm_response.confusion_analysis != "":
                    confusion_count += 1
                if llm_response.final_result:
                    validated_findings.append(finding)
                else:
                    logger.info(
                        f"[VALIDATION-EXPERIMENTAL] Discarding finding '{finding.title}' (ID: {finding.str_id}) based on validation."
                    )
        
        logger.info(
            f"[VALIDATION-EXPERIMENTAL] Validation complete: {len(validated_findings)}/{len(findings)} findings kept"
        )
        logger.info(
            f"[VALIDATION-EXPERIMENTAL] Confusion encountered in {confusion_count}/{len(findings)} findings"
        )
        logger.debug(f"[VALIDATION-EXPERIMENTAL] Kept findings: {[f.title[:50] for f in validated_findings[:5]]}...")
        logger.debug(f"[VALIDATION-EXPERIMENTAL] Removed findings: {[f.title[:50] for f in findings if f not in validated_findings][:5]}...")
        
        return validated_findings, validation_results_map
    
    async def apply_evaluation_results(
        self, task_id: str, findings: List[FindingDB], evaluation_results: Dict[str, DirectValidationResultExperimental]
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
                
                # Build evaluation comment from steps and confusion analysis
                step_summaries = []
                for i, step in enumerate(eval_result.steps, 1):
                    step_summaries.append(f"Step {i}: {step.reasoning[:100]}... ({'Pass' if step.step_result else 'Fail'})")
                
                evaluation_comment = f"Validation completed. Steps: {len(eval_result.steps)}. Final: {'Valid' if eval_result.final_result else 'Invalid'}.\n" + "\n".join(step_summaries)
                
                # Add confusion analysis if present
                if eval_result.confusion_analysis and eval_result.confusion_analysis.strip() and eval_result.confusion_analysis != "":
                    evaluation_comment += f"\n\n[EXPERIMENTAL] Confusion Analysis:\n{eval_result.confusion_analysis[:500]}..."
                
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
        Evaluate all findings using experimental one-by-one validation.
        
        Args:
            task_id: Task identifier
            findings: List of findings to evaluate
            duplicate_relationships: Duplicate relationships (for compatibility, not used)
            task_cache: Task context containing smart contract files and documentation
            contract_contents: Direct contract contents (takes precedence over task_cache)
            summary: Direct summary (takes precedence over task_cache)
            dev_doc: Direct dev doc (takes precedence over task_cache)
            
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
        
        logger.info(f"[VALIDATION-EXPERIMENTAL] Starting experimental one-by-one evaluation of {len(findings)} findings")
        
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
                        logger.debug("[VALIDATION-EXPERIMENTAL] Using formatted_docs from TaskCache")
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
                        logger.debug("[VALIDATION-EXPERIMENTAL] Using raw concatenated docs (formatted_docs not available)")
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
        
        # Validate findings one-by-one with experimental features
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
            logger.warning(f"[VALIDATION-EXPERIMENTAL] Completed evaluation: {valid_count} valid, {disputed_count} disputed, {failed_count} failed to update")
        else:
            logger.info(f"[VALIDATION-EXPERIMENTAL] Completed evaluation: {valid_count} valid, {disputed_count} disputed evaluations applied")
        
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

