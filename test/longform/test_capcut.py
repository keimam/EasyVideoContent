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
from app.services.longform.capcut.service import (
    build_smoke_draft,
    parse_srt_entries,
    get_unique_draft_name,
    validate_built_draft,
    build_backup_asset_pack,
)


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
        portable=True,
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


# ==============================================================================
# STAGE 6B UNIT TESTS (A1-A3, B1-B5)
# ==============================================================================

def test_stage6b_text_size_and_position_calibration(temp_draft_dir, mock_media_files):
    """Verify A1: Text size calibrated to 8.0, position y = -0.80, and auto line-wrap."""
    output_draft = temp_draft_dir / "draft_text_test"
    builder = CapCutDraftBuilder()

    long_text = "This is an extremely long subtitle sentence that exceeds forty-eight characters and should be wrapped automatically"
    subtitles = [
        {"text": long_text, "start_seconds": 0.0, "duration_seconds": 4.5},
    ]

    builder.build_draft(
        output_dir=output_draft,
        draft_name="TEST_TEXT_CALIB",
        video_clips=mock_media_files["videos"],
        narration_clips=[{"path": mock_media_files["audio"]["path"], "duration_seconds": 18.888, "start_offset_seconds": 0.0}],
        subtitles=subtitles,
    )

    content = json.loads((output_draft / "draft_content.json").read_text(encoding="utf-8"))
    texts = content["materials"]["texts"]
    assert len(texts) == 1
    t = texts[0]

    # Verify font_size 8.0 and style size 8 (matches ASS FontSize 64 on 1080p canvas)
    assert t["font_size"] == 8.0
    assert t["text_size"] == 16
    assert t["border_width"] == 0.15
    assert t["bold_width"] == 0.20
    assert t["has_shadow"] is True

    payload = json.loads(t["content"])
    assert payload["styles"][0]["size"] == 8
    # Verify auto wrapping (> 48 chars wrapped with newline)
    assert "\n" in payload["text"]

    # Verify lower-third position matching MarginV 80
    text_track = [trk for trk in content["tracks"] if trk["type"] == "text"][0]
    seg = text_track["segments"][0]
    assert seg["clip"]["transform"]["x"] == 0.0
    assert seg["clip"]["transform"]["y"] == -0.80


def test_stage6b_media_panel_bin_registration(temp_draft_dir, mock_media_files):
    """Verify A2: Imported media items are registered in draft_virtual_store and meta info."""
    output_draft = temp_draft_dir / "draft_bin_test"
    builder = CapCutDraftBuilder()

    builder.build_draft(
        output_dir=output_draft,
        draft_name="TEST_BIN_REG",
        video_clips=mock_media_files["videos"],
        narration_clips=[{"path": mock_media_files["audio"]["path"], "duration_seconds": 18.888, "start_offset_seconds": 0.0}],
        subtitles=mock_media_files["subtitles"],
    )

    # 1. Virtual store registration
    vs_data = json.loads((output_draft / "draft_virtual_store.json").read_text(encoding="utf-8"))
    vs_list = vs_data["draft_virtual_store"]
    bin_type1 = [entry for entry in vs_list if entry.get("type") == 1][0]
    bin_items = bin_type1.get("value", [])

    # 3 video clips + 1 narration = 4 items
    assert len(bin_items) == 4
    for item in bin_items:
        assert "child_id" in item
        assert item["parent_id"] == ""
        assert len(item["child_id"]) > 0

    # 2. Meta info materials registration
    meta_data = json.loads((output_draft / "draft_meta_info.json").read_text(encoding="utf-8"))
    registered_meta = meta_data["draft_materials"][0]["value"]
    assert len(registered_meta) == 4
    for mat in registered_meta:
        assert mat["enter_from"] == 0
        assert mat["extra_info"] != ""
        assert "roughcut_time_range" in mat
        assert mat["roughcut_time_range"]["start"] == 0
        assert mat["roughcut_time_range"]["duration"] > 0


def test_stage6b_multi_track_layout_and_chapter_offsets(temp_draft_dir, mock_media_files):
    """Verify B1: Track layout (Video, Subtitles, Multi-chapter Narration, Ducked BGM)."""
    output_draft = temp_draft_dir / "draft_multitrack_test"
    builder = CapCutDraftBuilder()

    # 2 chapter narrations with offsets
    narrations = [
        {"path": mock_media_files["audio"]["path"], "duration_seconds": 5.531, "start_offset_seconds": 0.0},
        {"path": mock_media_files["audio"]["path"], "duration_seconds": 13.357, "start_offset_seconds": 5.531},
    ]

    bgm_stem = {
        "path": mock_media_files["audio"]["path"],
        "duration_seconds": 18.888,
    }

    builder.build_draft(
        output_dir=output_draft,
        draft_name="TEST_MULTITRACK",
        video_clips=mock_media_files["videos"],
        narration_clips=narrations,
        bgm_clip=bgm_stem,
        subtitles=mock_media_files["subtitles"],
    )

    content = json.loads((output_draft / "draft_content.json").read_text(encoding="utf-8"))
    tracks = content["tracks"]
    assert len(tracks) == 4
    assert tracks[0]["type"] == "video"
    assert tracks[1]["type"] == "text"
    assert tracks[2]["type"] == "audio"  # Narration
    assert tracks[3]["type"] == "audio"  # Ducked BGM

    # Verify narration track offsets
    n_segs = tracks[2]["segments"]
    assert len(n_segs) == 2
    assert n_segs[0]["target_timerange"]["start"] == 0
    assert n_segs[0]["target_timerange"]["duration"] == 5531000
    assert n_segs[1]["target_timerange"]["start"] == 5531000
    assert n_segs[1]["target_timerange"]["duration"] == 13357000

    # Verify BGM track segment
    b_segs = tracks[3]["segments"]
    assert len(b_segs) == 1
    assert b_segs[0]["target_timerange"]["start"] == 0
    assert b_segs[0]["target_timerange"]["duration"] == 18888000


