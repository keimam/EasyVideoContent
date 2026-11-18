"""Single-instance desktop launcher & graceful shutdown runtime (Stage 8C.2, D3).

Handles:
- Finding a free local port starting at 8501
- Enforcing single instance via ``.efs_launcher.lock.json`` in ``get_data_dir()``
- Launching Streamlit headless without a console window (logs -> ``<data_dir>/logs/server.log``)
- Waiting for HTTP health readiness then opening default browser
- Detecting running background jobs before shutdown and terminating server cleanly
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.services.longform import app_paths, platform_utils


def _append_launcher_log(message: str) -> None:
    """Write timestamped launcher lifecycle line to `<data_dir>/logs/launcher.log`."""
    try:
        log_path = app_paths.get_logs_dir(create=True) / "launcher.log"
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def find_free_port(start_port: int = 8501, host: str = "127.0.0.1", max_tries: int = 50) -> int:
    """Return the first bindable TCP port starting from `start_port`."""
    for offset in range(max_tries):
        port = int(start_port) + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"Could not find a free local port between {start_port} and {start_port + max_tries - 1}.")


def read_launcher_lock(lock_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read launcher instance metadata if present and valid JSON."""
    target = Path(lock_path) if lock_path else app_paths.get_launcher_lock_path()
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_launcher_lock(data: Dict[str, Any], lock_path: Optional[Path] = None) -> Path:
    """Atomically write single-instance lock file into user data directory."""
    target = Path(lock_path) if lock_path else app_paths.get_launcher_lock_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def clear_launcher_lock(lock_path: Optional[Path] = None) -> None:
    """Remove launcher lock file if present."""
    target = Path(lock_path) if lock_path else app_paths.get_launcher_lock_path()
    try:
        target.unlink(missing_ok=True)
    except Exception:
        pass


