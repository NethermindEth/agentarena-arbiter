"""
Integration tests for cache functionality.
Tests real behavior with actual HTTP servers and file system operations.
"""
import pytest
import asyncio
import json
import tempfile
import io
import zipfile
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
        elif self.path == "/tasks/submitted" and self.headers.get("X-API-Key") == "test_key":
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            task_list = [{
                "id": "abc123",
                "taskId": "TEST123",
                "projectRepo": "https://example.com/repo.git",
                "title": "Test Task",
                "description": "A task for integration testing",
                "bounty": None,
                "status": "Open",
                "startTime": "1735689600",
                "deadline": "1893456000",
                "selectedBranch": "main",
                "selectedFiles": ["contracts/Vault.sol"],
                "selectedDocs": [],
                "additionalLinks": [],
                "additionalDocs": None,
                "qaResponses": []
            }]
            self.wfile.write(json.dumps(task_list).encode())
        elif self.path.startswith("/repo/") and self.headers.get("X-API-Key") == "test_key":
            # Serve a real ZIP containing selected files so the app can extract and read them
            # Extract task_id from the path (e.g., /repo/TEST123 -> TEST123)
            task_id = self.path.split("/repo/")[1]
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
                # Create a proper repository structure with a root directory
                # This mimics how real repository ZIPs are structured (e.g., from GitHub)
                zf.writestr("repo-main/contracts/Vault.sol", "contract Vault { function withdraw() public {} }")
            zip_bytes = buffer.getvalue()
            self.send_response(200)
            self.send_header('Content-type', 'application/zip')
            self.send_header('Content-Length', str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)
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
            backend_submitted_tasks_endpoint=f"{mock_http_server}/tasks/submitted",
            backend_task_repository_endpoint=f"{mock_http_server}/repo",
            backend_api_key="test_key",
            data_dir=test_data_dir
        )
        
        await app_main.set_task_caches(config)
        
        print(app_main.task_cache_map)

        # Verify cache map was updated
        assert "TEST123" in app_main.task_cache_map

    async def test_task_cache_data_directory_creation(self, mock_http_server):
        """Test that task cache creates data directories as needed."""
        import os
        from app import main as app_main
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Use nested directory that doesn't exist
            data_dir = f"{temp_dir}/nested/cache_data"
            
            # Verify the directory doesn't exist initially
            assert not os.path.exists(data_dir)
            
            config = Settings(
                backend_submitted_tasks_endpoint=f"{mock_http_server}/tasks/submitted",
                backend_task_repository_endpoint=f"{mock_http_server}/repo", 
                backend_api_key="test_key",
                data_dir=data_dir
            )
            
            await app_main.set_task_caches(config)
            
            # Verify the data directory was created
            assert os.path.exists(data_dir)
            assert os.path.isdir(data_dir)
            
            # Verify task repository was stored
            repo_dir = os.path.join(data_dir, "repo_TEST123")
            assert os.path.exists(repo_dir)
            assert os.path.isdir(repo_dir)
            
            # Verify the task was added to cache map
            assert "TEST123" in app_main.task_cache_map
            
            # Verify the selected file exists in the repository
            contracts_file = os.path.join(repo_dir, "contracts", "Vault.sol")
            assert os.path.exists(contracts_file)
            
            # Verify the file content matches what we put in the mock ZIP
            with open(contracts_file, 'r') as f:
                content = f.read()
                assert "contract Vault { function withdraw() public {} }" in content

    async def test_task_cache_with_unreachable_servers(self, test_data_dir):
        """Test task cache behavior with unreachable servers."""
        from app import main as app_main
        
        original_cache = app_main.task_cache_map.copy()
        
        config = Settings(
            backend_submitted_tasks_endpoint="http://127.0.0.1:99999/tasks/submitted",
            backend_task_repository_endpoint="http://127.0.0.1:99999/repo",
            backend_api_key="test_key",
            data_dir=test_data_dir
        )
        
        await app_main.set_task_caches(config)
        
        # Cache should remain unchanged
        assert app_main.task_cache_map == original_cache


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
            backend_submitted_tasks_endpoint="http://127.0.0.1:99999/tasks/submitted",
            backend_task_repository_endpoint="http://127.0.0.1:99999/repo",
            backend_api_key="test_key",
            data_dir=test_data_dir
        )
        
        # Clear caches
        app_main.agents_cache.clear()
        original_task_cache = app_main.task_cache_map.copy()
        
        # First operation should succeed
        await app_main.set_agent_data(agent_config)
        assert len(app_main.agents_cache) > 0
        
        # Second operation should fail gracefully
        await app_main.set_task_caches(task_config)
        assert app_main.task_cache_map == original_task_cache

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
