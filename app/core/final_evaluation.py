"""
Final evaluation module for security findings.
Evaluates pending findings to determine validity, category, and severity.
Uses LLM to analyze and categorize security issues.
"""
import os
import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from langchain_anthropic import ChatAnthropic
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

from app.database.mongodb_handler import mongodb
from app.models.finding_db import FindingDB, Status, EvaluatedSeverity
from app.core.cross_agent_comparison import CrossAgentComparison
from app.core.claude_model import create_claude_model

class FindingEvaluator:
    """
    Handles final evaluation of security findings.
    Analyzes findings content to determine validity, categorize, and assess severity.
    """
    
    def __init__(self, mongodb_client=None):
        """
        Initialize the finding evaluator.
        
        Args:
            mongodb_client: MongoDB client instance (uses global instance if None)
        """
        self.mongodb = mongodb_client or mongodb  # Use global instance if none provided
        self.cross_comparison = CrossAgentComparison(mongodb_client)
        self.evaluation_chain = self._setup_evaluation_chain()
    
    def _setup_evaluation_chain(self) -> LLMChain:
        """
        Setup LangChain components for finding evaluation.
        
        Returns:
            LLMChain configured to evaluate findings
        """
        # Initialize Claude model using centralized configuration
        model = create_claude_model()
        
        # Create evaluation prompt template focused on smart contract vulnerabilities
        evaluation_template = """
        You are a blockchain security expert tasked with evaluating the validity and severity of smart contract vulnerabilities.
        Please analyze the following security finding and determine:

        1. Is it a valid smart contract security issue? Evaluate the technical accuracy and impact.
        2. What security category does it belong to? Use standard categories for smart contracts (e.g., Reentrancy, Integer Overflow/Underflow, Access Control, Logic Error, etc.).
        3. What is the appropriate severity level (low, medium, high, critical)?
        4. Provide a brief explanation of your evaluation.

        Finding details:
        Title: {title}
        Description: {description}
        Reported Severity: {severity}

        Analyze the provided information thoroughly. Consider:
        - Technical accuracy and feasibility in blockchain context
        - Potential impact on contract funds, operations, or users
        - Exploitation difficulty and prerequisites
        
        Provide your evaluation in this exact format:
        IS_VALID: yes/no
        CATEGORY: category_name
        SEVERITY: severity_level
        COMMENT: Your explanation (2-3 sentences maximum)
        """
        
        prompt = PromptTemplate(
            input_variables=["title", "description", "severity"],
            template=evaluation_template
        )
        
        # Create and return chain
        return LLMChain(llm=model, prompt=prompt, output_key="evaluation")
    
    def _parse_evaluation_result(self, evaluation_text: str) -> Dict[str, Any]:
        """
        Parse the evaluation response from the LLM.
        
        Args:
            evaluation_text: Raw text response from LLM
            
        Returns:
            Dictionary with parsed evaluation data
        """
        # Initialize with None values instead of defaults
        result = {
            "is_valid": None,
            "category": None,
            "evaluated_severity": None,
            "evaluation_comment": None
        }
        
        # Parse the response line by line
        lines = evaluation_text.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('IS_VALID:'):
                value = line[len('IS_VALID:'):].strip().lower()
                result["is_valid"] = value == 'yes'
            elif line.startswith('CATEGORY:'):
                result["category"] = line[len('CATEGORY:'):].strip()
            elif line.startswith('SEVERITY:'):
                severity_text = line[len('SEVERITY:'):].strip().lower()
                # Map text severity to enum with consistent handling
                if severity_text in ["low", "trivial"]:
                    result["evaluated_severity"] = EvaluatedSeverity.LOW
                elif severity_text == "medium":
                    result["evaluated_severity"] = EvaluatedSeverity.MEDIUM
                elif severity_text in ["high", "critical"]:
                    result["evaluated_severity"] = EvaluatedSeverity.HIGH
                else:
                    # Unknown severity, leave as None
                    pass
            elif line.startswith('COMMENT:'):
                result["evaluation_comment"] = line[len('COMMENT:'):].strip()
        
        # Set defaults only if parsing didn't produce values
        if result["is_valid"] is None:
            result["is_valid"] = False  # Default to invalid if not specified
            
        if result["evaluation_comment"] is None:
            result["evaluation_comment"] = "No comment provided."
            
        # Note: category and evaluated_severity remain None if not found
        # They will only be set for valid findings later in the process
        
        return result
    
    async def evaluate_finding(self, finding: FindingDB) -> Dict[str, Any]:
        """
        Evaluate a single finding using LLM for analysis.
        
        Args:
            finding: The finding to evaluate
            
        Returns:
            Evaluation results
        """
        # Prepare input for evaluation
        eval_input = {
            "title": finding.title,
            "description": finding.description,
            "severity": finding.severity,
            "file_paths": finding.file_paths,
        }
        
        # Run evaluation chain
        response_dict = await self.evaluation_chain.ainvoke(eval_input)
        response = response_dict["evaluation"]  # Extract the response string from output_key
        
        # Parse results
        evaluation_result = self._parse_evaluation_result(response)
        
        return evaluation_result
    
    async def apply_evaluation(self, task_id: str, title: str, 
                             evaluation_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply evaluation results to a finding by updating its status, category, and severity.
        
        Args:
            task_id: Task identifier
            title: Finding title
            evaluation_result: Evaluation results from LLM
            
        Returns:
            Updated finding data
        """
        if not evaluation_result["is_valid"]:
            status = Status.DISPUTED
            evaluated_severity = None
            category = None
        else:
            status = Status.UNIQUE_VALID
            evaluated_severity = evaluation_result["evaluated_severity"]
            category = evaluation_result["category"]
            
            
            if evaluated_severity is None:
                evaluated_severity = EvaluatedSeverity.MEDIUM  
                
            if category is None or category.strip() == "":
                category = "Uncategorized"  
        
        # Use cross comparison method to update finding with proper category_id handling
        update_result = await self.cross_comparison.perform_final_evaluation(
            task_id,
            title,
            status,
            category,
            evaluated_severity,
            evaluation_result["evaluation_comment"]
        )
        
        return update_result
    
    async def get_pending_findings(self, task_id: str) -> List[FindingDB]:
        """
        Get all findings with pending status from a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            List of pending findings
        """
        all_findings = await self.mongodb.get_task_findings(task_id)
        return [f for f in all_findings if f.status == Status.PENDING]
    
    async def evaluate_all_pending(self, task_id: str) -> Dict[str, Any]:
        """
        Evaluate all pending findings in a task.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Summary of evaluation results
        """
        # Get all pending findings
        pending_findings = await self.get_pending_findings(task_id)
        
        results = {
            "total_pending": len(pending_findings),
            "evaluated_as_valid": 0,
            "evaluated_as_disputed": 0,
            "evaluations": []
        }
        
        # Process each pending finding
        for finding in pending_findings:
            # Evaluate the finding
            evaluation_result = await self.evaluate_finding(finding)
            
            # Apply the evaluation
            update_result = await self.apply_evaluation(
                task_id,
                finding.title,
                evaluation_result
            )
            
            # Update statistics
            if evaluation_result["is_valid"]:
                results["evaluated_as_valid"] += 1
            else:
                results["evaluated_as_disputed"] += 1
            
            # Add to results with correct severity handling
            evaluation_entry = {
                "title": finding.title,
                "status": Status.UNIQUE_VALID if evaluation_result["is_valid"] else Status.DISPUTED,
                "evaluation_comment": evaluation_result["evaluation_comment"]
            }
            
            # Only include category and severity for valid findings
            if evaluation_result["is_valid"]:
                evaluation_entry["evaluated_severity"] = evaluation_result["evaluated_severity"]
                evaluation_entry["category"] = evaluation_result["category"] or "Uncategorized"
            else:
                evaluation_entry["evaluated_severity"] = None
                evaluation_entry["category"] = None
                
            results["evaluations"].append(evaluation_entry)
        
        return results
    
    async def count_issues_by_category(self, task_id: str) -> Dict[str, Any]:
        """
        Count unique security issues by category in a task.
        Groups similar findings together based on category_id.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Dictionary of categories with count and finding IDs
        """
        all_findings = await self.mongodb.get_task_findings(task_id)
        
        # Filter for valid findings
        valid_findings = [f for f in all_findings 
                        if f.status in [Status.UNIQUE_VALID, Status.SIMILAR_VALID]]
        
        # Group by category_id
        issues_by_category = {}
        for finding in valid_findings:
            category_id = getattr(finding, 'category_id', None)
            category_name = getattr(finding, 'category', "Uncategorized")
            
            # Skip findings without category_id
            if not category_id:
                continue
            
            # Get severity, default to MEDIUM if None
            severity = getattr(finding, 'evaluated_severity', None) or EvaluatedSeverity.MEDIUM
                
            if category_id not in issues_by_category:
                issues_by_category[category_id] = {
                    "category": category_name,
                    "count": 0,
                    "severity": severity,
                    "findings": []
                }
            
            issues_by_category[category_id]["count"] += 1
            issues_by_category[category_id]["findings"].append(finding.title)
        
        return issues_by_category
    
    async def generate_summary_report(self, task_id: str) -> Dict[str, Any]:
        """
        Generate a summary report of all security findings in a task.
        Includes counts by status, severity, and category.
        
        Args:
            task_id: Task identifier
            
        Returns:
            Summary report data
        """
        all_findings = await self.mongodb.get_task_findings(task_id)
        
        # Count by status
        status_counts = {}
        for finding in all_findings:
            status = finding.status
            if status not in status_counts:
                status_counts[status] = 0
            status_counts[status] += 1
        
        # Get category counts
        category_data = await self.count_issues_by_category(task_id)
        
        # Generate summary
        summary = {
            "task_id": task_id,
            "total_findings": len(all_findings),
            "status_distribution": status_counts,
            "categories": category_data,
            "generated_at": datetime.utcnow().isoformat()
        }
        
        return summary 