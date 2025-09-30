"""
Unit tests for task processing functions.
Tests scheduling and processing logic without external dependencies.
"""
import pytest
from unittest.mock import AsyncMock, patch, Mock
from datetime import datetime, timezone


@pytest.mark.asyncio
class TestScheduleTaskProcessing:
    """Test the schedule_task_processing function."""
    
    @patch('app.main.scheduler')
    async def test_schedule_task_processing_success(self, mock_scheduler):
        """Test successful task processing scheduling."""
        from app.main import schedule_task_processing
        
        task_id = "test-task-123"
        start_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        deadline = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        
        # Mock scheduler methods
        mock_scheduler.get_job.return_value = None  # No existing job
        mock_scheduler.add_job.return_value = None
        
        await schedule_task_processing(task_id, start_time, deadline)
        
        # Verify scheduler was called correctly
        mock_scheduler.get_job.assert_called_once_with("task_test-task-123")
        mock_scheduler.add_job.assert_called_once()
    
    @patch('app.main.scheduler')
    @patch('app.main.logger')
    async def test_schedule_task_processing_invalid_timing(self, mock_logger, mock_scheduler):
        """Test scheduling with invalid timing (start >= deadline)."""
        from app.main import schedule_task_processing
        
        task_id = "test-task-invalid"
        start_time = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        deadline = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # Before start time
        
        await schedule_task_processing(task_id, start_time, deadline)
        
        # Should log error and return early
        mock_logger.error.assert_called_once()
        
        # Should not call scheduler
        mock_scheduler.add_job.assert_not_called()
    
    @patch('app.main.scheduler')
    async def test_schedule_task_processing_removes_existing_job(self, mock_scheduler):
        """Test scheduling removes existing job first."""
        from app.main import schedule_task_processing
        
        task_id = "test-task-existing"
        start_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        deadline = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        
        # Mock existing job
        mock_existing_job = Mock()
        mock_scheduler.get_job.return_value = mock_existing_job
        mock_scheduler.remove_job.return_value = None
        mock_scheduler.add_job.return_value = None
        
        await schedule_task_processing(task_id, start_time, deadline)
        
        # Should remove existing job
        mock_scheduler.remove_job.assert_called_once_with("task_test-task-existing")
    
    @patch('app.main.scheduler')
    @patch('app.main.logger')
    async def test_schedule_task_processing_exception_handling(self, mock_logger, mock_scheduler):
        """Test exception handling in scheduling."""
        from app.main import schedule_task_processing
        
        task_id = "test-task-error"
        start_time = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        deadline = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        
        # Mock scheduler to raise exception
        mock_scheduler.get_job.side_effect = Exception("Scheduler error")
        
        await schedule_task_processing(task_id, start_time, deadline)
        
        # Should log error
        mock_logger.error.assert_called_once()


@pytest.mark.asyncio
class TestProcessTaskScheduled:
    """Test the process_task_scheduled function."""
    
    @patch('app.main.process_task')
    @patch('app.main.mongodb')
    async def test_process_task_scheduled_success(self, mock_mongodb, mock_process_task):
        """Test successful scheduled task processing."""
        from app.main import process_task_scheduled
        
        task_id = "test-scheduled-task"
        
        # Mock async database methods properly
        mock_mongodb.get_metadata = AsyncMock(return_value=None)
        mock_mongodb.set_metadata = AsyncMock(return_value=True)
        mock_process_task.return_value = None
        
        await process_task_scheduled(task_id)
        
        # Should check for processed metadata
        mock_mongodb.get_metadata.assert_called_once_with("task_test-scheduled-task")
        
        # Should process the task
        mock_process_task.assert_called_once_with(task_id)
        
        # Should mark as processed
        mock_mongodb.set_metadata.assert_called_once()
        set_call = mock_mongodb.set_metadata.call_args
        assert set_call[0][0] == "task_test-scheduled-task"
        assert "processed_at" in set_call[0][1]
        assert set_call[0][1]["scheduled_processing"] == True
    
    @patch('app.main.process_task')
    @patch('app.main.mongodb')
    async def test_process_task_scheduled_already_processed(self, mock_mongodb, mock_process_task):
        """Test scheduled processing when task already processed."""
        from app.main import process_task_scheduled
        
        task_id = "test-already-processed"
        
        # Mock existing processed metadata
        mock_mongodb.get_metadata = AsyncMock(return_value={
            "processed_at": "2025-01-01T00:00:00Z",
            "scheduled_processing": True
        })
        
        await process_task_scheduled(task_id)
        
        # Should check metadata and return early
        mock_mongodb.get_metadata.assert_called_once_with("task_test-already-processed")

        # Should not process the task
        mock_process_task.assert_not_called()
        mock_mongodb.set_metadata.assert_not_called()
    
    @patch('app.main.process_task')
    @patch('app.main.mongodb')
    @patch('app.main.logger')
    async def test_process_task_scheduled_exception_handling(self, mock_logger, mock_mongodb, mock_process_task):
        """Test exception handling in scheduled processing."""
        from app.main import process_task_scheduled
        
        task_id = "test-error-processing"
        
        # Mock async database methods and set process_task to raise exception
        mock_mongodb.get_metadata = AsyncMock(return_value=None)
        mock_process_task.side_effect = Exception("Processing error")
        
        await process_task_scheduled(task_id)
        
        # Should log error
        mock_logger.error.assert_called_once()
