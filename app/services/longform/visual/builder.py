"""Shot Clip Builder: FFmpeg-based Ken Burns and Stock Video processing (4.3).

Generates uniform, concatenable H.264 video clips for each shot.
Strictly enforces uniform video streams:
- Resolution: 1920x1080 (16:9 landscape)
- FPS: 25.0 fps
- Codec: libx264, Profile: High, Level: 4.1, Pixel Format: yuv420p
- GOP: fixed GOP = fps (keyint=25, keyint_min=25, no scene cut)
- Exact frame count: target_frames (no drift)
- Ken Burns image processing: 2x pre-scale (3840x2160) to eliminate subpixel jitter
- Stock video processing: 0.5s intro trim, cover scaling, max 1.25x slowdown if short
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from app.services.longform.schema import MotionType, Shot
from app.services.longform.visual.base import VisualError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def _get_video_duration(path: Path) -> float:
    """Read media duration via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return 0.0


def verify_clip_stream_parameters(
    clip_path: Path,
    expected_frames: int,
    expected_fps: int = 25,
) -> Dict[str, Any]:
    """Verify generated clip stream parameters with ffprobe to ensure uniform concatability."""
    probe_cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,profile,level,pix_fmt,r_frame_rate,nb_read_frames,width,height",
        "-count_frames",
        "-of", "json",
        str(clip_path),
    ]
    res = subprocess.run(probe_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise VisualError(f"ffprobe verification failed for {clip_path}: {res.stderr}")

    data = json.loads(res.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise VisualError(f"No video streams found in {clip_path}")

    st = streams[0]
    codec = st.get("codec_name")
    profile = st.get("profile")
    pix_fmt = st.get("pix_fmt")
    width = st.get("width")
    height = st.get("height")
    read_frames = int(st.get("nb_read_frames") or 0)

    # Basic validations
    if codec != "h264":
        raise VisualError(f"Stream codec mismatch in {clip_path}: expected h264, got {codec}")
    if (width, height) != (1920, 1080):
        raise VisualError(f"Stream resolution mismatch in {clip_path}: expected 1920x1080, got {width}x{height}")
    if pix_fmt not in ("yuv420p", "yuvj420p"):
        raise VisualError(f"Stream pixel format mismatch in {clip_path}: expected yuv420p, got {pix_fmt}")
    if abs(read_frames - expected_frames) > 1:
        raise VisualError(
            f"Stream frame count drift in {clip_path}: expected {expected_frames}, got {read_frames}"
        )

    return {
        "codec": codec,
        "profile": profile,
        "pix_fmt": pix_fmt,
        "resolution": f"{width}x{height}",
        "read_frames": read_frames,
    }


def trim_source_video_asset(
    asset_path: Path,
    needed_duration_seconds: float,
) -> bool:
    """Trim source video file to needed duration (+ margin) to save project disk space (S2)."""
    p = Path(asset_path).resolve()
    if not p.is_file():
        return False
    raw_dur = _get_video_duration(p)
    if raw_dur <= (needed_duration_seconds + 1.0):
        return False

    tmp_trim = p.parent / f"{p.stem}.trim_tmp.{uuid.uuid4().hex}.mp4"
    # Fast stream copy first
    cmd = [
        "ffmpeg", "-y",
        "-ss", "0",
        "-i", str(p),
        "-t", str(needed_duration_seconds),
        "-c", "copy",
        str(tmp_trim),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not tmp_trim.is_file() or tmp_trim.stat().st_size == 0:
        # Fallback to fast re-encode
        cmd2 = [
            "ffmpeg", "-y",
            "-ss", "0",
            "-i", str(p),
            "-t", str(needed_duration_seconds),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "copy",
            str(tmp_trim),
        ]
        res2 = subprocess.run(cmd2, capture_output=True, text=True)
        if res2.returncode != 0 or not tmp_trim.is_file() or tmp_trim.stat().st_size == 0:
            if tmp_trim.exists():
                tmp_trim.unlink(missing_ok=True)
            return False

    os.replace(tmp_trim, p)
    logger.debug(f"Trimmed source asset {p.name} from {raw_dur:.1f}s to {needed_duration_seconds:.1f}s")
    return True


class ShotClipBuilder:
    """Builds rendered shot video clips with FFmpeg (4.3)."""

    def __init__(
        self,
        fps: int = 25,
        width: int = 1920,
        height: int = 1080,
    ) -> None:
        self.fps = fps
        self.width = width
        self.height = height

    def _get_motion_filter(self, motion: MotionType, target_frames: int) -> str:
        """Construct jitter-free zoompan filter expression for static images."""
        d = target_frames
        if motion == MotionType.zoom_out:
            return (
                f"zoompan=z='if(eq(on,0),1.25,max(1.001,zoom-0.0015))':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s={self.width}x{self.height}:fps={self.fps}"
            )
        elif motion == MotionType.pan_left:
            return (
                f"zoompan=z='1.15':"
                f"x='(1-on/{d})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':d={d}:s={self.width}x{self.height}:fps={self.fps}"
            )
        elif motion == MotionType.pan_right:
            return (
                f"zoompan=z='1.15':"
                f"x='(on/{d})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':d={d}:s={self.width}x{self.height}:fps={self.fps}"
            )
        elif motion == MotionType.static:
            return (
                f"zoompan=z='1.0':"
                f"x='0':y='0':d={d}:s={self.width}x{self.height}:fps={self.fps}"
            )
        else:  # zoom_in default
            return (
                f"zoompan=z='min(zoom+0.0015,1.25)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={d}:s={self.width}x{self.height}:fps={self.fps}"
            )

    def build_image_clip(
        self,
        image_path: Path,
        output_path: Path,
        motion: MotionType,
        target_frames: int,
    ) -> Tuple[Path, float]:
        """Convert static image into Ken Burns animated video clip (4.3)."""
        out = Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.parent / f"{out.stem}.tmp.{uuid.uuid4().hex}.mp4"

        # 2x pre-scale to eliminate subpixel jitter + safe crop to 16:9
        prescale_w = self.width * 2
        prescale_h = self.height * 2
        motion_expr = self._get_motion_filter(motion, target_frames)

        # Filtergraph pipeline:
        # 1. scale & crop to 3840x2160 (2x)
        # 2. zoompan to 1920x1080
        # 3. setsar=1
        # 4. full to tv range conversion & format=yuv420p
        vf = (
            f"scale={prescale_w}:{prescale_h}:force_original_aspect_ratio=increase,"
            f"crop={prescale_w}:{prescale_h},"
            f"{motion_expr},"
            f"setsar=1,"
            f"scale=in_range=full:out_range=tv,"
            f"format=yuv420p"
        )

        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(self.fps),
            "-loop", "1",
            "-i", str(image_path),
            "-vf", vf,
            "-frames:v", str(target_frames),
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level:v", "4.1",
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            "-g", str(self.fps),
            "-keyint_min", str(self.fps),
            "-sc_threshold", "0",
            "-an",
            str(tmp_out),
        ]

        t0 = time.perf_counter()
        res = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.perf_counter() - t0

        if res.returncode != 0 or not tmp_out.is_file():
            if tmp_out.exists():
                tmp_out.unlink(missing_ok=True)
            raise VisualError(f"FFmpeg Ken Burns image clip failed ({res.returncode}): {res.stderr[-500:]}")

        os.replace(tmp_out, out)
        verify_clip_stream_parameters(out, target_frames, self.fps)
        return out, elapsed

    def build_video_clip(
        self,
        video_path: Path,
        output_path: Path,
        target_frames: int,
    ) -> Tuple[Path, float]:
        """Process stock video into standardized clip (trim 0.5s, cover crop, slowdown if needed) (4.3)."""
        out = Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = out.parent / f"{out.stem}.tmp.{uuid.uuid4().hex}.mp4"

        target_duration = target_frames / self.fps
        # S2: Trim source video asset if longer than target duration + 1.5s margin
        trim_source_video_asset(video_path, target_duration + 1.5)

        raw_dur = _get_video_duration(video_path)
        usable_dur = max(raw_dur - 0.5, 0.5)

        # Check if video duration is slightly short -> apply slowdown up to 1.25x
        pts_filter = ""
        if usable_dur < target_duration and usable_dur >= (target_duration / 1.25):
            slowdown_ratio = target_duration / usable_dur
            logger.info(f"Applying subtle {slowdown_ratio:.2f}x slowdown to stock video {video_path.name}")
            pts_filter = f"setpts={slowdown_ratio}*PTS,"

        vf = (
            f"{pts_filter}"
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height},"
            f"setsar=1,"
            f"format=yuv420p"
        )

        cmd = [
            "ffmpeg", "-y",
            "-ss", "0.5",
            "-i", str(video_path),
            "-vf", vf,
            "-frames:v", str(target_frames),
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level:v", "4.1",
            "-pix_fmt", "yuv420p",
            "-r", str(self.fps),
            "-g", str(self.fps),
            "-keyint_min", str(self.fps),
            "-sc_threshold", "0",
            "-an",
            str(tmp_out),
        ]

        t0 = time.perf_counter()
        res = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.perf_counter() - t0

        if res.returncode != 0 or not tmp_out.is_file():
            if tmp_out.exists():
                tmp_out.unlink(missing_ok=True)
            raise VisualError(f"FFmpeg stock video clip failed ({res.returncode}): {res.stderr[-500:]}")

        os.replace(tmp_out, out)
        verify_clip_stream_parameters(out, target_frames, self.fps)
        return out, elapsed

    def build_shot_clip(
        self,
        asset_path: Path,
        output_path: Path,
        motion: MotionType,
        target_frames: int,
    ) -> Tuple[Path, float]:
        """Detect media type and build standardized shot clip."""
        p = Path(asset_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Shot asset file missing: {p}")

        ext = p.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            return self.build_video_clip(p, output_path, target_frames)
        else:
            return self.build_image_clip(p, output_path, motion, target_frames)
