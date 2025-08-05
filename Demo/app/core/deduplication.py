"""
Finding deduplication module for security findings submissions.
Uses Gemini 2.5 Pro to identify duplicates across all findings in a single prompt.
"""
import logging
from typing import List, Dict, Any

from app.core.gemini_model import create_structured_deduplication_model, find_duplicates_structured, DuplicateFinding, DeduplicationResult
from app.database.mongodb_handler import mongodb
from app.models.finding_db import FindingDB, Status

logger = logging.getLogger(__name__)

class FindingDeduplication:
    """
    Handles deduplication of findings using Gemini 2.5 Pro.
    Processes all findings in a single prompt to identify duplicates.
    """
    
    def __init__(self, mongodb_client=None):
        """
        Initialize the finding deduplication handler.
        
        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
        """
        self.mongodb = mongodb_client or mongodb  # Use global instance if none provided
        
        # Initialize structured deduplication model
        self.deduplication_model = create_structured_deduplication_model()
    
    async def deduplicate_findings(self, findings: List[FindingDB]) -> Dict[str, Any]:
        """
        Deduplicate findings using Gemini 2.5 Pro with structured output.
        
        Args:
            findings: List of findings to deduplicate
            
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
            
            # Use structured output for guaranteed JSON format
            dedup_result: DeduplicationResult = await find_duplicates_structured(
                self.deduplication_model, findings
            )
            duplicate_results: List[DuplicateFinding] = dedup_result.results
            
            # Validate that all IDs in the results are from the actual findings list
            valid_finding_ids = {f.str_id for f in findings}
            validated_duplicate_results = []
            
            for dup_finding in duplicate_results:
                error = False
                
                # Validate findingId exists in our findings
                if dup_finding.findingId not in valid_finding_ids:
                    logger.warning(f"Invalid findingId '{dup_finding.findingId}' not found in findings list")
                    error = True
                
                # Validate duplicateOf exists in our findings
                if dup_finding.duplicateOf not in valid_finding_ids:
                    logger.warning(f"Invalid duplicateOf '{dup_finding.duplicateOf}' not found in findings list")
                    error = True
                
                # Only include results with valid IDs
                if not error:
                    validated_duplicate_results.append(dup_finding)
                else:
                    logger.warning(f"Filtered out invalid duplicate result: {dup_finding.findingId} -> {dup_finding.duplicateOf}")
            
            logger.info(f"Validation completed: {len(validated_duplicate_results)}/{len(duplicate_results)} duplicate relationships validated")
            
            # Use validated results for further processing
            duplicate_results = validated_duplicate_results
            
            # Process the structured results
            duplicate_ids = set()
            original_ids = set()
            
            for dup_finding in duplicate_results:
                duplicate_ids.add(dup_finding.findingId)
                original_ids.add(dup_finding.duplicateOf)
            
            logger.info(f"Findings: {findings}")
            logger.info(f"Duplicate IDs: {duplicate_ids}")
            logger.info(f"Original IDs: {original_ids}")

            # Separate findings into duplicates and originals
            duplicate_findings = [f for f in findings if f.str_id in duplicate_ids]
            original_findings = [f for f in findings if f.str_id in original_ids]
            
            results = {
                "total": len(findings),
                "duplicates": len(duplicate_findings),
                "originals": len(original_findings),
                "duplicate_relationships": duplicate_results,
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
            # No duplicates found for this finding
            return Status.UNIQUE_VALID
        
        if is_duplicate and is_original:
            logger.error(f"Finding {finding.str_id} is both a duplicate and an original")
            return Status.PENDING
        
        if is_original:
            # This finding is the best/original version of a group with duplicates
            return Status.BEST_VALID
        
        if is_duplicate:
            # This finding is a duplicate of another finding
            original_id = duplicate_to_original[finding.str_id]
            
            # Get all findings in this duplicate group (original + all its duplicates)
            findings_in_group = []
            
            # Add the original finding
            if original_id in finding_map:
                findings_in_group.append(finding_map[original_id])
            
            # Add all duplicates in this group
            duplicate_ids = original_to_duplicates.get(original_id, [])
            for dup_id in duplicate_ids:
                if dup_id != finding.str_id and dup_id in finding_map:
                    findings_in_group.append(finding_map[dup_id])
            
            # Check if any finding in the group is from the same agent as current finding
            same_agent_findings = [f for f in findings_in_group if f.agent_id == finding.agent_id]
            
            if same_agent_findings:
                if any(f.status == Status.SIMILAR_VALID for f in same_agent_findings):
                    return Status.ALREADY_REPORTED
                else:
                    return Status.SIMILAR_VALID
            else:
                # Different agents reported all other findings in the group
                return Status.SIMILAR_VALID
        
        # Fallback (shouldn't reach here)
        return Status.PENDING

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
            duplicate_rels: List[DuplicateFinding] = dedup_results["duplicate_relationships"]
            
            # Create efficient mappings once for all findings processing
            original_to_duplicates = {}  # Maps original_id -> [duplicate_ids]
            duplicate_to_original = {}   # Maps duplicate_id -> original_id
            finding_map = {f.str_id: f for f in findings}  # Maps finding_id -> finding
            
            # Create lookup for duplicate explanations
            duplicate_explanations = {
                rel.findingId: rel.explanation 
                for rel in duplicate_rels
            }
            
            # Build deduplication mappings once
            for rel in duplicate_rels:
                # Build duplicate_to_original mapping
                duplicate_to_original[rel.findingId] = rel.duplicateOf
                
                # Build original_to_duplicates mapping
                if rel.duplicateOf not in original_to_duplicates:
                    original_to_duplicates[rel.duplicateOf] = []
                original_to_duplicates[rel.duplicateOf].append(rel.findingId)
            
            for finding in findings:
                # Determine the appropriate status using pre-created mappings
                new_status = self.determine_finding_status(finding, original_to_duplicates, duplicate_to_original, finding_map)
                
                # Update finding with new status
                old_status = finding.status
                finding.status = new_status

                # Set appropriate deduplication comment
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
                        finding.deduplication_comment = f"Similar to finding '{original_id}': {explanation}"
                elif new_status == Status.ALREADY_REPORTED:
                    explanation = duplicate_explanations.get(finding.str_id, "Previously reported by same agent")
                    original_id = duplicate_to_original.get(finding.str_id)
                    if original_id is None:
                        logger.error(f"No duplicate relationship found for finding {finding.str_id}")
                    else:
                        finding.deduplication_comment = f"Already reported by same agent (original: '{original_id}'): {explanation}"
                        
                # Save to database
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
    
    async def process_findings(self, task_id: str, findings: List[FindingDB]) -> Dict[str, Any]:
        """
        Main entry point for processing findings through deduplication with comprehensive status management.
        
        Args:
            task_id: Task identifier
            findings: List of findings to process
            
        Returns:
            Complete processing results with detailed status information
        """
        logger.info(f"Processing {len(findings)} findings for task {task_id}")
        
        # Step 1: Deduplicate findings
        dedup_results = await self.deduplicate_findings(findings)
        
        # Step 2: Apply comprehensive status management
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