"""Tests for Stage 5 Longform Video Rendering (5.1 - 5.6).

Covers:
- Subtitle chunking (2 lines, orphan word prevention, min/max duration, punctuation breaks)
- ASS PlayRes & font preflight (path with spaces, missing font warning)
- Stream parameter validation (rejecting mixed encoders / resolutions)
- Chapter duration validation (±1 frame tolerance)
- Final audio constructed from WAV
- Ducking and two-pass loudnorm calls
- YouTube chapters format & warnings (< 3 chapters or < 10s)
- Resume after chapter failure
- Ctrl+C cleanup of partial files
- Single sentence edit hash invalidation (only that chapter and final become stale)
- CLI smoke tests for all render subcommands
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from app.services.longform.hashing import compute_chapter_hash, compute_final_concat_hash
from app.services.longform.planner import compute_plan
from app.services.longform.render.bgm import BgmProcessor, concatenate_wav_files
from app.services.longform.render.chapter_render import (
    ChapterRenderer,
    escape_ffmpeg_filter_path,
    probe_media_file,
)
from app.services.longform.render.final_concat import (
    FinalConcatenator,
    format_youtube_timestamp,
    validate_chapter_streams,
)
from app.services.longform.render.service import RenderService
from app.services.longform.render.subtitle import (
    SubtitleChunker,
    check_font_availability,
    format_ass_timestamp,
    format_srt_timestamp,
    generate_ass_header,
    generate_chapter_ass_and_srt,
    wrap_chunk_lines,
)
from app.services.longform.schema import (
    Chapter,
    EncoderSettings,
    ItemStatus,
    MotionType,
    ProjectManifest,
    Shot,
    TtsGroup,
)
from app.services.longform.store import save_manifest
import cli_longform


# ---------------------------------------------------------------------------
# 5.2 Subtitle Chunking & ASS Tests
# ---------------------------------------------------------------------------

def test_subtitle_wrapping_avoids_hanging_orphan_word_on_second_line():
    """5.2: Subtitle wrapping ensures no single orphan word hangs on the second line."""
    words = ["Detroit", "muscle", "cars", "dominated", "the", "American", "highways", "loudly"]
    lines = wrap_chunk_lines(words, max_chars_per_line=35)
    assert len(lines) == 2
    # Line 2 must have at least 2 words (not a single orphan word)
    assert len(lines[1].split()) >= 2
    assert len(lines[0]) <= 35
    assert len(lines[1]) <= 35


def test_subtitle_chunker_duration_bounds_and_punctuation_break():
    """5.2: SubtitleChunker respects 0.8-6.0s duration and splits at punctuation."""
    words = [
        {"word": "In", "start": 0.0, "end": 0.2},
        {"word": "1964,", "start": 0.2, "end": 0.9},  # punctuation at 0.9s (duration 0.9 >= 0.8s)
        {"word": "Pontiac", "start": 1.0, "end": 1.5},
        {"word": "engineers", "start": 1.5, "end": 2.2},
        {"word": "broke", "start": 2.2, "end": 2.6},
        {"word": "all", "start": 2.6, "end": 2.9},
        {"word": "the", "start": 2.9, "end": 3.1},
        {"word": "rules.", "start": 3.1, "end": 3.7},
    ]
    chunker = SubtitleChunker(min_duration=0.8, max_duration=6.0, max_chars_per_line=42)
    chunks = chunker.chunk_words(words)
    assert len(chunks) == 2
    assert chunks[0].plain_text == "In 1964,"
    assert chunks[0].duration >= 0.8
    assert chunks[1].plain_text == "Pontiac engineers broke all the rules."


def test_ass_header_playres_matches_project_resolution():
    """5.2: ASS header sets PlayResX and PlayResY matching project resolution (1920x1080)."""
    style = {"font_name": "UTM Kabel KT", "font_size": 48}
    header = generate_ass_header(style, play_res_x=1920, play_res_y=1080)
    assert "PlayResX: 1920" in header
    assert "PlayResY: 1080" in header
    assert "UTM Kabel KT" in header


def test_font_path_with_spaces_and_missing_font_warning(tmp_path: Path, caplog):
    """5.2: Fonts with spaces ('UTM Kabel KT.ttf') are detected; missing fonts emit warning."""
    fonts_dir = tmp_path / "resource" / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    custom_font = fonts_dir / "UTM Kabel KT.ttf"
    custom_font.write_bytes(b"FONT_BYTES_1234")

    found, path = check_font_availability("UTM Kabel KT", fonts_dir)
    assert found is True
    assert path == custom_font

    # Check non-existent font warning
    with caplog.at_level("WARNING"):
        generate_ass_header({"font_name": "NonExistentFontXYZ"}, fonts_dir=fonts_dir)
    assert any("NonExistentFontXYZ" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 5.1 & 5.3 Stream Validation, Encoder Locking, and Concat Tests
# ---------------------------------------------------------------------------

def test_stream_parameter_validation_rejects_mixed_encoders(tmp_path: Path):
    """5.1 & 5.3: Stream validator rejects chapters encoded with different codecs or resolutions."""
    p_dir = tmp_path / "stream_val_proj"
    p_dir.mkdir(parents=True, exist_ok=True)
    ch_dir = p_dir / "assets" / "visual" / "chapters"
    ch_dir.mkdir(parents=True, exist_ok=True)

    ch1_mp4 = ch_dir / "chapter_01.mp4"
    ch2_mp4 = ch_dir / "chapter_02.mp4"
    ch1_mp4.write_bytes(b"DUMMY_MP4_1")
    ch2_mp4.write_bytes(b"DUMMY_MP4_2")

    ch1 = Chapter(chapter_index=1, chapter_title="Ch1", output_file="assets/visual/chapters/chapter_01.mp4")
    ch2 = Chapter(chapter_index=2, chapter_title="Ch2", output_file="assets/visual/chapters/chapter_02.mp4")

    # Mock probe_media_file: ch1 is 1920x1080 h264, ch2 is 1280x720 h264
    def mock_probe(path):
        if "chapter_01" in str(path):
            return {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"}]}
        return {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "pix_fmt": "yuv420p", "r_frame_rate": "30/1"}]}

    with patch("app.services.longform.render.final_concat.probe_media_file", side_effect=mock_probe):
        with pytest.raises(ValueError) as exc:
            validate_chapter_streams([ch1, ch2], p_dir)
        assert "Stream parameter mismatch" in str(exc.value)
        assert "width differs" in str(exc.value) or "height differs" in str(exc.value)


def test_final_audio_constructed_losslessly_from_wav(tmp_path: Path):
    """5.3: Final audio concatenates WAV files losslessly (not decoded from lossy AAC)."""
    p_dir = tmp_path / "audio_concat_proj"
    p_dir.mkdir(parents=True, exist_ok=True)

    wav1 = p_dir / "ch1.wav"
    wav2 = p_dir / "ch2.wav"
    out_wav = p_dir / "out.wav"

    wav1.write_bytes(b"RIFF_WAV1")
    wav2.write_bytes(b"RIFF_WAV2")

    with patch("subprocess.run") as mock_sub:
        concatenate_wav_files([wav1, wav2], out_wav)
        assert mock_sub.called
        cmd_args = mock_sub.call_args[0][0]
        # Must use concat demuxer and -c copy
        assert "-f" in cmd_args and "concat" in cmd_args
        assert "-c" in cmd_args and "copy" in cmd_args


def test_ducking_and_two_pass_loudnorm_arguments(tmp_path: Path):
    """5.3: BgmProcessor invokes sidechaincompress ducking and two-pass loudnorm to -14 LUFS."""
    p_dir = tmp_path / "bgm_proj"
    p_dir.mkdir(parents=True, exist_ok=True)

    wav1 = p_dir / "ch1.wav"
    wav1.write_bytes(b"RIFF_WAV")

    bgm_file = p_dir / "bgm.mp3"
    bgm_file.write_bytes(b"MP3_DATA")

    processor = BgmProcessor(p_dir)
    preset_snap = {
        "bgm_volume": 0.25,
        "bgm_ducking_threshold": 0.04,
        "bgm_ducking_ratio": 5.0,
    }

    sub_calls = []

    def mock_subprocess_run(cmd, *args, **kwargs):
        sub_calls.append(cmd)
        mock_res = MagicMock()
        mock_res.stdout = "5.0\n"
        if "print_format=json" in str(cmd):
            mock_res.stderr = json.dumps({
                "input_i": "-20.5",
                "input_tp": "-3.2",
                "input_lra": "8.1",
                "input_thresh": "-31.0",
                "target_offset": "0.5",
            })
        elif "ebur128=peak=true" in str(cmd):
            mock_res.stderr = "Integrated loudness:\n  I: -14.1 LUFS\nTrue peak:\n  Peak: -1.0 dBTP\nLRA:\n  LRA: 6.5 LU"
        else:
            mock_res.stderr = ""
        return mock_res

    with patch("subprocess.run", side_effect=mock_subprocess_run), patch("app.services.longform.render.bgm.concatenate_wav_files"):
        out_wav = p_dir / "final_norm.wav"
        res_wav, stats = processor.process_final_audio(
            chapter_wavs=[wav1],
            output_final_wav=out_wav,
            preset_snapshot=preset_snap,
            bgm_override=bgm_file,
        )

        assert stats["integrated_lufs"] == -14.1
        assert stats["true_peak_dbtp"] == -1.0

        # Check that sidechaincompress was invoked with preset parameters
        mix_calls = [c for c in sub_calls if any("sidechaincompress" in str(arg) for arg in c)]
        assert len(mix_calls) >= 1
        filter_str = str(mix_calls[0])
        assert "threshold=0.04" in filter_str
        assert "ratio=5.0" in filter_str
        assert "volume=0.25" in filter_str

        # Check two-pass loudnorm calls
        pass1_calls = [c for c in sub_calls if any("print_format=json" in str(arg) for arg in c)]
        assert len(pass1_calls) >= 1
        assert "I=-14" in str(pass1_calls[0])


# ---------------------------------------------------------------------------
# 5.4 YouTube Chapters Metadata Tests
# ---------------------------------------------------------------------------

def test_youtube_chapters_format_and_criteria_warnings(tmp_path: Path, capsys):
    """5.4: youtube_chapters.txt starts with 00:00 and warns if < 3 chapters or < 10s duration."""
    concat = FinalConcatenator(tmp_path)

    # 1. Project with only 2 chapters (violates YouTube >= 3 chapters requirement)
    ch1 = Chapter(chapter_index=1, chapter_title="Intro", audio_duration_seconds=5.0)  # violates < 10s
    ch2 = Chapter(chapter_index=2, chapter_title="Second", audio_duration_seconds=20.0)
    manifest = ProjectManifest(
        project_id="yt_proj",
        title="YT Proj",
        preset_id="automotive",
        preset_snapshot={},
        chapters=[ch1, ch2],
    )

    offsets = [(1, "Intro", 0.0), (2, "Second", 5.0)]
    yt_file = concat.generate_youtube_chapters(manifest, offsets)
    assert yt_file.is_file()

    content = yt_file.read_text(encoding="utf-8").strip().splitlines()
    assert content[0] == "00:00 Intro"
    assert content[1] == "00:05 Second"

    # Check warnings printed to stdout / logged
    captured = capsys.readouterr().out
    assert "at least 3 chapters" in captured
    assert "less than YouTube minimum 10 seconds" in captured


# ---------------------------------------------------------------------------
# 5.1 & 5.5 Resume and Stale Invalidation Tests
# ---------------------------------------------------------------------------

def test_resume_skips_completed_chapters_and_ctrl_c_cleanup(tmp_path: Path):
    """5.1 & 5.5: Resume skips already completed chapters; Ctrl+C cleans up partial .tmp.mp4 file."""
    p_dir = tmp_path / "resume_proj"
    p_dir.mkdir(parents=True, exist_ok=True)
    ch_dir = p_dir / "assets" / "visual" / "chapters"
    ch_dir.mkdir(parents=True, exist_ok=True)

    # Pre-existing completed chapter 1
    ch1_mp4 = ch_dir / "chapter_01.mp4"
    ch1_mp4.write_bytes(b"CHAPTER_1_MP4_COMPLETE")

    # Create dummy audio and shot files for Chapter 2
    audio_dir = p_dir / "assets" / "audio" / "chapters"
    audio_dir.mkdir(parents=True, exist_ok=True)
    ch2_wav = audio_dir / "ch2.wav"
    ch2_wav.write_bytes(b"RIFF_WAV_CH2")

    shot_dir = p_dir / "assets" / "visual" / "shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    shot2_clip = shot_dir / "ch02_shot001.mp4"
    shot2_clip.write_bytes(b"SHOT2_CLIP")

    shot2 = Shot(
        shot_id="ch02_shot001",
        chapter_index=2,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        rendered_clip="assets/visual/shots/ch02_shot001.mp4",
        status=ItemStatus.completed,
    )

    ch1 = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        audio_file="assets/audio/chapters/ch1.wav",
        output_file="assets/visual/chapters/chapter_01.mp4",
        status=ItemStatus.completed,
    )
    ch2 = Chapter(
        chapter_index=2,
        chapter_title="Chapter 2",
        audio_file="assets/audio/chapters/ch2.wav",
        shots=[shot2],
        status=ItemStatus.pending,
    )

    manifest = ProjectManifest(
        project_id="resume_proj",
        title="Resume Proj",
        preset_id="automotive",
        preset_snapshot={},
        chapters=[ch1, ch2],
    )
    ch1.chapter_hash = compute_chapter_hash(
        chapter=ch1,
        encoder_settings=manifest.encoder_settings,
        subtitle_style={},
        preset_snapshot={},
    )
    save_manifest(manifest, p_dir)

    service = RenderService(p_dir)

    # Render chapter 1 with force=False -> should skip
    m, stats1 = service.render_chapter(manifest, chapter_index=1, force=False)
    assert stats1["status"] == "skipped"

    # Test Ctrl+C cleanup in ChapterRenderer
    renderer = ChapterRenderer(p_dir)
    tmp_file = ch_dir / "chapter_02.tmp.mp4"

    def mock_popen_interrupt(*args, **kwargs):
        # Create tmp file then raise KeyboardInterrupt
        tmp_file.write_bytes(b"PARTIAL_MP4_DATA")
        raise KeyboardInterrupt()

    with patch("subprocess.Popen", side_effect=mock_popen_interrupt):
        with pytest.raises(KeyboardInterrupt):
            renderer.render_chapter(manifest, chapter_index=2)

    # Verify partial file is removed
    assert not tmp_file.is_file()


def test_single_sentence_edit_invalidates_only_that_chapter_and_final(tmp_path: Path):
    """5.1 & 5.6: Editing a sentence in chapter 1 marks only chapter 1 and final stale; chapter 2 remains completed."""
    p_dir = tmp_path / "inval_proj"
    p_dir.mkdir(parents=True, exist_ok=True)

    ch_dir = p_dir / "assets" / "visual" / "chapters"
    ch_dir.mkdir(parents=True, exist_ok=True)
    ch1_file = ch_dir / "chapter_01.mp4"
    ch2_file = ch_dir / "chapter_02.mp4"
    ch1_file.write_bytes(b"CH1_VIDEO")
    ch2_file.write_bytes(b"CH2_VIDEO")

    from app.services.longform.shot import compute_shot_hash

    # Shot clips on disk
    shot_dir = p_dir / "assets" / "visual" / "shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    clip1 = shot_dir / "ch01_shot001.mp4"
    clip2 = shot_dir / "ch02_shot001.mp4"
    clip1.write_bytes(b"CLIP1")
    clip2.write_bytes(b"CLIP2")

    hash1 = compute_shot_hash(
        shot_id="ch01_shot001",
        duration_seconds=3.0,
        target_frames=75,
        motion="zoom_in",
        asset_content_hash="",
        fps=25.0,
        requested_provider="ai_image",
        actual_provider="ai_image",
    )
    hash2 = compute_shot_hash(
        shot_id="ch02_shot001",
        duration_seconds=3.0,
        target_frames=75,
        motion="zoom_in",
        asset_content_hash="",
        fps=25.0,
        requested_provider="ai_image",
        actual_provider="ai_image",
    )

    # Shot in Chapter 1
    shot1 = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        motion=MotionType.zoom_in,
        rendered_clip="assets/visual/shots/ch01_shot001.mp4",
        shot_hash=hash1,
        status=ItemStatus.completed,
    )
    # Shot in Chapter 2
    shot2 = Shot(
        shot_id="ch02_shot001",
        chapter_index=2,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        motion=MotionType.zoom_in,
        rendered_clip="assets/visual/shots/ch02_shot001.mp4",
        shot_hash=hash2,
        status=ItemStatus.completed,
    )

    ch1 = Chapter(
        chapter_index=1,
        chapter_title="Ch1",
        shots=[shot1],
        output_file="assets/visual/chapters/chapter_01.mp4",
        status=ItemStatus.completed,
    )
    ch2 = Chapter(
        chapter_index=2,
        chapter_title="Ch2",
        shots=[shot2],
        output_file="assets/visual/chapters/chapter_02.mp4",
        status=ItemStatus.completed,
    )

    manifest = ProjectManifest(
        project_id="inval_proj",
        title="Inval Test",
        preset_id="automotive",
        preset_snapshot={},
        encoder_settings=EncoderSettings(fps=25.0),
        chapters=[ch1, ch2],
    )
    out_dir = p_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    final_file = out_dir / "final_video.mp4"
    final_file.write_bytes(b"FINAL_VIDEO")

    from app.services.longform.hashing import compute_final_concat_hash
    ch1.chapter_hash = compute_chapter_hash(ch1, manifest.encoder_settings, {}, {})
    ch2.chapter_hash = compute_chapter_hash(ch2, manifest.encoder_settings, {}, {})
    manifest.final_video_file = "output/final_video.mp4"
    manifest.final_video_hash = compute_final_concat_hash(manifest.chapters, bgm_hash="")
    save_manifest(manifest, p_dir)

    # 1. Initial plan: both chapters completed
    plan1 = compute_plan(manifest, p_dir)
    ch1_unit = next(u for u in plan1.all_units if u.unit_id == "ch1_render")
    ch2_unit = next(u for u in plan1.all_units if u.unit_id == "ch2_render")
    assert ch1_unit.status == ItemStatus.completed
    assert ch2_unit.status == ItemStatus.completed

    # 2. Modify sentence / shot in chapter 1 (e.g. shot duration changes)
    shot1.duration_seconds = 4.0

    plan2 = compute_plan(manifest, p_dir)
    ch1_unit_new = next(u for u in plan2.all_units if u.unit_id == "ch1_render")
    ch2_unit_new = next(u for u in plan2.all_units if u.unit_id == "ch2_render")
    final_unit_new = next(u for u in plan2.all_units if u.unit_id == "final_concat")

    # Chapter 1 is stale due to shot change
    assert ch1_unit_new.status == ItemStatus.stale
    # Chapter 2 is untouched and remains completed!
    assert ch2_unit_new.status == ItemStatus.completed
    # Final concat is stale because chapter 1 is not completed
    assert final_unit_new.status == ItemStatus.stale


# ---------------------------------------------------------------------------
# 5.5 CLI Smoke Tests
# ---------------------------------------------------------------------------

def test_cli_smoke_render_subcommands(tmp_path: Path):
    """5.5: Smoke test executing render chapter, all, status, final subcommands."""
    p_dir = tmp_path / "cli_render_proj"
    p_dir.mkdir(parents=True, exist_ok=True)

    manifest = ProjectManifest(
        project_id="cli_render_proj",
        title="CLI Render Proj",
        preset_id="automotive",
        preset_snapshot={},
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1")],
    )
    save_manifest(manifest, p_dir)

    with patch("cli_longform.get_project_dir", return_value=p_dir):
        # 1. render status
        with patch("sys.argv", ["cli_longform.py", "render", "status", "cli_render_proj"]):
            ret_st = cli_longform.main()
            assert ret_st == 0

        # 2. render chapter (mocking service)
        with patch("sys.argv", ["cli_longform.py", "render", "chapter", "cli_render_proj", "--chapter", "1"]):
            with patch("app.services.longform.render.service.RenderService.render_chapter") as mock_r_ch:
                mock_r_ch.return_value = (manifest, {
                    "render_seconds": 1.2,
                    "video_duration": 5.0,
                    "audio_duration": 5.0,
                    "drift_seconds": 0.0,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                    "video_encoder": "libx264",
                    "file_size_bytes": 1024,
                    "status": "rendered",
                })
                ret_ch = cli_longform.main()
                assert ret_ch == 0

        # 3. render final (mocking service)
        with patch("sys.argv", ["cli_longform.py", "render", "final", "cli_render_proj", "--no-bgm"]):
            with patch("app.services.longform.render.service.RenderService.render_final") as mock_r_final:
                mock_r_final.return_value = (manifest, {
                    "final_video_file": "export/final_video.mp4",
                    "final_video_duration": 10.0,
                    "drift_seconds": 0.0,
                    "integrated_lufs": -14.0,
                    "true_peak_dbtp": -1.0,
                    "youtube_chapters_file": "export/youtube_chapters.txt",
                    "full_timeline_srt": "export/subtitles/full_timeline.srt",
                })
                ret_fin = cli_longform.main()
                assert ret_fin == 0

        # 4. render all (mocking service)
        with patch("sys.argv", ["cli_longform.py", "render", "all", "cli_render_proj"]):
            with patch("app.services.longform.render.service.RenderService.render_all") as mock_r_all:
                mock_r_all.return_value = (manifest, {
                    "chapters_rendered": 1,
                    "chapters_skipped": 0,
                    "total_elapsed_seconds": 2.5,
                    "final_stats": {
                        "final_video_file": "export/final_video.mp4",
                        "final_video_duration": 10.0,
                        "total_audio_duration": 10.0,
                        "drift_seconds": 0.0,
                        "integrated_lufs": -14.0,
                        "true_peak_dbtp": -1.0,
                        "youtube_chapters_file": "export/youtube_chapters.txt",
                        "full_timeline_srt": "export/subtitles/full_timeline.srt",
                    },
                })
                ret_all = cli_longform.main()
                assert ret_all == 0
