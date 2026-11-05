"""Canonical Hashing Functions for Caching and Invalidation.

Implements deterministic hashing for audio groups (K3), visual segments,
sequential chapter composition (K1), and preset snapshots (K2).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.services.longform.schema import Chapter, EncoderSettings, TtsGroup, VisualSpec


def normalize_text(text: str | None) -> str:
    """Normalize text using Unicode NFC, collapsed whitespace, and trimmed ends."""
    if text is None:
        return ""
    norm = unicodedata.normalize("NFC", str(text))
    # Replace all whitespace sequences (tabs, newlines, multiple spaces) with single space
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def canonical_json(data: Any) -> str:
    """Serialize dictionary or data structure to deterministic canonical JSON."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_string(content: str) -> str:
    """Compute SHA-256 hexadecimal digest of UTF-8 encoded string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hash_file(file_path: str | Path, chunk_size: int = 65536) -> str:
    """Compute SHA-256 digest of file contents via streaming (safe for large files)."""
    hasher = hashlib.sha256()
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Cannot hash non-existent file: {path}")

    with open(path, "rb") as fp:
        while True:
            chunk = fp.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_audio_group_hash(
    paragraph_text: str,
    tts_provider: str = "edge",
    tts_model: str = "",
    voice_name: str = "",
    voice_rate: float = 1.0,
    text_norm_version: str = "v1",
) -> str:
    """Compute hash for a paragraph-level TTS Group (K3)."""
    norm_text = normalize_text(paragraph_text)
    payload = (
        f"{norm_text}|{tts_provider.strip().lower()}|{tts_model.strip()}|"
        f"{voice_name.strip()}|{voice_rate:.2f}|{text_norm_version.strip()}"
    )
    return hash_string(payload)


def compute_visual_segment_hash(
    source_content_hash: str | None,
    motion: str = "zoom_in",
    duration: float = 5.0,
    target_w: int = 1920,
    target_h: int = 1080,
    target_fps: float = 30.0,
) -> str:
    """Compute hash for a segment visual render."""
    content_hash = source_content_hash.strip() if source_content_hash else ""
    payload = f"{content_hash}|{motion.strip()}|{duration:.3f}|{target_w}x{target_h}|{target_fps:.2f}"
    return hash_string(payload)


def compute_chapter_hash(
    chapter: Chapter,
    encoder_settings: EncoderSettings,
    subtitle_style: dict[str, Any],
    preset_snapshot: dict[str, Any],
    pipeline_version: str = "1.0.0",
    sub_niche_focus: str = "",
) -> str:
    """Compute canonical hash for a chapter based on sequential elements (K1, K2, B3)."""
    # K1: Sequential concatenation of element hashes with newline delimiter
    elements = []
    for group in chapter.tts_groups:
        elements.append(f"audio_group_{group.group_index}:{group.audio.audio_hash or ''}")
        for seg in group.segments:
            elements.append(f"visual_seg_{seg.segment_index}:{seg.visual.visual_hash or ''}")
    joined_elements = "\n".join(elements)

    # K2: Snapshot canonical hash
    preset_snapshot_hash = hash_string(canonical_json(preset_snapshot))
    encoder_hash = hash_string(canonical_json(encoder_settings.model_dump()))
    subtitle_hash = hash_string(canonical_json(subtitle_style))
    focus_norm = normalize_text(sub_niche_focus)

    focus_part = f"|{focus_norm}" if focus_norm else ""
    payload = (
        f"{joined_elements}|{encoder_hash}|{subtitle_hash}|"
        f"{preset_snapshot_hash}{focus_part}|{pipeline_version}"
    )
    return hash_string(payload)


def compute_final_concat_hash(
    chapters: list[Chapter],
    bgm_hash: str = "",
    pipeline_version: str = "1.0.0",
) -> str:
    """Compute hash for final concat based on sequential chapter hashes."""
    joined = "\n".join(f"ch_{c.chapter_index}:{c.chapter_hash or ''}" for c in chapters)
    payload = f"{joined}|{bgm_hash}|{pipeline_version}"
    return hash_string(payload)
