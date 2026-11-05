"""
Finding writes through the AgentArena backend's arbiter API.

The backend stores agents' submissions itself, in the collections `MongoDBHandler.get_findings`
reads; the arbiter writes back only its verdicts, through `update_finding`.
"""

from typing import Any

import httpx
from pydantic_core import to_jsonable_python

from app.backend_api import TIMEOUT_SECONDS, auth_headers, path_segment, task_url
from app.config import Settings
from app.models.finding_db import FindingDB


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
