"""Request pieces shared by every call to the AgentArena backend's arbiter API."""

from urllib.parse import quote

from app.config import Settings

TIMEOUT_SECONDS = 20.0


def auth_headers(config: Settings) -> dict[str, str]:
    """Return the headers that authenticate the arbiter with the shared backend key."""
    return {"X-API-Key": config.backend_api_key}


def path_segment(value: str) -> str:
    """Escape a value for one URL path segment, so it cannot add segments or a query."""
    return quote(value, safe="")


def task_url(config: Settings, task_id: str) -> str:
    """Return the URL of one task under the backend's `/api/arbiter/tasks` endpoint."""
    return f"{config.backend_tasks_endpoint}/{path_segment(task_id)}"
