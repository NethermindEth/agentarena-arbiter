import asyncio
import os
from types import SimpleNamespace

from app.types import TaskResponse
import app.main as app_main

# ---------------------------------------------------------------------------
# Dummy configuration with required attributes for set_task_cache
# ---------------------------------------------------------------------------

class DummyConfig:
    backend_task_details_endpoint: str = "http://backend.test/task-details"
    backend_task_repository_endpoint: str = "http://backend.test/repo"
    backend_api_key: str = "mock_key"
    task_id: str = "dummy-task"
    data_dir: str = "/tmp/task_data_test"

# Ensure data_dir exists
os.makedirs(DummyConfig.data_dir, exist_ok=True)

# Reference to function under test
set_task_cache = app_main.set_task_cache


async def _run_test():
    """Verify that repeated set_task_cache calls update the global task_cache."""

    # Prepare two different TaskResponse objects to simulate backend changes
    responses = [
        TaskResponse(
            id="1",
            taskId="dummy-task",
            title="t1",
            description="d1",
            status="open",
            selectedFiles=["file1.sol"],
            selectedDocs=[],
        ),
        TaskResponse(
            id="1",
            taskId="dummy-task",
            title="t1",
            description="d1",
            status="open",
            selectedFiles=["file2.sol"],
            selectedDocs=[],
        ),
    ]

    # Monkey patches ---------------------------------------------------------
    async def mock_fetch_task_details(url, cfg):
        return responses.pop(0)

    async def mock_download_repository(repo_url, cfg):
        # Return a small dummy directory to avoid copying system folders
        dummy_repo = "/tmp/dummy_repo_test"
        os.makedirs(dummy_repo, exist_ok=True)
        # create a dummy file so directory is non-empty
        open(os.path.join(dummy_repo, "placeholder.txt"), "w").close()
        return dummy_repo

    def mock_read_and_concatenate_files(repo_dir, selected_files):
        # Return unique content depending on selected_files
        return ",".join(selected_files)

    # Patch shutil copytree/rmtree to no-op to speed up tests and avoid FS issues
    def mock_copytree(src, dst, *args, **kwargs):
        os.makedirs(dst, exist_ok=True)
        return dst

    def mock_rmtree(path, *args, **kwargs):
        # Remove directory if it exists to keep things clean
        if os.path.exists(path):
            try:
                for root, dirs, files in os.walk(path, topdown=False):
                    for name in files:
                        os.remove(os.path.join(root, name))
                    for name in dirs:
                        os.rmdir(os.path.join(root, name))
                os.rmdir(path)
            except Exception:
                pass

    # Save originals
    original_fetch = app_main.fetch_task_details
    original_download = app_main.download_repository
    original_read = app_main.read_and_concatenate_files
    original_copytree = app_main.shutil.copytree
    original_rmtree = app_main.shutil.rmtree

    # Apply patches
    app_main.fetch_task_details = mock_fetch_task_details
    app_main.download_repository = mock_download_repository
    app_main.read_and_concatenate_files = mock_read_and_concatenate_files
    app_main.shutil.copytree = mock_copytree
    app_main.shutil.rmtree = mock_rmtree

    try:
        # First refresh
        await set_task_cache(DummyConfig)
        cache1 = app_main.task_cache
        assert cache1.selectedFilesContent == "file1.sol", "First task_cache refresh failed"
        print("✅ First task_cache refresh loaded content from file1.sol")

        # Second refresh
        await set_task_cache(DummyConfig)
        cache2 = app_main.task_cache
        assert cache2.selectedFilesContent == "file2.sol", "Second task_cache refresh failed"
        print("✅ Second task_cache refresh loaded content from file2.sol (cache updated)")

        print("🎉 task_cache refresh logic works as intended")
    finally:
        # Restore originals to avoid side effects
        app_main.fetch_task_details = original_fetch
        app_main.download_repository = original_download
        app_main.read_and_concatenate_files = original_read
        app_main.shutil.copytree = original_copytree
        app_main.shutil.rmtree = original_rmtree


if __name__ == "__main__":
    asyncio.run(_run_test()) 