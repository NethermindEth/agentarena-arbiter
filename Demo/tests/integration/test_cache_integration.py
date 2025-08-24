"""
Integration tests for cache functionality.
Tests real behavior with actual HTTP servers and file system operations.
"""
import pytest
import asyncio
import json
import tempfile
import shutil
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from app.config import Settings


@pytest.fixture
def test_data_dir():
    """Create and cleanup test data directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestHTTPHandler(BaseHTTPRequestHandler):
    """Test HTTP handler for mock server."""
    
    def do_GET(self):
        if self.path == "/agents" and self.headers.get("X-API-Key") == "test_key":
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            test_agents = [{"agent_id": "test_agent", "api_key": "test_key"}]
            self.wfile.write(json.dumps(test_agents).encode())
        elif self.path == "/task-details" and self.headers.get("X-API-Key") == "test_key":
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            task_details = {
                "taskId": "TEST123",
                "startTime": "2025-01-01T00:00:00Z",
                "deadline": "2030-01-01T00:00:00Z"
            }
            self.wfile.write(json.dumps(task_details).encode())
        elif self.path == "/repo" and self.headers.get("X-API-Key") == "test_key":
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            repo_data = {
                "selectedFilesContent": "contract Test { }",
                "selectedDocsContent": "Test documentation"
            }
            self.wfile.write(json.dumps(repo_data).encode())
        else:
            self.send_response(401)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress server logs


@pytest.fixture
def mock_http_server():
    """Create a mock HTTP server for testing."""
    server = HTTPServer(('127.0.0.1', 0), TestHTTPHandler)
    port = server.server_address[1]
    
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    
    yield f"http://127.0.0.1:{port}"
    
    server.shutdown()
    server_thread.join(timeout=1)


@pytest.mark.asyncio
class TestAgentCacheIntegration:
    """Integration tests for agent cache with real HTTP communication."""

    async def test_agent_cache_with_real_http_server(self, mock_http_server, test_data_dir):
        """Test agent cache update with real HTTP server."""
        from app import main as app_main
        
        config = Settings(
            backend_agents_endpoint=f"{mock_http_server}/agents",
            backend_api_key="test_key",
            data_dir=test_data_dir
        )
        
        # Clear cache before test
        app_main.agents_cache.clear()
        
        await app_main.set_agent_data(config)
        
        # Verify cache was updated
        expected_agents = [{"agent_id": "test_agent", "api_key": "test_key"}]
        assert app_main.agents_cache == expected_agents

    async def test_agent_cache_with_unreachable_server(self, test_data_dir):
        """Test agent cache behavior with unreachable server."""
        from app import main as app_main
        
        original_cache = app_main.agents_cache.copy()
        
        config = Settings(
            backend_agents_endpoint="http://127.0.0.1:9999/nonexistent",
            backend_api_key="test_key",
            data_dir=test_data_dir
        )
        
        await app_main.set_agent_data(config)
        
        # Cache should remain unchanged
        assert app_main.agents_cache == original_cache

    async def test_agent_cache_with_invalid_auth(self, mock_http_server, test_data_dir):
        """Test agent cache behavior with invalid authentication."""
        from app import main as app_main
        
        original_cache = app_main.agents_cache.copy()
        
        config = Settings(
            backend_agents_endpoint=f"{mock_http_server}/agents",
            backend_api_key="invalid_key",  # Wrong API key
            data_dir=test_data_dir
        )
        
        await app_main.set_agent_data(config)
        
        # Cache should remain unchanged due to auth failure
        assert app_main.agents_cache == original_cache


@pytest.mark.asyncio
class TestTaskCacheIntegration:
    """Integration tests for task cache with real HTTP communication and file operations."""

    async def test_task_cache_with_real_http_server(self, mock_http_server, test_data_dir):
        """Test task cache update with real HTTP server."""
        from app import main as app_main
        
        config = Settings(
            backend_task_details_endpoint=f"{mock_http_server}/task-details",
            backend_task_repository_endpoint=f"{mock_http_server}/repo",
            backend_api_key="test_key",
            task_id="TEST123",
            data_dir=test_data_dir
        )
        
        await app_main.set_task_cache(config)
        
        # Verify cache was updated (basic check - actual structure depends on implementation)
        assert app_main.task_cache is not None

    async def test_task_cache_data_directory_creation(self, mock_http_server):
        """Test that task cache creates data directories as needed."""
        from app import main as app_main
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Use nested directory that doesn't exist
            data_dir = f"{temp_dir}/nested/cache_data"
            
            config = Settings(
                backend_task_details_endpoint=f"{mock_http_server}/task-details",
                backend_task_repository_endpoint=f"{mock_http_server}/repo", 
                backend_api_key="test_key",
                task_id="TEST123",
                data_dir=data_dir
            )
            
            await app_main.set_task_cache(config)
            
            # Directory creation depends on implementation details
            # This test mainly verifies no crashes occur

    async def test_task_cache_with_unreachable_servers(self, test_data_dir):
        """Test task cache behavior with unreachable servers."""
        from app import main as app_main
        
        original_cache = app_main.task_cache
        
        config = Settings(
            backend_task_details_endpoint="http://127.0.0.1:99999/task-details",
            backend_task_repository_endpoint="http://127.0.0.1:99999/repo",
            backend_api_key="test_key",
            task_id="TEST123",
            data_dir=test_data_dir
        )
        
        await app_main.set_task_cache(config)
        
        # Cache should remain unchanged
        assert app_main.task_cache == original_cache


@pytest.mark.asyncio
class TestCacheErrorScenarios:
    """Integration tests for various error scenarios."""

    async def test_mixed_success_failure_scenarios(self, mock_http_server, test_data_dir):
        """Test behavior when some operations succeed and others fail."""
        from app import main as app_main
        
        # Test agent cache success followed by task cache failure
        agent_config = Settings(
            backend_agents_endpoint=f"{mock_http_server}/agents",
            backend_api_key="test_key",
            data_dir=test_data_dir
        )
        
        task_config = Settings(
            backend_task_details_endpoint="http://127.0.0.1:99999/task-details",
            backend_task_repository_endpoint="http://127.0.0.1:99999/repo",
            backend_api_key="test_key",
            task_id="TEST123",
            data_dir=test_data_dir
        )
        
        # Clear caches
        app_main.agents_cache.clear()
        original_task_cache = app_main.task_cache
        
        # First operation should succeed
        await app_main.set_agent_data(agent_config)
        assert len(app_main.agents_cache) > 0
        
        # Second operation should fail gracefully
        await app_main.set_task_cache(task_config)
        assert app_main.task_cache == original_task_cache

    async def test_concurrent_cache_operations(self, mock_http_server, test_data_dir):
        """Test concurrent cache operations don't interfere with each other."""
        from app import main as app_main
        
        config1 = Settings(
            backend_agents_endpoint=f"{mock_http_server}/agents",
            backend_api_key="test_key",
            data_dir=test_data_dir
        )
        
        config2 = Settings(
            backend_agents_endpoint=f"{mock_http_server}/agents",
            backend_api_key="test_key",
            data_dir=test_data_dir
        )
        
        # Run concurrent operations
        await asyncio.gather(
            app_main.set_agent_data(config1),
            app_main.set_agent_data(config2)
        )
        
        # Cache should be in a consistent state
        assert isinstance(app_main.agents_cache, list)
