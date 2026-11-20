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

from app.services.branding.constants import get_app_build_signature
from app.services.longform import app_paths, platform_utils


APP_MUTEX_NAME = "EasyFacelessStudioRunningMutex"


def ensure_app_mutex() -> Optional[int]:
    """Acquire and hold single-instance OS mutex so installer detects running app (D9.6)."""
    return platform_utils.acquire_named_mutex(APP_MUTEX_NAME)


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


def _confirm_restart_with_active_jobs(
    active_jobs: List[Dict[str, Any]],
    *,
    old_version: str,
    new_version: str,
    allow_ui_prompt: bool = True,
    confirm_callback: Optional[Callable[[List[Dict[str, Any]]], bool]] = None,
) -> bool:
    """Warn user when replacing an active server that has running jobs (E12.1)."""
    if not active_jobs:
        return True
    _append_launcher_log(
        f"WARNING: {len(active_jobs)} active background job(s) running while upgrading server "
        f"({old_version or '<none>'} -> {new_version})"
    )
    if confirm_callback is not None:
        return bool(confirm_callback(active_jobs))
    if not allow_ui_prompt or os.environ.get("PYTEST_CURRENT_TEST", "").strip():
        return True
    if platform_utils.is_windows():
        try:
            import ctypes
            from app.services.longform.ui.i18n import t

            msg = t("launcher_upgrade_jobs_warning", count=len(active_jobs))
            title = "EasyFaceless Studio"
            # MB_YESNO (0x4) | MB_ICONWARNING (0x30) | MB_DEFBUTTON2 (0x100)
            res = ctypes.windll.user32.MessageBoxW(None, msg, title, 0x4 | 0x30 | 0x100)
            return int(res) == 6  # IDYES
        except Exception:
            return True
    return True


def _stop_running_jobs_cleanly(active_jobs: List[Dict[str, Any]]) -> None:
    if not active_jobs:
        return
    try:
        from app.services.longform.store import get_projects_root, request_job_stop

        proj_root = get_projects_root()
        for j in active_jobs:
            pid_name = str(j.get("project_id") or "")
            if pid_name and (proj_root / pid_name).is_dir():
                try:
                    request_job_stop(proj_root / pid_name)
                except Exception:
                    pass
            jpid = int(j.get("pid") or 0)
            if jpid > 0:
                platform_utils.terminate_process_tree(jpid, force=True, timeout=2.5)
    except Exception:
        pass


