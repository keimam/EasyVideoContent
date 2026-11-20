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


def _run_cdp_dom_check(browser_exe: str, base_url: str, *, expect_wizard: bool, expect_project_title: Optional[str] = None) -> List[str]:
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

        for route_idx, route in enumerate(["", "/Long_Video"], start=1):
            target = f"{base_url}{route}"
            ws.send(json.dumps({"id": 10 + route_idx, "method": "Page.navigate", "params": {"url": target}})); ws.recv()
            body = ""
            for _ in range(30):
                time.sleep(0.8)
                body = _eval(100 + route_idx, "document.body ? document.body.innerText : ''") or ""
                if ("EasyFaceless" in body and len(body.strip()) > 120) or any(n in body for n in _ERR_NEEDLES):
                    break
            time.sleep(1.0)
            body = _eval(200 + route_idx, "document.body ? document.body.innerText : ''") or ""
            exc_cnt = _eval(300 + route_idx, "document.querySelectorAll('[data-testid=\"stException\"]').length") or 0
            nav_vis = _eval(400 + route_idx, "(() => { const n = document.querySelector('[data-testid=\"stSidebarNav\"]'); return n ? getComputedStyle(n).display : 'none'; })()")

            if exc_cnt > 0:
                errs.append(f"[{target}] Found {exc_cnt} stException block(s) in DOM")
            for needle in _ERR_NEEDLES:
                if needle in body:
                    errs.append(f"[{target}] Found error text '{needle}' in rendered DOM")
            if "EasyFaceless" not in body:
                errs.append(f"[{target}] Branded sidebar header 'EasyFaceless' missing in DOM")
            if nav_vis != "none":
                errs.append(f"[{target}] Default Streamlit stSidebarNav is visible (display={nav_vis})")

            has_wizard = ("Wizard Pengaturan Pertama Kali" in body) or ("First-Run Setup Wizard" in body)
            if expect_wizard and not has_wizard:
                errs.append(f"[{target}] First-run wizard did not render on empty EFS_DATA_DIR")
            if not expect_wizard and has_wizard:
                errs.append(f"[{target}] First-run wizard unexpectedly appeared on populated EFS_DATA_DIR")
            if expect_wizard and ("Step 2: Configure the video" in body or "Langkah 2: Atur video" in body):
                errs.append(f"[{target}] Onboarding tour appeared while wizard is active")
            if expect_wizard and any(k in body for k in ("Pengaturan Skrip Video", "Video Script Settings", "Mulai video baru", "Start new video")):
                errs.append(f"[{target}] Page content appeared underneath standalone wizard")
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
                        # Poll for 5 full seconds to catch any id<->en flicker/loop
                        for sec_i in range(10):
                            time.sleep(0.5)
                            txt = _eval(470 + sec_i, "document.body ? document.body.innerText : ''") or ""
                            if "First-Run Setup Wizard" not in txt or "Wizard Pengaturan Pertama Kali" in txt:
                                errs.append(f"[{target}] Language flickered during 5s stability check (sample #{sec_i})")
                                break
                            if any(k in txt for k in ("Video Script Settings", "Pengaturan Skrip Video", "Generate Video")):
                                errs.append(f"[{target}] Short Video content leaked underneath English wizard")
                                break
            if route == "/Long_Video" and (not expect_wizard) and expect_project_title and expect_project_title not in body:
                errs.append(f"[{target}] Existing project '{expect_project_title}' missing on populated EFS_DATA_DIR")

        ws.close()
    finally:
        chrome.terminate()
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


def run_staged_smoke_test(staging: Path) -> List[str]:
    """Run live installed-mode smoke tests on staging runtime with empty & populated EFS_DATA_DIR (E8)."""
    staging = Path(staging).resolve()
    py_exe = staging / "runtime" / "python" / ("python.exe" if os.name == "nt" else "python3")
    main_py = staging / "webui" / "Main.py"
    if not py_exe.is_file() or not main_py.is_file():
        return [f"Missing staged python ({py_exe}) or Main.py ({main_py})"]

    errors: List[str] = []
    browser_exe = _find_headless_browser()

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
            srv.terminate()
            try:
                srv.wait(timeout=8)
            except Exception:
                srv.kill()
            if not log_path.is_file() or log_path.stat().st_size == 0:
                errors.append(f"[{scenario_name}] Staged server.log stayed empty (0 bytes)")
            shutil.rmtree(data_dir, ignore_errors=True)

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
