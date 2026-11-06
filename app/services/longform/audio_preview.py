"""Chapter Audio Timeline Assembly and Verification Preview Generator (3.4, 3.6, R1, R3).

Builds:
- Absolute chapter audio timelines combining group WAVs with configurable pauses
- Synchronization of word timing sidecars with absolute chapter offsets (R3)
- Zero-drift chapter preview WAVs in export/preview/chapter_NN_preview.wav
- Precise SRT subtitle files matching segment absolute timings for VLC verification
- 720p black-screen preview MP4s with burned ASS subtitles (R1) with safe path handling
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import uuid
from pathlib import Path
from typing import List, Tuple

from loguru import logger

from app.services.longform.audio_util import (
    concat_pcm_wavs,
    get_ffmpeg_binary,
    measure_wav_duration_and_samples,
)
from app.services.longform.schema import Chapter, ProjectManifest, Segment, TtsGroup


def format_srt_timestamp(seconds: float) -> str:
    """Format seconds into SubRip timestamp format: HH:MM:SS,mmm."""
    sec = max(0.0, float(seconds))
    total_ms = int(round(sec * 1000.0))
    hours = total_ms // (3600 * 1000)
    remainder = total_ms % (3600 * 1000)
    minutes = remainder // (60 * 1000)
    remainder = remainder % (60 * 1000)
    secs = remainder // 1000
    ms = remainder % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into Advanced SubStation Alpha timestamp: H:MM:SS.cc."""
    sec = max(0.0, float(seconds))
    total_cs = int(round(sec * 100.0))
    hours = total_cs // (3600 * 100)
    remainder = total_cs % (3600 * 100)
    minutes = remainder // (60 * 100)
    remainder = remainder % (60 * 100)
    secs = remainder // 100
    cs = remainder % 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def build_chapter_audio_and_timeline(
    project_dir: str | Path,
    chapter: Chapter,
    pause_between_groups_ms: int = 350,
    pause_start_ms: int = 0,
    pause_end_ms: int = 0,
) -> Tuple[Path, float]:
    """Assemble chapter audio by concatenating group WAVs and update absolute segment offsets (3.4, R3).

    Returns: (chapter_wav_path, chapter_duration_seconds).
    """
    p_dir = Path(project_dir).resolve()
    chapter_audio_dir = p_dir / "assets" / "audio" / "chapters"
    words_dir = p_dir / "assets" / "audio" / "words"
    chapter_audio_dir.mkdir(parents=True, exist_ok=True)

    ch_idx = chapter.chapter_index
    chapter_wav = chapter_audio_dir / f"chapter_{ch_idx:02d}.wav"

    group_wav_paths: List[Path] = []
    group_durations: List[float] = []

    for group in chapter.tts_groups:
        wav_path = p_dir / (group.audio.file_path or f"assets/audio/groups/ch{ch_idx}_g{group.group_index}.wav")
        if not wav_path.is_file():
            raise FileNotFoundError(
                f"Chapter {ch_idx} Group {group.group_index} audio file not found: {wav_path}. "
                "Run 'audio synth' first."
            )
        dur, _, _ = measure_wav_duration_and_samples(wav_path)
        group_wav_paths.append(wav_path)
        group_durations.append(dur)

    # Concatenate all groups with exact PCM sample silence
    concat_pcm_wavs(
        wav_paths=group_wav_paths,
        output_wav_path=chapter_wav,
        pause_between_ms=pause_between_groups_ms,
        pause_start_ms=pause_start_ms,
        pause_end_ms=pause_end_ms,
        sample_rate=48000,
        channels=1,
    )

    measured_chapter_dur = round(measure_wav_duration_and_samples(chapter_wav)[0], 3)

    # Compute absolute timeline offsets across groups
    current_time = float(pause_start_ms) / 1000.0

    for idx, (group, g_dur) in enumerate(zip(chapter.tts_groups, group_durations)):
        group_base_time = current_time

        for seg in group.segments:
            rel_start = seg.start_offset_seconds if seg.start_offset_seconds is not None else 0.0
            rel_end = seg.end_offset_seconds if seg.end_offset_seconds is not None else (rel_start + (seg.duration_seconds or 2.0))

            abs_start = round(group_base_time + rel_start, 3)
            abs_end = round(group_base_time + rel_end, 3)

            seg.chapter_start_seconds = abs_start
            seg.chapter_end_seconds = abs_end

        # Update per-word timing sidecar JSON with absolute chapter offsets (R3)
        words_sidecar = words_dir / f"ch{ch_idx}_g{group.group_index}_words.json"
        if words_sidecar.is_file():
            try:
                data = json.loads(words_sidecar.read_text(encoding="utf-8"))
                for w in data.get("words", []):
                    if w.get("group_start") is not None:
                        w["chapter_start"] = round(group_base_time + w["group_start"], 3)
                    if w.get("group_end") is not None:
                        w["chapter_end"] = round(group_base_time + w["group_end"], 3)
                words_sidecar.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                logger.warning(f"Could not update word timing sidecar {words_sidecar}: {exc}")

        # Advance current_time past this group and pause
        current_time += g_dur
        if idx < len(chapter.tts_groups) - 1:
            current_time += (pause_between_groups_ms / 1000.0)

    current_time += (pause_end_ms / 1000.0)

    # Update chapter model
    chapter.audio_file = f"assets/audio/chapters/chapter_{ch_idx:02d}.wav"
    chapter.audio_duration_seconds = measured_chapter_dur

    return chapter_wav, measured_chapter_dur


