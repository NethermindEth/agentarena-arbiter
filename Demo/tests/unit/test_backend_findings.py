"""
Unit tests for the finding writes through the backend's arbiter API.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from bson import ObjectId

from app.backend_findings import fetch_findings, update_finding
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


@pytest.mark.asyncio
class TestUpdateFinding:
    async def test_dict_fields_are_sent_as_json(self, config, http):
        """Enums and datetimes in a field dict are serialized for the PATCH body."""
        http.patch.return_value = respond(204)
        when = datetime(2026, 1, 2, tzinfo=UTC)

        ok = await update_finding(
            "task-1",
            "abc",
            {"status": Status.DISPUTED, "evaluated_severity": Severity.LOW, "updated_at": when},
            config,
        )

        assert ok is True
        http.patch.assert_called_once_with(
            f"{BASE}/task-1/findings/abc",
            json={
                "status": "disputed",
                "evaluated_severity": "Low",
                "updated_at": "2026-01-02T00:00:00Z",
            },
            headers=HEADERS,
        )

    async def test_finding_object_sends_set_fields_without_id(self, config, http):
        """A FindingDB sends the fields it has set, but never its id, which the URL carries."""
        http.patch.return_value = respond(204)
        finding = FindingDB.model_validate(
            {
                "_id": ObjectId(),
                **FINDING.model_dump(),
                "agent_id": "agent-1",
                "status": Status.PENDING,
            }
        )
        finding.status = Status.UNIQUE_VALID

        await update_finding("task-1", finding.str_id, finding, config)

        body = http.patch.call_args.kwargs["json"]
        assert "_id" not in body
        assert "id" not in body
        assert body["status"] == "unique_valid"
        assert body["agent_id"] == "agent-1"

    async def test_not_found_returns_false(self, config, http):
        """A 404 for the task or finding reports a failed update rather than raising."""
        http.patch.return_value = respond(404, {"detail": "Finding not found"})

        assert await update_finding("task-1", "abc", {"status": "pending"}, config) is False

    async def test_other_errors_raise(self, config, http):
        """A refused body is a bug, not a missing finding."""
        http.patch.return_value = respond(422, {"detail": "extra field"})

        with pytest.raises(httpx.HTTPStatusError):
            await update_finding("task-1", "abc", {"bogus": 1}, config)


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
        """Status goes as its value and the timestamp as ISO 8601, offset included."""
        http.get.return_value = respond(200, [])
        since = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

        await fetch_findings(
            "task-1",
            config,
            agent_id="agent-1",
            status=Status.UNIQUE_VALID,
            since_timestamp=since,
        )

        assert http.get.call_args.kwargs["params"] == {
            "agent_id": "agent-1",
            "status": "unique_valid",
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
class TestSharedClient:
    async def test_given_client_is_used_and_left_open(self, config):
        """A caller's client sends the requests, and no client of its own is created."""
        client = AsyncMock()
        client.get.return_value = respond(200, [])
        client.patch.return_value = respond(204)

        with patch("httpx.AsyncClient") as client_class:
            await fetch_findings("task-1", config, client=client)
            await update_finding("task-1", "abc", {"status": "pending"}, config, client=client)

        client_class.assert_not_called()
        client.get.assert_called_once()
        client.patch.assert_called_once()
        client.aclose.assert_not_called()


def test_timestamp_offset_survives_query_encoding():
    """httpx percent-encodes the offset's '+', which a raw query string would read as a space."""
    request = httpx.Request("GET", BASE, params={"since_timestamp": "2026-01-02T03:04:05+00:00"})

    assert "since_timestamp=2026-01-02T03%3A04%3A05%2B00%3A00" in str(request.url)
