"""Global theme, typography, and visual style helpers for EasyFaceless Studio (B2)."""

from __future__ import annotations

import base64
import html
import functools
from pathlib import Path
from typing import Optional

import streamlit as st

from app.services.branding.constants import (
    APP_NAME,
    FONT_TTF_PATH,
    PALETTE,
    RADII,
    CONTROL_MIN_HEIGHT,
    VERSION_STRING,
    UPSTREAM_UPDATE_PREFIX,
    UPSTREAM_REPO_URL,
)


@functools.lru_cache(maxsize=1)
def get_font_base64() -> str:
    """Read local Plus Jakarta Sans font and return as base64 string."""
    if FONT_TTF_PATH.is_file():
        data = FONT_TTF_PATH.read_bytes()
        return base64.b64encode(data).decode("ascii")
    return ""


def get_global_css() -> str:
    """Generate self-contained, offline CSS stylesheet for EasyFaceless Studio."""
    font_b64 = get_font_base64()
    font_face_rule = ""
    if font_b64:
        font_face_rule = f"""
        @font-face {{
            font-family: 'Plus Jakarta Sans';
            src: url('data:font/ttf;base64,{font_b64}') format('truetype');
            font-weight: 200 800;
            font-style: normal;
            font-display: swap;
        }}
        """

    css = f"""
    <style id="efs-brand-theme">
    {font_face_rule}

    /* --- Root Design Tokens (B2) --- */
    :root {{
        --efs-bg: {PALETTE["bg"]};
        --efs-sidebar: {PALETTE["sidebar"]};
        --efs-surface: {PALETTE["surface"]};
        --efs-surface-2: {PALETTE["surface_2"]};
        --efs-inset: {PALETTE["inset"]};
        --efs-border: {PALETTE["border"]};
        --efs-border-strong: {PALETTE["border_strong"]};
        --efs-text: {PALETTE["text"]};
        --efs-text-2: {PALETTE["text_2"]};
        --efs-muted: {PALETTE["muted"]};
        --efs-dim: {PALETTE["dim"]};
        --efs-accent: {PALETTE["accent"]};
        --efs-text-on-accent: {PALETTE["text_on_accent"]};
        --efs-success: {PALETTE["success"]};
        --efs-warning: {PALETTE["warning"]};
        --efs-danger-text: {PALETTE["danger_text"]};
        --efs-danger-border: {PALETTE["danger_border"]};
        --efs-nav-active: {PALETTE["nav_active"]};
        --efs-radius-card: {RADII["card"]};
        --efs-radius-input: {RADII["input"]};
        --efs-radius-button: {RADII["button"]};
        --efs-radius-chip: {RADII["chip"]};
        --efs-control-height: {CONTROL_MIN_HEIGHT};
    }}

    /* Global Typography Override: 100% Offline Plus Jakarta Sans */
    html, body, [class*="css"], .stApp,
    div[data-testid="stAppViewContainer"],
    [data-testid="stSidebar"],
    input, textarea, select, button, p, h1, h2, h3, h4, h5, h6, span, label {{
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
    }}

    /* App Background & Surfaces */
    .stApp,
    div[data-testid="stAppViewContainer"],
    div[data-testid="stAppViewBlockContainer"] {{
        background-color: var(--efs-bg) !important;
        color: var(--efs-text) !important;
    }}

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {{
        background-color: var(--efs-sidebar) !important;
        border-right: 1px solid var(--efs-border) !important;
    }}

    section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] {{
        background-color: var(--efs-sidebar) !important;
    }}

    /* Keep Sidebar Toggle Button visible, hide built-in stSidebarNav */
    header[data-testid="stHeader"] {{
        display: block !important;
        background: transparent !important;
        height: auto !important;
        pointer-events: none !important;
    }}

    div[data-testid="stToolbar"] {{
        display: flex !important;
        background: transparent !important;
        height: auto !important;
        pointer-events: none !important;
    }}

    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapseButton"] {{
        visibility: visible !important;
        display: inline-flex !important;
        pointer-events: auto !important;
        z-index: 999999 !important;
    }}

    [data-testid="stSidebarNav"],
    [data-testid="stSidebarNavSeparator"],
    ul[data-testid="stSidebarNavItems"],
    div[data-testid="stDecoration"],
    div[data-testid="stStatusWidget"],
    div[data-testid="stSkillsNudgeAnchor"],
    div[data-testid="stSkillsNudge"] {{
        display: none !important;
    }}

    /* Controls: Buttons (min height 44px, radius 10px) */
    div.stButton > button {{
        min-height: var(--efs-control-height) !important;
        border-radius: var(--efs-radius-button) !important;
        font-weight: 600 !important;
        transition: all 0.15s ease-in-out !important;
    }}

    /* Primary Buttons */
    div.stButton > button[kind="primary"],
    div.stButton > button[data-testid="baseButton-primary"] {{
        background-color: var(--efs-accent) !important;
        color: var(--efs-text-on-accent) !important;
        border: 1px solid var(--efs-accent) !important;
    }}
    div.stButton > button[kind="primary"]:hover,
    div.stButton > button[data-testid="baseButton-primary"]:hover {{
        filter: brightness(1.1) !important;
        box-shadow: 0 4px 12px rgba(143, 166, 255, 0.25) !important;
    }}

    /* Secondary Buttons */
    div.stButton > button[kind="secondary"],
    div.stButton > button[data-testid="baseButton-secondary"] {{
        background-color: var(--efs-surface) !important;
        color: var(--efs-text) !important;
        border: 1px solid var(--efs-border) !important;
    }}
    div.stButton > button[kind="secondary"]:hover,
    div.stButton > button[data-testid="baseButton-secondary"]:hover {{
        background-color: var(--efs-surface-2) !important;
        border-color: var(--efs-border-strong) !important;
    }}

    /* Inputs & Selectboxes (min height 44px, radius 10px, inset bg) */
    div[data-baseweb="input"],
    div[data-baseweb="select"] > div,
    div[data-baseweb="base-input"] {{
        background-color: var(--efs-inset) !important;
        border-color: var(--efs-border) !important;
        border-radius: var(--efs-radius-input) !important;
        color: var(--efs-text) !important;
        min-height: var(--efs-control-height) !important;
    }}

    div[data-baseweb="input"]:focus-within,
    div[data-baseweb="select"]:focus-within {{
        border-color: var(--efs-accent) !important;
        box-shadow: 0 0 0 1px var(--efs-accent) !important;
    }}

    input.st-am, textarea.st-am, input, textarea {{
        color: var(--efs-text) !important;
    }}

    /* Containers & Cards (radius 16px) */
    div[data-testid="stExpander"] {{
        background-color: var(--efs-surface) !important;
        border: 1px solid var(--efs-border) !important;
        border-radius: var(--efs-radius-card) !important;
    }}

    /* Brand Header on Main.py (B1) */
    .efs-main-brand {{
        display: flex;
        align-items: center;
        gap: 12px;
        margin: 0;
        padding: 0;
        line-height: 1.2;
    }}

    .efs-main-brand__logo {{
        width: 38px;
        height: 38px;
        border-radius: 9px;
        flex-shrink: 0;
    }}

    .efs-main-brand__name {{
        font-size: 1.55rem;
        font-weight: 700;
        color: var(--efs-text);
        letter-spacing: -0.02em;
    }}

    .efs-main-brand__badge {{
        display: inline-flex;
        align-items: center;
        padding: 3px 9px;
        border-radius: var(--efs-radius-chip);
        background-color: var(--efs-surface-2);
        border: 1px solid var(--efs-border);
        color: var(--efs-text-2);
        font-size: 0.78rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        text-decoration: none !important;
    }}

    .efs-main-brand__update {{
        display: inline-flex;
        align-items: center;
        padding: 3px 10px;
        border-radius: var(--efs-radius-chip);
        background-color: rgba(245, 181, 68, 0.15);
        border: 1px solid var(--efs-warning);
        color: var(--efs-warning);
        font-size: 0.78rem;
        font-weight: 600;
        text-decoration: none !important;
        transition: all 0.15s ease;
    }}
    .efs-main-brand__update:hover {{
        background-color: rgba(245, 181, 68, 0.25);
    }}

    /* Card & Tile Helpers for 8A.2 */
    .efs-card {{
        background-color: var(--efs-surface);
        border: 1px solid var(--efs-border);
        border-radius: var(--efs-radius-card);
        padding: 1.25rem;
        margin-bottom: 1rem;
    }}

    .efs-badge {{
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        border-radius: var(--efs-radius-chip);
        font-size: 0.75rem;
        font-weight: 600;
        line-height: 1.3;
    }}
    .efs-badge--neutral {{
        background-color: var(--efs-surface-2);
        color: var(--efs-text-2);
        border: 1px solid var(--efs-border);
    }}
    .efs-badge--accent {{
        background-color: rgba(143, 166, 255, 0.18);
        color: var(--efs-accent);
        border: 1px solid var(--efs-accent);
    }}
    .efs-badge--success {{
        background-color: rgba(74, 222, 155, 0.15);
        color: var(--efs-success);
        border: 1px solid var(--efs-success);
    }}
    .efs-badge--warning {{
        background-color: rgba(245, 181, 68, 0.15);
        color: var(--efs-warning);
        border: 1px solid var(--efs-warning);
    }}
    .efs-badge--danger {{
        background-color: rgba(255, 180, 168, 0.12);
        color: var(--efs-danger-text);
        border: 1px solid var(--efs-danger-border);
    }}

    .efs-metric-tile {{
        background-color: var(--efs-surface);
        border: 1px solid var(--efs-border);
        border-radius: var(--efs-radius-card);
        padding: 1rem;
        display: flex;
        flex-direction: column;
        gap: 4px;
    }}
    .efs-metric-tile__label {{
        font-size: 0.8rem;
        color: var(--efs-muted);
        font-weight: 500;
    }}
    .efs-metric-tile__value {{
        font-size: 1.5rem;
        color: var(--efs-text);
        font-weight: 700;
    }}
    .efs-metric-tile__hint {{
        font-size: 0.75rem;
        color: var(--efs-dim);
    }}
    </style>
    """
    return css.strip()


