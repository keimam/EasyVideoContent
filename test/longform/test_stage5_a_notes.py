"""Tests for Bagian A (Stage 4 Notes / Fixes A1 - A4)."""
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from app.services.longform.schema import (
    Chapter,
    EncoderSettings,
    ItemStatus,
    MotionType,
    ProjectManifest,
    Shot,
)
from app.services.longform.store import save_manifest
from app.services.longform.visual.service import VisualService
import cli_longform


def test_a1_visual_review_displays_correct_project_fps(tmp_path: Path, capsys):
    """A1: visual review displays fps from encoder_settings (e.g. 30 fps, not hardcoded 25 fps)."""
    proj_dir = tmp_path / "a1_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    shot = Shot(
        shot_id="ch04_shot012",
        chapter_index=4,
        shot_index=12,
        duration_seconds=6.235,
        target_frames=187,
        motion=MotionType.zoom_in,
        needs_review=True,
        status=ItemStatus.pending,
    )
    ch = Chapter(chapter_index=4, chapter_title="Final Lore", shots=[shot])
    manifest = ProjectManifest(
        project_id="a1_proj",
        title="A1 Test",
        preset_id="automotive",
        preset_snapshot={"required_subject_terms": ["car"]},
        encoder_settings=EncoderSettings(fps=30.0),
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    with patch("cli_longform.get_project_dir", return_value=proj_dir), patch("sys.argv", ["cli_longform.py", "visual", "review", "a1_proj"]):
        ret = cli_longform.main()
        assert ret == 0

    captured = capsys.readouterr().out
    assert "187 frames at 30 fps" in captured
    assert "25 fps" not in captured


def test_a2_relevance_rules_read_preset_snapshot_and_get_preset_backfill(tmp_path: Path, caplog):
    """A2: fetch_all correctly imports get_preset, logs warning if failed, and uses preset_snapshot."""
    proj_dir = tmp_path / "a2_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        visual_prompt="car engine",
        search_query="car engine",
        tags=["car", "engine"],
        status=ItemStatus.pending,
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])

    # Preset snapshot with custom required_subject_terms
    manifest = ProjectManifest(
        project_id="a2_proj",
        title="A2 Test",
        preset_id="automotive",
        preset_snapshot={
            "required_subject_terms": ["car", "automobile"],
            "negative_terms": ["sea", "boat"],
            "min_relevance_score": 0.40,
        },
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    service = VisualService(proj_dir)
    # Ensure backfill doesn't throw or log 'cannot import name PresetStore'
    with caplog.at_level("DEBUG"):
        # Run fetch with fake provider
        service.fetch_all(manifest, provider_name="fake", allow_fallback=False)

    for record in caplog.records:
        assert "cannot import name 'PresetStore'" not in record.message
        assert "PresetStore" not in record.message


def test_a3_cli_command_hints_syntax_and_smoke_execution(tmp_path: Path, capsys):
    """A3: Resolution hints match valid CLI syntax (--shot, --search) and can be executed cleanly."""
    proj_dir = tmp_path / "a3_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        visual_prompt="classic car",
        search_query="classic car",
        needs_review=True,
        status=ItemStatus.pending,
    )
    ch = Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])
    manifest = ProjectManifest(
        project_id="a3_proj",
        title="A3 Test",
        preset_id="automotive",
        preset_snapshot={"name": "Automotive"},
        encoder_settings=EncoderSettings(fps=25.0),
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    with patch("cli_longform.get_project_dir", return_value=proj_dir), patch("sys.argv", ["cli_longform.py", "visual", "review", "a3_proj"]):
        cli_longform.main()

    captured = capsys.readouterr().out
    assert "--shot ch01_shot001 --candidate" in captured
    assert "--shot ch01_shot001 --search" in captured
    assert "--shot ch01_shot001 --file" in captured
    assert "--prompt" not in captured

    # Test executing visual replace with --shot and --search and --list
    with patch("cli_longform.get_project_dir", return_value=proj_dir), patch("sys.argv", ["cli_longform.py", "visual", "replace", "a3_proj", "--shot", "ch01_shot001", "--search", "muscle car", "--provider", "fake", "--list"]):
        ret_search = cli_longform.main()
        assert ret_search == 0

        # Test selecting candidate 1
    with patch("cli_longform.get_project_dir", return_value=proj_dir), patch("sys.argv", ["cli_longform.py", "visual", "replace", "a3_proj", "--shot", "ch01_shot001", "--candidate", "1"]):
        ret_cand = cli_longform.main()
        assert ret_cand == 0


def test_a4_rejected_candidates_labeled_rejected_in_review(tmp_path: Path, capsys):
    """A4: Rejected candidates in visual review are labeled [REJECTED: ...] rather than appearing valid."""
    proj_dir = tmp_path / "a4_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)

    # Candidate with tags that fail automotive filter (dubrovnik port, no car in first 5 tags)
    rejected_cand = {
        "candidate_id": "pixabay_9999",
        "provider": "pixabay",
        "relevance_score": 0.60,
        "rendition_name": "1080p",
        "tags": ["dubrovnik", "sunset", "sea", "city", "port", "cars"],
    }
    shot = Shot(
        shot_id="ch04_shot012",
        chapter_index=4,
        shot_index=12,
        duration_seconds=4.0,
        target_frames=100,
        needs_review=True,
        candidates=[rejected_cand],
        status=ItemStatus.pending,
    )
    ch = Chapter(chapter_index=4, chapter_title="Ch4", shots=[shot])
    manifest = ProjectManifest(
        project_id="a4_proj",
        title="A4 Test",
        preset_id="automotive",
        preset_snapshot={
            "required_subject_terms": ["car", "automobile"],
            "negative_terms": ["sea", "port", "city"],
            "min_relevance_score": 0.35,
        },
        encoder_settings=EncoderSettings(fps=25.0),
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    with patch("cli_longform.get_project_dir", return_value=proj_dir), patch("sys.argv", ["cli_longform.py", "visual", "review", "a4_proj"]):
        cli_longform.main()

    captured = capsys.readouterr().out
    assert "PIXABAY ID=pixabay_9999" in captured
    assert "[REJECTED:" in captured
    assert "score: 0.60" not in captured
