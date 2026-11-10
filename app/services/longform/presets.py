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
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
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
    """Render a visual ASCII tree of presets showing niches and their sub-niches (B1, D8)."""
    if presets is None:
        presets = list_presets()

    if not presets:
        return "No presets found."

    resource_dir, storage_dir = get_preset_directories()
    raw_presets = _collect_raw_presets()
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
        badges = []
        is_custom = False
        if pid in raw_presets:
            p_path = raw_presets[pid][1]
            if storage_dir in p_path.parents or p_path.parent == storage_dir or pid.startswith("custom."):
                is_custom = True
        elif pid.startswith("custom."):
            is_custom = True

        if is_custom:
            badges.append("[kustom]")
        if pid in overrides:
            badges.append("[override]")

        badge_str = (" " + " ".join(badges)) if badges else ""
        info = f"{p.id}{badge_str} ({p.kind}, {p.language}, {p.words_per_minute} WPM) - {p.name}"
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


def slugify_preset_name(name: str) -> str:
    """Convert a human-readable preset name into a safe alphanumeric slug (G2)."""
    import re
    clean = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip().lower()).strip("_")
    return clean or "kustom"


def save_custom_preset(
    preset_data: dict[str, Any],
    allow_overwrite: bool = False,
) -> Path:
    """Save a user-defined custom preset into storage/presets/<id>.json (D8, G2).

    Validates schema and inheritance resolution before persisting to disk.
    Duplicate IDs and duplicate names (case-insensitive) are rejected unless allow_overwrite=True.
    Never writes to resource/presets/.
    """
    resource_dir, storage_dir = get_preset_directories()
    storage_dir.mkdir(parents=True, exist_ok=True)

    raw_id = (preset_data.get("id") or "").strip()
    raw_name = (preset_data.get("name") or "").strip()
    if not raw_id and not raw_name:
        raise PresetValidationError("Field 'id' atau 'name' wajib diisi untuk preset kustom.")

    if not raw_id:
        raw_id = slugify_preset_name(raw_name)

    # Ensure slug has valid format custom.<parent>.<name> or custom.<name>
    if not raw_id.startswith("custom."):
        preset_id = f"custom.{raw_id}"
    else:
        preset_id = raw_id

    preset_data["id"] = preset_id

    # Check for duplicate with built-in presets
    if (resource_dir / f"{raw_id}.json").is_file() or (resource_dir / f"{preset_id}.json").is_file():
        raise PresetValidationError(
            f"ID preset '{raw_id}' bertabrakan dengan preset bawaan sistem. Pilih nama atau ID lain."
        )

    # Case-insensitive duplicate check against all existing presets (G2)
    all_existing = list_presets()
    for existing_id, existing_preset in all_existing.items():
        if not allow_overwrite:
            if existing_id.lower() == preset_id.lower():
                raise PresetValidationError(
                    f"Preset kustom dengan ID '{preset_id}' sudah ada. Gunakan nama lain atau aktifkan pembaruan."
                )
            if raw_name and existing_preset.name.strip().lower() == raw_name.lower() and existing_id.lower() != preset_id.lower():
                raise PresetValidationError(
                    f"Preset dengan nama '{raw_name}' sudah ada ('{existing_id}'). Gunakan nama lain."
                )

    target_path = storage_dir / f"{preset_id}.json"
    if target_path.is_file() and not allow_overwrite:
        raise PresetValidationError(
            f"Preset kustom dengan ID '{preset_id}' sudah ada di '{target_path.name}'. Gunakan nama lain atau aktifkan pembaruan."
        )

    # Set default kind if missing
    if not preset_data.get("kind"):
        preset_data["kind"] = "sub_niche" if preset_data.get("extends") else "niche"
    if not preset_data.get("schema_version"):
        preset_data["schema_version"] = "1.0.0"

    # Validate before saving: write to temp file, validate inheritance & schema, then atomically replace
    temp_path = storage_dir / f".tmp_{preset_id}_{int(time.time())}.json"
    try:
        with open(temp_path, "w", encoding="utf-8") as fp:
            json.dump(preset_data, fp, indent=2, ensure_ascii=False)

        # Validate with load_preset_file
        load_preset_file(temp_path)

        # Validated successfully, move to final target
        if target_path.exists():
            target_path.unlink()
        temp_path.replace(target_path)
        logger.info(f"Custom preset '{preset_id}' saved successfully to {target_path}")
        return target_path
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise PresetValidationError(f"Gagal memvalidasi preset kustom '{preset_id}': {exc}") from exc


