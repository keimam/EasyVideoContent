"""Navigation helper for bidirectional routing between Main (Video Pendek) and Long Video (G5, H3, H5, 8A.1)."""

from __future__ import annotations

import streamlit as st


def _inject_sidebar_css(is_main: bool = True):
    """Inject branding theme and sidebar toggle CSS (H3, H5, 8A.1)."""
    try:
        from app.services.branding.theme import inject_brand_theme
        inject_brand_theme()
    except Exception:
        pass


def _render_studio_nav(current_page: str = "main"):
    """Render unified EasyFaceless Studio sidebar (B3)."""
    try:
        from app.services.branding.sidebar import render_brand_sidebar
        render_brand_sidebar(current_page=current_page)
    except Exception:
        # Fallback to basic navigation if branding module is detached
        with st.sidebar:
            st.subheader("🧭 Navigasi Studio")
            is_main_active = (current_page == "main")
            st.page_link(
                "Main.py",
                label="**Video Pendek**" if is_main_active else "Video Pendek",
                icon="🤖",
            )
            is_lv_active = (current_page == "longform")
            st.page_link(
                "pages/1_🎬_Long_Video.py",
                label="**Video Panjang**" if is_lv_active else "Video Panjang",
                icon="🎬",
            )


def render_longform_entry():
    """Render '🎬 Long Video' link in Main.py, inject brand theme, and render sidebar (G5, H3, H5, 8A.1)."""
    # 1. Inject brand theme & CSS
    _inject_sidebar_css(is_main=True)

    # 2. Sidebar navigation in Main.py (main is active)
    _render_studio_nav(current_page="main")

    # 3. Top-bar link in Main.py header actions (G5)
    try:
        st.page_link(
            "pages/1_🎬_Long_Video.py",
            label="🎬 Long Video",
            help="Buka Studio Video Panjang (10–20 menit)",
        )
    except Exception:
        if st.button("🎬 Long Video", key="btn_nav_long_video", help="Buka Studio Video Panjang"):
            try:
                st.switch_page("pages/1_🎬_Long_Video.py")
            except Exception:
                pass


def render_mpt_entry(show_top_return: bool = True):
    """Render '← MoneyPrinterTurbo' link in Long Video, inject brand theme, and render sidebar (G5, H3, H5, 8A.1)."""
    # 1. Inject brand theme & CSS
    _inject_sidebar_css(is_main=False)

    # 2. Sidebar navigation in Long Video page (longform is active)
    _render_studio_nav(current_page="longform")

    # 3. Top-of-page quick return link
    if show_top_return:
        try:
            st.page_link(
                "Main.py",
                label="← MoneyPrinterTurbo (video pendek)",
                help="Kembali ke halaman utama pembuatan video pendek MoneyPrinterTurbo",
            )
        except Exception:
            if st.button("← MoneyPrinterTurbo (video pendek)", key="btn_nav_mpt_main"):
                try:
                    st.switch_page("Main.py")
                except Exception:
                    pass
