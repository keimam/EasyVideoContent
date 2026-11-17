"""Safe project deletion, Trash (_trash/) management, and CapCut draft cleanup (Stage 8B.2, F7).

Deletes projects safely by moving them to ``storage/longform_projects/_trash/<id>_<ts>/``
rather than wiping them immediately. Rejects deletion when a background job or
PID lock is active. Linked CapCut Desktop drafts (``EFS_<id>_*`` / ``MPT_<id>_*``)
are NEVER touched unless explicitly opted in. Global visual caches and custom
niche presets are never deleted.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from app.services.longform.capcut.detector import detect_draft_folder
from app.services.longform.store import (
    ProjectLockedError,
    get_project_dir,
    get_projects_root,
    is_pid_alive,
    load_manifest,
)

LOCK_FILENAME = ".lock"
TRASH_DIR_NAME = "_trash"
TRASH_META_FILENAME = ".trash_meta.json"


def get_trash_root() -> Path:
    """Return path to ``storage/longform_projects/_trash/``."""
    return get_projects_root() / TRASH_DIR_NAME


def compute_folder_size_bytes(folder: Path) -> int:
    """Compute total disk size in bytes for all files under ``folder``."""
    p = Path(folder)
    if not p.is_dir():
        return 0
    total = 0
    try:
        for f in p.rglob("*"):
            if f.is_file():
                try:
                    total += int(f.stat().st_size)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _matches_project_draft_name(folder_name: str, project_id: str) -> bool:
    """Return True if a CapCut draft directory name strictly belongs to ``project_id``."""
    pid = (project_id or "").strip()
    if not pid:
        return False
    return (
        folder_name == f"EFS_{pid}"
        or folder_name.startswith(f"EFS_{pid}_")
        or folder_name == f"MPT_{pid}"
        or folder_name.startswith(f"MPT_{pid}_")
    )


def find_project_capcut_drafts(
    project_id: str,
    *,
    capcut_drafts_dir: Optional[Path] = None,
) -> List[Path]:
    """Find installed CapCut draft directories belonging to ``project_id``."""
    draft_root = capcut_drafts_dir if capcut_drafts_dir is not None else detect_draft_folder()
    if not draft_root or not Path(draft_root).is_dir():
        return []
    matches: List[Path] = []
    try:
        for entry in sorted(Path(draft_root).iterdir(), key=lambda x: x.name):
            if entry.is_dir() and _matches_project_draft_name(entry.name, project_id):
                matches.append(entry)
    except OSError as exc:
        logger.warning(f"Could not inspect CapCut draft directory '{draft_root}': {exc}")
    return matches


def _check_project_busy_or_locked(project_id: str, p_dir: Path) -> Optional[str]:
    """Return a non-empty reason string if project currently has an active job or live lock."""
    try:
        from app.services.longform.ui import jobs

        job_st = jobs.get_job_state(project_id)
        if job_st and job_st.get("status") == "running":
            pid = job_st.get("pid")
            if not pid or is_pid_alive(int(pid)):
                step = job_st.get("step") or "job"
                return f"Project '{project_id}' has an active running job ('{step}', PID {pid})."
    except Exception:
        pass

    lock_file = Path(p_dir) / LOCK_FILENAME
    if lock_file.is_file():
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            lock_pid = data.get("pid")
            if lock_pid and is_pid_alive(int(lock_pid)):
                return f"Project '{project_id}' is currently locked by PID {lock_pid}."
            # Stale lock from dead process can be cleaned up safely
            lock_file.unlink(missing_ok=True)
        except Exception:
            return f"Project '{project_id}' holds an unreadable active lock file."
    return None


def get_project_delete_preview(
    project_id: str,
    *,
    capcut_drafts_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Inspect size, linked CapCut drafts, and lock status before showing delete dialog."""
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir() or not (p_dir / "manifest.json").is_file():
        raise FileNotFoundError(f"Project '{project_id}' not found.")

    manifest = load_manifest(p_dir)
    size_bytes = compute_folder_size_bytes(p_dir)
    drafts = find_project_capcut_drafts(project_id, capcut_drafts_dir=capcut_drafts_dir)
    busy_reason = _check_project_busy_or_locked(project_id, p_dir)

    return {
        "project_id": project_id,
        "title": manifest.title,
        "preset_id": manifest.preset_id,
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "capcut_drafts": [str(d) for d in drafts],
        "capcut_draft_count": len(drafts),
        "is_locked_or_running": bool(busy_reason),
        "lock_reason": busy_reason,
    }