def delete_custom_preset(preset_id: str) -> None:
    """Delete a custom preset from storage/presets/ (D8, G2).

    Rejects deletion if the preset is built-in or if it is currently in-use by any project
    or any descendant preset.
    """
    resource_dir, storage_dir = get_preset_directories()

    # Reject if built-in
    if (resource_dir / f"{preset_id}.json").is_file():
        raise PresetValidationError(f"Preset '{preset_id}' adalah preset bawaan dan tidak dapat dihapus.")

    target_path = storage_dir / f"{preset_id}.json"
    if not target_path.is_file():
        raise PresetNotFoundError(f"Preset kustom '{preset_id}' tidak ditemukan di storage/presets/.")

    # Guard: check if any project uses this preset OR any descendant preset (G2)
    from app.services.longform.store import get_projects_root, load_manifest

    all_p = list_presets()
    target_ids = {preset_id}
    for pid, p in all_p.items():
        if p.extends == preset_id:
            target_ids.add(pid)

    child_presets = [pid for pid, p in all_p.items() if p.extends == preset_id]

    root = get_projects_root()
    in_use_projects: List[str] = []
    if root.is_dir():
        for p_dir in root.iterdir():
            if p_dir.is_dir() and (p_dir / "manifest.json").is_file():
                try:
                    manifest = load_manifest(p_dir)
                    chain = getattr(manifest, "preset_inheritance_chain", []) or []
                    if manifest.preset_id in target_ids or any(tid in chain for tid in target_ids):
                        in_use_projects.append(f"'{manifest.title}' ({manifest.project_id})")
                except Exception:
                    pass

    if in_use_projects:
        proj_list = ", ".join(in_use_projects)
        raise PresetValidationError(
            f"Tidak dapat menghapus preset '{preset_id}' karena sedang digunakan oleh proyek: {proj_list}."
        )

    if child_presets:
        raise PresetValidationError(
            f"Tidak dapat menghapus preset '{preset_id}' karena masih memiliki sub-niche turunan: {', '.join(child_presets)}. Hapus sub-niche terlebih dahulu."
        )

    # Proceed with deletion
    try:
        target_path.unlink()
        override_path = storage_dir / f"{preset_id}.override.json"
        if override_path.is_file():
            override_path.unlink()
        logger.info(f"Custom preset '{preset_id}' successfully deleted.")
    except Exception as exc:
        raise PresetError(f"Gagal menghapus berkas preset '{preset_id}': {exc}") from exc


