"""Navigation helper for bidirectional routing between Main (Video Pendek) and Long Video (G5, H3, H5, 8A.1)."""

from __future__ import annotations

import streamlit as st


def _inject_sidebar_css(is_main: bool = True):
    """Inject branding theme and sidebar toggle CSS (H3, H5, 8A.1)."""
    try:
        from app.services.longform.launcher_runtime import ensure_app_mutex
        ensure_app_mutex()
    except Exception:
        pass
    try:
        from app.services.branding.theme import inject_brand_theme
        inject_brand_theme()
    except Exception:
        pass


def _render_studio_nav(current_page: str = "main"):
    """Render unified EasyFaceless Studio sidebar (B3, R2)."""
    from app.services.branding.sidebar import render_brand_sidebar
    render_brand_sidebar(current_page=current_page)


def render_longform_entry():
    """Backward-compatible entry point for Main.py sidebar rendering (R2)."""
    _inject_sidebar_css(is_main=True)
    _render_studio_nav(current_page="main")


def render_mpt_entry(show_top_return: bool = False):
    """Inject brand theme and render studio sidebar; optional top return uses neutral Short Video label (G5, H3, H5, 8A.1, C6)."""
    # 1. Inject brand theme & CSS
    _inject_sidebar_css(is_main=False)

    # 2. Sidebar navigation in Long Video page (longform is active)
    _render_studio_nav(current_page="longform")

    # 3. Top-of-page quick return link
    if show_top_return:
        from app.services.longform.ui.i18n import t
        try:
            st.page_link(
                "Main.py",
                label=t("nav_back_mpt"),
                help=t("nav_back_mpt_help"),
            )
        except Exception:
            if st.button(t("nav_back_mpt"), key="btn_nav_mpt_main"):
                try:
                    st.switch_page("Main.py")
                except Exception:
                    pass
