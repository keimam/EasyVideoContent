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
from app.services.longform.ui.i18n import t


def _render_about_dialog_body():
    """Dialog body displaying version, description, and MIT upstream attribution."""
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
    st.markdown(t("about_dialog_desc"))
    st.divider()
    st.markdown(f"### {t('about_dialog_license')}\n\n{MIT_THANKS}")
    st.markdown(
        f"""
        - **{t('about_dialog_original_proj')}** [{UPSTREAM_REPO_URL}]({UPSTREAM_REPO_URL})
        - **{t('about_dialog_license_type')}** {t('license_mit_detail')}
        - **{t('about_dialog_status')}** {t('about_dialog_status_desc')}
        """
    )
    if st.button(t("dialog_close"), key="btn_close_about_dialog", use_container_width=True):
        st.rerun()


def show_about_dialog():
    """Open localized About dialog dynamically."""
    st.dialog(t("about_dialog_title"))(_render_about_dialog_body)()


def _render_niche_dialog_body():
    """Dialog body for inspecting and managing niches & sub-niches (R2)."""
    from app.services.longform.presets import (
        list_presets,
        format_preset_tree,
        get_builtin_presets,
        get_custom_presets,
        get_preset,
    )

    st.subheader(t("niche_dialog_title"))
    st.caption(t("niche_dialog_desc"))

    all_presets = list_presets()
    builtin_presets = get_builtin_presets()
    custom_presets = get_custom_presets()

    # Summary metrics
    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric(t("niche_metric_total"), len(all_presets))
    with m2:
        st.metric(t("niche_metric_builtin"), len(builtin_presets), help=t("niche_metric_builtin_help"))
    with m3:
        st.metric(t("niche_metric_custom"), len(custom_presets), help=t("niche_metric_custom_help"))

    st.markdown("---")

    col1, col2 = st.columns([1.3, 1.7])
    with col1:
        st.markdown(f"**{t('niche_tree_heading')}**")
        tree_text = format_preset_tree(all_presets)
        st.code(tree_text, language="text")

    with col2:
        st.markdown(f"**{t('niche_detail_heading')}**")
        preset_ids = sorted(list(all_presets.keys()))
        chosen_id = st.selectbox(t("niche_select_view"), preset_ids, key="sb_dialog_chosen_preset")
        if chosen_id and chosen_id in all_presets:
            p_data = all_presets[chosen_id]
            st.write(f"**{t('niche_field_name')}** {getattr(p_data, 'name', chosen_id)}")
            is_sub = bool(getattr(p_data, "extends", None))
            st.write(f"**{t('niche_field_type')}** {t('niche_type_sub') if is_sub else t('niche_type_main')}")
            if is_sub:
                st.write(f"**{t('niche_derived_from')}** `{p_data.extends}`")
            kw = getattr(p_data, "footage_keywords", []) or []
            if kw:
                st.write(f"**{t('niche_footage_keywords', count=len(kw))}** {', '.join(kw[:8])}{'...' if len(kw) > 8 else ''}")
            neg = getattr(p_data, "avoid_keywords", []) or []
            if neg:
                st.write(f"**{t('niche_avoid_keywords', count=len(neg))}** {', '.join(neg[:6])}{'...' if len(neg) > 6 else ''}")

    st.markdown("---")
    act_col1, act_col2 = st.columns([1.6, 1])
    with act_col1:
        if st.button(t("niche_btn_open_manager"), key="btn_open_niche_manager", type="primary", use_container_width=True):
            st.session_state["show_niche_mgmt"] = True
            try:
                st.switch_page("pages/1_🎬_Long_Video.py")
            except Exception:
                pass
            st.rerun()
    with act_col2:
        if st.button(t("dialog_close"), key="btn_close_niche_dialog", use_container_width=True):
            st.rerun()


