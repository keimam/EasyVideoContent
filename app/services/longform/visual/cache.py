"""Global media download cache for longform projects (4.1).

Stores downloaded stock videos and AI images in `storage/longform_cache/`
keyed by the SHA-256 hash of their remote URL to prevent redundant network traffic
and rate-limit exhaustion across multiple projects.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

from app.services.longform.visual.base import compute_file_content_hash


def get_global_cache_dir(repo_root: Optional[Path] = None) -> Path:
    """Return the resolved path to storage/longform_cache/."""
    root = repo_root or Path(__file__).resolve().parent.parent.parent.parent.parent
    cache_dir = root / "storage" / "longform_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


class GlobalMediaCache:
    """Manages cross-project cached media files with atomic writes and backoff retries."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or get_global_cache_dir()

    def get_cache_path_for_url(self, url: str) -> Path:
        """Derive cached local path based on URL hash and original extension."""
        url_hash = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        ext = os.path.splitext(path)[1].lower()
        if not ext or len(ext) > 5:
            ext = ".mp4" if "video" in url.lower() else ".jpg"
        return self.cache_dir / f"cache_{url_hash[:24]}{ext}"

    def is_cached(self, url: str) -> bool:
        """Check if URL exists in cache with positive file size."""
        cached = self.get_cache_path_for_url(url)
        return cached.is_file() and cached.stat().st_size > 0

    def download_or_get(
        self,
        url: str,
        dest_path: Path,
        max_retries: int = 3,
        backoff_seconds: float = 1.5,
        timeout: int = 60,
    ) -> Path:
        """Fetch media file using global cache, downloading with exponential backoff on cache miss."""
        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        cached = self.get_cache_path_for_url(url)

        # Cache Hit
        if cached.is_file() and cached.stat().st_size > 0:
            logger.debug(f"Global cache hit for URL: {url[:60]}... -> {cached.name}")
            shutil.copyfile(cached, dest)
            return dest

        # Cache Miss: Download with retries
        logger.info(f"Downloading media to global cache: {url[:80]}...")
        tmp_cached = self.cache_dir / f"{cached.name}.tmp.{uuid.uuid4().hex}"
        last_error = ""

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        for attempt in range(1, max_retries + 1):
            try:
                with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
                    resp.raise_for_status()
                    with open(tmp_cached, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            if chunk:
                                f.write(chunk)

                if tmp_cached.is_file() and tmp_cached.stat().st_size > 0:
                    os.replace(tmp_cached, cached)
                    shutil.copyfile(cached, dest)
                    return dest
                else:
                    raise IOError("Downloaded file is empty (0 bytes)")

            except Exception as exc:
                if tmp_cached.exists():
                    tmp_cached.unlink(missing_ok=True)
                last_error = str(exc)
                logger.warning(f"Download attempt {attempt}/{max_retries} failed for {url[:60]}: {last_error}")
                if attempt < max_retries:
                    time.sleep(backoff_seconds * (2 ** (attempt - 1)))

        raise RuntimeError(f"Failed to download media after {max_retries} attempts: {last_error}")
