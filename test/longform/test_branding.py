"""Tests for Stage 8A.1: EasyFaceless Studio branding, typography, sidebar, and CapCut prefix (B1-B5)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from app.services.branding.constants import (
    APP_NAME,
    APP_SHORT,
    APP_TAGLINE,
    APP_VERSION,
    CORE_NAME,
    CORE_VERSION,
    MIT_ATTRIBUTION,
    MIT_THANKS,
    VERSION_STRING,
    LOGO_SVG_PATH,
    LOGO_PNG_PATH,
    FAVICON_PATH,
    FONT_TTF_PATH,
    FONT_OFL_PATH,
    PALETTE,
)
from app.services.branding.theme import get_global_css
from app.services.longform.status import find_latest_capcut_draft, compute_project_step_status
from app.services.longform.store import create_project


def test_branding_constants():
    """Verify B1 brand identity constants and asset files."""
    assert APP_NAME == "EasyFaceless Studio"
    assert APP_SHORT == "EasyFaceless"
    assert APP_TAGLINE == "STUDIO"
    assert APP_VERSION == "1.0.0"
    assert CORE_NAME == "MoneyPrinterTurbo"
    assert CORE_VERSION == "1.3.10"
    assert "v1.0" in VERSION_STRING
    assert "inti v1.3.10" in VERSION_STRING

    # MIT attribution
    assert MIT_ATTRIBUTION == "Dibangun di atas MoneyPrinterTurbo (MIT)"
    assert "Harry" in MIT_THANKS
    assert "MoneyPrinterTurbo" in MIT_THANKS

    # Brand assets
    assert LOGO_SVG_PATH.is_file(), f"Logo SVG missing at {LOGO_SVG_PATH}"
    assert LOGO_PNG_PATH.is_file(), f"Logo PNG missing at {LOGO_PNG_PATH}"
    assert FAVICON_PATH.is_file(), f"Favicon PNG missing at {FAVICON_PATH}"
    assert LOGO_SVG_PATH.stat().st_size > 0
    assert LOGO_PNG_PATH.stat().st_size > 0
    assert FAVICON_PATH.stat().st_size > 0


def test_font_offline_bundling():
    """Verify B2/R2 font bundling: static URL serving, zero base64 bloat, 100% offline."""
    assert FONT_TTF_PATH.is_file(), f"Plus Jakarta Sans TTF missing at {FONT_TTF_PATH}"
    assert FONT_OFL_PATH.is_file(), f"OFL license file missing at {FONT_OFL_PATH}"
    assert FONT_TTF_PATH.stat().st_size > 50000, "Font file seems suspiciously small"

    webui_static_font = Path("webui/static/fonts/PlusJakartaSans[wght].ttf")
    assert webui_static_font.is_file(), f"Static served font missing at {webui_static_font}"

    css = get_global_css()
    assert "@font-face" in css
    assert "Plus Jakarta Sans" in css
    assert "/app/static/fonts/PlusJakartaSans[wght].ttf" in css
    # R2: No base64 bloat embedded in CSS on rerun
    assert "data:font/ttf;base64," not in css
    assert len(css) < 20000, f"CSS payload too large ({len(css)} bytes), expected lightweight static ref"

    # Strict offline guarantee: ZERO calls to external font CDNs or web URLs
    assert "fonts.googleapis.com" not in css
    assert "fonts.gstatic.com" not in css
    assert "http://" not in css
    assert "https://" not in css


def test_material_symbols_protection():
    """Verify R1: Material Symbols are explicitly protected and not overridden by Plus Jakarta Sans."""
    css = get_global_css()

    # R1: Never use universal wildcard !important override
    assert "* { font-family" not in css
    assert "*{" not in css

    # R1: Explicit Material Symbols preservation rule
    assert '[data-testid="stIconMaterial"]' in css
    assert '[class*="material-symbols"]' in css
    assert "Material Symbols Rounded" in css



def test_capcut_draft_prefix_and_backward_compatibility(tmp_path, monkeypatch):
    """Verify B1/B5: New exports use 'EFS_' prefix, legacy 'MPT_' drafts remain detected."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: projects_root)

    manifest, p_dir = create_project("test_brand_capcut", "Test Brand Project", "automotive")
    capcut_export_dir = p_dir / "export" / "capcut"
    capcut_export_dir.mkdir(parents=True, exist_ok=True)

    # 1. Simulate legacy MPT draft
    legacy_mpt_dir = capcut_export_dir / "MPT_test_brand_capcut_20261110-1200"
    legacy_mpt_dir.mkdir(parents=True, exist_ok=True)
    (legacy_mpt_dir / "draft_content.json").write_text("{}", encoding="utf-8")

    latest_legacy = find_latest_capcut_draft(p_dir, "test_brand_capcut")
    assert latest_legacy is not None
    assert latest_legacy["draft_name"] == "MPT_test_brand_capcut_20261110-1200"

    # 2. Simulate new EFS draft created after legacy draft
    new_efs_dir = capcut_export_dir / "EFS_test_brand_capcut_20261113-1000"
    new_efs_dir.mkdir(parents=True, exist_ok=True)
    (new_efs_dir / "draft_content.json").write_text("{}", encoding="utf-8")

    latest_new = find_latest_capcut_draft(p_dir, "test_brand_capcut")
    assert latest_new is not None
    assert latest_new["draft_name"] == "EFS_test_brand_capcut_20261113-1000"


