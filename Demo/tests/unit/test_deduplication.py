"""
Unit tests for deduplication logic.
"""

from unittest.mock import patch

import pytest

from app.core.deduplication import FindingDeduplication
from app.core.gemini_model import DeduplicationResult, DuplicateFinding
from app.models.finding_db import Status


class TestFindingDeduplication:
    """Test FindingDeduplication class."""

    @pytest.fixture
    def deduplicator(self):
        """Create FindingDeduplication instance."""
        return FindingDeduplication()

    def test_initialization(self, deduplicator):
        """Test deduplicator initializes correctly."""
        assert deduplicator is not None
        # Add more specific initialization checks if needed

    @pytest.mark.asyncio
    async def test_process_findings_empty_list(self, deduplicator, sample_task_cache):
        """Test processing empty findings list."""
        with patch("app.core.deduplication.find_duplicates_structured") as mock_find_duplicates:
            mock_find_duplicates.return_value = DeduplicationResult(results=[])

            result = await deduplicator.process_findings("test-task", [], sample_task_cache)

            assert result is not None
            assert "deduplication" in result
            assert "summary" in result
            assert result["summary"]["originals_found"] == 0
            assert result["summary"]["duplicates_found"] == 0

    @pytest.mark.asyncio
    async def test_process_findings_no_duplicates(
        self, deduplicator, sample_findings, sample_task_cache
    ):
        """Test processing findings with no duplicates found."""
        with patch("app.core.deduplication.find_duplicates_structured") as mock_find_duplicates:
            mock_find_duplicates.return_value = DeduplicationResult(results=[])

            # Use only the first finding to avoid duplicates
            single_finding = [sample_findings[0]]
            result = await deduplicator.process_findings(
                "test-task", single_finding, sample_task_cache
            )

            # No duplicates found, so there are no original or duplicate findings
            assert result["summary"]["originals_found"] == 0
            assert result["summary"]["duplicates_found"] == 0
            assert len(result["deduplication"]["duplicate_relationships"]) == 0

            # The finding is set unique in memory
            assert single_finding[0].status == Status.UNIQUE_VALID
            assert result["summary"]["successfully_updated"] == 1

    @pytest.mark.asyncio
    async def test_process_findings_with_duplicates(
        self, deduplicator, sample_findings, sample_task_cache
    ):
        """Test processing findings with duplicates detected."""
        with patch("app.core.deduplication.find_duplicates_structured") as mock_find_duplicates:
            # Mock finding duplicates (findings 1 and 2 are similar reentrancy issues)
            mock_duplicates = DeduplicationResult(
                results=[
                    DuplicateFinding(
                        findingId=sample_findings[1].str_id,
                        duplicateOf=sample_findings[0].str_id,
                        explanation="Both describe reentrancy vulnerabilities",
                    )
                ]
            )
            mock_find_duplicates.return_value = mock_duplicates

            result = await deduplicator.process_findings(
                "test-task", sample_findings, sample_task_cache
            )

            assert result["summary"]["duplicates_found"] == 1
            assert len(result["deduplication"]["duplicate_relationships"]) == 1

            # Check that the duplicate relationship is recorded correctly
            dup_rel = result["deduplication"]["duplicate_relationships"][0]
            assert dup_rel.findingId == sample_findings[1].str_id
            assert dup_rel.duplicateOf == sample_findings[0].str_id

            # The statuses and the duplicate link are set on the findings themselves
            assert sample_findings[0].status == Status.BEST_VALID
            assert sample_findings[1].status == Status.SIMILAR_VALID
            assert sample_findings[1].duplicateOf == sample_findings[0].str_id
            assert sample_findings[1].deduplication_comment

    @pytest.mark.asyncio
    async def test_writes_nothing_to_the_backend(
        self, deduplicator, sample_findings, sample_task_cache
    ):
        """Deduplication sends no request; the run posts every verdict once, after evaluation."""
        with (
            patch("app.core.deduplication.find_duplicates_structured") as mock_find_duplicates,
            patch("httpx.AsyncClient") as client_class,
        ):
            mock_find_duplicates.return_value = DeduplicationResult(results=[])

            await deduplicator.process_findings("test-task", sample_findings, sample_task_cache)

        client_class.assert_not_called()
