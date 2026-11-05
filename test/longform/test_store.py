"""Tests for project storage, atomic writes, project locking, and file staging."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.services.longform.presets import get_preset
from app.services.longform.schema import ItemStatus, ProjectManifest
from app.services.longform.store import (
    ProjectLock,
    ProjectLockedError,
    ProjectNotFoundError,
    ProjectStoreError,
    create_project,
    is_pid_alive,
    load_manifest,
    save_manifest,
    stage_local_visual_file,
)


def test_atomic_manifest_save_and_load(tmp_path: Path):
    """Verify that save_manifest writes atomically and load_manifest parses accurately."""
    project_dir = tmp_path / "proj_1"
    project_dir.mkdir(parents=True, exist_ok=True)

    manifest = ProjectManifest(
        project_id="proj_1",
        title="Atomic Test",
        preset_id="documentary_en",
        preset_snapshot={"name": "Preset Test"},
        status=ItemStatus.pending,
    )

    saved_path = save_manifest(manifest, project_dir)
    assert saved_path.is_file()
    assert saved_path.name == "manifest.json"

    # Ensure no lingering temporary files
    tmp_files = list(project_dir.glob("manifest.json.tmp.*"))
    assert len(tmp_files) == 0

    loaded = load_manifest(project_dir)
    assert loaded.project_id == "proj_1"
    assert loaded.title == "Atomic Test"


def test_save_manifest_retries_on_permission_error(monkeypatch, tmp_path: Path):
    """Verify atomic write retries when os.replace raises transient PermissionError on Windows."""
    project_dir = tmp_path / "proj_retry"
    project_dir.mkdir(parents=True, exist_ok=True)

    manifest = ProjectManifest(
        project_id="proj_retry",
        title="Retry Test",
        preset_id="documentary_en",
        preset_snapshot={},
    )

    original_replace = os.replace
    calls = []

    def flaky_replace(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("Access is denied (transient lock)")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    save_manifest(manifest, project_dir)
    assert len(calls) == 3
    assert (project_dir / "manifest.json").is_file()


def test_project_lock_mutual_exclusion(tmp_path: Path):
    """Verify that acquiring an already-held lock raises ProjectLockedError."""
    project_dir = tmp_path / "locked_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    lock1 = ProjectLock(project_dir)
    lock1.acquire()

    lock2 = ProjectLock(project_dir)
    with pytest.raises(ProjectLockedError) as exc_info:
        lock2.acquire()

    assert "currently locked by PID" in str(exc_info.value)

    # Release lock1, then lock2 should succeed
    lock1.release()
    lock2.acquire()
    lock2.release()


def test_project_lock_stale_lock_takeover(tmp_path: Path):
    """Verify that a lock from a dead PID is automatically taken over (K5)."""
    project_dir = tmp_path / "stale_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    lock_file = project_dir / ".lock"
    # Write a lock file with non-existent PID
    dead_pid = 99999999
    assert not is_pid_alive(dead_pid)

    with open(lock_file, "w", encoding="utf-8") as fp:
        json.dump({"pid": dead_pid, "created_at": "2020-01-01T00:00:00Z"}, fp)

    # New lock should detect stale lock and take over
    lock = ProjectLock(project_dir)
    lock.acquire()

    # Verify lock file now contains current process PID
    with open(lock_file, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    assert data["pid"] == os.getpid()

    lock.release()
    assert not lock_file.exists()


def test_stage_local_visual_file(tmp_path: Path):
    """Verify staging copies local visual assets to assets/visual/raw/ and returns content hash (K7)."""
    project_dir = tmp_path / "stage_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy source image
    source_img = tmp_path / "user_input.jpg"
    source_content = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01test_image_data"
    with open(source_img, "wb") as fp:
        fp.write(source_content)

    rel_path, content_hash = stage_local_visual_file(project_dir, source_img)

    assert rel_path.startswith("assets/visual/raw/")
    dest_path = project_dir / rel_path
    assert dest_path.is_file()
    assert dest_path.read_bytes() == source_content

    # Calling stage again on same file should be idempotent and fast
    rel_path2, hash2 = stage_local_visual_file(project_dir, source_img)
    assert rel_path2 == rel_path
    assert hash2 == content_hash
