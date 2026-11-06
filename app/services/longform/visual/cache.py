"""Global media download cache and LRU lifecycle management for longform projects (4.1, S2).

Stores downloaded stock videos and AI images in `storage/longform_cache/`
keyed by the SHA-256 hash of their remote URL to prevent redundant network traffic
and rate-limit exhaustion across multiple projects.

Stage 4.2 (S2):
- Global cache size limit configured in `config/longform.toml` (default 10 GB).
- Automatic LRU eviction when cache exceeds max size.
- CLI commands: `cache status` and `cache prune [--older-than 30d] [--max-size 5GB]`.
- Files referenced by locked shots across all projects are strictly protected from deletion.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from loguru import logger

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore

from app.services.longform.visual.base import compute_file_content_hash


def get_repo_root() -> Path:
    """Return repository root path."""
    return Path(__file__).resolve().parent.parent.parent.parent.parent


def get_global_cache_dir(repo_root: Optional[Path] = None) -> Path:
    """Return the resolved path to storage/longform_cache/."""
    root = repo_root or get_repo_root()
    cache_dir = root / "storage" / "longform_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@dataclass
class CacheConfig:
    """Configuration loaded from config/longform.toml."""
    max_size_gb: float = 10.0
    max_age_days: int = 30

    @property
    def max_size_bytes(self) -> int:
        return int(self.max_size_gb * (1024 ** 3))


def load_cache_config(config_path: Optional[Path] = None) -> CacheConfig:
    """Load cache configuration from config/longform.toml, falling back to defaults."""
    cfg_file = config_path or (get_repo_root() / "config" / "longform.toml")
    if not cfg_file.is_file():
        return CacheConfig()

    try:
        with open(cfg_file, "rb") as fp:
            data = tomllib.load(fp)
        cache_data = data.get("cache", {})
        return CacheConfig(
            max_size_gb=float(cache_data.get("max_size_gb", 10.0)),
            max_age_days=int(cache_data.get("max_age_days", 30)),
        )
    except Exception as exc:
        logger.warning(f"Failed to read cache config from {cfg_file}: {exc}; using defaults")
        return CacheConfig()


def get_locked_asset_references(projects_dir: Optional[Path] = None) -> Set[str]:
    """Scan all projects in storage/longform_projects/ to find all assets referenced by locked shots.

    Returns a set of identifiers:
    - sha256 content hashes of locked shot assets
    - URL hash prefixes (first 24 chars) of locked shot candidates
    - Filenames of locked assets
    """
    p_dir = projects_dir or (get_repo_root() / "storage" / "longform_projects")
    locked_refs: Set[str] = set()

    if not p_dir.is_dir():
        return locked_refs

    for proj in p_dir.iterdir():
        if not proj.is_dir():
            continue
        manifest_file = proj / "manifest.json"
        if not manifest_file.is_file():
            continue

        try:
            with open(manifest_file, "r", encoding="utf-8-sig") as fp:
                data = json.load(fp)
            chapters = data.get("chapters", [])
            for ch in chapters:
                for s in ch.get("shots", []):
                    if s.get("locked"):
                        # Asset file name and stem
                        if s.get("asset_path"):
                            p = Path(s["asset_path"])
                            locked_refs.add(p.name)
                            locked_refs.add(p.stem)
                        # Candidate URL hashes (matches cache_<hash> filenames)
                        for c in s.get("candidates", []):
                            u = c.get("url")
                            if u:
                                uhash = hashlib.sha256(u.strip().encode("utf-8")).hexdigest()[:24]
                                locked_refs.add(uhash)
                                locked_refs.add(f"cache_{uhash}")
        except Exception as exc:
            logger.debug(f"Could not read manifest {manifest_file} during locked asset scan: {exc}")

    return locked_refs


def is_file_locked(file_path: Path, locked_refs: Set[str]) -> bool:
    """Check if a cached file matches any locked reference by URL hash or filename."""
    name = file_path.name
    stem = file_path.stem
    for ref in locked_refs:
        if ref and (ref in name or ref in stem):
            return True
    return False


def get_cache_status(cache_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Inspect global media cache and compute usage statistics and locked items."""
    cdir = cache_dir or get_global_cache_dir()
    cfg = load_cache_config()
    locked_refs = get_locked_asset_references()

    total_files = 0
    total_bytes = 0
    locked_files_count = 0
    oldest_ts: Optional[float] = None
    newest_ts: Optional[float] = None

    if cdir.is_dir():
        for entry in cdir.iterdir():
            if not entry.is_file():
                continue
            try:
                st = entry.stat()
                total_files += 1
                total_bytes += st.st_size

                atime = st.st_atime
                if oldest_ts is None or atime < oldest_ts:
                    oldest_ts = atime
                if newest_ts is None or atime > newest_ts:
                    newest_ts = atime

                if is_file_locked(entry, locked_refs):
                    locked_files_count += 1
            except Exception:
                continue

    total_mb = round(total_bytes / (1024 * 1024), 2)
    total_gb = round(total_bytes / (1024 ** 3), 3)

    return {
        "cache_dir": str(cdir),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_mb": total_mb,
        "total_gb": total_gb,
        "max_size_gb": cfg.max_size_gb,
        "max_age_days": cfg.max_age_days,
        "locked_files_count": locked_files_count,
        "oldest_timestamp": oldest_ts,
        "newest_timestamp": newest_ts,
    }