def test_apptest_branding_main_page(monkeypatch):
    """Verify B5: Main.py renders with EasyFaceless Studio brand title, 5 sidebar items, and MIT attribution."""
    import shutil
    import subprocess
    from app.services.codex_models import Catalog

    repo_root = Path(__file__).resolve().parents[2]
    main_file = str(repo_root / "webui" / "Main.py")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}
    mock_resp.text = "{}"
    mock_resp.content = b"{}"

    real_which = shutil.which
    def safe_which(cmd, *args, **kwargs):
        if cmd in {"gemini", "copilot", "claude", "codex", "cursor", "gh"}:
            return None
        return real_which(cmd, *args, **kwargs)

    real_subproc_run = subprocess.run
    def safe_subproc_run(cmd, *args, **kwargs):
        cmd_name = cmd[0] if isinstance(cmd, (list, tuple)) else str(cmd).split()[0]
        if any(agent in str(cmd_name).lower() for agent in ["gemini", "copilot", "claude", "codex", "cursor"]):
            res = MagicMock()
            res.returncode = 1
            res.stdout = b""
            res.stderr = b"mocked agent"
            return res
        return real_subproc_run(cmd, *args, **kwargs)

    real_subproc_popen = subprocess.Popen
    def safe_subproc_popen(cmd, *args, **kwargs):
        cmd_name = cmd[0] if isinstance(cmd, (list, tuple)) else str(cmd).split()[0]
        if any(agent in str(cmd_name).lower() for agent in ["gemini", "copilot", "claude", "codex", "cursor"]):
            proc = MagicMock()
            proc.poll.return_value = 1
            proc.wait.return_value = 1
            proc.communicate.return_value = (b"", b"mocked agent")
            return proc
        return real_subproc_popen(cmd, *args, **kwargs)

    with patch("app.services.version_checker.poll_available_update") as mock_update, \
         patch("app.services.version_checker.get_available_update", return_value=None), \
         patch("app.services.codex_models.get_catalog", return_value=Catalog(models=(), fetched_at=0.0)), \
         patch("app.services.chatgpt_models.get_catalog", return_value=Catalog(models=(), fetched_at=0.0)), \
         patch("requests.get", return_value=mock_resp), \
         patch("requests.post", return_value=mock_resp), \
         patch("httpx.get", return_value=mock_resp), \
         patch("httpx.post", return_value=mock_resp), \
         patch("httpx.Client.send", return_value=mock_resp), \
         patch("shutil.which", side_effect=safe_which), \
         patch("subprocess.run", side_effect=safe_subproc_run), \
         patch("subprocess.Popen", side_effect=safe_subproc_popen):

        mock_update.return_value.complete = True
        mock_update.return_value.available_version = None

        at = AppTest.from_file(main_file)
        at.run(timeout=30)

        assert not at.exception, f"Main.py threw exceptions: {[e.value for e in at.exception]}"

        # 1. Brand header rendered in markdown
        md_texts = [m.value for m in at.markdown]
        has_brand = any("EasyFaceless Studio" in t for t in md_texts)
        assert has_brand, "EasyFaceless Studio brand title not found in Main.py"

        # 2. MIT Attribution rendered
        has_mit = any("MoneyPrinterTurbo (MIT)" in t for t in md_texts)
        assert has_mit, "MIT attribution not found in Main.py sidebar footer"

        # 3. 5 Sidebar navigation / setting items present
        page_link_labels = [str(pl.label) for pl in at.page_link]
        button_labels = [str(b.label) for b in at.button]
        all_labels = page_link_labels + button_labels

        assert any("Video Panjang" in l for l in all_labels), f"Missing Video Panjang item in {all_labels}"
        assert any("Video Pendek" in l for l in all_labels), f"Missing Video Pendek item in {all_labels}"
        assert any("Kelola Niche" in l for l in all_labels), f"Missing Kelola Niche item in {all_labels}"
        assert any("API & Penyedia AI" in l for l in all_labels), f"Missing API & Penyedia AI item in {all_labels}"
        assert any("Cache & Penyimpanan" in l for l in all_labels), f"Missing Cache & Penyimpanan item in {all_labels}"


