"""Pydantic Models for Longform Projects and Presets.

Implements structured models for Project, Chapter, TtsGroup, and Segment,
with relative path enforcement (K7) and schema versioning.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import PurePath
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator


def _assert_relative_path(value: str | None, field_name: str) -> str | None:
    """Validate that a path string is relative and safe (K7)."""
    if value is None:
        return None
    val = value.strip()
    if not val:
        return None

    # Check if absolute via OS or drive letter or leading slashes
    if os.path.isabs(val) or val.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", val):
        raise ValueError(
            f"Field '{field_name}' must be a relative path to the project directory, "
            f"got absolute path: {value!r}"
        )
    return val.replace("\\", "/")


class ItemStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
    stale = "stale"


class MotionType(str, Enum):
    zoom_in = "zoom_in"
    zoom_out = "zoom_out"
    pan_left = "pan_left"
    pan_right = "pan_right"
    static = "static"
    alternating = "alternating"


class VisualSourceType(str, Enum):
    ai_image = "ai_image"
    stock_video = "stock_video"
    local = "local"


class Preset(BaseModel):
    """Preset configuration defining audience, tone, structure, and visual/audio defaults."""

    id: str
    name: str
    schema_version: str = "1.0.0"
    language: str = "en-US"
    audience: str
    narration_style: str
    chapter_structure: List[str]
    hook_rules: str
    words_per_minute: int = 145
    subtitle_style: dict[str, Any] = Field(default_factory=dict)
    visual_style: str
    image_prompt_template: dict[str, str] = Field(default_factory=dict)
    visual_source_mix: dict[str, float] = Field(default_factory=dict)
    default_voice: str
    default_bgm: dict[str, Any] = Field(default_factory=dict)
    ken_burns_style: str = "alternating"

    model_config = {"extra": "forbid"}


class EncoderSettings(BaseModel):
    """Audio/video encoder settings to guarantee stream consistency across all chapters (K5)."""

    video_encoder: str = "libx264"
    video_profile: str = "high"
    video_level: str = "4.2"
    pix_fmt: str = "yuv420p"
    resolution: Tuple[int, int] = (1920, 1080)
    fps: float = 30.0
    audio_codec: str = "aac"
    audio_sample_rate: int = 44100
    audio_channels: int = 2

    model_config = {"extra": "forbid"}


class VisualSpec(BaseModel):
    """Visual requirements and rendered asset state for a segment."""

    type: VisualSourceType = VisualSourceType.ai_image
    prompt: str = ""
    motion: MotionType = MotionType.zoom_in
    source_file: Optional[str] = None
    source_content_hash: Optional[str] = None
    rendered_clip: Optional[str] = None
    visual_hash: Optional[str] = None

    model_config = {"extra": "forbid"}

    @field_validator("source_file", "rendered_clip")
    @classmethod
    def validate_relative_paths(cls, value: str | None, info) -> str | None:
        return _assert_relative_path(value, info.field_name)


class Segment(BaseModel):
    """A sentence-level segment, corresponding to a single visual scene (K3)."""

    segment_index: int
    narration_text: str
    start_offset_seconds: Optional[float] = None
    end_offset_seconds: Optional[float] = None
    duration_seconds: Optional[float] = None
    visual: VisualSpec = Field(default_factory=VisualSpec)
    status: ItemStatus = ItemStatus.pending

    model_config = {"extra": "forbid"}


class AudioSpec(BaseModel):
    """Audio metadata and generated asset state for a TTS group."""

    tts_provider: str = "edge"
    tts_model: str = ""
    voice_name: str = ""
    voice_rate: float = 1.0
    text_norm_version: str = "v1"
    duration_seconds: Optional[float] = None
    file_path: Optional[str] = None
    audio_hash: Optional[str] = None

    model_config = {"extra": "forbid"}

    @field_validator("file_path")
    @classmethod
    def validate_relative_path(cls, value: str | None, info) -> str | None:
        return _assert_relative_path(value, info.field_name)


class TtsGroup(BaseModel):
    """A paragraph-level TTS group containing 1 or more sentence segments (K3)."""

    group_index: int
    paragraph_text: str
    audio: AudioSpec = Field(default_factory=AudioSpec)
    segments: List[Segment] = Field(default_factory=list)
    status: ItemStatus = ItemStatus.pending

    model_config = {"extra": "forbid"}


class Chapter(BaseModel):
    """A major narrative chapter, serving as the unit of partial rendering and resume."""

    chapter_index: int
    chapter_title: str
    tts_groups: List[TtsGroup] = Field(default_factory=list)
    output_file: Optional[str] = None
    chapter_hash: Optional[str] = None
    status: ItemStatus = ItemStatus.pending

    model_config = {"extra": "forbid"}

    @field_validator("output_file")
    @classmethod
    def validate_relative_path(cls, value: str | None, info) -> str | None:
        return _assert_relative_path(value, info.field_name)


class ProjectManifest(BaseModel):
    """Single source of truth for longform video project data and execution progress."""

    pipeline_version: str = "1.0.0"
    schema_version: str = "1.0.0"
    project_id: str
    title: str
    aspect_ratio: str = "16:9"
    preset_id: str
    preset_snapshot: dict[str, Any]
    encoder_settings: EncoderSettings = Field(default_factory=EncoderSettings)
    chapters: List[Chapter] = Field(default_factory=list)
    status: ItemStatus = ItemStatus.pending
    final_video_file: Optional[str] = None
    final_video_hash: Optional[str] = None

    model_config = {"extra": "forbid"}

    @field_validator("final_video_file")
    @classmethod
    def validate_relative_path(cls, value: str | None, info) -> str | None:
        return _assert_relative_path(value, info.field_name)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        SUPPORTED = {"1.0.0"}
        if value not in SUPPORTED:
            raise ValueError(
                f"Unsupported manifest schema version: '{value}'. "
                f"Supported versions: {sorted(SUPPORTED)}"
            )
        return value
