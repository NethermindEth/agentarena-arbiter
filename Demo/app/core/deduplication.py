"""
Finding deduplication module for security findings submissions.
Uses Gemini 2.5 Pro to identify duplicates across all findings in a single prompt.
"""
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from app.core.gemini_model import create_structured_deduplication_model, find_duplicates_structured, DuplicateFinding
from app.database.mongodb_handler import mongodb
from app.models.finding_db import FindingDB, Status
from app.config import config

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
            duplicate_results: List[DuplicateFinding] = find_duplicates_structured(
                self.deduplication_model, findings
            ).results
            
            # Validate that all IDs in the results are from the actual findings list
            valid_finding_ids = {f.id for f in findings}
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
            
            # Extract duplicate relationships with explanations
            duplicate_relationships = []
            for dup_finding in duplicate_results:
                duplicate_ids.add(dup_finding.findingId)
                original_ids.add(dup_finding.duplicateOf)
                
                # Convert to dictionary format for compatibility
                duplicate_relationships.append({
                    "findingId": dup_finding.findingId,
                    "duplicateOf": dup_finding.duplicateOf,
                    "explanation": dup_finding.explanation
                })
            
            logger.info(f"Findings: {findings}")
            logger.info(f"Duplicate IDs: {duplicate_ids}")
            logger.info(f"Original IDs: {original_ids}")

            # Separate findings into duplicates and originals
            duplicate_findings = [f for f in findings if f.id in duplicate_ids]
            original_findings = [f for f in findings if f.id not in duplicate_ids]
            
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

    def determine_finding_status(self, finding: FindingDB, dedup_results: Dict[str, Any], all_findings: List[FindingDB]) -> Status:
        """
        Determine the appropriate status for a finding based on deduplication results.
        
        Args:
            finding: The finding to determine status for
            dedup_results: Results from deduplication analysis
            all_findings: All findings being processed
            
        Returns:
            Appropriate Status enum value
        """
        duplicate_relationships = dedup_results["duplicate_relationships"]
        
        # Check if this finding is a duplicate
        is_duplicate = any(rel["findingId"] == finding.id for rel in duplicate_relationships)
        
        # Check if this finding is an original (referenced by duplicates)
        is_best = any(rel["duplicateOf"] == finding.id for rel in duplicate_relationships)
        
        if not is_duplicate and not is_best:
            # No duplicates found for this finding
            return Status.UNIQUE_VALID
        elif is_best:
            # This finding is the best/original version of a group with duplicates
            return Status.BEST_VALID
        elif is_duplicate:
            # This finding is a duplicate of another finding
            
            # Get the original/best finding ID for this duplicate
            best_id = next((rel["duplicateOf"] for rel in duplicate_relationships if rel["findingId"] == finding.id), None)
            if best_id is None:
                logger.error(f"No duplicate relationship found for finding {finding.id}")
                return Status.PENDING
            
            # Find all findings in this duplicate group (all duplicates that point to the same best_id + the best_id itself)
            findings_in_group = []
            
            # Add the best/original finding
            best_finding = next((f for f in all_findings if f.id == best_id), None)
            if best_finding:
                findings_in_group.append(best_finding)
            
            # Add all other duplicates that point to the same best_id
            for rel in duplicate_relationships:
                if rel["duplicateOf"] == best_id and rel["findingId"] != finding.id:
                    duplicate_finding = next((f for f in all_findings if f.id == rel["findingId"]), None)
                    if duplicate_finding:
                        findings_in_group.append(duplicate_finding)
            
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
            duplicate_relationships = dedup_results["duplicate_relationships"]
            
            # Create lookup for duplicate explanations
            duplicate_explanations = {
                rel["findingId"]: rel["explanation"] 
                for rel in duplicate_relationships
            }
            
            for finding in findings:
                # Determine the appropriate status
                new_status = self.determine_finding_status(finding, dedup_results, findings)
                
                # Update finding with new status
                old_status = finding.status
                finding.status = new_status
                finding.updated_at = datetime.now(timezone.utc)

                # Set appropriate deduplication comment
                if new_status == Status.UNIQUE_VALID:
                    finding.deduplication_comment = "Unique finding with no duplicates identified"
                elif new_status == Status.BEST_VALID:
                    finding.deduplication_comment = "Selected as the best quality finding among duplicates"    
                elif new_status == Status.SIMILAR_VALID:
                    explanation = duplicate_explanations.get(finding.id, "Identified as duplicate by AI analysis")
                    original_id = next((rel["duplicateOf"] for rel in duplicate_relationships if rel["findingId"] == finding.id), None)
                    if original_id is None:
                        logger.error(f"No duplicate relationship found for finding {finding.id}")
                    else:
                        finding.deduplication_comment = f"Similar to finding '{original_id}': {explanation}"
                elif new_status == Status.ALREADY_REPORTED:
                    explanation = duplicate_explanations.get(finding.id, "Previously reported by same agent")
                    original_id = next((rel["duplicateOf"] for rel in duplicate_relationships if rel["findingId"] == finding.id), None)
                    if original_id is None:
                        logger.error(f"No duplicate relationship found for finding {finding.id}")
                    else:
                        finding.deduplication_comment = f"Already reported by same agent (original: '{original_id}'): {explanation}"
                        
                # Save to database
                success = await self.mongodb.update_finding(task_id, finding.id, finding.model_dump())
                
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
                "duplicate_relationships_count": len(duplicate_relationships)
            }
            
        except Exception as e:
            logger.error(f"Error applying finding statuses: {str(e)}")
            return {
                "total_processed": len(findings),
                "updated_count": 0,
                "status_distribution": {},
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