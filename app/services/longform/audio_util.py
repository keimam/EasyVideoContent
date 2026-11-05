"""Audio Processing Utilities for Longform Video Generation (3.2, 3.4).

Provides drift-free audio operations:
- Direct conversion of raw TTS audio (MP3) to uncompressed WAV PCM 48 kHz mono
- High-precision duration measurement from exact WAV sample counts
- Deterministic WAV PCM concatenation with configurable inter-group pauses
- Synthetic tone/silence audio generation for offline testing and fake providers
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path
from typing import List, Tuple

from loguru import logger

from app.utils import utils


def get_ffmpeg_binary() -> str:
    """Return the resolved path to ffmpeg executable."""
    try:
        return utils.get_ffmpeg_binary()
    except Exception:
        return "ffmpeg"


def convert_to_pcm_wav(
    input_audio_path: str | Path,
    output_wav_path: str | Path,
    sample_rate: int = 48000,
    channels: int = 1,
) -> Path:
    """Convert any audio file (e.g. MP3) to standard uncompressed 16-bit PCM WAV (3.2).

    Atomic write: writes to temporary file first, then renames.
    """
    in_path = Path(input_audio_path).resolve()
    out_path = Path(output_wav_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not in_path.is_file():
        raise FileNotFoundError(f"Source audio file not found: {in_path}")

    ffmpeg = get_ffmpeg_binary()
    tmp_path = out_path.parent / f"{out_path.name}.tmp.{uuid.uuid4().hex}.wav"

    cmd = [
        ffmpeg,
        "-y",
        "-v", "error",
        "-i", str(in_path),
        "-acodec", "pcm_s16le",
        "-ac", str(channels),
        "-ar", str(sample_rate),
        str(tmp_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg PCM conversion failed for '{in_path}': {exc.stderr}") from exc

    # Atomic rename
    os.replace(tmp_path, out_path)
    return out_path


def measure_wav_duration_and_samples(wav_path: str | Path) -> Tuple[float, int, int]:
    """Measure duration from exact sample frames in a WAV file (3.2).

    Returns (duration_seconds, total_frames, sample_rate).
    """
    path = Path(wav_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"WAV file not found: {path}")

    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        if rate <= 0:
            raise ValueError(f"Invalid sample rate {rate} in WAV: {path}")
        duration = round(frames / float(rate), 3)
        return duration, frames, rate


def get_wav_duration_seconds(wav_path: str | Path) -> float:
    """Convenience helper returning duration in seconds with millisecond precision."""
    dur, _, _ = measure_wav_duration_and_samples(wav_path)
    return dur


def generate_synthetic_tone_wav(
    output_wav_path: str | Path,
    duration_seconds: float,
    sample_rate: int = 48000,
    channels: int = 1,
    frequency_hz: float = 440.0,
) -> Path:
    """Generate deterministic 16-bit PCM WAV tone for offline testing and fake providers (3.1)."""
    out_path = Path(output_wav_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dur = max(0.05, float(duration_seconds))
    total_samples = int(dur * sample_rate)
    tmp_path = out_path.parent / f"{out_path.name}.tmp.{uuid.uuid4().hex}.wav"

    # Generate mild sine wave
    amplitude = 4000  # -18 dBFS gentle test tone
    with wave.open(str(tmp_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)

        # Buffer generation in chunks of 4096 samples
        chunk_size = 4096
        samples_written = 0
        while samples_written < total_samples:
            count = min(chunk_size, total_samples - samples_written)
            raw_data = bytearray()
            for i in range(count):
                idx = samples_written + i
                val = int(amplitude * math.sin(2.0 * math.pi * frequency_hz * idx / sample_rate))
                for _ in range(channels):
                    raw_data.extend(struct.pack("<h", val))
            wf.writeframes(raw_data)
            samples_written += count

    os.replace(tmp_path, out_path)
    return out_path


def generate_silence_wav(
    output_wav_path: str | Path,
    duration_seconds: float,
    sample_rate: int = 48000,
    channels: int = 1,
) -> Path:
    """Generate exact silence PCM WAV for a given duration."""
    out_path = Path(output_wav_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total_samples = int(duration_seconds * sample_rate)
    tmp_path = out_path.parent / f"{out_path.name}.tmp.{uuid.uuid4().hex}.wav"

    with wave.open(str(tmp_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Write zero bytes
        chunk_samples = 4096
        zero_chunk = b"\x00" * (chunk_samples * 2 * channels)
        samples_written = 0
        while samples_written < total_samples:
            count = min(chunk_samples, total_samples - samples_written)
            if count == chunk_samples:
                wf.writeframes(zero_chunk)
            else:
                wf.writeframes(b"\x00" * (count * 2 * channels))
            samples_written += count

    os.replace(tmp_path, out_path)
    return out_path


def concat_pcm_wavs(
    wav_paths: List[str | Path],
    output_wav_path: str | Path,
    pause_between_ms: int = 350,
    pause_start_ms: int = 0,
    pause_end_ms: int = 0,
    sample_rate: int = 48000,
    channels: int = 1,
) -> Tuple[Path, float]:
    """Concatenate multiple PCM WAV files with inter-group pauses directly at sample level (3.4).

    Guarantees 0ms drift between expected timeline sum and resultant WAV duration.
    """
    out_path = Path(output_wav_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    paths = [Path(p).resolve() for p in wav_paths]
    for p in paths:
        if not p.is_file():
            raise FileNotFoundError(f"Cannot concatenate missing audio file: {p}")

    tmp_path = out_path.parent / f"{out_path.name}.tmp.{uuid.uuid4().hex}.wav"
    bytes_per_sample = 2 * channels

    def make_silence(ms: int) -> bytes:
        if ms <= 0:
            return b""
        num_frames = int(sample_rate * (ms / 1000.0))
        return b"\x00" * (num_frames * bytes_per_sample)

    pause_between_bytes = make_silence(pause_between_ms)
    start_pause_bytes = make_silence(pause_start_ms)
    end_pause_bytes = make_silence(pause_end_ms)

    total_frames = 0
    with wave.open(str(tmp_path), "wb") as out_wf:
        out_wf.setnchannels(channels)
        out_wf.setsampwidth(2)
        out_wf.setframerate(sample_rate)

        if start_pause_bytes:
            out_wf.writeframes(start_pause_bytes)
            total_frames += len(start_pause_bytes) // bytes_per_sample

        for idx, p in enumerate(paths):
            with wave.open(str(p), "rb") as in_wf:
                in_ch = in_wf.getnchannels()
                in_rate = in_wf.getframerate()
                if in_ch != channels or in_rate != sample_rate:
                    raise ValueError(
                        f"WAV '{p}' format mismatch (channels={in_ch}, rate={in_rate}), "
                        f"expected (channels={channels}, rate={sample_rate})"
                    )
                in_frames = in_wf.getnframes()
                # Stream frames in chunks
                chunk = in_wf.readframes(4096)
                while chunk:
                    out_wf.writeframes(chunk)
                    chunk = in_wf.readframes(4096)
                total_frames += in_frames

            # Add inter-group pause between items
            if idx < len(paths) - 1 and pause_between_bytes:
                out_wf.writeframes(pause_between_bytes)
                total_frames += len(pause_between_bytes) // bytes_per_sample

        if end_pause_bytes:
            out_wf.writeframes(end_pause_bytes)
            total_frames += len(end_pause_bytes) // bytes_per_sample

    os.replace(tmp_path, out_path)
    total_duration = round(total_frames / float(sample_rate), 3)
    return out_path, total_duration
