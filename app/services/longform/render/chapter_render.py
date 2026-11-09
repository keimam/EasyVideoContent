"""Chapter video rendering combining shot clips, chapter WAV, and ASS subtitles (Stage 5.1).

Renders shots in manifest order via concat demuxer, burns ASS subtitles with libass,
encodes in a single pass with locked project encoder settings, and validates post-render duration
and stream parameters.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.longform.audio_preview import format_ass_timestamp
from app.services.longform.config import load_longform_config
from app.services.longform.hashing import compute_chapter_hash
from app.services.longform.render.subtitle import generate_chapter_ass_and_srt
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, Shot

logger = logging.getLogger(__name__)


def escape_ffmpeg_filter_path(path: Path | str) -> str:
    """Escape file path for FFmpeg filter argument (Windows safe)."""
    p_str = str(Path(path).resolve()).replace("\\", "/")
    # Escape colons (e.g. F: -> F\:) and single quotes
    p_str = p_str.replace(":", "\\:").replace("'", "\\'")
    return p_str


def probe_media_file(file_path: Path | str) -> Dict[str, Any]:
    """Probe media file for duration, video streams, audio streams, and stream parameters."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration,size,bit_rate:stream=index,codec_type,codec_name,profile,level,pix_fmt,r_frame_rate,width,height,duration,nb_read_frames",
        "-of", "json",
        str(file_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def get_available_encoders() -> List[str]:
    """Query ffmpeg -encoders to detect available hardware and software video encoders."""
    try:
        res = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True, check=True)
        encoders = []
        for line in res.stdout.splitlines():
            if "V....." in line:
                parts = line.split()
                if len(parts) >= 2:
                    encoders.append(parts[1])
        return encoders
    except Exception as exc:
        logger.warning(f"Failed to query ffmpeg encoders: {exc}")
        return ["libx264"]


def resolve_project_video_encoder(manifest: ProjectManifest) -> Tuple[str, str, int]:
    """Select and lock video encoder, preset, and crf once per project (5.1).

    Priority:
    1. Already locked in manifest.encoder_settings.video_encoder
    2. config/longform.toml [render] section
    3. Default to libx264, preset veryfast, crf 20
    """
    conf = load_longform_config()
    render_conf = conf.get("render", {}) if isinstance(conf, dict) else {}

    default_enc = render_conf.get("video_encoder", "libx264")
    preset = render_conf.get("preset", "veryfast")
    crf = int(render_conf.get("crf", 20))

    current_enc = getattr(manifest.encoder_settings, "video_encoder", None)
    if current_enc and current_enc in ["libx264", "h264_nvenc", "h264_amf"]:
        chosen_enc = current_enc
    else:
        # Check hardware encoders if requested in config
        avail = get_available_encoders()
        if default_enc in ["h264_nvenc", "nvenc"] and "h264_nvenc" in avail:
            chosen_enc = "h264_nvenc"
        elif default_enc in ["h264_amf", "amf"] and "h264_amf" in avail:
            chosen_enc = "h264_amf"
        else:
            chosen_enc = "libx264"

    manifest.encoder_settings.video_encoder = chosen_enc
    return chosen_enc, preset, crf