def launch_or_focus_app(
    *,
    start_port: int = 8501,
    host: str = "127.0.0.1",
    ready_timeout: float = 45.0,
    open_browser: bool = True,
    confirm_jobs_callback: Optional[Callable[[List[Dict[str, Any]]], bool]] = None,
) -> Dict[str, Any]:
    """Start headless Streamlit server or open existing single-instance URL in browser (D3, E12.1)."""
    app_paths.bootstrap_runtime_environment()
    install_root = app_paths.get_install_root()
    data_dir = app_paths.get_data_dir()
    logs_dir = app_paths.get_logs_dir(create=True)
    server_log = logs_dir / "server.log"

    desired_update_url = os.environ.get("EFS_UPDATE_URL", "").strip()
    cur_sig = get_app_build_signature()
    cur_version = cur_sig["app_version"]
    cur_build_id = cur_sig["build_id"]

    existing = read_launcher_lock()
    if existing:
        old_pid = int(existing.get("pid") or 0)
        old_port = int(existing.get("port") or start_port)
        old_url = str(existing.get("url") or f"http://{host}:{old_port}")
        old_update_url = str(existing.get("update_url") or "").strip()
        old_version = str(existing.get("app_version") or "").strip()
        old_build_id = str(existing.get("build_id") or "").strip()

        if old_pid > 0 and platform_utils.is_pid_alive(old_pid) and is_streamlit_server_healthy(old_port, host=host):
            version_or_build_mismatch = (old_version != cur_version) or (old_build_id != cur_build_id)
            update_url_mismatch = desired_update_url != old_update_url

            if version_or_build_mismatch:
                active_jobs = list_active_running_jobs()
                if active_jobs and not _confirm_restart_with_active_jobs(
                    active_jobs,
                    old_version=old_version,
                    new_version=cur_version,
                    allow_ui_prompt=open_browser,
                    confirm_callback=confirm_jobs_callback,
                ):
                    _append_launcher_log(
                        f"Keeping active server (pid={old_pid}, old_version={old_version or '<none>'}, old_build_id={old_build_id or '<none>'}) "
                        f"because user declined stopping {len(active_jobs)} running job(s)"
                    )
                    if open_browser:
                        platform_utils.open_url_in_browser(old_url)
                    return {
                        "reused": True,
                        "restart_cancelled": True,
                        "pid": old_pid,
                        "port": old_port,
                        "url": old_url,
                        "data_dir": str(data_dir),
                        "app_version": old_version,
                        "build_id": old_build_id,
                    }

                _append_launcher_log(
                    f"Replacing outdated active server (pid={old_pid}, old_version={old_version or '<none>'}, "
                    f"old_build_id={old_build_id or '<none>'} -> version={cur_version}, build_id={cur_build_id}, "
                    f"active_jobs={len(active_jobs)})"
                )
                _stop_running_jobs_cleanly(active_jobs)
                platform_utils.terminate_process_tree(old_pid, force=True, timeout=5.0)
                clear_launcher_lock()
            elif update_url_mismatch and not list_active_running_jobs():
                _append_launcher_log(
                    f"Restarting active server (pid={old_pid}) because EFS_UPDATE_URL changed ({old_update_url!r} -> {desired_update_url!r})"
                )
                platform_utils.terminate_process_tree(old_pid, force=True, timeout=5.0)
                clear_launcher_lock()
            else:
                _append_launcher_log(
                    f"Reusing active server instance (pid={old_pid}, port={old_port}, "
                    f"app_version={cur_version}, build_id={cur_build_id})"
                )
                if open_browser:
                    platform_utils.open_url_in_browser(old_url)
                return {
                    "reused": True,
                    "pid": old_pid,
                    "port": old_port,
                    "url": old_url,
                    "data_dir": str(data_dir),
                    "app_version": cur_version,
                    "build_id": cur_build_id,
                }
        else:
            _append_launcher_log(f"Clearing stale launcher lock (pid={old_pid}, port={old_port})")
            clear_launcher_lock()

    port = find_free_port(start_port=start_port, host=host)
    url = f"http://{host}:{port}"
    python_exe = _resolve_launcher_python(install_root)
    main_script = install_root / "webui" / "Main.py"
    if not main_script.is_file():
        raise FileNotFoundError(f"Main WebUI entrypoint not found: {main_script}")

    installed_mode = app_paths.is_installed_mode()
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
    if installed_mode:
        cmd.extend(["--server.fileWatcherType", "none"])

    child_env = os.environ.copy()
    child_env["EFS_DATA_DIR"] = str(data_dir)
    if installed_mode:
        child_env["EFS_INSTALLED"] = "1"
        child_env["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"
    if desired_update_url:
        child_env["EFS_UPDATE_URL"] = desired_update_url
    child_env["EFS_SERVER_PORT"] = str(port)
    child_env["EFS_SERVER_APP_VERSION"] = cur_version
    child_env["EFS_SERVER_BUILD_ID"] = cur_build_id
    child_env["EFS_SERVER_BUILD_COMMIT"] = cur_sig["git_commit"]
    child_env["EFS_SERVER_BUILD_TIME"] = cur_sig["build_time"]
    existing_pp = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = f"{install_root}{os.pathsep}{existing_pp}" if existing_pp else str(install_root)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUNBUFFERED"] = "1"

    _append_launcher_log(
        f"Starting Streamlit server on {url} (data_dir={data_dir}, installed={installed_mode}, "
        f"app_version={cur_version}, build_id={cur_build_id}, update_url={desired_update_url or '<default>'})"
    )
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
        "update_url": desired_update_url,
        "app_version": cur_version,
        "build_id": cur_build_id,
        "git_commit": cur_sig["git_commit"],
        "build_time": cur_sig["build_time"],
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

    _append_launcher_log(
        f"Server ready on {url} (pid={proc.pid}, app_version={cur_version}, build_id={cur_build_id}); opening browser={open_browser}"
    )
    if open_browser:
        platform_utils.open_url_in_browser(url)

    return {
        "reused": False,
        "pid": int(proc.pid),
        "port": port,
        "url": url,
        "data_dir": str(data_dir),
        "app_version": cur_version,
        "build_id": cur_build_id,
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


def get_running_app_status(install_root: Optional[Path] = None) -> Dict[str, Any]:
    """Inspect active EasyFaceless Studio server lock, background jobs, and processes (D9.6, E12.2)."""
    root = Path(install_root).resolve() if install_root else app_paths.get_install_root()
    lock_info = read_launcher_lock()
    jobs_active = list_active_running_jobs()
    dir_procs = platform_utils.find_processes_in_directory(root, include_cmd_markers=True)

    pids = set()
    if lock_info and int(lock_info.get("pid") or 0) > 0 and platform_utils.is_pid_alive(int(lock_info["pid"])):
        pids.add(int(lock_info["pid"]))
    for j in jobs_active:
        if int(j.get("pid") or 0) > 0:
            pids.add(int(j["pid"]))
    for p in dir_procs:
        if int(p.get("pid") or 0) > 0:
            pids.add(int(p["pid"]))

    return {
        "running": bool(pids),
        "pids": sorted(pids),
        "jobs": jobs_active,
        "processes": dir_procs,
        "server_pid": int(lock_info.get("pid") or 0) if lock_info else 0,
    }


def shutdown_running_app(
    install_root: Optional[Path] = None,
    *,
    force: bool = True,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Cleanly stop running jobs, server, and launcher processes before install/upgrade/uninstall (D9.6)."""
    from app.services.longform.store import get_projects_root, request_job_stop

    root = Path(install_root).resolve() if install_root else app_paths.get_install_root()
    status = get_running_app_status(root)
    stopped: List[int] = []

    # 1. Advise running jobs to stop cleanly before terminating their process trees
    proj_root = get_projects_root()
    for j in status.get("jobs") or []:
        j_pid_name = str(j.get("project_id") or "")
        if j_pid_name and (proj_root / j_pid_name).is_dir():
            try:
                request_job_stop(proj_root / j_pid_name)
            except Exception:
                pass

    # 2. Terminate tracked server, job PIDs, and any other processes inside install_root
    target_pids = [int(p) for p in status.get("pids") or [] if int(p) > 0 and int(p) != os.getpid()]
    for pid in target_pids:
        if platform_utils.terminate_process_tree(pid, force=force, timeout=min(3.0, timeout)):
            stopped.append(pid)

    clear_launcher_lock()

    # 3. Wait briefly until no directory-locking or EFS server processes remain
    deadline = time.monotonic() + max(1.0, float(timeout))
    remaining = platform_utils.find_processes_in_directory(root, include_cmd_markers=True)
    while remaining and time.monotonic() < deadline:
        for proc in remaining:
            p_id = int(proc.get("pid") or 0)
            if p_id > 0 and p_id != os.getpid():
                platform_utils.terminate_process_tree(p_id, force=True, timeout=2.0)
        time.sleep(0.25)
        remaining = platform_utils.find_processes_in_directory(root, include_cmd_markers=True)

    _append_launcher_log(
        f"Pre-setup shutdown finished (stopped={stopped}, remaining={len(remaining)})"
    )
    return {
        "stopped_pids": stopped,
        "remaining": remaining,
        "clean": len(remaining) == 0,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI/Desktop entrypoint for EasyFaceless Studio Windows launcher."""
    import argparse

    platform_utils.configure_console_utf8()
    parser = argparse.ArgumentParser(description="EasyFaceless Studio Desktop Launcher Runtime")
    parser.add_argument("--status-json", action="store_true", help="Print running app/job status JSON and exit")
    parser.add_argument("--shutdown", action="store_true", help="Cleanly stop running app, server, and jobs")
    parser.add_argument("--install-root", default=None, help="Override install root directory")
    args, _ = parser.parse_known_args(argv)

    if args.status_json:
        info = get_running_app_status(Path(args.install_root) if args.install_root else None)
        print(json.dumps(info, ensure_ascii=False))
        return 0

    if args.shutdown:
        res = shutdown_running_app(Path(args.install_root) if args.install_root else None)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["clean"] else 1

    try:
        ensure_app_mutex()
        open_browser = os.environ.get("EFS_NO_BROWSER", "").strip().lower() not in ("1", "true", "yes")
        info = launch_or_focus_app(open_browser=open_browser)
        print(f"EasyFaceless Studio ready at {info['url']} (reused={info['reused']})")
        return 0
    except Exception as exc:
        _append_launcher_log(f"Launcher fatal error: {exc}")
        print(f"Failed to launch EasyFaceless Studio: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
