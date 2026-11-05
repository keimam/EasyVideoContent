"""Project Store and Manifest Persistence for Longform Video Generation.

Handles project directory creation, atomic manifest writes with Windows retry logic (K5),
file staging for local visual assets (K7), and single-process project locking with
stale lock takeover (K5).
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from loguru import logger

from app.services.longform.config import load_longform_config
from app.services.longform.hashing import hash_file
from app.services.longform.presets import get_preset
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest


class ProjectStoreError(Exception):
    """Base exception for project store operations."""
    pass


class ProjectLockedError(ProjectStoreError):
    """Raised when an operation cannot proceed because the project is locked by another active process."""
    pass


class ProjectNotFoundError(ProjectStoreError):
    """Raised when a requested project directory or manifest does not exist."""
    pass


def is_pid_alive(pid: int) -> bool:
    """Check whether a given PID is still running.

    Safe for Windows (uses Win32 OpenProcess/GetExitCodeProcess) and POSIX.
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


class ProjectLock:
    """Advisory file lock for longform project directory (K5).

    Creates a '.lock' file containing PID, hostname, and timestamp.
    If a lock already exists, verifies if the process is alive. Stale locks
    from deceased processes are automatically reclaimed.
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()
        self.lock_path = self.project_dir / ".lock"
        self._acquired = False

    def acquire(self) -> None:
        """Acquire the lock or raise ProjectLockedError."""
        self.project_dir.mkdir(parents=True, exist_ok=True)

        if self.lock_path.is_file():
            lock_info = self._read_lock_file()
            pid = lock_info.get("pid")
            created_at = lock_info.get("created_at", "unknown")

            if pid and isinstance(pid, int) and is_pid_alive(pid):
                raise ProjectLockedError(
                    f"Project '{self.project_dir.name}' is currently locked by PID {pid} "
                    f"(since {created_at}). Another active process is working on this project."
                )
            else:
                logger.warning(
                    f"Stale lock found on project '{self.project_dir.name}' (PID: {pid}). "
                    f"Process is dead; breaking and reclaiming lock."
                )
                try:
                    self.lock_path.unlink()
                except Exception as exc:
                    logger.warning(f"Could not remove stale lock file: {exc}")

        # Write lock file
        info = {
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
        }
        try:
            with open(self.lock_path, "w", encoding="utf-8") as fp:
                json.dump(info, fp, indent=2)
            self._acquired = True
        except Exception as exc:
            raise ProjectStoreError(f"Failed to create lock file at {self.lock_path}: {exc}") from exc

    def release(self) -> None:
        """Release the lock by deleting the .lock file."""
        if self._acquired and self.lock_path.is_file():
            try:
                # Only delete if it belongs to our PID
                lock_info = self._read_lock_file()
                if lock_info.get("pid") == os.getpid():
                    self.lock_path.unlink()
            except Exception as exc:
                logger.warning(f"Failed to release lock file '{self.lock_path}': {exc}")
            finally:
                self._acquired = False

    def _read_lock_file(self) -> dict[str, Any]:
        try:
            with open(self.lock_path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            return {}

    def __enter__(self) -> "ProjectLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


def get_projects_root() -> Path:
    """Return the absolute path to storage/longform_projects/."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return repo_root / "storage" / "longform_projects"


def get_project_dir(project_id: str) -> Path:
    """Return absolute path for a specific project ID."""
    return get_projects_root() / project_id


def save_manifest(manifest: ProjectManifest, project_dir: str | Path) -> Path:
    """Atomically save manifest.json with fsync and Windows retry logic (K5)."""
    p_dir = Path(project_dir).resolve()
    target_path = p_dir / "manifest.json"
    temp_path = p_dir / f"manifest.json.tmp.{uuid.uuid4().hex}"

    payload = manifest.model_dump_json(indent=2).encode("utf-8")

    # 1. Write to temporary file with flush & fsync
    try:
        with open(temp_path, "wb") as fp:
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise ProjectStoreError(f"Failed to write temporary manifest '{temp_path}': {exc}") from exc

    # 2. Atomic replacement with retries for Windows PermissionError
    config = load_longform_config()
    retries = max(1, config.atomic_retry_count)
    delay = max(0.01, config.atomic_retry_delay_seconds)

    for attempt in range(retries):
        try:
            os.replace(temp_path, target_path)
            return target_path
        except PermissionError as exc:
            if attempt == retries - 1:
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)
                raise ProjectStoreError(
                    f"Failed to atomically replace manifest at '{target_path}' "
                    f"after {retries} retries due to PermissionError: {exc}"
                ) from exc
            time.sleep(delay * (attempt + 1))
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise ProjectStoreError(f"Unexpected error replacing manifest '{target_path}': {exc}") from exc

    return target_path


