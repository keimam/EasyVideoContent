"""Tests for CapCut desktop template analysis, doctor, and draft builder (Stage 6A)."""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.longform.capcut.builder import CapCutDraftBuilder
from app.services.longform.capcut.detector import (
    TEMPLATE_VERSION,
    detect_capcut_version,
    detect_draft_folder,
    get_capcut_doctor_report,
    is_capcut_running,
)
from app.services.longform.capcut.service import build_smoke_draft, parse_srt_entries


@pytest.fixture
def temp_draft_dir():
    temp_dir = Path(tempfile.mkdtemp(prefix="test_capcut_draft_"))
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_media_files(temp_draft_dir):
    media_dir = temp_draft_dir / "input_media"
    media_dir.mkdir()

    v1 = media_dir / "clip_01.mp4"
    v1.write_bytes(b"dummy video 1 content")
    v2 = media_dir / "clip_02.mp4"
    v2.write_bytes(b"dummy video 2 content")
    v3 = media_dir / "clip_03.mp4"
    v3.write_bytes(b"dummy video 3 content")

    a1 = media_dir / "audio.wav"
    a1.write_bytes(b"dummy audio content")

    return {
        "videos": [
            {"path": v1, "duration_seconds": 5.531},
            {"path": v2, "duration_seconds": 6.238},
            {"path": v3, "duration_seconds": 7.119},
        ],
        "audio": {"path": a1, "duration_seconds": 18.888},
        "subtitles": [
            {"text": "Subtitle Line 1", "start_seconds": 0.0, "duration_seconds": 5.531},
            {"text": "Subtitle Line 2", "start_seconds": 5.531, "duration_seconds": 6.238},
        ],
    }


def test_capcut_doctor_detection():
    """Verify CapCut doctor returns correct structure and detects version 8.9.0."""
    report = get_capcut_doctor_report()
    assert isinstance(report, dict)
    assert "installed" in report
    assert "template_version" in report
    assert report["template_version"] == "8.9.0"
    assert "is_running" in report
    assert "draft_folder" in report
    assert isinstance(report["warnings"], list)


