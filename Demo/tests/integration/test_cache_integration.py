"""
Integration tests for task data functionality.
Tests real behavior with actual HTTP servers and file system operations.
"""
import pytest
import tempfile
import io
import zipfile
import shutil
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import patch, AsyncMock

from app.types import Task, TaskCache


@pytest.fixture
def test_data_dir():
    """Create and cleanup test data directory."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


class MockHTTPHandler(BaseHTTPRequestHandler):
    """Test HTTP handler for mock server."""
    
    def do_GET(self):
        if self.path.startswith("/repo/") and self.headers.get("X-API-Key") == "test_key":
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
    server = HTTPServer(('127.0.0.1', 0), MockHTTPHandler)
    port = server.server_address[1]
    
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    
    yield f"http://127.0.0.1:{port}"
    
    server.shutdown()
    server_thread.join(timeout=1)


@pytest.mark.asyncio 
class TestFetchTaskDataIntegration:
    """Integration tests for fetch_task_data with real HTTP communication and file operations."""

    async def test_fetch_task_data_with_real_http_server(self, mock_http_server, test_data_dir):
        """Test fetch_task_data with real HTTP server."""
        from app import main as app_main
        from tests.conftest import create_sample_task
        
        sample_task = create_sample_task(
            task_id="TEST123",
            description="A task for integration testing"
        )
        
        with patch('app.main.config') as mock_config, \
             patch('app.main.mongodb') as mock_mongodb:
            
            mock_config.backend_task_repository_endpoint = f"{mock_http_server}/repo"
            mock_config.backend_api_key = "test_key"
            mock_config.data_dir = test_data_dir
            
            mock_mongodb.get_task = AsyncMock(return_value=sample_task)
            
            result = await app_main.fetch_task_data("TEST123")
            
            # Verify task cache was created successfully
            assert result is not None
            assert isinstance(result, TaskCache)
            assert result.taskId == "TEST123"
            assert result.selectedFilesContent
            assert "contract Vault { function withdraw() public {} }" in result.selectedFilesContent

    async def test_fetch_task_data_directory_creation(self, mock_http_server):
        """Test that fetch_task_data creates data directories as needed."""
        import os
        from app import main as app_main
        from tests.conftest import create_sample_task
        
        sample_task = create_sample_task(
            task_id="TEST123",
            description="A task for integration testing"
        )
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Use nested directory that doesn't exist
            data_dir = f"{temp_dir}/nested/cache_data"
            
            # Verify the directory doesn't exist initially
            assert not os.path.exists(data_dir)
            
            with patch('app.main.config') as mock_config, \
                 patch('app.main.mongodb') as mock_mongodb:
                
                mock_config.backend_task_repository_endpoint = f"{mock_http_server}/repo"
                mock_config.backend_api_key = "test_key"
                mock_config.data_dir = data_dir
                
                mock_mongodb.get_task = AsyncMock(return_value=sample_task)
                
                result = await app_main.fetch_task_data("TEST123")
                
                # Verify the data directory was created
                assert os.path.exists(data_dir)
                assert os.path.isdir(data_dir)
                
                # Verify task repository was stored
                repo_dir = os.path.join(data_dir, "repo_TEST123")
                assert os.path.exists(repo_dir)
                assert os.path.isdir(repo_dir)
                
                # Verify the selected file exists in the repository
                contracts_file = os.path.join(repo_dir, "contracts", "Vault.sol")
                assert os.path.exists(contracts_file)
                
                # Verify the file content matches what we put in the mock ZIP
                with open(contracts_file, 'r') as f:
                    content = f.read()
                    assert "contract Vault { function withdraw() public {} }" in content
                
                # Verify result contains processed content
                assert result is not None
                assert result.selectedFilesContent
                assert "contract Vault { function withdraw() public {} }" in result.selectedFilesContent



@pytest.mark.asyncio
class TestTESSTASKCacheIntegration:
    """Integration tests for TESTTASK caching behavior."""

    async def test_testtask_cache_hit_integration(self, mock_http_server, test_data_dir):
        """Test TESTTASK cache hit with real HTTP server."""
        from app import main as app_main
        
        testtask = Task(
            taskId="TESTTASK",
            projectRepo="https://example.com/repo.git",
            title="Test Task",
            description="A task for testing cache",
            bounty=None,
            status="Open",
            startTime="1735689600", 
            deadline="1893456000",
            selectedBranch="main",
            selectedFiles=["contracts/Vault.sol"],
            selectedDocs=[],
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[],
            commitSha="abc123def456"
        )
        
        with patch('app.main.config') as mock_config, \
             patch('app.main.mongodb') as mock_mongodb:
            
            mock_config.backend_task_repository_endpoint = f"{mock_http_server}/repo"
            mock_config.backend_api_key = "test_key"
            mock_config.data_dir = test_data_dir
            
            mock_mongodb.get_task = AsyncMock(return_value=testtask)
            
            # First call - should download and cache
            result1 = await app_main.fetch_task_data("TESTTASK")
            assert result1 is not None
            assert app_main.test_task_cache is not None
            assert app_main.test_task_cache["commitSha"] == "abc123def456"
            
            # Second call with same commitSha - should use cache
            result2 = await app_main.fetch_task_data("TESTTASK")
            assert result2 is result1  # Should be the same object from cache

    async def test_testtask_cache_miss_integration(self, mock_http_server, test_data_dir):
        """Test TESTTASK cache miss when commitSha changes."""
        from app import main as app_main
        
        testtask_v1 = Task(
            taskId="TESTTASK",
            projectRepo="https://example.com/repo.git",
            title="Test Task V1",
            description="A task for testing cache v1",
            bounty=None,
            status="Open", 
            startTime="1735689600",
            deadline="1893456000",
            selectedBranch="main",
            selectedFiles=["contracts/Vault.sol"],
            selectedDocs=[],
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[],
            commitSha="old123commit456"
        )
        
        testtask_v2 = Task(
            taskId="TESTTASK",
            projectRepo="https://example.com/repo.git",
            title="Test Task V2",
            description="A task for testing cache v2",
            bounty=None,
            status="Open",
            startTime="1735689600",
            deadline="1893456000",
            selectedBranch="main",
            selectedFiles=["contracts/Vault.sol"],
            selectedDocs=[],
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[],
            commitSha="new789commit012"
        )
        
        with patch('app.main.config') as mock_config, \
             patch('app.main.mongodb') as mock_mongodb:
            
            mock_config.backend_task_repository_endpoint = f"{mock_http_server}/repo"
            mock_config.backend_api_key = "test_key"
            mock_config.data_dir = test_data_dir
            
            # First call with old commitSha
            mock_mongodb.get_task = AsyncMock(return_value=testtask_v1)
            result1 = await app_main.fetch_task_data("TESTTASK")
            assert result1 is not None
            assert app_main.test_task_cache["commitSha"] == "old123commit456"
            
            # Second call with new commitSha - should re-download
            mock_mongodb.get_task = AsyncMock(return_value=testtask_v2)
            result2 = await app_main.fetch_task_data("TESTTASK")
            assert result2 is not None
            assert result2 is not result1  # Should be different objects
            assert app_main.test_task_cache["commitSha"] == "new789commit012"
