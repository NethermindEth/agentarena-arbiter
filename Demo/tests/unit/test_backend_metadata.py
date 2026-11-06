"""
Unit tests for the arbiter's per-task metadata through the backend's arbiter API.
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.backend_api import TaskNotFoundError
from app.backend_metadata import (
    fetch_processed_marker,
    fetch_task_metadata,
    mark_task_processed,
    store_task_metadata,
)

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


@pytest.mark.asyncio
class TestFetchTaskMetadata:
    async def test_stored_metadata_is_returned(self, config, http):
        """The task id is escaped into the URL and the stored object comes back as is."""
        http.get.return_value = respond(200, {"processed_at": "2026-01-02T00:00:00+00:00"})

        metadata = await fetch_task_metadata("task/1", config)

        http.get.assert_called_once_with(f"{BASE}/task%2F1/metadata", headers=HEADERS)
        assert metadata == {"processed_at": "2026-01-02T00:00:00+00:00"}

    @pytest.mark.parametrize("body", [None, {}])
    async def test_no_metadata_is_an_empty_dict(self, config, http, body):
        """Null and an empty object both mean nothing was stored."""
        http.get.return_value = respond(200, body)

        assert await fetch_task_metadata("task-1", config) == {}

    async def test_unknown_task_raises_not_found(self, config, http):
        """A 404 means the task does not exist, unlike a task with no metadata."""
        http.get.return_value = respond(404, {"detail": "Task not found"})

        with pytest.raises(TaskNotFoundError):
            await fetch_task_metadata("missing", config)

    async def test_other_errors_raise(self, config, http):
        """A server error is not an unknown task."""
        http.get.return_value = respond(503, {"detail": "unavailable"})

        with pytest.raises(httpx.HTTPStatusError):
            await fetch_task_metadata("task-1", config)


@pytest.mark.asyncio
class TestStoreTaskMetadata:
    async def test_body_is_put_and_stored_object_returned(self, config, http):
        """The whole object goes as the PUT body, and the backend's copy comes back."""
        http.put.return_value = respond(200, {"a": 1})

        stored = await store_task_metadata("task-1", {"a": 1}, config)

        http.put.assert_called_once_with(f"{BASE}/task-1/metadata", json={"a": 1}, headers=HEADERS)
        assert stored == {"a": 1}

    async def test_unknown_task_raises_not_found(self, config, http):
        """A 404 means the task does not exist."""
        http.put.return_value = respond(404, {"detail": "Task not found"})

        with pytest.raises(TaskNotFoundError):
            await store_task_metadata("missing", {"a": 1}, config)


@pytest.mark.asyncio
class TestProcessedMarker:
    @pytest.mark.parametrize("body", [None, {}, {"other": "value"}])
    async def test_unmarked_task_has_no_marker(self, config, http, body):
        """Metadata without `processed_at` is not a marker."""
        http.get.return_value = respond(200, body)

        assert await fetch_processed_marker("task-1", config) is None

    async def test_marked_task_returns_marker(self, config, http):
        """Stored metadata with `processed_at` is the marker."""
        marker = {"processed_at": "2026-01-02T00:00:00+00:00", "scheduled_processing": True}
        http.get.return_value = respond(200, marker)

        assert await fetch_processed_marker("task-1", config) == marker

    async def test_mark_stores_a_json_marker(self, config, http):
        """The marker records the time as ISO 8601 and how the run was started."""
        http.put.return_value = respond(200, {})

        processed_at = await mark_task_processed("task-1", config, scheduled=True)

        body = http.put.call_args.kwargs["json"]
        assert body == {"processed_at": processed_at.isoformat(), "scheduled_processing": True}
        assert datetime.fromisoformat(body["processed_at"]).tzinfo is not None
