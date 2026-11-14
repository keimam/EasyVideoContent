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