def create_custom_niche_hierarchy(
    niche_name: str,
    sub_niche_name: Optional[str] = None,
    parent_niche_id: Optional[str] = None,
    language: str = "id-ID",
    topic_keywords: Optional[List[str]] = None,
    visual_keywords: Optional[List[str]] = None,
    required_subject_terms: Optional[List[str]] = None,
    negative_terms: Optional[List[str]] = None,
    blocked_terms: Optional[List[str]] = None,
) -> Tuple[str, List[Path]]:
    """Create custom niche and/or sub-niche presets in one coordinated action (G2).

    - If both niche_name and sub_niche_name are provided:
      Creates custom.<niche_slug> (extends umum) AND custom.<niche_slug>.<sub_slug> (extends custom.<niche_slug>).
      Keywords & negative terms are stored in the sub-niche preset.
      Returns (sub_niche_preset_id, [path_niche, path_sub]).
    - If only niche_name is provided:
      Creates custom.<niche_slug> (extends parent_niche_id or umum).
      Keywords & negative terms are stored in this niche.
      Returns (niche_preset_id, [path_niche]).
    - If parent_niche_id is provided and sub_niche_name is provided:
      Creates custom.<parent_slug>.<sub_slug> extending parent_niche_id.
      Keywords & negative terms are stored in the sub-niche preset.
      Returns (sub_niche_preset_id, [path_sub]).
    """
    saved_paths: List[Path] = []
    clean_sub = sub_niche_name.strip() if sub_niche_name and sub_niche_name.strip() not in ("(Gunakan Niche Utama)", "+ Sub-niche baru…") else None
    all_p = list_presets()

    # Case 1: Brand new root niche
    if niche_name and niche_name.strip() not in ("+ Niche baru…", ""):
        clean_niche = niche_name.strip()
        root_slug = slugify_preset_name(clean_niche)
        root_pid = f"custom.{root_slug}"

        # Duplicate check (case-insensitive) (G2)
        for existing_id, existing_p in all_p.items():
            if existing_p.name.strip().lower() == clean_niche.lower():
                raise PresetValidationError(
                    f"Preset dengan nama '{clean_niche}' sudah ada ('{existing_id}'). Gunakan nama lain."
                )
            if existing_id.lower() == root_pid.lower():
                raise PresetValidationError(
                    f"Preset dengan ID '{root_pid}' sudah ada. Gunakan nama lain."
                )

        if clean_sub:
            for existing_id, existing_p in all_p.items():
                if existing_p.name.strip().lower() == clean_sub.lower():
                    raise PresetValidationError(
                        f"Preset dengan nama '{clean_sub}' sudah ada ('{existing_id}'). Gunakan nama lain."
                    )

            root_data = {
                "id": root_pid,
                "name": clean_niche,
                "schema_version": "1.0.0",
                "kind": "niche",
                "extends": "umum",
                "language": language,
                "capcut_text_size": 8,
            }
            p_root = save_custom_preset(root_data, allow_overwrite=False)
            saved_paths.append(p_root)

            sub_slug = slugify_preset_name(clean_sub)
            sub_pid = f"{root_pid}.{sub_slug}"
            sub_data = {
                "id": sub_pid,
                "name": clean_sub,
                "schema_version": "1.0.0",
                "kind": "sub_niche",
                "extends": root_pid,
                "language": language,
                "+topic_keywords": topic_keywords or [],
                "+visual_keywords": visual_keywords or [],
                "+required_subject_terms": required_subject_terms or [],
                "+negative_terms": negative_terms or [],
                "+blocked_terms": blocked_terms or [],
                "capcut_text_size": 8,
            }
            p_sub = save_custom_preset(sub_data, allow_overwrite=False)
            saved_paths.append(p_sub)
            return sub_pid, saved_paths

        else:
            root_data = {
                "id": root_pid,
                "name": clean_niche,
                "schema_version": "1.0.0",
                "kind": "niche",
                "extends": "umum",
                "language": language,
                "+topic_keywords": topic_keywords or [],
                "+visual_keywords": visual_keywords or [],
                "+required_subject_terms": required_subject_terms or [],
                "+negative_terms": negative_terms or [],
                "+blocked_terms": blocked_terms or [],
                "capcut_text_size": 8,
            }
            p_root = save_custom_preset(root_data, allow_overwrite=False)
            saved_paths.append(p_root)
            return root_pid, saved_paths

    # Case 2: Existing root niche selected, new sub-niche typed
    elif parent_niche_id and clean_sub:
        parent_slug = parent_niche_id.removeprefix("custom.")
        sub_slug = slugify_preset_name(clean_sub)
        sub_pid = f"custom.{parent_slug}.{sub_slug}"

        # Duplicate check (case-insensitive) under parent (G2)
        for existing_id, existing_p in all_p.items():
            if existing_p.extends == parent_niche_id and existing_p.name.strip().lower() == clean_sub.lower():
                raise PresetValidationError(
                    f"Sub-niche dengan nama '{clean_sub}' sudah ada di bawah niche ini ('{existing_id}'). Gunakan nama lain."
                )
            if existing_id.lower() == sub_pid.lower():
                raise PresetValidationError(
                    f"Preset dengan ID '{sub_pid}' sudah ada. Gunakan nama lain."
                )

        sub_data = {
            "id": sub_pid,
            "name": clean_sub,
            "schema_version": "1.0.0",
            "kind": "sub_niche",
            "extends": parent_niche_id,
            "language": language,
            "+topic_keywords": topic_keywords or [],
            "+visual_keywords": visual_keywords or [],
            "+required_subject_terms": required_subject_terms or [],
            "+negative_terms": negative_terms or [],
            "+blocked_terms": blocked_terms or [],
            "capcut_text_size": 8,
        }
        p_sub = save_custom_preset(sub_data, allow_overwrite=False)
        saved_paths.append(p_sub)
        return sub_pid, saved_paths

    raise PresetValidationError("Nama niche atau sub-niche baru tidak valid.")


