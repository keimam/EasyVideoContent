"""First-run wizard setup state, marker persistence, and starter project builder (Stage 8C.3, E1)."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from app.services.branding.constants import APP_VERSION
from app.services.longform import app_paths


SETUP_MARKER_FILENAME = ".efs_setup_complete.json"
STUDIO_STATE_FILENAME = ".efs_studio_state.json"


def get_setup_marker_path() -> Path:
    """Return path to the setup-completed marker inside ``get_data_dir()``."""
    return app_paths.get_data_dir() / SETUP_MARKER_FILENAME


def get_studio_state_path() -> Path:
    """Return path to lightweight user studio preferences inside ``get_data_dir()``."""
    return app_paths.get_data_dir() / STUDIO_STATE_FILENAME


def _atomic_write_json(target: Path, payload: Dict[str, Any]) -> Path:
    """Write JSON atomically to avoid partial/corrupted state files."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2, ensure_ascii=False)
            fp.write("\n")
        os.replace(tmp_name, target)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return target


def read_studio_state() -> Dict[str, Any]:
    """Load persisted studio preferences from `<data_dir>/.efs_studio_state.json`."""
    path = get_studio_state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning(f"Failed reading studio state '{path}': {exc}")
        return {}


def update_studio_state(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `updates` into studio preferences inside `get_data_dir()` and persist atomically."""
    current = read_studio_state()
    current.update(updates or {})
    _atomic_write_json(get_studio_state_path(), current)
    return current


def get_custom_capcut_drafts_dir() -> Optional[Path]:
    """Return user-configured CapCut drafts directory override if set and non-empty."""
    raw = str(read_studio_state().get("capcut_drafts_dir") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def set_custom_capcut_drafts_dir(folder: Optional[str | Path]) -> Optional[Path]:
    """Save or clear manual CapCut drafts folder override in studio state."""
    cleaned = str(folder or "").strip()
    if not cleaned:
        update_studio_state({"capcut_drafts_dir": ""})
        return None
    resolved = str(Path(cleaned).expanduser())
    update_studio_state({"capcut_drafts_dir": resolved})
    return Path(resolved)


def is_setup_completed() -> bool:
    """Return True when the active data directory contains a setup completion marker."""
    return get_setup_marker_path().is_file()


def read_setup_marker() -> Optional[Dict[str, Any]]:
    """Read setup completion marker metadata if present."""
    marker = get_setup_marker_path()
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"completed": True}
    except Exception:
        return {"completed": True}


def mark_setup_completed(*, skipped: bool = False, extra: Optional[Dict[str, Any]] = None) -> Path:
    """Write `.efs_setup_complete.json` inside `get_data_dir()`."""
    app_paths.ensure_user_data_initialized()
    payload: Dict[str, Any] = {
        "completed": True,
        "skipped": bool(skipped),
        "app_version": APP_VERSION,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    return _atomic_write_json(get_setup_marker_path(), payload)


def reset_setup_marker() -> bool:
    """Remove setup completion marker so first-run wizard displays again."""
    marker = get_setup_marker_path()
    existed = marker.is_file()
    marker.unlink(missing_ok=True)
    return existed


_SAMPLE_SCRIPT_ID = """# Bab 1: Mengapa Terumbu Karang Penting bagi Lautan
Di kedalaman perairan tropis yang hangat, terumbu karang berdiri bagaikan kota bawah laut yang penuh kehidupan. Meskipun hanya menutupi kurang dari satu persen dasar samudra, ekosistem ini menjadi rumah bagi seperempat seluruh spesies laut di bumi. Dari ikan karang kecil hingga penyu laut, semuanya bergantung pada celah dan perlindungan struktur kapur alami ini.

# Bab 2: Cara Menjaga Kelestarian Terumbu Karang
Hari ini, perubahan suhu air laut dan polusi plastik mengancam kelangsungan terumbu karang di berbagai belahan dunia. Namun, upaya konservasi sederhana seperti mengurangi sampah sekali pakai, mendukung wisata bahari yang ramah lingkungan, dan menanam ulang fragmen karang mulai menunjukkan hasil positif bagi pemulihan ekosistem laut kita.
"""

_SAMPLE_SCRIPT_EN = """# Chapter 1: Why Coral Reefs Matter to Our Oceans
Beneath warm tropical waters, coral reefs rise like bustling underwater cities teeming with life. Even though they cover less than one percent of the ocean floor, these living structures shelter roughly a quarter of all marine species on Earth. Tiny reef fish, sea turtles, and countless invertebrates rely on coral ridges for food and protection.

# Chapter 2: Protecting Reefs for the Future
Rising ocean temperatures and coastal pollution have placed many reefs under severe stress. Fortunately, practical conservation steps such as reducing single-use plastics, practicing responsible marine tourism, and restoring nursery-grown coral fragments are already helping battered reefs recover across the globe.
"""


def create_starter_sample_project(
    lang: str = "id",
    *,
    projects_root: Optional[Path] = None,
) -> Tuple[str, Path]:
    """Create a small ready-to-run starter documentary project from wizard completion (E1)."""
    from app.services.longform.store import create_project

    is_id = str(lang or "id").lower().startswith("id")
    base_id = "proyek_contoh_karang" if is_id else "starter_coral_reefs"
    root = Path(projects_root).resolve() if projects_root else app_paths.get_longform_projects_dir(create=True)

    candidate_id = base_id
    idx = 2
    while (root / candidate_id / "manifest.json").is_file():
        candidate_id = f"{base_id}_{idx}"
        idx += 1

    title = (
        "Proyek Contoh: Kota Bawah Laut Terumbu Karang"
        if is_id
        else "Starter Project: Underwater Cities of Coral Reefs"
    )
    topic = (
        "Keajaiban ekosistem terumbu karang di perairan tropis"
        if is_id
        else "Wonders of coral reef ecosystems in tropical oceans"
    )
    preset_id = "documentary_id" if is_id else "documentary_en"
    script_md = _SAMPLE_SCRIPT_ID if is_id else _SAMPLE_SCRIPT_EN
    lang_tag = "id-ID" if is_id else "en-US"

    manifest, p_dir = create_project(
        project_id=candidate_id,
        title=title,
        preset_id=preset_id,
        script_markdown=script_md,
        aspect_ratio="16:9",
        topic=topic,
        target_duration_minutes=2,
        language=lang_tag,
        projects_root=root,
    )
    return manifest.project_id, p_dir
