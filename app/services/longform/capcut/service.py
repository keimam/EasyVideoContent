"""High-level CapCut service coordinating doctor checks and smoke draft creation."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.longform.capcut.builder import CapCutDraftBuilder
from app.services.longform.capcut.detector import (
    detect_draft_folder,
    get_capcut_doctor_report,
    is_capcut_running,
)
from app.services.longform.store import get_project_dir, load_manifest


def parse_srt_entries(srt_path: Path, max_entries: int = 2) -> list[dict[str, Any]]:
    """Parse up to max_entries from an SRT file into dicts with text and timing in seconds."""
    if not srt_path.is_file():
        return []

    content = srt_path.read_text(encoding="utf-8", errors="ignore")
    # Pattern for SRT blocks: index, timestamp --> timestamp, text lines
    pattern = re.compile(
        r"(\d+)\s*\r?\n(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\r?\n(.*?)(?=\r?\n\r?\n|\Z)",
        re.DOTALL,
    )

    entries = []
    for match in pattern.finditer(content):
        (
            idx,
            sh, sm, ss, sms,
            eh, em, es, ems,
            text_block,
        ) = match.groups()

        start_s = (
            int(sh) * 3600
            + int(sm) * 60
            + int(ss)
            + int(sms) / 1000.0
        )
        end_s = (
            int(eh) * 3600
            + int(em) * 60
            + int(es)
            + int(ems) / 1000.0
        )
        dur_s = max(0.1, end_s - start_s)
        clean_text = " ".join(line.strip() for line in text_block.splitlines() if line.strip())

        entries.append({
            "text": clean_text,
            "start_seconds": start_s,
            "duration_seconds": dur_s,
        })
        if len(entries) >= max_entries:
            break

    return entries


def build_smoke_draft(project_id: str, install: bool = False) -> dict[str, Any]:
    """
    Build smoke draft 'MPT_SMOKE_<id>' from project assets:
    - 3 first shots of Chapter 1
    - Audio trimmed to the duration of those 3 shots
    - 2 subtitle entries from Chapter 1

    :param project_id: Project identifier (e.g. uji_nokey)
    :param install: If True, install draft into CapCut draft folder (when CapCut is closed)
    :return: Summary dictionary
    """
    project_dir = get_project_dir(project_id)
    manifest = load_manifest(project_dir)

    if not manifest.chapters:
        raise ValueError(f"Project '{project_id}' has no chapters in manifest.")

    ch1 = manifest.chapters[0]
    if len(ch1.shots) < 3:
        raise ValueError(f"Chapter 1 has fewer than 3 shots (found {len(ch1.shots)}).")

    shots = ch1.shots[:3]

    # Resolve video clip files
    video_clips = []
    total_shots_duration = 0.0
    for s in shots:
        clip_rel = s.rendered_clip or s.asset_path
        if not clip_rel:
            raise FileNotFoundError(f"Shot '{s.shot_id}' has no rendered_clip or asset_path.")
        clip_path = project_dir / clip_rel
        if not clip_path.is_file():
            # Try alternate fallback if path relative to project
            alt_path = project_dir / "assets" / "visual" / "shots" / f"{s.shot_id}.mp4"
            if alt_path.is_file():
                clip_path = alt_path
            else:
                raise FileNotFoundError(f"Clip file not found: {clip_path}")

        dur = s.duration_seconds or 5.0
        total_shots_duration += dur
        video_clips.append({
            "path": clip_path,
            "duration_seconds": dur,
        })

    # Resolve chapter 1 audio and trim to total_shots_duration
    if not ch1.audio_file:
        raise FileNotFoundError("Chapter 1 has no audio_file recorded in manifest.")
    ch1_audio_path = project_dir / ch1.audio_file
    if not ch1_audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {ch1_audio_path}")

    temp_audio_dir = Path(tempfile.mkdtemp(prefix="mpt_capcut_audio_"))
    trimmed_audio_path = temp_audio_dir / "audio_ch01_trimmed.wav"

    try:
        trim_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(ch1_audio_path),
            "-t",
            f"{total_shots_duration:.3f}",
            "-c:a",
            "pcm_s16le",
            str(trimmed_audio_path),
        ]
        res = subprocess.run(trim_cmd, capture_output=True, text=True, check=False, timeout=15)
        if res.returncode != 0 or not trimmed_audio_path.is_file():
            raise RuntimeError(f"FFmpeg audio trim failed: {res.stderr}")
    except Exception as exc:
        shutil.rmtree(temp_audio_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to trim audio for smoke draft: {exc}") from exc

    audio_clip = {
        "path": trimmed_audio_path,
        "duration_seconds": total_shots_duration,
    }

    # Resolve 2 subtitles from preview SRT or project subtitles
    srt_candidates = [
        project_dir / "export" / "preview" / "chapter_01_preview.srt",
        project_dir / "export" / "subtitles" / "full_timeline.srt",
    ]
    subtitles = []
    for sc in srt_candidates:
        if sc.is_file():
            subtitles = parse_srt_entries(sc, max_entries=2)
            if subtitles:
                break

    if not subtitles:
        # Fallback to first 2 segment texts if SRT not rendered yet
        subtitles = [
            {"text": "Subtitle Preview 1", "start_seconds": 0.0, "duration_seconds": 5.0},
            {"text": "Subtitle Preview 2", "start_seconds": 5.0, "duration_seconds": 5.0},
        ]

    draft_name = f"MPT_SMOKE_{project_id}"
    export_draft_dir = project_dir / "export" / "capcut" / draft_name

    builder = CapCutDraftBuilder()
    builder.build_draft(
        output_dir=export_draft_dir,
        draft_name=draft_name,
        video_clips=video_clips,
        audio_clip=audio_clip,
        subtitles=subtitles,
    )

    # Clean up temp audio dir
    shutil.rmtree(temp_audio_dir, ignore_errors=True)

    installed_path = None
    if install:
        if is_capcut_running():
            raise RuntimeError(
                "CapCut is currently running! Please completely close CapCut before installing drafts to prevent draft corruption."
            )

        draft_root = detect_draft_folder()
        if not draft_root or not draft_root.is_dir():
            raise RuntimeError("Could not locate CapCut draft directory (com.lveditor.draft).")

        target_install_dir = draft_root / draft_name
        # Safe protection: Only allow writing into exact draft_name MPT_SMOKE_<id>
        if target_install_dir.name != draft_name:
            raise RuntimeError(f"Security check failed: target install dir name mismatch ({target_install_dir.name})")

        # Build directly or copy and adjust paths for installed destination
        builder.build_draft(
            output_dir=target_install_dir,
            draft_name=draft_name,
            video_clips=video_clips,
            audio_clip={
                "path": export_draft_dir / "media" / "audio_track.wav",
                "duration_seconds": total_shots_duration,
            },
            subtitles=subtitles,
            draft_root_path=draft_root.resolve().as_posix(),
        )
        installed_path = str(target_install_dir)
        logger.info(f"Draft installed to CapCut directory: {installed_path}")

    return {
        "project_id": project_id,
        "draft_name": draft_name,
        "export_path": str(export_draft_dir),
        "installed": install,
        "installed_path": installed_path,
        "shots_count": len(video_clips),
        "total_duration_seconds": total_shots_duration,
        "subtitles_count": len(subtitles),
    }
