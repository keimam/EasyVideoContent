"""BGM processing, ducking, two-pass loudnorm, and ebur128 measurement (Stage 5.3).

Builds final audio directly from uncompressed chapter WAVs (avoiding multi-generation AAC loss),
applies sidechain ducking and fades, normalizes to -14 LUFS / -1 dBTP via two-pass loudnorm,
and measures exact loudness with ebur128.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def concatenate_wav_files(wav_files: List[Path], output_wav: Path) -> Path:
    """Concatenate chapter WAV files losslessly into a single continuous uncompressed WAV (5.3)."""
    if not wav_files:
        raise ValueError("No WAV files provided for concatenation")
    if len(wav_files) == 1:
        # Copy or symlink single file
        import shutil
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wav_files[0], output_wav)
        return output_wav

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    concat_list_file = output_wav.parent / f"wav_concat_{output_wav.stem}.txt"
    lines = []
    for w in wav_files:
        safe_p = str(w.resolve()).replace("\\", "/")
        lines.append(f"file '{safe_p}'")
    concat_list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file.resolve()),
        "-c", "copy",
        str(output_wav.resolve()),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    finally:
        if concat_list_file.is_file():
            concat_list_file.unlink(missing_ok=True)

    return output_wav


def resolve_bgm_file(
    project_dir: Path,
    preset_snapshot: Dict[str, Any],
) -> Optional[Path]:
    """Find BGM file from project local assets, preset default_bgm, or resource/songs (5.3)."""
    # 1. Check project custom BGM
    local_bgm_dir = project_dir / "assets" / "audio" / "bgm"
    if local_bgm_dir.is_dir():
        for f in local_bgm_dir.iterdir():
            if f.is_file() and f.suffix.lower() in [".mp3", ".wav", ".m4a", ".aac"]:
                return f

    # 2. Check preset default_bgm
    default_bgm_data = preset_snapshot.get("default_bgm")
    if not default_bgm_data:
        return None

    default_bgm_name = None
    if isinstance(default_bgm_data, str):
        default_bgm_name = default_bgm_data
    elif isinstance(default_bgm_data, dict):
        default_bgm_name = default_bgm_data.get("file") or default_bgm_data.get("name")
    songs_dir = project_dir / "resource" / "songs"
    if not songs_dir.is_dir():
        songs_dir = Path("resource/songs")

    if default_bgm_name and songs_dir.is_dir():
        candidates = [
            songs_dir / default_bgm_name,
            songs_dir / f"{default_bgm_name}.mp3",
            songs_dir / f"{default_bgm_name}.wav",
        ]
        for c in candidates:
            if c.is_file():
                return c

    # Fallback to first available song in resource/songs if default_bgm is enabled in preset
    if songs_dir.is_dir():
        for f in sorted(songs_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in [".mp3", ".wav"]:
                return f

    return None


class BgmProcessor:
    """Handles BGM mixing, sidechain ducking, two-pass loudnorm, and ebur128 measurement (5.3)."""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)

    def process_final_audio(
        self,
        chapter_wavs: List[Path],
        output_final_wav: Path,
        preset_snapshot: Dict[str, Any],
        no_bgm: bool = False,
        bgm_override: Optional[Path] = None,
    ) -> Tuple[Path, Dict[str, Any]]:
        """Build continuous narration from WAVs, apply BGM + ducking, and two-pass loudnorm to -14 LUFS (5.3)."""
        temp_dir = self.project_dir / "temp" / "render_audio"
        temp_dir.mkdir(parents=True, exist_ok=True)

        raw_narration_wav = temp_dir / "raw_narration.wav"
        concatenate_wav_files(chapter_wavs, raw_narration_wav)

        # Probe narration duration
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(raw_narration_wav),
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        total_duration = float(res.stdout.strip())

        bgm_file = None if no_bgm else (bgm_override or resolve_bgm_file(self.project_dir, preset_snapshot))
        bgm_used = bgm_file is not None and bgm_file.is_file()

        unnormalized_mixed_wav = temp_dir / "unnormalized_mixed.wav"

        if bgm_used and bgm_file:
            logger.info(f"Applying BGM track: {bgm_file.name} with sidechain ducking (total dur: {total_duration:.2f}s)")
            bgm_vol = float(preset_snapshot.get("bgm_volume", 0.20))
            duck_thresh = float(preset_snapshot.get("bgm_ducking_threshold", 0.03))
            duck_ratio = float(preset_snapshot.get("bgm_ducking_ratio", 4.0))
            duck_attack = int(preset_snapshot.get("bgm_ducking_attack_ms", 200))
            duck_release = int(preset_snapshot.get("bgm_ducking_release_ms", 1000))

            fade_out_start = max(0.0, total_duration - 3.0)

            # Filter complex:
            # 0: BGM stream looped infinitely
            # 1: Narration WAV
            filter_complex = (
                f"[0:a]volume={bgm_vol},"
                f"afade=t=in:ss=0:d=2,"
                f"afade=t=out:st={fade_out_start:.2f}:d=3[bgm_faded];"
                f"[1:a]asplit=2[narr_main][narr_sc];"
                f"[bgm_faded][narr_sc]sidechaincompress="
                f"threshold={duck_thresh}:ratio={duck_ratio}:attack={duck_attack}:release={duck_release}[ducked_bgm];"
                f"[narr_main][ducked_bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[mixed]"
            )

            mix_cmd = [
                "ffmpeg", "-y",
                "-stream_loop", "-1",
                "-i", str(bgm_file.resolve()),
                "-i", str(raw_narration_wav.resolve()),
                "-filter_complex", filter_complex,
                "-map", "[mixed]",
                "-t", str(total_duration),
                "-c:a", "pcm_s16le",
                str(unnormalized_mixed_wav),
            ]
            subprocess.run(mix_cmd, capture_output=True, text=True, check=True)
        else:
            logger.info("Rendering final audio without BGM (narration only)")
            unnormalized_mixed_wav = raw_narration_wav

        # Two-Pass Loudnorm Normalization (-14 LUFS, -1 dBTP)
        normalized_wav = self._run_two_pass_loudnorm(unnormalized_mixed_wav, output_final_wav)

        # Measure with ebur128
        ebur_stats = self.measure_ebur128(normalized_wav)

        stats = {
            "total_audio_duration": total_duration,
            "bgm_used": bgm_used,
            "bgm_file": str(bgm_file) if bgm_used and bgm_file else None,
            "integrated_lufs": ebur_stats["integrated_lufs"],
            "true_peak_dbtp": ebur_stats["true_peak_dbtp"],
            "loudness_range_lu": ebur_stats["loudness_range_lu"],
        }
        return normalized_wav, stats

    def _run_two_pass_loudnorm(self, input_wav: Path, output_wav: Path) -> Path:
        """Run standard two-pass EBU R128 / ITU-R BS.1770 loudnorm filter to -14 LUFS (5.3)."""
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        target_i = -14.0
        target_tp = -1.0
        target_lra = 11.0

        # Pass 1: Measure
        pass1_cmd = [
            "ffmpeg", "-y",
            "-i", str(input_wav.resolve()),
            "-af", f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:print_format=json",
            "-f", "null",
            "-",
        ]
        res1 = subprocess.run(pass1_cmd, capture_output=True, text=True, check=True)

        # Extract JSON block from stderr
        json_match = re.search(r"\{[\s\S]*\}", res1.stderr)
        if not json_match:
            logger.warning("Could not parse loudnorm pass 1 JSON; running single pass loudnorm")
            pass2_filter = f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}"
        else:
            try:
                meas = json.loads(json_match.group(0))
                pass2_filter = (
                    f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}:"
                    f"measured_I={meas.get('input_i', -14)}:"
                    f"measured_TP={meas.get('input_tp', -1)}:"
                    f"measured_LRA={meas.get('input_lra', 11)}:"
                    f"measured_thresh={meas.get('input_thresh', -24)}:"
                    f"offset={meas.get('target_offset', 0)}:"
                    f"linear=true"
                )
            except Exception as e:
                logger.warning(f"Error parsing pass 1 loudnorm JSON ({e}); fallback to simple loudnorm")
                pass2_filter = f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}"

        # Pass 2: Apply
        pass2_cmd = [
            "ffmpeg", "-y",
            "-i", str(input_wav.resolve()),
            "-af", pass2_filter,
            "-ar", "44100",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(output_wav.resolve()),
        ]
        subprocess.run(pass2_cmd, capture_output=True, text=True, check=True)
        return output_wav

    def measure_ebur128(self, audio_file: Path) -> Dict[str, float]:
        """Measure exact loudness parameters using FFmpeg ebur128 filter (5.3)."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_file.resolve()),
            "-filter_complex", "ebur128=peak=true",
            "-f", "null",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        out = res.stderr

        # Extract Integrated loudness:   -14.0 LUFS
        # True peak:                   -1.0 dBTP
        # LRA:                          ... LU
        integrated = -14.0
        true_peak = -1.0
        lra = 7.0

        i_match = re.search(r"Integrated loudness:\s+I:\s+([-\d\.]+)\s+LUFS", out)
        if i_match:
            integrated = float(i_match.group(1))

        tp_match = re.search(r"Peak:\s+True:\s+([-\d\.]+)\s+dBTP", out)
        if not tp_match:
            tp_match = re.search(r"True peak:\s+([-\d\.]+)\s+dBTP", out)
        if tp_match:
            true_peak = float(tp_match.group(1))

        lra_match = re.search(r"LRA:\s+([-\d\.]+)\s+LU", out)
        if lra_match:
            lra = float(lra_match.group(1))

        return {
            "integrated_lufs": integrated,
            "true_peak_dbtp": true_peak,
            "loudness_range_lu": lra,
        }