def get_available_niches_for_ui(language: str = "id-ID") -> Tuple[List[str], Dict[str, str]]:
    """Return available root niches filtered by language, with simplified labels (G3).

    - 'umum' is always the first option.
    - 'documentary_id' and 'documentary_en' are hidden from dropdown.
    - Built-in niches matching language (or neutral) are shown.
    - Custom niches are shown with '(buatan sendiri)'.
    - '+ Niche baru…' is always appended at the end.
    """
    all_p = list_presets()
    custom_p = get_custom_presets()

    built_in_labels = {
        "umum": "Umum (Dokumenter Naratif)",
        "automotive": "Otomotif & Rekayasa Mesin",
        "history": "Sejarah & Peradaban",
        "technology": "Teknologi & Sains",
        "true_crime": "Misteri & Investigasi",
    }

    root_ids: List[str] = ["umum"]
    labels: Dict[str, str] = {"umum": built_in_labels["umum"]}

    for pid, p in sorted(all_p.items()):
        # Hide base internal presets (G3)
        if pid in ("umum", "documentary_id", "documentary_en"):
            continue

        # Must be a root niche
        is_root = (not p.extends) or (p.extends in ("documentary_id", "documentary_en", "umum") and p.kind == "niche")
        if not is_root:
            continue

        # Language filter (G3)
        p_lang = getattr(p, "language", "") or ""
        if language.startswith("id"):
            if p_lang.startswith("en"):
                continue
        elif language.startswith("en"):
            if p_lang.startswith("id"):
                continue

        root_ids.append(pid)
        if pid in custom_p:
            labels[pid] = f"{p.name} (buatan sendiri)"
        elif pid in built_in_labels:
            labels[pid] = built_in_labels[pid]
        else:
            labels[pid] = p.name

    root_ids.append("+ Niche baru…")
    labels["+ Niche baru…"] = "➕ Buat Niche Baru…"
    return root_ids, labels


def get_sub_niches_for_ui(parent_niche_id: str, language: str = "id-ID") -> Tuple[List[str], Dict[str, str]]:
    """Return available sub-niches for the chosen parent niche (G3).

    - Starts with '(Gunakan Niche Utama)'.
    - Lists sub-niches extending parent_niche_id.
    - Custom sub-niches marked '(buatan sendiri)'.
    - Ends with '+ Sub-niche baru…'.
    """
    if parent_niche_id in ("+ Niche baru…", ""):
        return [], {}

    all_p = list_presets()
    custom_p = get_custom_presets()

    sub_ids: List[str] = ["(Gunakan Niche Utama)"]
    labels: Dict[str, str] = {"(Gunakan Niche Utama)": "(Gunakan Niche Utama)"}

    built_in_sub_labels = {
        "automotive.muscle_car": "American Muscle Cars & V8",
        "automotive.jdm": "Japanese Domestic Market (JDM)",
        "automotive.supercar": "Supercars & Hypercars",
        "automotive.classic_restoration": "Restorasi Mobil Klasik",
    }

    for pid, p in sorted(all_p.items()):
        if p.extends == parent_niche_id and p.kind == "sub_niche":
            # Language filter
            p_lang = getattr(p, "language", "") or ""
            if language.startswith("id") and p_lang.startswith("en"):
                continue
            if language.startswith("en") and p_lang.startswith("id"):
                continue

            sub_ids.append(pid)
            if pid in custom_p:
                labels[pid] = f"{p.name} (buatan sendiri)"
            elif pid in built_in_sub_labels:
                labels[pid] = built_in_sub_labels[pid]
            else:
                labels[pid] = p.name

    sub_ids.append("+ Sub-niche baru…")
    labels["+ Sub-niche baru…"] = "➕ Buat Sub-Niche Baru…"
    return sub_ids, labels


