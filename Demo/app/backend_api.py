"""Request pieces shared by every call to the AgentArena backend's arbiter API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx

from app.config import Settings

TIMEOUT_SECONDS = 20.0


class TaskNotFoundError(LookupError):
    """The backend answered 404 for a task id it does not know."""


def new_client() -> httpx.AsyncClient:
    """
    Return a client for the backend, to share across the calls of one task's processing.

    Judging a task writes once per finding in each pass; one client keeps those writes on pooled
    connections instead of opening a connection per call. The caller closes it.
    """
    return httpx.AsyncClient(timeout=TIMEOUT_SECONDS)


@asynccontextmanager
async def client_or_new(client: httpx.AsyncClient | None) -> AsyncIterator[httpx.AsyncClient]:
    """Yield the given client, or a new one closed on exit when the caller has none."""
    if client is not None:
        yield client
        return
    async with new_client() as created:
        yield created


def auth_headers(config: Settings) -> dict[str, str]:
    """Return the headers that authenticate the arbiter with the shared backend key."""
    return {"X-API-Key": config.backend_api_key}


def path_segment(value: str) -> str:
    """Escape a value for one URL path segment, so it cannot add segments or a query."""
    return quote(value, safe="")


def task_url(config: Settings, task_id: str) -> str:
    """Return the URL of one task under the backend's `/api/arbiter/tasks` endpoint."""
    return f"{config.backend_tasks_endpoint}/{path_segment(task_id)}"