def load_manifest(project_dir: str | Path) -> ProjectManifest:
    """Load and validate manifest.json from project directory.

    BOM-safe (utf-8-sig). Raises ProjectNotFoundError or ProjectStoreError.
    """
    p_dir = Path(project_dir).resolve()
    manifest_path = p_dir / "manifest.json"

    if not manifest_path.is_file():
        raise ProjectNotFoundError(f"Manifest file not found at '{manifest_path}'")

    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as fp:
            data = json.load(fp)
    except Exception as exc:
        raise ProjectStoreError(f"Failed to parse manifest JSON at '{manifest_path}': {exc}") from exc

    try:
        manifest = ProjectManifest.model_validate(data)
        return manifest
    except Exception as exc:
        raise ProjectStoreError(f"Manifest validation failed for '{manifest_path}': {exc}") from exc


def create_project(
    project_id: str,
    title: str,
    preset_id: str,
    script_markdown: Optional[str] = None,
    aspect_ratio: str = "16:9",
) -> Tuple[ProjectManifest, Path]:
    """Create a new longform project folder with directory structure and manifest.

    Copies preset snapshot into manifest (K2).
    """
    clean_id = project_id.strip()
    if not clean_id or not clean_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError(
            f"Invalid project_id '{project_id}'. Must contain alphanumeric, underscore, or hyphen."
        )

    project_dir = get_project_dir(clean_id)
    if project_dir.exists() and (project_dir / "manifest.json").exists():
        raise FileExistsError(f"Project '{clean_id}' already exists at {project_dir}")

    # Create directory tree
    subdirs = [
        project_dir / "assets" / "visual" / "raw",
        project_dir / "assets" / "visual" / "clips",
        project_dir / "assets" / "audio" / "groups",
        project_dir / "assets" / "chapters",
        project_dir / "output",
    ]
    for d in subdirs:
        d.mkdir(parents=True, exist_ok=True)

    # Fetch preset & snapshot
    preset = get_preset(preset_id)
    preset_snapshot = preset.model_dump()

    # Create chapters from script if provided
    chapters: list[Chapter] = []
    if script_markdown:
        from app.services.longform.script_import import parse_markdown_script
        chapters, _ = parse_markdown_script(script_markdown, preset)

    manifest = ProjectManifest(
        pipeline_version="1.0.0",
        schema_version="1.0.0",
        project_id=clean_id,
        title=title,
        aspect_ratio=aspect_ratio,
        preset_id=preset_id,
        preset_snapshot=preset_snapshot,
        chapters=chapters,
        status=ItemStatus.pending,
    )

    with ProjectLock(project_dir):
        save_manifest(manifest, project_dir)

    return manifest, project_dir


def stage_local_visual_file(project_dir: str | Path, source_path: str | Path) -> Tuple[str, str]:
    """Copy a local visual file into assets/visual/raw/ and return (relative_path, content_hash) (K7).

    Guarantees project portability and stable content hashing.
    """
    p_dir = Path(project_dir).resolve()
    src = Path(source_path).resolve()

    if not src.is_file():
        raise FileNotFoundError(f"Local visual source file not found: {src}")

    content_hash = hash_file(src)
    ext = src.suffix.lower()
    # Unique safe filename preserving extension
    safe_name = f"{content_hash[:16]}_{src.stem[:32]}{ext}"
    dest_dir = p_dir / "assets" / "visual" / "raw"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / safe_name

    # Only copy if destination does not already exist
    if not dest_file.exists():
        shutil.copy2(src, dest_file)

    rel_path = f"assets/visual/raw/{safe_name}"
    return rel_path, content_hash
