"""
Finding reads and verdict writes through the AgentArena backend's arbiter API.

The backend stores agents' submissions itself; the arbiter reads them with `fetch_findings` and
writes back only its verdicts, through `update_finding`.
"""

from datetime import datetime
from typing import Any

import httpx
from bson import ObjectId
from pydantic_core import to_jsonable_python

from app.backend_api import auth_headers, client_or_new, path_segment, task_url
from app.config import Settings
from app.models.finding_db import FindingDB, Status


async def fetch_findings(
    task_id: str,
    config: Settings,
    agent_id: str | None = None,
    status: Status | None = None,
    since_timestamp: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[FindingDB] | None:
    """
    Fetch a task's findings through `GET /api/arbiter/tasks/{task_id}/findings`.

    Args:
        task_id: Task identifier
        config: Application configuration, giving the endpoint and the shared API key
        agent_id: Only this agent's findings (optional)
        status: Only findings with this status (optional)
        since_timestamp: Only findings created strictly after this instant (optional); the
            backend reads one without an offset as UTC
        client: Client to send the request with (optional); a new one is used when left out

    Returns:
        The findings matching every filter given, or None when the backend does not know the
        task (404), which is distinct from a known task with no findings

    Raises:
        httpx.HTTPError: when the request fails or the backend answers with another error status
        bson.errors.InvalidId: when a finding's `_id` is not an ObjectId
    """
    params: dict[str, str] = {}
    if agent_id:
        params["agent_id"] = agent_id
    if status:
        params["status"] = status.value
    if since_timestamp:
        params["since_timestamp"] = since_timestamp.isoformat()
    async with client_or_new(client) as http:
        response = await http.get(
            f"{task_url(config, task_id)}/findings", params=params, headers=auth_headers(config)
        )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return [_finding_from_json(item) for item in response.json()]


def _finding_from_json(item: dict[str, Any]) -> FindingDB:
    """Build a FindingDB from the backend's JSON, whose `_id` is the ObjectId as a string."""
    raw_id = item.pop("_id", None) or item.pop("id")
    return FindingDB.model_validate({**item, "_id": ObjectId(raw_id)})


async def update_finding(
    task_id: str,
    finding_id: str,
    update_fields: dict[str, Any] | FindingDB,
    config: Settings,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """
    Update fields of a stored finding through
    `PATCH /api/arbiter/tasks/{task_id}/findings/{finding_id}`.

    The backend stamps `updated_at` when the update leaves it out.

    Args:
        task_id: Task identifier
        finding_id: Finding ID as string
        update_fields: Fields to write, or a FindingDB whose set fields are written
        config: Application configuration, giving the endpoint and the shared API key
        client: Client to send the request with (optional); a new one is used when left out

    Returns:
        True if the finding exists and was updated, False when the backend answers 404 for the
        task or the finding (an id that is no ObjectId included)

    Raises:
        httpx.HTTPError: when the request fails or the backend answers with another error status,
            422 included for a field the backend's finding does not have
    """
    if isinstance(update_fields, FindingDB):
        # The id names the finding in the URL; the backend refuses it as a field to write.
        body = update_fields.model_dump(mode="json", exclude_unset=True, exclude={"id"})
    else:
        body = to_jsonable_python(update_fields)
    async with client_or_new(client) as http:
        response = await http.patch(
            f"{task_url(config, task_id)}/findings/{path_segment(finding_id)}",
            json=body,
            headers=auth_headers(config),
        )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True
