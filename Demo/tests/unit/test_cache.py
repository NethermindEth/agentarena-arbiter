"""
Unit tests for cache functionality.
Tests individual cache components in isolation with proper mocking.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch
import tempfile
import os

from app.config import Settings


@pytest.fixture
def valid_agent_config():
    """Valid agent configuration for testing."""
    return Settings(
        backend_agents_endpoint="http://test.example.com/agents",
        backend_api_key="test_key",
        data_dir="/tmp/test_cache_data"
    )


@pytest.fixture
def invalid_agent_config():
    """Invalid agent configuration (missing endpoint)."""
    return Settings(
        backend_agents_endpoint="",
        backend_api_key="test_key",
        data_dir="/tmp/test_cache_data"
    )


@pytest.fixture
def valid_task_config():
    """Valid task configuration for testing."""
    return Settings(
        backend_submitted_tasks_endpoint="http://test.example.com/tasks/submitted",
        backend_task_repository_endpoint="http://test.example.com/repo",
        backend_api_key="test_key",
        data_dir="/tmp/test_cache_data"
    )


@pytest.fixture
def invalid_task_config():
    """Invalid task configuration (missing endpoints)."""
    return Settings(
        backend_submitted_tasks_endpoint="",
        backend_task_repository_endpoint="",
        backend_api_key="",
        data_dir="/tmp/test_cache_data"
    )


@pytest.mark.asyncio
class TestAgentCacheUnit:
    """Unit tests for agent cache functionality."""

    @patch('app.main.agents_cache', [])
    async def test_agent_cache_invalid_config_handling(self, invalid_agent_config):
        """Test that invalid config doesn't update agent cache."""
        from app import main as app_main
        
        original_cache = app_main.agents_cache.copy()
        
        with patch('httpx.AsyncClient') as mock_client:
            await app_main.set_agent_data(invalid_agent_config)
            
            # HTTP client should not be called with invalid config
            mock_client.assert_not_called()
            assert app_main.agents_cache == original_cache

    @patch('app.main.agents_cache', [])
    async def test_agent_cache_http_error_handling(self, valid_agent_config):
        """Test that HTTP errors don't update agent cache."""
        from app import main as app_main
        
        original_cache = app_main.agents_cache.copy()
        
        with patch('httpx.AsyncClient') as mock_client:
            # Mock HTTP client to raise an exception
            mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("Network error")
            
            await app_main.set_agent_data(valid_agent_config)
            
            # Cache should remain unchanged on error
            assert app_main.agents_cache == original_cache

    @patch('app.main.agents_cache', [])
    async def test_agent_cache_successful_update(self, valid_agent_config):
        """Test successful agent cache update with mocked HTTP response."""
        from app import main as app_main
        
        test_agents = [{"agent_id": "test_agent", "api_key": "test_key"}]
        
        with patch('httpx.AsyncClient') as mock_client:
            # Mock successful HTTP response
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = test_agents
            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response
            
            await app_main.set_agent_data(valid_agent_config)
            
            # Cache should be updated
            assert app_main.agents_cache == test_agents


@pytest.mark.asyncio
class TestTaskCacheUnit:
    """Unit tests for task cache functionality."""

    @patch('app.main.task_cache_map', {})
    async def test_task_cache_invalid_config_handling(self, invalid_task_config):
        """Test that invalid config doesn't update task cache."""
        from app import main as app_main
        
        original_cache = app_main.task_cache_map.copy()
        
        with patch('httpx.AsyncClient') as mock_client:
            await app_main.set_task_caches(invalid_task_config)
            
            # HTTP client should not be called with invalid config
            mock_client.assert_not_called()
            assert app_main.task_cache_map == original_cache

    @patch('app.main.task_cache_map', {})
    async def test_task_cache_http_error_handling(self, valid_task_config):
        """Test that HTTP errors don't update task cache."""
        from app import main as app_main
        
        original_cache = app_main.task_cache_map.copy()
        
        with patch('httpx.AsyncClient') as mock_client:
            # Mock HTTP client to raise an exception
            mock_client.return_value.__aenter__.return_value.get.side_effect = Exception("Network error")
            
            await app_main.set_task_caches(valid_task_config)
            
            # Cache should remain unchanged on error
            assert app_main.task_cache_map == original_cache

    async def test_testtask_special_handling(self):
        """Test that TESTTASK doesn't trigger job scheduling."""
        from app import main as app_main
        
        testtask_config = Settings(
            backend_submitted_tasks_endpoint="http://test.example.com/tasks/submitted",
            backend_task_repository_endpoint="http://test.example.com/repo",
            backend_api_key="test_key",
            data_dir="/tmp/test_cache_data"
        )
        
        with patch('httpx.AsyncClient') as mock_client, \
             patch.object(app_main.scheduler, 'add_job') as mock_add_job:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = [{
                "taskId": "TESTTASK",
                "startTime": "1735689600",
                "deadline": "1893456000",
                "selectedFiles": ["contracts/Vault.sol"],
                "selectedDocs": [],
                "additionalLinks": [],
                "additionalDocs": None,
                "qaResponses": []
            }]
            mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

            await app_main.set_task_caches(testtask_config)

            # No jobs should be scheduled for TESTTASK
            mock_add_job.assert_not_called()

    @pytest.mark.unit
    def test_data_directory_path_creation(self):
        """Test data directory path handling."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = os.path.join(temp_dir, "nested", "cache_data")
            
            config = Settings(
                data_dir=data_dir,
                backend_submitted_tasks_endpoint="http://test.example.com/tasks/submitted",
                backend_task_repository_endpoint="http://test.example.com/repo",
                backend_api_key="test_key"
            )
            
            # Test that config accepts the path (actual directory creation is tested in integration)
            assert config.data_dir == data_dir