def is_streamlit_server_healthy(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Return True if local Streamlit server responds 200 on `/_stcore/health`."""
    url = f"http://{host}:{int(port)}/_stcore/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(getattr(resp, "status", 0)) == 200
    except Exception:
        return False


def _resolve_launcher_python(install_root: Path) -> str:
    """Resolve bundled headless Python executable when present, falling back to `sys.executable`."""
    candidates = [
        install_root / "runtime" / "python" / "python.exe",
        install_root / "runtime" / "python" / "bin" / "python3",
        install_root / "runtime" / "python" / "bin" / "python",
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return sys.executable


def list_active_running_jobs() -> List[Dict[str, Any]]:
    """Inspect all projects and return any live background jobs."""
    from app.services.longform.store import get_projects_root
    from app.services.longform.ui import jobs

    active: List[Dict[str, Any]] = []
    proj_root = get_projects_root()
    if not proj_root.is_dir():
        return active

    for p_dir in sorted(proj_root.iterdir()):
        if not p_dir.is_dir() or p_dir.name == "_trash":
            continue
        pid_id = p_dir.name
        try:
            state = jobs.get_job_state(pid_id)
            if state and state.get("status") == "running":
                job_pid = int(state.get("pid") or 0)
                if job_pid > 0 and platform_utils.is_pid_alive(job_pid):
                    active.append({
                        "project_id": pid_id,
                        "step": state.get("step", "job"),
                        "pid": job_pid,
                    })
        except Exception:
            continue
    return active


def launch_or_focus_app(
    *,
    start_port: int = 8501,
    host: str = "127.0.0.1",
    ready_timeout: float = 45.0,
    open_browser: bool = True,
) -> Dict[str, Any]:
    """Start headless Streamlit server or open existing single-instance URL in browser (D3)."""
    app_paths.bootstrap_runtime_environment()
    install_root = app_paths.get_install_root()
    data_dir = app_paths.get_data_dir()
    logs_dir = app_paths.get_logs_dir(create=True)
    server_log = logs_dir / "server.log"

    existing = read_launcher_lock()
    if existing:
        old_pid = int(existing.get("pid") or 0)
        old_port = int(existing.get("port") or start_port)
        old_url = str(existing.get("url") or f"http://{host}:{old_port}")
        if old_pid > 0 and platform_utils.is_pid_alive(old_pid) and is_streamlit_server_healthy(old_port, host=host):
            _append_launcher_log(f"Reusing active server instance (pid={old_pid}, port={old_port})")
            if open_browser:
                platform_utils.open_url_in_browser(old_url)
            return {
                "reused": True,
                "pid": old_pid,
                "port": old_port,
                "url": old_url,
                "data_dir": str(data_dir),
            }
        _append_launcher_log(f"Clearing stale launcher lock (pid={old_pid}, port={old_port})")
        clear_launcher_lock()

    port = find_free_port(start_port=start_port, host=host)
    url = f"http://{host}:{port}"
    python_exe = _resolve_launcher_python(install_root)
    main_script = install_root / "webui" / "Main.py"
    if not main_script.is_file():
        raise FileNotFoundError(f"Main WebUI entrypoint not found: {main_script}")

    cmd = [
        python_exe,
        "-m",
        "streamlit",
        "run",
        str(main_script),
        "--server.port",
        str(port),
        "--server.address",
        host,
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--global.developmentMode",
        "false",
    ]
    child_env = os.environ.copy()
    child_env["EFS_DATA_DIR"] = str(data_dir)
    if app_paths.is_installed_mode():
        child_env["EFS_INSTALLED"] = "1"
    child_env["EFS_SERVER_PORT"] = str(port)
    existing_pp = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = f"{install_root}{os.pathsep}{existing_pp}" if existing_pp else str(install_root)
    child_env["PYTHONIOENCODING"] = "utf-8"

    _append_launcher_log(f"Starting Streamlit server on {url} (data_dir={data_dir})")
    proc = platform_utils.launch_desktop_app_detached(
        cmd,
        cwd=install_root,
        log_file=server_log,
        env=child_env,
    )
    lock_payload = {
        "pid": int(proc.pid),
        "port": port,
        "url": url,
        "started_at": datetime.now().isoformat(),
        "data_dir": str(data_dir),
        "install_root": str(install_root),
    }
    write_launcher_lock(lock_payload)

    deadline = time.monotonic() + float(ready_timeout)
    ready = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _append_launcher_log(f"Server exited unexpectedly with code {proc.returncode}")
            clear_launcher_lock()
            raise RuntimeError(f"Streamlit server exited early (code {proc.returncode}). Check {server_log}")
        if is_streamlit_server_healthy(port, host=host, timeout=0.8):
            ready = True
            break
        time.sleep(0.35)

    if not ready:
        _append_launcher_log(f"Server timed out waiting for readiness on {url}")
        platform_utils.terminate_process_tree(proc.pid, force=True)
        clear_launcher_lock()
        raise TimeoutError(f"EasyFaceless Studio server did not become ready within {ready_timeout}s.")

    _append_launcher_log(f"Server ready on {url} (pid={proc.pid}); opening browser={open_browser}")
    if open_browser:
        platform_utils.open_url_in_browser(url)

    return {
        "reused": False,
        "pid": int(proc.pid),
        "port": port,
        "url": url,
        "data_dir": str(data_dir),
        "log_file": str(server_log),
    }


def request_app_shutdown(
    *,
    stop_running_jobs: bool = True,
    delay_seconds: float = 0.6,
    exit_fn: Optional[Callable[[int], None]] = None,
) -> Dict[str, Any]:
    """Cleanly stop active background jobs, remove launcher lock, and shut down Streamlit server (D3)."""
    active_jobs = list_active_running_jobs()
    stopped_pids: List[int] = []
    if stop_running_jobs:
        for j in active_jobs:
            jpid = int(j.get("pid") or 0)
            if jpid > 0 and platform_utils.terminate_process_tree(jpid, force=True, timeout=3.0):
                stopped_pids.append(jpid)

    lock_info = read_launcher_lock()
    clear_launcher_lock()
    _append_launcher_log(
        f"Shutdown requested from WebUI (stopped_jobs={len(stopped_pids)}, lock_pid={lock_info.get('pid') if lock_info else None})"
    )

    def _terminate_server():
        # Terminate tracked server PID if different from current process, else exit current process cleanly
        target_pid = int(lock_info.get("pid") or 0) if lock_info else 0
        if target_pid > 0 and target_pid != os.getpid() and platform_utils.is_pid_alive(target_pid):
            platform_utils.terminate_process_tree(target_pid, force=True, timeout=3.0)
        if exit_fn is not None:
            exit_fn(0)
        else:
            os._exit(0)

    timer = threading.Timer(max(0.05, float(delay_seconds)), _terminate_server)
    timer.daemon = True
    timer.start()

    return {
        "shutdown_scheduled": True,
        "stopped_job_pids": stopped_pids,
        "lock_cleared": True,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI/Desktop entrypoint for EasyFaceless Studio Windows launcher."""
    platform_utils.configure_console_utf8()
    try:
        info = launch_or_focus_app(open_browser=True)
        print(f"EasyFaceless Studio ready at {info['url']} (reused={info['reused']})")
        return 0
    except Exception as exc:
        _append_launcher_log(f"Launcher fatal error: {exc}")
        print(f"Failed to launch EasyFaceless Studio: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