def soft_delete_project(
    project_id: str,
    *,
    delete_capcut_drafts: bool = False,
    capcut_drafts_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Move project folder to ``_trash/<id>_<timestamp>/`` after verifying it is idle/unlocked."""
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir() or not (p_dir / "manifest.json").is_file():
        raise FileNotFoundError(f"Project '{project_id}' not found.")

    busy_reason = _check_project_busy_or_locked(project_id, p_dir)
    if busy_reason:
        raise ProjectLockedError(busy_reason)

    manifest = load_manifest(p_dir)
    size_bytes = compute_folder_size_bytes(p_dir)

    trash_root = get_trash_root()
    trash_root.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    trash_folder_name = f"{project_id}_{ts_str}"
    dest_dir = trash_root / trash_folder_name

    shutil.move(str(p_dir), str(dest_dir))

    meta = {
        "project_id": project_id,
        "title": manifest.title,
        "preset_id": manifest.preset_id,
        "deleted_at": datetime.now().isoformat(),
        "size_bytes": size_bytes,
        "trash_id": trash_folder_name,
    }
    meta_file = dest_dir / TRASH_META_FILENAME
    try:
        meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed writing trash metadata for '{project_id}': {exc}")

    removed_drafts: List[str] = []
    if delete_capcut_drafts:
        for d_path in find_project_capcut_drafts(project_id, capcut_drafts_dir=capcut_drafts_dir):
            try:
                shutil.rmtree(d_path)
                removed_drafts.append(str(d_path))
            except Exception as exc:
                logger.warning(f"Failed removing CapCut draft '{d_path}': {exc}")

    logger.info(
        f"Moved project '{project_id}' to trash '{dest_dir}' "
        f"(size={size_bytes}B, capcut_drafts_deleted={len(removed_drafts)})"
    )
    return {
        "project_id": project_id,
        "title": manifest.title,
        "trash_id": trash_folder_name,
        "trash_path": str(dest_dir),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "capcut_drafts_deleted": removed_drafts,
    }


def list_trashed_projects() -> List[Dict[str, Any]]:
    """Return list of projects currently inside ``storage/longform_projects/_trash/``."""
    trash_root = get_trash_root()
    if not trash_root.is_dir():
        return []

    items: List[Dict[str, Any]] = []
    for entry in trash_root.iterdir():
        if not entry.is_dir():
            continue
        meta_file = entry / TRASH_META_FILENAME
        meta: Dict[str, Any] = {}
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        manifest_file = entry / "manifest.json"
        if not meta.get("project_id") and manifest_file.is_file():
            try:
                m = load_manifest(entry)
                meta["project_id"] = m.project_id
                meta["title"] = m.title
                meta["preset_id"] = m.preset_id
            except Exception:
                pass
        pid = str(meta.get("project_id") or entry.name.rsplit("_", 3)[0])
        title = str(meta.get("title") or pid)
        size_bytes = int(meta.get("size_bytes") or compute_folder_size_bytes(entry))
        deleted_at = str(meta.get("deleted_at") or datetime.fromtimestamp(entry.stat().st_mtime).isoformat())
        items.append({
            "trash_id": entry.name,
            "trash_path": str(entry),
            "project_id": pid,
            "title": title,
            "preset_id": str(meta.get("preset_id") or ""),
            "deleted_at": deleted_at,
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
        })

    items.sort(key=lambda x: x.get("deleted_at", ""), reverse=True)
    return items


def _resolve_trash_entry(identifier: str) -> Dict[str, Any]:
    ident = (identifier or "").strip()
    entries = list_trashed_projects()
    for item in entries:
        if item["trash_id"] == ident or item["project_id"] == ident:
            return item
    raise FileNotFoundError(f"Trashed project '{identifier}' not found in '{get_trash_root()}'.")


def restore_trashed_project(identifier: str) -> Dict[str, Any]:
    """Restore a project from ``_trash/`` back to active ``storage/longform_projects/``."""
    entry = _resolve_trash_entry(identifier)
    src_dir = Path(entry["trash_path"])
    pid = entry["project_id"]
    dest_dir = get_project_dir(pid)
    if dest_dir.exists():
        raise FileExistsError(
            f"Cannot restore '{pid}': active project directory '{dest_dir}' already exists."
        )

    shutil.move(str(src_dir), str(dest_dir))
    meta_file = dest_dir / TRASH_META_FILENAME
    if meta_file.is_file():
        try:
            meta_file.unlink()
        except OSError:
            pass
    # Also remove any stale lock left over
    stale_lock = dest_dir / LOCK_FILENAME
    if stale_lock.is_file():
        stale_lock.unlink(missing_ok=True)

    logger.info(f"Restored project '{pid}' from trash '{src_dir}' -> '{dest_dir}'")
    return {
        "project_id": pid,
        "title": entry["title"],
        "restored_path": str(dest_dir),
    }


def permanently_delete_trashed_project(identifier: str) -> Dict[str, Any]:
    """Permanently remove a single project entry from ``_trash/``."""
    entry = _resolve_trash_entry(identifier)
    src_dir = Path(entry["trash_path"])
    size_bytes = int(entry.get("size_bytes") or compute_folder_size_bytes(src_dir))
    shutil.rmtree(src_dir)
    logger.info(f"Permanently deleted trashed project '{entry['trash_id']}' ({size_bytes}B freed)")
    return {
        "trash_id": entry["trash_id"],
        "project_id": entry["project_id"],
        "title": entry["title"],
        "bytes_freed": size_bytes,
        "mb_freed": round(size_bytes / (1024 * 1024), 2),
    }


def empty_trash() -> Dict[str, Any]:
    """Permanently delete all projects currently inside ``_trash/``."""
    entries = list_trashed_projects()
    deleted_count = 0
    bytes_freed = 0
    for entry in entries:
        p = Path(entry["trash_path"])
        sz = int(entry.get("size_bytes") or compute_folder_size_bytes(p))
        if p.is_dir():
            shutil.rmtree(p)
            deleted_count += 1
            bytes_freed += sz
    logger.info(f"Emptied Long Video trash: {deleted_count} projects, {bytes_freed}B freed")
    return {
        "deleted_count": deleted_count,
        "bytes_freed": bytes_freed,
        "mb_freed": round(bytes_freed / (1024 * 1024), 2),
    }
