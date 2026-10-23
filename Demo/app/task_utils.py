import asyncio
import logging
import os
import shutil
import tempfile
import zipfile

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def remove_tree(path: str) -> None:
    """Remove a temporary tree, logging a file that resists instead of raising."""
    shutil.rmtree(path, onexc=lambda _fn, p, exc: logger.warning(f"Could not remove {p}: {exc}"))


def _extract_archive(content: bytes, temp_dir: str) -> str:
    """Write the downloaded ZIP into temp_dir, extract it and return the repository root."""
    zip_path = os.path.join(temp_dir, "repo.zip")
    with open(zip_path, "wb") as f:
        f.write(content)
    extract_dir = os.path.join(temp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_dir)
    # Most archives hold a single root directory; that directory is the repository root.
    contents = os.listdir(extract_dir)
    if len(contents) == 1 and os.path.isdir(os.path.join(extract_dir, contents[0])):
        return os.path.join(extract_dir, contents[0])
    return extract_dir


async def download_repository(repo_url: str, config: Settings) -> tuple[str | None, str | None]:
    """
    Download repository ZIP file and extract to a temporary directory.

    Args:
        repo_url: URL to download repository ZIP
        config: Application configuration

    Returns:
        Tuple of (repo_root_path, temp_dir_path) or (None, None) if all attempts fail
    """
    last_exception = None
    max_retries = 3

    # Retry loop for downloading the repository
    for attempt in range(max_retries):
        temp_dir = None
        try:
            logger.info(
                f"Downloading repository from {repo_url} (attempt {attempt + 1}/{max_retries})"
            )

            # Create a temporary directory
            temp_dir = tempfile.mkdtemp()

            # Download the ZIP file
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(repo_url, headers={"X-API-Key": config.backend_api_key})
                response.raise_for_status()

            # Writing and extracting the archive is file I/O, kept off the event loop.
            repo_root = await asyncio.to_thread(_extract_archive, response.content, temp_dir)
            logger.info(f"Successfully downloaded repository on attempt {attempt + 1}")
            return repo_root, temp_dir

        except Exception as e:
            last_exception = e
            logger.warning(f"Download attempt {attempt + 1} failed for {repo_url}: {str(e)}")

            # Clean up temp directory on failure
            if temp_dir:
                await asyncio.to_thread(remove_tree, temp_dir)

            # Wait before retry with exponential backoff
            wait_time = 2**attempt
            logger.info(f"Waiting {wait_time} seconds before retry...")
            await asyncio.sleep(wait_time)

    logger.error(
        f"Failed to download repository from {repo_url} after {max_retries} attempts. Last error: {last_exception}"
    )
    return None, None


def read_and_concatenate_files(repo_dir: str, selected_files: list) -> str:
    """
    Read and concatenate content of selected files from the repository.

    Args:
        repo_dir: Path to the repository directory
        selected_files: List of file paths to read

    Returns:
        String with all files concatenated with headers
    """
    concatenated = ""

    try:
        for file_path in selected_files:
            full_path = os.path.join(repo_dir, file_path)
            logger.info(f"Reading file: {full_path}")
            if os.path.isfile(full_path):
                try:
                    with open(full_path, encoding="utf-8") as f:
                        file_content = f.read()
                        concatenated += f"// {file_path}\n{file_content}\n\n"
                except UnicodeDecodeError:
                    # Try with different encoding if utf-8 fails
                    with open(full_path, encoding="latin-1") as f:
                        file_content = f.read()
                        concatenated += f"// {file_path}\n{file_content}\n\n"
            else:
                logger.warning(f"Selected file not found: {file_path}")

        return concatenated
    except Exception as e:
        logger.error(f"Error reading and concatenating files: {str(e)}", exc_info=True)
        return ""
