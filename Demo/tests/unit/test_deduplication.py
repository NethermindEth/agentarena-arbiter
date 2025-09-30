"""
Unit tests for deduplication logic.
"""
import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import mock_mongodb
from app.core.gemini_model import DeduplicationResult, DuplicateFinding
from app.core.deduplication import FindingDeduplication

class TestFindingDeduplication:
    """Test FindingDeduplication class."""
    
    @pytest.fixture
    def deduplicator(self):
        """Create FindingDeduplication instance."""
        return FindingDeduplication(mongodb_client=mock_mongodb)
    
    def test_initialization(self, deduplicator):
        """Test deduplicator initializes correctly."""
        assert deduplicator is not None
        # Add more specific initialization checks if needed
    
    @pytest.mark.asyncio
    async def test_process_findings_empty_list(self, deduplicator, sample_task_cache):
        """Test processing empty findings list."""
        with patch('app.core.deduplication.find_duplicates_structured') as mock_find_duplicates:
            mock_find_duplicates.return_value = DeduplicationResult(results=[])
            
            result = await deduplicator.process_findings("test-task", [], sample_task_cache)
            
            assert result is not None
            assert "deduplication" in result
            assert "summary" in result
            assert result["summary"]["originals_found"] == 0
            assert result["summary"]["duplicates_found"] == 0
    
    @pytest.mark.asyncio
    async def test_process_findings_no_duplicates(self, deduplicator, sample_findings, sample_task_cache):
        """Test processing findings with no duplicates found."""
        with patch('app.core.deduplication.find_duplicates_structured') as mock_find_duplicates:
            
            mock_find_duplicates.return_value = DeduplicationResult(results=[])
            mock_mongodb.update_finding = AsyncMock()
            
            # Use only the first finding to avoid duplicates
            single_finding = [sample_findings[0]]
            result = await deduplicator.process_findings("test-task", single_finding, sample_task_cache)
            
            # No duplicates found, so there are no original or duplicate findings
            assert result["summary"]["originals_found"] == 0
            assert result["summary"]["duplicates_found"] == 0
            assert len(result["deduplication"]["duplicate_relationships"]) == 0

            # The update_finding method should be called when setting the finding as unique
            mock_mongodb.update_finding.assert_called_once()
    
    @pytest.mark.asyncio 
    async def test_process_findings_with_duplicates(self, deduplicator, sample_findings, sample_task_cache):
        """Test processing findings with duplicates detected."""
        with patch('app.core.deduplication.find_duplicates_structured') as mock_find_duplicates:
            
            # Mock finding duplicates (findings 1 and 2 are similar reentrancy issues)
            mock_duplicates = DeduplicationResult(
                results=[DuplicateFinding(
                    findingId=sample_findings[1].str_id,
                    duplicateOf=sample_findings[0].str_id,
                    explanation='Both describe reentrancy vulnerabilities'
                )]
            )
            mock_find_duplicates.return_value = mock_duplicates
            mock_mongodb.update_finding = AsyncMock()
            
            result = await deduplicator.process_findings("test-task", sample_findings, sample_task_cache)
            
            assert result["summary"]["duplicates_found"] == 1
            assert len(result["deduplication"]["duplicate_relationships"]) == 1
            
            # Check that the duplicate relationship is recorded correctly
            dup_rel = result["deduplication"]["duplicate_relationships"][0]
            assert dup_rel.findingId == sample_findings[1].str_id
            assert dup_rel.duplicateOf == sample_findings[0].str_id

            mock_mongodb.update_finding.assert_called()