def show_niche_dialog():
    """Open localized Manage Niche dialog dynamically."""
    st.dialog(t("niche_dialog_title"), width="large")(_render_niche_dialog_body)()


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

    /* R2 & R4: Uniform Sidebar Navigation Items (PRODUKSI and PENGATURAN match mockup exactly) */
    [data-testid="stSidebar"] div.stPageLink a,
    [data-testid="stSidebar"] div.stPageLink a[data-testid="stPageLink-NavLink"],
    [data-testid="stSidebar"] div.stElementContainer:not([class*="efs_btn_about"]) div.stButton button {
        background-color: transparent !important;
        border: none !important;
        border-width: 0 !important;
        border-style: none !important;
        border-color: transparent !important;
        outline: none !important;
        box-shadow: none !important;
        color: var(--efs-text-2) !important;
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        text-align: left !important;
        padding: 0 12px !important;
        min-height: 44px !important;
        height: 44px !important;
        border-radius: 8px !important;
        font-weight: 500 !important;
        font-size: 0.88rem !important;
        width: 100% !important;
        gap: 8px !important;
        transition: background-color 0.15s ease, color 0.15s ease !important;
    }

    /* R4: Ensure inner button container and text are left-aligned with 8px icon gap */
    [data-testid="stSidebar"] div.stElementContainer:not([class*="efs_btn_about"]) div.stButton button > div,
    [data-testid="stSidebar"] div.stElementContainer:not([class*="efs_btn_about"]) div.stButton button > div > span,
    [data-testid="stSidebar"] div.stElementContainer:not([class*="efs_btn_about"]) div.stButton button [data-testid="stMarkdownContainer"] {
        display: flex !important;
        justify-content: flex-start !important;
        align-items: center !important;
        text-align: left !important;
        width: 100% !important;
        gap: 8px !important;
    }

    [data-testid="stSidebar"] div.stButton button p,
    [data-testid="stSidebar"] div.stButton button [data-testid="stMarkdownContainer"] > p,
    [data-testid="stSidebar"] div.stPageLink a p {
        margin: 0 !important;
        text-align: left !important;
        display: inline-block !important;
        font-weight: 500 !important;
        font-size: 0.88rem !important;
        line-height: 1.2 !important;
        color: inherit !important;
    }

    /* Vertical rhythm between sidebar items */
    [data-testid="stSidebar"] div.stButton,
    [data-testid="stSidebar"] div.stPageLink {
        margin-bottom: 2px !important;
    }

    /* Hover state for sidebar items */
    [data-testid="stSidebar"] div.stPageLink a:hover,
    [data-testid="stSidebar"] div.stElementContainer:not([class*="efs_btn_about"]) div.stButton button:hover {
        background-color: var(--efs-surface) !important;
        color: var(--efs-text) !important;
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
    }

    /* Focus & active outline removal */
    [data-testid="stSidebar"] div.stButton button:focus,
    [data-testid="stSidebar"] div.stButton button:active {
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
    }

    /* Icon styling for ALL sidebar items */
    [data-testid="stSidebar"] div.stPageLink a [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stPageLink a [class*="material-symbols"],
    [data-testid="stSidebar"] div.stButton button [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stButton button [class*="material-symbols"] {
        color: var(--efs-dim) !important;
        margin: 0 !important;
        margin-right: 0 !important;
        padding: 0 !important;
        font-size: 1.2rem !important;
        width: 1.25rem !important;
        height: 1.25rem !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        flex-shrink: 0 !important;
        transition: color 0.15s ease !important;
    }

    [data-testid="stSidebar"] div.stPageLink a:hover [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stPageLink a:hover [class*="material-symbols"],
    [data-testid="stSidebar"] div.stElementContainer:not([class*="efs_btn_about"]) div.stButton button:hover [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stElementContainer:not([class*="efs_btn_about"]) div.stButton button:hover [class*="material-symbols"] {
        color: var(--efs-text) !important;
    }

    /* Active Item (PRODUKSI or PENGATURAN): background #1F2633 + accent icon #8FA6FF, NO left border line */
    [data-testid="stSidebar"] div.stPageLink a:has(strong),
    [data-testid="stSidebar"] div.stPageLink a:has(b),
    [data-testid="stSidebar"] div.stPageLink a[data-testid="stPageLink-NavLink"]:has(strong),
    [data-testid="stSidebar"] div.stPageLink a[data-testid="stPageLink-NavLink"]:has(b),
    [data-testid="stSidebar"] div.stButton button[kind="primary"],
    [data-testid="stSidebar"] div.stButton button[data-testid*="primary"] {
        background-color: var(--efs-nav-active) !important;
        border: none !important;
        border-left: none !important;
        color: var(--efs-text) !important;
        font-weight: 600 !important;
    }

    [data-testid="stSidebar"] div.stPageLink a:has(strong) [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stPageLink a:has(b) [data-testid="stIconMaterial"],
    [data-testid="stSidebar"] div.stButton button[kind="primary"] [data-testid="stIconMaterial"] {
        color: var(--efs-accent) !important;
    }

    /* R4: "Tentang" button at bottom remains framed */
    [data-testid="stSidebar"] div.stElementContainer[class*="efs_btn_about"] div.stButton button,
    [data-testid="stSidebar"] div[class*="efs_btn_about"] button {
        border: 1px solid var(--efs-border) !important;
        background-color: var(--efs-surface) !important;
        border-radius: 8px !important;
        justify-content: center !important;
        text-align: center !important;
        margin-top: 8px !important;
        min-height: 40px !important;
        height: 40px !important;
        width: 100% !important;
    }

    [data-testid="stSidebar"] div[class*="efs_btn_about"] button > div,
    [data-testid="stSidebar"] div[class*="efs_btn_about"] button > div > span {
        justify-content: center !important;
        text-align: center !important;
        gap: 8px !important;
    }

    [data-testid="stSidebar"] div[class*="efs_btn_about"] button:hover {
        background-color: var(--efs-nav-active) !important;
        border-color: var(--efs-accent) !important;
        color: var(--efs-text) !important;
    }

    [data-testid="stSidebar"] div.stButton[class*="efs_btn_about"] button > div,
    [data-testid="stSidebar"] div.stButton[class*="efs_btn_about"] button > div > span {
        justify-content: center !important;
        text-align: center !important;
        gap: 8px !important;
    }

    [data-testid="stSidebar"] div.stButton[class*="efs_btn_about"] button:hover {
        background-color: var(--efs-nav-active) !important;
        border-color: var(--efs-accent) !important;
        color: var(--efs-text) !important;
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
        st.markdown(f'<div class="efs-sb-section">{t("nav_production")}</div>', unsafe_allow_html=True)

        is_lv = (current_page == "longform")
        lv_label = f"**{t('nav_long_video')}**" if is_lv else t("nav_long_video")
        lv_help = t("nav_long_video_help") + (f" {t('active_page_indicator')}" if is_lv else "")
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
                t("nav_long_video") + (f" {t('active_page_indicator')}" if is_lv else ""),
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
        main_label = f"**{t('nav_short_video')}**" if is_main else t("nav_short_video")
        main_help = t("nav_short_video_help") + (f" {t('active_page_indicator')}" if is_main else "")
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
                t("nav_short_video") + (f" {t('active_page_indicator')}" if is_main else ""),
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
        st.markdown(f'<div class="efs-sb-section">{t("nav_settings")}</div>', unsafe_allow_html=True)

        # 3.1 Kelola Niche
        if st.button(
            t("nav_manage_niche"),
            key=f"efs_btn_niche_{current_page}",
            icon=":material/category:",
            use_container_width=True,
            help=t("nav_manage_niche_help"),
        ):
            show_niche_dialog()

        # 3.2 API & Penyedia AI
        if st.button(
            t("nav_ai_settings"),
            key=f"efs_btn_settings_ai_{current_page}",
            icon=":material/tune:",
            use_container_width=True,
            help=t("nav_ai_settings_help"),
        ):
            st.session_state["settings_dialog_open"] = True
            st.session_state["settings_dialog_target_tab"] = "llm"
            st.rerun()

        # 3.3 Cache & Penyimpanan
        if st.button(
            t("nav_cache_storage"),
            key=f"efs_btn_settings_cache_{current_page}",
            icon=":material/cleaning_services:",
            use_container_width=True,
            help=t("nav_cache_storage_help"),
        ):
            st.session_state["settings_dialog_open"] = True
            st.session_state["settings_dialog_target_tab"] = "cache"
            st.rerun()

        # 4. Footer (matching mockup)
        st.markdown(
            f"""
            <div class="efs-sb-footer">
                <div class="efs-sb-footer__version">{VERSION_STRING}</div>
                <div class="efs-sb-footer__mit">{t("built_on_mpt")}<br>MoneyPrinterTurbo (MIT)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if st.button(
            t("nav_about"),
            key=f"efs_btn_about_{current_page}",
            icon=":material/info:",
            use_container_width=True,
            help=t("nav_about_help"),
        ):
            show_about_dialog()
