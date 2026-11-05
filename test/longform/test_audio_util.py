"""Tests for Audio Utilities, PCM WAV Conversion, and Zero-Drift Concatenation (3.2, 3.4)."""

import shutil
import tempfile
import wave
from pathlib import Path
import pytest

from app.services.longform.audio_util import (
    concat_pcm_wavs,
    convert_to_pcm_wav,
    generate_silence_wav,
    generate_synthetic_tone_wav,
    measure_wav_duration_and_samples,
)


def test_synthetic_tone_and_duration_measurement(tmp_path: Path):
    """Verify synthetic tone WAV generation and millisecond sample measurement (3.2)."""
    wav_path = tmp_path / "tone.wav"
    generate_synthetic_tone_wav(
        output_wav_path=wav_path,
        duration_seconds=2.5,
        sample_rate=48000,
        channels=1,
    )
    assert wav_path.is_file()

    dur, frames, rate = measure_wav_duration_and_samples(wav_path)
    assert rate == 48000
    assert frames == 48000 * 2.5
    assert abs(dur - 2.5) < 0.001


def test_concat_wavs_drift_under_10ms_for_10_groups(tmp_path: Path):
    """Verify concatenation of 10+ audio groups with pauses has <= 10 ms drift (3.4)."""
    group_wavs = []
    expected_sum = 0.0
    pause_ms = 350

    # Create 12 distinct audio groups of varying lengths (1.2s to 3.5s)
    durations = [1.2, 2.4, 1.8, 3.1, 2.0, 1.5, 2.8, 3.3, 1.9, 2.2, 2.7, 3.0]
    assert len(durations) >= 10

    for idx, d in enumerate(durations, start=1):
        g_path = tmp_path / f"group_{idx:02d}.wav"
        generate_synthetic_tone_wav(g_path, duration_seconds=d, sample_rate=48000, channels=1)
        dur_meas, _, _ = measure_wav_duration_and_samples(g_path)
        group_wavs.append(g_path)
        expected_sum += dur_meas

    # Total pauses = 11 pauses between 12 items
    total_pause_seconds = (len(durations) - 1) * (pause_ms / 1000.0)
    expected_total_duration = expected_sum + total_pause_seconds

    out_wav = tmp_path / "chapter_combined.wav"
    result_path, total_measured = concat_pcm_wavs(
        wav_paths=group_wavs,
        output_wav_path=out_wav,
        pause_between_ms=pause_ms,
        sample_rate=48000,
        channels=1,
    )

    assert result_path.is_file()
    # Drift must be <= 10 ms (0.010 s)
    drift_ms = abs(total_measured - expected_total_duration) * 1000.0
    assert drift_ms <= 10.0, f"Drift {drift_ms:.3f}ms exceeds 10ms tolerance"


def test_convert_to_pcm_wav_generates_valid_48k_wav(tmp_path: Path):
    """Verify convert_to_pcm_wav outputs valid 48kHz mono 16-bit PCM (3.2)."""
    # Create input source WAV
    source_wav = tmp_path / "source.wav"
    generate_synthetic_tone_wav(source_wav, duration_seconds=1.5, sample_rate=44100, channels=1)

    target_wav = tmp_path / "converted_48k.wav"
    convert_to_pcm_wav(source_wav, target_wav, sample_rate=48000, channels=1)

    assert target_wav.is_file()
    dur, frames, rate = measure_wav_duration_and_samples(target_wav)
    assert rate == 48000
    assert abs(dur - 1.5) < 0.05
