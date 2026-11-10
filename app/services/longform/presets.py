"""Preset Loader, Inheritance Resolution, and Validation for Longform Video Generation.

Loads presets from built-in 'resource/presets/*.json' and user-defined
'storage/presets/*.json'. Supports multi-tier inheritance (Addendum 1.5 B1):
- child overrides parent fields
- dicts merged recursively per key
- lists replaced entirely by default, or appended if key starts with '+' (e.g. '+topic_keywords')
- cycle detection and missing parent validation with explicit errors (citing file + id)
- tree formatting for CLI
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.services.longform.schema import Preset


class PresetError(Exception):
    """Base exception for preset loading and validation errors."""
    pass


class PresetNotFoundError(PresetError):
    """Raised when a requested preset ID is not found."""
    pass


class PresetValidationError(PresetError):
    """Raised when a preset fails schema or JSON validation or inheritance checks."""
    pass


def get_preset_directories() -> Tuple[Path, Path]:
    """Return the absolute paths for resource presets and storage presets."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    resource_dir = repo_root / "resource" / "presets"
    storage_dir = repo_root / "storage" / "presets"
    return resource_dir, storage_dir


def deep_merge_dicts(parent: dict[str, Any], child: dict[str, Any], strip_plus: bool = True) -> dict[str, Any]:
    """Merge child dict into parent dict recursively with '+' list append support (B1).

    - Keys starting with '+' (e.g. '+topic_keywords') append child list to parent list.
    - Dicts are merged recursively per key.
    - Other values in child override parent.
    - If strip_plus is True, any remaining '+' keys are removed from the result.
    """
    merged = dict(parent)

    for k, v in child.items():
        if k.startswith("+"):
            base_k = k[1:]
            parent_val = merged.get(base_k, [])
            if not isinstance(parent_val, list):
                parent_val = [parent_val] if parent_val is not None else []
            if not isinstance(v, list):
                raise PresetValidationError(
                    f"Append prefix '+' can only be used on list fields, got {type(v).__name__} for key '{k}'"
                )
            merged[base_k] = list(parent_val) + list(v)
        elif isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = deep_merge_dicts(merged[k], v, strip_plus=strip_plus)
        else:
            merged[k] = v

    if strip_plus:
        return {k: v for k, v in merged.items() if not k.startswith("+")}
    return merged


