"""Unified TTS Adapter for Longform Video Generation (3.1).

Coordinates speech synthesis across paragraph-level TTS groups:
- Calls upstream TTS: app.services.voice.tts (voice.py:1159)
- Primary provider: Edge TTS with word boundaries via SubMaker
- Deterministic 'fake' mock provider for zero-cost offline development and testing
- Immediate conversion of raw MP3 to standard uncompressed WAV PCM 48 kHz mono (3.2)
- High-precision duration measurement from exact WAV sample frames
- Sentence-level timing alignment with boundary invariants (3.3)
- Bounded retries with exponential backoff and configurable concurrency
"""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services import voice as upstream_voice
from app.services.longform.alignment import WordCue, align_group_segments
from app.services.longform.audio_util import (
    convert_to_pcm_wav,
    generate_synthetic_tone_wav,
    measure_wav_duration_and_samples,
)
from app.services.longform.schema import Segment, TtsGroup

_SENSITIVE_KEY_RE = re.compile(
    r"((?:api[_-]?key|access[_-]?token|secret|password|bearer\s+)[=:\s]+)([a-zA-Z0-9_\-\.]{8,})",
    re.IGNORECASE,
)


def sanitize_error(msg: str) -> str:
    """Mask potential API keys or secrets in error messages."""
    return _SENSITIVE_KEY_RE.sub(r"\1***REDACTED***", str(msg))


class TTSAdapterError(Exception):
    """Raised when TTS synthesis fails."""
    pass


class TTSConfigurationError(TTSAdapterError):
    """Raised when a selected TTS provider is missing required credentials or setup."""
    pass


@dataclass
class TTSResult:
    """Result of synthesizing a single TTSGroup."""

    group_index: int
    wav_file: Path
    raw_mp3_file: Optional[Path]
    duration_seconds: float
    total_samples: int
    sample_rate: int
    alignment_method: str  # "word_cue" | "whisper" | "proportional"
    is_fake: bool
    provider: str
    voice_name: str
    voice_rate: float
    updated_segments: List[Segment]


def clean_narration_text(raw_text: str) -> str:
    """Strip all longform bracket directives ([visual], [motion], [source], [search], [verify]) (3.2)."""
    # Remove bracket tags
    clean = re.sub(r"\[(?:visual|motion|source|file|voice|search|verify)\s*[:=][^\]]*\]", "", raw_text, flags=re.IGNORECASE)
    # Remove leftover orphan brackets
    clean = re.sub(r"\[[^\]]+\]", "", clean)
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


