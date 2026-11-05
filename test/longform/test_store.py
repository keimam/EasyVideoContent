"""Tests for project storage, atomic writes, project locking, BOM handling, script updating, and snapshot immutability (A2, A4, B2, B3)."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.services.longform.hashing import compute_chapter_hash
from app.services.longform.planner import compute_plan
from app.services.longform.presets import get_preset
from app.services.longform.schema import Chapter, EncoderSettings, ItemStatus, ProjectManifest, Segment, TtsGroup, VisualSpec
from app.services.longform.store import (
    ProjectLock,
    ProjectLockedError,
    ProjectNotFoundError,
    ProjectStoreError,
    create_project,
    is_pid_alive,
    load_manifest,
    refresh_project_preset,
    save_manifest,
    stage_local_visual_file,
    update_project_script,
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

    lock1.release()
    lock2.acquire()
    lock2.release()


def test_project_lock_stale_lock_takeover(tmp_path: Path):
    """Verify that a lock from a dead PID is automatically taken over (K5)."""
    project_dir = tmp_path / "stale_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    lock_file = project_dir / ".lock"
    dead_pid = 99999999
    assert not is_pid_alive(dead_pid)

    with open(lock_file, "w", encoding="utf-8") as fp:
        json.dump({"pid": dead_pid, "created_at": "2020-01-01T00:00:00Z"}, fp)

    lock = ProjectLock(project_dir)
    lock.acquire()

    with open(lock_file, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    assert data["pid"] == os.getpid()

    lock.release()
    assert not lock_file.exists()


def test_stage_local_visual_file(tmp_path: Path):
    """Verify staging copies local visual assets to assets/visual/raw/ and returns content hash (K7)."""
    project_dir = tmp_path / "stage_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    source_img = tmp_path / "user_input.jpg"
    source_content = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01test_image_data"
    with open(source_img, "wb") as fp:
        fp.write(source_content)

    rel_path, content_hash = stage_local_visual_file(project_dir, source_img)

    assert rel_path.startswith("assets/visual/raw/")
    dest_path = project_dir / rel_path
    assert dest_path.is_file()
    assert dest_path.read_bytes() == source_content

    rel_path2, hash2 = stage_local_visual_file(project_dir, source_img)
    assert rel_path2 == rel_path
    assert hash2 == content_hash


def test_load_manifest_bom_and_syntax_error_reporting(tmp_path: Path):
    """Verify BOM handling, JSON syntax error with line/col, and file preservation (A2)."""
    proj_dir = tmp_path / "bom_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = proj_dir / "manifest.json"

    # 1. BOM-safe read
    manifest_data = {
        "pipeline_version": "1.0.0",
        "schema_version": "1.0.0",
        "project_id": "bom_proj",
        "title": "BOM Manifest",
        "preset_id": "documentary_en",
        "preset_snapshot": {"name": "Test"},
    }
    with open(manifest_file, "w", encoding="utf-8-sig") as fp:
        json.dump(manifest_data, fp)

    loaded = load_manifest(proj_dir)
    assert loaded.project_id == "bom_proj"

    # 2. Corrupt JSON syntax (missing closing bracket)
    broken_content = '{\n  "project_id": "broken",\n  "title": \n}'
    with open(manifest_file, "w", encoding="utf-8") as fp:
        fp.write(broken_content)

    with pytest.raises(ProjectStoreError) as exc_info:
        load_manifest(proj_dir)

    err_str = str(exc_info.value)
    assert "Invalid JSON syntax" in err_str
    assert "line 4" in err_str or "line 3" in err_str  # reports exact line
    assert "manifest.json" in err_str

    # Ensure broken file on disk was NOT overwritten or deleted
    assert manifest_file.read_text(encoding="utf-8") == broken_content


def test_update_project_script_edit_add_remove(tmp_path: Path):
    """Verify update_project_script retains unchanged items and marks diffs (A4)."""
    initial_script = """# Title

## Chapter 1: Arrival
Sentence one of group one. Sentence two of group one.

## Chapter 2: Conflict
Sentence one of group two.

