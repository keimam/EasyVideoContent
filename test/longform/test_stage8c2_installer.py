"""Tests for Stage 8C.2: Windows installer packaging, paths, launcher, platform_utils, and FFmpeg GPLv3 compliance."""

from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import config


def test_d0_license_owner_and_constants():
    """D0: Owner 'Naseh Ngulik' set cleanly in LICENSE and single-source constants without placeholder."""
    from app.services.branding.constants import APP_OWNER, APP_PUBLISHER, APP_SETUP_PREFIX, APP_VERSION

    repo_root = Path(__file__).resolve().parents[2]
    lic = (repo_root / "LICENSE").read_text(encoding="utf-8")
    assert "Copyright (c) 2024 Harry" in lic
    assert "Copyright (c) 2026 Naseh Ngulik" in lic
    assert "[NAMA PEMILIK]" not in lic
    assert APP_OWNER == "Naseh Ngulik"
    assert APP_PUBLISHER == "Naseh Ngulik"
    assert APP_SETUP_PREFIX == "EasyFacelessStudio-Setup"
    assert APP_VERSION == "1.0.1"


def test_d2_app_paths_source_vs_installed_and_efs_data_dir(tmp_path: Path, monkeypatch):
    """D2: Source checkout uses repo paths; EFS_DATA_DIR & installed mode isolate config/storage safely."""
    from app.services.longform import app_paths, presets, store
    from app.services.longform.visual import cache as visual_cache

    repo_root = Path(__file__).resolve().parents[2].resolve()

    # 1. Source checkout mode (clean env)
    monkeypatch.delenv("EFS_DATA_DIR", raising=False)
    monkeypatch.delenv("EFS_INSTALLED", raising=False)
    monkeypatch.delenv("MPT_HOME", raising=False)
    assert app_paths.get_install_root() == repo_root
    assert app_paths.is_installed_mode() is False
    assert app_paths.get_data_dir() == repo_root
    assert store.get_projects_root() == repo_root / "storage" / "longform_projects"
    res_dir, user_presets_dir = presets.get_preset_directories()
    assert res_dir == repo_root / "resource" / "presets"
    assert user_presets_dir == repo_root / "storage" / "presets"
    assert visual_cache.get_global_cache_dir() == repo_root / "storage" / "longform_cache"

    # 2. EFS_DATA_DIR override redirects ALL data paths cleanly & initializes config.toml without API keys
    custom_data = (tmp_path / "user_data_override").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(custom_data))
    assert app_paths.is_installed_mode() is True
    assert app_paths.get_data_dir() == custom_data
    assert store.get_projects_root() == custom_data / "storage" / "longform_projects"
    _, ov_presets_dir = presets.get_preset_directories()
    assert ov_presets_dir == custom_data / "storage" / "presets"
    assert visual_cache.get_global_cache_dir() == custom_data / "storage" / "longform_cache"

    app_paths.ensure_user_data_initialized()
    cfg_init = custom_data / "config.toml"
    assert cfg_init.is_file()
    cfg_txt = cfg_init.read_text(encoding="utf-8")
    assert "sk-or-v1-" not in cfg_txt
    assert (custom_data / "logs").is_dir()

    # Subsequent ensure_user_data_initialized preserves user's edited config.toml
    cfg_init.write_text("# user custom config\n[app]\nllm_provider = 'openrouter'\n", encoding="utf-8")
    app_paths.ensure_user_data_initialized()
    assert "user custom config" in cfg_init.read_text(encoding="utf-8")

    # 3. Installed mode without EFS_DATA_DIR uses %LOCALAPPDATA%\EasyFaceless Studio (via platform_utils)
    monkeypatch.delenv("EFS_DATA_DIR", raising=False)
    monkeypatch.setenv("EFS_INSTALLED", "1")
    fake_local = tmp_path / "FakeLocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    assert app_paths.is_installed_mode() is True
    expected_dir = (fake_local / "EasyFaceless Studio").resolve()
    assert app_paths.get_data_dir() == expected_dir


