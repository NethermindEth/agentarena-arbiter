"""
Simplified cache test - tests both agent and task cache refresh functionality.
Clean, focused tests without over-engineering.
"""
import asyncio
import os
from types import SimpleNamespace
import httpx

import app.main as app_main
from app.types import TaskResponse

class MockConfig:
    """Mock configuration for testing cache functionality."""
    backend_agents_endpoint: str = "http://backend.test/agents"
    backend_task_details_endpoint: str = "http://backend.test/task-details"
    backend_task_repository_endpoint: str = "http://backend.test/repo"
    backend_api_key: str = "mock_key"
    task_id: str = "test-task"
    data_dir: str = "/tmp/cache_test_data"

# Test setup
os.makedirs(MockConfig.data_dir, exist_ok=True)

async def test_agents_cache_refresh():
    """Test 1: Agent cache refresh works and updates properly."""
    print("\n🧪 Test 1: Agent Cache Refresh")
    
    # Mock agent data for two refreshes
    mock_agents = [
        [{"agent_id": "agent1", "api_key": "key1"}],
        [{"agent_id": "agent2", "api_key": "key2"}]
    ]
    
    async def mock_get(self, url, headers=None):
        """Mock HTTP GET for agent data."""
        return SimpleNamespace(status_code=200, json=lambda: mock_agents.pop(0))
    
    # Save original and patch
    original_get = httpx.AsyncClient.get
    httpx.AsyncClient.get = mock_get
    
    try:
        # First refresh
        await app_main.set_agent_data(MockConfig)
        cache1 = app_main.agents_cache
        assert cache1[0]["agent_id"] == "agent1", "First refresh failed"
        print("  ✅ First refresh: loaded agent1")
        
        # Second refresh 
        await app_main.set_agent_data(MockConfig)
        cache2 = app_main.agents_cache
        assert cache2[0]["agent_id"] == "agent2", "Second refresh failed"
        print("  ✅ Second refresh: loaded agent2 (cache updated)")
        
    finally:
        httpx.AsyncClient.get = original_get

async def test_task_cache_refresh():
    """Test 2: Task cache refresh works and updates properly."""
    print("\n🧪 Test 2: Task Cache Refresh")
    
    # Mock task responses for two refreshes
    mock_tasks = [
        TaskResponse(
            id="1", taskId="test-task", title="Task 1", description="First task",
            status="open", selectedFiles=["file1.sol"], selectedDocs=[]
        ),
        TaskResponse(
            id="1", taskId="test-task", title="Task 1", description="First task", 
            status="open", selectedFiles=["file2.sol"], selectedDocs=[]
        )
    ]
    
    # Mock functions
    async def mock_fetch_task_details(url, cfg):
        return mock_tasks.pop(0)
    
    async def mock_download_repository(repo_url, cfg):
        dummy_repo = "/tmp/test_repo"
        temp_dir = "/tmp/test_temp"
        os.makedirs(dummy_repo, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)
        return dummy_repo, temp_dir
    
    def mock_read_files(repo_dir, selected_files):
        return ",".join(selected_files)
    
    def mock_copytree(src, dst, *args, **kwargs):
        os.makedirs(dst, exist_ok=True)
        return dst
    
    def mock_rmtree(path, *args, **kwargs):
        pass  # No-op for testing
    
    # Save originals and patch
    originals = {
        'fetch_task_details': app_main.fetch_task_details,
        'download_repository': app_main.download_repository, 
        'read_and_concatenate_files': app_main.read_and_concatenate_files,
        'copytree': app_main.shutil.copytree,
        'rmtree': app_main.shutil.rmtree
    }
    
    app_main.fetch_task_details = mock_fetch_task_details
    app_main.download_repository = mock_download_repository
    app_main.read_and_concatenate_files = mock_read_files
    app_main.shutil.copytree = mock_copytree
    app_main.shutil.rmtree = mock_rmtree
    
    try:
        # First refresh
        await app_main.set_task_cache(MockConfig)
        cache1 = app_main.task_cache
        assert cache1.selectedFilesContent == "file1.sol", "First task refresh failed"
        print("  ✅ First refresh: loaded file1.sol")
        
        # Second refresh
        await app_main.set_task_cache(MockConfig)
        cache2 = app_main.task_cache
        assert cache2.selectedFilesContent == "file2.sol", "Second task refresh failed"
        print("  ✅ Second refresh: loaded file2.sol (cache updated)")
        
    finally:
        # Restore all originals
        app_main.fetch_task_details = originals['fetch_task_details']
        app_main.download_repository = originals['download_repository']
        app_main.read_and_concatenate_files = originals['read_and_concatenate_files']
        app_main.shutil.copytree = originals['copytree']
        app_main.shutil.rmtree = originals['rmtree']

async def run_cache_tests():
    """Run all cache tests."""
    print("🚀 Starting Cache Tests")
    print("=" * 30)
    
    try:
        await test_agents_cache_refresh()
        await test_task_cache_refresh()
        
        print("\n" + "=" * 30)
        print("✅ All cache tests passed!")
        
    except Exception as e:
        print(f"\n❌ Cache test failed: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(run_cache_tests())