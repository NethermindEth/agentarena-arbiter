"""
Unit tests for evaluation logic.
"""

from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId

from app.core.claude_code_aggregator import AggregatedFinding
from app.core.claude_code_cli import json_block
from app.core.evaluation import (
    MAX_AGGREGATION_JSON_CHARS,
    MAX_MEMBER_REPORT_JSON_CHARS,
    MAX_MEMBER_REPORTS,
    MAX_MEMBER_REPORTS_JSON_CHARS,
    FindingEvaluation,
    FindingEvaluator,
)
from app.core.gemini_model import DuplicateFinding
from app.models.finding_db import FindingDB, Severity, Status


class TestFindingEvaluator:
    """Test FindingEvaluator class."""

    @pytest.fixture
    def evaluator(self):
        """Create FindingEvaluator instance."""
        return FindingEvaluator(batch_size=5)

    def test_initialization(self, evaluator):
        """Test evaluator initializes with correct batch size."""
        assert evaluator.batch_size == 5

    def test_group_findings_for_evaluation_no_duplicates(self, evaluator, sample_findings):
        """Test grouping findings when there are no duplicates."""
        duplicate_relationships = []

        related_groups, individual_groups = evaluator.group_findings_for_evaluation(
            sample_findings, duplicate_relationships
        )

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
                explanation="Both are reentrancy issues",
            )
        ]

        related_groups, individual_groups = evaluator.group_findings_for_evaluation(
            sample_findings, duplicate_relationships
        )

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
        result = await evaluator.evaluate_all_findings([], [], sample_task_cache)

        assert result is not None
        assert "application_results" in result
        assert result["application_results"]["valid_count"] == 0
        assert result["application_results"]["disputed_count"] == 0
        assert result["application_results"]["failed_count"] == 0

    @pytest.mark.asyncio
    async def test_evaluate_all_findings_success(
        self, evaluator, sample_findings, sample_task_cache
    ):
        """Test successful evaluation of findings."""
        with patch.object(evaluator, "evaluate_groups") as mock_evaluate_groups:
            # The triage returns (evaluations, findings that could not be evaluated)
            mock_evaluate_groups.return_value = (
                [
                    FindingEvaluation(
                        finding_id=sample_findings[0].str_id,
                        is_valid=True,
                        severity=Severity.HIGH,
                        comment="Valid security issue",
                    )
                ],
                [],
            )

            result = await evaluator.evaluate_all_findings(
                sample_findings[:1],  # Just test with one finding
                [],
                sample_task_cache,
            )

            assert result["application_results"]["valid_count"] == 1
            assert result["application_results"]["disputed_count"] == 0
            assert result["application_results"]["failed_count"] == 0
            assert result["failed_findings"] == []

            # The verdict is set on the finding itself
            assert sample_findings[0].evaluated_severity == Severity.HIGH
            assert sample_findings[0].evaluation_comment == "Valid security issue"

    def test_apply_evaluation_results_disputes_invalid_findings(self, evaluator, sample_findings):
        """An invalid verdict disputes the finding and keeps its comment and severity."""
        finding = sample_findings[0]
        finding.status = Status.UNIQUE_VALID

        result = evaluator.apply_evaluation_results(
            [finding],
            [
                FindingEvaluation(
                    finding_id=finding.str_id,
                    is_valid=False,
                    severity=Severity.LOW,
                    comment="Not exploitable",
                )
            ],
        )

        assert result["disputed_count"] == 1
        assert finding.status == Status.DISPUTED
        assert finding.evaluated_severity == Severity.LOW
        assert finding.evaluation_comment == "Not exploitable"

    def test_apply_evaluation_results_unknown_finding_is_failed(self, evaluator, sample_findings):
        """A verdict for a finding that was not evaluated changes nothing and counts as failed."""
        sample_findings[0].evaluation_comment = None

        result = evaluator.apply_evaluation_results(
            sample_findings[:1],
            [
                FindingEvaluation(
                    finding_id="000000000000000000000000",
                    is_valid=False,
                    severity=Severity.LOW,
                    comment="?",
                )
            ],
        )

        assert result["failed_count"] == 1
        assert sample_findings[0].evaluation_comment is None


def _finding(
    i: int,
    description: str = "d",
    paths: list[str] | None = None,
    severity: Severity = Severity.HIGH,
) -> FindingDB:
    return FindingDB(
        _id=ObjectId(),
        title=f"Finding {i}",
        description=description,
        severity=severity,
        file_paths=paths if paths is not None else [f"src/F{i}.sol"],
        agent_id=f"agent{i}",
    )


def _dup(finding, original) -> DuplicateFinding:
    return DuplicateFinding(findingId=finding.str_id, duplicateOf=original.str_id, explanation="")


