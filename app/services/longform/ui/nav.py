"""Navigation helper for bidirectional routing between MoneyPrinterTurbo Main and Long Video (G5)."""

from __future__ import annotations

import streamlit as st


def render_longform_entry():
    """Render '🎬 Long Video' navigation link in Main.py top bar (G5)."""
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
    """Render '← MoneyPrinterTurbo (video pendek)' navigation link in Long Video studio (G5)."""
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
