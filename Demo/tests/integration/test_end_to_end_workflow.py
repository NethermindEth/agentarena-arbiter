"""
Integration tests for end-to-end workflow processing.
These tests verify the complete processing flow from findings input to structured output.
"""
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from bson import ObjectId

from app.core.evaluation import FindingEvaluator, FindingEvaluation
from app.core.deduplication import FindingDeduplication
from app.core.gemini_model import DuplicateFinding, DeduplicationResult
from app.models.finding_db import FindingDB, Status, Severity


class TestCompleteWorkflow:
    """Test the complete end-to-end workflow from findings submission to final results."""
    
    @pytest.fixture
    def workflow_findings(self):
        """Create findings for workflow testing that include potential duplicates."""
        base_time = datetime.now(timezone.utc)
        
        return [
            FindingDB(
                _id=ObjectId("507f1f77bcf86cd799439011"),
                title="Reentrancy vulnerability in withdraw function",
                description="The withdraw function makes external calls before updating user balances, allowing for reentrancy attacks.",
                severity=Severity.HIGH,
                file_paths=["contracts/Vault.sol"],
                agent_id="agent_alpha",
                status=Status.PENDING,
                created_at=base_time,
                updated_at=base_time
            ),
            FindingDB(
                _id=ObjectId("507f1f77bcf86cd799439012"),
                title="External call before state update in withdrawal",
                description="The withdrawal method calls external contracts before reducing the user's balance, creating reentrancy risk.",
                severity=Severity.HIGH,
                file_paths=["contracts/Vault.sol"],
                agent_id="agent_beta",
                status=Status.PENDING,
                created_at=base_time,
                updated_at=base_time
            ),
            FindingDB(
                _id=ObjectId("507f1f77bcf86cd799439013"),
                title="Missing access control in admin function",
                description="The admin function lacks proper authorization checks allowing unauthorized access.",
                severity=Severity.MEDIUM,
                file_paths=["contracts/Access.sol"],
                agent_id="agent_gamma",
                status=Status.PENDING,
                created_at=base_time,
                updated_at=base_time
            )
        ]
    
    @pytest.mark.asyncio
    async def test_complete_deduplication_and_evaluation_workflow(self, workflow_findings, sample_task_cache, mock_mongodb):
        """Test the complete workflow from findings input through deduplication to evaluation."""
        
        # Step 1: Mock deduplication results (finding 2 is duplicate of finding 1)
        expected_duplicates = DeduplicationResult(
            results=[
                DuplicateFinding(
                    findingId=workflow_findings[1].str_id,
                    duplicateOf=workflow_findings[0].str_id,
                    explanation="Both describe the same reentrancy vulnerability in the withdraw function"
                )
            ]
        )
        
        # Step 2: Mock evaluation results
        # evaluate_groups returns one evaluation per member finding (a group's verdict is
        # applied to every member).
        expected_evaluations = [
            FindingEvaluation(
                finding_id=workflow_findings[i].str_id,
                is_valid=True,
                severity=Severity.HIGH,
                comment="Valid reentrancy vulnerability with high impact potential"
            )
            for i in (0, 1)
        ] + [
            FindingEvaluation(
                finding_id=workflow_findings[i].str_id,
                is_valid=True,
                severity=Severity.MEDIUM,
                comment="Missing access control could allow unauthorized operations"
            )
            for i in (2,)
        ]
        
        # Step 3: Run deduplication
        with patch('app.core.deduplication.find_duplicates_structured') as mock_find_duplicates:
            mock_find_duplicates.return_value = expected_duplicates
            mock_mongodb.update_finding = AsyncMock()
            
            deduplicator = FindingDeduplication(mongodb_client=mock_mongodb)
            dedup_result = await deduplicator.process_findings(
                "test-task",
                workflow_findings,
                sample_task_cache
            )
            
            # Verify deduplication results
            assert dedup_result["summary"]["duplicates_found"] == 1
            assert len(dedup_result["deduplication"]["duplicate_relationships"]) == 1
            
            # Extract duplicate relationships for evaluation
            duplicate_relationships = dedup_result["deduplication"]["duplicate_relationships"]
        
        # Step 4: Run evaluation with grouping
        with patch.object(FindingEvaluator, 'evaluate_groups') as mock_evaluate_batch:
            # Mock batch evaluation to return expected results
            mock_evaluate_batch.return_value = expected_evaluations
            
            # evaluate_groups returns (evaluations, findings that could not be evaluated)
            mock_evaluate_batch.return_value = (mock_evaluate_batch.return_value, [])
            evaluator = FindingEvaluator(batch_size=10, mongodb_client=mock_mongodb)
            eval_result = await evaluator.evaluate_all_findings(
                "test-task",
                workflow_findings,
                duplicate_relationships,
                sample_task_cache
            )
            
            # Verify evaluation results
            assert eval_result["application_results"]["valid_count"] == 3
            assert eval_result["application_results"]["disputed_count"] == 0
            assert eval_result["application_results"]["failed_count"] == 0
            
            # One triage call per task, with the duplicate pair as one group and the rest alone
            assert mock_evaluate_batch.call_count == 1
            groups = mock_evaluate_batch.call_args[0][0]
            assert sorted(len(g) for g in groups) == [1, 2]

    @pytest.mark.asyncio
    async def test_workflow_with_all_duplicates(self, workflow_findings, sample_task_cache, mock_mongodb):
        """Test workflow when all findings are duplicates of the first one."""
        
        # Mock all findings as duplicates of the first
        duplicate_relationships = [
            DuplicateFinding(
                findingId=workflow_findings[i].str_id,
                duplicateOf=workflow_findings[0].str_id,
                explanation=f"Duplicate of first finding"
            ) for i in range(1, len(workflow_findings))
        ]
        
        with patch('app.core.deduplication.find_duplicates_structured') as mock_find_duplicates:
            mock_find_duplicates.return_value = DeduplicationResult(results=duplicate_relationships)
            mock_mongodb.update_finding = AsyncMock()
            
            # Run deduplication
            deduplicator = FindingDeduplication(mongodb_client=mock_mongodb)
            dedup_result = await deduplicator.process_findings(
                "test-task",
                workflow_findings,
                sample_task_cache
            )
            
            assert dedup_result["summary"]["duplicates_found"] == len(workflow_findings) - 1
            
            # Run evaluation - should only evaluate one group (all findings together)
            with patch.object(FindingEvaluator, 'evaluate_groups') as mock_evaluate_batch:
                mock_evaluate_batch.return_value = [
                    FindingEvaluation(
                        finding_id=workflow_findings[0].str_id,
                        is_valid=True,
                        severity=Severity.HIGH,
                        comment="Valid finding representing the group"
                    )
                ]
                
                # evaluate_groups returns (evaluations, findings that could not be evaluated)
                mock_evaluate_batch.return_value = (mock_evaluate_batch.return_value, [])
                evaluator = FindingEvaluator(batch_size=10, mongodb_client=mock_mongodb)
                
                # Get duplicate relationships from deduplication result
                duplicate_rels = dedup_result["deduplication"]["duplicate_relationships"]
                
                eval_result = await evaluator.evaluate_all_findings(
                    "test-task",
                    workflow_findings,
                    duplicate_rels,
                    sample_task_cache
                )
                
                # One triage call, with every finding in a single duplicate group
                assert mock_evaluate_batch.call_count == 1
                groups = mock_evaluate_batch.call_args[0][0]
                assert len(groups) == 1
                assert len(groups[0]) == len(workflow_findings)

    @pytest.mark.asyncio
    async def test_workflow_with_no_duplicates(self, workflow_findings, sample_task_cache, mock_mongodb):
        """Test workflow when no duplicates are found."""
        
        # Mock no duplicates found
        with patch('app.core.deduplication.find_duplicates_structured') as mock_find_duplicates:
            mock_find_duplicates.return_value = DeduplicationResult(results=[])
            mock_mongodb.update_finding = AsyncMock()
            
            # Run deduplication
            deduplicator = FindingDeduplication(mongodb_client=mock_mongodb)
            dedup_result = await deduplicator.process_findings(
                "test-task",
                workflow_findings,
                sample_task_cache
            )
            
            assert dedup_result["summary"]["duplicates_found"] == 0
            assert len(dedup_result["deduplication"]["duplicate_relationships"]) == 0
            
            # Run evaluation
            with patch.object(FindingEvaluator, 'evaluate_groups') as mock_evaluate_batch:
                # Mock all findings as valid
                mock_evaluations = [
                    FindingEvaluation(
                        finding_id=finding.str_id,
                        is_valid=True,
                        severity=finding.severity,
                        comment=f"Valid finding: {finding.title}"
                    ) for finding in workflow_findings
                ]
                mock_evaluate_batch.return_value = mock_evaluations
                
                # evaluate_groups returns (evaluations, findings that could not be evaluated)
                mock_evaluate_batch.return_value = (mock_evaluate_batch.return_value, [])
                evaluator = FindingEvaluator(batch_size=10, mongodb_client=mock_mongodb)
                eval_result = await evaluator.evaluate_all_findings(
                    "test-task",
                    workflow_findings,
                    [],  # No duplicate relationships
                    sample_task_cache
                )
                
                assert eval_result["application_results"]["valid_count"] == len(workflow_findings)

    @pytest.mark.asyncio
    async def test_workflow_error_handling_deduplication_failure(self, workflow_findings, sample_task_cache, mock_mongodb):
        """Test workflow behavior when deduplication fails."""
        
        with patch('app.core.deduplication.find_duplicates_structured') as mock_find_duplicates, \
             patch('app.core.deduplication.logger') as mock_logger:
            
            mock_find_duplicates.side_effect = Exception("Deduplication API error")
            mock_mongodb.update_finding = AsyncMock(return_value=True)
            
            deduplicator = FindingDeduplication(mongodb_client=mock_mongodb)
            
            result = await deduplicator.process_findings(
                "test-task",
                workflow_findings,
                sample_task_cache
            )
            
            # Verify error was logged during deduplication
            mock_logger.error.assert_called_with("Error during deduplication: Deduplication API error")
            
            # When deduplication fails, it should fallback to treating all findings as originals
            assert result["summary"]["duplicates_found"] == 0
            assert result["summary"]["originals_found"] == len(workflow_findings)
            
            # All findings should be updated with UNIQUE_VALID status in the fallback
            assert mock_mongodb.update_finding.call_count == len(workflow_findings)
    
    @pytest.mark.asyncio
    async def test_workflow_error_handling_evaluation_failure(self, workflow_findings, sample_task_cache, mock_mongodb):
        """Test workflow behavior when evaluation fails."""
        
        # Deduplication succeeds
        with patch('app.core.deduplication.find_duplicates_structured') as mock_find_duplicates:
            mock_find_duplicates.return_value = DeduplicationResult(results=[])
            mock_mongodb.update_finding = AsyncMock()
            
            deduplicator = FindingDeduplication(mongodb_client=mock_mongodb)
            dedup_result = await deduplicator.process_findings(
                "test-task",
                workflow_findings,
                sample_task_cache
            )
            
            # Evaluation fails
            with patch.object(FindingEvaluator, 'evaluate_groups') as mock_evaluate_batch:
                mock_evaluate_batch.side_effect = Exception("Evaluation API error")
                
                # evaluate_groups returns (evaluations, findings that could not be evaluated)
                mock_evaluate_batch.return_value = (mock_evaluate_batch.return_value, [])
                evaluator = FindingEvaluator(batch_size=10, mongodb_client=mock_mongodb)
                
                with pytest.raises(Exception, match="Evaluation API error"):
                    await evaluator.evaluate_all_findings(
                        "test-task",
                        workflow_findings,
                        [],
                        sample_task_cache
                    )