def test_d3_launcher_single_instance_port_and_exit_app_ui(tmp_path: Path, monkeypatch):
    """D3: Free port finder, single-instance reuse, shutdown helper, and sidebar Exit App confirmation."""
    from streamlit.testing.v1 import AppTest
    from app.services.longform import launcher_runtime, store

    # 1. find_free_port skips occupied port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    busy_port = sock.getsockname()[1]
    try:
        picked = launcher_runtime.find_free_port(start_port=busy_port, max_tries=10)
        assert picked > busy_port
    finally:
        sock.close()

    # 2. Single-instance reuses active healthy server instead of spawning a second process
    data_dir = tmp_path / "launcher_data"
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    from app.services.branding.constants import get_app_build_signature

    sig = get_app_build_signature()
    launcher_runtime.write_launcher_lock({
        "pid": 43210,
        "port": 8519,
        "url": "http://127.0.0.1:8519",
        "app_version": sig["app_version"],
        "build_id": sig["build_id"],
    })
    opened_urls: list[str] = []
    spawned: list[list[str]] = []
    monkeypatch.setattr("app.services.longform.platform_utils.is_pid_alive", lambda pid: pid == 43210)
    monkeypatch.setattr(launcher_runtime, "is_streamlit_server_healthy", lambda port, host="127.0.0.1", timeout=1.0: port == 8519)
    monkeypatch.setattr("app.services.longform.platform_utils.open_url_in_browser", lambda u: opened_urls.append(u) or True)
    monkeypatch.setattr(
        "app.services.longform.platform_utils.launch_desktop_app_detached",
        lambda cmd, **kw: spawned.append(list(cmd)),
    )

    info = launcher_runtime.launch_or_focus_app(open_browser=True)
    assert info["reused"] is True
    assert info["port"] == 8519
    assert opened_urls == ["http://127.0.0.1:8519"]
    assert spawned == []
    assert (data_dir / "logs" / "launcher.log").is_file()

    # 3. request_app_shutdown clears lock & invokes exit callback
    terminated_pids: list[int] = []
    monkeypatch.setattr(
        "app.services.longform.platform_utils.terminate_process_tree",
        lambda pid, **kw: terminated_pids.append(int(pid)) or True,
    )
    exited: list[int] = []
    res_shut = launcher_runtime.request_app_shutdown(
        stop_running_jobs=False,
        delay_seconds=0.05,
        exit_fn=lambda code: exited.append(code),
    )
    assert res_shut["lock_cleared"] is True
    assert not (data_dir / ".efs_launcher.lock.json").exists()
    import time
    for _ in range(20):
        if exited:
            break
        time.sleep(0.05)
    assert exited == [0]
    assert terminated_pids == [43210]

    # 4. Sidebar "Tutup aplikasi" opens dialog & warns when a background job is running
    repo_root = Path(__file__).resolve().parents[2]
    lv_file = str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")
    proj_dir = store.get_projects_root() / "running_demo"
    _, proj_dir = store.create_project("running_demo", "Demo Jalan", "documentary_id")
    (proj_dir / "job_state.json").write_text(
        json.dumps({"project_id": "running_demo", "step": "render_final", "status": "running", "pid": 99991}),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.longform.platform_utils.is_pid_alive", lambda pid: pid == 99991)
    monkeypatch.setitem(config.ui, "language", "id")

    at = AppTest.from_file(lv_file)
    at.query_params["lang"] = "id"
    at.run(timeout=25)
    assert not at.exception
    exit_btn = at.button(key="efs_btn_exit_app_longform")
    assert exit_btn is not None
    exit_btn.click().run(timeout=25)
    assert not at.exception
    warn_txt = "\n".join(str(w.value) for w in at.warning)
    assert "1 tugas" in warn_txt
    assert at.button(key="btn_exit_app_confirm") is not None


def test_d5_verify_package_script_catches_forbidden_items_and_secrets(tmp_path: Path):
    """D5: Package verification passes on clean staging and rejects config.toml, storage, _trash, logs, .git, test, screens, or API keys."""
    from scripts.build.verify_package import REQUIRED_BUNDLE_PATHS, verify_staging_dir

    clean = tmp_path / "staging_clean"
    for rel in REQUIRED_BUNDLE_PATHS:
        p = clean / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("ok", encoding="utf-8")
    (clean / "config.example.toml").write_text("[app]\nllm_provider = ''\n", encoding="utf-8")
    assert verify_staging_dir(clean) == []

    # Inject forbidden artifacts one by one
    bad_cases = [
        clean / "config.toml",
        clean / "storage" / "longform_projects" / "p1" / "manifest.json",
        clean / "_trash" / "old.txt",
        clean / "server.log",
        clean / "debug.backup.py",
        clean / ".git" / "HEAD",
        clean / "test" / "test_foo.py",
        clean / "docs" / "branding" / "screens" / "home.png",
        clean / "cache" / "item.bin",
        clean / "runtime" / "python" / "Lib" / "site-packages" / "leak.pth",
        clean / "app" / "leak_key.py",
    ]
    for fp in bad_cases:
        fp.parent.mkdir(parents=True, exist_ok=True)
        if fp.name == "leak.pth":
            fp.write_text("F:\\MPTLong\n", encoding="utf-8")
        elif fp.name == "leak_key.py":
            fp.write_text('KEY = "sk-or-v1-testsecret1234567890abcdef"\n', encoding="utf-8")
        else:
            fp.write_text("bad", encoding="utf-8")

    errs = verify_staging_dir(clean)
    assert len(errs) >= 10, f"Expected all forbidden cases caught, got: {errs}"


def test_d6_no_scattered_platform_checks_and_platform_utils_os_coverage(monkeypatch):
    """D6: Zero os.name=='nt' / sys.platform scattered outside platform_utils.py; cross-OS paths work."""
    from app.services.longform import platform_utils

    repo_root = Path(__file__).resolve().parents[2]
    scan_roots = [
        repo_root / "app" / "services" / "longform",
        repo_root / "app" / "services" / "branding",
        repo_root / "cli_longform.py",
    ]
    forbidden_re = re.compile(r"\bsys\.platform\b|os\.name\s*[!=]=\s*[\"']nt[\"']|\bCREATE_NO_WINDOW\b|\btaskkill\b|\btasklist\b|os\.startfile")

    for target in scan_roots:
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for fp in files:
            if fp.name == "platform_utils.py" and fp.parent.name == "longform":
                continue
            src = fp.read_text(encoding="utf-8")
            m = forbidden_re.search(src)
            assert m is None, f"D6 violation in {fp.relative_to(repo_root)}: found {m.group(0)!r}"

    # Verify per-OS path/behavior helpers under platform_utils
    monkeypatch.setattr(platform_utils, "get_platform_key", lambda: "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Demo\AppData\Local")
    assert platform_utils.get_hidden_subprocess_flags() != 0
    assert str(platform_utils.get_user_data_dir("EasyFaceless Studio")).endswith("EasyFaceless Studio")
    assert "com.lveditor.draft" in str(platform_utils.get_capcut_drafts_dir())

    monkeypatch.setattr(platform_utils, "get_platform_key", lambda: "darwin")
    assert platform_utils.get_hidden_subprocess_flags() == 0
    assert "Application Support" in str(platform_utils.get_user_data_dir("EasyFaceless Studio"))
    assert "Movies" in str(platform_utils.get_capcut_drafts_dir())
    assert str(platform_utils.get_capcut_product_info_path()).endswith("Info.plist")

    monkeypatch.setattr(platform_utils, "get_platform_key", lambda: "linux")
    assert platform_utils.get_hidden_subprocess_flags() == 0
    assert platform_utils.is_capcut_running() is False


def test_d7_ffmpeg_gplv3_licenses_and_notices():
    """D7: FFmpeg GPLv3 license text, build info, and THIRD_PARTY_NOTICES entries present."""
    repo_root = Path(__file__).resolve().parents[2]
    gpl = (repo_root / "licenses" / "FFMPEG-GPLv3-LICENSE.txt").read_text(encoding="utf-8")
    info = (repo_root / "licenses" / "FFMPEG-BUILD-INFO.txt").read_text(encoding="utf-8")
    notices = (repo_root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in gpl
    assert "Version 3" in gpl
    assert "gyan.dev" in info
    assert "https://github.com/FFmpeg/FFmpeg/commit/" in info
    assert "FFMPEG-GPLv3-LICENSE.txt" in notices
    assert "win64-lgpl" in notices
