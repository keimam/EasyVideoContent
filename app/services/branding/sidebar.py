"""Sidebar navigation and branding components for EasyFaceless Studio (B3, R1, R2)."""

from __future__ import annotations

import html
import streamlit as st

from app.services.branding.constants import (
    APP_NAME,
    APP_SHORT,
    APP_TAGLINE,
    VERSION_STRING,
    MIT_ATTRIBUTION,
    MIT_THANKS,
    UPSTREAM_REPO_URL,
    get_logo_data_uri,
)


@st.dialog("Tentang EasyFaceless Studio")
def show_about_dialog():
    """Dialog displaying version, description, and MIT upstream attribution."""
    logo_uri = get_logo_data_uri()
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 14px; margin-bottom: 1rem;">
            <img src="{logo_uri}" style="width: 44px; height: 44px; border-radius: 10px;" alt="Logo" />
            <div>
                <h3 style="margin: 0; font-size: 1.3rem; font-weight: 700; color: var(--efs-text);">{APP_NAME}</h3>
                <span style="font-size: 0.82rem; color: var(--efs-accent); font-weight: 600;">{VERSION_STRING}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        **EasyFaceless Studio** adalah rangkaian alat pembuatan video otomatis faceless bertenaga AI,
        mendukung pembuatan naskah, sintesis suara, visualisasi shot otomatis, penggabungan musik latar,
        dan integrasi pengeditan timeline CapCut Desktop.
        """
    )
    st.divider()
    st.markdown(f"### Atribusi & Lisensi\n\n{MIT_THANKS}")
    st.markdown(
        f"""
        - **Proyek Asli:** [{UPSTREAM_REPO_URL}]({UPSTREAM_REPO_URL})
        - **Lisensi:** MIT License (Copyright &copy; 2024 Harry)
        - **Status Modifikasi:** Fitur Video Panjang dikembangkan sebagai ekstensi mandiri yang terisolasi.
        """
    )
    if st.button("Tutup", key="btn_close_about_dialog", use_container_width=True):
        st.rerun()


@st.dialog("Kelola Niche & Sub-Niche", width="large")
def show_niche_dialog():
    """Dialog for inspecting and managing niches & sub-niches."""
    from app.services.longform.presets import list_presets, render_preset_tree

    st.subheader("🏷️ Daftar Niche & Sub-Niche")
    st.caption("Niche mendefinisikan gaya narasi, kata kunci footage, musik, dan istilah negatif.")

    presets = list_presets()

    col1, col2 = st.columns([1.2, 1.8])
    with col1:
        st.markdown("**Struktur Niche:**")
        tree_text = render_preset_tree()
        st.code(tree_text, language="text")

    with col2:
        st.markdown("**Detail Preset:**")
        preset_ids = [p["preset_id"] for p in presets]
        chosen_id = st.selectbox("Pilih Preset untuk Dilihat:", preset_ids, key="sb_dialog_chosen_preset")
        if chosen_id:
            from app.services.longform.presets import get_preset
            p_data = get_preset(chosen_id)
            if p_data:
                st.write(f"**Nama:** {p_data.get('name', chosen_id)}")
                st.write(f"**Tipe:** {'Sub-Niche' if p_data.get('extends') else 'Niche Utama'}")
                if p_data.get("extends"):
                    st.write(f"**Turunan dari:** `{p_data.get('extends')}`")
                kw = p_data.get("footage_keywords", [])
                if kw:
                    st.write(f"**Kata Kunci Footage:** {', '.join(kw[:8])}...")
                neg = p_data.get("avoid_keywords", [])
                if neg:
                    st.write(f"**Kata Kunci Dihindari:** {', '.join(neg[:6])}...")

    if st.button("Tutup", key="btn_close_niche_dialog", use_container_width=True):
        st.rerun()


def _inject_sidebar_styles():
    """Inject styling specific to EasyFaceless sidebar (R2: matches mockup)."""
    css = """
    <style id="efs-sidebar-styles">
    /* Header Brand matching mockup (logo left, name + STUDIO subtitle stacked) */
    .efs-sb-header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 0.25rem 0.25rem 1.25rem 0.25rem;
        margin-bottom: 0.5rem;
    }
    .efs-sb-header__logo {
        width: 36px;
        height: 36px;
        border-radius: 8px;
        flex-shrink: 0;
    }
    .efs-sb-header__text {
        display: flex;
        flex-direction: column;
        gap: 1px;
    }
    .efs-sb-header__title {
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
        font-size: 1.15rem;
        font-weight: 700;
        color: var(--efs-text);
        letter-spacing: -0.01em;
        line-height: 1.2;
    }
    .efs-sb-header__subtitle {
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif !important;
        font-size: 0.65rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        color: var(--efs-dim);
        text-transform: uppercase;
        line-height: 1;
    }

    /* Section Headers */
    .efs-sb-section {
        font-size: 0.72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--efs-dim);
        margin-top: 1.1rem;
        margin-bottom: 0.4rem;
        padding-left: 0.35rem;
    }

    /* R2: Active item: background #1F2633 + accent icon, NO left border line */
    [data-testid="stSidebar"] div.stPageLink a:has(strong),
    [data-testid="stSidebar"] div.stPageLink a:has(b),
    [data-testid="stSidebar"] div.stPageLink a[data-testid="stPageLink-NavLink"]:has(strong),
    [data-testid="stSidebar"] div.stPageLink a[data-testid="stPageLink-NavLink"]:has(b) {
        background-color: var(--efs-nav-active) !important;
        border-left: none !important;
        border: none !important;
        color: var(--efs-text) !important;
        padding: 8px 12px !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
    }

    /* Active icon gets accent color #8FA6FF */
    [data-testid="stSidebar"] div.stPageLink a:has(strong) [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stPageLink a:has(b) [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stPageLink a:has(strong) [class*="material-symbols"],
    [data-testid="stSidebar"] div.stPageLink a:has(b) [class*="material-symbols"] {
        color: var(--efs-accent) !important;
    }

    /* Inactive nav items */
    [data-testid="stSidebar"] div.stPageLink a {
        border-radius: 8px !important;
        border: none !important;
        transition: all 0.15s ease-in-out !important;
        padding: 8px 12px !important;
        color: var(--efs-text-2) !important;
    }

    [data-testid="stSidebar"] div.stPageLink a [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stPageLink a [class*="material-symbols"] {
        color: var(--efs-dim) !important;
        margin-right: 4px !important;
    }

    [data-testid="stSidebar"] div.stPageLink a:hover {
        background-color: var(--efs-surface) !important;
        color: var(--efs-text) !important;
    }

    [data-testid="stSidebar"] div.stPageLink a:hover [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stPageLink a:hover [class*="material-symbols"] {
        color: var(--efs-text) !important;
    }

    /* Sidebar Action Buttons styled like nav items */
    [data-testid="stSidebar"] div.stButton > button {
        background-color: transparent !important;
        border: none !important;
        color: var(--efs-text-2) !important;
        text-align: left !important;
        justify-content: flex-start !important;
        padding: 8px 12px !important;
        min-height: 40px !important;
        border-radius: 8px !important;
        font-weight: 500 !important;
        transition: all 0.15s ease-in-out !important;
    }

    [data-testid="stSidebar"] div.stButton > button:hover {
        background-color: var(--efs-surface) !important;
        color: var(--efs-text) !important;
    }

    [data-testid="stSidebar"] div.stButton > button [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stButton > button [class*="material-symbols"] {
        color: var(--efs-dim) !important;
        margin-right: 4px !important;
    }

    [data-testid="stSidebar"] div.stButton > button:hover [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stButton > button:hover [class*="material-symbols"] {
        color: var(--efs-text) !important;
    }

    /* Fallback button active styling */
    [data-testid="stSidebar"] div.stButton > button[kind="primary"] {
        background-color: var(--efs-nav-active) !important;
        color: var(--efs-text) !important;
        border: none !important;
    }

    [data-testid="stSidebar"] div.stButton > button[kind="primary"] [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stButton > button[kind="primary"] [class*="material-symbols"] {
        color: var(--efs-accent) !important;
    }

    /* Sidebar Footer matching mockup: subtle card at bottom */
    .efs-sb-footer {
        margin-top: 2.5rem;
        padding: 12px 14px;
        border-radius: 10px;
        background-color: var(--efs-surface);
        border: 1px solid var(--efs-border);
        font-size: 0.75rem;
        line-height: 1.4;
        color: var(--efs-muted);
    }
    .efs-sb-footer__version {
        font-weight: 600;
        color: var(--efs-text-2);
        margin-bottom: 3px;
    }
    .efs-sb-footer__mit {
        color: var(--efs-dim);
        font-size: 0.72rem;
    }
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def render_brand_sidebar(current_page: str = "main"):
    """Render unified EasyFaceless Studio sidebar (B3, R2).

    :param current_page: 'main' (video pendek) or 'longform' (video panjang).
    """
    _inject_sidebar_styles()
    logo_uri = get_logo_data_uri()

    with st.sidebar:
        # 1. Header (Logo + EasyFaceless + STUDIO subtitle, matching mockup)
        st.markdown(
            f"""
            <div class="efs-sb-header">
                <img class="efs-sb-header__logo" src="{logo_uri}" alt="{APP_NAME}" />
                <div class="efs-sb-header__text">
                    <span class="efs-sb-header__title">{APP_SHORT}</span>
                    <span class="efs-sb-header__subtitle">{APP_TAGLINE}</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # 2. Group PRODUKSI
        st.markdown('<div class="efs-sb-section">PRODUKSI</div>', unsafe_allow_html=True)

        is_lv = (current_page == "longform")
        lv_label = "**Video Panjang**" if is_lv else "Video Panjang"
        lv_help = "Studio Video Panjang Dokumenter (10–20 menit)" + (" (Halaman Aktif)" if is_lv else "")
        lv_rendered = False
        for path in ["pages/1_🎬_Long_Video.py", "1_🎬_Long_Video.py", "webui/pages/1_🎬_Long_Video.py"]:
            try:
                st.page_link(path, label=lv_label, icon=":material/movie:", help=lv_help)
                lv_rendered = True
                break
            except Exception:
                continue

        if not lv_rendered:
            if st.button(
                "Video Panjang" + (" (Aktif)" if is_lv else ""),
                key=f"efs_btn_lv_{current_page}",
                icon=":material/movie:",
                type="primary" if is_lv else "secondary",
                use_container_width=True,
                help=lv_help,
            ):
                if not is_lv:
                    try:
                        st.switch_page("pages/1_🎬_Long_Video.py")
                    except Exception:
                        pass

        is_main = (current_page == "main")
        main_label = "**Video Pendek**" if is_main else "Video Pendek"
        main_help = "Studio Pembuatan Video Pendek" + (" (Halaman Aktif)" if is_main else "")
        main_rendered = False
        for path in ["Main.py", "webui/Main.py", "../Main.py"]:
            try:
                st.page_link(path, label=main_label, icon=":material/bolt:", help=main_help)
                main_rendered = True
                break
            except Exception:
                continue

        if not main_rendered:
            if st.button(
                "Video Pendek" + (" (Aktif)" if is_main else ""),
                key=f"efs_btn_main_{current_page}",
                icon=":material/bolt:",
                type="primary" if is_main else "secondary",
                use_container_width=True,
                help=main_help,
            ):
                if not is_main:
                    try:
                        st.switch_page("Main.py")
                    except Exception:
                        pass

        # 3. Group PENGATURAN
        st.markdown('<div class="efs-sb-section">PENGATURAN</div>', unsafe_allow_html=True)

        # 3.1 Kelola Niche
        if st.button(
            "Kelola Niche",
            key=f"efs_btn_niche_{current_page}",
            icon=":material/category:",
            use_container_width=True,
            help="Kelola template topik, niche, dan kata kunci footage",
        ):
            show_niche_dialog()

        # 3.2 API & Penyedia AI
        if st.button(
            "API & Penyedia AI",
            key=f"efs_btn_settings_ai_{current_page}",
            icon=":material/tune:",
            use_container_width=True,
            help="Buka konfigurasi model AI (LLM, TTS, dan Gambar)",
        ):
            if current_page == "main":
                st.session_state["settings_dialog_open"] = True
                st.session_state["settings_dialog_target_tab"] = "llm"
                st.rerun()
            else:
                try:
                    st.switch_page("Main.py")
                except Exception:
                    pass

        # 3.3 Cache & Penyimpanan
        if st.button(
            "Cache & Penyimpanan",
            key=f"efs_btn_settings_cache_{current_page}",
            icon=":material/cleaning_services:",
            use_container_width=True,
            help="Periksa dan bersihkan cache video & materi",
        ):
            if current_page == "main":
                st.session_state["settings_dialog_open"] = True
                st.session_state["settings_dialog_target_tab"] = "cache"
                st.rerun()
            else:
                try:
                    st.switch_page("Main.py")
                except Exception:
                    pass

        # 4. Footer (matching mockup)
        st.markdown(
            f"""
            <div class="efs-sb-footer">
                <div class="efs-sb-footer__version">{VERSION_STRING}</div>
                <div class="efs-sb-footer__mit">Dibangun di atas<br>MoneyPrinterTurbo (MIT)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button(
            "Tentang",
            key=f"efs_btn_about_{current_page}",
            icon=":material/info:",
            use_container_width=True,
            help="Informasi versi, lisensi MIT, dan atribusi MoneyPrinterTurbo",
        ):
            show_about_dialog()