def test_stage6b_unique_draft_name_collision_avoidance(temp_draft_dir):
    """Verify B3: Draft name collisions append _1, _2 suffix cleanly."""
    d1 = temp_draft_dir / "MPT_sample"
    d1.mkdir()

    # Collision 1
    name1 = get_unique_draft_name("MPT_sample", [temp_draft_dir])
    assert name1 == "MPT_sample_1"

    d2 = temp_draft_dir / "MPT_sample_1"
    d2.mkdir()

    # Collision 2
    name2 = get_unique_draft_name("MPT_sample", [temp_draft_dir])
    assert name2 == "MPT_sample_2"


def test_stage6b_dual_draft_content_identical_byte_validation(temp_draft_dir, mock_media_files):
    """Verify B3: Root draft_content.json and Timelines/<id>/draft_content.json are byte-identical."""
    output_draft = temp_draft_dir / "draft_dual_test"
    builder = CapCutDraftBuilder()

    builder.build_draft(
        output_dir=output_draft,
        draft_name="TEST_DUAL",
        video_clips=mock_media_files["videos"],
        narration_clips=[{"path": mock_media_files["audio"]["path"], "duration_seconds": 18.888, "start_offset_seconds": 0.0}],
        subtitles=mock_media_files["subtitles"],
    )

    # validate_built_draft passes
    validate_built_draft(output_draft)

    content = json.loads((output_draft / "draft_content.json").read_text(encoding="utf-8"))
    tl_id = content["id"]
    active_content = output_draft / "Timelines" / tl_id / "draft_content.json"

    assert (output_draft / "draft_content.json").read_bytes() == active_content.read_bytes()

    # Tampering with one should cause validate_built_draft to raise RuntimeError
    active_content.write_bytes(b'{"tampered": true}')
    with pytest.raises(RuntimeError, match="are not identical"):
        validate_built_draft(output_draft)


def test_stage6b_backup_asset_pack_structure(temp_draft_dir, mock_media_files):
    """Verify B5: Standalone backup asset pack produces numbered folders, audio, subtitles, and README.txt."""
    project_dir = temp_draft_dir / "mock_project"
    project_dir.mkdir()

    class MockShot:
        def __init__(self, shot_id):
            self.shot_id = shot_id

    class MockChapter:
        def __init__(self, idx, title):
            self.chapter_index = idx
            self.chapter_title = title

    class MockEncoderSettings:
        fps = 30.0
        width = 1920
        height = 1080

    class MockManifest:
        project_id = "test_proj"
        encoder_settings = MockEncoderSettings()
        chapters = [
            MockChapter(1, "The Beginning"),
            MockChapter(2, "The Climax"),
        ]

    manifest = MockManifest()
    v1 = mock_media_files["videos"][0]["path"]
    v2 = mock_media_files["videos"][1]["path"]
    a1 = mock_media_files["audio"]["path"]

    clips = [
        {"path": v1, "duration_seconds": 5.531, "chapter_index": 1, "shot_id": "ch01_shot001"},
        {"path": v2, "duration_seconds": 6.238, "chapter_index": 2, "shot_id": "ch02_shot001"},
    ]

    srt_file = project_dir / "sample.srt"
    srt_file.write_text("1\n00:00:00,000 --> 00:00:05,000\nHello\n", encoding="utf-8")

    pack_dir = build_backup_asset_pack(
        project_dir=project_dir,
        manifest=manifest,
        all_video_clips=clips,
        chapter_wav_paths=[a1],
        ducked_bgm_path=a1,
        subtitles_srt=srt_file,
    )

    assert pack_dir.is_dir()
    assert (pack_dir / "chapter_01" / f"001_{v1.name}").is_file()
    assert (pack_dir / "chapter_02" / f"001_{v2.name}").is_file()
    assert (pack_dir / "audio" / "chapter_01_narration.wav").is_file()
    assert (pack_dir / "audio" / "bgm_ducked.wav").is_file()
    assert (pack_dir / "subtitles" / "full_timeline.srt").is_file()

    readme_content = (pack_dir / "README.txt").read_text(encoding="utf-8")
    assert "Proyek ID:       test_proj" in readme_content
    assert "Resolusi:        1920x1080" in readme_content
    assert "Frame Rate:      30.00 fps" in readme_content
    assert "Bab 01: The Beginning" in readme_content
    assert "Bab 02: The Climax" in readme_content