class TTSAdapter:
    """Adapter mediating TTS calls with WAV conversion, alignment, retries, and fake provider support."""

    def __init__(
        self,
        provider: str = "edge",
        voice_name: str = "en-US-ChristopherNeural-Male",
        voice_rate: float = 1.0,
        output_dir: Optional[str | Path] = None,
        max_retries: int = 3,
        retry_delay: float = 1.5,
        fake_delay: float = 0.0,
        words_per_minute: int = 145,
    ) -> None:
        self.provider = provider.strip().lower() if provider else "edge"
        self.voice_name = voice_name.strip()
        self.voice_rate = float(voice_rate)
        self.output_dir = Path(output_dir).resolve() if output_dir else Path.cwd()
        self.max_retries = max(1, int(max_retries))
        self.retry_delay = max(0.2, float(retry_delay))
        self.fake_delay = max(0.0, float(fake_delay))
        self.words_per_minute = max(60, int(words_per_minute))

        self.raw_mp3_dir = self.output_dir / "assets" / "audio" / "raw_mp3"
        self.groups_wav_dir = self.output_dir / "assets" / "audio" / "groups"

        self.raw_mp3_dir.mkdir(parents=True, exist_ok=True)
        self.groups_wav_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_fake(self) -> bool:
        """Return True if this adapter is running against the local fake test mock."""
        return self.provider == "fake"

    def validate_configuration(self) -> None:
        """Verify that provider and voice are valid and configured before execution."""
        if self.is_fake:
            return

        # Edge TTS requires no API key, but requires a voice name
        if self.provider == "edge":
            if not self.voice_name:
                raise TTSConfigurationError("Edge TTS requires a valid voice_name (e.g. 'en-US-ChristopherNeural-Male').")
            return

        # Upstream provider validation
        # If another provider is requested (e.g. 'openai', 'gemini', 'azure'), check upstream config
        from app.config import config
        if self.provider == "openai":
            key = config.app.get("openai_api_key", "").strip()
            if not key:
                raise TTSConfigurationError("OpenAI TTS selected, but 'openai_api_key' is empty in config.toml.")
        elif self.provider == "gemini":
            key = config.app.get("gemini_api_key", "").strip()
            if not key:
                raise TTSConfigurationError("Gemini TTS selected, but 'gemini_api_key' is empty in config.toml.")
        else:
            # Other unsupported providers
            raise TTSConfigurationError(
                f"TTS provider '{self.provider}' is not supported for longform synthesis. "
                "Supported providers: 'edge' (primary) or 'fake' (testing)."
            )

    def synthesize_group(
        self,
        group: TtsGroup,
        chapter_index: int,
        voice_override: Optional[str] = None,
        force_refresh: bool = False,
    ) -> TTSResult:
        """Synthesize audio for a single TtsGroup, convert to WAV 48k PCM, and align segments (3.1-3.3)."""
        self.validate_configuration()

        g_idx = group.group_index
        effective_voice = (voice_override or self.voice_name).strip()
        clean_text = clean_narration_text(group.get_effective_paragraph_text())

        if not clean_text:
            raise TTSAdapterError(f"Chapter {chapter_index} Group {g_idx} has empty narration text.")

        target_wav = self.groups_wav_dir / f"ch{chapter_index}_g{g_idx}.wav"
        target_raw_mp3 = self.raw_mp3_dir / f"ch{chapter_index}_g{g_idx}.mp3"

        # Check existing cached WAV
        if not force_refresh and target_wav.is_file() and target_wav.stat().st_size > 44:
            try:
                dur, frames, rate = measure_wav_duration_and_samples(target_wav)
                if dur > 0.05 and group.audio.duration_seconds is not None:
                    # Re-use existing valid audio
                    logger.debug(f"Reusing cached WAV audio for Ch {chapter_index} Group {g_idx}")
                    return TTSResult(
                        group_index=g_idx,
                        wav_file=target_wav,
                        raw_mp3_file=target_raw_mp3 if target_raw_mp3.is_file() else None,
                        duration_seconds=dur,
                        total_samples=frames,
                        sample_rate=rate,
                        alignment_method=group.audio.alignment_method or "word_cue",
                        is_fake=self.is_fake,
                        provider=self.provider,
                        voice_name=effective_voice,
                        voice_rate=self.voice_rate,
                        updated_segments=group.segments,
                    )
            except Exception as exc:
                logger.warning(f"Failed to read existing WAV '{target_wav}': {exc}. Re-synthesizing.")

        # Execute synthesis via fake mock or upstream Edge TTS
        if self.is_fake:
            return self._synthesize_fake(
                group=group,
                chapter_index=chapter_index,
                clean_text=clean_text,
                target_wav=target_wav,
            )
        else:
            return self._synthesize_upstream(
                group=group,
                chapter_index=chapter_index,
                clean_text=clean_text,
                target_wav=target_wav,
                target_raw_mp3=target_raw_mp3,
                effective_voice=effective_voice,
            )

    def _synthesize_fake(
        self,
        group: TtsGroup,
        chapter_index: int,
        clean_text: str,
        target_wav: Path,
    ) -> TTSResult:
        """Synthesize deterministic tone WAV with synthetic cues for offline testing (3.1)."""
        if self.fake_delay > 0:
            time.sleep(self.fake_delay)

        words = [w for w in clean_text.split() if w]
        num_words = max(1, len(words))

        # Duration derived from words / words_per_minute formula
        duration = max(0.5, round((num_words / float(self.words_per_minute)) * 60.0, 3))

        # Generate 440 Hz tone WAV
        generate_synthetic_tone_wav(
            output_wav_path=target_wav,
            duration_seconds=duration,
            sample_rate=48000,
            channels=1,
        )

        dur_measured, frames, rate = measure_wav_duration_and_samples(target_wav)

        # Generate synthetic word cues
        cue_duration = dur_measured / float(num_words)
        cues = [
            WordCue(
                word=w,
                start=round(i * cue_duration, 3),
                end=round((i + 0.85) * cue_duration, 3),
            )
            for i, w in enumerate(words)
        ]

        # Align segments
        updated_segments, alignment_method = align_group_segments(
            segments=group.segments,
            group_duration=dur_measured,
            word_cues=cues,
            wav_path=target_wav,
        )

        return TTSResult(
            group_index=group.group_index,
            wav_file=target_wav,
            raw_mp3_file=None,
            duration_seconds=dur_measured,
            total_samples=frames,
            sample_rate=rate,
            alignment_method=alignment_method,
            is_fake=True,
            provider="fake",
            voice_name=self.voice_name,
            voice_rate=self.voice_rate,
            updated_segments=updated_segments,
        )

    def _synthesize_upstream(
        self,
        group: TtsGroup,
        chapter_index: int,
        clean_text: str,
        target_wav: Path,
        target_raw_mp3: Path,
        effective_voice: str,
    ) -> TTSResult:
        """Call upstream voice.tts (voice.py:1159), convert to PCM WAV 48kHz, and align segments."""
        last_error = ""

        tmp_raw_mp3 = target_raw_mp3.parent / f"{target_raw_mp3.name}.tmp.{uuid.uuid4().hex}.mp3"

        for attempt in range(1, self.max_retries + 1):
            try:
                # Calls upstream app/services/voice.py:1159 (def tts)
                sub_maker = upstream_voice.tts(
                    text=clean_text,
                    voice_name=effective_voice,
                    voice_rate=self.voice_rate,
                    voice_file=str(tmp_raw_mp3),
                )

                if sub_maker is None or not tmp_raw_mp3.is_file() or tmp_raw_mp3.stat().st_size == 0:
                    raise TTSAdapterError(
                        f"Upstream TTS returned empty audio or None for Ch {chapter_index} Group {group.group_index}"
                    )

                # Atomically preserve raw mp3
                os.replace(tmp_raw_mp3, target_raw_mp3)

                # Immediately convert to WAV PCM 48kHz mono (3.2)
                convert_to_pcm_wav(
                    input_audio_path=target_raw_mp3,
                    output_wav_path=target_wav,
                    sample_rate=48000,
                    channels=1,
                )

                # Measure exact duration from WAV samples
                dur_measured, frames, rate = measure_wav_duration_and_samples(target_wav)

                # Extract word cues from SubMaker
                cues: List[WordCue] = []
                if hasattr(sub_maker, "cues") and sub_maker.cues:
                    for c in sub_maker.cues:
                        cues.append(
                            WordCue(
                                word=c.content,
                                start=c.start.total_seconds(),
                                end=c.end.total_seconds(),
                            )
                        )

                # Align group segments
                updated_segments, alignment_method = align_group_segments(
                    segments=group.segments,
                    group_duration=dur_measured,
                    word_cues=cues,
                    wav_path=target_wav,
                )

                return TTSResult(
                    group_index=group.group_index,
                    wav_file=target_wav,
                    raw_mp3_file=target_raw_mp3,
                    duration_seconds=dur_measured,
                    total_samples=frames,
                    sample_rate=rate,
                    alignment_method=alignment_method,
                    is_fake=False,
                    provider="edge",
                    voice_name=effective_voice,
                    voice_rate=self.voice_rate,
                    updated_segments=updated_segments,
                )

            except Exception as exc:
                if tmp_raw_mp3.exists():
                    tmp_raw_mp3.unlink(missing_ok=True)
                last_error = sanitize_error(str(exc))
                logger.warning(
                    f"TTS synthesis attempt {attempt}/{self.max_retries} failed for "
                    f"Ch {chapter_index} Group {group.group_index}: {last_error}"
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * (2 ** (attempt - 1)))

        raise TTSAdapterError(
            f"Failed to synthesize audio for Chapter {chapter_index} Group {group.group_index} "
            f"after {self.max_retries} attempts. Last error: {last_error}"
        )
