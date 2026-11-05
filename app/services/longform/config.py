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
    """Return the absolute path to config/longform.toml."""
    # Base directory is repo root
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return repo_root / "config" / "longform.toml"


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
