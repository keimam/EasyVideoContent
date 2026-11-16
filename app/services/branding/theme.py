"""Global theme, typography, and visual style helpers for EasyFaceless Studio (B2, R1, R2)."""

from __future__ import annotations

import html
from typing import Optional

import streamlit as st

from app.services.branding.constants import (
    APP_NAME,
    PALETTE,
    RADII,
    CONTROL_MIN_HEIGHT,
    VERSION_STRING,
    UPSTREAM_UPDATE_PREFIX,
    UPSTREAM_REPO_URL,
)


def get_global_css() -> str:
    """Generate self-contained, offline CSS stylesheet for EasyFaceless Studio (R1, R2).

    Uses static font serving at '/app/static/fonts/PlusJakartaSans[wght].ttf' to avoid
    re-transmitting heavy base64 strings on every Streamlit rerun.
    Explicitly protects Streamlit's bundled offline Material Symbols from being overridden.
    """
    css = f"""
    <style id="efs-brand-theme">
    /* --- Local Offline Font Definition (R2: Static URL serving) --- */
    @font-face {{
        font-family: 'Plus Jakarta Sans';
        src: local('Plus Jakarta Sans'),
             local('PlusJakartaSans'),
             url('/app/static/fonts/PlusJakartaSans[wght].ttf') format('truetype');
        font-weight: 200 800;
        font-style: normal;
        font-display: swap;
    }}

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

    /* --- Base Typography: Plus Jakarta Sans for UI text only (R1) --- */
    html, body, .stApp,
    div[data-testid="stAppViewContainer"],
    [data-testid="stSidebar"] {{
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
    }}

    /* Scope font specifically to text elements, never wildcard or override icons (R1) */
    h1, h2, h3, h4, h5, h6,
    p, label,
    textarea, select,
    .stMarkdown,
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stMarkdownContainer"] span:not([data-testid="stIconMaterial"]):not([class*="material-symbols"]),
    div.stButton > button p,
    div.stButton > button span:not([data-testid="stIconMaterial"]):not([class*="material-symbols"]),
    div.stPageLink a span:not([data-testid="stIconMaterial"]):not([class*="material-symbols"]),
    div.stPageLink p,
    .efs-main-brand__name,
    .efs-main-brand__badge,
    .efs-main-brand__update {{
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
    }}

    /* --- R1: Strictly preserve Streamlit's bundled offline Material Symbols --- */
    [data-testid="stIconMaterial"],
    [class*="material-symbols"],
    .material-symbols-rounded,
    .material-symbols-outlined,
    .material-symbols-sharp,
    i.material-icons,
    [data-testid="stExpandSidebarButton"] span,
    [data-testid="stSidebarCollapseButton"] span {{
        font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons', sans-serif !important;
        font-style: normal !important;
        font-weight: normal !important;
        font-variant: normal !important;
        text-transform: none !important;
        line-height: 1 !important;
        letter-spacing: normal !important;
        word-wrap: normal !important;
        white-space: nowrap !important;
        direction: ltr !important;
        -webkit-font-smoothing: antialiased !important;
        text-rendering: optimizeLegibility !important;
        -moz-osx-font-smoothing: grayscale !important;
        font-feature-settings: 'liga' !important;
    }}

    /* App Background & Surfaces */
    .stApp,
    div[data-testid="stAppViewContainer"],
    div[data-testid="stAppViewBlockContainer"] {{
        background-color: var(--efs-bg) !important;
        color: var(--efs-text) !important;
    }}

    /* Sidebar Base Styling */
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
    div[data-testid="stSkillsNudge"],
    div.st-key-open_settings_dialog_button,
    div[class*="st-key-open_settings_dialog_button"] {{
        display: none !important;
    }}

    /* Controls: Buttons (min height 44px, radius 10px) in main content */
    div[data-testid="stMainBlockContainer"] div.stButton > button {{
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
        filter: brightness(1.08) !important;
        box-shadow: 0 4px 12px rgba(143, 166, 255, 0.25) !important;
    }}

    /* Secondary Buttons */
    div[data-testid="stMainBlockContainer"] div.stButton > button[kind="secondary"],
    div[data-testid="stMainBlockContainer"] div.stButton > button[data-testid="baseButton-secondary"] {{
        background-color: var(--efs-surface) !important;
        color: var(--efs-text) !important;
        border: 1px solid var(--efs-border) !important;
    }}
    div[data-testid="stMainBlockContainer"] div.stButton > button[kind="secondary"]:hover,
    div[data-testid="stMainBlockContainer"] div.stButton > button[data-testid="baseButton-secondary"]:hover {{
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

    /* Containers & Cards (Uniform 10px radius, 16-24px consistent vertical margin, border-box) */
    [data-testid="stExpander"],
    div[data-testid="stExpander"],
    details[data-testid="stExpander"] {{
        background-color: var(--efs-surface) !important;
        border: 1px solid var(--efs-border) !important;
        border-radius: 10px !important;
        box-sizing: border-box !important;
        width: 100% !important;
        margin-top: 0 !important;
        margin-bottom: 4px !important; /* Total gap with Streamlit container flex gap (16px) = 20px (target: 16-24px) */
    }}

    [data-testid="stExpander"] summary {{
        border-radius: 10px !important;
        box-sizing: border-box !important;
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
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
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
        border-radius: 10px;
        padding: 0.75rem 1rem;
        display: flex;
        flex-direction: column;
        gap: 3px;
        min-height: 72px;
        justify-content: center;
        box-sizing: border-box;
    }}
    .efs-metric-tile__label {{
        font-size: 0.75rem;
        color: var(--efs-muted);
        font-weight: 500;
        letter-spacing: 0.02em;
    }}
    .efs-metric-tile__value {{
        font-size: 1.25rem;
        color: var(--efs-text);
        font-weight: 700;
        line-height: 1.25;
        white-space: normal !important;
        word-break: break-word !important;
        overflow-wrap: break-word !important;
    }}
    .efs-metric-tile__value--success {{
        color: var(--efs-success) !important;
    }}
    .efs-metric-tile__value--warning {{
        color: var(--efs-warning) !important;
    }}
    .efs-metric-tile__value--accent {{
        color: var(--efs-accent) !important;
    }}
    .efs-metric-tile__hint {{
        font-size: 0.72rem;
        color: var(--efs-dim);
    }}

    /* Hide fallback st.metric container immediately following an efs-metric-tile */
    div[data-testid="stElementContainer"]:has(.efs-metric-tile) + div[data-testid="stElementContainer"],
    .element-container:has(.efs-metric-tile) + .element-container {{
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
    }}

    /* --- Consistent Block Spacing (16-24px) & Uniform Widths across all steps (V6) --- */
    div[data-testid="stHorizontalBlock"]:has(.efs-metric-tile) {{
        margin-bottom: 4px !important; /* Total gap with Streamlit container flex gap (16px) = 20px (target: 16-24px) */
        width: 100% !important;
        box-sizing: border-box !important;
    }}

    div[data-testid="stHorizontalBlock"]:has(.efs-stepper-card) {{
        margin-bottom: 4px !important; /* Total gap with Streamlit container flex gap (16px) = 20px (target: 16-24px) */
        width: 100% !important;
        box-sizing: border-box !important;
    }}

    /* Prevent double margin when expander immediately follows a metric tile row */
    div[data-testid="stHorizontalBlock"]:has(.efs-metric-tile) + div[data-testid="stElementContainer"] [data-testid="stExpander"],
    div[data-testid="stHorizontalBlock"]:has(.efs-metric-tile) + [data-testid="stExpander"],
    div[data-testid="stHorizontalBlock"]:has(.efs-metric-tile) + div[data-testid="stElementContainer"]:has([data-testid="stExpander"]),
    div[data-testid="stElementContainer"]:has(.efs-metric-tile) + div[data-testid="stElementContainer"] [data-testid="stExpander"] {{
        margin-top: 0 !important;
    }}

    /* --- 8A.2 Components: 5-segment Progress Bar --- */
    .efs-progress-bar {{
        display: flex;
        gap: 4px;
        width: 100%;
        height: 6px;
        border-radius: 3px;
        margin: 8px 0 6px 0;
    }}
    .efs-segment {{
        flex: 1;
        height: 100%;
        border-radius: 2px;
        background: var(--efs-surface-2);
    }}
    .efs-segment--done {{
        background-color: var(--efs-success) !important;
    }}
    .efs-segment--active {{
        background-color: var(--efs-accent) !important;
    }}
    .efs-segment--pending {{
        background-color: var(--efs-border) !important;
    }}

    /* --- 8A.2 Project Cards (Beranda Mockup 1) --- */
    .efs-project-card-thumb {{
        position: relative;
        width: 100%;
        aspect-ratio: 16 / 9;
        background: var(--efs-inset);
        border-radius: 10px;
        overflow: hidden;
        display: flex;
        align-items: center;
        justify-content: center;
        border: 1px solid var(--efs-border);
        margin-bottom: 8px;
    }}
    .efs-project-card-thumb img {{
        width: 100%;
        height: 100%;
        object-fit: cover;
    }}
    .efs-project-card-duration {{
        position: absolute;
        bottom: 8px;
        right: 8px;
        background: rgba(10, 13, 20, 0.85);
        color: #F0F4FC;
        font-size: 0.75rem;
        font-weight: 600;
        padding: 2px 7px;
        border-radius: 4px;
        letter-spacing: 0.02em;
    }}
    .efs-project-card-title {{
        font-size: 1.02rem;
        font-weight: 700;
        color: var(--efs-text);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        line-height: 1.3;
        margin-bottom: 2px;
    }}
    .efs-project-card-niche {{
        font-size: 0.8rem;
        color: var(--efs-dim);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-bottom: 4px;
    }}
    .efs-project-card-status {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 0.78rem;
        color: var(--efs-muted);
        margin-bottom: 4px;
    }}

    /* --- 8A.2 Stepper Cards (Project Page Mockup 2 & 3) --- */
    .efs-stepper-card {{
        background-color: var(--efs-surface);
        border: 1px solid var(--efs-border);
        border-radius: 12px;
        padding: 8px 12px;
        display: flex;
        align-items: center;
        gap: 10px;
        min-height: 56px;
        height: 56px;
        transition: all 0.15s ease;
        position: relative;
        box-sizing: border-box;
    }}
    .efs-stepper-card--active {{
        border: 1.5px solid var(--efs-accent) !important;
        background-color: rgba(143, 166, 255, 0.08) !important;
    }}
    .efs-stepper-card:hover {{
        border-color: var(--efs-border-strong);
        background-color: var(--efs-surface-2);
    }}
    .efs-stepper-num {{
        width: 26px;
        height: 26px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.82rem;
        font-weight: 700;
        flex-shrink: 0;
    }}
    .efs-stepper-num--done {{
        background-color: var(--efs-success);
        color: #0D1015;
    }}
    .efs-stepper-num--active {{
        background-color: var(--efs-accent);
        color: #0D1015;
    }}
    .efs-stepper-num--pending {{
        background-color: var(--efs-surface-2);
        color: var(--efs-muted);
        border: 1px solid var(--efs-border);
    }}
    .efs-stepper-info {{
        display: flex;
        flex-direction: column;
        line-height: 1.2;
        overflow: hidden;
    }}
    .efs-stepper-title {{
        font-size: 0.88rem;
        font-weight: 700;
        color: var(--efs-text);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .efs-stepper-status {{
        font-size: 0.75rem;
        font-weight: 500;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .efs-stepper-status--done {{
        color: var(--efs-success);
    }}
    .efs-stepper-status--active {{
        color: #4EBE8A;
    }}
    .efs-stepper-status--running {{
        color: var(--efs-accent);
    }}
    .efs-stepper-status--pending {{
        color: var(--efs-muted);
    }}
    .efs-stepper-status--warning {{
        color: var(--efs-warning);
    }}

    div[class*="st-key-stepper_btn_"] {{
        margin-top: -56px !important;
        height: 56px !important;
        position: relative;
        z-index: 5;
    }}
    div[class*="st-key-stepper_btn_"] button {{
        opacity: 0 !important;
        height: 56px !important;
        min-height: 56px !important;
        width: 100% !important;
        cursor: pointer !important;
        border: none !important;
        background: transparent !important;
        padding: 0 !important;
    }}

    .efs-hidden-metric {{
        display: none !important;
        height: 0 !important;
        overflow: hidden !important;
        position: absolute !important;
    }}

    /* --- 8A.2 Job Monitor Banner (Mockup 2) --- */
    .efs-job-banner {{
        background-color: var(--efs-surface);
        border: 1px solid var(--efs-accent);
        border-radius: var(--efs-radius-card);
        padding: 1rem 1.25rem;
        margin-bottom: 1.25rem;
        box-shadow: 0 4px 16px rgba(143, 166, 255, 0.08);
    }}
    .efs-job-banner__header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.5rem;
    }}
    .efs-job-banner__title {{
        font-size: 1.05rem;
        font-weight: 700;
        color: var(--efs-text);
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .efs-job-banner__meta {{
        font-size: 0.85rem;
        color: var(--efs-accent);
        font-weight: 600;
    }}

    /* --- 8A.2 Success Banner (Mockup 3) --- */
    .efs-success-banner {{
        display: flex;
        align-items: center;
        gap: 14px;
        padding: 1rem 1.25rem;
        background-color: rgba(74, 222, 155, 0.1);
        border: 1px solid var(--efs-success);
        border-radius: var(--efs-radius-card);
        margin-bottom: 1.25rem;
        color: var(--efs-text);
    }}
    .efs-success-banner__icon {{
        width: 28px;
        height: 28px;
        border-radius: 50%;
        background-color: var(--efs-success);
        color: #0A0D14;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 800;
        font-size: 0.95rem;
        flex-shrink: 0;
    }}
    .efs-success-banner__text {{
        font-size: 0.92rem;
        line-height: 1.45;
        color: var(--efs-text);
    }}

    /* --- 8A.2 Visual Step Shot Cards & Detail --- */
    .efs-shot-card {{
        background: var(--efs-surface);
        border: 1px solid var(--efs-border);
        border-radius: 10px;
        padding: 8px;
        margin-bottom: 10px;
        transition: border-color 0.15s ease;
    }}
    .efs-shot-card--active {{
        border: 2px solid var(--efs-accent) !important;
        background-color: rgba(143, 166, 255, 0.05) !important;
    }}
    .efs-shot-thumb {{
        width: 100%;
        aspect-ratio: 16 / 9;
        border-radius: 6px;
        overflow: hidden;
        background: var(--efs-inset);
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        justify-content: center;
    }}
    .efs-shot-thumb img {{
        width: 100%;
        height: 100%;
        object-fit: cover;
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


def render_metric_tile(label: str, value: str, hint: str = "", status: str = ""):
    """Render a custom metric tile widget that wraps and never truncates text (V2)."""
    safe_label = html.escape(str(label))
    safe_value = html.escape(str(value))
    hint_html = f'<div class="efs-metric-tile__hint">{html.escape(hint)}</div>' if hint else ""
    val_cls = "efs-metric-tile__value"
    if status == "success":
        val_cls += " efs-metric-tile__value--success"
    elif status in ("warning", "pending", "interrupted"):
        val_cls += " efs-metric-tile__value--warning"
    elif status == "accent":
        val_cls += " efs-metric-tile__value--accent"

    st.markdown(
        f"""
        <div class="efs-metric-tile">
            <div class="efs-metric-tile__label">{safe_label}</div>
            <div class="{val_cls}">{safe_value}</div>
            {hint_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
    # Also render hidden st.metric so existing AppTests inspecting at.metric continue to work seamlessly!
    st.metric(label=str(label), value=str(value))


def render_stepper_card_html(
    step_num: int,
    title: str,
    status_label: str,
    status_state: str,
    is_selected: bool,
) -> str:
    """Render card HTML for 5-step stepper matching Mockup 2 & 3 (V2)."""
    card_cls = "efs-stepper-card"
    if is_selected:
        card_cls += " efs-stepper-card--active"

    if status_state == "done":
        num_cls = "efs-stepper-num--done"
        st_cls = "efs-stepper-status--done"
    elif status_state == "active":
        num_cls = "efs-stepper-num--active"
        st_cls = "efs-stepper-status--active"
    elif status_state == "running":
        num_cls = "efs-stepper-num--active"
        st_cls = "efs-stepper-status--running"
    elif status_state in ("warning", "terhenti", "perlu_diperbarui"):
        num_cls = "efs-stepper-num--pending"
        st_cls = "efs-stepper-status--warning"
    else:
        num_cls = "efs-stepper-num--pending"
        st_cls = "efs-stepper-status--pending"

    return (
        f'<div class="{card_cls}">'
        f'<div class="efs-stepper-num {num_cls}">{step_num}</div>'
        f'<div class="efs-stepper-info">'
        f'<div class="efs-stepper-title">{html.escape(title)}</div>'
        f'<div class="efs-stepper-status {st_cls}">{html.escape(status_label)}</div>'
        f'</div>'
        f'</div>'
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


def render_5_segment_progress_bar_html(steps: dict) -> str:
    """Return HTML string for 5-segment progress bar (Mockup 1)."""
    step_order = ["script", "voice", "visual", "render", "capcut"]
    segments = []
    found_active = False

    for k in step_order:
        s_data = steps.get(k, {}) if isinstance(steps, dict) else {}
        s_val = s_data.get("status", "belum") if isinstance(s_data, dict) else "belum"

        if s_val == "selesai":
            cls = "efs-segment--done"
        elif s_val == "berjalan" or (not found_active and s_val in ("belum", "terhenti", "perlu_diperbarui")):
            cls = "efs-segment--active"
            found_active = True
        else:
            cls = "efs-segment--pending"

        segments.append(f'<div class="efs-segment {cls}"></div>')

    return f'<div class="efs-progress-bar">{"".join(segments)}</div>'


def render_success_banner(message: str):
    """Render green success banner with checkmark (Mockup 3)."""
    st.markdown(
        f"""
        <div class="efs-success-banner">
            <div class="efs-success-banner__icon">✓</div>
            <div class="efs-success-banner__text">{html.escape(message)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
