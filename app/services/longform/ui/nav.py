"""Navigation helper for bidirectional routing between MoneyPrinterTurbo Main and Long Video (G5, H3, H5)."""

from __future__ import annotations

import streamlit as st


def _inject_sidebar_css(is_main: bool = True):
    """Inject CSS for sidebar open/close toggle, hide default stSidebarNav, and highlight active nav (H3, H5)."""
    sidebar_css = """
    <style>
    /* Stage 7A.5 H3: Re-reveal exclusively the sidebar open/close toggle without exposing header toolbar or decorations */
    header[data-testid="stHeader"] {
        display: block !important;
        background: transparent !important;
        height: auto !important;
        pointer-events: none !important;
    }
    div[data-testid="stToolbar"] {
        display: flex !important;
        background: transparent !important;
        height: auto !important;
        pointer-events: none !important;
    }
    [data-testid="stExpandSidebarButton"] {
        visibility: visible !important;
        display: inline-flex !important;
        pointer-events: auto !important;
        z-index: 999999 !important;
    }
    [data-testid="stExpandSidebarButton"] * {
        visibility: visible !important;
        pointer-events: auto !important;
    }
    [data-testid="stSidebarCollapseButton"] {
        visibility: visible !important;
        display: inline-flex !important;
        pointer-events: auto !important;
    }
    /* Strictly keep all other decorations, platform toolbars, and nudges hidden */
    div[data-testid="stDecoration"],
    div[data-testid="stStatusWidget"],
    div[data-testid="stSkillsNudgeAnchor"],
    div[data-testid="stSkillsNudge"] {
        display: none !important;
    }
    div[data-testid="stToolbar"] > div:not(:has([data-testid="stExpandSidebarButton"])) {
        display: none !important;
    }

    /* Stage 7A.5 H5: Hide Streamlit's built-in multi-page sidebar navigation completely in both pages */
    [data-testid="stSidebarNav"],
    [data-testid="stSidebarNavSeparator"],
    ul[data-testid="stSidebarNavItems"] {
        display: none !important;
    }

    /* Stage 7A.5 H5: Distinctly highlight the active item in 'Navigasi Studio' */
    [data-testid="stSidebar"] div.stPageLink a:has(b),
    [data-testid="stSidebar"] div.stPageLink a:has(strong),
    [data-testid="stSidebar"] div.stPageLink a[data-testid="stPageLink-NavLink"]:has(b),
    [data-testid="stSidebar"] div.stPageLink a[data-testid="stPageLink-NavLink"]:has(strong) {
        background-color: rgba(255, 75, 75, 0.12) !important;
        border-left: 4px solid #ff4b4b !important;
        padding-left: 8px !important;
        border-radius: 4px !important;
        font-weight: 700 !important;
    }
    </style>
    """
    st.markdown(sidebar_css, unsafe_allow_html=True)


def _render_studio_nav(current_page: str = "main"):
    """Render unified '🧭 Navigasi Studio' in sidebar with active page highlight (H3, H5)."""
    with st.sidebar:
        st.subheader("🧭 Navigasi Studio")

        # 1. MoneyPrinterTurbo (Video Pendek)
        is_main_active = (current_page == "main")
        main_label = "**MoneyPrinterTurbo (Video Pendek)**" if is_main_active else "MoneyPrinterTurbo (Video Pendek)"
        main_help = "Halaman utama pembuatan video pendek MoneyPrinterTurbo" + (" (Halaman Aktif)" if is_main_active else "")
        main_rendered = False

        for candidate in ["Main.py", "webui/Main.py", "../Main.py"]:
            try:
                st.page_link(
                    candidate,
                    label=main_label,
                    icon="🤖",
                    help=main_help,
                )
                main_rendered = True
                break
            except Exception:
                continue

        if not main_rendered:
            btn_type = "primary" if is_main_active else "secondary"
            if st.button(
                "MoneyPrinterTurbo (Video Pendek)" + (" (Aktif)" if is_main_active else ""),
                key=f"btn_sb_nav_mpt_{current_page}",
                icon="🤖",
                type=btn_type,
                use_container_width=True,
                help=main_help,
            ):
                if not is_main_active:
                    try:
                        st.switch_page("Main.py")
                    except Exception:
                        pass

        # 2. Studio Video Panjang
        is_lv_active = (current_page == "longform")
        lv_label = "**Studio Video Panjang**" if is_lv_active else "Studio Video Panjang"
        lv_help = "Buka Studio Video Panjang (10–20 menit)" + (" (Halaman Aktif)" if is_lv_active else "")
        lv_rendered = False

        for candidate in ["pages/1_🎬_Long_Video.py", "1_🎬_Long_Video.py", "webui/pages/1_🎬_Long_Video.py"]:
            try:
                st.page_link(
                    candidate,
                    label=lv_label,
                    icon="🎬",
                    help=lv_help,
                )
                lv_rendered = True
                break
            except Exception:
                continue

        if not lv_rendered:
            btn_type = "primary" if is_lv_active else "secondary"
            if st.button(
                "Studio Video Panjang" + (" (Aktif)" if is_lv_active else ""),
                key=f"btn_sb_nav_lv_{current_page}",
                icon="🎬",
                type=btn_type,
                use_container_width=True,
                help=lv_help,
            ):
                if not is_lv_active:
                    try:
                        st.switch_page("pages/1_🎬_Long_Video.py")
                    except Exception:
                        pass


def render_longform_entry():
    """Render '🎬 Long Video' link in Main.py, unhide sidebar toggle, setup sidebar nav, and hide stSidebarNav (G5, H3, H5)."""
    # 1. H3 & H5: Inject CSS
    _inject_sidebar_css(is_main=True)

    # 2. H3 & H5: Sidebar navigation in Main.py (main is active)
    _render_studio_nav(current_page="main")

    # 3. G5: Top-bar link in Main.py header actions
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
    """Render '← MoneyPrinterTurbo' link in Long Video, setup sidebar nav, and hide stSidebarNav (G5, H3, H5)."""
    # 1. H5: Inject CSS
    _inject_sidebar_css(is_main=False)

    # 2. H3 & H5: Sidebar navigation in Long Video page (longform is active)
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
