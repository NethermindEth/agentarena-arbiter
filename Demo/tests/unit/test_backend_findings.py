"""
Unit tests for the finding writes through the backend's arbiter API.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from bson import ObjectId

from app.backend_findings import update_finding
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