def test_builder_constructs_valid_draft_schema(temp_draft_dir, mock_media_files):
    """Verify builder produces verified CapCut 8.9.0 draft structure and schema."""
    output_draft = temp_draft_dir / "output_draft"
    builder = CapCutDraftBuilder()

    builder.build_draft(
        output_dir=output_draft,
        draft_name="TEST_MPT_DRAFT",
        video_clips=mock_media_files["videos"],
        audio_clip=mock_media_files["audio"],
        subtitles=mock_media_files["subtitles"],
    )

    assert output_draft.is_dir()
    assert (output_draft / "draft_content.json").is_file()
    assert (output_draft / "draft_meta_info.json").is_file()
    assert (output_draft / "Timelines" / "project.json").is_file()

    # Verify draft_content.json
    content = json.loads((output_draft / "draft_content.json").read_text(encoding="utf-8"))
    assert content["version"] == 360000
    assert content["new_version"] == "175.0.0"
    assert content["fps"] == 30.0
    timeline_id = content["id"]
    assert len(timeline_id) == 36

    # Verify duration in microseconds (18.888s -> 18,888,000 us)
    expected_duration_us = 18888000
    assert content["duration"] == expected_duration_us

    # Verify tracks
    tracks = content["tracks"]
    assert len(tracks) == 3
    track_types = [t["type"] for t in tracks]
    assert track_types == ["video", "text", "audio"]

    video_track = tracks[0]
    assert len(video_track["segments"]) == 3
    assert video_track["segments"][0]["target_timerange"]["duration"] == 5531000
    assert video_track["segments"][1]["target_timerange"]["duration"] == 6238000
    assert video_track["segments"][2]["target_timerange"]["duration"] == 7119000

    text_track = tracks[1]
    assert len(text_track["segments"]) == 2
    assert text_track["segments"][0]["target_timerange"]["start"] == 0
    assert text_track["segments"][1]["target_timerange"]["start"] == 5531000

    audio_track = tracks[2]
    assert len(audio_track["segments"]) == 1
    assert audio_track["segments"][0]["target_timerange"]["duration"] == expected_duration_us

    # Verify media portability
    media_dir = output_draft / "media"
    assert (media_dir / "video_001.mp4").is_file()
    assert (media_dir / "video_002.mp4").is_file()
    assert (media_dir / "video_003.mp4").is_file()
    assert (media_dir / "audio_track.wav").is_file()

    # Verify Timelines directory consistency
    tl_content_path = output_draft / "Timelines" / timeline_id / "draft_content.json"
    assert tl_content_path.is_file()
    tl_content = json.loads(tl_content_path.read_text(encoding="utf-8"))
    assert tl_content["id"] == timeline_id
    assert tl_content["duration"] == expected_duration_us

    # Verify draft_meta_info.json
    meta = json.loads((output_draft / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert meta["draft_name"] == "TEST_MPT_DRAFT"
    assert meta["tm_duration"] == expected_duration_us
    assert len(meta["draft_materials"][0]["value"]) == 4  # 3 videos + 1 audio


def test_rejection_when_capcut_is_running():
    """Verify that build_smoke_draft rejects install if CapCut process is active."""
    with patch("app.services.longform.capcut.service.is_capcut_running", return_value=True):
        with pytest.raises(RuntimeError, match="CapCut is currently running"):
            build_smoke_draft("uji_nokey", install=True)


def test_install_does_not_modify_other_drafts(temp_draft_dir):
    """Verify installer creates only target draft and never touches or corrupts existing user drafts."""
    # Simulate user draft directory with existing drafts
    draft_root = temp_draft_dir / "com.lveditor.draft"
    draft_root.mkdir()

    existing_draft_1 = draft_root / "USER_IMPORTANT_PROJECT"
    existing_draft_1.mkdir()
    marker_1 = existing_draft_1 / "keep_me.txt"
    marker_1.write_text("critical user content", encoding="utf-8")

    existing_draft_2 = draft_root / "MPT_TEMPLATE"
    existing_draft_2.mkdir()
    marker_2 = existing_draft_2 / "template_data.json"
    marker_2.write_text("{\"original\": true}", encoding="utf-8")

    with patch("app.services.longform.capcut.service.detect_draft_folder", return_value=draft_root), \
         patch("app.services.longform.capcut.service.is_capcut_running", return_value=False):
        res = build_smoke_draft("uji_nokey", install=True)

    installed_path = Path(res["installed_path"])
    assert installed_path.is_dir()
    assert installed_path.name == "MPT_SMOKE_uji_nokey"

    # Verify other drafts are untouched
    assert marker_1.is_file()
    assert marker_1.read_text(encoding="utf-8") == "critical user content"
    assert marker_2.is_file()
    assert marker_2.read_text(encoding="utf-8") == "{\"original\": true}"


def test_parse_srt_entries_helper(temp_draft_dir):
    """Verify parse_srt_entries parses standard SRT formatting."""
    srt_file = temp_draft_dir / "sample.srt"
    srt_file.write_text(
        "1\n00:00:01,000 --> 00:00:03,500\nFirst line of subtitle\n\n"
        "2\n00:00:03,500 --> 00:00:07,250\nSecond line of subtitle\n\n"
        "3\n00:00:07,250 --> 00:00:10,000\nThird line\n",
        encoding="utf-8",
    )
    entries = parse_srt_entries(srt_file, max_entries=2)
    assert len(entries) == 2
    assert entries[0]["text"] == "First line of subtitle"
    assert entries[0]["start_seconds"] == 1.0
    assert entries[0]["duration_seconds"] == 2.5
    assert entries[1]["text"] == "Second line of subtitle"
    assert entries[1]["start_seconds"] == 3.5
    assert entries[1]["duration_seconds"] == 3.75