class TestTransitiveGrouping:
    """Duplicate relationships are merged transitively, whatever their order."""

    @pytest.mark.parametrize("reverse", [False, True])
    def test_a_chain_is_one_group_led_by_its_root(self, reverse):
        a, b, c = _finding(0), _finding(1), _finding(2)
        relationships = [_dup(a, b), _dup(b, c)]  # A duplicates B, B duplicates C
        if reverse:
            relationships.reverse()

        related, individual = FindingEvaluator().group_findings_for_evaluation(
            [a, b, c], relationships
        )

        assert related == [[c, a, b]]
        assert individual == []

    def test_groups_joined_through_a_shared_member_merge(self):
        a, b, c, d, e = (_finding(i) for i in range(5))
        relationships = [_dup(a, b), _dup(c, d), _dup(b, d)]

        related, individual = FindingEvaluator().group_findings_for_evaluation(
            [a, b, c, d, e], relationships
        )

        assert related == [[d, a, b, c]]
        assert individual == [[e]]

    def test_self_and_unknown_relationships_are_ignored(self):
        a, b = _finding(0), _finding(1)
        relationships = [
            _dup(a, a),
            DuplicateFinding(findingId=b.str_id, duplicateOf="missing", explanation=""),
        ]

        related, individual = FindingEvaluator().group_findings_for_evaluation(
            [a, b], relationships
        )

        assert related == []
        assert individual == [[a, b]]


class TestPromptBudgets:
    """Member reports and the aggregation prompt stay within their size budgets."""

    def test_member_reports_are_capped_in_number_and_size(self):
        # Non-ASCII text takes six characters per character once escaped into the prompt.
        group = [_finding(i, "é" * 20_000) for i in range(15)]

        reports, omitted = FindingEvaluator()._member_reports(group)

        sizes = [len(json_block(r)) for r in reports]
        assert 0 < len(reports) <= MAX_MEMBER_REPORTS
        assert omitted == len(group) - len(reports)
        assert max(sizes) <= MAX_MEMBER_REPORT_JSON_CHARS
        assert sum(sizes) <= MAX_MEMBER_REPORTS_JSON_CHARS
        assert all(r["description"].endswith("(truncated by the arbiter)") for r in reports)

    def test_short_member_reports_are_kept_whole(self):
        group = [_finding(i, f"description {i}") for i in range(3)]

        evaluator = FindingEvaluator()
        unit = evaluator._group_unit("u000", group, group[0])

        assert [r["description"] for r in unit.finding["member_reports"]] == [
            f.description for f in group
        ]
        assert unit.finding["member_reports_omitted"] == 0

    def test_many_member_reports_are_counted_when_left_out(self):
        group = [_finding(i) for i in range(MAX_MEMBER_REPORTS + 3)]

        unit = FindingEvaluator()._group_unit("u000", group, group[0])

        assert len(unit.finding["member_reports"]) == MAX_MEMBER_REPORTS
        assert unit.finding["member_reports_omitted"] == 3

    @pytest.mark.parametrize("members,merge_ok", [(40, True), (150, True), (150, False)])
    async def test_aggregation_fits_the_budget_and_keeps_every_path(
        self, sample_task_cache, members, merge_ok
    ):
        group = [_finding(i, "x" * 30_000) for i in range(members)]
        evaluator = FindingEvaluator()
        evaluator.aggregator.aggregate = AsyncMock(
            return_value=AggregatedFinding("Merged", "merged", "High", ["src/Merged.sol"])
            if merge_ok
            else None
        )

        merged = await evaluator._aggregate(group, sample_task_cache)

        payload = evaluator.aggregator.aggregate.call_args.kwargs["findings"]
        assert sum(len(json_block(item)) for item in payload) <= MAX_AGGREGATION_JSON_CHARS
        # 40 members share the budget; 150 would each get less than the minimum share, so the
        # prompt is cut at the budget instead.
        if members == 40:
            assert len(payload) == members
        else:
            assert 100 <= len(payload) < members
        # Paths of members left out of the prompt are kept too.
        expected = {f"src/F{i}.sol" for i in range(members)} | (
            {"src/Merged.sol"} if merge_ok else set()
        )
        assert set(merged.file_paths) == expected


class TestClaimedSeverity:
    """A unit claims the highest severity of its reports; it decides whether it gets PoCs."""

    async def test_a_group_claims_its_highest_member_severity(self, sample_task_cache):
        group = [
            _finding(0, severity=Severity.LOW),
            _finding(1, severity=Severity.HIGH),
            _finding(2, severity=Severity.INFO),
        ]
        evaluator = FindingEvaluator()
        # The merge rates the group Low; it cannot talk the group out of its PoCs.
        evaluator.aggregator.aggregate = AsyncMock(
            return_value=AggregatedFinding("Merged", "merged", "Low", [])
        )

        unit = await evaluator._build_unit("u000", group, sample_task_cache)

        assert unit.claimed_severity == "High"
        assert unit.finding["claimed_severity"] == "Low"  # the merged finding is shown as merged

    async def test_a_single_finding_claims_its_own_severity(self, sample_task_cache):
        unit = await FindingEvaluator()._build_unit(
            "u000", [_finding(0, severity=Severity.INFO)], sample_task_cache
        )
        assert unit.claimed_severity == "Info"