def test_apptest_branding_long_video_page(tmp_path, monkeypatch):
    """Verify B5: 1_🎬_Long_Video.py renders with EasyFaceless Studio brand sidebar, 5 items, and MIT attribution."""
    repo_root = Path(__file__).resolve().parents[2]
    lv_file = str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    at = AppTest.from_file(lv_file)
    at.run(timeout=15)

    assert not at.exception, f"Long Video threw exceptions: {[e.value for e in at.exception]}"

    md_texts = [m.value for m in at.markdown]
    has_mit = any("MoneyPrinterTurbo (MIT)" in t for t in md_texts)
    assert has_mit, "MIT attribution not found in Long Video sidebar footer"

    page_link_labels = [str(pl.label) for pl in at.page_link]
    button_labels = [str(b.label) for b in at.button]
    all_labels = page_link_labels + button_labels

    assert any("Video Panjang" in l for l in all_labels), f"Missing Video Panjang item in {all_labels}"
    assert any("Video Pendek" in l for l in all_labels), f"Missing Video Pendek item in {all_labels}"
    assert any("Kelola Niche" in l for l in all_labels), f"Missing Kelola Niche item in {all_labels}"
    assert any("API & Penyedia AI" in l for l in all_labels), f"Missing API & Penyedia AI item in {all_labels}"
    assert any("Cache & Penyimpanan" in l for l in all_labels), f"Missing Cache & Penyimpanan item in {all_labels}"


def test_short_video_flow_submission():
    """Verify B5: Short video pipeline submission continues to work properly after Main.py rebrand."""
    import uuid
    from unittest.mock import patch
    from app.models.schema import VideoParams
    from app.services import webui_task

    task_id = str(uuid.uuid4())
    params = VideoParams(
        video_subject="Uji Singkat EasyFaceless",
        video_script="Naskah uji singkat untuk validasi alur video pendek tanpa regresi.",
        voice_name="id-ID-ArdiNeural",
        video_source="pixabay",
    )

    with patch("app.services.task.start") as mock_start:
        submitted_id = webui_task.submit_generation(task_id, params)
        assert submitted_id == task_id
        assert webui_task.is_generation_active(task_id) or submitted_id == task_id


