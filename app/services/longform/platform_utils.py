"""Cross-platform OS abstraction layer for EasyFaceless Studio (Stage 8C.2, D6).

Centralizes all OS-specific behavior (Windows vs macOS vs Linux) so no other module
under ``app/services/longform/``, ``app/services/branding/``, or ``cli_longform.py``
needs inline ``sys.platform`` or ``os.name == 'nt'`` branching.

Functions implemented per OS:
- Console UTF-8 configuration (``configure_console_utf8``)
- Subprocess window-hiding & process-group flags (``get_hidden_subprocess_flags``)
- Process liveness & tree termination (``is_pid_alive``, ``terminate_process_tree``)
- User data & installation paths (``get_local_appdata_dir``, ``get_user_data_dir``, ``get_user_programs_dir``)
- CapCut Desktop detection & paths (``is_capcut_running``, ``get_capcut_product_info_path``, ``get_capcut_drafts_dir``)
- Trash directory move / read-only cleanup (``move_dir_to_trash_reliable``, ``remove_tree_with_retries``)
- OS file/folder & browser openers (``open_path_in_os``, ``open_url_in_browser``)
- Detached launcher process startup (``start_hidden_subprocess``, ``launch_desktop_app_detached``)
"""

from __future__ import annotations

import ctypes
import errno
import gc
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from loguru import logger


def get_platform_key() -> str:
    """Return canonical platform key: 'win32', 'darwin', or 'linux'."""
    plat = (sys.platform or "").lower()
    if plat.startswith("win") or os.name == "nt":
        return "win32"
    if plat == "darwin":
        return "darwin"
    return "linux"


def is_windows() -> bool:
    return get_platform_key() == "win32"


def is_macos() -> bool:
    return get_platform_key() == "darwin"


def is_linux() -> bool:
    return get_platform_key() == "linux"


def configure_console_utf8() -> None:
    """Ensure stdout/stderr handle UTF-8 cleanly on Windows consoles."""
    if not is_windows():
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def get_hidden_subprocess_flags() -> int:
    """Return subprocess creationflags suppressing console popups on Windows."""
    if is_windows():
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    return 0


def get_cancelled_exit_code() -> int:
    """Return conventional OS exit code representing user/force termination."""
    if is_windows():
        return 3221225786
    return -int(signal.SIGTERM)


def is_pid_alive(pid: int) -> bool:
    """Check whether a given PID is still running cleanly on Windows, macOS, or Linux."""
    if pid <= 0:
        return False

    if is_windows():
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def terminate_process_tree(pid: Optional[int], *, force: bool = True, timeout: float = 5.0) -> bool:
    """Terminate an external PID and any descendant child processes cleanly."""
    if not pid or int(pid) <= 0 or int(pid) == os.getpid():
        return False

    target_pid = int(pid)
    try:
        if is_windows():
            args = ["taskkill", "/PID", str(target_pid), "/T"]
            if force:
                args.append("/F")
            subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=max(1.0, float(timeout)),
                creationflags=get_hidden_subprocess_flags(),
            )
            return True

        # POSIX (macOS & Linux): attempt process group signal first, fallback to PID
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(os.getpgid(target_pid), sig)
        except Exception:
            os.kill(target_pid, sig)
        return True
    except Exception as exc:
        logger.warning(f"Process termination attempt for PID {target_pid} failed: {exc}")
        return False


def get_local_appdata_dir() -> Optional[Path]:
    """Return platform local application data base folder when available."""
    if is_windows():
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app:
            user_profile = os.environ.get("USERPROFILE", "").strip()
            if user_profile:
                local_app = os.path.join(user_profile, "AppData", "Local")
        if local_app:
            return Path(local_app)
        home_local = Path.home() / "AppData" / "Local"
        return home_local

    if is_macos():
        return Path.home() / "Library" / "Application Support"

    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        p = Path(xdg).expanduser()
        if p.is_absolute():
            return p
    return Path.home() / ".local" / "share"


def get_user_data_dir(app_name: str = "EasyFaceless Studio") -> Path:
    """Return default per-user writable data directory for installed app mode."""
    base = get_local_appdata_dir()
    if base is None:
        return (Path.home() / app_name).resolve()
    return (base / app_name).resolve()


def get_user_programs_dir(app_name: str = "EasyFaceless Studio") -> Path:
    """Return per-user install destination directory per OS conventions."""
    if is_windows():
        local_app = get_local_appdata_dir() or (Path.home() / "AppData" / "Local")
        return (local_app / "Programs" / app_name).resolve()
    if is_macos():
        return Path(f"/Applications/{app_name}.app")
    return (Path.home() / ".local" / "opt" / app_name.lower().replace(" ", "-")).resolve()


