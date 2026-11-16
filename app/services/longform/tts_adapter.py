"""Unified TTS Adapter for Longform Video Generation (3.1, R2, R3).

Coordinates speech synthesis across paragraph-level TTS groups:
- Calls upstream TTS: app.services.voice.tts (voice.py:1159)
- Primary provider: Edge TTS with word boundaries via SubMaker
- Deterministic 'fake' mock provider for zero-cost offline development and testing
- Immediate conversion of raw MP3 to standard uncompressed WAV PCM 48 kHz mono (3.2)
- High-precision duration measurement from exact WAV sample frames
- Sentence-level timing alignment with boundary invariants (3.3)
- Word match ratio tracking and fallback recording (R2)
- Word-level timing serialization to JSON sidecars (R3)
- Bounded retries with exponential backoff and configurable concurrency
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
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

# Canonical MoneyPrinterTurbo TTS providers (matches webui/Main.py tts_servers)
MPT_TTS_PROVIDERS: Tuple[Tuple[str, str, bool], ...] = (
    ("edge", "Azure TTS V1 (Edge TTS)", True),
    ("azure-tts-v2", "Azure TTS V2", True),
    ("siliconflow", "SiliconFlow TTS", False),
    ("gemini-tts", "Google Gemini TTS", False),
    ("mimo-tts", "Xiaomi MiMo TTS", False),
    ("minimax-tts", "MiniMax TTS", False),
    ("elevenlabs", "ElevenLabs TTS", False),
    ("chatterbox", "Chatterbox TTS", False),
    ("kokoro", "Kokoro TTS", False),
    ("fish_audio", "Fish Audio TTS", False),
    ("voxcpm", "VoxCPM TTS", False),
)

_TTS_PROVIDER_ALIASES: Dict[str, str] = {
    "azure-tts-v1": "edge",
    "azure_tts_v1": "edge",
    "edge-tts": "edge",
    "azure_tts_v2": "azure-tts-v2",
    "gemini": "gemini-tts",
    "gemini_tts": "gemini-tts",
    "mimo": "mimo-tts",
    "mimo_tts": "mimo-tts",
    "minimax": "minimax-tts",
    "minimax_tts": "minimax-tts",
}


def normalize_tts_provider_id(provider: Optional[str]) -> str:
    """Normalize MPT & longform TTS provider aliases into canonical longform ID."""
    raw = (provider or "edge").strip().lower()
    return _TTS_PROVIDER_ALIASES.get(raw, raw)


def provider_has_word_timings(provider: Optional[str]) -> bool:
    """Return True if the MPT TTS provider emits native word-level boundary timings."""
    pid = normalize_tts_provider_id(provider)
    for key, _, has_words in MPT_TTS_PROVIDERS:
        if key == pid:
            return has_words
    return False


def get_tts_provider_label(provider: Optional[str]) -> str:
    """Return human-readable label matching MPT UI for a TTS provider."""
    pid = normalize_tts_provider_id(provider)
    for key, label, _ in MPT_TTS_PROVIDERS:
        if key == pid:
            return label
    return pid


def is_tts_provider_configured(provider: Optional[str]) -> bool:
    """Return True if the MPT TTS provider has required credentials or config ready."""
    from app.config import config

    pid = normalize_tts_provider_id(provider)
    if pid == "edge":
        return True
    if pid == "azure-tts-v2":
        az = getattr(config, "azure", {}) or {}
        return bool(str(az.get("speech_key", "") or "").strip() and str(az.get("speech_region", "") or "").strip())
    if pid == "siliconflow":
        sf = getattr(config, "siliconflow", {}) or {}
        return bool(str(sf.get("api_key", "") or "").strip())
    if pid == "gemini-tts":
        return bool(str(config.app.get("gemini_api_key", "") or "").strip())
    if pid == "mimo-tts":
        return bool(str(config.app.get("mimo_api_key", "") or "").strip())
    if pid == "minimax-tts":
        try:
            return bool(str(upstream_voice.get_minimax_tts_api_key() or "").strip())
        except Exception:
            mm = getattr(config, "minimax_tts", {}) or {}
            return bool(str(mm.get("api_key", "") or config.app.get("minimax_api_key", "") or "").strip())
    if pid == "elevenlabs":
        try:
            return bool(str(upstream_voice.get_elevenlabs_api_key() or "").strip())
        except Exception:
            el = getattr(config, "elevenlabs", {}) or {}
            return bool(str(el.get("api_key", "") or "").strip())
    if pid == "fish_audio":
        try:
            return bool(str(upstream_voice.get_fish_audio_api_key() or "").strip())
        except Exception:
            fa = getattr(config, "fish_audio", {}) or {}
            return bool(str(fa.get("api_key", "") or "").strip())
    if pid == "voxcpm":
        vx = getattr(config, "voxcpm", {}) or {}
        return bool(str(vx.get("api_key", "") or "").strip() and str(vx.get("model_id", "") or "").strip())
    if pid == "chatterbox":
        cb = getattr(config, "chatterbox", {}) or {}
        ui_srv = str(getattr(config, "ui", {}).get("tts_server", "") or "").strip().lower()
        base_url = str(cb.get("base_url", "") or "").strip()
        api_key = str(cb.get("api_key", "") or "").strip()
        voices = [v for v in (cb.get("voices") or []) if str(v).strip() and str(v).strip() != "default-Female"]
        return bool(api_key or voices or ui_srv == "chatterbox" or (base_url and base_url != "http://127.0.0.1:4123/v1"))
    if pid == "kokoro":
        kk = getattr(config, "kokoro", {}) or {}
        ui_srv = str(getattr(config, "ui", {}).get("tts_server", "") or "").strip().lower()
        base_url = str(kk.get("base_url", "") or "").strip()
        api_key = str(kk.get("api_key", "") or "").strip()
        voices = [v for v in (kk.get("voices") or []) if str(v).strip()]
        return bool(api_key or voices or ui_srv == "kokoro" or (base_url and base_url != "http://127.0.0.1:8880/v1"))
    if pid == "openai":
        return bool(str(config.app.get("openai_api_key", "") or "").strip())
    return False


def list_configured_tts_providers() -> List[Dict[str, Any]]:
    """Return configured MPT TTS providers in UI display order (Edge always present)."""
    out: List[Dict[str, Any]] = []
    for pid, label, has_words in MPT_TTS_PROVIDERS:
        if is_tts_provider_configured(pid):
            out.append(
                {
                    "id": pid,
                    "label": label,
                    "has_word_timings": has_words,
                }
            )
    return out


def _ensure_provider_voice_name(provider: str, voice_name: str) -> str:
    """Format voice_name so upstream ``voice.tts`` dispatches cleanly to the selected provider."""
    pid = normalize_tts_provider_id(provider)
    vn = (voice_name or "").strip()
    if not vn:
        return vn
    if pid == "azure-tts-v2":
        if ":" in vn or vn.endswith("-V2") or "-V2-" in vn:
            return vn
        if vn.endswith("-Female"):
            return f"{vn.removesuffix('-Female')}-V2-Female"
        if vn.endswith("-Male"):
            return f"{vn.removesuffix('-Male')}-V2-Male"
        return f"{vn}-V2"
    prefix_map = {
        "siliconflow": "siliconflow:",
        "gemini-tts": "gemini:",
        "mimo-tts": "mimo:",
        "minimax-tts": "minimax:",
        "elevenlabs": "elevenlabs:",
        "chatterbox": "chatterbox:",
        "kokoro": "kokoro:",
        "fish_audio": "fish_audio:",
        "voxcpm": "voxcpm:",
    }
    pref = prefix_map.get(pid)
    if pref and not vn.startswith(pref) and ":" not in vn:
        return f"{pref}{vn}"
    return vn


def list_voices_for_provider(
    provider: Optional[str] = None,
    language_prefix: Optional[str] = None,
) -> List[str]:
    """Query available voices for any MPT TTS provider using upstream ``voice`` helpers."""
    pid = normalize_tts_provider_id(provider)
    filters = [language_prefix.strip()] if (language_prefix and language_prefix.strip()) else None

    if pid == "edge":
        return upstream_voice.get_all_azure_voices(filters)
    if pid == "azure-tts-v2":
        raw = upstream_voice.get_all_azure_voices(filters)
        return [_ensure_provider_voice_name("azure-tts-v2", v) for v in raw]
    if pid == "siliconflow":
        return upstream_voice.get_siliconflow_voices()
    if pid == "gemini-tts":
        return upstream_voice.get_gemini_voices()
    if pid == "mimo-tts":
        return upstream_voice.get_mimo_voices()
    if pid == "minimax-tts":
        return upstream_voice.get_minimax_voices()
    if pid == "elevenlabs":
        api_key = upstream_voice.get_elevenlabs_api_key()
        return upstream_voice.get_elevenlabs_voices(api_key)
    if pid == "chatterbox":
        return upstream_voice.get_chatterbox_voices()
    if pid == "kokoro":
        return upstream_voice.get_kokoro_voices()
    if pid == "fish_audio":
        return upstream_voice.get_fish_audio_voices()
    if pid == "voxcpm":
        return upstream_voice.get_voxcpm_voices()
    return upstream_voice.get_all_azure_voices(filters)


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
    word_match_ratio: float = 1.0
    fallback_reason: Optional[str] = None
    word_timings_file: Optional[str] = None
    word_timings: List[Dict[str, Any]] = field(default_factory=list)


def clean_narration_text(raw_text: str) -> str:
    """Strip all longform bracket directives ([visual], [motion], [source], [search], [verify]) (3.2)."""
    clean = re.sub(r"\[(?:visual|motion|source|file|voice|search|verify)\s*[:=][^\]]*\]", "", raw_text, flags=re.IGNORECASE)
    clean = re.sub(r"\[[^\]]+\]", "", clean)
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
        word_cue_threshold: float = 0.85,
    ) -> None:
        self.provider = normalize_tts_provider_id(provider)
        self.voice_name = voice_name.strip()
        self.voice_rate = float(voice_rate)
        self.output_dir = Path(output_dir).resolve() if output_dir else Path.cwd()
        self.max_retries = max(1, int(max_retries))
        self.retry_delay = max(0.2, float(retry_delay))
        self.fake_delay = max(0.0, float(fake_delay))
        self.words_per_minute = max(60, int(words_per_minute))
        self.word_cue_threshold = max(0.0, min(1.0, float(word_cue_threshold)))

        self.raw_mp3_dir = self.output_dir / "assets" / "audio" / "raw_mp3"
        self.groups_wav_dir = self.output_dir / "assets" / "audio" / "groups"
        self.words_dir = self.output_dir / "assets" / "audio" / "words"

        self.raw_mp3_dir.mkdir(parents=True, exist_ok=True)
        self.groups_wav_dir.mkdir(parents=True, exist_ok=True)
        self.words_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_fake(self) -> bool:
        """Return True if this adapter is running against the local fake test mock."""
        return self.provider == "fake"

    def validate_configuration(self) -> None:
        """Verify that provider and voice are valid and configured before execution."""
        if self.is_fake:
            return

        if not self.voice_name:
            raise TTSConfigurationError(
                f"TTS provider '{self.provider}' requires a valid voice_name (e.g. 'en-US-ChristopherNeural-Male')."
            )

        known_ids = {pid for pid, _, _ in MPT_TTS_PROVIDERS} | {"openai"}
        if self.provider not in known_ids:
            raise TTSConfigurationError(
                f"TTS provider '{self.provider}' is not supported for longform synthesis. "
                "Supported providers include 'edge', 'azure-tts-v2', and configured MPT TTS services."
            )

        if not is_tts_provider_configured(self.provider):
            raise TTSConfigurationError(
                f"TTS provider '{self.provider}' is selected, but its credentials/configuration are missing in MPT Settings."
            )

    def _write_word_timings_sidecar(
        self,
        chapter_index: int,
        group_index: int,
        match_ratio: float,
        fallback_reason: Optional[str],
        alignment_method: str,
        word_timings: List[Dict[str, Any]],
    ) -> Path:
        """Serialize per-word timings to atomic JSON sidecar file (R3)."""
        target_file = self.words_dir / f"ch{chapter_index}_g{group_index}_words.json"
        tmp_file = self.words_dir / f"{target_file.name}.tmp.{uuid.uuid4().hex}.json"
        sidecar_data = {
            "chapter_index": chapter_index,
            "group_index": group_index,
            "word_match_ratio": round(match_ratio, 3),
            "fallback_reason": fallback_reason,
            "alignment_method": alignment_method,
            "words": word_timings,
        }
        tmp_file.write_text(json.dumps(sidecar_data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_file, target_file)
        return target_file

    def synthesize_group(
        self,
        group: TtsGroup,
        chapter_index: int,
        voice_override: Optional[str] = None,
        force_refresh: bool = False,
    ) -> TTSResult:
        """Synthesize audio for a single TtsGroup, convert to WAV 48k PCM, and align segments (3.1-3.3, R2, R3)."""
        self.validate_configuration()

        g_idx = group.group_index
        effective_voice = (voice_override or self.voice_name).strip()
        clean_text = clean_narration_text(group.get_effective_paragraph_text())

        if not clean_text:
            raise TTSAdapterError(f"Chapter {chapter_index} Group {g_idx} has empty narration text.")

        target_wav = self.groups_wav_dir / f"ch{chapter_index}_g{g_idx}.wav"
        target_raw_mp3 = self.raw_mp3_dir / f"ch{chapter_index}_g{g_idx}.mp3"
        target_words_file = self.words_dir / f"ch{chapter_index}_g{g_idx}_words.json"

        # Check existing cached WAV
        if not force_refresh and target_wav.is_file() and target_wav.stat().st_size > 44:
            try:
                dur, frames, rate = measure_wav_duration_and_samples(target_wav)
                if dur > 0.05 and group.audio.duration_seconds is not None:
                    logger.debug(f"Reusing cached WAV audio for Ch {chapter_index} Group {g_idx}")
                    cached_timings: List[Dict[str, Any]] = []
                    if target_words_file.is_file():
                        try:
                            sidecar = json.loads(target_words_file.read_text(encoding="utf-8"))
                            cached_timings = sidecar.get("words", [])
                        except Exception:
                            pass

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
                        word_match_ratio=group.audio.word_match_ratio if group.audio.word_match_ratio is not None else 1.0,
                        fallback_reason=group.audio.fallback_reason,
                        word_timings_file=f"assets/audio/words/ch{chapter_index}_g{g_idx}_words.json",
                        word_timings=cached_timings,
                    )
            except Exception as exc:
                logger.warning(f"Failed to read existing WAV '{target_wav}': {exc}. Re-synthesizing.")

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

        duration = max(0.5, round((num_words / float(self.words_per_minute)) * 60.0, 3))

        generate_synthetic_tone_wav(
            output_wav_path=target_wav,
            duration_seconds=duration,
            sample_rate=48000,
            channels=1,
        )

        dur_measured, frames, rate = measure_wav_duration_and_samples(target_wav)

        cue_duration = dur_measured / float(num_words)
        cues = [
            WordCue(
                word=w,
                start=round(i * cue_duration, 3),
                end=round((i + 0.85) * cue_duration, 3),
            )
            for i, w in enumerate(words)
        ]

        align_res = align_group_segments(
            segments=group.segments,
            group_duration=dur_measured,
            word_cues=cues,
            wav_path=target_wav,
            word_cue_threshold=self.word_cue_threshold,
        )

        self._write_word_timings_sidecar(
            chapter_index=chapter_index,
            group_index=group.group_index,
            match_ratio=align_res.match_ratio,
            fallback_reason=align_res.fallback_reason,
            alignment_method=align_res.method,
            word_timings=align_res.word_timings,
        )

        return TTSResult(
            group_index=group.group_index,
            wav_file=target_wav,
            raw_mp3_file=None,
            duration_seconds=dur_measured,
            total_samples=frames,
            sample_rate=rate,
            alignment_method=align_res.method,
            is_fake=True,
            provider="fake",
            voice_name=self.voice_name,
            voice_rate=self.voice_rate,
            updated_segments=align_res.segments,
            word_match_ratio=align_res.match_ratio,
            fallback_reason=align_res.fallback_reason,
            word_timings_file=f"assets/audio/words/ch{chapter_index}_g{group.group_index}_words.json",
            word_timings=align_res.word_timings,
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
        dispatched_voice = _ensure_provider_voice_name(self.provider, effective_voice)

        for attempt in range(1, self.max_retries + 1):
            try:
                sub_maker = upstream_voice.tts(
                    text=clean_text,
                    voice_name=dispatched_voice,
                    voice_rate=self.voice_rate,
                    voice_file=str(tmp_raw_mp3),
                )

                if sub_maker is None or not tmp_raw_mp3.is_file() or tmp_raw_mp3.stat().st_size == 0:
                    raise TTSAdapterError(
                        f"Upstream TTS returned empty audio or None for Ch {chapter_index} Group {group.group_index}"
                    )

                os.replace(tmp_raw_mp3, target_raw_mp3)

                convert_to_pcm_wav(
                    input_audio_path=target_raw_mp3,
                    output_wav_path=target_wav,
                    sample_rate=48000,
                    channels=1,
                )

                dur_measured, frames, rate = measure_wav_duration_and_samples(target_wav)

                cues: List[WordCue] = []
                if self.provider == "edge" and hasattr(sub_maker, "cues") and sub_maker.cues:
                    for c in sub_maker.cues:
                        cues.append(
                            WordCue(
                                word=c.content,
                                start=c.start.total_seconds(),
                                end=c.end.total_seconds(),
                            )
                        )
                elif self.provider == "azure-tts-v2":
                    # azure_tts_v2 stores word boundaries in .subs & .offset (100ns ticks)
                    subs = getattr(sub_maker, "subs", None) or []
                    offsets = getattr(sub_maker, "offset", None) or []
                    for word_txt, span in zip(subs, offsets):
                        if isinstance(span, (list, tuple)) and len(span) >= 2:
                            s_sec = float(span[0]) / 10_000_000.0
                            e_sec = float(span[1]) / 10_000_000.0
                            if str(word_txt).strip() and e_sec >= s_sec:
                                cues.append(WordCue(word=str(word_txt), start=s_sec, end=e_sec))

                # Non-word-cue MPT providers fall back to Whisper when native cues are absent
                allow_whisper = not cues
                align_res = align_group_segments(
                    segments=group.segments,
                    group_duration=dur_measured,
                    word_cues=cues,
                    wav_path=target_wav,
                    word_cue_threshold=self.word_cue_threshold,
                    allow_whisper=allow_whisper,
                )

                self._write_word_timings_sidecar(
                    chapter_index=chapter_index,
                    group_index=group.group_index,
                    match_ratio=align_res.match_ratio,
                    fallback_reason=align_res.fallback_reason,
                    alignment_method=align_res.method,
                    word_timings=align_res.word_timings,
                )

                return TTSResult(
                    group_index=group.group_index,
                    wav_file=target_wav,
                    raw_mp3_file=target_raw_mp3,
                    duration_seconds=dur_measured,
                    total_samples=frames,
                    sample_rate=rate,
                    alignment_method=align_res.method,
                    is_fake=False,
                    provider=self.provider,
                    voice_name=dispatched_voice,
                    voice_rate=self.voice_rate,
                    updated_segments=align_res.segments,
                    word_match_ratio=align_res.match_ratio,
                    fallback_reason=align_res.fallback_reason,
                    word_timings_file=f"assets/audio/words/ch{chapter_index}_g{group.group_index}_words.json",
                    word_timings=align_res.word_timings,
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
