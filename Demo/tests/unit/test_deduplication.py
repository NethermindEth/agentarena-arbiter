"""
Unit tests for deduplication logic.
"""
import pytest
from unittest.mock import AsyncMock, Mock, patch

from tests.conftest import mock_mongodb
from app.core.gemini_model import DeduplicationResult, DuplicateFinding
from app.core.deduplication import FindingDeduplication
from app.models.finding_db import Status

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


class TestDetermineFindingStatus:
    """Status assignment must be deterministic and credit each agent once per group."""

    @pytest.fixture
    def deduplicator(self):
        return FindingDeduplication(mongodb_client=mock_mongodb)

    @staticmethod
    def _finding(str_id: str, agent_id: str) -> Mock:
        f = Mock()
        f.str_id = str_id
        f.agent_id = agent_id
        f.status = Status.PENDING
        return f

    def test_duplicate_from_same_agent_as_original_is_already_reported(self, deduplicator):
        # Alice reports the same issue three times: one original + two duplicates.
        o = self._finding("o", "alice")
        d1 = self._finding("d1", "alice")
        d2 = self._finding("d2", "alice")
        finding_map = {"o": o, "d1": d1, "d2": d2}
        original_to_duplicates = {"o": ["d1", "d2"]}
        duplicate_to_original = {"d1": "o", "d2": "o"}

        assert (
            deduplicator.determine_finding_status(o, original_to_duplicates, duplicate_to_original, finding_map)
            == Status.BEST_VALID
        )
        # Alice is already credited through the original, so both duplicates are already reported.
        assert (
            deduplicator.determine_finding_status(d1, original_to_duplicates, duplicate_to_original, finding_map)
            == Status.ALREADY_REPORTED
        )
        assert (
            deduplicator.determine_finding_status(d2, original_to_duplicates, duplicate_to_original, finding_map)
            == Status.ALREADY_REPORTED
        )

    def test_duplicate_from_different_agent_is_similar_valid(self, deduplicator):
        o = self._finding("o", "alice")
        d = self._finding("d", "bob")
        finding_map = {"o": o, "d": d}
        assert (
            deduplicator.determine_finding_status(d, {"o": ["d"]}, {"d": "o"}, finding_map)
            == Status.SIMILAR_VALID
        )

    def test_repeated_duplicate_from_same_agent_is_credited_once(self, deduplicator):
        # Original by alice; carol reports the same issue twice.
        o = self._finding("o", "alice")
        c1 = self._finding("c1", "carol")
        c2 = self._finding("c2", "carol")
        finding_map = {"o": o, "c1": c1, "c2": c2}
        original_to_duplicates = {"o": ["c1", "c2"]}
        duplicate_to_original = {"c1": "o", "c2": "o"}
        s1 = deduplicator.determine_finding_status(c1, original_to_duplicates, duplicate_to_original, finding_map)
        s2 = deduplicator.determine_finding_status(c2, original_to_duplicates, duplicate_to_original, finding_map)
        assert {s1, s2} == {Status.SIMILAR_VALID, Status.ALREADY_REPORTED}

    def test_status_is_order_independent(self, deduplicator):
        o = self._finding("o", "alice")
        d1 = self._finding("d1", "alice")
        d2 = self._finding("d2", "alice")
        finding_map = {"o": o, "d1": d1, "d2": d2}
        original_to_duplicates = {"o": ["d1", "d2"]}
        duplicate_to_original = {"d1": "o", "d2": "o"}

        def statuses(order):
            return [
                deduplicator.determine_finding_status(
                    f, original_to_duplicates, duplicate_to_original, finding_map
                )
                for f in order
            ]

        forward = statuses([o, d1, d2])
        reverse = statuses([d2, d1, o])
        assert sorted(s.value for s in forward) == sorted(s.value for s in reverse)
        assert forward.count(Status.BEST_VALID) == 1
        assert forward.count(Status.ALREADY_REPORTED) == 2
        assert forward.count(Status.SIMILAR_VALID) == 0
