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
    EncoderSettings,
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


def test_planner_segment_text_edit_invalidates_group_and_chapter(tmp_path: Path):
    """Verify editing narration_text in a segment flags its audio group, chapter render, and final concat as stale (A1)."""
    project_dir = tmp_path / "a1_proj"
    project_dir.mkdir(parents=True, exist_ok=True)

    # 1. Setup completed project with 2 chapters
    audio1 = project_dir / "assets/audio/groups/ch1_g1.mp3"
    audio1.parent.mkdir(parents=True, exist_ok=True)
    audio1.write_bytes(b"audio_ch1")

    clip1 = project_dir / "assets/visual/clips/ch1_s1.mp4"
    clip1.parent.mkdir(parents=True, exist_ok=True)
    clip1.write_bytes(b"clip_ch1")

    ch1_file = project_dir / "assets/chapters/ch1.mp4"
    ch1_file.parent.mkdir(parents=True, exist_ok=True)
    ch1_file.write_bytes(b"chapter_1_mp4")

    final_file = project_dir / "output/final_video.mp4"
    final_file.parent.mkdir(parents=True, exist_ok=True)
    final_file.write_bytes(b"final_video_mp4")

    initial_text_seg1 = "Original first sentence."
    seg1 = Segment(
        segment_index=1,
        narration_text=initial_text_seg1,
        duration_seconds=3.0,
        visual=VisualSpec(
            rendered_clip="assets/visual/clips/ch1_s1.mp4",
            visual_hash=compute_visual_segment_hash("", "zoom_in", 3.0),
        ),
        status=ItemStatus.completed,
    )

    initial_group_hash = compute_audio_group_hash(initial_text_seg1, voice_name="en-US-ChristopherNeural")
    group1 = TtsGroup(
        group_index=1,
        paragraph_text=initial_text_seg1,
        audio=AudioSpec(
            file_path="assets/audio/groups/ch1_g1.mp3",
            voice_name="en-US-ChristopherNeural",
            audio_hash=initial_group_hash,
        ),
        segments=[seg1],
        status=ItemStatus.completed,
    )

    encoder = EncoderSettings()
    ch1 = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        output_file="assets/chapters/ch1.mp4",
        tts_groups=[group1],
        status=ItemStatus.completed,
    )
    ch1.chapter_hash = compute_chapter_hash(ch1, encoder, {}, {"name": "Test"})

    manifest = ProjectManifest(
        project_id="a1_proj",
        title="A1 Test",
        preset_id="documentary_en",
        preset_snapshot={"name": "Test"},
        chapters=[ch1],
        final_video_file="output/final_video.mp4",
        final_video_hash=compute_final_concat_hash([ch1]),
        status=ItemStatus.completed,
    )

    # Initial plan: all completed
    plan_initial = compute_plan(manifest, project_dir)
    assert plan_initial.is_fully_completed

    # User manually edits narration_text of segment 1 in manifest
    seg1.narration_text = "Edited first sentence with new words."

    # Planner computes plan:
    # - ch1_g1_audio must become STALE because effective text changed
    # - ch1_render must become STALE
    # - final_concat must become STALE
    # - visual segment remains completed (clip & visual params unchanged)
    plan_after_edit = compute_plan(manifest, project_dir)
    units_by_id = {u.unit_id: u for u in plan_after_edit.all_units}

    assert units_by_id["ch1_g1_audio"].status == ItemStatus.stale
    assert units_by_id["ch1_render"].status == ItemStatus.stale
    assert units_by_id["final_concat"].status == ItemStatus.stale
    assert units_by_id["ch1_s1_visual"].status == ItemStatus.completed
