"""Configuration for Longform Video Generation.

Loads configuration from config/longform.toml if present, while providing
safe, conservative in-code defaults. Does NOT modify or rely on upstream config.toml.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import toml
except ImportError:
    toml = None


@dataclass(frozen=True)
class LongformConfig:
    min_duration_seconds: int = 600
    max_duration_seconds: int = 1200
    target_lufs: float = -14.0
    ducking_attenuation_db: float = -16.0
    hardware_encoder: str = "auto"
    atomic_retry_count: int = 5
    atomic_retry_delay_seconds: float = 0.05


_CONFIG_CACHE: LongformConfig | None = None


def get_config_file_path() -> Path:
    """Return the absolute path to config/longform.toml in the app root (or user data override if present)."""
    from app.services.longform import app_paths

    user_override = app_paths.get_data_dir() / "config" / "longform.toml"
    if user_override.is_file():
        return user_override
    return app_paths.get_install_root() / "config" / "longform.toml"


def load_longform_config(reload: bool = False) -> LongformConfig:
    """Load longform configuration from file or fallback to code defaults."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not reload:
        return _CONFIG_CACHE

    config_path = get_config_file_path()
    defaults = {
        "min_duration_seconds": 600,
        "max_duration_seconds": 1200,
        "target_lufs": -14.0,
        "ducking_attenuation_db": -16.0,
        "hardware_encoder": "auto",
        "atomic_retry_count": 5,
        "atomic_retry_delay_seconds": 0.05,
    }

    if config_path.is_file() and toml is not None:
        try:
            with open(config_path, "r", encoding="utf-8") as fp:
                data = toml.load(fp)
                section = data.get("longform", data)
                for key in defaults:
                    if key in section:
                        defaults[key] = section[key]
            logger.debug(f"Loaded longform configuration from {config_path}")
        except Exception as exc:
            logger.warning(f"Failed to load {config_path}, falling back to defaults: {exc}")

    _CONFIG_CACHE = LongformConfig(**defaults)
    return _CONFIG_CACHE


_PRICING_DEFAULTS: dict[str, Any] = {
    "updated_at": "2026-11-17",
    "source": "Official public model pricing pages (checked 2026-11-17)",
    "models": {
        "wavespeed-ai/wan-2.2/t2v-480p-ultra-fast": {
            "per_second": 0.010,
            "resolution_multipliers": {"480p": 1.0, "720p": 4.0, "1080p": 6.0},
            "url": "https://wavespeed.ai/models/wavespeed-ai/wan-2.2/t2v-480p-ultra-fast",
            "checked_at": "2026-11-17",
        },
        "wavespeed-ai/wan-2.2/t2v-5b-720p": {
            "per_second": 0.060,
            "resolution_multipliers": {"480p": 0.7, "720p": 1.0, "1080p": 1.5},
            "url": "https://wavespeed.ai/models/wavespeed-ai/wan-2.2/t2v-5b-720p",
            "checked_at": "2026-11-17",
        },
        "bytedance/seedance-2.0-fast/text-to-video": {
            "per_second": 0.100,
            "resolution_multipliers": {"480p": 0.8, "720p": 1.0, "1080p": 1.5},
            "url": "https://wavespeed.ai/models/bytedance/seedance-2.0-fast/text-to-video",
            "checked_at": "2026-11-17",
        },
        "bytedance/seedance-2.0-fast": {
            "per_second": 0.100,
            "resolution_multipliers": {"480p": 0.8, "720p": 1.0, "1080p": 1.5},
            "url": "https://ofox.ai/models",
            "checked_at": "2026-11-17",
        },
        "bytedance/seedance-1-pro-fast/text-to-video": {
            "per_second": 0.090,
            "resolution_multipliers": {"480p": 0.8, "720p": 1.0, "1080p": 1.5},
            "url": "https://wavespeed.ai/models/bytedance/seedance-v1-pro-fast/text-to-video",
            "checked_at": "2026-11-17",
        },
        "alibaba/wan-2.5/text-to-video": {
            "per_second": 0.070,
            "resolution_multipliers": {"480p": 0.7, "720p": 1.0, "1080p": 1.5},
            "url": "https://wavespeed.ai/models/alibaba/wan-2.5/text-to-video",
            "checked_at": "2026-11-17",
        },
        "kwaivgi/kling-v2.1-t2v-std": {
            "per_second": 0.070,
            "resolution_multipliers": {"480p": 0.8, "720p": 1.0, "1080p": 1.5},
            "url": "https://wavespeed.ai/models/kwaivgi/kling-v2.1-t2v-std",
            "checked_at": "2026-11-17",
        },
        "doubao-seedance-1-0-pro-250528": {
            "per_second": 0.080,
            "resolution_multipliers": {"480p": 0.7, "720p": 1.0, "1080p": 1.6},
            "url": "https://www.volcengine.com/docs/82379/1399008",
            "checked_at": "2026-11-17",
        },
        "doubao-seedance-2-0-fast-260128": {
            "per_second": 0.120,
            "resolution_multipliers": {"480p": 0.8, "720p": 1.0, "1080p": 1.5},
            "url": "https://infistar.cc/",
            "checked_at": "2026-11-17",
        },
        "Doubao-Seedance-2-5-260628": {
            "per_second": 0.180,
            "resolution_multipliers": {"480p": 0.8, "720p": 1.0, "1080p": 1.5},
            "url": "https://api.jalapeno-cloud.ai/",
            "checked_at": "2026-11-17",
        },
        "MiniMax-H3": {
            "per_second": 0.080,
            "resolution_multipliers": {"720p": 1.0, "1080p": 1.5, "2K": 2.0},
            "url": "https://metaso.cn/",
            "checked_at": "2026-11-17",
        },
        "seedance-lite-t2v": {
            "per_second": 0.050,
            "resolution_multipliers": {"480p": 0.8, "720p": 1.0, "1080p": 1.5},
            "url": "https://muapi.ai/pricing",
            "checked_at": "2026-11-17",
        },
        "veo31fast": {
            "per_second": 0.100,
            "resolution_multipliers": {"720p": 1.0, "1080p": 1.4},
            "url": "https://loomloom.shengsuanyun.com/",
            "checked_at": "2026-11-17",
        },
    },
}


def load_ai_video_pricing(config_path: Path | None = None) -> dict[str, Any]:
    """Load editable AI Video pricing table from config/longform.toml."""
    path = config_path or get_config_file_path()
    result = {
        "updated_at": _PRICING_DEFAULTS["updated_at"],
        "source": _PRICING_DEFAULTS["source"],
        "models": dict(_PRICING_DEFAULTS["models"]),
    }
    if path.is_file() and toml is not None:
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = toml.load(fp)
            section = data.get("ai_video_pricing")
            if isinstance(section, dict):
                if section.get("updated_at"):
                    result["updated_at"] = str(section["updated_at"])
                if section.get("source"):
                    result["source"] = str(section["source"])
                raw_models = section.get("models")
                if isinstance(raw_models, dict):
                    result["models"] = {
                        str(m_key).strip(): m_val
                        for m_key, m_val in raw_models.items()
                    }
        except Exception as exc:
            logger.warning(f"Failed to load ai_video_pricing from {path}: {exc}")
    return result