def prune_cache(
    max_size_bytes: Optional[int] = None,
    older_than_days: Optional[float] = None,
    cache_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Prune global media cache using LRU order and age limits, protecting locked shots (S2).

    Never deletes files referenced by locked shots across all projects.
    """
    cdir = cache_dir or get_global_cache_dir()
    cfg = load_cache_config()
    target_max_bytes = max_size_bytes if max_size_bytes is not None else cfg.max_size_bytes
    locked_refs = get_locked_asset_references()

    now = time.time()
    files_list: List[Dict[str, Any]] = []
    total_bytes = 0
    locked_count = 0

    if not cdir.is_dir():
        return {
            "files_removed": 0,
            "bytes_freed": 0,
            "mb_freed": 0.0,
            "files_remaining": 0,
            "total_size_mb": 0.0,
            "locked_files_skipped": 0,
        }

    for entry in cdir.iterdir():
        if not entry.is_file():
            continue
        try:
            st = entry.stat()
            locked = is_file_locked(entry, locked_refs)
            if locked:
                locked_count += 1
            files_list.append({
                "path": entry,
                "size": st.st_size,
                "atime": st.st_atime,
                "locked": locked,
            })
            total_bytes += st.st_size
        except Exception:
            continue

    files_removed = 0
    bytes_freed = 0

    # 1. Age-based eviction (if older_than_days specified)
    if older_than_days is not None and older_than_days > 0:
        cutoff_sec = older_than_days * 86400.0
        remaining_files = []
        for item in files_list:
            age = now - item["atime"]
            if not item["locked"] and age >= cutoff_sec:
                try:
                    item["path"].unlink(missing_ok=True)
                    files_removed += 1
                    bytes_freed += item["size"]
                    total_bytes -= item["size"]
                    logger.debug(f"Evicted aged cache file: {item['path'].name} (age: {age/86400.0:.1f}d)")
                except Exception as exc:
                    logger.warning(f"Failed to delete {item['path']}: {exc}")
                    remaining_files.append(item)
            else:
                remaining_files.append(item)
        files_list = remaining_files

    # 2. LRU size-based eviction
    if total_bytes > target_max_bytes:
        # Sort unlocked files by last access time ascending (oldest accessed first)
        unlocked = [f for f in files_list if not f["locked"]]
        unlocked.sort(key=lambda x: x["atime"])

        for item in unlocked:
            if total_bytes <= target_max_bytes:
                break
            try:
                item["path"].unlink(missing_ok=True)
                files_removed += 1
                bytes_freed += item["size"]
                total_bytes -= item["size"]
                logger.debug(f"Evicted LRU cache file: {item['path'].name}")
            except Exception as exc:
                logger.warning(f"Failed to delete {item['path']}: {exc}")

    files_remaining = max(len(files_list) - files_removed, 0)
    mb_freed = round(bytes_freed / (1024 * 1024), 2)
    total_size_mb = round(total_bytes / (1024 * 1024), 2)

    return {
        "files_removed": files_removed,
        "bytes_freed": bytes_freed,
        "mb_freed": mb_freed,
        "files_remaining": files_remaining,
        "total_size_mb": total_size_mb,
        "locked_files_skipped": locked_count,
    }


class GlobalMediaCache:
    """Manages cross-project cached media files with atomic writes, LRU tracking, and auto-eviction."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or get_global_cache_dir()
        self.config = load_cache_config()

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
        """Fetch media file using global cache, downloading with exponential backoff on cache miss.

        Touches file access time on hit (LRU maintenance) and triggers auto-eviction if cache exceeds limit.
        """
        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        cached = self.get_cache_path_for_url(url)

        # Cache Hit
        if cached.is_file() and cached.stat().st_size > 0:
            logger.debug(f"Global cache hit for URL: {url[:60]}... -> {cached.name}")
            # Update access time for LRU tracking
            try:
                os.utime(cached, None)
            except Exception:
                pass
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
                    try:
                        os.utime(cached, None)
                    except Exception:
                        pass

                    # Automatic LRU eviction check after adding new file (S2)
                    self._check_and_auto_evict()

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

    def _check_and_auto_evict(self) -> None:
        """Check if global cache exceeds configured max_size_bytes and evict oldest non-locked items."""
        try:
            status = get_cache_status(self.cache_dir)
            if status["total_bytes"] > self.config.max_size_bytes:
                logger.info(
                    f"Global cache size ({status['total_mb']} MB) exceeds limit ({self.config.max_size_gb} GB). "
                    "Running automatic LRU eviction..."
                )
                res = prune_cache(
                    max_size_bytes=self.config.max_size_bytes,
                    cache_dir=self.cache_dir,
                )
                logger.info(f"Auto-evicted {res['files_removed']} files, freed {res['mb_freed']} MB.")
        except Exception as exc:
            logger.warning(f"Cache auto-eviction failed: {exc}")