def _load_raw_preset_file(file_path: Path) -> dict[str, Any]:
    """Load JSON content from file safely handling UTF-8 BOM."""
    if not file_path.is_file():
        raise FileNotFoundError(f"Preset file not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8-sig") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        raise PresetValidationError(
            f"Invalid JSON syntax in preset file '{file_path}' at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except Exception as exc:
        raise PresetValidationError(
            f"Failed to read preset file '{file_path}': {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise PresetValidationError(
            f"Preset file '{file_path}' must contain a JSON object at root, got {type(data).__name__}"
        )

    return data


def get_preset_override_path(preset_id: str) -> Path:
    """Return path to potential override file for a preset."""
    _, storage_dir = get_preset_directories()
    return storage_dir / f"{preset_id}.override.json"


def has_preset_override(preset_id: str) -> bool:
    """Check if an override file exists for the given preset."""
    return get_preset_override_path(preset_id).is_file()


def get_all_preset_overrides() -> dict[str, Path]:
    """Return a mapping of preset_id -> override Path for all existing overrides."""
    _, storage_dir = get_preset_directories()
    overrides: dict[str, Path] = {}
    if storage_dir.is_dir():
        for f in sorted(storage_dir.glob("*.override.json")):
            try:
                data = _load_raw_preset_file(f)
                pid = data.get("id") or f.name[:-len(".override.json")]
            except Exception:
                pid = f.name[:-len(".override.json")]
            overrides[pid] = f
    return overrides


def _collect_raw_presets() -> dict[str, Tuple[dict[str, Any], Path]]:
    """Scan resource/presets and storage/presets to collect raw dictionary data.

    Presets in storage/presets override presets in resource/presets with the same ID.
    Files ending with .override.json are treated as partial override layers and deep-merged
    into the corresponding preset definition.
    """
    resource_dir, storage_dir = get_preset_directories()
    raw_presets: dict[str, Tuple[dict[str, Any], Path]] = {}

    # 1. Built-in presets
    if resource_dir.is_dir():
        for f in sorted(resource_dir.glob("*.json")):
            if f.name.endswith(".override.json"):
                continue
            data = _load_raw_preset_file(f)
            pid = data.get("id")
            if not pid:
                raise PresetValidationError(f"Preset file '{f}' is missing required root 'id' field.")
            raw_presets[pid] = (data, f)

    # 2. User storage presets (standalone presets, excluding overrides)
    if storage_dir.is_dir():
        for f in sorted(storage_dir.glob("*.json")):
            if f.name.endswith(".override.json"):
                continue
            data = _load_raw_preset_file(f)
            pid = data.get("id")
            if not pid:
                raise PresetValidationError(f"Preset file '{f}' is missing required root 'id' field.")
            raw_presets[pid] = (data, f)

    # 3. User storage override layers (*.override.json)
    if storage_dir.is_dir():
        for f in sorted(storage_dir.glob("*.override.json")):
            try:
                data = _load_raw_preset_file(f)
            except Exception as exc:
                logger.warning(f"Failed to read override file '{f}': {exc}")
                continue

            target_pid = data.get("id") or f.name[:-len(".override.json")]
            if target_pid in raw_presets:
                base_dict, base_path = raw_presets[target_pid]
                merged = deep_merge_dicts(base_dict, data, strip_plus=False)
                raw_presets[target_pid] = (merged, base_path)
            else:
                logger.warning(f"Override file '{f}' specifies preset '{target_pid}' which was not found.")

    return raw_presets


def _resolve_preset(
    preset_id: str,
    raw_presets: dict[str, Tuple[dict[str, Any], Path]],
    visited: list[str],
) -> Tuple[dict[str, Any], list[str]]:
    """Recursively resolve preset inheritance with cycle detection (B1).

    Returns (resolved_dict, inheritance_chain_list).
    """
    if preset_id in visited:
        cycle_chain = " -> ".join(visited + [preset_id])
        raise PresetValidationError(f"Preset inheritance cycle detected: {cycle_chain}")

    if preset_id not in raw_presets:
        caller_id = visited[-1] if visited else "unknown"
        caller_file = raw_presets[caller_id][1] if caller_id in raw_presets else "unknown"
        raise PresetValidationError(
            f"Preset '{caller_id}' (in '{caller_file}') extends unknown parent preset '{preset_id}'"
        )

    raw_data, file_path = raw_presets[preset_id]
    parent_id = raw_data.get("extends")

    if not parent_id:
        # Base preset (root niche)
        cleaned = {k: v for k, v in raw_data.items() if not k.startswith("+")}
        return cleaned, [preset_id]

    parent_resolved, parent_chain = _resolve_preset(parent_id, raw_presets, visited + [preset_id])
    merged = deep_merge_dicts(parent_resolved, raw_data)
    chain = parent_chain + [preset_id]
    return merged, chain


def load_preset_file(file_path: str | Path) -> Preset:
    """Load and validate a single preset file, resolving its inheritance if it extends a parent."""
    path = Path(file_path).resolve()
    raw_presets = _collect_raw_presets()
    raw_data = _load_raw_preset_file(path)
    pid = raw_data.get("id")
    if not pid:
        if path.name.endswith(".override.json"):
            pid = path.name[:-len(".override.json")]
        else:
            raise PresetValidationError(f"Preset file '{path}' is missing required 'id' field.")

    if path.name.endswith(".override.json"):
        if pid in raw_presets:
            base_dict, _ = raw_presets[pid]
            merged_dict = deep_merge_dicts(base_dict, raw_data)
            raw_presets[pid] = (merged_dict, path)
        else:
            raise PresetValidationError(f"Preset override file '{path}' targets unknown preset '{pid}'.")
    else:
        # Put this file in raw_presets map so resolution can use it
        raw_presets[pid] = (raw_data, path)

    resolved_dict, _ = _resolve_preset(pid, raw_presets, [])

    try:
        preset = Preset.model_validate(resolved_dict)
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
    """Scan, resolve inheritance, and validate all presets."""
    raw_presets = _collect_raw_presets()
    resolved_presets: Dict[str, Preset] = {}

    for pid in raw_presets:
        resolved_dict, _ = _resolve_preset(pid, raw_presets, [])
        file_path = raw_presets[pid][1]
        try:
            preset = Preset.model_validate(resolved_dict)
            resolved_presets[pid] = preset
        except ValidationError as exc:
            errors_summary = [
                f"Field '{' -> '.join(str(p) for p in err.get('loc', []))}': {err.get('msg')}"
                for err in exc.errors()
            ]
            details = "\n  - " + "\n  - ".join(errors_summary)
            raise PresetValidationError(
                f"Validation failed for preset '{file_path}':{details}"
            ) from exc

    return resolved_presets


def get_preset(preset_id: str) -> Preset:
    """Retrieve a fully resolved preset by its ID."""
    all_presets = list_presets()
    if preset_id not in all_presets:
        available = ", ".join(sorted(all_presets.keys())) or "None"
        raise PresetNotFoundError(
            f"Preset with id '{preset_id}' not found. Available presets: {available}"
        )
    return all_presets[preset_id]


def get_preset_inheritance_chain(preset_id: str) -> List[str]:
    """Return the ordered list of preset IDs in the inheritance hierarchy for preset_id."""
    raw_presets = _collect_raw_presets()
    if preset_id not in raw_presets:
        raise PresetNotFoundError(f"Preset with id '{preset_id}' not found.")
    _, chain = _resolve_preset(preset_id, raw_presets, [])
    return chain


def format_preset_tree(presets: Dict[str, Preset] | None = None) -> str:
    """Render a visual ASCII tree of presets showing niches and their sub-niches (B1)."""
    if presets is None:
        presets = list_presets()

    if not presets:
        return "No presets found."

    overrides = get_all_preset_overrides()

    # Identify roots (niche or no extends) and build children map
    roots: List[str] = []
    children_map: Dict[str, List[str]] = {}

    for pid, p in sorted(presets.items()):
        parent = p.extends
        if parent and parent in presets:
            if parent not in children_map:
                children_map[parent] = []
            children_map[parent].append(pid)
        else:
            roots.append(pid)

    lines: List[str] = []
    lines.append(f"Available Presets ({len(presets)}):")
    lines.append("-" * 85)

    def _render_node(pid: str, prefix: str = "", is_last: bool = True, is_root: bool = False):
        p = presets[pid]
        ovr_badge = " [OVERRIDE]" if pid in overrides else ""
        info = f"{p.id}{ovr_badge} ({p.kind}, {p.language}, {p.words_per_minute} WPM) - {p.name}"
        if is_root:
            lines.append(info)
        else:
            branch = "└── " if is_last else "├── "
            lines.append(f"{prefix}{branch}{info}")

        child_prefix = prefix + ("    " if is_last else "│   ") if not is_root else "  "
        sub_kids = sorted(children_map.get(pid, []))
        for idx, kid_id in enumerate(sub_kids):
            _render_node(kid_id, child_prefix, is_last=(idx == len(sub_kids) - 1), is_root=False)

    for r_idx, root_id in enumerate(sorted(roots)):
        _render_node(root_id, "", is_last=(r_idx == len(roots) - 1), is_root=True)

    lines.append("-" * 85)
    return "\n".join(lines)
