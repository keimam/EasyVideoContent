"""Isolated EasyFaceless Studio hook helpers invoked from minimal try/except hooks in webui/Main.py (C1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

import streamlit as st

from app.services.branding.constants import APP_NAME, FAVICON_PATH, MIT_THANKS, UPSTREAM_REPO_URL


def init_main_page(styles_css_path: Path) -> None:
    """Configure Streamlit page meta, inject branding CSS, and render sidebar on Short Video main page."""
    if st.session_state.get("_efs_settings_library_only", False):
        return

    page_icon = str(FAVICON_PATH) if FAVICON_PATH.is_file() else "🎬"
    about_content = f"# {APP_NAME}\n\n{MIT_THANKS}\n\n{UPSTREAM_REPO_URL}"
    st.set_page_config(
        page_title=f"{APP_NAME} — Video Pendek",
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="auto",
        menu_items={
            "Report a bug": f"{UPSTREAM_REPO_URL}/issues",
            "About": about_content,
        },
    )

    if styles_css_path.is_file():
        st.markdown(f"<style>{styles_css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

    from app.services.branding.theme import inject_brand_theme
    from app.services.branding.sidebar import render_brand_sidebar
    from app.services.branding.lifecycle_ui import render_studio_lifecycle_ui

    inject_brand_theme()
    render_brand_sidebar(current_page="main")
    render_studio_lifecycle_ui(key_prefix="main")


_EMBEDDED_SETTINGS_RUNNER: Callable[[], Any] | None = None


def run_main_or_settings(
    render_application_fn: Callable[[], Any],
    render_settings_dialog_fn: Callable[[], Any],
    init_session_state_fn: Callable[[], Any],
    locales: Dict[str, Any],
    save_runtime_config_fn: Callable[[], Any],
) -> None:
    """Run either the full Short Video page or just the embedded Settings dialog for Long Video (B3, C1)."""
    global _EMBEDDED_SETTINGS_RUNNER

    def _run_embedded() -> None:
        init_session_state_fn()
        qp_lang = str(st.query_params.get("lang", "") or "").strip().lower()
        if qp_lang.startswith("id") and "id" in locales:
            st.session_state["ui_language"] = "id"
        elif qp_lang.startswith("en") and "en" in locales:
            st.session_state["ui_language"] = "en"
        render_settings_dialog_fn()
        save_runtime_config_fn()

    _EMBEDDED_SETTINGS_RUNNER = _run_embedded

    if st.session_state.get("_efs_settings_library_only", False):
        _run_embedded()
        return

    if "open_settings" in st.query_params:
        target = st.query_params.pop("open_settings")
        st.session_state["settings_dialog_open"] = True
        st.session_state["settings_dialog_target_tab"] = target

    render_application_fn()


def open_embedded_settings_dialog() -> None:
    """Open the cached embedded Settings dialog on Long Video without re-reloading webui.Main (B3, C1)."""
    if _EMBEDDED_SETTINGS_RUNNER is not None:
        _EMBEDDED_SETTINGS_RUNNER()
        return

    import importlib

    st.session_state["_efs_settings_library_only"] = True
    try:
        importlib.import_module("webui.Main")
    finally:
        st.session_state.pop("_efs_settings_library_only", None)


def render_settings_studio_controls() -> None:
    """Hook rendered inside Settings General tab for Setup Wizard re-run and Update checks (E1/E2)."""
    from app.services.branding.lifecycle_ui import render_settings_studio_controls as _render

    _render(key_prefix="general_settings")