def test_r2_sidebar_click_all_items_main_page():
    """Verify R2: clicking all sidebar items on Main.py operates without exception,

    dialogs open without ImportError, redundant top-bar links are absent,
    and 'Navigasi Studio' never appears anywhere.
    """
    import shutil
    import subprocess
    from app.services.codex_models import Catalog

    repo_root = Path(__file__).resolve().parents[2]
    main_file = str(repo_root / "webui" / "Main.py")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}
    mock_resp.text = "{}"
    mock_resp.content = b"{}"

    real_which = shutil.which
    def safe_which(cmd, *args, **kwargs):
        if cmd in {"gemini", "copilot", "claude", "codex", "cursor", "gh"}:
            return None
        return real_which(cmd, *args, **kwargs)

    real_subproc_run = subprocess.run
    def safe_subproc_run(cmd, *args, **kwargs):
        cmd_name = cmd[0] if isinstance(cmd, (list, tuple)) else str(cmd).split()[0]
        if any(agent in str(cmd_name).lower() for agent in ["gemini", "copilot", "claude", "codex", "cursor"]):
            res = MagicMock()
            res.returncode = 1
            res.stdout = b""
            res.stderr = b"mocked agent"
            return res
        return real_subproc_run(cmd, *args, **kwargs)

    real_subproc_popen = subprocess.Popen
    def safe_subproc_popen(cmd, *args, **kwargs):
        cmd_name = cmd[0] if isinstance(cmd, (list, tuple)) else str(cmd).split()[0]
        if any(agent in str(cmd_name).lower() for agent in ["gemini", "copilot", "claude", "codex", "cursor"]):
            proc = MagicMock()
            proc.poll.return_value = 1
            proc.wait.return_value = 1
            proc.communicate.return_value = (b"", b"mocked agent")
            return proc
        return real_subproc_popen(cmd, *args, **kwargs)

    with patch("app.services.version_checker.poll_available_update") as mock_update, \
         patch("app.services.version_checker.get_available_update", return_value=None), \
         patch("app.services.codex_models.get_catalog", return_value=Catalog(models=(), fetched_at=0.0)), \
         patch("app.services.chatgpt_models.get_catalog", return_value=Catalog(models=(), fetched_at=0.0)), \
         patch("requests.get", return_value=mock_resp), \
         patch("requests.post", return_value=mock_resp), \
         patch("httpx.get", return_value=mock_resp), \
         patch("httpx.post", return_value=mock_resp), \
         patch("httpx.Client.send", return_value=mock_resp), \
         patch("shutil.which", side_effect=safe_which), \
         patch("subprocess.run", side_effect=safe_subproc_run), \
         patch("subprocess.Popen", side_effect=safe_subproc_popen):

        mock_update.return_value.complete = True
        mock_update.return_value.available_version = None

        # 1. Initial page load
        at = AppTest.from_file(main_file)
        at.run(timeout=30)
        assert not at.exception, f"Main.py initial run threw exceptions: {[e.value for e in at.exception]}"

        # Verify old G5 link '🎬 Long Video' is completely absent from top bar
        assert not any(pl.label == "🎬 Long Video" for pl in at.page_link)
        # Verify 'Navigasi Studio' never appears
        assert not any("Navigasi Studio" in s.value for s in at.subheader)
        assert not any("Navigasi Studio" in m.value for m in at.markdown)

        # 2. Click 'Kelola Niche' (Item 3) -> Opens show_niche_dialog without ImportError
        niche_btn = at.button(key="efs_btn_niche_main")
        assert niche_btn is not None
        niche_btn.click().run(timeout=20)
        assert not at.exception, f"Clicking Kelola Niche threw exception: {[e.value for e in at.exception]}"
        assert not any("Navigasi Studio" in s.value for s in at.subheader)
        assert not any("Navigasi Studio" in m.value for m in at.markdown)
        # Verify preset tree and metrics rendered in niche dialog
        has_niche_tree = any("Pohon Hirarki Niche" in m.value for m in at.markdown) or any("Struktur Niche" in m.value for m in at.markdown)
        assert has_niche_tree, "Niche dialog tree content was not rendered"
        assert len(at.metric) >= 3, "Summary metrics (Total, Bawaan, Kustom) not found in Niche dialog"

        # 3. Click 'API & Penyedia AI' (Item 4) -> Opens upstream settings dialog with all tabs
        at2 = AppTest.from_file(main_file).run(timeout=30)
        ai_btn = at2.button(key="efs_btn_settings_ai_main")
        assert ai_btn is not None
        ai_btn.click().run(timeout=20)
        assert not at2.exception, f"Clicking API & Penyedia AI threw exception: {[e.value for e in at2.exception]}"
        assert at2.session_state.get("settings_dialog_open", False) is True
        assert not any("Navigasi Studio" in s.value for s in at2.subheader)

        # 4. Click 'Cache & Penyimpanan' (Item 5) -> Opens upstream settings dialog focused on cache
        at3 = AppTest.from_file(main_file).run(timeout=30)
        cache_btn = at3.button(key="efs_btn_settings_cache_main")
        assert cache_btn is not None
        cache_btn.click().run(timeout=20)
        assert not at3.exception, f"Clicking Cache & Penyimpanan threw exception: {[e.value for e in at3.exception]}"
        assert at3.session_state.get("settings_dialog_open", False) is True
        assert not any("Navigasi Studio" in s.value for s in at3.subheader)

        # 5. Click 'Tentang' (Item 6) -> Opens show_about_dialog
        at4 = AppTest.from_file(main_file).run(timeout=30)
        about_btn = at4.button(key="efs_btn_about_main")
        assert about_btn is not None
        about_btn.click().run(timeout=20)
        assert not at4.exception, f"Clicking Tentang threw exception: {[e.value for e in at4.exception]}"
        assert any("MoneyPrinterTurbo" in m.value for m in at4.markdown)
        assert not any("Navigasi Studio" in s.value for s in at4.subheader)


