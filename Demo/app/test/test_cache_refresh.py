import asyncio
from types import SimpleNamespace
import httpx
import importlib

# Import the module so we can access the live global variable
import app.main as app_main

# ---------------------------------------------------------------------------
# Dummy configuration object with only the fields needed by set_agent_data
# ---------------------------------------------------------------------------

class DummyConfig:
    """Light-weight stand-in for app.config.Settings."""

    backend_agents_endpoint: str = "http://backend.test/agents"
    backend_api_key: str = "mock_key"


# Re-export function for convenience
set_agent_data = app_main.set_task_cache.__globals__.get('set_agent_data', None) or app_main.set_agent_data

# Helper to access the current agents_cache lazily
def current_agents_cache():
    return app_main.agents_cache


async def _run_test():
    """Run the cache-refresh test with two different mocked responses."""

    # Prepare two different agent lists to simulate a change in backend data
    responses = [
        [{"api_key": "key1", "agent_id": "agent1"}],
        [{"api_key": "key2", "agent_id": "agent2"}],
    ]

    async def mock_get(self, url, headers=None):
        """Return the next response from the pre-defined list."""
        data = responses.pop(0)
        return SimpleNamespace(status_code=200, json=lambda: data)

    # Monkey-patch httpx.AsyncClient.get so set_agent_data() uses our mock
    original_get = httpx.AsyncClient.get
    httpx.AsyncClient.get = mock_get

    try:
        # First refresh – should load agent1
        await set_agent_data(DummyConfig)
        cache1 = current_agents_cache()
        assert cache1 and cache1[0]["agent_id"] == "agent1", "First refresh failed"
        print("✅ First refresh loaded agent1 as expected")

        # Second refresh – should overwrite with agent2
        await set_agent_data(DummyConfig)
        cache2 = current_agents_cache()
        assert cache2 and cache2[0]["agent_id"] == "agent2", "Second refresh failed"
        print("✅ Second refresh loaded agent2 as expected (cache updated)")

        print("🎉 agents_cache refresh logic works as intended")
    finally:
        # Always restore the original method to avoid side effects
        httpx.AsyncClient.get = original_get


if __name__ == "__main__":
    asyncio.run(_run_test()) 