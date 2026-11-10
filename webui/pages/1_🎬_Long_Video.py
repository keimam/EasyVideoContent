"""Longform Video Studio - MoneyPrinterTurbo WebUI Page (Stage 7A).

Provides structured 10-20 minute longform video project management,
AI/Markdown script generation, and voice synthesis orchestration.
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

from app.services.longform.ui import components
from app.services.longform.ui.i18n import t

st.set_page_config(
    page_title="Video Panjang (Longform) - MoneyPrinterTurbo",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="auto",
)

st.title(f"🎬 {t('page_title')}")
st.caption(t("page_subtitle"))

# Session state initialization for selected project
if "selected_longform_project" not in st.session_state:
    st.session_state["selected_longform_project"] = None

selected_pid = st.session_state["selected_longform_project"]

tab_proj, tab_script, tab_voice = st.tabs([
    t("tab_projects"),
    t("tab_script"),
    t("tab_voice"),
])

with tab_proj:
    new_selected = components.render_projects_tab(selected_pid)
    if new_selected != selected_pid:
        st.session_state["selected_longform_project"] = new_selected

with tab_script:
    components.render_script_tab(st.session_state["selected_longform_project"])

with tab_voice:
    components.render_voice_tab(st.session_state["selected_longform_project"])