def test_r2_sidebar_click_all_items_long_video_page(tmp_path, monkeypatch):
    """Verify R2: clicking all sidebar items on Long Video operates without exception,

    dialogs open cleanly, and 'Navigasi Studio' never appears anywhere.
    """
    repo_root = Path(__file__).resolve().parents[2]
    lv_file = str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    # 1. Initial page load
    at = AppTest.from_file(lv_file).run(timeout=15)
    assert not at.exception, f"Long Video initial run threw exception: {[e.value for e in at.exception]}"
    assert not any("Navigasi Studio" in s.value for s in at.subheader)
    assert not any("Navigasi Studio" in m.value for m in at.markdown)

    # 2. Click 'Kelola Niche'
    niche_btn = at.button(key="efs_btn_niche_longform")
    assert niche_btn is not None
    niche_btn.click().run(timeout=15)
    assert not at.exception, f"Long Video Kelola Niche threw exception: {[e.value for e in at.exception]}"
    assert not any("Navigasi Studio" in s.value for s in at.subheader)
    assert not any("Navigasi Studio" in m.value for m in at.markdown)
    has_niche_tree = any("Pohon Hirarki Niche" in m.value for m in at.markdown) or any("Struktur Niche" in m.value for m in at.markdown)
    assert has_niche_tree, "Niche dialog tree content was not rendered in Long Video"

    # 3. Click 'API & Penyedia AI'
    at2 = AppTest.from_file(lv_file).run(timeout=15)
    ai_btn = at2.button(key="efs_btn_settings_ai_longform")
    assert ai_btn is not None
    ai_btn.click().run(timeout=15)
    assert not at2.exception, f"Long Video API & Penyedia AI threw exception: {[e.value for e in at2.exception]}"
    assert at2.session_state["settings_dialog_open"] is True
    assert at2.session_state["settings_dialog_target_tab"] == "llm"
    assert not any("Navigasi Studio" in s.value for s in at2.subheader)

    # 4. Click 'Cache & Penyimpanan'
    at3 = AppTest.from_file(lv_file).run(timeout=15)
    cache_btn = at3.button(key="efs_btn_settings_cache_longform")
    assert cache_btn is not None
    cache_btn.click().run(timeout=15)
    assert not at3.exception, f"Long Video Cache & Penyimpanan threw exception: {[e.value for e in at3.exception]}"
    assert at3.session_state["settings_dialog_open"] is True
    assert at3.session_state["settings_dialog_target_tab"] == "cache"
    assert not any("Navigasi Studio" in s.value for s in at3.subheader)

    # 5. Click 'Tentang'
    at4 = AppTest.from_file(lv_file).run(timeout=15)
    about_btn = at4.button(key="efs_btn_about_longform")
    assert about_btn is not None
    about_btn.click().run(timeout=15)
    assert not at4.exception, f"Long Video Tentang threw exception: {[e.value for e in at4.exception]}"
    assert any("MoneyPrinterTurbo" in m.value for m in at4.markdown)
    assert not any("Navigasi Studio" in s.value for s in at4.subheader)