## Chapter 3: Departure
Sentence one of group three.
"""
    manifest, proj_dir = create_project(
        project_id="update_proj",
        title="Title",
        preset_id="documentary_en",
        script_markdown=initial_script,
        projects_root=tmp_path,
    )

    # Simulate completed assets for chapter 1 and chapter 2
    for ch in manifest.chapters:
        for g in ch.tts_groups:
            g.audio.file_path = f"assets/audio/groups/ch{ch.chapter_index}_g{g.group_index}.mp3"
            g.status = ItemStatus.completed
            for s in g.segments:
                s.visual.rendered_clip = f"assets/visual/clips/ch{ch.chapter_index}_s{s.segment_index}.mp4"
                s.status = ItemStatus.completed
        ch.output_file = f"assets/chapters/ch_{ch.chapter_index}.mp4"
        ch.status = ItemStatus.completed
    save_manifest(manifest, proj_dir)

    # Edit script:
    # - In Ch 1: Edit sentence 1 of group 1
    # - In Ch 2: Add a second paragraph (group 2)
    # - Ch 3: Removed entirely!
    new_script = """# Title

## Chapter 1: Arrival
Sentence one MODIFIED. Sentence two of group one.

## Chapter 2: Conflict
Sentence one of group two.

New second paragraph in chapter two.

"""
    stats = update_project_script(proj_dir, new_script)

    assert stats["chapters_unchanged"] == 0
    assert stats["chapters_modified"] == 2  # Ch 1 and Ch 2
    assert stats["chapters_removed"] == 1  # Ch 3 removed
    assert stats["groups_modified"] == 1  # Ch 1 group 1
    assert stats["groups_added"] == 1  # Ch 2 group 2
    assert stats["segments_modified"] == 1  # sentence 1

    updated_manifest = load_manifest(proj_dir)
    assert len(updated_manifest.chapters) == 2  # Ch 3 is gone

    # Ch 1 Seg 2 is unchanged -> completed preserved
    ch1_g1 = updated_manifest.chapters[0].tts_groups[0]
    assert ch1_g1.segments[1].status == ItemStatus.completed
    # Ch 1 Seg 1 was modified -> stale
    assert ch1_g1.segments[0].status == ItemStatus.stale
    # Ch 1 Group 1 was modified -> stale
    assert ch1_g1.status == ItemStatus.stale

    # Ch 2 Group 1 was unchanged -> completed preserved
    ch2_g1 = updated_manifest.chapters[1].tts_groups[0]
    assert ch2_g1.status == ItemStatus.completed
    # Ch 2 Group 2 is new -> pending
    ch2_g2 = updated_manifest.chapters[1].tts_groups[1]
    assert ch2_g2.status == ItemStatus.pending


def test_preset_snapshot_immutability_and_refresh(tmp_path: Path):
    """Verify manifest preserves snapshot even if preset changes, unless refresh_project_preset is called (B2)."""
    manifest, proj_dir = create_project(
        project_id="snap_proj_test",
        title="Snapshot Test",
        preset_id="automotive.muscle_car",
        sub_niche_focus="1960s drag racing",
        projects_root=tmp_path,
    )
    assert manifest.preset_inheritance_chain == ["automotive", "automotive.muscle_car"]
    assert manifest.sub_niche_focus == "1960s drag racing"
    orig_wpm = manifest.preset_snapshot["words_per_minute"]

    # Refresh preset
    refreshed_manifest, details = refresh_project_preset(proj_dir)
    assert details["preset_id"] == "automotive.muscle_car"
    assert refreshed_manifest.preset_inheritance_chain == ["automotive", "automotive.muscle_car"]


def test_sub_niche_focus_alters_chapter_hash():
    """Verify sub_niche_focus participates in chapter hash calculation (B3)."""
    encoder = EncoderSettings()
    subtitle = {"font_size": 18}
    preset_snap = {"name": "Test"}
    ch = Chapter(chapter_index=1, chapter_title="Ch1", tts_groups=[])

    hash_no_focus = compute_chapter_hash(ch, encoder, subtitle, preset_snap, sub_niche_focus="")
    hash_with_focus = compute_chapter_hash(ch, encoder, subtitle, preset_snap, sub_niche_focus="1960s drag racing")

    assert hash_no_focus != hash_with_focus
