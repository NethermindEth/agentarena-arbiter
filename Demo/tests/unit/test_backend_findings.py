"""
Unit tests for the finding writes through the backend's arbiter API.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from bson import ObjectId

from app.backend_findings import fetch_findings, post_results
from app.models.finding_db import FindingDB, Status
from app.models.finding_input import Finding, Severity

BASE = "http://test.com/api/arbiter/tasks"
HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture
def config():
    """Settings with the backend endpoint and key the calls use."""
    config = Mock()
    config.backend_tasks_endpoint = BASE
    config.backend_api_key = "test-key"
    return config


@pytest.fixture
def http():
    """Patch httpx.AsyncClient and return the client its context manager yields."""
    with patch("httpx.AsyncClient") as client_class:
        client = AsyncMock()
        client_class.return_value.__aenter__.return_value = client
        yield client


def respond(status_code: int, body=None) -> Mock:
    """Build a response mock whose raise_for_status behaves like httpx's."""
    response = Mock()
    response.status_code = status_code
    response.json.return_value = body
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=Mock(), response=response
        )
    return response


FINDING = Finding(
    title="Reentrancy", description="withdraw", severity=Severity.HIGH, file_paths=["a.sol"]
)


def stored_json(finding_id: ObjectId, **overrides) -> dict:
    """A finding as the backend's findings listing returns it."""
    now = "2026-01-02T00:00:00Z"
    return {
        **FINDING.model_dump(mode="json"),
        "_id": str(finding_id),
        "agent_id": "agent-1",
        "status": "pending",
        "duplicateOf": None,
        "deduplication_comment": None,
        "evaluated_severity": None,
        "evaluation_comment": None,
        "created_at": now,
        "updated_at": now,
        **overrides,
    }


@pytest.mark.asyncio
class TestFetchFindings:
    async def test_no_filters_sends_no_params(self, config, http):
        """Without filters the whole task is listed, and ids come back as ObjectIds."""
        finding_id = ObjectId()
        http.get.return_value = respond(200, [stored_json(finding_id)])

        findings = await fetch_findings("task/1", config)

        http.get.assert_called_once_with(f"{BASE}/task%2F1/findings", params={}, headers=HEADERS)
        assert len(findings) == 1
        assert isinstance(findings[0], FindingDB)
        assert findings[0].id == finding_id
        assert findings[0].str_id == str(finding_id)
        assert findings[0].status == Status.PENDING

    async def test_filters_are_sent_as_query_params(self, config, http):
        """The agent goes as is and the timestamp as ISO 8601, offset included."""
        http.get.return_value = respond(200, [])
        since = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

        await fetch_findings(
            "task-1",
            config,
            agent_id="agent-1",
            since_timestamp=since,
        )

        assert http.get.call_args.kwargs["params"] == {
            "agent_id": "agent-1",
            "since_timestamp": "2026-01-02T03:04:05+00:00",
        }

    async def test_unknown_task_returns_none(self, config, http):
        """A 404 is None rather than an empty list, so a misrouted call cannot look idle."""
        http.get.return_value = respond(404, {"detail": "Task not found"})

        assert await fetch_findings("missing", config) is None

    async def test_other_errors_raise(self, config, http):
        """A server error is not an unknown task."""
        http.get.return_value = respond(503, {"detail": "unavailable"})

        with pytest.raises(httpx.HTTPStatusError):
            await fetch_findings("task-1", config)


@pytest.mark.asyncio
class TestPostResults:
    @staticmethod
    def judged() -> FindingDB:
        finding_id = ObjectId()
        finding = FindingDB.model_validate({**stored_json(finding_id), "_id": finding_id})
        finding.status = Status.SIMILAR_VALID
        finding.duplicateOf = "other"
        finding.deduplication_comment = "Same bug"
        finding.evaluated_severity = Severity.LOW
        finding.evaluation_comment = "Valid"
        return finding

    async def test_every_finding_goes_in_one_post(self, config, http):
        """The whole task is one request carrying each finding's verdict as JSON."""
        config.backend_findings_endpoint = "http://test.com/api/insert-findings"
        http.post.return_value = respond(200)
        findings = [self.judged(), self.judged()]

        await post_results("task-1", findings, config)

        http.post.assert_called_once()
        assert http.post.call_args.args[0] == "http://test.com/api/insert-findings"
        assert http.post.call_args.kwargs["headers"] == HEADERS
        body = http.post.call_args.kwargs["json"]
        assert body["task_id"] == "task-1"
        assert [f["id"] for f in body["findings"]] == [f.str_id for f in findings]
        assert body["findings"][0] | {"created_at": None} == {
            "id": findings[0].str_id,
            "agent_id": "agent-1",
            "title": "Reentrancy",
            "description": "withdraw",
            "severity": "High",
            "status": "similar_valid",
            "file_paths": ["a.sol"],
            "duplicate_of": "other",
            "deduplication_comment": "Same bug",
            "evaluated_severity": "Low",
            "evaluation_comment": "Valid",
            "created_at": None,
        }

    async def test_error_status_raises(self, config, http):
        """A refused post is an error, so the run is not reported as completed."""
        config.backend_findings_endpoint = "http://test.com/api/insert-findings"
        http.post.return_value = respond(409, {"detail": "task not approved"})

        with pytest.raises(httpx.HTTPStatusError):
            await post_results("task-1", [self.judged()], config)


@pytest.mark.asyncio
class TestSharedClient:
    async def test_given_client_is_used_and_left_open(self, config):
        """A caller's client sends the requests, and no client of its own is created."""
        client = AsyncMock()
        client.get.return_value = respond(200, [])
        client.post.return_value = respond(200)

        with patch("httpx.AsyncClient") as client_class:
            await fetch_findings("task-1", config, client=client)
            await post_results("task-1", [], config, client=client)

        client_class.assert_not_called()
        client.get.assert_called_once()
        client.post.assert_called_once()
        client.aclose.assert_not_called()


def test_timestamp_offset_survives_query_encoding():
    """httpx percent-encodes the offset's '+', which a raw query string would read as a space."""
    request = httpx.Request("GET", BASE, params={"since_timestamp": "2026-01-02T03:04:05+00:00"})

    assert "since_timestamp=2026-01-02T03%3A04%3A05%2B00%3A00" in str(request.url)
