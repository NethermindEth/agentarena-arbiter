"""
Finding writes through the AgentArena backend's arbiter API.

The backend stores them in the collections `MongoDBHandler.get_findings` reads, so the arbiter
still reads its findings from MongoDB but writes them only through these calls.
"""

from typing import Any

import httpx
from bson import ObjectId
from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

from app.backend_api import TIMEOUT_SECONDS, auth_headers, path_segment, task_url
from app.config import Settings
from app.models.finding_db import FindingDB, Status
from app.models.finding_input import Finding, FindingInput

_titles = TypeAdapter(list[str])


async def create_finding(
    task_id: str,
    agent_id: str,
    finding: Finding,
    config: Settings,
    status: Status = Status.PENDING,
) -> FindingDB:
    """
    Store one finding for an agent through `POST /api/arbiter/tasks/{task_id}/findings`.

    Args:
        task_id: Task identifier
        agent_id: Agent identifier
        finding: The finding to store
        config: Application configuration, giving the endpoint and the shared API key
        status: Status of the finding (defaults to PENDING)

    Returns:
        The stored finding, with the id the backend gave it

    Raises:
        httpx.HTTPError: when the request fails or the backend answers with an error status,
            404 included for an unknown task
    """
    body = {**finding.model_dump(mode="json"), "agent_id": agent_id, "status": status.value}
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{task_url(config, task_id)}/findings", json=body, headers=auth_headers(config)
        )
        response.raise_for_status()
    stored: dict[str, Any] = response.json()
    # The id travels as a string; FindingDB holds the ObjectId get_findings reads back.
    stored["_id"] = ObjectId(stored.pop("_id", None) or stored.pop("id"))
    return FindingDB.model_validate(stored)


async def create_findings_batch(
    agent_id: str, input_data: FindingInput, config: Settings
) -> list[str]:
    """
    Store an agent's pending findings in one write through
    `POST /api/arbiter/tasks/{task_id}/findings/batch`.

    Args:
        agent_id: Agent identifier
        input_data: FindingInput containing task_id and a list of findings
        config: Application configuration, giving the endpoint and the shared API key

    Returns:
        The titles of the stored findings, in order

    Raises:
        httpx.HTTPError: when the request fails or the backend answers with an error status,
            404 for an unknown task and 400 past the backend's findings-per-batch cap
    """
    if not input_data.findings:
        return []
    body = {
        "agent_id": agent_id,
        "findings": [finding.model_dump(mode="json") for finding in input_data.findings],
    }
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{task_url(config, input_data.task_id)}/findings/batch",
            json=body,
            headers=auth_headers(config),
        )
        response.raise_for_status()
    return _titles.validate_python(response.json())


async def update_finding(
    task_id: str, finding_id: str, update_fields: dict[str, Any] | FindingDB, config: Settings
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
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.patch(
            f"{task_url(config, task_id)}/findings/{path_segment(finding_id)}",
            json=body,
            headers=auth_headers(config),
        )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


async def delete_agent_findings(task_id: str, agent_id: str, config: Settings) -> int:
    """
    Delete all of an agent's findings for a task through
    `DELETE /api/arbiter/tasks/{task_id}/agents/{agent_id}/findings`.
    Used when an agent makes a new submission to override the previous one.

    Args:
        task_id: Task identifier
        agent_id: Agent identifier
        config: Application configuration, giving the endpoint and the shared API key

    Returns:
        Number of findings deleted

    Raises:
        httpx.HTTPError: when the request fails or the backend answers with an error status,
            404 included for an unknown task
    """
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.delete(
            f"{task_url(config, task_id)}/agents/{path_segment(agent_id)}/findings",
            headers=auth_headers(config),
        )
        response.raise_for_status()
    return int(response.json()["deleted"])
