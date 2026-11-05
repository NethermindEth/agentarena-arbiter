"""Task metadata read from the AgentArena backend's arbiter API."""

from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from app.config import Settings
from app.types import Task

_task_list = TypeAdapter(list[Task])


async def fetch_approved_tasks(config: Settings) -> list[Task]:
    """
    Fetch the approved tasks from the backend's approved-tasks endpoint.

    Args:
        config: Application configuration, giving the endpoint and the shared API key

    Returns:
        The approved tasks, validated as Task objects

    Raises:
        httpx.HTTPError: when the request fails or the backend answers with an error status
        pydantic.ValidationError: when the response body is not a list of tasks
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            config.backend_approved_tasks_endpoint,
            headers={"X-API-Key": config.backend_api_key},
        )
        response.raise_for_status()
    return _task_list.validate_python(response.json())


async def fetch_task(task_id: str, config: Settings) -> Task | None:
    """
    Fetch one task from the backend's task endpoint.

    Args:
        task_id: Task identifier
        config: Application configuration, giving the endpoint and the shared API key

    Returns:
        The task, or None when the backend does not know it (404)

    Raises:
        httpx.HTTPError: when the request fails or the backend answers with another error status
        pydantic.ValidationError: when the response body is not a task
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{config.backend_tasks_endpoint}/{quote(task_id, safe='')}",
            headers={"X-API-Key": config.backend_api_key},
        )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return Task.model_validate(response.json())
