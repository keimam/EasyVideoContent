"""Navigation helper for bidirectional routing between MoneyPrinterTurbo Main and Long Video (G5, H3)."""

from __future__ import annotations

import streamlit as st


def render_longform_entry():
    """Render '🎬 Long Video' navigation link in Main.py top bar, unhide sidebar toggle, and setup sidebar nav (G5, H3)."""
    # 1. H3: Inject CSS to reveal ONLY the sidebar expand/collapse chevron without exposing platform toolbars/decorations
    sidebar_toggle_css = """
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
    </style>
    """
    st.markdown(sidebar_toggle_css, unsafe_allow_html=True)

    # 2. H3: Sidebar navigation in Main.py
    with st.sidebar:
        st.subheader("🧭 Navigasi Studio")
        try:
            st.page_link(
                "Main.py",
                label="MoneyPrinterTurbo (Video Pendek)",
                icon="🤖",
                help="Halaman utama pembuatan video pendek MoneyPrinterTurbo",
            )
        except Exception:
            pass
        try:
            st.page_link(
                "pages/1_🎬_Long_Video.py",
                label="Studio Video Panjang",
                icon="🎬",
                help="Buka Studio Video Panjang (10–20 menit)",
            )
        except Exception:
            pass

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


def render_mpt_entry():
    """Render '← MoneyPrinterTurbo (video pendek)' navigation link in Long Video studio and sidebar nav (G5, H3)."""
    # Sidebar navigation in Long Video page
    with st.sidebar:
        st.subheader("🧭 Navigasi Studio")
        try:
            st.page_link(
                "Main.py",
                label="MoneyPrinterTurbo (Video Pendek)",
                icon="🤖",
                help="Kembali ke halaman utama pembuatan video pendek MoneyPrinterTurbo",
            )
        except Exception:
            if st.button("MoneyPrinterTurbo (Video Pendek)", key="btn_sb_mpt_main", icon="🤖"):
                try:
                    st.switch_page("Main.py")
                except Exception:
                    pass
        try:
            st.page_link(
                "pages/1_🎬_Long_Video.py",
                label="Studio Video Panjang",
                icon="🎬",
                help="Studio Video Panjang (10–20 menit)",
            )
        except Exception:
            pass

    # Top-of-page quick return link
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
