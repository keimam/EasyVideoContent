"""Tests for ExecutionPlanner, missing/0-byte file detection, and resume state."""

from pathlib import Path
import pytest

from app.services.longform.hashing import (
    compute_audio_group_hash,
    compute_chapter_hash,
    compute_final_concat_hash,
    compute_visual_segment_hash,
)
from app.services.longform.planner import compute_plan
from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    ItemStatus,
    ProjectManifest,
    Segment,
    TtsGroup,
    VisualSpec,
)


def test_planner_detects_missing_and_zero_byte_files(tmp_path: Path):
    """Verify planner flags missing files and 0-byte files as pending."""
    project_dir = tmp_path / "plan_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    audio_file = "assets/audio/groups/ch1_g1.mp3"
    clip_file = "assets/visual/clips/ch1_s1.mp4"

    # Make clip_file 0-byte
    full_clip = project_dir / clip_file
    full_clip.parent.mkdir(parents=True, exist_ok=True)
    full_clip.write_bytes(b"")  # 0 bytes

    # Audio file is completely missing

    seg = Segment(
        segment_index=1,
        narration_text="Test sentence.",
        duration_seconds=3.0,
        visual=VisualSpec(rendered_clip=clip_file),
    )
    group = TtsGroup(
        group_index=1,
        paragraph_text="Test sentence.",
        audio=AudioSpec(file_path=audio_file),
        segments=[seg],
    )
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        output_file="assets/chapters/ch1.mp4",
        tts_groups=[group],
    )
    manifest = ProjectManifest(
        project_id="plan_proj",
        title="Plan Test",
        preset_id="documentary_en",
        preset_snapshot={},
        chapters=[ch],
    )

    plan = compute_plan(manifest, project_dir)

    units_by_id = {u.unit_id: u for u in plan.all_units}
    assert units_by_id["ch1_g1_audio"].status == ItemStatus.pending
    assert "does not exist" in units_by_id["ch1_g1_audio"].reason

    assert units_by_id["ch1_s1_visual"].status == ItemStatus.pending
    assert "0 bytes" in units_by_id["ch1_s1_visual"].reason

    # Chapter render must be pending due to incomplete dependencies
    assert units_by_id["ch1_render"].status == ItemStatus.pending


def test_planner_detects_stale_hash_when_file_exists(tmp_path: Path):
    """Verify planner flags existing file as stale when hash does not match manifest."""
    project_dir = tmp_path / "stale_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    audio_file = "assets/audio/groups/ch1_g1.mp3"
    full_audio = project_dir / audio_file
    full_audio.parent.mkdir(parents=True, exist_ok=True)
    full_audio.write_bytes(b"some audio data")

    # Hash in manifest does not match current paragraph text
    old_hash = "old_outdated_hash_1234"
    group = TtsGroup(
        group_index=1,
        paragraph_text="Edited paragraph text.",
        audio=AudioSpec(file_path=audio_file, audio_hash=old_hash),
        segments=[],
    )
    ch = Chapter(chapter_index=1, chapter_title="Chapter 1", tts_groups=[group])
    manifest = ProjectManifest(
        project_id="stale_proj",
        title="Stale Test",
        preset_id="documentary_en",
        preset_snapshot={},
        chapters=[ch],
    )

    plan = compute_plan(manifest, project_dir)
    audio_unit = [u for u in plan.all_units if u.unit_id == "ch1_g1_audio"][0]
    assert audio_unit.status == ItemStatus.stale
    assert "mismatch" in audio_unit.reason
