"""Smoke and component tests for Long Video WebUI (Stage 7A, B7).

Uses streamlit.testing.v1.AppTest to verify:
- Clean render with empty storage (no projects)
- Clean render with active test project
- API key masking security (B6)
- Safe Windows path handling (B6)
"""

import os
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from app.services.longform.store import create_project, get_project_dir
from app.services.longform.ui.security import mask_api_key, safe_windows_path


def get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


def test_api_key_masking_security():
    """Verify B6 API key masking security."""
    assert mask_api_key(None) == "(belum diatur)"
    assert mask_api_key("") == "(belum diatur)"
    assert mask_api_key("12345") == "****"
    
    # Prefix-based keys
    pk_key = "pk_live_123456789abcdef0"
    masked_pk = mask_api_key(pk_key)
    assert masked_pk.startswith("pk_****")
    assert masked_pk.endswith("ef0")
    assert "123456789" not in masked_pk

    sk_key = "sk-proj-9876543210zyxwvu"
    masked_sk = mask_api_key(sk_key)
    assert masked_sk.startswith("sk-****")
    assert masked_sk.endswith("wvu")
    assert "9876543210" not in masked_sk

    aiza_key = "AIzaSyD1234567890abcdef"
    masked_aiza = mask_api_key(aiza_key)
    assert masked_aiza.startswith("AIz****")
    assert masked_aiza.endswith("cdef")
    assert "1234567890" not in masked_aiza


def test_safe_windows_path_handling():
    """Verify B6 safe Windows path normalization with spaces."""
    raw_path = r"C:\Users\IT SUPPORT 2\Desktop\test project"
    safe = safe_windows_path(raw_path)
    assert "IT SUPPORT 2" in safe
    assert isinstance(safe, str)


def test_webui_smoke_empty_storage(tmp_path, monkeypatch):
    """Smoke test: verify UI opens cleanly without exception when storage has 0 projects (B7)."""
    # Point projects root to empty tmp_path
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=10)

    assert not at.exception, f"AppTest threw exceptions: {[e.value for e in at.exception]}"
    assert len(at.tabs) == 3
    # Check that info message is displayed in empty storage
    info_messages = [inf.value for inf in at.info]
    assert any("Belum ada proyek" in msg for msg in info_messages)


def test_webui_smoke_with_test_project(tmp_path, monkeypatch):
    """Smoke test: verify UI opens cleanly without exception with an active project (B7)."""
    # Create test project inside tmp_path
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    
    test_pid = "smoke_test_project_7a"
    create_project(
        project_id=test_pid,
        title="Smoke Test Muscle Cars",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=10)

    assert not at.exception, f"AppTest threw exceptions: {[e.value for e in at.exception]}"
    assert len(at.tabs) == 3

    # Check selectbox contains the project
    proj_selectors = [sb for sb in at.selectbox if "Pilih Proyek Aktif" in (sb.label or "")]
    assert len(proj_selectors) == 1
    assert test_pid in proj_selectors[0].options
