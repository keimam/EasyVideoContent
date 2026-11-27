"""Verify staged Windows installer package hygiene (Stage 8C.2, D5).

Fails non-zero if any forbidden developer/runtime artifact, personal secret/API key,
or developer checkout path is found inside the staging folder.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path
from typing import List, Optional


FORBIDDEN_TOP_OR_NESTED_NAMES = {
    ".git",
    ".venv",
    "storage",
    "_trash",
    "test",
    "tests",
    "dist",
    "build",
    "config.toml",
    ".pytest_cache",
    "__pycache__",
}

FORBIDDEN_SUBPATHS = (
    "docs/branding/screens",
    "storage/longform_projects",
    "storage/longform_cache",
    "storage/presets",
)

FORBIDDEN_FILE_PATTERNS = (
    "*.log",
    "*.backup*",
    "*.bak",
    "*.pyc",
    "*.pyo",
    "*_editable*.pth",
)

SECRET_TOKEN_RE = re.compile(
    r"\b(sk-(?:or-v1-|proj-|ant-)[a-zA-Z0-9_\-]{12,}|AIza[0-9A-Za-z_\-]{20,}|gsk_[0-9A-Za-z_\-]{16,})"
)

TEXT_CHECK_EXTS = {
    ".py",
    ".toml",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".pth",
    ".ini",
    ".cfg",
}

REQUIRED_BUNDLE_PATHS = (
    "EasyFaceless Studio.exe",
    ".efs_installed",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "config.example.toml",
    "cli.py",
    "cli_longform.py",
    "webui/Main.py",
    "runtime/python/python.exe",
    "runtime/python/pythonw.exe",
    "runtime/ffmpeg/bin/ffmpeg.exe",
    "runtime/ffmpeg/bin/ffprobe.exe",
    "licenses/FFMPEG-GPLv3-LICENSE.txt",
    "licenses/FFMPEG-BUILD-INFO.txt",
    "docs/longform/capcut_template_8.9.0/draft_content.json",
    "docs/longform/capcut_template_8.9.0/draft_meta_info.json",
)


def check_default_configs_no_fake(root: Path) -> List[str]:
    """Fail if packaged default configuration enables 'fake' visual provider or allow_fallback=true (D8)."""
    errors: List[str] = []

    cfg_example = root / "config.example.toml"
    if cfg_example.is_file():
        try:
            data = tomllib.loads(cfg_example.read_text(encoding="utf-8"))
            app_sec = data.get("app") if isinstance(data.get("app"), dict) else {}
            if str(app_sec.get("video_source", "")).strip().lower() == "fake":
                errors.append("Default config config.example.toml activates video_source='fake'")
            if str(app_sec.get("preferred_visual_source", "")).strip().lower() == "fake":
                errors.append("Default config config.example.toml activates preferred_visual_source='fake'")
            if app_sec.get("allow_fallback") is True:
                errors.append("Default config config.example.toml enables allow_fallback=true")
        except Exception as exc:
            errors.append(f"Failed to parse config.example.toml: {exc}")

    longform_toml = root / "config" / "longform.toml"
    if longform_toml.is_file():
        try:
            data = tomllib.loads(longform_toml.read_text(encoding="utf-8"))
            vis_sec = data.get("visual") if isinstance(data.get("visual"), dict) else {}
            for key in ("preferred_visual_source", "provider", "default_provider"):
                if str(vis_sec.get(key, "")).strip().lower() == "fake":
                    errors.append(f"Default config/longform.toml sets visual.{key}='fake'")
            if vis_sec.get("allow_fallback") is True:
                errors.append("Default config/longform.toml sets visual.allow_fallback=true")
        except Exception as exc:
            errors.append(f"Failed to parse config/longform.toml: {exc}")

    presets_dir = root / "resource" / "presets"
    if presets_dir.is_dir():
        for pfile in sorted(presets_dir.glob("*.json")):
            try:
                pdata = json.loads(pfile.read_text(encoding="utf-8"))
                if str(pdata.get("preferred_visual_source", "")).strip().lower() == "fake":
                    errors.append(f"Preset {pfile.name} sets preferred_visual_source='fake'")
                if pdata.get("allow_fallback") is True:
                    errors.append(f"Preset {pfile.name} sets allow_fallback=true")
            except Exception as exc:
                errors.append(f"Failed to parse preset {pfile.name}: {exc}")

    return errors


def verify_staging_dir(staging_dir: Path, *, check_required: bool = True) -> List[str]:
    """Return list of human-readable verification errors; empty list means clean."""
    root = Path(staging_dir).resolve()
    errors: List[str] = []
    if not root.is_dir():
        return [f"Staging directory does not exist: {root}"]

    errors.extend(check_default_configs_no_fake(root))

    if check_required:
        for rel in REQUIRED_BUNDLE_PATHS:
            if not (root / rel).is_file():
                errors.append(f"Missing required bundled file: {rel}")

        # Verify embedded build metadata in real installed staging build (E12.3)
        constants_py = root / "app" / "services" / "branding" / "constants.py"
        if (root / ".efs_installed").is_file() and constants_py.is_file() and constants_py.stat().st_size > 100:
            meta_file = root / "app" / "services" / "branding" / "_build_meta.json"
            if not meta_file.is_file():
                errors.append("Missing embedded build metadata: app/services/branding/_build_meta.json")
            else:
                try:
                    meta_obj = json.loads(meta_file.read_text(encoding="utf-8"))
                    if not str(meta_obj.get("git_commit") or "").strip() or not str(meta_obj.get("build_id") or "").strip():
                        errors.append("Invalid _build_meta.json: missing git_commit or build_id")
                except Exception as exc:
                    errors.append(f"Invalid _build_meta.json: {exc}")

        # Verify staged .streamlit/config.toml is valid BOM-free TOML with fileWatcherType = "none" (E10.1)
        st_cfg = root / "webui" / ".streamlit" / "config.toml"
        if st_cfg.is_file():
            raw_bytes = st_cfg.read_bytes()
            if raw_bytes.startswith(b"\xef\xbb\xbf"):
                errors.append("webui/.streamlit/config.toml has a UTF-8 BOM (breaks Python tomllib)")
            else:
                try:
                    parsed = tomllib.loads(raw_bytes.decode("utf-8"))
                    if parsed.get("server", {}).get("fileWatcherType") != "none":
                        errors.append("webui/.streamlit/config.toml [server] must set fileWatcherType = 'none' in packaged build")
                except Exception as exc:
                    errors.append(f"webui/.streamlit/config.toml invalid TOML: {exc}")

    for sub in FORBIDDEN_SUBPATHS:
        if (root / sub).exists():
            errors.append(f"Forbidden path present in staging: {sub}")

    personal_dev_path_re = re.compile(r"F:\\MPTLong|" + re.escape("IT " + "SUPPORT 2"), re.IGNORECASE)

    for item in root.rglob("*"):
        rel = item.relative_to(root)
        rel_posix = rel.as_posix()
        name = item.name
        name_low = name.lower()

        # site-packages legitimately contains subpackages like streamlit/runtime/caching/storage;
        # webui/.streamlit/config.toml is Streamlit theme config (not root user config.toml).
        in_site_packages = "site-packages" in rel.parts or rel_posix.startswith("runtime/python/Lib")
        if name_low == "config.toml" and rel_posix == "webui/.streamlit/config.toml":
            continue
        if name_low in FORBIDDEN_TOP_OR_NESTED_NAMES:
            if (not in_site_packages) or name_low in (".git", "config.toml", "_trash"):
                errors.append(f"Forbidden item present: {rel_posix}")
                continue

        if name_low == "cache" and not in_site_packages:
            errors.append(f"Forbidden cache folder present: {rel_posix}")
            continue

        if item.is_file():
            # Allow Python standard library/package internals where .log/.bak filenames rarely appear? Wait: still check app root/non-site-packages + always forbid config.toml / *.backup*
            for pat in FORBIDDEN_FILE_PATTERNS:
                if fnmatch.fnmatch(name_low, pat):
                    if in_site_packages and pat == "*.pyc":
                        continue
                    errors.append(f"Forbidden file pattern ({pat}): {rel_posix}")
                    break

            if item.suffix.lower() in TEXT_CHECK_EXTS and item.stat().st_size <= 2 * 1024 * 1024:
                try:
                    txt = item.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    txt = ""
                if SECRET_TOKEN_RE.search(txt):
                    errors.append(f"Potential secret/API key string leaked in {rel_posix}")
                if name_low.endswith(".pth") and personal_dev_path_re.search(txt):
                    errors.append(f"Developer machine path leaked in .pth file: {rel_posix}")

    return errors


_ERR_NEEDLES = (
    "StreamlitMissingRequiredParameterError",
    "StreamlitAPIException",
    "Internal Server Error",
    "NoEventLoopError",
    "Traceback (most recent call last)",
    "TypeError:",
    "AttributeError:",
    "ModuleNotFoundError:",
    "ImportError:",
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _terminate_pid_tree(pid: Optional[int]) -> None:
    """Terminate `pid` and all descendant processes cleanly (R1.4)."""
    if not pid or int(pid) <= 0:
        return
    p = int(pid)
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(p)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    else:
        import signal
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(p, sig)
            except ProcessLookupError:
                break
            except Exception:
                pass
            time.sleep(0.2)


def _stop_popen(proc: Optional[subprocess.Popen]) -> None:
    """Terminate a Popen process tree cleanly in `finally` blocks (R1.4)."""
    if proc is None:
        return
    try:
        if proc.poll() is None:
            _terminate_pid_tree(proc.pid)
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def find_staging_processes(staging: Path) -> List[dict]:
    """Return active processes whose executable or command line belongs to `staging` (R1.4)."""
    root = Path(staging).resolve()
    root_low = str(root).rstrip("\\/").lower()
    found: List[dict] = []
    if os.name == "nt":
        ps_cmd = (
            "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
            "Where-Object { $_.ExecutablePath -or $_.CommandLine } | "
            "Select-Object ProcessId, Name, ExecutablePath, CommandLine | ConvertTo-Json -Compress"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0 and res.stdout.strip():
                raw = json.loads(res.stdout.strip())
                items = raw if isinstance(raw, list) else [raw]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    pid = int(item.get("ProcessId") or 0)
                    exe = str(item.get("ExecutablePath") or "")
                    cmd = str(item.get("CommandLine") or "")
                    if pid <= 0 or pid == os.getpid():
                        continue
                    exe_low = exe.lower()
                    cmd_low = cmd.lower()
                    if exe_low.startswith(root_low + "\\") or exe_low.startswith(root_low + "/") or root_low in cmd_low:
                        found.append({"pid": pid, "name": str(item.get("Name") or ""), "executable": exe})
        except Exception:
            pass
    return found


def cleanup_staging_processes(staging: Path, *, exclude_pids: Optional[set[int]] = None) -> List[int]:
    """Kill any processes still running inside `staging` after tests (R1.4)."""
    excluded = set(exclude_pids or ()) | {os.getpid()}
    killed: List[int] = []
    for proc_info in find_staging_processes(staging):
        pid = int(proc_info["pid"])
        if pid not in excluded:
            _terminate_pid_tree(pid)
            killed.append(pid)
    return killed


def _find_headless_browser() -> Optional[str]:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    return None


def _build_staged_env(staging: Path, data_dir: Path) -> dict[str, str]:
    py_dir = staging / "runtime" / "python"
    ff_bin = staging / "runtime" / "ffmpeg" / "bin"
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    base_path = f"{ff_bin}{os.pathsep}{py_dir}{os.pathsep}{sys_root}\\System32" if os.name == "nt" else f"{ff_bin}{os.pathsep}{py_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    return {
        "SystemRoot": sys_root,
        "TEMP": str(data_dir),
        "TMP": str(data_dir),
        "HOME": str(data_dir),
        "USERPROFILE": os.environ.get("USERPROFILE", str(data_dir)),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", str(data_dir)),
        "EFS_INSTALLED": "1",
        "EFS_DATA_DIR": str(data_dir),
        "PYTHONHOME": str(py_dir),
        "PYTHONPATH": str(staging),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "STREAMLIT_SERVER_FILE_WATCHER_TYPE": "none",
        "PATH": base_path,
    }


def _check_http_endpoint(url: str, timeout: float = 5.0) -> Optional[str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status = int(getattr(resp, "status", 0))
            if status != 200:
                return f"HTTP {status} for {url}"
            body = resp.read().decode("utf-8", errors="ignore")
            for needle in _ERR_NEEDLES:
                if needle in body:
                    return f"Error marker '{needle}' in HTTP body of {url}"
        return None
    except Exception as exc:
        return f"HTTP request failed for {url}: {exc}"


def _read_app_version_from_tree(root: Path) -> str:
    const_py = root / "app" / "services" / "branding" / "constants.py"
    if const_py.is_file():
        m = re.search(r'^\s*APP_VERSION\s*=\s*"([^"]+)"', const_py.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            return m.group(1).strip()
    return "1.0.2"


def _next_patch_version(ver: str) -> str:
    parts = [int(p) for p in re.findall(r"\d+", str(ver or "1.0.2"))[:3]]
    while len(parts) < 3:
        parts.append(0)
    return f"{parts[0]}.{parts[1]}.{parts[2] + 1}"


def _run_cdp_dom_check(
    browser_exe: str,
    base_url: str,
    *,
    expect_wizard: bool,
    expect_project_title: Optional[str] = None,
    expect_update_banner: Optional[bool] = None,
    expected_update_version: Optional[str] = None,
) -> List[str]:
    errs: List[str] = []
    try:
        import websocket  # type: ignore
    except Exception:
        return errs

    cdp_port = _find_free_port()
    chr_dir = Path(tempfile.mkdtemp(prefix="efs_smoke_chr_"))
    chrome = subprocess.Popen(
        [
            browser_exe,
            "--headless=new",
            f"--remote-debugging-port={cdp_port}",
            "--window-size=1440,1000",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-data-dir={chr_dir}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(30):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2).read())
                if tabs:
                    ws_url = tabs[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                time.sleep(0.3)
        if not ws_url:
            errs.append("Headless browser CDP endpoint failed to respond")
            return errs

        ws = websocket.create_connection(ws_url, timeout=30)

        def _eval(mid: int, expr: str):
            ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
            while True:
                d = json.loads(ws.recv())
                if d.get("id") == mid:
                    return d.get("result", {}).get("result", {}).get("value")

        ws.send(json.dumps({"id": 1, "method": "Page.enable"})); ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Runtime.enable"})); ws.recv()

        def _check_loaded_dom(label: str, route: str, mid_base: int) -> str:
            body = ""
            for _ in range(60):
                time.sleep(0.75)
                body = _eval(mid_base, "document.body ? document.body.innerText : ''") or ""
                if ("EasyFaceless" in body and len(body.strip()) > 120) or any(n in body for n in _ERR_NEEDLES):
                    break
            time.sleep(0.8)
            body = _eval(mid_base + 1, "document.body ? document.body.innerText : ''") or ""
            exc_cnt = _eval(mid_base + 2, "document.querySelectorAll('[data-testid=\"stException\"]').length") or 0
            nav_vis = _eval(mid_base + 3, "(() => { const n = document.querySelector('[data-testid=\"stSidebarNav\"]'); return n ? getComputedStyle(n).display : 'none'; })()")

            if exc_cnt > 0:
                errs.append(f"[{label}] Found {exc_cnt} stException block(s) in DOM")
            for needle in _ERR_NEEDLES:
                if needle in body:
                    errs.append(f"[{label}] Found error text '{needle}' in rendered DOM")
            if "EasyFaceless" not in body:
                errs.append(f"[{label}] Branded sidebar header 'EasyFaceless' missing in DOM")
            if nav_vis != "none":
                errs.append(f"[{label}] Default Streamlit stSidebarNav is visible (display={nav_vis})")

            is_help_route = "dialog=help" in route
            has_help = any(k in body for k in ("Bantuan & Panduan Pengguna", "Help & User Guide", "Mulai Cepat", "Quick Start"))
            has_wizard = ("Wizard Pengaturan Pertama Kali" in body) or ("First-Run Setup Wizard" in body)
            if is_help_route:
                if not (has_help or has_wizard):
                    errs.append(f"[{label}] Help route did not render Help dialog or Wizard cleanly")
            else:
                if expect_wizard and not has_wizard:
                    errs.append(f"[{label}] First-run wizard did not render on empty EFS_DATA_DIR")
                if (not expect_wizard) and has_wizard:
                    errs.append(f"[{label}] First-run wizard unexpectedly appeared on populated EFS_DATA_DIR")
            if expect_wizard and ("Step 2: Configure the video" in body or "Langkah 2: Atur video" in body):
                errs.append(f"[{label}] Onboarding tour appeared while wizard is active")
            if expect_wizard and any(k in body for k in ("Pengaturan Skrip Video", "Video Script Settings", "Mulai video baru", "Start new video")):
                errs.append(f"[{label}] Page content appeared underneath standalone wizard")
            if route == "/Long_Video" and (not expect_wizard) and expect_project_title and expect_project_title not in body:
                errs.append(f"[{label}] Existing project '{expect_project_title}' missing on populated EFS_DATA_DIR")
            exp_ver = str(expected_update_version or "1.0.2")
            if expect_update_banner is True and (f"v{exp_ver}" not in body or ("Pembaruan Tersedia" not in body and "Update Available" not in body)):
                errs.append(f"[{label}] Expected v{exp_ver} update banner did not appear automatically")
            if expect_update_banner is False and ("Pembaruan Tersedia" in body or "Update Available" in body):
                errs.append(f"[{label}] Unexpected update banner appeared")
            return body

        # E10.3: Check Help/route navigation + direct sub-page + F5 reload on each route
        routes = ["", "/Long_Video", "/?dialog=help"]
        if expect_update_banner is not None:
            routes = [""]

        for route_idx, route in enumerate(routes, start=1):
            target = f"{base_url}{route}"
            ws.send(json.dumps({"id": 10 * route_idx, "method": "Page.navigate", "params": {"url": target}})); ws.recv()
            body = _check_loaded_dom(target, route, 100 * route_idx)

            if expect_update_banner is None and route in ("", "/Long_Video"):
                # E10.3: Full browser refresh (F5 equivalent) on direct routes must stay healthy & branded
                ws.send(json.dumps({"id": 10 * route_idx + 5, "method": "Page.reload", "params": {"ignoreCache": True}})); ws.recv()
                _check_loaded_dom(f"{target} (F5 reload)", route, 100 * route_idx + 50)

            if expect_wizard and route == "":
                # E9.3: Choose English (en) in wizard and verify 5-second language & layout stability (zero flip-flop)
                clicked_en = _eval(
                    450,
                    """(() => {
                        const labels = Array.from(document.querySelectorAll('label, [data-testid="stRadio"] p, span'));
                        const hit = labels.find(el => (el.innerText || '').trim() === 'English (en)');
                        if (hit) { hit.click(); return true; }
                        return false;
                    })()""",
                )
                if not clicked_en:
                    errs.append(f"[{target}] Could not click 'English (en)' radio in wizard")
                else:
                    switched_ok = False
                    for _ in range(25):
                        time.sleep(0.4)
                        txt = _eval(460, "document.body ? document.body.innerText : ''") or ""
                        if "First-Run Setup Wizard" in txt and "Wizard Pengaturan Pertama Kali" not in txt:
                            switched_ok = True
                            break
                    if not switched_ok:
                        errs.append(f"[{target}] Wizard did not switch cleanly to English after clicking English (en)")
                    else:
                        for sec_i in range(10):
                            time.sleep(0.5)
                            txt = _eval(470 + sec_i, "document.body ? document.body.innerText : ''") or ""
                            if "First-Run Setup Wizard" not in txt or "Wizard Pengaturan Pertama Kali" in txt:
                                errs.append(f"[{target}] Language flickered during 5s stability check (sample #{sec_i})")
                                break
                            if any(k in txt for k in ("Video Script Settings", "Pengaturan Skrip Video", "Generate Video")):
                                errs.append(f"[{target}] Short Video content leaked underneath English wizard")
                                break

        ws.close()
    finally:
        _stop_popen(chrome)
        shutil.rmtree(chr_dir, ignore_errors=True)
    return errs


def _run_bundled_apptest_smoke(py_exe: Path, staging: Path, env: dict[str, str], *, expect_wizard: bool) -> List[str]:
    errs: List[str] = []
    code = f"""
