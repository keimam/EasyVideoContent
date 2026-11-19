"""Safe project deletion, Trash (_trash/) management, and CapCut draft cleanup (Stage 8B.2, F7/F10).

Moves projects safely into ``storage/longform_projects/_trash/<id>_<ts>/``.
Supports stopping an active running job after user confirmation (stop marker ->
bounded wait -> terminate if needed -> lock release -> stopped status on restore),
reliable Windows folder moves (handle GC, read-only bit clearing, backoff retry,
copy-then-remove fallback, explicit locked-file diagnostics), and optional removal
of installed CapCut drafts (``EFS_<id>_*`` / ``MPT_<id>_*``).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from app.services.longform import platform_utils
from app.services.longform.capcut.detector import detect_draft_folder
from app.services.longform.store import (
    ProjectLockedError,
    clear_stop_request,
    get_project_dir,
    get_projects_root,
    is_pid_alive,
    load_manifest,
    request_job_stop,
)

LOCK_FILENAME = ".lock"
TRASH_DIR_NAME = "_trash"
TRASH_META_FILENAME = ".trash_meta.json"


class ProjectTrashError(RuntimeError):
    """Raised when moving a project to Trash fails, preserving locked file details."""

    def __init__(self, message: str, *, locked_files: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.locked_files: List[str] = list(locked_files or [])


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


def _inspect_active_job_and_lock(project_id: str, p_dir: Path) -> Dict[str, Any]:
    """Return active job PID, step label, and live lock state (cleaning stale dead locks)."""
    running_pid: Optional[int] = None
    running_step: str = ""
    lock_pid: Optional[int] = None
    busy_reason: Optional[str] = None

    try:
        from app.services.longform.ui import jobs

        job_st = jobs.get_job_state(project_id)
        if job_st and job_st.get("status") == "running":
            raw_pid = job_st.get("pid")
            step = str(job_st.get("step") or "job")
            if raw_pid is None or is_pid_alive(int(raw_pid)):
                running_pid = int(raw_pid) if raw_pid is not None else None
                running_step = step
                busy_reason = f"Job '{step}' sedang berjalan (PID {running_pid or '-'})"
    except Exception:
        pass

    lock_file = Path(p_dir) / LOCK_FILENAME
    if lock_file.is_file():
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            raw_lp = data.get("pid")
            if raw_lp and is_pid_alive(int(raw_lp)):
                lock_pid = int(raw_lp)
                if not busy_reason:
                    busy_reason = f"Proyek terkunci oleh proses PID {lock_pid}"
            else:
                lock_file.unlink(missing_ok=True)
        except Exception:
            if not busy_reason:
                busy_reason = "Proyek memiliki berkas .lock aktif yang tidak terbaca"

    return {
        "is_busy": bool(busy_reason),
        "reason": busy_reason,
        "running_step": running_step,
        "running_pid": running_pid,
        "lock_pid": lock_pid,
    }


def _check_project_busy_or_locked(project_id: str, p_dir: Path) -> Optional[str]:
    """Backward-compatible wrapper returning reason string if busy/locked, else None."""
    return _inspect_active_job_and_lock(project_id, p_dir)["reason"]


def _force_kill_pid(pid: Optional[int]) -> None:
    """Terminate an external PID (and child tree) cleanly via platform_utils."""
    platform_utils.terminate_process_tree(pid, force=True, timeout=5.0)


def _mark_job_stopped_on_disk(p_dir: Path) -> None:
    """Ensure any 'running'/'cancelled' job_state.json transitions cleanly to 'stopped' for resume after restore."""
    job_file = Path(p_dir) / "job_state.json"
    if not job_file.is_file():
        return
    try:
        data = json.loads(job_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") in ("running", "cancelled"):
            data["status"] = "stopped"
            data["finished_at"] = datetime.now().isoformat()
            data["error_message"] = None
            job_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Could not mark job_state.json as stopped in {p_dir}: {exc}")


def _stop_running_job_and_release_lock(
    project_id: str,
    p_dir: Path,
    *,
    max_wait_seconds: float = 20.0,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """Stop any running job for ``project_id``, wait up to ``max_wait_seconds``, terminate if needed."""
    info = _inspect_active_job_and_lock(project_id, p_dir)
    if not info["is_busy"]:
        return {"stopped": False, "terminated": False}

    def _report(frac: float, msg: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb(min(1.0, max(0.0, frac)), msg)
            except Exception:
                pass

    _report(0.1, "stop_marker")
    request_job_stop(p_dir)

    wait_grace = min(15.0, max(0.5, float(max_wait_seconds) * 0.75))
    t0 = time.monotonic()
    terminated = False

    while (time.monotonic() - t0) < wait_grace:
        cur = _inspect_active_job_and_lock(project_id, p_dir)
        pids = [p for p in (cur["running_pid"], cur["lock_pid"]) if p and p != os.getpid()]
        if not cur["is_busy"] or not any(is_pid_alive(p) for p in pids):
            break
        elapsed = time.monotonic() - t0
        _report(0.1 + 0.6 * (elapsed / wait_grace), "waiting")
        time.sleep(0.25)

    # Terminate if any child job PID is still alive
    cur_after = _inspect_active_job_and_lock(project_id, p_dir)
    still_pids = {p for p in (cur_after["running_pid"], cur_after["lock_pid"]) if p and p != os.getpid() and is_pid_alive(p)}
    if still_pids or cur_after["is_busy"]:
        _report(0.75, "terminating")
        terminated = True
        try:
            from app.services.longform.ui import jobs

            jobs.cancel_job(project_id)
        except Exception:
            pass
        for spid in still_pids:
            _force_kill_pid(spid)
        # Brief wait for OS file handles to close after termination
        end_kill = time.monotonic() + min(5.0, max(0.5, max_wait_seconds - wait_grace))
        while time.monotonic() < end_kill:
            if not any(is_pid_alive(p) for p in still_pids):
                break
            time.sleep(0.2)

    clear_stop_request(p_dir)
    lock_file = Path(p_dir) / LOCK_FILENAME
    if lock_file.is_file():
        # If an in-process lock or dead PID lock persists, only remove if PID dead
        try:
            ldata = json.loads(lock_file.read_text(encoding="utf-8"))
            lpid = ldata.get("pid")
            if not lpid or not is_pid_alive(int(lpid)) or lpid == os.getpid():
                lock_file.unlink(missing_ok=True)
        except Exception:
            lock_file.unlink(missing_ok=True)

    _mark_job_stopped_on_disk(p_dir)
    _report(0.9, "stopped")
    return {"stopped": True, "terminated": terminated}


def _clear_readonly_tree(root: Path) -> None:
    """Clear read-only attributes recursively via platform_utils."""
    platform_utils.clear_readonly_tree(root)


def _remove_tree_with_retries(src_dir: Path, *, retries: int = 3) -> List[str]:
    """Remove directory tree bottom-up cleanly via platform_utils."""
    return platform_utils.remove_tree_with_retries(src_dir, retries=retries)


def _reliable_move_dir_to_trash(src_dir: Path, dest_dir: Path) -> None:
    """Move project folder cleanly to Trash via platform_utils with ProjectTrashError wrapping."""
    try:
        platform_utils.move_dir_to_trash_reliable(src_dir, dest_dir, move_fn=shutil.move)
    except PermissionError as exc:
        raise ProjectTrashError(str(exc), locked_files=getattr(exc, "locked_files", None)) from exc
    except OSError as exc:
        raise ProjectTrashError(f"Gagal memindahkan berkas proyek ke Sampah: {exc}") from exc


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
    busy_info = _inspect_active_job_and_lock(project_id, p_dir)

    return {
        "project_id": project_id,
        "title": manifest.title,
        "preset_id": manifest.preset_id,
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "capcut_drafts": [str(d) for d in drafts],
        "capcut_draft_count": len(drafts),
        "is_locked_or_running": bool(busy_info["is_busy"]),
        "lock_reason": busy_info["reason"],
        "running_step": busy_info["running_step"],
    }


def soft_delete_project(
    project_id: str,
    *,
    delete_capcut_drafts: bool = False,
    stop_running_job: bool = False,
    max_wait_seconds: float = 20.0,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    capcut_drafts_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Move project folder to ``_trash/<id>_<timestamp>/``.

    When ``stop_running_job=True``, stops active jobs (STOP marker -> bounded wait
    -> terminate if needed -> release lock -> mark stopped) before moving.
    When ``stop_running_job=False`` (strict guard), raises ``ProjectLockedError``
    if a job/lock is active.
    """
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir() or not (p_dir / "manifest.json").is_file():
        raise FileNotFoundError(f"Project '{project_id}' not found.")

    busy_info = _inspect_active_job_and_lock(project_id, p_dir)
    job_stopped_info = {"stopped": False, "terminated": False}
    if busy_info["is_busy"]:
        if not stop_running_job:
            raise ProjectLockedError(str(busy_info["reason"]))
        job_stopped_info = _stop_running_job_and_release_lock(
            project_id,
            p_dir,
            max_wait_seconds=max_wait_seconds,
            progress_cb=progress_cb,
        )

    manifest = load_manifest(p_dir)
    size_bytes = compute_folder_size_bytes(p_dir)

    trash_root = get_trash_root()
    trash_root.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    trash_folder_name = f"{project_id}_{ts_str}"
    dest_dir = trash_root / trash_folder_name

    _reliable_move_dir_to_trash(p_dir, dest_dir)

    meta = {
        "project_id": project_id,
        "title": manifest.title,
        "preset_id": manifest.preset_id,
        "deleted_at": datetime.now().isoformat(),
        "size_bytes": size_bytes,
        "trash_id": trash_folder_name,
        "job_was_stopped": bool(job_stopped_info["stopped"]),
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
                _remove_tree_with_retries(d_path, retries=2)
                removed_drafts.append(str(d_path))
            except Exception as exc:
                logger.warning(f"Failed removing CapCut draft '{d_path}': {exc}")

    if progress_cb is not None:
        try:
            progress_cb(1.0, "moved")
        except Exception:
            pass

    logger.info(
        f"Moved project '{project_id}' to trash '{dest_dir}' "
        f"(size={size_bytes}B, job_stopped={job_stopped_info['stopped']}, capcut_drafts_deleted={len(removed_drafts)})"
    )
    return {
        "project_id": project_id,
        "title": manifest.title,
        "trash_id": trash_folder_name,
        "trash_path": str(dest_dir),
        "size_bytes": size_bytes,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "job_stopped": bool(job_stopped_info["stopped"]),
        "job_terminated": bool(job_stopped_info["terminated"]),
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

    _reliable_move_dir_to_trash(src_dir, dest_dir)
    meta_file = dest_dir / TRASH_META_FILENAME
    if meta_file.is_file():
        try:
            meta_file.unlink()
        except OSError:
            pass
    stale_lock = dest_dir / LOCK_FILENAME
    if stale_lock.is_file():
        stale_lock.unlink(missing_ok=True)
    clear_stop_request(dest_dir)
    _mark_job_stopped_on_disk(dest_dir)

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
    locked = _remove_tree_with_retries(src_dir, retries=3)
    if locked:
        raise ProjectTrashError(
            f"Gagal menghapus permanen karena berkas masih terkunci: {', '.join(locked[:5])}",
            locked_files=locked,
        )
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
            locked = _remove_tree_with_retries(p, retries=3)
            if locked:
                raise ProjectTrashError(
                    f"Gagal mengosongkan sampah pada '{entry['project_id']}': berkas masih terkunci ({', '.join(locked[:5])})",
                    locked_files=locked,
                )
            deleted_count += 1
            bytes_freed += sz
    logger.info(f"Emptied Long Video trash: {deleted_count} projects, {bytes_freed}B freed")
    return {
        "deleted_count": deleted_count,
        "bytes_freed": bytes_freed,
        "mb_freed": round(bytes_freed / (1024 * 1024), 2),
    }
