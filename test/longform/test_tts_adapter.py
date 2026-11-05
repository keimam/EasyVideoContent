import pytest
from pathlib import Path
from app.services.longform.schema import Segment, TtsGroup, VisualSpec
from app.services.longform.tts_adapter import (
    TTSAdapter,
    TTSConfigurationError,
    clean_narration_text,
    sanitize_error,
)


def test_clean_narration_text():
    raw = (
        "In 1964, Pontiac shocked the world. "
        "[visual: 1964 Pontiac GTO driving on highway] "
        "[motion: zoom_in] [source: pexels] [verify: check sales figure] "
        "Sales were unprecedented."
    )
    cleaned = clean_narration_text(raw)
    assert "[visual" not in cleaned
    assert "[motion" not in cleaned
    assert "[source" not in cleaned
    assert "[verify" not in cleaned
    assert cleaned == "In 1964, Pontiac shocked the world. Sales were unprecedented."


def test_sanitize_error_masks_sensitive_data():
    err = "Connection failed with api_key=sk-1234567890abcdef and secret=mysecrettoken"
    sanitized = sanitize_error(err)
    assert "sk-1234567890abcdef" not in sanitized
    assert "mysecrettoken" not in sanitized
    assert "***REDACTED***" in sanitized


def test_adapter_default_is_not_fake():
    adapter = TTSAdapter()
    assert adapter.provider == "edge"
    assert not adapter.is_fake


def test_adapter_validate_configuration(monkeypatch):
    # Valid edge config
    adapter_edge = TTSAdapter(provider="edge", voice_name="en-US-ChristopherNeural-Male")
    adapter_edge.validate_configuration()

    # Empty voice on edge
    adapter_bad_voice = TTSAdapter(provider="edge", voice_name="")
    with pytest.raises(TTSConfigurationError, match="valid voice_name"):
        adapter_bad_voice.validate_configuration()

    # Fake provider is always valid
    adapter_fake = TTSAdapter(provider="fake")
    adapter_fake.validate_configuration()

    # Unsupported provider
    adapter_bad = TTSAdapter(provider="unknown_provider")
    with pytest.raises(TTSConfigurationError, match="not supported"):
        adapter_bad.validate_configuration()


def test_synthesize_fake_deterministic(tmp_path: Path):
    adapter = TTSAdapter(
        provider="fake",
        output_dir=tmp_path,
        words_per_minute=120,
    )

    group = TtsGroup(
        group_index=1,
        paragraph_text="The 1964 Pontiac GTO ignited the muscle car movement. Engineers stuffed a massive 389 V8 into an intermediate coupe.",
        segments=[
            Segment(
                segment_index=1,
                narration_text="The 1964 Pontiac GTO ignited the muscle car movement.",
                visual=VisualSpec(prompt="1964 Pontiac GTO in motion"),
            ),
            Segment(
                segment_index=2,
                narration_text="Engineers stuffed a massive 389 V8 into an intermediate coupe.",
                visual=VisualSpec(prompt="Engine bay close up"),
            ),
        ],
    )

    result = adapter.synthesize_group(
        group=group,
        chapter_index=1,
    )

    assert result.is_fake is True
    assert result.provider == "fake"
    assert result.wav_file.is_file()
    assert result.duration_seconds > 1.0
    assert result.sample_rate == 48000
    assert result.total_samples > 0
    assert len(result.updated_segments) == 2

    # Check zero-gap segment boundaries
    seg1, seg2 = result.updated_segments
    assert seg1.start_offset_seconds == 0.0
    assert seg1.end_offset_seconds == seg2.start_offset_seconds
    assert seg2.end_offset_seconds == result.duration_seconds


def test_synthesize_cached_wav_reuse(tmp_path: Path):
    adapter = TTSAdapter(
        provider="fake",
        output_dir=tmp_path,
    )

    group = TtsGroup(
        group_index=1,
        paragraph_text="Testing audio cache reuse.",
        segments=[
            Segment(
                segment_index=1,
                narration_text="Testing audio cache reuse.",
                visual=VisualSpec(prompt="Audio graph"),
            )
        ],
    )

    res1 = adapter.synthesize_group(group, chapter_index=1)
    group.audio.duration_seconds = res1.duration_seconds
    group.audio.alignment_method = res1.alignment_method

    # Calling without force_refresh should reuse existing WAV
    res2 = adapter.synthesize_group(group, chapter_index=1, force_refresh=False)
    assert res2.wav_file == res1.wav_file
    assert res2.duration_seconds == res1.duration_seconds