class ChapterRenderer:
    """Renders chapter video clips into a single MP4 with burned subtitles (5.1)."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)

    def render_chapter(
        self,
        manifest: ProjectManifest,
        chapter_index: int,
        force: bool = False,
    ) -> Tuple[Path, Dict[str, Any]]:
        """Render a single chapter MP4 with concat demuxer, audio WAV, and burned ASS (5.1)."""
        ch = next((c for c in manifest.chapters if c.chapter_index == chapter_index), None)
        if not ch:
            raise ValueError(f"Chapter {chapter_index} not found in project '{manifest.project_id}'")

        # 1. Preflight checks
        if not ch.audio_file:
            raise ValueError(f"Chapter {chapter_index} has no audio_file in manifest")
        audio_wav_path = self.project_dir / ch.audio_file
        if not audio_wav_path.is_file():
            raise FileNotFoundError(f"Chapter {chapter_index} audio WAV file not found: {audio_wav_path}")

        if not ch.shots:
            raise ValueError(f"Chapter {chapter_index} has no shots to render")

        # Check all shot clips exist
        shot_clips: List[Path] = []
        for s in sorted(ch.shots, key=lambda x: x.shot_index):
            if not s.rendered_clip:
                raise ValueError(f"Shot {s.shot_id} has no rendered_clip. Run 'visual build' first.")
            clip_file = self.project_dir / s.rendered_clip
            if not clip_file.is_file():
                raise FileNotFoundError(f"Shot clip not found on disk: {clip_file}")
            shot_clips.append(clip_file)

        # 2. Output paths & directories
        chapters_dir = self.project_dir / "assets" / "visual" / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        rel_output = f"assets/visual/chapters/chapter_{chapter_index:02d}.mp4"
        final_mp4_path = self.project_dir / rel_output
        tmp_mp4_path = chapters_dir / f"chapter_{chapter_index:02d}.tmp.mp4"
        concat_txt_path = chapters_dir / f"concat_ch{chapter_index:02d}.txt"

        # 3. Subtitles generation
        preset_snap = manifest.preset_snapshot or {}
        subtitle_mode = preset_snap.get("subtitle_mode", "sentence_chunks").lower()
        ass_path: Optional[Path] = None
        if subtitle_mode != "off":
            ass_path = chapters_dir / f"chapter_{chapter_index:02d}.ass"
            srt_path = chapters_dir / f"chapter_{chapter_index:02d}.srt"
            generate_chapter_ass_and_srt(
                chapter=ch,
                project_dir=self.project_dir,
                preset_snapshot=preset_snap,
                encoder_settings=manifest.encoder_settings,
                output_ass_path=ass_path,
                output_srt_path=srt_path,
            )

        # 4. Concat demuxer list file
        concat_lines = []
        for clip in shot_clips:
            # Use forward slashes for ffmpeg concat demuxer
            safe_p = str(clip.resolve()).replace("\\", "/")
            concat_lines.append(f"file '{safe_p}'")
        concat_txt_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

        # 5. Resolve encoder settings
        video_enc, preset, crf = resolve_project_video_encoder(manifest)
        fps = float(getattr(manifest.encoder_settings, "fps", 25.0) or 25.0)

        # 6. Build FFmpeg command
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt_path.resolve()),
            "-i", str(audio_wav_path.resolve()),
        ]

        # Video filters (burned ASS subtitle with fontsdir)
        vf_filters = []
        if ass_path and ass_path.is_file():
            fonts_dir = self.project_dir / "resource" / "fonts"
            if not fonts_dir.is_dir():
                fonts_dir = Path("resource/fonts")
            esc_ass = escape_ffmpeg_filter_path(ass_path)
            esc_fonts = escape_ffmpeg_filter_path(fonts_dir)
            vf_filters.append(f"ass=filename='{esc_ass}':fontsdir='{esc_fonts}'")

        if vf_filters:
            cmd.extend(["-vf", ",".join(vf_filters)])

        cmd.extend([
            "-c:v", video_enc,
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-ac", "2",
            str(tmp_mp4_path),
        ])

        # 7. Execute FFmpeg with Ctrl+C protection
        logger.info(f"Rendering chapter {chapter_index} -> {tmp_mp4_path}...")
        t0 = time.perf_counter()
        proc: Optional[subprocess.Popen] = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"FFmpeg failed (code {proc.returncode}):\n{stderr}")

            # Atomic rename on success
            if tmp_mp4_path.is_file():
                if final_mp4_path.is_file():
                    final_mp4_path.unlink()
                os.replace(tmp_mp4_path, final_mp4_path)

        except KeyboardInterrupt:
            logger.warning(f"Render interrupted for Chapter {chapter_index}")
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait()
            if tmp_mp4_path.is_file():
                tmp_mp4_path.unlink(missing_ok=True)
            if concat_txt_path.is_file():
                concat_txt_path.unlink(missing_ok=True)
            raise

        except Exception:
            if tmp_mp4_path.is_file():
                tmp_mp4_path.unlink(missing_ok=True)
            raise

        finally:
            if concat_txt_path.is_file():
                concat_txt_path.unlink(missing_ok=True)

        elapsed = time.perf_counter() - t0

        # 8. Post-render validation (5.1)
        probe_info = probe_media_file(final_mp4_path)
        v_stream = next((s for s in probe_info.get("streams", []) if s.get("codec_type") == "video"), None)
        if not v_stream:
            raise RuntimeError(f"Rendered chapter {chapter_index} has no video stream")

        actual_video_dur = float(probe_info.get("format", {}).get("duration", 0.0))
        audio_probe = probe_media_file(audio_wav_path)
        audio_dur = float(audio_probe.get("format", {}).get("duration", 0.0))

        frame_duration = 1.0 / fps
        allowed_drift = frame_duration + 0.08  # ± 1 frame tolerance
        drift = abs(actual_video_dur - audio_dur)

        if drift > allowed_drift:
            raise ValueError(
                f"Chapter {chapter_index} duration validation failed: "
                f"video duration ({actual_video_dur:.3f}s) differs from audio duration "
                f"({audio_dur:.3f}s) by {drift:.3f}s (exceeds ±1 frame tolerance of {allowed_drift:.3f}s)"
            )

        # 9. Update manifest state & chapter hash
        expected_hash = compute_chapter_hash(
            chapter=ch,
            encoder_settings=manifest.encoder_settings,
            subtitle_style=preset_snap.get("subtitle_style", {}),
            preset_snapshot=preset_snap,
            pipeline_version=manifest.pipeline_version,
            sub_niche_focus=manifest.sub_niche_focus or "",
        )

        ch.output_file = rel_output
        ch.chapter_hash = expected_hash
        ch.status = ItemStatus.completed

        stats = {
            "chapter_index": chapter_index,
            "output_file": rel_output,
            "render_seconds": round(elapsed, 2),
            "video_duration": round(actual_video_dur, 3),
            "audio_duration": round(audio_dur, 3),
            "drift_seconds": round(drift, 4),
            "video_encoder": v_stream.get("codec_name"),
            "width": v_stream.get("width"),
            "height": v_stream.get("height"),
            "pix_fmt": v_stream.get("pix_fmt"),
            "fps": fps,
            "file_size_bytes": final_mp4_path.stat().st_size,
        }
        logger.info(
            f"Chapter {chapter_index} rendered successfully in {elapsed:.2f}s "
            f"({stats['file_size_bytes'] / (1024*1024):.2f} MB, drift: {drift*1000:.1f}ms)"
        )
        return final_mp4_path, stats