def is_capcut_running() -> bool:
    """Check whether CapCut Desktop is currently running."""
    if is_windows():
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq CapCut.exe", "/FO", "CSV"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
                creationflags=get_hidden_subprocess_flags(),
            )
            for line in (result.stdout or "").splitlines():
                if "capcut.exe" in line.lower():
                    return True
            return False
        except Exception as exc:
            logger.warning(f"Error checking Windows CapCut process status: {exc}")
            return False

    if is_macos():
        try:
            result = subprocess.run(
                ["pgrep", "-f", "CapCut.app"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            return result.returncode == 0
        except Exception as exc:
            logger.warning(f"Error checking macOS CapCut process status: {exc}")
            return False

    return False


def get_capcut_product_info_path() -> Optional[Path]:
    """Return candidate path to CapCut Desktop version metadata file."""
    if is_windows():
        local_app = get_local_appdata_dir()
        if not local_app:
            return None
        return local_app / "CapCut" / "Apps" / "ProductInfo.xml"
    if is_macos():
        return Path("/Applications/CapCut.app/Contents/Info.plist")
    return None


def get_capcut_drafts_dir() -> Optional[Path]:
    """Return candidate CapCut Desktop user drafts directory (existing or expected)."""
    if is_windows():
        local_app = get_local_appdata_dir()
        if not local_app:
            return None
        return local_app / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    if is_macos():
        # Standard CapCut for macOS draft root (to be verified on physical macOS hardware)
        return Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    return None


def clear_readonly_tree(root: Path) -> None:
    """Clear read-only attributes recursively so moves/deletes succeed cleanly."""
    for item in [root, *root.rglob("*")]:
        try:
            mode = item.stat().st_mode
            if not (mode & stat.S_IWRITE):
                os.chmod(item, mode | stat.S_IWRITE)
        except OSError:
            pass


def remove_tree_with_retries(src_dir: Path, *, retries: int = 3) -> List[str]:
    """Remove directory tree bottom-up, returning any still-locked relative paths."""
    src_dir = Path(src_dir)
    for attempt in range(retries):
        gc.collect()
        clear_readonly_tree(src_dir)
        locked: List[str] = []
        for f in sorted((p for p in src_dir.rglob("*") if p.is_file()), key=lambda p: len(p.parts), reverse=True):
            try:
                f.unlink()
            except OSError:
                locked.append(str(f.relative_to(src_dir)))
        for d in sorted((p for p in src_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        if not src_dir.exists() or not any(src_dir.rglob("*")):
            try:
                if src_dir.exists():
                    src_dir.rmdir()
                return []
            except OSError:
                pass
        if attempt < retries - 1:
            time.sleep(0.25 * (attempt + 1))

    remaining = []
    if src_dir.exists():
        for f in src_dir.rglob("*"):
            if f.is_file():
                remaining.append(str(f.relative_to(src_dir)))
        if not remaining:
            try:
                shutil.rmtree(src_dir)
                return []
            except OSError as exc:
                remaining.append(f"<folder: {exc}>")
    return remaining


class LockedFilesPermissionError(PermissionError):
    """PermissionError carrying the exact list of still-locked relative paths."""

    def __init__(self, message: str, locked_files: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.locked_files: List[str] = list(locked_files or [])


def move_dir_to_trash_reliable(
    src_dir: Path,
    dest_dir: Path,
    *,
    move_fn: Any = None,
) -> None:
    """Move a project directory to Trash reliably across Windows file locks or cross-device moves."""
    src_dir = Path(src_dir)
    dest_dir = Path(dest_dir)
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    do_move = move_fn or shutil.move

    last_exc: Optional[Exception] = None
    backoff_steps = (0.1, 0.2, 0.35, 0.5, 0.7) if is_windows() else (0.05, 0.15)
    for delay in backoff_steps:
        gc.collect()
        if is_windows():
            clear_readonly_tree(src_dir)
        try:
            do_move(str(src_dir), str(dest_dir))
            return
        except PermissionError as exc:
            last_exc = exc
            time.sleep(delay)
        except OSError as exc:
            last_exc = exc
            if getattr(exc, "errno", None) == errno.EXDEV:
                break
            time.sleep(delay)

    logger.warning(f"Direct move of '{src_dir}' hit ({last_exc}); using copy-then-remove fallback.")
    try:
        shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
    except Exception as copy_exc:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise OSError(f"Failed copying directory to Trash: {copy_exc}") from copy_exc

    locked = remove_tree_with_retries(src_dir, retries=3)
    if locked:
        try:
            for item in dest_dir.rglob("*"):
                rel = item.relative_to(dest_dir)
                target = src_dir / rel
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
            shutil.rmtree(dest_dir, ignore_errors=True)
        except Exception:
            pass
        sample = ", ".join(locked[:5]) + (" ..." if len(locked) > 5 else "")
        raise LockedFilesPermissionError(
            f"Tidak dapat memindahkan proyek karena berkas masih digunakan/terkunci oleh proses lain: {sample}",
            locked_files=locked,
        )


def open_path_in_os(path: Path | str, *, is_folder: bool = False) -> bool:
    """Open a file or directory using the host operating system's default handler."""
    p = Path(path).resolve()
    try:
        if is_windows():
            if is_folder and p.is_file():
                subprocess.Popen(["explorer.exe", f"/select,{str(p)}"])
            else:
                os.startfile(str(p))  # type: ignore[attr-defined]
            return True
        if is_macos():
            if is_folder and p.is_file():
                subprocess.Popen(["open", "-R", str(p)])
            else:
                subprocess.Popen(["open", str(p)])
            return True
        subprocess.Popen(["xdg-open", str(p.parent if (is_folder and p.is_file()) else p)])
        return True
    except Exception as exc:
        logger.warning(f"Could not open path {p}: {exc}")
        return False


def open_url_in_browser(url: str) -> bool:
    """Open URL in the user's default web browser cleanly across platforms."""
    target = str(url or "").strip()
    if not target:
        return False
    try:
        if webbrowser.open(target, new=2):
            return True
    except Exception as exc:
        logger.warning(f"webbrowser.open failed for {target}: {exc}")
    if is_windows():
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return True
        except Exception:
            pass
    elif is_macos():
        try:
            subprocess.Popen(["open", target])
            return True
        except Exception:
            pass
    else:
        try:
            subprocess.Popen(["xdg-open", target])
            return True
        except Exception:
            pass
    return False


def start_hidden_subprocess(
    cmd: Sequence[str],
    *,
    cwd: Path,
    log_file: Path,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.Popen:
    """Spawn a detached headless subprocess redirecting output to `log_file`."""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fp = open(log_path, "a", encoding="utf-8", buffering=1)
    kwargs: Dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": fp,
        "stderr": subprocess.STDOUT,
        "cwd": str(cwd),
        "close_fds": True,
    }
    if env is not None:
        kwargs["env"] = env
    if is_windows():
        kwargs["creationflags"] = get_hidden_subprocess_flags()
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen([str(x) for x in cmd], **kwargs)  # type: ignore[arg-type]
    except Exception:
        fp.close()
        raise


def launch_desktop_app_detached(
    cmd: Sequence[str],
    *,
    cwd: Path,
    log_file: Path,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.Popen:
    """Launch desktop server process cleanly per OS (detached & hidden on Windows)."""
    return start_hidden_subprocess(cmd, cwd=cwd, log_file=log_file, env=env)


def copy_text_to_clipboard(text: str) -> bool:
    """Copy text to the OS clipboard cleanly with headless subprocess/tkinter fallback."""
    if is_windows():
        try:
            subprocess.run(
                ["clip"],
                input=text.encode("utf-16-le"),
                check=True,
                creationflags=get_hidden_subprocess_flags(),
            )
            return True
        except Exception:
            pass
    elif is_macos():
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            return True
        except Exception:
            pass
    else:
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True)
                return True
            except Exception:
                pass
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return True
    except Exception:
        return False


_NAMED_MUTEX_HANDLES: Dict[str, Any] = {}


def acquire_named_mutex(mutex_name: str) -> Optional[int]:
    """Create/hold a named OS mutex for installer AppMutex detection (Windows) (D9.6)."""
    if not mutex_name:
        return None
    if mutex_name in _NAMED_MUTEX_HANDLES:
        return _NAMED_MUTEX_HANDLES[mutex_name]
    if is_windows():
        try:
            import ctypes
            handle = ctypes.windll.kernel32.CreateMutexW(None, False, str(mutex_name))
            if handle:
                _NAMED_MUTEX_HANDLES[mutex_name] = int(handle)
                return int(handle)
        except Exception:
            pass
    return None


def find_processes_in_directory(
    root_dir: Path,
    *,
    exclude_pids: Optional[Sequence[int]] = None,
) -> List[Dict[str, Any]]:
    """Return running processes whose executable lives inside `root_dir` (D9.6)."""
    root = Path(root_dir).resolve()
    excluded = {int(p) for p in (exclude_pids or ())} | {os.getpid()}
    found: List[Dict[str, Any]] = []

    if is_windows():
        ps_cmd = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.ExecutablePath } | "
            "Select-Object ProcessId, Name, ExecutablePath | ConvertTo-Json -Compress"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=get_hidden_subprocess_flags(),
            )
            if res.returncode == 0 and res.stdout.strip():
                import json as _json
                raw = _json.loads(res.stdout.strip())
                items = raw if isinstance(raw, list) else [raw]
                root_low = str(root).rstrip("\\/").lower()
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    pid = int(item.get("ProcessId") or 0)
                    exe = str(item.get("ExecutablePath") or "")
                    if pid <= 0 or pid in excluded or not exe:
                        continue
                    exe_low = exe.lower()
                    if exe_low.startswith(root_low + "\\") or exe_low.startswith(root_low + "/"):
                        found.append({
                            "pid": pid,
                            "name": str(item.get("Name") or ""),
                            "executable": exe,
                        })
        except Exception as exc:
            logger.warning(f"Failed inspecting Windows processes under {root}: {exc}")
    else:
        try:
            res = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=5)
            root_str = str(root)
            for line in res.stdout.splitlines()[1:]:
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and root_str in parts[1]:
                    pid = int(parts[0])
                    if pid > 0 and pid not in excluded:
                        found.append({"pid": pid, "name": parts[1].split()[0], "executable": parts[1]})
        except Exception:
            pass
    return found