def inject_brand_theme():
    """Inject global brand theme CSS into current Streamlit page."""
    css = get_global_css()
    st.markdown(css, unsafe_allow_html=True)


def render_brand_header(available_update: Optional[str] = None):
    """Render top brand header for Main.py (B1)."""
    from app.services.branding.constants import get_logo_data_uri

    logo_uri = get_logo_data_uri()
    update_link_html = ""
    if available_update:
        label = html.escape(f"{UPSTREAM_UPDATE_PREFIX}v{available_update}")
        update_link_html = (
            f'<a class="efs-main-brand__update" href="{UPSTREAM_REPO_URL}" '
            f'target="_blank" rel="noopener noreferrer" title="{label}">{label}</a>'
        )

    badge_label = html.escape(VERSION_STRING)
    header_html = f"""
    <div class="efs-main-brand">
        <img class="efs-main-brand__logo" src="{logo_uri}" alt="{APP_NAME} Logo" />
        <span class="efs-main-brand__name">{APP_NAME}</span>
        <a class="efs-main-brand__badge" href="{UPSTREAM_REPO_URL}" target="_blank" rel="noopener noreferrer" title="Berdasarkan {UPSTREAM_REPO_URL}">{badge_label}</a>
        {update_link_html}
    </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)


def render_card(content_html: str, class_name: str = ""):
    """Render a styled surface card."""
    cls = f"efs-card {class_name}".strip()
    st.markdown(f'<div class="{cls}">{content_html}</div>', unsafe_allow_html=True)


def render_badge(text: str, variant: str = "neutral") -> str:
    """Return HTML snippet for a badge/chip."""
    safe_text = html.escape(text)
    return f'<span class="efs-badge efs-badge--{variant}">{safe_text}</span>'


def render_metric_tile(label: str, value: str, hint: str = ""):
    """Render a metric tile widget."""
    safe_label = html.escape(label)
    safe_value = html.escape(str(value))
    hint_html = f'<div class="efs-metric-tile__hint">{html.escape(hint)}</div>' if hint else ""
    st.markdown(
        f"""
        <div class="efs-metric-tile">
            <div class="efs-metric-tile__label">{safe_label}</div>
            <div class="efs-metric-tile__value">{safe_value}</div>
            {hint_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(title: str, subtitle: str = "", badge: str = ""):
    """Render a structured page title header."""
    badge_html = f' {render_badge(badge, "accent")}' if badge else ""
    st.markdown(
        f"""
        <div style="margin-bottom: 1.25rem;">
            <h1 style="font-size: 1.85rem; font-weight: 700; margin: 0 0 0.35rem 0; color: var(--efs-text); display: flex; align-items: center; gap: 8px;">
                {html.escape(title)}{badge_html}
            </h1>
            {f'<p style="font-size: 0.95rem; color: var(--efs-muted); margin: 0;">{html.escape(subtitle)}</p>' if subtitle else ""}
        </div>
        """,
        unsafe_allow_html=True,
    )
