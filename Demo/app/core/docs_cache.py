"""
File-based caching for link content.
Simple JSON-based cache stored in local filesystem.
"""
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional
import aiofiles

logger = logging.getLogger(__name__)


class DocsCache:
    """File-based cache for link content summaries."""
    
    def __init__(self, cache_dir: str = "docs_cache"):
        """
        Initialize the cache.
        
        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "link_cache.json"
        self._cache: dict[str, str] = {}
        self._load_cache()
    
    def _load_cache(self):
        """Load cache from disk."""
        try:
            if self.cache_file.exists():
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
                logger.info(f"Loaded {len(self._cache)} cached links")
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            self._cache = {}
    
    async def _save_cache(self):
        """Save cache to disk."""
        try:
            async with aiofiles.open(self.cache_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(self._cache, indent=2))
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    
    def _get_cache_key(self, url: str) -> str:
        """Generate cache key from URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    
    def check_if_visited(self, url: str) -> bool:
        """Check if URL is in cache."""
        key = self._get_cache_key(url)
        return key in self._cache
    
    async def fetch_link_content(self, url: str) -> Optional[str]:
        """Fetch cached content for URL."""
        key = self._get_cache_key(url)
        return self._cache.get(key)
    
    async def add_content(self, url: str, content: str):
        """Add content to cache."""
        key = self._get_cache_key(url)
        self._cache[key] = content
        await self._save_cache()
        logger.info(f"Cached content for URL: {url[:50]}...")