def burn_preview_mp4(
    preview_dir: Path,
    wav_filename: str,
    ass_filename: str,
    output_mp4_filename: str,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Render a 1280x720 black video with chapter audio and burned subtitles in ASS format (R1).

    Runs with cwd=preview_dir to safely avoid Windows drive letter and path spacing issues in FFmpeg filter graph.
    """
    tmp_mp4 = preview_dir / f"{output_mp4_filename}.tmp.{uuid.uuid4().hex}.mp4"
    out_mp4 = preview_dir / output_mp4_filename

    cmd = [
        ffmpeg_bin,
        "-y",
        "-v", "error",
        "-f", "lavfi",
        "-i", "color=c=black:s=1280x720:r=25",
        "-i", wav_filename,
        "-vf", f"ass={ass_filename}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(tmp_mp4.name),
    ]

    try:
        subprocess.run(
            cmd,
            cwd=str(preview_dir),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if tmp_mp4.exists():
            os.replace(tmp_mp4, out_mp4)
    except subprocess.CalledProcessError as exc:
        if tmp_mp4.exists():
            tmp_mp4.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg burning preview MP4 failed: {exc.stderr}") from exc

    return out_mp4


def generate_chapter_preview_and_srt(
    project_dir: str | Path,
    manifest: ProjectManifest,
    chapter_index: int,
    pause_between_groups_ms: int = 350,
) -> Tuple[Path, Path, Path]:
    """Generate chapter_NN_preview.wav, chapter_NN_preview.srt, and chapter_NN_preview.mp4 in export/preview/ (3.6, R1)."""
    p_dir = Path(project_dir).resolve()
    preview_dir = p_dir / "export" / "preview"
    preview_dir.mkdir(parents=True, exist_ok=True)

    target_chapter: Chapter | None = None
    for ch in manifest.chapters:
        if ch.chapter_index == chapter_index:
            target_chapter = ch
            break

    if target_chapter is None:
        raise ValueError(f"Chapter {chapter_index} not found in project '{manifest.project_id}'.")

    # 1. Build chapter audio & update absolute timeline
    ch_wav, ch_dur = build_chapter_audio_and_timeline(
        project_dir=p_dir,
        chapter=target_chapter,
        pause_between_groups_ms=pause_between_groups_ms,
    )

    # 2. Copy/link to export/preview/
    import shutil
    preview_wav = preview_dir / f"chapter_{chapter_index:02d}_preview.wav"
    shutil.copyfile(ch_wav, preview_wav)

    # 3. Generate SRT subtitle file
    preview_srt = preview_dir / f"chapter_{chapter_index:02d}_preview.srt"
    preview_ass = preview_dir / f"chapter_{chapter_index:02d}_preview.ass"
    srt_lines: List[str] = []
    ass_events: List[str] = []
    srt_counter = 1

    for group in target_chapter.tts_groups:
        for seg in group.segments:
            start_sec = seg.chapter_start_seconds if seg.chapter_start_seconds is not None else 0.0
            end_sec = seg.chapter_end_seconds if seg.chapter_end_seconds is not None else (start_sec + 2.0)

            t_start_srt = format_srt_timestamp(start_sec)
            t_end_srt = format_srt_timestamp(end_sec)

            t_start_ass = format_ass_timestamp(start_sec)
            t_end_ass = format_ass_timestamp(end_sec)

            clean_sub_text = seg.narration_text.strip()

            # SRT entry
            srt_lines.append(str(srt_counter))
            srt_lines.append(f"{t_start_srt} --> {t_end_srt}")
            srt_lines.append(clean_sub_text)
            srt_lines.append("")
            srt_counter += 1

            # ASS event entry
            ass_events.append(
                f"Dialogue: 0,{t_start_ass},{t_end_ass},Default,,0,0,0,,{clean_sub_text}"
            )

    preview_srt.write_text("\n".join(srt_lines).strip() + "\n", encoding="utf-8")

    # Write ASS with fixed PlayResX/PlayResY to guarantee consistent font scaling (R1)
    ass_header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1280\n"
        "PlayResY: 720\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,28,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,40,40,40,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    ass_content = ass_header + "\n".join(ass_events).strip() + "\n"
    preview_ass.write_text(ass_content, encoding="utf-8")

    # 4. Generate burned 720p MP4 preview video (R1)
    ffmpeg_bin = get_ffmpeg_binary()
    preview_mp4 = burn_preview_mp4(
        preview_dir=preview_dir,
        wav_filename=preview_wav.name,
        ass_filename=preview_ass.name,
        output_mp4_filename=f"chapter_{chapter_index:02d}_preview.mp4",
        ffmpeg_bin=ffmpeg_bin,
    )

    target_chapter.preview_video_file = f"export/preview/chapter_{chapter_index:02d}_preview.mp4"

    return preview_wav, preview_srt, preview_mp4
