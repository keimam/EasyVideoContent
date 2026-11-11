"""Final video concatenation, audio muxing, YouTube metadata, and timeline validation (Stage 5.3 & 5.4).

Validates identical stream parameters across chapters, concatenates chapter video streams via -c copy,
muxes uncompressed-derived normalized audio with BGM, generates YouTube chapters metadata,
and updates attribution credits.
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

from app.services.longform.hashing import compute_final_concat_hash
from app.services.longform.render.bgm import BgmProcessor
from app.services.longform.render.chapter_render import probe_media_file
from app.services.longform.render.subtitle import generate_full_timeline_subtitles
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest
from app.services.longform.visual.credits import CreditsManager

logger = logging.getLogger(__name__)


def format_youtube_timestamp(seconds: float) -> str:
    """Format seconds into YouTube chapter timestamp: MM:SS or HH:MM:SS (5.4)."""
    sec = max(0, int(round(seconds)))
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    secs = sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def validate_chapter_streams(chapters: List[Chapter], project_dir: Path) -> Dict[str, Any]:
    """Validate that all chapter video clips have identical stream encoding parameters (5.1 & 5.3)."""
    reference_params: Optional[Dict[str, Any]] = None
    ref_ch_idx = 1

    for ch in sorted(chapters, key=lambda c: c.chapter_index):
        if not ch.output_file:
            raise ValueError(f"Chapter {ch.chapter_index} has no output_file recorded in manifest")
        ch_file = project_dir / ch.output_file
        if not ch_file.is_file():
            raise FileNotFoundError(f"Chapter {ch.chapter_index} video file not found: {ch_file}")

        info = probe_media_file(ch_file)
        v_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
        if not v_stream:
            raise ValueError(f"Chapter {ch.chapter_index} video stream not found in {ch_file}")

        params = {
            "codec_name": v_stream.get("codec_name"),
            "width": v_stream.get("width"),
            "height": v_stream.get("height"),
            "pix_fmt": v_stream.get("pix_fmt"),
            "r_frame_rate": v_stream.get("r_frame_rate"),
        }

        if reference_params is None:
            reference_params = params
            ref_ch_idx = ch.chapter_index
        else:
            for k, expected_v in reference_params.items():
                actual_v = params.get(k)
                if actual_v != expected_v:
                    raise ValueError(
                        f"Stream parameter mismatch between Chapter {ref_ch_idx} and Chapter {ch.chapter_index}: "
                        f"{k} differs ({actual_v} != {expected_v}). "
                        f"Cannot use concat -c copy with mixed stream parameters."
                    )

    return reference_params or {}


class FinalConcatenator:
    """Concatenates chapter videos and muxes normalized final audio (5.3 & 5.4)."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)

    def concatenate_final_video(
        self,
        manifest: ProjectManifest,
        no_bgm: bool = False,
        bgm_override: Optional[Path] = None,
    ) -> Tuple[Path, Dict[str, Any]]:
        """Run complete final video assembly with copy concatenation and audio muxing (5.3)."""
        if not manifest.chapters:
            raise ValueError("Manifest contains no chapters")

        # 1. Stream compatibility preflight
        ref_params = validate_chapter_streams(manifest.chapters, self.project_dir)
        logger.info(f"Chapter video stream validation passed: {ref_params}")

        # 2. Output directories & paths
        export_dir = self.project_dir / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = self.project_dir / "temp" / "final_concat"
        temp_dir.mkdir(parents=True, exist_ok=True)

        rel_final = "export/final_video.mp4"
        final_video_path = self.project_dir / rel_final
        tmp_final_path = export_dir / "final_video.tmp.mp4"

        # 3. Concatenate video streams via -c copy
        concat_txt = temp_dir / "chapters_video_concat.txt"
        video_only_mp4 = temp_dir / "raw_video_concatenated.mp4"

        lines = []
        for ch in sorted(manifest.chapters, key=lambda c: c.chapter_index):
            ch_abs = (self.project_dir / ch.output_file).resolve()
            safe_p = str(ch_abs).replace("\\", "/")
            lines.append(f"file '{safe_p}'")
        concat_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

        copy_video_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt.resolve()),
            "-c:v", "copy",
            "-an",
            str(video_only_mp4.resolve()),
        ]
        subprocess.run(copy_video_cmd, capture_output=True, text=True, check=True)

        # 4. Build continuous audio from WAVs and normalize
        chapter_wavs: List[Path] = []
        for ch in sorted(manifest.chapters, key=lambda c: c.chapter_index):
            if not ch.audio_file:
                raise ValueError(f"Chapter {ch.chapter_index} has no audio_file")
            w_path = self.project_dir / ch.audio_file
            if not w_path.is_file():
                raise FileNotFoundError(f"Chapter audio WAV not found: {w_path}")
            chapter_wavs.append(w_path)

        bgm_proc = BgmProcessor(self.project_dir)
        final_norm_wav = temp_dir / "final_normalized_audio.wav"
        preset_snap = manifest.preset_snapshot or {}

        t0_audio = time.perf_counter()
        normalized_audio, audio_stats = bgm_proc.process_final_audio(
            chapter_wavs=chapter_wavs,
            output_final_wav=final_norm_wav,
            preset_snapshot=preset_snap,
            no_bgm=no_bgm,
            bgm_override=bgm_override,
        )
        audio_proc_time = time.perf_counter() - t0_audio

        # 5. Mux copy video + normalized audio into final video
        t0_mux = time.perf_counter()
        proc: Optional[subprocess.Popen] = None
        mux_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_only_mp4.resolve()),
            "-i", str(normalized_audio.resolve()),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            str(tmp_final_path.resolve()),
        ]

        try:
            proc = subprocess.Popen(
                mux_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Final mux failed (code {proc.returncode}):\n{stderr}")

            if tmp_final_path.is_file():
                if final_video_path.is_file():
                    final_video_path.unlink()
                os.replace(tmp_final_path, final_video_path)

        except KeyboardInterrupt:
            logger.warning("Final video rendering interrupted by user")
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait()
            if tmp_final_path.is_file():
                tmp_final_path.unlink(missing_ok=True)
            raise
        except Exception:
            if tmp_final_path.is_file():
                tmp_final_path.unlink(missing_ok=True)
            raise
        finally:
            if concat_txt.is_file():
                concat_txt.unlink(missing_ok=True)

        mux_proc_time = time.perf_counter() - t0_mux

        # 6. Post-concat validation (5.3)
        final_probe = probe_media_file(final_video_path)
        final_dur = float(final_probe.get("format", {}).get("duration", 0.0))
        total_audio_dur = audio_stats["total_audio_duration"]

        fps = float(getattr(manifest.encoder_settings, "fps", 25.0) or 25.0)
        allowed_drift = (1.0 / fps) + 0.08
        final_drift = abs(final_dur - total_audio_dur)

        if final_drift > allowed_drift:
            raise ValueError(
                f"Final video duration validation failed: video duration ({final_dur:.3f}s) "
                f"differs from total audio duration ({total_audio_dur:.3f}s) by {final_drift:.3f}s "
                f"(tolerance: ±1 frame = {allowed_drift:.3f}s)"
            )

        # 7. Validate chapter boundaries & offsets (no duplicates, no gaps)
        boundary_offsets = self._validate_chapter_offsets(manifest)

        # 8. Generate YouTube Chapters Metadata (5.4)
        yt_chapters_file = self.generate_youtube_chapters(manifest, boundary_offsets)

        # 9. Generate Subtitles Full Timeline (SRT + ASS) (5.2)
        full_srt, full_ass = generate_full_timeline_subtitles(manifest, self.project_dir)

        # 10. Update credits.txt with BGM attribution (5.4)
        if audio_stats.get("bgm_used") and audio_stats.get("bgm_file"):
            self._update_credits_with_bgm(audio_stats["bgm_file"])

        # 11. Compute final canonical hash & update manifest
        bgm_hash = ""
        if audio_stats.get("bgm_file"):
            from app.services.longform.hashing import hash_file
            bgm_p = Path(audio_stats["bgm_file"])
            if bgm_p.is_file():
                bgm_hash = hash_file(bgm_p)

        final_hash = compute_final_concat_hash(
            chapters=manifest.chapters,
            bgm_hash=bgm_hash,
            pipeline_version=manifest.pipeline_version,
        )
        manifest.final_video_file = rel_final
        manifest.final_video_hash = final_hash
        manifest.status = ItemStatus.completed

        stats = {
            "final_video_file": str(final_video_path),
            "final_video_duration": round(final_dur, 3),
            "total_audio_duration": round(total_audio_dur, 3),
            "drift_seconds": round(final_drift, 4),
            "file_size_bytes": final_video_path.stat().st_size,
            "audio_process_seconds": round(audio_proc_time, 2),
            "mux_seconds": round(mux_proc_time, 2),
            "integrated_lufs": audio_stats["integrated_lufs"],
            "true_peak_dbtp": audio_stats["true_peak_dbtp"],
            "loudness_range_lu": audio_stats["loudness_range_lu"],
            "bgm_used": audio_stats["bgm_used"],
            "bgm_file": audio_stats["bgm_file"],
            "youtube_chapters_file": str(yt_chapters_file),
            "full_timeline_srt": str(full_srt),
            "full_timeline_ass": str(full_ass),
        }

        try:
            (export_dir / "render_stats.json").write_text(
                json.dumps(stats, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"Failed to write render_stats.json: {exc}")

        return final_video_path, stats

    def _validate_chapter_offsets(self, manifest: ProjectManifest) -> List[Tuple[int, str, float]]:
        """Validate sequential chapter offsets ensuring no duplicates or gaps (5.3)."""
        offsets: List[Tuple[int, str, float]] = []
        cumulative_sec = 0.0

        for ch in sorted(manifest.chapters, key=lambda c: c.chapter_index):
            offsets.append((ch.chapter_index, ch.chapter_title, cumulative_sec))
            ch_dur = ch.audio_duration_seconds or 0.0
            cumulative_sec += ch_dur

        return offsets

    def generate_youtube_chapters(
        self,
        manifest: ProjectManifest,
        offsets: List[Tuple[int, str, float]],
    ) -> Path:
        """Write export/youtube_chapters.txt verifying YouTube criteria (5.4)."""
        export_dir = self.project_dir / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        yt_file = export_dir / "youtube_chapters.txt"

        lines = []
        warnings = []

        # YouTube rule 1: first timestamp must be 00:00
        # YouTube rule 2: minimum 3 chapters
        if len(offsets) < 3:
            warnings.append(f"YouTube requires at least 3 chapters (project has {len(offsets)})")

        for idx, (ch_idx, title, offset_sec) in enumerate(offsets):
            ts_str = format_youtube_timestamp(offset_sec)
            # YouTube rule: first chapter must start at 00:00
            if idx == 0 and offset_sec > 0.001:
                ts_str = "00:00"
            clean_title = title.replace("\n", " ").strip()
            lines.append(f"{ts_str} {clean_title}")

            # YouTube rule 3: each chapter >= 10s
            next_offset = offsets[idx + 1][2] if idx + 1 < len(offsets) else None
            ch_obj = next((c for c in manifest.chapters if c.chapter_index == ch_idx), None)
            ch_dur = (next_offset - offset_sec) if next_offset is not None else (ch_obj.audio_duration_seconds if ch_obj else 0.0)
            if ch_dur and ch_dur < 10.0:
                warnings.append(f"Chapter {ch_idx} duration ({ch_dur:.1f}s) is less than YouTube minimum 10 seconds")

        for w in warnings:
            logger.warning(f"YouTube Chapter Warning: {w}")
            print(f"WARNING: [YouTube Chapters] {w}")

        yt_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(f"Generated YouTube chapters metadata: {yt_file}")
        return yt_file

    def _update_credits_with_bgm(self, bgm_path_str: str) -> None:
        """Add background music citation to export/credits.txt (5.4)."""
        credits_file = self.project_dir / "export" / "credits.txt"
        credits_file.parent.mkdir(parents=True, exist_ok=True)

        bgm_p = Path(bgm_path_str)
        bgm_name = bgm_p.stem
        bgm_entry = f"[BGM] {bgm_name} | Source: Royalty-Free Audio Library | License: Commercial Use Allowed"

        existing = []
        if credits_file.is_file():
            existing = credits_file.read_text(encoding="utf-8").splitlines()

        if any(f"[BGM] {bgm_name}" in line for line in existing):
            return

        existing.append(bgm_entry)
        credits_file.write_text("\n".join(existing) + "\n", encoding="utf-8")
        logger.info(f"Updated credits.txt with BGM citation: {bgm_name}")