import sys
from pathlib import Path
from streamlit.testing.v1 import AppTest

for page_rel in ["webui/Main.py", "webui/pages/1_🎬_Long_Video.py"]:
    p = Path(r"{staging}") / page_rel
    at = AppTest.from_file(str(p))
    at.run(timeout=75)
    if at.exception:
        raise AssertionError(f"{{page_rel}} AppTest exception: {{[e.value for e in at.exception]}}")
    md_all = " ".join(m.value for m in at.markdown)
    if "EasyFaceless" not in md_all:
        raise AssertionError(f"{{page_rel}} missing branded sidebar 'EasyFaceless'")
    has_wiz = ("Wizard Pengaturan Pertama Kali" in md_all) or ("First-Run Setup Wizard" in md_all)
    if {expect_wizard!r} and not has_wiz:
        raise AssertionError(f"{{page_rel}} expected wizard on empty data dir")
    if (not {expect_wizard!r}) and has_wiz:
        raise AssertionError(f"{{page_rel}} unexpected wizard on populated data dir")
print("BUNDLED_APPTEST_OK")
"""
    res = subprocess.run([str(py_exe), "-B", "-c", code], cwd=str(staging), env=env, capture_output=True, text=True, timeout=180)
    if res.returncode != 0 or "BUNDLED_APPTEST_OK" not in res.stdout:
        errs.append(f"Bundled AppTest smoke failed (exit={res.returncode}): {res.stderr.strip() or res.stdout.strip()}")
    return errs


def _run_native_exe_smoke(staging: Path, browser_exe: Optional[str]) -> List[str]:
    """E11.5: Launch real EasyFaceless Studio.exe with EFS_UPDATE_URL and verify banner, badge, About check & server.log."""
    errs: List[str] = []
    exe_path = staging / "EasyFaceless Studio.exe"
    if not exe_path.is_file():
        return [f"Missing native launcher executable for E11.5 smoke: {exe_path}"]

    cur_ver = _read_app_version_from_tree(staging)
    next_ver = _next_patch_version(cur_ver)

    u_dir = Path(tempfile.mkdtemp(prefix="efs_e11_exe_smoke_"))
    srv_pid: Optional[int] = None
    proc: Optional[subprocess.Popen] = None
    try:
        (u_dir / ".efs_setup_complete.json").write_text(f'{{"completed": true, "version": "{cur_ver}"}}\n', encoding="utf-8")
        (u_dir / "config.toml").write_text('[ui]\nlanguage = "id"\n', encoding="utf-8")
        manifest_path = u_dir / "latest_next.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": next_ver,
                    "download_url": f"https://example.invalid/EasyFacelessStudio-Setup-{next_ver}.exe",
                    "notes_id": "Perbaikan pembaruan & stabilitas.",
                    "notes_en": "Update reliability & stability fixes.",
                    "released": "2026-11-23",
                }
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["EFS_DATA_DIR"] = str(u_dir)
        env["EFS_UPDATE_URL"] = manifest_path.as_uri()
        env["EFS_NO_BROWSER"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

        proc = subprocess.Popen(
            [str(exe_path)],
            cwd=str(staging),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        lock_path = u_dir / ".efs_launcher.lock.json"
        base_url = ""
        lock_data: dict = {}
        for _ in range(90):
            if lock_path.is_file():
                try:
                    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
                    cand_url = str(lock_data.get("url") or "").strip().rstrip("/")
                    cand_pid = lock_data.get("pid")
                    if isinstance(cand_pid, int):
                        srv_pid = cand_pid
                    if cand_url:
                        with urllib.request.urlopen(f"{cand_url}/_stcore/health", timeout=2) as resp:
                            if int(getattr(resp, "status", 0)) == 200:
                                base_url = cand_url
                                break
                except Exception:
                    pass
            time.sleep(0.5)

        if not base_url:
            errs.append("[E11.5 native exe] EasyFaceless Studio.exe server failed to become ready")
            return errs
        if not str(lock_data.get("app_version") or "").strip() or not str(lock_data.get("build_id") or "").strip():
            errs.append("[E12.1 native exe] Launcher lock file missing app_version or build_id")

        if browser_exe:
            try:
                import websocket  # type: ignore
            except Exception:
                websocket = None  # type: ignore

            if websocket is not None:
                cdp_port = _find_free_port()
                chr_dir = Path(tempfile.mkdtemp(prefix="efs_e11_chr_"))
                chrome = subprocess.Popen(
                    [
                        browser_exe,
                        "--headless=new",
                        f"--remote-debugging-port={cdp_port}",
                        "--window-size=1440,1000",
                        "--disable-gpu",
                        "--no-sandbox",
                        f"--user-data-dir={chr_dir}",
                        "about:blank",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    ws_url = None
                    for _ in range(30):
                        try:
                            tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2).read())
                            if tabs:
                                ws_url = tabs[0]["webSocketDebuggerUrl"]
                                break
                        except Exception:
                            time.sleep(0.3)
                    if not ws_url:
                        errs.append("[E11.5 native exe] Headless browser CDP endpoint failed")
                    else:
                        ws = websocket.create_connection(ws_url, timeout=30)

                        def _eval(mid: int, expr: str):
                            ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
                            while True:
                                d = json.loads(ws.recv())
                                if d.get("id") == mid:
                                    return d.get("result", {}).get("result", {}).get("value")

                        ws.send(json.dumps({"id": 1, "method": "Page.enable"})); ws.recv()
                        ws.send(json.dumps({"id": 2, "method": "Runtime.enable"})); ws.recv()

                        for idx, route in enumerate(("/Long_Video", ""), start=1):
                            ws.send(json.dumps({"id": 10 * idx, "method": "Page.navigate", "params": {"url": f"{base_url}{route}"}})); ws.recv()
                            body = ""
                            for _ in range(60):
                                time.sleep(0.6)
                                body = _eval(100 * idx, "document.body ? document.body.innerText : ''") or ""
                                if f"v{next_ver}" in body and (f"Pembaruan tersedia (v{next_ver})" in body or f"Update available (v{next_ver})" in body):
                                    break
                            exc_cnt = _eval(100 * idx + 1, "document.querySelectorAll('[data-testid=\"stException\"]').length") or 0
                            if exc_cnt > 0:
                                errs.append(f"[E11.5 native exe {route or '/'}] Found {exc_cnt} stException(s)")
                            if f"v{next_ver}" not in body or ("Pembaruan Tersedia" not in body and "Update Available" not in body):
                                errs.append(f"[E11.5 native exe {route or '/'}] Missing v{next_ver} update banner")
                            if f"Pembaruan tersedia (v{next_ver})" not in body and f"Update available (v{next_ver})" not in body:
                                errs.append(f"[E11.5 native exe {route or '/'}] Missing sidebar update badge")

                        # Open About dialog from sidebar & click "Cek pembaruan sekarang"
                        opened_about = _eval(
                            300,
                            """(() => {
                                const btns = Array.from(document.querySelectorAll('button'));
                                const hit = btns.find(b => ['Tentang', 'About'].some(lbl => (b.innerText || '').includes(lbl)));
                                if (hit) { hit.click(); return true; }
                                return false;
                            })()""",
                        )
                        if not opened_about:
                            errs.append("[E11.5 native exe] Could not find About button in sidebar")
                        else:
                            dlg_ok = False
                            for _ in range(25):
                                time.sleep(0.4)
                                dlg_txt = _eval(301, "document.body ? document.body.innerText : ''") or ""
                                if "Cek pembaruan sekarang" in dlg_txt or "Check for updates now" in dlg_txt:
                                    dlg_ok = True
                                    break
                            if not dlg_ok:
                                errs.append("[E11.5 native exe] About dialog did not render update check button")
                            else:
                                _eval(
                                    302,
                                    """(() => {
                                        const btns = Array.from(document.querySelectorAll('[role="dialog"] button, button'));
                                        const hit = btns.find(b => ['Cek pembaruan sekarang', 'Check for updates now'].some(lbl => (b.innerText || '').includes(lbl)));
                                        if (hit) { hit.click(); return true; }
                                        return false;
                                    })()""",
                                )
                                check_ok = False
                                dlg_txt = ""
                                for _ in range(30):
                                    time.sleep(0.5)
                                    dlg_txt = _eval(303, "document.body ? document.body.innerText : ''") or ""
                                    if (
                                        f"v{next_ver}" in dlg_txt
                                        and ("tersedia" in dlg_txt.lower() or "available" in dlg_txt.lower())
                                        and ("Unduh Pembaruan" in dlg_txt or "Download Update" in dlg_txt)
                                    ):
                                        check_ok = True
                                        break
                                if not check_ok:
                                    errs.append(f"[E11.5/E12.5 native exe] About update check did not report v{next_ver} + Download button")
                                if "Tutup dialog Pengaturan" in dlg_txt or "Close Settings" in dlg_txt:
                                    errs.append("[E12.5 native exe] About update check result still mentions Settings/Pengaturan")
                                if "· build " not in dlg_txt:
                                    errs.append("[E12.3 native exe] About dialog missing '· build <hash>' badge")
                        ws.close()
                finally:
                    _stop_popen(chrome)
                    shutil.rmtree(chr_dir, ignore_errors=True)

        # Wait briefly for log flush & inspect server.log
        time.sleep(1.0)
        srv_log = u_dir / "logs" / "server.log"
        if not srv_log.is_file():
            errs.append(f"[E11.5 native exe] Missing server.log at {srv_log}")
        else:
            log_txt = srv_log.read_text(encoding="utf-8", errors="replace")
            if "Update env at startup:" not in log_txt:
                errs.append("[E11.5 native exe] server.log missing startup EFS_UPDATE_URL log")
            if "decision=update_available" not in log_txt:
                errs.append("[E11.5 native exe] server.log missing decision=update_available")
    finally:
        _terminate_pid_tree(srv_pid)
        _stop_popen(proc)
        try:
            (u_dir / ".efs_launcher.lock.json").unlink(missing_ok=True)
        except Exception:
            pass
        time.sleep(0.5)
        shutil.rmtree(u_dir, ignore_errors=True)
    return errs


def run_staged_smoke_test(staging: Path) -> List[str]:
    """Run live installed-mode smoke tests on staging runtime with empty & populated EFS_DATA_DIR (E8)."""
    staging = Path(staging).resolve()
    py_exe = staging / "runtime" / "python" / ("python.exe" if os.name == "nt" else "python3")
    main_py = staging / "webui" / "Main.py"
    if not py_exe.is_file() or not main_py.is_file():
        return [f"Missing staged python ({py_exe}) or Main.py ({main_py})"]

    errors: List[str] = []
    browser_exe = _find_headless_browser()
    pre_pids = {int(p["pid"]) for p in find_staging_processes(staging)}

    # Scenario 1: Empty EFS_DATA_DIR -> Wizard + branded sidebar + zero Streamlit exceptions
    # Scenario 2: Populated EFS_DATA_DIR -> Project visible + no Wizard + zero exceptions
    for scenario_name, populate_project in (("empty_data_dir", False), ("populated_data_dir", True)):
        data_dir = Path(tempfile.mkdtemp(prefix=f"efs_smoke_{scenario_name}_"))
        env = _build_staged_env(staging, data_dir)
        project_title = "Smoke Test Existing Doc" if populate_project else None

        if populate_project:
            seed_code = (
                "from app.services.longform import app_paths, first_run; "
                "from app.services.longform.store import create_project; "
                "app_paths.bootstrap_runtime_environment(); "
                "first_run.mark_setup_completed(skipped=False); "
                "create_project('smoke_existing_proj', 'Smoke Test Existing Doc', 'documentary_id')"
            )
            sres = subprocess.run([str(py_exe), "-B", "-c", seed_code], cwd=str(staging), env=env, capture_output=True, text=True, timeout=30)
            if sres.returncode != 0:
                errors.append(f"[{scenario_name}] Failed seeding populated data dir: {sres.stderr.strip()}")
                shutil.rmtree(data_dir, ignore_errors=True)
                continue

        # A. Run bundled AppTest smoke on Main.py and 1_Long_Video.py
        errors.extend(_run_bundled_apptest_smoke(py_exe, staging, env, expect_wizard=not populate_project))

        # B. Launch real Streamlit HTTP server on random free port + check endpoints & headless browser DOM
        port = _find_free_port()
        log_path = data_dir / "logs" / "server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as fp:
            srv = subprocess.Popen(
                [
                    str(py_exe), "-m", "streamlit", "run", str(main_py),
                    "--server.port", str(port),
                    "--server.address", "127.0.0.1",
                    "--server.headless", "true",
                    "--browser.gatherUsageStats", "false",
                    "--global.developmentMode", "false",
                    "--server.fileWatcherType", "none",
                ],
                cwd=str(staging),
                env=env,
                stdout=fp,
                stderr=subprocess.STDOUT,
            )
        try:
            base_url = f"http://127.0.0.1:{port}"
            ready = False
            for _ in range(45):
                if srv.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(f"{base_url}/_stcore/health", timeout=1) as r:
                        if int(getattr(r, "status", 0)) == 200:
                            ready = True
                            break
                except Exception:
                    time.sleep(0.5)

            if not ready:
                errors.append(f"[{scenario_name}] Staged Streamlit server failed to become ready on {base_url}")
            else:
                for path_suffix in ("/", "/Long_Video"):
                    http_err = _check_http_endpoint(f"{base_url}{path_suffix}")
                    if http_err:
                        errors.append(f"[{scenario_name}] {http_err}")
                if browser_exe:
                    errors.extend(
                        _run_cdp_dom_check(
                            browser_exe,
                            base_url,
                            expect_wizard=not populate_project,
                            expect_project_title=project_title,
                        )
                    )
        finally:
            _stop_popen(srv)
            try:
                (data_dir / ".efs_launcher.lock.json").unlink(missing_ok=True)
            except Exception:
                pass
            if not log_path.is_file() or log_path.stat().st_size == 0:
                errors.append(f"[{scenario_name}] Staged server.log stayed empty (0 bytes)")
            shutil.rmtree(data_dir, ignore_errors=True)

    # E10.3: Live staged update-banner smoke checks (newer version banner, current version no banner, invalid URL graceful)
    if browser_exe:
        _cur_ver = _read_app_version_from_tree(staging)
        _next_ver = _next_patch_version(_cur_ver)
        for upd_label, manifest_payload, expect_banner in (
            ("update_next", {"version": _next_ver, "released": "2026-12-01", "notes_id": f"Catatan smoke {_next_ver}", "notes_en": f"Smoke release {_next_ver}", "download_url": f"https://example.com/{_next_ver}.exe"}, True),
            ("same_cur", {"version": _cur_ver, "released": "2026-11-23"}, False),
            ("invalid_url", None, False),
        ):
            u_dir = Path(tempfile.mkdtemp(prefix=f"efs_smoke_upd_{upd_label}_"))
            u_env = _build_staged_env(staging, u_dir)
            if manifest_payload is not None:
                m_path = u_dir / "latest.json"
                m_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
                u_env["EFS_UPDATE_URL"] = m_path.as_uri()
            else:
                u_env["EFS_UPDATE_URL"] = "http://127.0.0.1:9/nonexistent.json"
            # Seed setup complete so we test post-wizard Home banner state directly
            (u_dir / ".efs_setup_complete.json").write_text('{"completed": true}\n', encoding="utf-8")
            u_port = _find_free_port()
            u_log = u_dir / "logs" / "server.log"
            u_log.parent.mkdir(parents=True, exist_ok=True)
            with open(u_log, "w", encoding="utf-8") as u_fp:
                u_srv = subprocess.Popen(
                    [
                        str(py_exe), "-m", "streamlit", "run", str(main_py),
                        "--server.port", str(u_port),
                        "--server.address", "127.0.0.1",
                        "--server.headless", "true",
                        "--browser.gatherUsageStats", "false",
                        "--global.developmentMode", "false",
                        "--server.fileWatcherType", "none",
                    ],
                    cwd=str(staging),
                    env=u_env,
                    stdout=u_fp,
                    stderr=subprocess.STDOUT,
                )
            try:
                u_url = f"http://127.0.0.1:{u_port}"
                u_ready = False
                for _ in range(45):
                    if u_srv.poll() is not None:
                        break
                    try:
                        with urllib.request.urlopen(f"{u_url}/_stcore/health", timeout=1) as r:
                            if int(getattr(r, "status", 0)) == 200:
                                u_ready = True
                                break
                    except Exception:
                        time.sleep(0.5)
                if not u_ready:
                    errors.append(f"[{upd_label}] Staged server failed to become ready")
                else:
                    errors.extend(
                        _run_cdp_dom_check(
                            browser_exe,
                            u_url,
                            expect_wizard=False,
                            expect_update_banner=expect_banner,
                            expected_update_version=_next_ver,
                        )
                    )
            finally:
                _stop_popen(u_srv)
                try:
                    (u_dir / ".efs_launcher.lock.json").unlink(missing_ok=True)
                except Exception:
                    pass
                shutil.rmtree(u_dir, ignore_errors=True)

    try:
        errors.extend(_run_native_exe_smoke(staging, browser_exe))
    finally:
        leaked = cleanup_staging_processes(staging, exclude_pids=pre_pids)
        if leaked:
            errors.append(f"[R1.4] Smoke test left lingering staging process PID(s): {leaked}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify EasyFaceless Studio staging folder hygiene and runtime smoke.")
    parser.add_argument("--staging", required=True, help="Path to staged package folder")
    parser.add_argument("--skip-required", action="store_true", help="Only check forbidden items & secrets")
    parser.add_argument("--smoke", action="store_true", help="Run live installed-mode smoke tests (empty & populated EFS_DATA_DIR)")
    args = parser.parse_args()

    staging = Path(args.staging)
    errs = verify_staging_dir(staging, check_required=not args.skip_required)
    if errs:
        print(f"[PACKAGE VERIFY FAILED] Found {len(errs)} hygiene issue(s):", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"[PACKAGE VERIFY PASSED] Clean staging package verified at: {staging.resolve()}")

    if args.smoke:
        smoke_errs = run_staged_smoke_test(staging)
        if smoke_errs:
            print(f"[SMOKE TEST FAILED] Found {len(smoke_errs)} runtime issue(s):", file=sys.stderr)
            for se in smoke_errs:
                print(f"  - {se}", file=sys.stderr)
            return 1
        print("[SMOKE TEST PASSED] Staged runtime verified cleanly for empty & populated EFS_DATA_DIR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
