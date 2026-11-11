"""Background job runner for Long Video WebUI operations (B2).

Runs longform CLI operations as detached background subprocesses,
with PID-level locking, process group signaling (CTRL_BREAK_EVENT on Windows),
persistent state in project directories, and manifest-backed progress monitoring.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from app.services.longform.schema import ItemStatus, ProjectManifest
from app.services.longform.store import (
    clear_stop_request,
    get_project_dir,
    is_pid_alive,
    is_stop_requested,
    load_manifest,
    request_job_stop,
    update_job_phase,
)

# In-memory tracking of active process handles in this Python process
_ACTIVE_PROCESSES: Dict[str, subprocess.Popen] = {}

ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi_codes(text: str) -> str:
    """Strip ANSI escape sequences from text for clean UI display (Stage 7B.2 P)."""
    return ANSI_REGEX.sub("", text)


def get_job_state_file(project_id: str) -> Path:
    """Return the path to the persistent job state JSON file for a project."""
    p_dir = get_project_dir(project_id)
    return p_dir / "job_state.json"


def _read_job_state_from_disk(project_id: str) -> Optional[Dict[str, Any]]:
    path = get_job_state_file(project_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning(f"Failed to read job state for '{project_id}': {exc}")
    return None


def _save_job_state_to_disk(project_id: str, state: Dict[str, Any]) -> None:
    path = get_job_state_file(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.error(f"Failed to write job state for '{project_id}': {exc}")


def cleanup_stale_lock(project_id: str) -> bool:
    """Remove project .lock file if the holding PID is dead (C1)."""
    p_dir = get_project_dir(project_id)
    lock_file = p_dir / ".lock"
    if lock_file.is_file():
        try:
            lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
            lock_pid = lock_data.get("pid")
            if lock_pid and not is_pid_alive(lock_pid):
                lock_file.unlink(missing_ok=True)
                logger.info(f"Removed stale lock file for project '{project_id}' (dead PID {lock_pid})")
                return True
        except Exception:
            pass
    return False


def is_job_step_completed(project_id: str, step: str) -> bool:
    """Check if the manifest/disk assets for a step are fully completed (C1)."""
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir() or not (p_dir / "manifest.json").is_file():
        return False
    try:
        manifest = load_manifest(p_dir)
        if step in ("script_outline", "script_generate"):
            return len(manifest.chapters) > 0 and all(
                any(g.paragraph_text and g.paragraph_text.strip() for g in ch.tts_groups)
                or (p_dir / "script" / f"chapter_{ch.chapter_index:02d}.md").is_file()
                for ch in manifest.chapters
            )
        elif step == "audio_synthesize":
            total_g = sum(len(ch.tts_groups) for ch in manifest.chapters)
            if total_g == 0:
                return False
            done_g = sum(
                1 for ch in manifest.chapters for g in ch.tts_groups
                if g.audio and g.audio.file_path and (p_dir / g.audio.file_path).is_file() and (p_dir / g.audio.file_path).stat().st_size > 44
            )
            return done_g == total_g
        elif step in ("visual_generate", "visual_pipeline", "visual", "visual_build"):
            total_s = sum(len(ch.shots) for ch in manifest.chapters)
            if total_s == 0:
                return False
            done_s = sum(
                1 for ch in manifest.chapters for sh in ch.shots
                if sh.rendered_clip and (p_dir / sh.rendered_clip).is_file()
            )
            return done_s == total_s
        elif step == "visual_fetch":
            total_s = sum(len(ch.shots) for ch in manifest.chapters)
            if total_s == 0:
                return False
            done_s = sum(
                1 for ch in manifest.chapters for sh in ch.shots
                if sh.asset_path and (p_dir / sh.asset_path).is_file()
            )
            return done_s == total_s
        elif step in ("render_final", "render_all", "render_pipeline", "render", "pipeline_auto"):
            return bool(manifest.final_video_file and (p_dir / manifest.final_video_file).is_file())
        elif step == "capcut_export":
            from app.services.longform.status import find_latest_capcut_draft
            return find_latest_capcut_draft(p_dir, project_id) is not None
    except Exception:
        return False
    return False


def get_job_state(project_id: str) -> Optional[Dict[str, Any]]:
    """Get the current job state for a project, refreshing live status if running."""
    cleanup_stale_lock(project_id)

    state = _read_job_state_from_disk(project_id)
    if not state:
        return None

    if state.get("status") == "running":
        pid = state.get("pid")
        step = state.get("step", "")
        proc = _ACTIVE_PROCESSES.get(project_id)

        # Check process status
        if proc is not None:
            ret = proc.poll()
            if ret is not None:
                # Process has finished
                state["finished_at"] = datetime.now().isoformat()
                state["exit_code"] = ret
                if ret == 0:
                    state["status"] = "completed"
                elif ret in (-1073741510, 3221225786, -signal.SIGINT, signal.SIGINT):
                    state["status"] = "cancelled"
                else:
                    disk_state = _read_job_state_from_disk(project_id)
                    if disk_state and disk_state.get("status") in ("failed", "stopped"):
                        state["status"] = disk_state["status"]
                        if disk_state.get("error_message"):
                            state["error_message"] = disk_state["error_message"]
                        if disk_state.get("failed_step"):
                            state["failed_step"] = disk_state["failed_step"]
                        if disk_state.get("phase"):
                            state["phase"] = disk_state["phase"]
                    elif ret == 2:
                        state["status"] = "stopped"
                    else:
                        state["status"] = "failed" if not is_job_step_completed(project_id, step) else "completed"
                _save_job_state_to_disk(project_id, state)
                _ACTIVE_PROCESSES.pop(project_id, None)
        elif pid and not is_pid_alive(pid):
            # Process was not alive and not in _ACTIVE_PROCESSES
            state["finished_at"] = datetime.now().isoformat()
            disk_state = _read_job_state_from_disk(project_id)
            if disk_state and disk_state.get("status") in ("failed", "stopped"):
                state["status"] = disk_state["status"]
                if disk_state.get("error_message"):
                    state["error_message"] = disk_state["error_message"]
                if disk_state.get("failed_step"):
                    state["failed_step"] = disk_state["failed_step"]
                if disk_state.get("phase"):
                    state["phase"] = disk_state["phase"]
            elif is_job_step_completed(project_id, step):
                state["status"] = "completed"
                state["exit_code"] = 0
            else:
                state["status"] = "failed"
                state["exit_code"] = state.get("exit_code") or -1
            _save_job_state_to_disk(project_id, state)

    return state


def start_job(
    project_id: str,
    step: str,
    command: List[str],
    cwd: Optional[Path] = None,
) -> Dict[str, Any]:
    """Launch a background job as a detached process (B2).

    Enforces 1 active job per project and respects existing PID lock.
    """
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir():
        raise FileNotFoundError(f"Project directory '{p_dir}' does not exist.")

    # 1. Enforce single active job per project
    current_state = get_job_state(project_id)
    if current_state and current_state.get("status") == "running":
        pid = current_state.get("pid")
        if pid and is_pid_alive(pid):
            raise RuntimeError(
                f"Job '{current_state.get('step')}' is currently running for project '{project_id}' (PID {pid})."
            )

    # 2. Check project .lock file and reclaim stale locks
    cleanup_stale_lock(project_id)
    lock_file = p_dir / ".lock"
    if lock_file.is_file():
        try:
            lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
            lock_pid = lock_data.get("pid")
            if lock_pid and is_pid_alive(lock_pid) and lock_pid != os.getpid():
                raise RuntimeError(
                    f"Project '{project_id}' is locked by external process (PID {lock_pid})."
                )
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Setup logs directory and log file
    logs_dir = p_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"job_{timestamp}_{step}.log"

    # Clear any lingering stop marker from previous cancellation (D2)
    clear_stop_request(p_dir)

    # 4. Configure process creation flags (D2: Completely headless without console window)
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

    repo_root = cwd or Path(__file__).resolve().parents[4]

    # Normalize command to use active python interpreter (sys.executable)
    cmd_list = list(command)
    if cmd_list and cmd_list[0] in ("python", "python3"):
        cmd_list[0] = sys.executable

    # Open log file handle for child process
    fp = open(log_file, "a", encoding="utf-8", buffering=1)

    try:
        proc = subprocess.Popen(
            cmd_list,
            stdin=subprocess.DEVNULL,
            stdout=fp,
            stderr=subprocess.STDOUT,
            cwd=str(repo_root),
            creationflags=creationflags,
            close_fds=True,
        )
    except Exception as exc:
        fp.close()
        raise RuntimeError(f"Failed to start background job: {exc}") from exc

    _ACTIVE_PROCESSES[project_id] = proc

    state: Dict[str, Any] = {
        "project_id": project_id,
        "step": step,
        "status": "running",
        "pid": proc.pid,
        "exit_code": None,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
        "log_file": str(log_file),
        "command": command,
        "error_message": None,
        "dismissed": False,
    }
    _save_job_state_to_disk(project_id, state)
    logger.info(f"Started background job '{step}' for project '{project_id}' (PID {proc.pid}) -> {log_file}")
    return state


def cancel_job(project_id: str, timeout_seconds: float = 15.0) -> bool:
    """Cancel an active background job via stop marker file with 15s clean exit fallback (D2)."""
    state = get_job_state(project_id)
    if not state or state.get("status") != "running":
        return False

    pid = state.get("pid")
    proc = _ACTIVE_PROCESSES.get(project_id)
    p_dir = get_project_dir(project_id)

    logger.info(f"Requesting graceful stop for project '{project_id}' via stop marker (timeout: {timeout_seconds}s)")
    request_job_stop(p_dir)

    cancelled = False
    start_time = time.time()

    # Wait for process to exit gracefully between work units
    while time.time() - start_time < timeout_seconds:
        if proc is not None and proc.poll() is not None:
            cancelled = True
            break
        if pid and not is_pid_alive(pid):
            cancelled = True
            break
        time.sleep(0.5)

    # Fallback: force terminate if process is still running after timeout
    if not cancelled:
        logger.warning(f"Process PID {pid} did not exit gracefully within {timeout_seconds}s; terminating.")
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        elif pid and is_pid_alive(pid):
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        cancelled = True

    clear_stop_request(p_dir)
    cleanup_stale_lock(project_id)

    state["status"] = "cancelled"
    state["finished_at"] = datetime.now().isoformat()
    state["exit_code"] = -signal.SIGTERM if sys.platform != "win32" else 3221225786
    _save_job_state_to_disk(project_id, state)
    _ACTIVE_PROCESSES.pop(project_id, None)

    logger.info(f"Background job for project '{project_id}' successfully cancelled.")
    return True


def dismiss_job(project_id: str) -> bool:
    """Mark the current job as dismissed in job_state.json so it is no longer shown in UI (W3)."""
    state = _read_job_state_from_disk(project_id)
    if not state:
        return False
    state["dismissed"] = True
    _save_job_state_to_disk(project_id, state)
    logger.info(f"Dismissed completed/finished job notification for project '{project_id}'")
    return True


def get_job_logs(project_id: str, max_lines: int = 30) -> List[str]:
    """Read the last max_lines from the active or latest job log file with ANSI codes stripped."""
    state = _read_job_state_from_disk(project_id)
    if not state:
        return []

    log_path_str = state.get("log_file")
    if not log_path_str:
        return []

    log_path = Path(log_path_str)
    if not log_path.is_file():
        return []

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return [strip_ansi_codes(l.rstrip("\r\n")) for l in lines[-max_lines:]]
    except Exception as exc:
        return [f"Error reading log file: {exc}"]


def get_project_progress(project_id: str, step: Optional[str] = None) -> Dict[str, Any]:
    """Compute progress of project pipeline steps from manifest and live job state (B2, Stage 7B.2 P)."""
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir():
        return {"completed": 0, "total": 0, "percentage": 0.0, "unit_label": "unit"}

    try:
        manifest = load_manifest(p_dir)
    except Exception:
        return {"completed": 0, "total": 0, "percentage": 0.0, "unit_label": "unit"}

    state = _read_job_state_from_disk(project_id) or {}
    job_status = state.get("status")
    job_progress = state.get("progress") or {}
    is_running = (job_status == "running")
    is_completed = (job_status == "completed")

    total_chapters = len(manifest.chapters)

    # 1. Script step
    if step in ("script_outline", "script_generate"):
        completed_chapters = sum(
            1 for ch in manifest.chapters
            if any(g.paragraph_text and g.paragraph_text.strip() for g in ch.tts_groups)
            or (p_dir / "script" / f"chapter_{ch.chapter_index:02d}.md").is_file()
        )
        if is_completed:
            pct = 100.0
            phase = "Selesai"
        elif is_running:
            pct = min((completed_chapters / total_chapters * 100.0) if total_chapters > 0 else 5.0, 99.0)
            phase = job_progress.get("phase") or "Menulis naskah"
        else:
            pct = (completed_chapters / total_chapters * 100.0) if total_chapters > 0 else 0.0
            phase = "Selesai" if (total_chapters > 0 and completed_chapters == total_chapters) else "Draft naskah"
        return {
            "completed": completed_chapters,
            "total": total_chapters,
            "percentage": round(pct, 1),
            "unit_label": "bab",
            "phase": phase,
            "is_indeterminate": False,
        }

    # 2. Visual step (7B.1 v2, 7B.2 P)
    if step in ("visual", "visual_pipeline", "visual_fetch", "visual_build"):
        total_shots = sum(len(ch.shots) for ch in manifest.chapters)
        if total_shots == 0:
            return {
                "completed": 0,
                "total": 0,
                "percentage": 5.0,
                "unit_label": "shot",
                "phase": "Membagi shot",
                "is_indeterminate": True,
            }

        fetched_shots = sum(
            1 for ch in manifest.chapters for sh in ch.shots
            if sh.asset_path and (p_dir / sh.asset_path).is_file()
        )
        built_shots = sum(
            1 for ch in manifest.chapters for sh in ch.shots
            if sh.rendered_clip and (p_dir / sh.rendered_clip).is_file()
        )

        if is_completed:
            return {
                "completed": total_shots,
                "total": total_shots,
                "percentage": 100.0,
                "unit_label": f"klip ({total_shots}/{total_shots})",
                "phase": "Selesai",
                "is_indeterminate": False,
            }

        if step == "visual_fetch":
            pct = (fetched_shots / total_shots * 100.0) if total_shots > 0 else 0.0
            if is_running:
                pct = min(pct, 99.0)
            return {
                "completed": fetched_shots,
                "total": total_shots,
                "percentage": round(pct, 1),
                "unit_label": "shot diunduh",
                "phase": job_progress.get("phase") or "Mencari footage",
                "is_indeterminate": bool(job_progress.get("is_indeterminate")),
            }
        elif step == "visual_build":
            pct = (built_shots / total_shots * 100.0) if total_shots > 0 else 0.0
            if is_running:
                pct = min(pct, 99.0)
            return {
                "completed": built_shots,
                "total": total_shots,
                "percentage": round(pct, 1),
                "unit_label": "klip dibuat",
                "phase": job_progress.get("phase") or "Menyusun klip",
                "is_indeterminate": bool(job_progress.get("is_indeterminate")),
            }
        else:  # visual_pipeline / visual
            if fetched_shots < total_shots:
                pct = 5.0 + (fetched_shots / total_shots * 45.0) if total_shots > 0 else 5.0
                phase_label = job_progress.get("phase") or "Mencari footage"
                unit_label = f"footage ({fetched_shots}/{total_shots})"
                completed_count = fetched_shots
            elif built_shots < total_shots:
                pct = 50.0 + (built_shots / total_shots * 50.0) if total_shots > 0 else 50.0
                phase_label = job_progress.get("phase") or "Menyusun klip"
                unit_label = f"klip ({built_shots}/{total_shots})"
                completed_count = built_shots
            else:
                pct = 99.0 if is_running else 100.0
                phase_label = job_progress.get("phase") if is_running else "Selesai"
                unit_label = f"klip ({total_shots}/{total_shots})"
                completed_count = total_shots

            if is_running:
                pct = min(pct, 99.0)
                if phase_label == "Selesai":
                    phase_label = "Menyelesaikan visual"

            return {
                "completed": completed_count,
                "total": total_shots,
                "percentage": round(pct, 1),
                "unit_label": unit_label,
                "phase": phase_label,
                "is_indeterminate": bool(job_progress.get("is_indeterminate")),
            }

    # 3. Render step (Stage 7B.2 R3 & P)
    if step in ("render", "render_all", "render_pipeline", "render_final"):
        rendered_chapters = sum(
            1 for ch in manifest.chapters
            if ch.output_file and (p_dir / ch.output_file).is_file() and ch.status == ItemStatus.completed
        )

        if is_completed:
            return {
                "completed": total_chapters,
                "total": total_chapters,
                "percentage": 100.0,
                "unit_label": f"bab ({total_chapters}/{total_chapters})",
                "phase": "Selesai",
                "is_indeterminate": False,
            }

        active_phase = job_progress.get("phase")
        is_indet = bool(job_progress.get("is_indeterminate"))

        if is_running:
            if is_indet or rendered_chapters >= total_chapters:
                pct = float(job_progress.get("percentage", 85.0))
                pct = min(max(pct, 80.0), 95.0)  # Capped at 95% while running indeterminate final steps
                return {
                    "completed": total_chapters,
                    "total": total_chapters,
                    "percentage": round(pct, 1),
                    "unit_label": "penggabungan",
                    "phase": active_phase or "Menggabungkan video & audio final",
                    "is_indeterminate": True,
                }
            else:
                pct = float(job_progress.get("percentage", round((rendered_chapters / total_chapters) * 80.0, 1) if total_chapters > 0 else 0.0))
                return {
                    "completed": rendered_chapters,
                    "total": total_chapters,
                    "percentage": round(min(pct, 99.0), 1),
                    "unit_label": f"bab ({rendered_chapters}/{total_chapters})",
                    "phase": active_phase or f"Bab {rendered_chapters + 1} dari {total_chapters}",
                    "is_indeterminate": False,
                }
        else:
            # Stopped, cancelled, or not started
            has_final = bool(manifest.final_video_file and (p_dir / manifest.final_video_file).is_file())
            if has_final and rendered_chapters == total_chapters:
                return {
                    "completed": total_chapters,
                    "total": total_chapters,
                    "percentage": 100.0,
                    "unit_label": f"bab ({total_chapters}/{total_chapters})",
                    "phase": "Selesai",
                    "is_indeterminate": False,
                }
            pct = round((rendered_chapters / total_chapters) * 80.0, 1) if total_chapters > 0 else 0.0
            return {
                "completed": rendered_chapters,
                "total": total_chapters,
                "percentage": round(min(pct, 95.0), 1),
                "unit_label": f"bab ({rendered_chapters}/{total_chapters})",
                "phase": f"Bab {rendered_chapters} dari {total_chapters}" if rendered_chapters > 0 else "Belum dirender",
                "is_indeterminate": False,
            }

    # 4. Pipeline Auto step (Stage 7B.2 A3 & P)
    if step == "pipeline_auto":
        if is_completed:
            return {
                "completed": 1,
                "total": 1,
                "percentage": 100.0,
                "unit_label": "selesai",
                "phase": "Selesai",
                "is_indeterminate": False,
            }

        # Check Voice
        total_groups = sum(len(ch.tts_groups) for ch in manifest.chapters)
        completed_groups = sum(
            1 for ch in manifest.chapters for g in ch.tts_groups
            if g.audio and g.audio.file_path and (p_dir / g.audio.file_path).is_file() and (p_dir / g.audio.file_path).stat().st_size > 44
        )
        if completed_groups < total_groups:
            pct = round((completed_groups / total_groups) * 25.0, 1) if total_groups > 0 else 5.0
            return {
                "completed": completed_groups,
                "total": total_groups,
                "percentage": min(pct, 25.0),
                "unit_label": f"audio ({completed_groups}/{total_groups})",
                "phase": job_progress.get("phase") or "Langkah 2/4 · Suara · Menyintesis narasi",
                "is_indeterminate": False,
            }

        # Check Visual
        total_shots = sum(len(ch.shots) for ch in manifest.chapters)
        built_shots = sum(
            1 for ch in manifest.chapters for sh in ch.shots
            if sh.rendered_clip and (p_dir / sh.rendered_clip).is_file()
        )
        fetched_shots = sum(
            1 for ch in manifest.chapters for sh in ch.shots
            if sh.asset_path and (p_dir / sh.asset_path).is_file()
        )
        if built_shots < total_shots:
            if fetched_shots < total_shots:
                pct = 25.0 + ((fetched_shots / total_shots) * 20.0) if total_shots > 0 else 25.0
                phase_label = job_progress.get("phase") or f"Langkah 3/4 · Visual · Mencari footage ({fetched_shots}/{total_shots})"
                unit_label = f"footage ({fetched_shots}/{total_shots})"
                completed_count = fetched_shots
            else:
                pct = 45.0 + ((built_shots / total_shots) * 25.0) if total_shots > 0 else 45.0
                phase_label = job_progress.get("phase") or f"Langkah 3/4 · Visual · Menyusun klip ({built_shots}/{total_shots})"
                unit_label = f"klip ({built_shots}/{total_shots})"
                completed_count = built_shots
            return {
                "completed": completed_count,
                "total": total_shots,
                "percentage": round(min(pct, 70.0), 1),
                "unit_label": unit_label,
                "phase": phase_label,
                "is_indeterminate": False,
            }

        # Check Render
        rendered_chapters = sum(
            1 for ch in manifest.chapters
            if ch.output_file and (p_dir / ch.output_file).is_file() and ch.status == ItemStatus.completed
        )
        has_final = bool(manifest.final_video_file and (p_dir / manifest.final_video_file).is_file())
        if not has_final or is_running:
            if rendered_chapters < total_chapters:
                pct = 70.0 + ((rendered_chapters / total_chapters) * 20.0) if total_chapters > 0 else 70.0
                phase_label = job_progress.get("phase") or f"Langkah 4/4 · Render · Bab {rendered_chapters + 1} dari {total_chapters}"
                is_indet = False
            else:
                pct = min(float(job_progress.get("percentage", 92.0)), 95.0)
                phase_label = job_progress.get("phase") or "Langkah 4/4 · Render · Menggabungkan & Normalisasi"
                is_indet = True
            return {
                "completed": rendered_chapters,
                "total": total_chapters,
                "percentage": round(pct, 1),
                "unit_label": f"bab ({rendered_chapters}/{total_chapters})",
                "phase": phase_label,
                "is_indeterminate": is_indet,
            }

        # Check CapCut if present in job command
        cmd = state.get("command", [])
        with_cc = any("capcut" in str(arg).lower() for arg in cmd)
        from app.services.longform.status import find_latest_capcut_draft
        cc_draft = find_latest_capcut_draft(p_dir, project_id)
        if with_cc and not cc_draft:
            return {
                "completed": 0,
                "total": 1,
                "percentage": 98.0,
                "unit_label": "draft",
                "phase": job_progress.get("phase") or "Langkah 5/5 · CapCut · Mengekspor draft",
                "is_indeterminate": True,
            }

        return {
            "completed": 1,
            "total": 1,
            "percentage": 99.0 if is_running else 100.0,
            "unit_label": "selesai",
            "phase": "Menyelesaikan auto pipeline" if is_running else "Selesai",
            "is_indeterminate": False,
        }

    # 5. CapCut step (Stage 7B.2 C3 & P)
    if step in ("capcut_export", "capcut"):
        if is_completed:
            return {
                "completed": 1,
                "total": 1,
                "percentage": 100.0,
                "unit_label": "draft",
                "phase": "Selesai",
                "is_indeterminate": False,
            }
        active_phase = job_progress.get("phase") or "Mengekspor draft CapCut"
        pct = float(job_progress.get("percentage", 50.0))
        return {
            "completed": 0,
            "total": 1,
            "percentage": round(min(pct, 95.0), 1),
            "unit_label": "draft",
            "phase": active_phase,
            "is_indeterminate": True,
        }

    # 6. Voice / Audio synthesize step
    total_groups = sum(len(ch.tts_groups) for ch in manifest.chapters)
    completed_groups = 0
    for ch in manifest.chapters:
        for g in ch.tts_groups:
            if g.audio and g.audio.file_path:
                af = p_dir / g.audio.file_path
                if af.is_file() and af.stat().st_size > 0:
                    completed_groups += 1

    if is_completed:
        return {
            "completed": total_groups,
            "total": total_groups,
            "percentage": 100.0,
            "unit_label": "grup audio",
            "phase": "Selesai",
            "is_indeterminate": False,
        }

    pct = (completed_groups / total_groups * 100.0) if total_groups > 0 else 0.0
    if is_running:
        pct = min(pct, 99.0)
        phase_label = job_progress.get("phase") or f"Menyintesis narasi suara ({completed_groups}/{total_groups})"
    else:
        phase_label = "Selesai" if (total_groups > 0 and completed_groups == total_groups) else "Sintesis suara"

    return {
        "completed": completed_groups,
        "total": total_groups,
        "percentage": round(pct, 1),
        "unit_label": "grup audio",
        "phase": phase_label,
        "is_indeterminate": bool(job_progress.get("is_indeterminate")),
    }