def get_custom_presets() -> Dict[str, Preset]:
    """Return all user-defined custom presets."""
    all_p = list_presets()
    _, storage_dir = get_preset_directories()
    raw_presets = _collect_raw_presets()
    custom_p: Dict[str, Preset] = {}

    for pid, p in all_p.items():
        if pid in raw_presets:
            p_path = raw_presets[pid][1]
            if storage_dir in p_path.parents or p_path.parent == storage_dir or pid.startswith("custom."):
                custom_p[pid] = p
        elif pid.startswith("custom."):
            custom_p[pid] = p

    return custom_p


def get_builtin_presets() -> Dict[str, Preset]:
    """Return all built-in resource presets."""
    all_p = list_presets()
    custom_p = get_custom_presets()
    return {pid: p for pid, p in all_p.items() if pid not in custom_p}


def suggest_niche_keywords(
    niche_name: str,
    topic: str = "",
    use_llm: bool = True,
) -> dict[str, list[str]]:
    """Propose keywords for a custom niche, with AI when available or heuristic fallback (D8)."""
    import re
    words = re.findall(r"[a-zA-Z0-9]+", f"{niche_name} {topic}".lower())
    meaningful = [
        w for w in dict.fromkeys(words)
        if len(w) > 3 and w not in {"video", "cerita", "tentang", "yang", "dan", "dari", "with", "this", "that"}
    ]

    fallback_result = {
        "topic_keywords": meaningful[:6] if meaningful else [niche_name.lower()],
        "visual_keywords": [f"{w} footage" for w in meaningful[:4]] if meaningful else [niche_name.lower()],
        "required_subject_terms": meaningful[:3] if meaningful else [],
        "negative_terms": ["watermark", "blurry", "low quality"],
        "blocked_terms": ["cartoon", "illustration", "distorted"],
    }

    if not use_llm:
        return fallback_result

    # Try LLM proposal if configured
    try:
        from app.services.longform.llm_adapter import LLMAdapter
        adapter = LLMAdapter()
        adapter.validate_configuration()
        prompt = (
            f"You are a documentary video producer. Propose search keywords and filters for a video project.\n"
            f"Niche: {niche_name}\n"
            f"Topic: {topic or niche_name}\n\n"
            f"Output strictly a JSON object with keys:\n"
            f"- topic_keywords: list of 4-6 topical keywords\n"
            f"- visual_keywords: list of 4-6 specific visual search queries for b-roll\n"
            f"- required_subject_terms: list of 2-3 essential subject keywords\n"
            f"- negative_terms: list of 2-4 visual aspects to avoid\n"
            f"- blocked_terms: list of 2-4 blocked content terms\n"
        )
        resp = adapter.generate(prompt=prompt, fake_kind="outline")
        from app.services.longform.script_generator import extract_json_payload
        data = json.loads(extract_json_payload(resp.content))
        if isinstance(data, dict) and "visual_keywords" in data:
            return {
                "topic_keywords": [str(x).strip() for x in data.get("topic_keywords", []) if str(x).strip()],
                "visual_keywords": [str(x).strip() for x in data.get("visual_keywords", []) if str(x).strip()],
                "required_subject_terms": [str(x).strip() for x in data.get("required_subject_terms", []) if str(x).strip()],
                "negative_terms": [str(x).strip() for x in data.get("negative_terms", []) if str(x).strip()],
                "blocked_terms": [str(x).strip() for x in data.get("blocked_terms", []) if str(x).strip()],
            }
    except Exception as exc:
        logger.debug(f"LLM keyword proposal skipped or failed: {exc}. Using heuristic fallback.")

    return fallback_result
