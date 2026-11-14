"""Unit tests for Long Video i18n localization system (8A.2 L4)."""

import pytest
from app.services.longform.ui.i18n import (
    ID_TRANSLATIONS,
    EN_TRANSLATIONS,
    get_ui_language,
    t,
)


def test_i18n_key_symmetry():
    """Verify that every single key in ID_TRANSLATIONS exists in EN_TRANSLATIONS and vice versa."""
    id_keys = set(ID_TRANSLATIONS.keys())
    en_keys = set(EN_TRANSLATIONS.keys())

    missing_in_en = id_keys - en_keys
    missing_in_id = en_keys - id_keys

    assert not missing_in_en, f"Keys present in ID but missing in EN: {missing_in_en}"
    assert not missing_in_id, f"Keys present in EN but missing in ID: {missing_in_id}"
    assert len(id_keys) > 100, f"Expected comprehensive dictionary, found {len(id_keys)} keys"


def test_i18n_formatting():
    """Verify that parameter formatting works in both languages."""
    msg_id = t("project_next_step", lang="id", step="Visual")
    assert msg_id == "Berikutnya: Visual"

    msg_en = t("project_next_step", lang="en", step="Visual")
    assert msg_en == "Next: Visual"

    prog_id = t("project_step_progress", lang="id", completed=3)
    assert prog_id == "3/5 langkah"

    prog_en = t("project_step_progress", lang="en", completed=3)
    assert prog_en == "3/5 steps"


def test_i18n_fallback_behavior(monkeypatch):
    """Verify language resolution and fallback: 'zh' or unsupported falls back to 'en'."""
    # Test with monkeypatched session state
    class FakeState(dict):
        pass

    fake_session = FakeState()

    import streamlit as st
    monkeypatch.setattr(st, "session_state", fake_session)

    # 1. Default when session is empty (falls back to config or 'id')
    assert get_ui_language() in {"id", "en"}

    # 2. Indonesian session
    fake_session["ui_language"] = "id"
    assert get_ui_language() == "id"

    fake_session["ui_language"] = "id-ID"
    assert get_ui_language() == "id"

    # 3. English session
    fake_session["ui_language"] = "en"
    assert get_ui_language() == "en"

    fake_session["ui_language"] = "en-US"
    assert get_ui_language() == "en"

    # 4. Chinese session -> Must fallback to 'en' (L4 requirement)
    fake_session["ui_language"] = "zh"
    assert get_ui_language() == "en"

    # 5. Other languages (es, fr, de, etc.) -> Fallback to 'en'
    fake_session["ui_language"] = "de"
    assert get_ui_language() == "en"


def test_brand_names_not_translated():
    """Verify L4 rule: Brand names 'EasyFaceless Studio' and 'MoneyPrinterTurbo' are not translated."""
    for lang in ["id", "en"]:
        about_title = t("about_dialog_title", lang=lang)
        assert "EasyFaceless Studio" in about_title

        built_on = t("nav_about_help", lang=lang)
        assert "MoneyPrinterTurbo" in built_on


def test_i18n_codebase_scanner():
    """Verify V1: Every single t('...') key called anywhere in code exists in both ID and EN dictionaries."""
    import re
    from pathlib import Path

    key_pattern = re.compile(r'\bt\(\s*[\"\']([a-zA-Z0-9_]+)[\"\']')
    repo_root = Path(__file__).resolve().parents[2]

    files = (
        list((repo_root / "app" / "services" / "longform" / "ui").rglob("*.py"))
        + list((repo_root / "app" / "services" / "branding").rglob("*.py"))
        + list((repo_root / "webui" / "pages").rglob("*.py"))
    )

    missing_in_id = set()
    missing_in_en = set()

    for f in files:
        content = f.read_text(encoding="utf-8")
        for m in key_pattern.finditer(content):
            k = m.group(1)
            if k not in ID_TRANSLATIONS:
                missing_in_id.add((k, f.name))
            if k not in EN_TRANSLATIONS:
                missing_in_en.add((k, f.name))

    assert not missing_in_id, f"Found t(...) keys in code missing in ID_TRANSLATIONS: {missing_in_id}"
    assert not missing_in_en, f"Found t(...) keys in code missing in EN_TRANSLATIONS: {missing_in_en}"


def test_no_raw_i18n_keys_rendered_in_apptest(tmp_path, monkeypatch):
    """Verify V1: No raw i18n keys (e.g. gallery_heading, unit_minutes) leak into rendered UI."""
    from streamlit.testing.v1 import AppTest
    from pathlib import Path
    from app.services.longform.store import create_project

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    # 1. Check Home page
    repo_root = Path(__file__).resolve().parents[2]
    app_file = str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")

    create_project("test_proj_p1", "Test Project Title", "automotive.muscle_car", projects_root=tmp_path)

    at = AppTest.from_file(app_file)
    at.run(timeout=15)
    assert not at.exception

    all_ui_text = " ".join([m.value for m in at.markdown] + [b.label for b in at.button if b.label] + [t.value for t in at.title])
    known_leaks = ["gallery_heading", "all_chapters_option", "filter_needs_review", "filter_locked", "btn_auto_run_until_done", "unit_minutes"]
    for leak in known_leaks:
        assert leak not in all_ui_text, f"Raw i18n key '{leak}' was rendered on the Home page!"

    # 2. Check Project page
    at_proj = AppTest.from_file(app_file)
    at_proj.query_params["project"] = "test_proj_p1"
    at_proj.run(timeout=15)
    assert not at_proj.exception

    all_proj_text = " ".join([m.value for m in at_proj.markdown] + [b.label for b in at_proj.button if b.label] + [t.value for t in at_proj.title])
    for leak in known_leaks:
        assert leak not in all_proj_text, f"Raw i18n key '{leak}' was rendered on the Project page!"

