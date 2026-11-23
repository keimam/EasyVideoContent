"""Pydantic Models for Longform Projects and Presets.

Implements structured models for Project, Chapter, TtsGroup, Segment, and ScriptOutline,
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
    kind: str = "niche"  # "niche" | "sub_niche"
    extends: Optional[str] = None
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
    topic_keywords: List[str] = Field(default_factory=list)
    visual_keywords: List[str] = Field(default_factory=list)
    sensitivity_notes: str = ""
    recommended_visual_sources: dict[str, float] = Field(default_factory=dict)
    required_subject_terms: List[str] = Field(default_factory=list)
    blocked_terms: List[str] = Field(default_factory=list)
    negative_terms: List[str] = Field(default_factory=list)
    min_relevance_score: float = 0.35
    stock_category: Optional[str] = None
    capcut_text_size: Optional[int] = None

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
    search_keywords: Optional[str] = None
    shot_id: Optional[str] = None
    locked: bool = False
    requested_source: Optional[str] = None
    actual_source: Optional[str] = None
    fallback_reason: Optional[str] = None
    source_candidate_id: Optional[str] = None
    candidates: List[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @field_validator("source_file", "rendered_clip")
    @classmethod
    def validate_relative_paths(cls, value: str | None, info) -> str | None:
        return _assert_relative_path(value, info.field_name)


class Shot(BaseModel):
    """A visual shot unit (4.2), formed by 1 or more sentence segments."""

    shot_id: str
    chapter_index: int
    shot_index: int
    segment_indices: List[int] = Field(default_factory=list)
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    duration_seconds: float = 0.0
    target_frames: int = 0
    requested_source: Optional[str] = None
    actual_source: Optional[str] = None
    fallback_reason: Optional[str] = None
    source_candidate_id: Optional[str] = None
    visual_prompt: str = ""
    search_query: str = ""
    motion: MotionType = MotionType.zoom_in
    asset_path: Optional[str] = None
    asset_content_hash: Optional[str] = None
    rendered_clip: Optional[str] = None
    shot_hash: Optional[str] = None
    locked: bool = False
    is_fake: bool = False
    candidates: List[dict[str, Any]] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    relevance_score: Optional[float] = None
    needs_review: bool = False
    chosen_rendition: Optional[str] = None
    is_low_res: bool = False
    effective_query: Optional[str] = None
    search_query_en: Optional[str] = None
    search_queries_en: List[str] = Field(default_factory=list)  # Ordered concrete alternatives (F12)
    search_query_source: Optional[str] = None  # "ai" | "extraction" | "tag"
    search_query_reason: Optional[str] = None  # Reason when fallback/extraction used (F11)
    ai_relevance_status: Optional[str] = None
    ai_relevance_reason: Optional[str] = None
    ai_relevance_asset_hash: Optional[str] = None
    ai_video_provider: Optional[str] = None
    ai_video_model: Optional[str] = None
    search_attempts: List[dict[str, Any]] = Field(default_factory=list)  # Tiered search diagnostics (D9.1)
    no_visual_reason_summary: Optional[str] = None
    attribution_title: Optional[str] = None
    attribution_author: Optional[str] = None
    attribution_author_url: Optional[str] = None
    attribution_license: Optional[str] = None
    attribution_url: Optional[str] = None
    requires_attribution: bool = False
    status: ItemStatus = ItemStatus.pending

    model_config = {"extra": "forbid", "validate_assignment": True}

    @field_validator("visual_prompt", "search_query", mode="before")
    @classmethod
    def coerce_none_text(cls, value: Any) -> str:
        return "" if value is None else str(value)

    @field_validator("segment_indices", "candidates", "tags", "search_queries_en", "search_attempts", mode="before")
    @classmethod
    def coerce_none_list(cls, value: Any) -> list:
        return [] if value is None else value

    @field_validator("locked", "is_fake", "needs_review", "is_low_res", "requires_attribution", mode="before")
    @classmethod
    def coerce_none_bool(cls, value: Any) -> bool:
        return False if value is None else bool(value)

    @field_validator("requested_source", "actual_source", "fallback_reason", mode="before")
    @classmethod
    def coerce_optional_source_str(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        txt = str(value).strip()
        return txt or None

    @field_validator("asset_path", "rendered_clip")
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
    chapter_start_seconds: Optional[float] = None
    chapter_end_seconds: Optional[float] = None
    visual: VisualSpec = Field(default_factory=VisualSpec)
    verify: Optional[str] = None
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
    alignment_method: Optional[str] = None
    word_match_ratio: Optional[float] = None
    fallback_reason: Optional[str] = None
    word_timings_file: Optional[str] = None
    sample_rate: int = 48000
    channels: int = 1
    audio_format: str = "wav_48k_pcm"

    model_config = {"extra": "forbid"}

    @field_validator("file_path", "word_timings_file")
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

    def get_effective_paragraph_text(self) -> str:
        """Return paragraph text reconstructed from segments if present, or self.paragraph_text."""
        if self.segments:
            texts = [
                s.narration_text.strip()
                for s in self.segments
                if s.narration_text and s.narration_text.strip()
            ]
            if texts:
                return " ".join(texts)
        return self.paragraph_text


class Chapter(BaseModel):
    """A major narrative chapter, serving as the unit of partial rendering and resume."""

    chapter_index: int
    chapter_title: str
    tts_groups: List[TtsGroup] = Field(default_factory=list)
    shots: List[Shot] = Field(default_factory=list)
    output_file: Optional[str] = None
    chapter_hash: Optional[str] = None
    voice_override: Optional[str] = None
    audio_file: Optional[str] = None
    audio_duration_seconds: Optional[float] = None
    visual_duration_seconds: Optional[float] = None
    visual_frames: Optional[int] = None
    preview_video_file: Optional[str] = None
    min_word_match_ratio: Optional[float] = None
    avg_word_match_ratio: Optional[float] = None
    needs_language_review: bool = False
    language_warning: Optional[str] = None
    status: ItemStatus = ItemStatus.pending

    model_config = {"extra": "forbid"}

    @field_validator("output_file", "audio_file", "preview_video_file")
    @classmethod
    def validate_relative_path(cls, value: str | None, info) -> str | None:
        return _assert_relative_path(value, info.field_name)


class OutlineChapter(BaseModel):
    """Chapter entry in an LLM-generated script outline."""

    chapter_index: int
    title: str
    purpose: str
    key_points: List[str] = Field(default_factory=list)
    target_words: int

    model_config = {"extra": "forbid"}


class ScriptOutline(BaseModel):
    """Structured outline generated by LLM prior to drafting individual chapters."""

    topic: str
    target_duration_minutes: float
    words_per_minute: int
    total_target_words: int
    candidate_titles: List[str] = Field(default_factory=list)
    opening_hook: str
    chapters: List[OutlineChapter] = Field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None

    model_config = {"extra": "forbid"}

    @field_validator("candidate_titles")
    @classmethod
    def validate_candidate_titles(cls, v: List[str]) -> List[str]:
        if len(v) < 3:
            raise ValueError("ScriptOutline requires at least 3 candidate titles.")
        return v

    @field_validator("chapters")
    @classmethod
    def validate_chapters(cls, v: List[OutlineChapter]) -> List[OutlineChapter]:
        if not v:
            raise ValueError("ScriptOutline must contain at least 1 chapter.")
        return v


CURRENT_SCHEMA_VERSION = "1.1.0"
SUPPORTED_SCHEMA_VERSIONS = {"1.0.0", "1.1.0"}


class ProjectManifest(BaseModel):
    """Single source of truth for longform video project data and execution progress."""

    pipeline_version: str = "1.0.0"
    schema_version: str = CURRENT_SCHEMA_VERSION
    project_id: str
    title: str
    topic: Optional[str] = None
    language: Optional[str] = None
    target_duration_minutes: Optional[int] = None
    aspect_ratio: str = "16:9"
    preset_id: str
    preset_inheritance_chain: List[str] = Field(default_factory=list)
    preset_snapshot: dict[str, Any]
    sub_niche_focus: Optional[str] = None
    visual_anchor_terms: List[str] = Field(default_factory=list)
    visual_avoid_terms: List[str] = Field(default_factory=list)
    visual_summary: Optional[str] = None
    # V0/V1/V3 visual search & external bridge settings on manifest.
    # Note: schema default stays "priority" so legacy saved manifests preserve Prioritas behavior;
    # create_project() sets "mix_best" for newly created projects.
    visual_search_mode: str = "priority"
    visual_source_order: List[str] = Field(default_factory=list)
    visual_max_source_share: float = Field(default=0.6, ge=0.1, le=1.0)
    visual_avoid_consecutive_same_source: bool = True
    fill_local_random: bool = False
    last_prompt_export_shot_ids: List[str] = Field(default_factory=list)
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
        if value not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported manifest schema version: '{value}'. "
                f"Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        return value
