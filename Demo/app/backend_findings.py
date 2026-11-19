"""
Finding reads and the result write through the AgentArena backend.

The backend stores agents' submissions itself; the arbiter reads them with `fetch_findings` and
writes a task's verdicts back in one call, `post_results`, once the whole run has decided them.
"""

from datetime import datetime
from typing import Any

import httpx
from bson import ObjectId
from pydantic_core import to_jsonable_python

from app.backend_api import auth_headers, client_or_new, task_url
from app.config import Settings
from app.models.finding_db import FindingDB


async def fetch_findings(
    task_id: str,
    config: Settings,
    agent_id: str | None = None,
    since_timestamp: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[FindingDB] | None:
    """
    Fetch a task's findings through `GET /api/arbiter/tasks/{task_id}/findings`.

    Args:
        task_id: Task identifier
        config: Application configuration, giving the endpoint and the shared API key
        agent_id: Only this agent's findings (optional)
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


def format_finding(finding: FindingDB) -> dict[str, Any]:
    """Return a finding with its verdict in the shape the backend's findings insert takes."""
    return {
        "id": finding.str_id,
        "agent_id": finding.agent_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity,
        "status": finding.status,
        "file_paths": finding.file_paths,
        "duplicate_of": finding.duplicateOf or None,
        "deduplication_comment": finding.deduplication_comment or None,
        "evaluated_severity": finding.evaluated_severity or None,
        "evaluation_comment": finding.evaluation_comment or None,
        "created_at": finding.created_at.isoformat(),
    }


async def post_results(
    task_id: str,
    findings: list[FindingDB],
    config: Settings,
    client: httpx.AsyncClient | None = None,
) -> None:
    """
    Write a task's judged findings to the backend in one `POST` to `BACKEND_FINDINGS_ENDPOINT`.

    This is the run's only write: the backend stores every verdict and closes the task in one
    transaction, so a run that fails before this call leaves the backend unchanged and can be
    retried over all the findings.

    Args:
        task_id: Task identifier
        findings: Every finding of the task, each carrying its status and verdict
        config: Application configuration, giving the endpoint and the shared API key
        client: Client to send the request with (optional); a new one is used when left out

    Raises:
        httpx.HTTPError: when the request fails or the backend answers with an error status
    """
    payload = {"task_id": task_id, "findings": [format_finding(f) for f in findings]}
    async with client_or_new(client) as http:
        response = await http.post(
            config.backend_findings_endpoint,
            json=to_jsonable_python(payload),
            headers=auth_headers(config),
        )
    response.raise_for_status()
