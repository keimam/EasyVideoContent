"""Longform Video Studio - EasyFaceless Studio WebUI Page (Stage 7A.1, 8A.1).

Provides structured 10-20 minute longform video project management,
AI/Markdown script generation, voice synthesis orchestration,
and step-by-step workflow navigation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure repository root is on sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

import streamlit as st

try:
    from app.services.longform import app_paths, launcher_runtime, update_checker
    app_paths.bootstrap_runtime_environment()
    launcher_runtime.ensure_app_mutex()
    update_checker.log_startup_update_env_once()
except Exception:
    pass

try:
    from app.services.branding.language import ensure_ui_language_initialized
    ensure_ui_language_initialized()
except Exception:
    pass

from app.services.longform.ui import components
from app.services.longform.ui.i18n import t

try:
    from app.services.branding.constants import APP_NAME, FAVICON_PATH
    from app.services.branding.theme import inject_brand_theme
    _page_title = f"{APP_NAME} — {t('nav_long_video')}"
    _page_icon = str(FAVICON_PATH) if FAVICON_PATH.is_file() else "🎬"
except Exception:
    _page_title = t("nav_long_video")
    _page_icon = "🎬"

st.set_page_config(
    page_title=_page_title,
    page_icon=_page_icon,
    layout="wide",
    initial_sidebar_state="auto",
)

try:
    inject_brand_theme()
except Exception:
    pass

_wizard_active = False
_help_or_about_req = ""
try:
    from app.services.branding.lifecycle_ui import is_wizard_active, render_studio_lifecycle_ui
    from app.services.branding.sidebar import (
        is_help_or_about_query_request,
        maybe_open_sidebar_dialog_from_query_params,
        render_brand_sidebar,
    )

    _wizard_active = is_wizard_active()
    _help_or_about_req = is_help_or_about_query_request()
except Exception:
    pass

if _wizard_active:
    if not _help_or_about_req:
        try:
            st.switch_page("Main.py")
        except Exception:
            pass
    render_brand_sidebar(current_page="wizard")
    maybe_open_sidebar_dialog_from_query_params()
    render_studio_lifecycle_ui(key_prefix="home")
else:
    try:
        maybe_open_sidebar_dialog_from_query_params()
    except Exception:
        pass

    # B3/C1: Render native Settings dialog right on Long Video via cached hook without switching pages
    if st.session_state.get("settings_dialog_open", False):
        try:
            from app.services.branding.hooks import open_embedded_settings_dialog
            open_embedded_settings_dialog()
        except Exception as _exc:
            st.error(t("err_open_settings_dialog", err=str(_exc)))

    # Read query parameter for persistent project selection across refreshes (C4.5, A1.4)
    query_pid = st.query_params.get("project")
    if isinstance(query_pid, list):
        query_pid = query_pid[0] if query_pid else None
    query_pid = str(query_pid).strip() if query_pid else None

    if query_pid:
        st.session_state["efs_active_project_id"] = query_pid
        components.render_project_page(query_pid)
    else:
        st.session_state.pop("efs_active_project_id", None)
        components.render_home_page()
