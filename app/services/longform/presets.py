"""Preset Loader and Validation for Longform Video Generation.

Loads presets from built-in 'resource/presets/*.json' and user-defined
'storage/presets/*.json'. User presets override built-in presets sharing the same ID.
All reads are BOM-safe (utf-8-sig) and provide detailed errors citing file and field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.services.longform.schema import Preset


class PresetError(Exception):
    """Base exception for preset loading and validation errors."""
    pass


class PresetNotFoundError(PresetError):
    """Raised when a requested preset ID is not found."""
    pass


class PresetValidationError(PresetError):
    """Raised when a preset fails schema or JSON validation."""
    pass


def get_preset_directories() -> Tuple[Path, Path]:
    """Return the absolute paths for resource presets and storage presets."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    resource_dir = repo_root / "resource" / "presets"
    storage_dir = repo_root / "storage" / "presets"
    return resource_dir, storage_dir


def load_preset_file(file_path: str | Path) -> Preset:
    """Load and validate a single preset JSON file.

    BOM-safe (utf-8-sig). Raises PresetValidationError on JSON decode error
    or Pydantic validation failure with explicit file path and field names.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Preset file not found: {path}")

    try:
        # BOM-safe read
        with open(path, "r", encoding="utf-8-sig") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        raise PresetValidationError(
            f"Invalid JSON syntax in preset file '{path}' at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except Exception as exc:
        raise PresetValidationError(
            f"Failed to read preset file '{path}': {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise PresetValidationError(
            f"Preset file '{path}' must contain a JSON object at root, got {type(data).__name__}"
        )

    try:
        preset = Preset.model_validate(data)
        return preset
    except ValidationError as exc:
        errors_summary = []
        for err in exc.errors():
            loc = " -> ".join(str(p) for p in err.get("loc", []))
            msg = err.get("msg", "Invalid value")
            errors_summary.append(f"Field '{loc}': {msg}")
        details = "\n  - " + "\n  - ".join(errors_summary)
        raise PresetValidationError(
            f"Validation failed for preset '{path}':{details}"
        ) from exc


def validate_preset_file(file_path: str | Path) -> Tuple[bool, Optional[str]]:
    """Validate a preset file, returning (is_valid, error_message_or_none)."""
    try:
        load_preset_file(file_path)
        return True, None
    except Exception as exc:
        return False, str(exc)


def list_presets() -> Dict[str, Preset]:
    """Scan and load all presets from resource/presets and storage/presets.

    Presets in storage/presets override presets in resource/presets with the same ID.
    """
    resource_dir, storage_dir = get_preset_directories()
    presets: Dict[str, Preset] = {}

    # 1. Load built-in resource presets
    if resource_dir.is_dir():
        for file in sorted(resource_dir.glob("*.json")):
            try:
                preset = load_preset_file(file)
                presets[preset.id] = preset
            except Exception as exc:
                # Log or re-raise; for robust listing we raise PresetValidationError
                raise PresetValidationError(f"Error loading built-in preset {file}: {exc}") from exc

    # 2. Load user storage presets (overriding existing)
    if storage_dir.is_dir():
        for file in sorted(storage_dir.glob("*.json")):
            try:
                preset = load_preset_file(file)
                presets[preset.id] = preset
            except Exception as exc:
                raise PresetValidationError(f"Error loading user preset {file}: {exc}") from exc

    return presets


def get_preset(preset_id: str) -> Preset:
    """Retrieve a preset by its ID.

    Raises PresetNotFoundError if the preset ID does not exist.
    """
    all_presets = list_presets()
    if preset_id not in all_presets:
        available = ", ".join(sorted(all_presets.keys())) or "None"
        raise PresetNotFoundError(
            f"Preset with id '{preset_id}' not found. Available presets: {available}"
        )
    return all_presets[preset_id]
