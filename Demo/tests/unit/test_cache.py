"""
Unit tests for task data functionality.
Tests individual components related to task processing and TESTTASK caching.
"""
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from app.types import TaskCache


@pytest.mark.asyncio
class TestFetchTaskData:
    """Unit tests for fetch_task_data functionality."""

    @patch('app.main.mongodb')
    async def test_fetch_task_data_task_not_found(self, mock_mongodb):
        """Test fetch_task_data when task is not found in database."""
        from app import main as app_main
        
        mock_mongodb.get_task = AsyncMock(return_value=None)
        
        result = await app_main.fetch_task_data("nonexistent-task")
        
        assert result is None
        mock_mongodb.get_task.assert_called_once_with("nonexistent-task")

    @patch('app.main.mongodb')
    async def test_fetch_task_data_no_selected_files(self, mock_mongodb, sample_task):
        """Test fetch_task_data when task has no selected files."""
        from app import main as app_main
        
        # Modify sample task to have no files
        sample_task.selectedFiles = []
        
        mock_mongodb.get_task = AsyncMock(return_value=sample_task)
            
        result = await app_main.fetch_task_data("TEST123")
        
        assert result is None

    @patch('app.main.download_repository')
    @patch('app.main.mongodb')
    @patch('app.main.config')
    async def test_fetch_task_data_download_failure(self, mock_config, mock_mongodb, mock_download, sample_task):
        """Test fetch_task_data when repository download fails."""
        from app import main as app_main
        import tempfile
        
        with tempfile.TemporaryDirectory() as temp_data_dir:
            mock_config.backend_task_repository_endpoint = "http://test.com/repo"
            mock_config.data_dir = temp_data_dir
                
            mock_mongodb.get_task = AsyncMock(return_value=sample_task)
            mock_download.return_value = (None, None)  # Download failure
            
            result = await app_main.fetch_task_data("TEST123")
            
            assert result is None

    @patch('app.main.download_repository')
    @patch('app.main.mongodb')
    async def test_testtask_cache_hit(self, mock_mongodb, mock_download, sample_task):
        """Test that TESTTASK uses cache when commitSha matches."""
        from app import main as app_main
        
        # Setup existing cache
        cached_task_cache = TaskCache(
            taskId="TESTTASK",
            startTime=datetime.now(timezone.utc),
            deadline=datetime.now(timezone.utc),
            selectedFilesContent="cached content",
            selectedDocsContent="",
            additionalLinks=[],
            additionalDocs=None,
            qaResponses=[]
        )
        
        app_main.test_task_cache = {
            "commitSha": "abc123def456",
            "task_cache": cached_task_cache,
            "cached_at": datetime.now(timezone.utc)
        }
        
        sample_task.taskId = "TESTTASK"
        sample_task.commitSha = "abc123def456"  # Same as cached
        
        mock_mongodb.get_task = AsyncMock(return_value=sample_task)

        result = await app_main.fetch_task_data("TESTTASK")
        
        # Should return cached data without downloading
        assert result is cached_task_cache
        mock_download.assert_not_called()

    @patch('app.main.download_repository')
    @patch('app.main.mongodb')
    @patch('app.main.config')
    async def test_testtask_cache_miss(self, mock_config, mock_mongodb, mock_download, sample_task):
        """Test that TESTTASK re-downloads when commitSha changes."""
        from app import main as app_main
        import tempfile
        
        # Setup existing cache with different commitSha
        app_main.test_task_cache = {
            "commitSha": "old123commit456",
            "task_cache": TaskCache(
                taskId="TESTTASK",
                startTime=datetime.now(timezone.utc),
                deadline=datetime.now(timezone.utc),
                selectedFilesContent="old content",
                selectedDocsContent="",
                additionalLinks=[],
                additionalDocs=None,
                qaResponses=[]
            ),
            "cached_at": datetime.now(timezone.utc)
        }
        
        sample_task.taskId = "TESTTASK"
        sample_task.commitSha = "new789commit012"  # Different from cached
        
        # Use real temp directories for both config.data_dir and mock download paths
        with tempfile.TemporaryDirectory() as temp_data_dir, \
             tempfile.TemporaryDirectory() as temp_repo_dir, \
             tempfile.TemporaryDirectory() as temp_download_dir:
            
            # Create a mock file structure in the temp repo directory
            import os
            contracts_dir = os.path.join(temp_repo_dir, "contracts")
            os.makedirs(contracts_dir, exist_ok=True)
            with open(os.path.join(contracts_dir, "Vault.sol"), "w") as f:
                f.write("contract Vault { function withdraw() public {} }")
            
            # Setup mocks - only mock what needs mocking
            mock_config.backend_task_repository_endpoint = "http://test.com/repo"
            mock_config.data_dir = temp_data_dir
            mock_download.return_value = (temp_repo_dir, temp_download_dir)  # Real temp paths
            
            mock_mongodb.get_task = AsyncMock(return_value=sample_task)
             
            result = await app_main.fetch_task_data("TESTTASK")
            
            # Should have downloaded and updated cache
            assert result is not None
            assert app_main.test_task_cache["commitSha"] == "new789commit012"
            
            # Verify download was called (cache miss)
            mock_download.assert_called_once()
