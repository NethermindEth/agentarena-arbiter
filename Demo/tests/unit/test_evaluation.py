"""
Unit tests for evaluation logic.
"""
import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import mock_mongodb
from app.core.claude_model import FindingEvaluation
from app.models.finding_db import Severity
from app.core.gemini_model import DuplicateFinding
from app.core.evaluation import FindingEvaluator
from app.models.finding_db import Status


class TestFindingEvaluator:
    """Test FindingEvaluator class."""
    
    @pytest.fixture
    def evaluator(self):
        """Create FindingEvaluator instance.""" 
        return FindingEvaluator(batch_size=5, mongodb_client=mock_mongodb)
    
    def test_initialization(self, evaluator):
        """Test evaluator initializes with correct batch size."""
        assert evaluator.batch_size == 5
    
    def test_group_findings_for_evaluation_no_duplicates(self, evaluator, sample_findings):
        """Test grouping findings when there are no duplicates."""
        duplicate_relationships = []
        
        related_groups, individual_groups = evaluator.group_findings_for_evaluation(sample_findings, duplicate_relationships)
        
        # With no duplicates, all findings should have been batched together in an individual group
        assert len(individual_groups) == 1
        assert len(individual_groups[0]) == len(sample_findings)
    
    def test_group_findings_for_evaluation_with_duplicates(self, evaluator, sample_findings):
        """Test grouping findings when there are duplicates."""
        # Mock duplicate relationships (findings 0 and 1 are duplicates)
        duplicate_relationships = [
            DuplicateFinding(
                findingId=sample_findings[1].str_id,
                duplicateOf=sample_findings[0].str_id,
                explanation="Both are reentrancy issues"
            )
        ]
        
        related_groups, individual_groups = evaluator.group_findings_for_evaluation(sample_findings, duplicate_relationships)

        # Should have at least one group with multiple findings (the duplicates)
        has_group_with_multiple = any(len(group) > 1 for group in related_groups)
        assert has_group_with_multiple

        # Related groups should contain the first two findings
        assert len(related_groups) == 1
        assert len(related_groups[0]) == 2
        assert related_groups[0][0] == sample_findings[0]
        assert related_groups[0][1] == sample_findings[1]

        # Individual groups should contain the third finding alone
        assert len(individual_groups) == 1
        assert len(individual_groups[0]) == 1
        assert individual_groups[0][0] == sample_findings[2]
    
    @pytest.mark.asyncio
    async def test_evaluate_all_findings_empty_list(self, evaluator, sample_task_cache):
        """Test evaluating empty findings list."""
        result = await evaluator.evaluate_all_findings(
            "test-task",
            [],
            [],
            sample_task_cache
        )
        
        assert result is not None
        assert "application_results" in result
        assert result["application_results"]["valid_count"] == 0
        assert result["application_results"]["disputed_count"] == 0
    
    @pytest.mark.asyncio
    async def test_evaluate_all_findings_success(self, evaluator, sample_findings, sample_task_cache):
        """Test successful evaluation of findings."""
        with patch.object(evaluator, 'evaluate_findings_batch') as mock_evaluate_batch:
            # Mock batch evaluation returning valid results
            mock_evaluate_batch.return_value = [
                FindingEvaluation(
                    finding_id=sample_findings[0].str_id,
                    is_valid=True,
                    severity=Severity.HIGH,
                    comment="Valid security issue"
                )
            ]
            mock_mongodb.update_finding = AsyncMock()
            
            result = await evaluator.evaluate_all_findings(
                "test-task",
                sample_findings[:1],  # Just test with one finding
                [],
                sample_task_cache
            )
            
            assert result["application_results"]["valid_count"] == 1
            assert result["application_results"]["disputed_count"] == 0
            
            # Verify database update was called
            mock_mongodb.update_finding.assert_called()
