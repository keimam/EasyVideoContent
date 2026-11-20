"""Isolated EasyFaceless Studio hook helpers invoked from minimal try/except hooks in webui/Main.py (C1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from loguru import logger
import streamlit as st

from app.services.branding.constants import APP_NAME, FAVICON_PATH, MIT_THANKS, UPSTREAM_REPO_URL


def init_main_page(styles_css_path: Path) -> None:
    """Configure Streamlit page meta, inject branding CSS, and render sidebar on Short Video main page."""
    if st.session_state.get("_efs_settings_library_only", False):
        return

    st.session_state["_efs_dialog_opened_this_run"] = False

    try:
        from app.services.longform import app_paths, update_checker
        app_paths.bootstrap_runtime_environment()
        update_checker.log_startup_update_env_once()
    except Exception as exc:
        logger.warning(f"Runtime environment bootstrap warning: {exc}")

    try:
        from app.services.branding.language import ensure_ui_language_initialized
        ensure_ui_language_initialized()
    except Exception as exc:
        logger.debug(f"UI language init warning: {exc}")

    try:
        from app.services.longform.ui.i18n import t
        short_title = t("nav_short_video")
    except Exception:
        short_title = "Video Pendek"

    wizard_active = False
    try:
        from app.services.branding.lifecycle_ui import is_wizard_active
        wizard_active = is_wizard_active()
    except Exception:
        wizard_active = False

    page_icon = str(FAVICON_PATH) if FAVICON_PATH.is_file() else "🎬"
    about_content = f"# {APP_NAME}\n\n{MIT_THANKS}\n\n{UPSTREAM_REPO_URL}"
    st.set_page_config(
        page_title=APP_NAME if wizard_active else f"{APP_NAME} — {short_title}",
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
    from app.services.branding.sidebar import (
        maybe_open_sidebar_dialog_from_query_params,
        render_brand_sidebar,
    )

    inject_brand_theme()
    try:
        render_brand_sidebar(current_page="wizard" if wizard_active else "main")
        maybe_open_sidebar_dialog_from_query_params()
    except Exception as exc:
        logger.exception(f"Brand sidebar rendering failed: {exc}")

    try:
        from app.services.branding.lifecycle_ui import render_studio_lifecycle_ui
        render_studio_lifecycle_ui(key_prefix="main")
    except Exception as exc:
        logger.exception(f"Studio lifecycle/wizard UI failed: {exc}")
        st.warning(f"Setup/update panel failed to load: {exc}")


_EMBEDDED_SETTINGS_RUNNER: Callable[[], Any] | None = None


def run_main_or_settings(
    render_application_fn: Callable[[], Any],
    render_settings_dialog_fn: Callable[[], Any],
    init_session_state_fn: Callable[[], Any],
    locales: Dict[str, Any],
    save_runtime_config_fn: Callable[[], Any],
) -> None:
    """Run either standalone Wizard, Short Video page, or embedded Settings dialog (B3, C1, E9)."""
    global _EMBEDDED_SETTINGS_RUNNER
    from app.services.branding.language import ensure_ui_language_initialized
    from app.services.branding.lifecycle_ui import is_wizard_active

    def _run_embedded() -> None:
        ensure_ui_language_initialized(supported_codes=locales.keys())
        init_session_state_fn()
        render_settings_dialog_fn()
        save_runtime_config_fn()

    _EMBEDDED_SETTINGS_RUNNER = _run_embedded

    if st.session_state.get("_efs_settings_library_only", False):
        _run_embedded()
        return

    ensure_ui_language_initialized(supported_codes=locales.keys())
    if is_wizard_active():
        # E9.2: Wizard owns the viewport cleanly; do not render Short Video underneath it.
        return

    if "open_settings" in st.query_params:
        target = st.query_params.pop("open_settings")
        st.session_state["settings_dialog_open"] = True
        st.session_state["settings_dialog_target_tab"] = target

    render_application_fn()


def render_top_language_selector(locales: Dict[str, Any]) -> None:
    """Render MPT top-right language selector backed by the single canonical E9.1 callback."""
    from app.services.branding.language import (
        ensure_ui_language_initialized,
        on_language_widget_change,
        resolve_mpt_locale_code,
    )

    language_codes = list(locales.keys())
    ensure_ui_language_initialized(supported_codes=language_codes)
    cur_code = resolve_mpt_locale_code(
        st.session_state.get("ui_language", ""),
        supported_codes=language_codes,
        default="id",
    )
    if st.session_state.get("top_language_code_selector") not in language_codes:
        st.session_state["top_language_code_selector"] = cur_code

    st.selectbox(
        "Language / 语言",
        options=language_codes,
        format_func=lambda code: locales[code].get("Language", code),
        key="top_language_code_selector",
        label_visibility="collapsed",
        width=180,
        on_change=on_language_widget_change,
        args=("top_language_code_selector", language_codes),
    )


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


def run_short_video_onboarding_tour(tour_cls: Any) -> bool:
    """Run localized Short Video onboarding tour only when wizard is finished and no dialog is open (E8)."""
    from app.services.branding.lifecycle_ui import run_short_video_onboarding_tour as _run_tour

    return _run_tour(tour_cls)
