"""
The arbiter's own per-task metadata, which the backend keeps at
`/api/arbiter/tasks/{task_id}/metadata`.

The backend stores the object as given, apart from the task, and no other route shows or changes
it; the arbiter keeps its processed-task marker there. A PUT replaces the whole object.
"""

from datetime import UTC, datetime
from typing import Any

import httpx

from app.backend_api import TaskNotFoundError, auth_headers, client_or_new, task_url
from app.config import Settings


def _metadata_url(config: Settings, task_id: str) -> str:
    return f"{task_url(config, task_id)}/metadata"


async def fetch_task_metadata(
    task_id: str, config: Settings, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """
    Fetch a task's metadata through `GET /api/arbiter/tasks/{task_id}/metadata`.

    Args:
        task_id: Task identifier
        config: Application configuration, giving the endpoint and the shared API key
        client: Client to send the request with (optional); a new one is used when left out

    Returns:
        The metadata as last stored, or an empty dict when none was

    Raises:
        TaskNotFoundError: when the backend does not know the task (404)
        httpx.HTTPError: when the request fails or the backend answers with another error status
    """
    async with client_or_new(client) as http:
        response = await http.get(_metadata_url(config, task_id), headers=auth_headers(config))
    if response.status_code == 404:
        raise TaskNotFoundError(task_id)
    response.raise_for_status()
    # The backend answers null (or an empty object) for a task with no metadata stored.
    return response.json() or {}


async def store_task_metadata(
    task_id: str,
    metadata: dict[str, Any],
    config: Settings,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Replace a task's metadata through `PUT /api/arbiter/tasks/{task_id}/metadata`.

    Args:
        task_id: Task identifier
        metadata: The whole new metadata object; JSON-serializable values only
        config: Application configuration, giving the endpoint and the shared API key
        client: Client to send the request with (optional); a new one is used when left out

    Returns:
        The metadata as the backend stored it

    Raises:
        TaskNotFoundError: when the backend does not know the task (404)
        httpx.HTTPError: when the request fails or the backend answers with another error status
    """
    async with client_or_new(client) as http:
        response = await http.put(
            _metadata_url(config, task_id), json=metadata, headers=auth_headers(config)
        )
    if response.status_code == 404:
        raise TaskNotFoundError(task_id)
    response.raise_for_status()
    return response.json()


async def fetch_processed_marker(task_id: str, config: Settings) -> dict[str, Any] | None:
    """
    Return the task's processed marker, or None when the task has not been processed yet.

    Raises:
        TaskNotFoundError: when the backend does not know the task (404)
        httpx.HTTPError: when the request fails or the backend answers with another error status
    """
    metadata = await fetch_task_metadata(task_id, config)
    return metadata if metadata.get("processed_at") else None


async def mark_task_processed(task_id: str, config: Settings, *, scheduled: bool) -> datetime:
    """
    Store the processed marker, so neither the scheduled job nor a manual trigger runs it again.

    The marker is the task's whole metadata object, so storing it replaces anything else there.

    Args:
        task_id: Task identifier
        config: Application configuration, giving the endpoint and the shared API key
        scheduled: Whether the scheduled job processed the task, rather than a manual trigger

    Returns:
        The processing time recorded in the marker

    Raises:
        TaskNotFoundError: when the backend does not know the task (404)
        httpx.HTTPError: when the request fails or the backend answers with another error status
    """
    processed_at = datetime.now(UTC)
    await store_task_metadata(
        task_id,
        {"processed_at": processed_at.isoformat(), "scheduled_processing": scheduled},
        config,
    )
    return processed_at
