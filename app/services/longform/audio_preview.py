"""Chapter Audio Timeline Assembly and Verification Preview Generator (3.4, 3.6).

Builds:
- Absolute chapter audio timelines combining group WAVs with configurable pauses
- Zero-drift chapter preview WAVs in export/preview/chapter_NN_preview.wav
- Precise SRT subtitle files matching segment absolute timings for human VLC verification
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Tuple

from app.services.longform.audio_util import concat_pcm_wavs, measure_wav_duration_and_samples
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


def build_chapter_audio_and_timeline(
    project_dir: str | Path,
    chapter: Chapter,
    pause_between_groups_ms: int = 350,
    pause_start_ms: int = 0,
    pause_end_ms: int = 0,
) -> Tuple[Path, float]:
    """Assemble chapter audio by concatenating group WAVs and update absolute segment offsets (3.4).

    Returns: (chapter_wav_path, chapter_duration_seconds).
    """
    p_dir = Path(project_dir).resolve()
    chapter_audio_dir = p_dir / "assets" / "audio" / "chapters"
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

        # Advance current_time past this group and pause
        current_time += g_dur
        if idx < len(chapter.tts_groups) - 1:
            current_time += (pause_between_groups_ms / 1000.0)

    current_time += (pause_end_ms / 1000.0)

    # Update chapter model
    chapter.audio_file = f"assets/audio/chapters/chapter_{ch_idx:02d}.wav"
    chapter.audio_duration_seconds = measured_chapter_dur

    return chapter_wav, measured_chapter_dur


def generate_chapter_preview_and_srt(
    project_dir: str | Path,
    manifest: ProjectManifest,
    chapter_index: int,
    pause_between_groups_ms: int = 350,
) -> Tuple[Path, Path]:
    """Generate chapter_NN_preview.wav and chapter_NN_preview.srt in export/preview/ (3.6)."""
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
    srt_lines: List[str] = []
    srt_counter = 1

    for group in target_chapter.tts_groups:
        for seg in group.segments:
            start_sec = seg.chapter_start_seconds if seg.chapter_start_seconds is not None else 0.0
            end_sec = seg.chapter_end_seconds if seg.chapter_end_seconds is not None else (start_sec + 2.0)

            t_start = format_srt_timestamp(start_sec)
            t_end = format_srt_timestamp(end_sec)
            clean_sub_text = seg.narration_text.strip()

            srt_lines.append(str(srt_counter))
            srt_lines.append(f"{t_start} --> {t_end}")
            srt_lines.append(clean_sub_text)
            srt_lines.append("")  # Blank separator
            srt_counter += 1

    preview_srt.write_text("\n".join(srt_lines).strip() + "\n", encoding="utf-8")

    return preview_wav, preview_srt
