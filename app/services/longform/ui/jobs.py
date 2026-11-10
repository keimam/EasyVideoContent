"""Background job runner for Long Video WebUI operations (B2).

Runs longform CLI operations as detached background subprocesses,
with PID-level locking, process group signaling (CTRL_BREAK_EVENT on Windows),
persistent state in project directories, and manifest-backed progress monitoring.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from app.services.longform.schema import ItemStatus, ProjectManifest
from app.services.longform.store import get_project_dir, is_pid_alive, load_manifest

# In-memory tracking of active process handles in this Python process
_ACTIVE_PROCESSES: Dict[str, subprocess.Popen] = {}


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


def get_job_state(project_id: str) -> Optional[Dict[str, Any]]:
    """Get the current job state for a project, refreshing live status if running."""
    state = _read_job_state_from_disk(project_id)
    if not state:
        return None

    if state.get("status") == "running":
        pid = state.get("pid")
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
                    state["status"] = "failed"
                _save_job_state_to_disk(project_id, state)
                _ACTIVE_PROCESSES.pop(project_id, None)
        elif pid and not is_pid_alive(pid):
            # Process was not alive and not in _ACTIVE_PROCESSES
            state["finished_at"] = datetime.now().isoformat()
            if state.get("exit_code") is None:
                state["exit_code"] = 0  # Process ended
            if state.get("status") == "running":
                state["status"] = "completed" if state.get("exit_code") == 0 else "failed"
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

    # 2. Check project .lock file
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

    # 4. Configure process creation flags
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    repo_root = cwd or Path(__file__).resolve().parents[4]

    # Open log file handle for child process
    fp = open(log_file, "a", encoding="utf-8", buffering=1)

    try:
        proc = subprocess.Popen(
            command,
            stdout=fp,
            stderr=subprocess.STDOUT,
            cwd=str(repo_root),
            creationflags=creationflags,
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
    }
    _save_job_state_to_disk(project_id, state)
    logger.info(f"Started background job '{step}' for project '{project_id}' (PID {proc.pid}) -> {log_file}")
    return state


def cancel_job(project_id: str, timeout_seconds: float = 3.0) -> bool:
    """Cancel an active background job using CTRL_BREAK_EVENT on Windows (B2).

    Fallback to terminate() and kill() if process does not exit within timeout.
    """
    state = get_job_state(project_id)
    if not state or state.get("status") != "running":
        return False

    pid = state.get("pid")
    proc = _ACTIVE_PROCESSES.get(project_id)

    cancelled = False

    if proc is not None and proc.poll() is None:
        logger.info(f"Sending break signal to active process PID {proc.pid} for project '{project_id}'")
        if sys.platform == "win32":
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception as exc:
                logger.warning(f"Failed to send CTRL_BREAK_EVENT: {exc}, using terminate()")
                proc.terminate()
        else:
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                proc.terminate()

        try:
            proc.wait(timeout=timeout_seconds)
            cancelled = True
        except subprocess.TimeoutExpired:
            logger.warning(f"Process PID {proc.pid} did not exit after signal; terminating.")
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
                cancelled = True
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
                cancelled = True
        _ACTIVE_PROCESSES.pop(project_id, None)

    elif pid and is_pid_alive(pid):
        logger.info(f"Sending break signal to external PID {pid} for project '{project_id}'")
        if sys.platform == "win32":
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
                time.sleep(1.0)
            except Exception:
                pass
            if is_pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(1.0)
                except Exception:
                    pass
            if is_pid_alive(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
            cancelled = not is_pid_alive(pid)
        else:
            try:
                os.kill(pid, signal.SIGINT)
                time.sleep(1.0)
            except Exception:
                pass
            if is_pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
            cancelled = not is_pid_alive(pid)
    else:
        cancelled = True

    state["status"] = "cancelled"
    state["finished_at"] = datetime.now().isoformat()
    state["exit_code"] = -1
    _save_job_state_to_disk(project_id, state)
    logger.info(f"Background job for project '{project_id}' marked as cancelled.")
    return cancelled


def get_job_logs(project_id: str, max_lines: int = 30) -> List[str]:
    """Read the last max_lines from the active or latest job log file."""
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
            return [l.rstrip("\r\n") for l in lines[-max_lines:]]
    except Exception as exc:
        return [f"Error reading log file: {exc}"]


def get_project_progress(project_id: str, step: Optional[str] = None) -> Dict[str, Any]:
    """Compute progress of project pipeline steps from manifest (B2)."""
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir():
        return {"completed": 0, "total": 0, "percentage": 0.0, "unit_label": "unit"}

    try:
        manifest = load_manifest(p_dir)
    except Exception:
        return {"completed": 0, "total": 0, "percentage": 0.0, "unit_label": "unit"}

    total_chapters = len(manifest.chapters)

    # 1. Script step
    if step in ("script_outline", "script_generate"):
        completed_chapters = sum(
            1 for ch in manifest.chapters
            if any(g.paragraph_text and g.paragraph_text.strip() for g in ch.tts_groups)
            or (p_dir / "script" / f"chapter_{ch.chapter_index:02d}.md").is_file()
        )
        pct = (completed_chapters / total_chapters * 100.0) if total_chapters > 0 else 0.0
        return {
            "completed": completed_chapters,
            "total": total_chapters,
            "percentage": round(pct, 1),
            "unit_label": "bab",
        }

    # 2. Voice / Audio synthesize step
    total_groups = sum(len(ch.tts_groups) for ch in manifest.chapters)
    completed_groups = 0
    for ch in manifest.chapters:
        for g in ch.tts_groups:
            if g.audio and g.audio.file_path:
                af = p_dir / g.audio.file_path
                if af.is_file() and af.stat().st_size > 0:
                    completed_groups += 1

    pct = (completed_groups / total_groups * 100.0) if total_groups > 0 else 0.0
    return {
        "completed": completed_groups,
        "total": total_groups,
        "percentage": round(pct, 1),
        "unit_label": "grup audio",
    }
