"""
Finding deduplication module for security findings submissions.
Uses LangChain and Claude to compare findings and identify duplicates within the same agent's submissions.
"""
import os
import re
from typing import List, Dict, Any, Optional, Tuple
import asyncio
from dotenv import load_dotenv

from app.core.claude_model import create_similarity_chain
from app.database.mongodb_handler import mongodb
from app.models.finding_input import FindingInput
from app.models.finding_db import FindingDB, Status, EvaluatedSeverity

# Load environment variables
load_dotenv()

# Default similarity threshold
DEFAULT_SIMILARITY_THRESHOLD = 0.8

class FindingDeduplication:
    """
    Handles deduplication of findings submitted by the same agent.
    Identifies findings that have already been reported by the agent.
    Compares title, description, recommendation and code_references using LangChain and Claude.
    """
    
    def __init__(self, mongodb_client=None, similarity_threshold=None):
        """
        Initialize the finding deduplication handler.
        
        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
            similarity_threshold: Threshold for considering two findings as duplicates (0.0-1.0)
                                 If None, reads from SIMILARITY_THRESHOLD env var or uses default
        """
        self.mongodb = mongodb_client or mongodb  # Use global instance if none provided
        
        # Get similarity threshold from param, env var, or default
        if similarity_threshold is None:
            try:
                similarity_threshold = float(os.getenv("SIMILARITY_THRESHOLD", DEFAULT_SIMILARITY_THRESHOLD))
            except (ValueError, TypeError):
                similarity_threshold = DEFAULT_SIMILARITY_THRESHOLD
        
        # Validate similarity threshold
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0.0 and 1.0")
        self.similarity_threshold = similarity_threshold
        
        # Initialize similarity chain
        self.similarity_chain = create_similarity_chain()
    
    def get_finding_content(self, finding: FindingInput) -> str:
        """
        Extract content to be used for similarity comparison from a finding.
        
        Args:
            finding: The finding to extract content from
            
        Returns:
            String containing the relevant fields for comparison
        """
        content = [
            f"Title: {finding.title}",
            f"Description: {finding.description}",
            f"Recommendation: {finding.recommendation}",
            f"Code References: {', '.join(finding.code_references)}"
        ]
        return "\n".join(content)
    
    def _parse_similarity_score(self, response: str) -> float:
        """
        Parse the similarity score from the LLM response.
        
        Args:
            response: Text response from LLM
            
        Returns:
            Similarity score as a float between 0 and 1
        """
        # Look for a floating point number in the response
        match = re.search(r'(\d+\.\d+|\d+)', response)
        if match:
            score = float(match.group(0))
            # Ensure score is between 0 and 1
            return min(max(score, 0.0), 1.0)
        else:
            # Default to 0 if no number found
            return 0.0
            
    def _extract_explanation(self, response: str) -> str:
        """
        Extract the explanation part from the LLM response, before the numerical score.
        
        Args:
            response: Text response from LLM
            
        Returns:
            Explanation text
        """
        # Simple approach: split on the first number
        match = re.search(r'(\d+\.\d+|\d+)', response)
        if match:
            explanation = response[:match.start()].strip()
            return explanation
        else:
            return response.strip()
    
    async def compare_findings_with_langchain(self, finding1: FindingInput, finding2: FindingInput) -> Tuple[float, str]:
        """
        Compare two findings using LangChain and Claude to determine similarity.
        
        Args:
            finding1: First finding to compare
            finding2: Second finding to compare
            
        Returns:
            Tuple of (similarity score between 0 and 1, explanation text)
        """
        try:
            # Extract content
            content1 = self.get_finding_content(finding1)
            content2 = self.get_finding_content(finding2)
            
            # Run similarity chain
            response = await self.similarity_chain.arun(finding1=content1, finding2=content2)
            
            # Parse similarity score from response
            similarity_score = self._parse_similarity_score(response)
            
            # Extract explanation
            explanation = self._extract_explanation(response)
            
            return similarity_score, explanation
        except Exception as e:
            # Log the error and return low similarity score
            print(f"Error comparing findings: {str(e)}")
            return 0.0, f"Error during comparison: {str(e)}"
    
    async def _get_agent_findings(self, project_id: str, agent_id: str, exclude_reported: bool = True) -> List[FindingDB]:
        """
        Get all findings submitted by a specific agent for a project.
        
        Args:
            project_id: Project identifier
            agent_id: Agent identifier
            exclude_reported: If True, exclude findings already marked as 'already_reported'
            
        Returns:
            List of findings from the specified agent
        """
        try:
            # Get all findings for the project
            all_findings = await self.mongodb.get_project_findings(project_id)
            
            # Filter for the specific agent
            agent_findings = [finding for finding in all_findings if finding.reported_by_agent == agent_id]
            
            # Optionally exclude findings already marked as duplicates
            if exclude_reported:
                return [finding for finding in agent_findings if finding.status != Status.ALREADY_REPORTED]
            
            return agent_findings
        except Exception as e:
            print(f"Error getting agent findings: {str(e)}")
            return []
    
    async def process_findings(self, project_id: str, agent_id: str, new_findings: List[FindingInput]) -> Dict[str, Any]:
        """
        Process a batch of new findings, detect duplicates and mark them as already reported.
        Only compares with non-duplicate findings from the same agent.
        
        Args:
            project_id: Project identifier
            agent_id: Agent identifier
            new_findings: List of new findings to process
            
        Returns:
            Statistics about processed findings
        """
        # Filter out findings that are not from the specified agent
        filtered_findings = [f for f in new_findings if f.reported_by_agent == agent_id]
        
        if not filtered_findings:
            return {
                "total": 0,
                "duplicates": 0,
                "new": 0,
                "duplicate_ids": [],
                "new_ids": []
            }
        
        try:
            # Get existing findings from same agent, excluding already reported ones
            existing_findings = await self._get_agent_findings(project_id, agent_id, exclude_reported=True)
            
            # Initialize known findings list with existing findings
            known_findings = list(existing_findings)
            
            results = {
                "total": len(filtered_findings),
                "duplicates": 0,
                "new": 0,
                "duplicate_ids": [],
                "new_ids": []
            }
            
            # List to collect all processed findings for batch processing
            all_processed_findings = []
            
            # Process each finding to determine if it's a duplicate or new
            for finding in filtered_findings:
                is_duplicate = False
                similarity_explanation = ""
                similar_to = None
                
                # Compare with all known findings (non-duplicate ones from the same agent)
                for known_finding in known_findings:
                    similarity_score, explanation = await self.compare_findings_with_langchain(finding, known_finding)
                    
                    if similarity_score >= self.similarity_threshold:
                        is_duplicate = True
                        similarity_explanation = explanation
                        similar_to = known_finding.finding_id
                        break
                
                if is_duplicate:
                    # Prepare finding with already_reported status
                    finding_dict = finding.model_dump()
                    finding_dict["status"] = Status.ALREADY_REPORTED
                    finding_dict["evaluation_comment"] = f"Similar to finding {similar_to}. {similarity_explanation}"
                    duplicate_finding = FindingDB(**finding_dict)
                    
                    # Add to the consolidated findings list
                    all_processed_findings.append(duplicate_finding)
                    
                    results["duplicates"] += 1
                    results["duplicate_ids"].append(finding.finding_id)
                else:
                    # Prepare as new finding
                    new_finding = FindingDB(**finding.model_dump())
                    
                    # Add to the consolidated findings list
                    all_processed_findings.append(new_finding)
                    
                    # Add to known findings for subsequent comparisons
                    known_findings.append(new_finding)
                    
                    results["new"] += 1
                    results["new_ids"].append(finding.finding_id)
            
            # Batch create all findings together to ensure they share the same submission_id
            if all_processed_findings:
                await self.mongodb.create_finding_batch(all_processed_findings)
            
            return results
        
        except Exception as e:
            print(f"Error processing findings: {str(e)}")
            return {
                "total": len(filtered_findings),
                "duplicates": 0,
                "new": 0,
                "duplicate_ids": [],
                "new_ids": [],
                "error": str(e)
            } 