"""Longform Video Studio - MoneyPrinterTurbo WebUI Page (Stage 7A.1).

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

from app.services.longform.ui import components

st.set_page_config(
    page_title="Studio Video Panjang - MoneyPrinterTurbo",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="auto",
)

# Read query parameter for persistent project selection across refreshes (C4.5)
query_pid = st.query_params.get("project")

if query_pid:
    components.render_project_page(query_pid)
else:
    components.render_home_page()
