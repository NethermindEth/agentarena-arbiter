"""
Finding deduplication module using hierarchical batch processing.
Processes findings grouped by contract using index-based deduplication.
"""
import json
import logging
from typing import List, Dict, Any

from app.types import TaskCache
from app.models.finding_db import FindingDB, Status
from app.core.batch_processor import process_in_batches, calculate_optimal_batch_size
from app.core.contract_grouping import process_findings_by_contract_groups, ContractFile
from app.core.critic_utils import (
    filter_findings_by_contracts_in_scope,
    filter_interfaces_findings_and_update_paths,
    assign_temporary_indexes,
    coerce_findings_to_finding,
)
from app.core.prompts import DEDUPLICATE_PROMPT
from app.core.claude_model import create_claude_model
from app.core.gemini_model import DeduplicatedFindings  # Keep the schema, just use Claude to call it
from app.database.mongodb_handler import mongodb

logger = logging.getLogger(__name__)


class FindingDeduplication:
    """
    Handles deduplication of findings using hierarchical batch processing.
    Processes findings grouped by contract using index-based deduplication.
    """
    
    def __init__(self, mongodb_client=None):
        """
        Initialize the finding deduplication handler.
        
        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
        """
        self.mongodb = mongodb_client or mongodb
        # Use Claude for deduplication instead of Gemini
        claude_model = create_claude_model()
        self.deduplication_model = claude_model.with_structured_output(DeduplicatedFindings)
    
    async def _get_deduplication_indexes(self, indexed_findings: List[FindingDB]) -> DeduplicatedFindings:
        """
        Get indexes of findings to keep from LLM using DEDUPLICATE_PROMPT.
        Uses Claude for deduplication.
        
        Args:
            indexed_findings: List of indexed findings to analyze
            
        Returns:
            LLM response with indexes of findings to keep
        """
        # Check if Claude API key is available
        from app.config import config
        if not config.claude_api_key or config.claude_api_key == "":
            logger.warning("[DEDUPLICATION] Claude API key not set, skipping deduplication (keeping all findings)")
            # Return all indexes as fallback
            all_indexes = [getattr(f, 'index', i) for i, f in enumerate(indexed_findings)]
            return DeduplicatedFindings(indexes=all_indexes, removed_duplicates=[])
        
        # Convert findings to public dict format (with index field)
        from pathlib import Path
        from app.core.category_utils import CategoryEnum
        
        findings_dicts = []
        for f in indexed_findings:
            # Extract contract names from file_paths (using stem)
            contracts = [Path(fp).stem for fp in f.file_paths] if f.file_paths else []
            
            # Get actual category
            finding_category = getattr(f, 'category', None)
            if finding_category is None:
                # Fallback: try to infer from title/description
                from app.core.category_utils import infer_category
                finding_category = infer_category(f.title, f.description)
            
            # Get category value
            if isinstance(finding_category, CategoryEnum):
                category_value = finding_category.value
            elif hasattr(finding_category, 'value'):
                category_value = finding_category.value
            else:
                category_value = str(finding_category) if finding_category else CategoryEnum.OTHER.value
            
            finding_dict = {
                "index": getattr(f, 'index', None),
                "Issue": f.title,
                "Description": f.description,
                "Severity": f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
                "Contracts": contracts,  # Use contract name stems, not full paths
                "Category": category_value,  # Use actual inferred category
            }
            findings_dicts.append(finding_dict)
        
        # Format findings for LLM (no indent, compact format)
        formatted_findings = json.dumps(findings_dicts)
        prompt = DEDUPLICATE_PROMPT.format(vulnerabilities=formatted_findings)
        
        # Call LLM
        try:
            llm_response = await self.deduplication_model.ainvoke(prompt)
            
            if not llm_response or not llm_response.indexes:
                logger.warning("[DEDUPLICATION] No findings returned from LLM, returning all findings")
                # Return all indexes as fallback
                all_indexes = [getattr(f, 'index', i) for i, f in enumerate(indexed_findings)]
                return DeduplicatedFindings(indexes=all_indexes, removed_duplicates=[])
            
            return llm_response
        except Exception as e:
            logger.error(f"[DEDUPLICATION] Error calling LLM: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            # Return all indexes as fallback
            all_indexes = [getattr(f, 'index', i) for i, f in enumerate(indexed_findings)]
            return DeduplicatedFindings(indexes=all_indexes, removed_duplicates=[])
    
    def _validate_deduplication_indexes(
        self, indexes: List[int], indexed_findings: List[FindingDB]
    ) -> set[int]:
        """
        Validate and filter indexes returned by LLM.
        
        Args:
            indexes: List of indexes returned by LLM
            indexed_findings: List of indexed findings
            
        Returns:
            Set of valid indexes
        """
        # Create a set of valid indexes
        valid_index_set = {getattr(f, 'index', i) for i, f in enumerate(indexed_findings)}
        
        # Filter out invalid indexes
        valid_indexes = [idx for idx in indexes if idx in valid_index_set]
        
        if len(valid_indexes) != len(indexes):
            logger.warning(
                f"[DEDUPLICATION] LLM returned {len(indexes) - len(valid_indexes)} invalid indexes - filtering them out"
            )
        
        if not valid_indexes:
            logger.warning("[DEDUPLICATION] No valid indexes returned, keeping all findings")
            return valid_index_set
        
        return set(valid_indexes)
    
    async def _deduplicate_findings_within_contract_group(
        self, group_indexed_findings: List[FindingDB]
    ) -> List[FindingDB]:
        """
        Perform hierarchical batched deduplication for a specific group of indexed findings.
        
        Args:
            group_indexed_findings: List of indexed findings in a contract group
            
        Returns:
            List of deduplicated findings
        """
        if not group_indexed_findings:
            return []
        
        if len(group_indexed_findings) == 1:
            logger.info(
                "[DEDUPLICATION] Contract group has a single finding; skipping group deduplication."
            )
            return group_indexed_findings
        
        logger.info(
            f"[DEDUPLICATION] Processing contract group with {len(group_indexed_findings)} findings for deduplication."
        )
        
        batch_size = calculate_optimal_batch_size(items_count=len(group_indexed_findings))
        
        async def _llm_process_batch_for_group(current_batch: List[FindingDB]) -> List[FindingDB]:
            """Process a single batch of indexed findings for deduplication using LLM."""
            try:
                if not current_batch:
                    return []
                
                # Get indexes of findings to keep from LLM
                llm_response = await self._get_deduplication_indexes(current_batch)
                
                # Validate and filter indexes
                valid_indexes = self._validate_deduplication_indexes(llm_response.indexes, current_batch)
                
                # Create a map for efficient lookup
                index_map = {getattr(f, 'index', i): f for i, f in enumerate(current_batch)}
                
                # Get deduplicated findings using returned indexes
                deduplicated_findings_in_batch = [index_map[idx] for idx in valid_indexes if idx in index_map]
                
                logger.info(
                    f"[DEDUPLICATION] Batch deduplication within contract group: {len(deduplicated_findings_in_batch)}/{len(current_batch)} findings kept"
                )
                return deduplicated_findings_in_batch
            except Exception as e:
                error_msg = f"Error in deduplication batch for contract group: {str(e)}"
                logger.exception(error_msg)
                # Return all findings as fallback
                return current_batch
        
        final_deduplicated_group_findings = await process_in_batches(
            items=group_indexed_findings,
            processor=_llm_process_batch_for_group,
            batch_size=batch_size,
            description="findings for deduplication within contract group",
            hierarchical=True,
        )
        
        logger.info(
            f"[DEDUPLICATION] Contract group processing complete: {len(final_deduplicated_group_findings)} findings retained for this group."
        )
        return final_deduplicated_group_findings
    
    async def remove_duplicates_batched(
        self, findings: List[FindingDB], contract_contents: Dict[str, ContractFile]
    ) -> List[FindingDB]:
        """
        Remove duplicate findings using hierarchical batch processing by contract groups.
        
        Args:
            findings: List of findings to deduplicate
            contract_contents: Dictionary mapping contract names to ContractFile objects
            
        Returns:
            List of deduplicated findings
        """
        if not findings:
            return []
        
        if len(findings) == 1:
            logger.info("[DEDUPLICATION] Single finding provided; skipping deduplication.")
            return findings
        
        logger.info(f"[DEDUPLICATION] Removing duplicates from {len(findings)} findings...")
        logger.debug(f"[DEDUPLICATION] Contract contents keys: {list(contract_contents.keys())}")
        
        # Filter findings to contracts in scope
        selected_contracts = list(contract_contents.keys())
        findings = filter_findings_by_contracts_in_scope(
            findings=findings, selected_contracts=selected_contracts
        )
        # Update paths and filter interfaces
        findings = filter_interfaces_findings_and_update_paths(findings, selected_contracts)
        
        # Sort findings by severity (High -> Medium -> Low -> Info)
        severity_order = {
            "High": 0,
            "Medium": 1,
            "Low": 2,
            "Info": 3,
        }
        findings.sort(key=lambda f: severity_order.get(
            f.severity.value if hasattr(f.severity, 'value') else str(f.severity), 
            999
        ))
        logger.info("[DEDUPLICATION] Findings sorted by severity prior to deduplication.")
        
        try:
            # Assign temporary indexes (coerce first)
            indexed_findings = assign_temporary_indexes(
                coerce_findings_to_finding(list(findings))
            )
            
            # Process findings by contract groups
            deduplicated = await process_findings_by_contract_groups(
                findings=indexed_findings,
                contract_contents=contract_contents,
                processor=self._deduplicate_findings_within_contract_group,
                operation_name="deduplication",
            )
            
            logger.info(f"[DEDUPLICATION] Deduplication complete: {len(deduplicated)}/{len(findings)} findings retained")
            return deduplicated
            
        except Exception as e:
            error_msg = f"[DEDUPLICATION] Failed to remove duplicates: {str(e)}"
            logger.exception(error_msg)
            # Return all findings as fallback
            return findings
    
    def _build_duplicate_relationships_from_indexes(
        self, original_findings: List[FindingDB], deduplicated_findings: List[FindingDB]
    ) -> List[Dict[str, Any]]:
        """
        Build duplicate relationships from index-based deduplication results.
        This converts index-based results to ID-based relationships for status management.
        
        Args:
            original_findings: All original findings (with indexes)
            deduplicated_findings: Findings that were kept after deduplication
            
        Returns:
            List of duplicate relationships (for compatibility with existing status system)
        """
        # Create index to finding map
        index_to_finding = {getattr(f, 'index', i): f for i, f in enumerate(original_findings)}
        
        # Get indexes of kept findings
        kept_indexes = {getattr(f, 'index', i) for i, f in enumerate(deduplicated_findings)}
        
        # Find removed findings (duplicates)
        removed_indexes = set(index_to_finding.keys()) - kept_indexes
        
        # Build relationships: for each removed finding, find the best original in the same group
        # For simplicity, we'll use the first kept finding as the original
        # In a more sophisticated implementation, we could analyze which finding is "best"
        relationships = []
        
        if not removed_indexes:
            return relationships
        
        # Group findings by similarity (simplified - in practice, this would use the LLM's grouping)
        # For now, we'll create a simple mapping: removed findings point to the first kept finding
        if kept_indexes:
            # Use the first kept finding as the "original" for all duplicates
            # In practice, the LLM would have determined which is the best original
            first_kept_index = min(kept_indexes)
            original_finding = index_to_finding[first_kept_index]
            
            for removed_index in removed_indexes:
                removed_finding = index_to_finding[removed_index]
                relationships.append({
                    "findingId": removed_finding.str_id,
                    "duplicateOf": original_finding.str_id,
                    "explanation": "Identified as duplicate by hierarchical batch deduplication"
                })
        
        return relationships
    
    def _build_contract_contents_from_task_cache(self, task_cache: TaskCache) -> Dict[str, ContractFile]:
        """
        Build contract_contents dictionary from task_cache.
        This is a simplified version - in practice, you'd parse selectedFilesContent properly.
        
        Args:
            task_cache: Task cache with contract content
            
        Returns:
            Dictionary mapping contract names to ContractFile objects
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
                        contract_contents[contract_name] = ContractFile(
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
    
    async def deduplicate_findings(
        self, findings: List[FindingDB], task_cache: TaskCache = None, contract_contents: Dict[str, ContractFile] = None
    ) -> Dict[str, Any]:
        """
        Deduplicate findings using hierarchical batch processing.
        Maintains compatibility with existing status management system.
        
        Args:
            findings: List of findings to deduplicate
            task_cache: Task context with contract content
            
        Returns:
            Dictionary containing deduplication results and statistics
        """
        if not findings:
            return {
                "total": 0,
                "duplicates": 0,
                "originals": 0,
                "duplicate_relationships": [],
                "original_findings": [],
                "duplicate_findings": []
            }
        
        try:
            logger.info(f"Starting deduplication of {len(findings)} findings")
            
            # Use provided contract_contents or build from task_cache
            if contract_contents is None:
                if task_cache:
                    contract_contents = self._build_contract_contents_from_task_cache(task_cache)
                else:
                    logger.error("No contract_contents or task_cache provided")
                    return {
                        "total": len(findings),
                        "duplicates": 0,
                        "originals": len(findings),
                        "duplicate_relationships": [],
                        "original_findings": findings,
                        "duplicate_findings": [],
                        "error": "No contract_contents or task_cache provided"
                    }
            
            # Perform hierarchical batch deduplication
            deduplicated = await self.remove_duplicates_batched(findings, contract_contents)
            
            # Build duplicate relationships for status management
            duplicate_relationships = self._build_duplicate_relationships_from_indexes(
                findings, deduplicated
            )
            
            # Separate findings into duplicates and originals
            kept_ids = {f.str_id for f in deduplicated}
            duplicate_findings = [f for f in findings if f.str_id not in kept_ids]
            original_findings = [f for f in findings if f.str_id in kept_ids]
            
            results = {
                "total": len(findings),
                "duplicates": len(duplicate_findings),
                "originals": len(original_findings),
                "duplicate_relationships": duplicate_relationships,
                "original_findings": original_findings,
                "duplicate_findings": duplicate_findings
            }
            
            logger.info(f"Deduplication completed: {len(original_findings)} originals, {len(duplicate_findings)} duplicates")
            
            return results
            
        except Exception as e:
            logger.error(f"Error during deduplication: {str(e)}")
            return {
                "total": len(findings),
                "duplicates": 0,
                "originals": len(findings),
                "duplicate_relationships": [],
                "original_findings": findings,
                "duplicate_findings": [],
                "error": str(e)
            }
    
    def determine_finding_status(self, finding: FindingDB, original_to_duplicates: Dict[str, List[str]], 
                                duplicate_to_original: Dict[str, str], finding_map: Dict[str, FindingDB]) -> Status:
        """
        Determine the appropriate status for a finding based on deduplication results.
        
        Args:
            finding: The finding to determine status for
            original_to_duplicates: Maps original_id -> [duplicate_ids]
            duplicate_to_original: Maps duplicate_id -> original_id
            finding_map: Maps finding_id -> finding
            
        Returns:
            Appropriate Status enum value
        """
        is_duplicate = finding.str_id in duplicate_to_original
        is_original = finding.str_id in original_to_duplicates
        
        if not is_duplicate and not is_original:
            return Status.UNIQUE_VALID
        
        if is_duplicate and is_original:
            logger.error(f"Finding {finding.str_id} is both a duplicate and an original - treating as unique")
            return Status.UNIQUE_VALID
        
        if is_original:
            return Status.BEST_VALID
        
        if is_duplicate:
            original_id = duplicate_to_original[finding.str_id]
            findings_in_group = []
            
            if original_id in finding_map:
                findings_in_group.append(finding_map[original_id])
            
            duplicate_ids = original_to_duplicates.get(original_id, [])
            for dup_id in duplicate_ids:
                if dup_id != finding.str_id and dup_id in finding_map:
                    findings_in_group.append(finding_map[dup_id])
            
            same_agent_findings = [f for f in findings_in_group if f.agent_id == finding.agent_id]
            
            if same_agent_findings:
                if any(f.status == Status.BEST_VALID or f.status == Status.SIMILAR_VALID for f in same_agent_findings):
                    return Status.ALREADY_REPORTED
                else:
                    return Status.SIMILAR_VALID
            else:
                return Status.SIMILAR_VALID
        
        logger.error("Finding status determination fell through to fallback")
        return Status.UNIQUE_VALID
    
    async def apply_finding_statuses(self, task_id: str, findings: List[FindingDB], dedup_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply appropriate statuses to all findings based on deduplication results.
        
        Args:
            task_id: Task identifier
            findings: All findings being processed
            dedup_results: Results from deduplication analysis
            
        Returns:
            Summary of applied status changes
        """
        try:
            status_counts = {
                Status.BEST_VALID: 0,
                Status.UNIQUE_VALID: 0,
                Status.SIMILAR_VALID: 0,
                Status.ALREADY_REPORTED: 0,
                Status.PENDING: 0
            }
            
            updated_count = 0
            # Handle both dict and DuplicateFinding object formats
            duplicate_rels_raw = dedup_results.get("duplicate_relationships", [])
            if duplicate_rels_raw is None:
                duplicate_rels_raw = []
            
            # Convert to DuplicateFinding-like objects if needed
            from app.core.gemini_model import DuplicateFinding
            duplicate_rels = []
            for rel in duplicate_rels_raw:
                if isinstance(rel, dict):
                    duplicate_rels.append(DuplicateFinding(
                        findingId=rel.get("findingId", ""),
                        duplicateOf=rel.get("duplicateOf", ""),
                        explanation=rel.get("explanation", "Identified as duplicate")
                    ))
                else:
                    duplicate_rels.append(rel)
            
            original_to_duplicates = {}
            duplicate_to_original = {}
            finding_map = {f.str_id: f for f in findings}
            
            duplicate_explanations = {
                rel.findingId: rel.explanation 
                for rel in duplicate_rels
            }
            
            for rel in duplicate_rels:
                duplicate_to_original[rel.findingId] = rel.duplicateOf
                if rel.duplicateOf not in original_to_duplicates:
                    original_to_duplicates[rel.duplicateOf] = []
                original_to_duplicates[rel.duplicateOf].append(rel.findingId)
            
            for finding in findings:
                new_status = self.determine_finding_status(finding, original_to_duplicates, duplicate_to_original, finding_map)
                old_status = finding.status
                finding.status = new_status

                if new_status == Status.UNIQUE_VALID:
                    finding.deduplication_comment = "Unique finding with no duplicates identified"
                elif new_status == Status.BEST_VALID:
                    finding.deduplication_comment = "Selected as the best quality finding among duplicates"    
                elif new_status == Status.SIMILAR_VALID:
                    explanation = duplicate_explanations.get(finding.str_id, "Identified as duplicate by AI analysis")
                    original_id = duplicate_to_original.get(finding.str_id)
                    if original_id is None:
                        logger.error(f"No duplicate relationship found for finding {finding.str_id}")
                    else:
                        finding.duplicateOf = original_id
                        finding.deduplication_comment = f"Similar to finding '{original_id}': {explanation}"
                elif new_status == Status.ALREADY_REPORTED:
                    explanation = duplicate_explanations.get(finding.str_id, "Previously reported by same agent")
                    original_id = duplicate_to_original.get(finding.str_id)
                    if original_id is None:
                        logger.error(f"No duplicate relationship found for finding {finding.str_id}")
                    else:
                        finding.duplicateOf = original_id
                        finding.deduplication_comment = f"Already reported in finding '{original_id}': {explanation}"
                        
                success = await self.mongodb.update_finding(task_id, finding.str_id, finding)
                
                if success:
                    updated_count += 1
                    status_counts[new_status] += 1
                    logger.info(f"Updated '{finding.title}' status: {old_status} → {new_status}")
                else:
                    logger.warning(f"Failed to update status for '{finding.title}'")
            
            return {
                "total_processed": len(findings),
                "updated_count": updated_count,
                "status_distribution": {status.value: count for status, count in status_counts.items()},
                "duplicate_relationships_count": len(duplicate_rels)
            }
            
        except Exception as e:
            logger.error(f"Error applying finding statuses: {str(e)}")
            return {
                "total_processed": len(findings),
                "updated_count": 0,
                "status_distribution": {},
                "duplicate_relationships_count": 0,
                "error": str(e)
            }
    
    async def process_findings(self, task_id: str, findings: List[FindingDB], task_cache: TaskCache = None, contract_contents: Dict[str, ContractFile] = None) -> Dict[str, Any]:
        """
        Main entry point for processing findings through deduplication with comprehensive status management.
        """
        logger.info(f"Processing {len(findings)} findings for task {task_id}")
        
        # Step 1: Deduplicate findings using hierarchical batch processing
        dedup_results = await self.deduplicate_findings(findings, task_cache, contract_contents)
        
        # Step 2: Apply comprehensive status management (existing logic)
        status_results = await self.apply_finding_statuses(task_id, findings, dedup_results)
        
        # Combine results
        combined_results = {
            "deduplication": {
                "total_analyzed": dedup_results["total"],
                "duplicate_relationships": dedup_results["duplicate_relationships"],
                "duplicates_found": len(dedup_results["duplicate_relationships"]),
                "original_findings": dedup_results["original_findings"]
            },
            "status_application": status_results,
            "summary": {
                "total_processed": status_results["total_processed"],
                "status_distribution": status_results["status_distribution"],
                "duplicate_relationships": status_results["duplicate_relationships_count"],
                "successfully_updated": status_results["updated_count"],
                "originals_found": len(dedup_results["original_findings"]),
                "duplicates_found": len(dedup_results["duplicate_relationships"])
            }
        }
        
        logger.info(f"Processing completed for task {task_id}: {combined_results['summary']}")
        return combined_results

