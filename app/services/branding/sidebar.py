"""Sidebar navigation and branding components for EasyFaceless Studio (B3, R1, R2)."""

from __future__ import annotations

import html
import streamlit as st

from app.services.branding.constants import (
    APP_COPYRIGHT_YEAR,
    APP_NAME,
    APP_OWNER,
    APP_SHORT,
    APP_TAGLINE,
    APP_VERSION,
    CORE_VERSION,
    PROJECT_ROOT,
    VERSION_STRING,
    MIT_ATTRIBUTION,
    MIT_THANKS,
    UPSTREAM_REPO_URL,
    get_app_build_signature,
    get_logo_data_uri,
)
from app.services.longform.ui.i18n import t


def _render_about_dialog_body():
    """Dialog body displaying app/core versions, build metadata, update check, MIT attribution, and LICENSE text (C7, E12)."""
    logo_uri = get_logo_data_uri()
    sig = get_app_build_signature()
    build_commit = sig["git_commit"]
    build_time = sig["build_time"]
    version_badge = f"v{APP_VERSION} · build {html.escape(build_commit)}"
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 14px; margin-bottom: 1rem;">
            <img src="{logo_uri}" style="width: 44px; height: 44px; border-radius: 10px;" alt="Logo" />
            <div>
                <h3 style="margin: 0; font-size: 1.3rem; font-weight: 700; color: var(--efs-text);">{APP_NAME}</h3>
                <span style="font-size: 0.82rem; color: var(--efs-accent); font-weight: 600;">{version_badge}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(t("about_dialog_desc"))
    st.divider()
    st.markdown(f"### {t('about_update_section_title')}")
    try:
        from app.services.branding.lifecycle_ui import render_update_check_controls
        render_update_check_controls(key_prefix="about_dialog", include_wizard_rerun=False)
    except Exception:
        pass
    st.divider()
    st.markdown(f"### {t('about_dialog_license')}")
    st.markdown(
        f"- {t('about_dialog_app_version', app_ver=APP_VERSION)}\n"
        f"- {t('about_dialog_build_line', build_commit=build_commit, build_time=build_time)}\n"
        f"- {t('about_dialog_owner_line', owner=APP_OWNER, year=APP_COPYRIGHT_YEAR)}\n"
        f"- {t('about_dialog_core_version', core_ver=CORE_VERSION)}\n"
        f"- {t('about_dialog_built_on', url=UPSTREAM_REPO_URL)}\n"
        f"- **{t('about_dialog_original_proj')}** [{UPSTREAM_REPO_URL}]({UPSTREAM_REPO_URL})\n"
        f"- **{t('about_dialog_license_type')}** {t('license_mit_detail')}\n"
        f"- {t('about_dialog_third_party_line')}\n"
        f"- **{t('about_dialog_status')}** {t('about_dialog_status_desc')}"
    )
    license_path = PROJECT_ROOT / "LICENSE"
    license_text = (
        license_path.read_text(encoding="utf-8")
        if license_path.is_file()
        else t("about_dialog_license_missing")
    )
    with st.expander(t("about_dialog_view_license"), expanded=False):
        st.code(license_text, language="text")
    if st.button(t("dialog_close"), key="btn_close_about_dialog", use_container_width=True):
        st.rerun()


def show_about_dialog():
    """Open localized About dialog dynamically."""
    st.session_state["_efs_dialog_opened_this_run"] = True
    st.dialog(t("about_dialog_title"))(_render_about_dialog_body)()


def is_help_or_about_query_request() -> str:
    """Return 'help' or 'about' when URL query params explicitly request Help or About dialogs (E9.2)."""
    try:
        qp = st.query_params
    except Exception:
        return ""
    dlg = str(qp.get("dialog") or "").strip().lower()
    if dlg in ("help", "about"):
        return dlg
    if str(qp.get("help") or "").strip().lower() in ("1", "true", "yes"):
        return "help"
    if str(qp.get("about") or "").strip().lower() in ("1", "true", "yes"):
        return "about"
    return ""


def maybe_open_sidebar_dialog_from_query_params() -> bool:
    """Open Help or About dialog once when explicitly requested via URL query parameters (E9.2)."""
    req = is_help_or_about_query_request()
    if not req:
        return False
    try:
        st.query_params.pop("dialog", None)
        st.query_params.pop("help", None)
        st.query_params.pop("about", None)
    except Exception:
        pass
    if req == "help":
        show_help_dialog()
        return True
    if req == "about":
        show_about_dialog()
        return True
    return False


def _render_help_dialog_body():
    """Dialog body rendering the in-app Help document viewer (C8.1)."""
    from app.services.branding.help_viewer import render_help_dialog_body

    render_help_dialog_body()


def show_help_dialog():
    """Open localized Help & Documentation dialog dynamically."""
    st.session_state["_efs_dialog_opened_this_run"] = True
    st.dialog(t("help_dialog_title"), width="large")(_render_help_dialog_body)()


def _render_exit_app_dialog_body():
    """Confirm and trigger clean application server shutdown (D3)."""
    from app.services.longform.launcher_runtime import (
        list_active_running_jobs,
        request_app_shutdown,
    )

    active_jobs = list_active_running_jobs()
    if active_jobs:
        st.warning(t("exit_app_running_jobs_warning", count=len(active_jobs)))
        for item in active_jobs[:5]:
            st.caption(
                t(
                    "exit_app_job_item",
                    project_id=item.get("project_id"),
                    step=item.get("step"),
                    pid=item.get("pid"),
                )
            )
    else:
        st.markdown(t("exit_app_confirm_desc"))

    col_cancel, col_ok = st.columns(2)
    with col_cancel:
        if st.button(t("exit_app_btn_cancel"), key="btn_exit_app_cancel", use_container_width=True):
            st.rerun()
    with col_ok:
        if st.button(t("exit_app_btn_confirm"), key="btn_exit_app_confirm", type="primary", use_container_width=True):
            request_app_shutdown(stop_running_jobs=True)
            st.success(t("exit_app_goodbye"))
            st.stop()


def show_exit_app_dialog():
    """Open localized Exit App confirmation dialog dynamically (D3)."""
    st.session_state["_efs_dialog_opened_this_run"] = True
    st.dialog(t("exit_app_dialog_title"))(_render_exit_app_dialog_body)()


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
    st.session_state["_efs_dialog_opened_this_run"] = True
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

    /* C7: Minimal left-aligned clickable dimmed version link right above About */
    [data-testid="stSidebar"] div.stElementContainer[class*="efs_btn_version"] div.stButton button,
    [data-testid="stSidebar"] div[class*="efs_btn_version"] button {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 2px 4px !important;
        min-height: 24px !important;
        height: auto !important;
        margin-top: 2rem !important;
        margin-bottom: 2px !important;
        justify-content: flex-start !important;
        text-align: left !important;
        color: var(--efs-dim) !important;
        font-size: 0.78rem !important;
        font-weight: 500 !important;
        width: auto !important;
    }
    [data-testid="stSidebar"] div[class*="efs_btn_version"] button:hover {
        background: transparent !important;
        border: none !important;
        color: var(--efs-text-2) !important;
        text-decoration: underline !important;
    }
    [data-testid="stSidebar"] div[class*="efs_btn_version"] button > div,
    [data-testid="stSidebar"] div[class*="efs_btn_version"] button p {
        justify-content: flex-start !important;
        text-align: left !important;
        font-size: 0.78rem !important;
        margin: 0 !important;
    }

    /* E11.3: Compact sidebar update badge directly under version line */
    [data-testid="stSidebar"] div.stElementContainer[class*="efs_btn_sidebar_update"] div.stButton button,
    [data-testid="stSidebar"] div[class*="efs_btn_sidebar_update"] button {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 2px 4px !important;
        min-height: 22px !important;
        height: auto !important;
        margin-top: 0 !important;
        margin-bottom: 4px !important;
        justify-content: flex-start !important;
        text-align: left !important;
        color: var(--efs-accent) !important;
        font-size: 0.76rem !important;
        font-weight: 600 !important;
        width: auto !important;
    }
    [data-testid="stSidebar"] div[class*="efs_btn_sidebar_update"] button > div,
    [data-testid="stSidebar"] div[class*="efs_btn_sidebar_update"] button p {
        justify-content: flex-start !important;
        text-align: left !important;
        font-size: 0.76rem !important;
        color: var(--efs-accent) !important;
        margin: 0 !important;
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
        lv_help = t("nav_long_video_help")
        has_open_project = bool(st.query_params.get("project") or st.session_state.get("efs_active_project_id"))

        def _reset_lv_project_cb() -> None:
            try:
                from app.services.longform.ui.components import clear_project_state
                clear_project_state()
            except Exception:
                st.query_params.pop("project", None)
                st.query_params.pop("step", None)
                st.session_state.pop("efs_active_project_id", None)

        lv_rendered = False
        if is_lv and has_open_project:
            # Clicking "Video Panjang" inside an open project MUST return to Project List cleanly (A1.4)
            st.button(
                lv_label,
                key="efs_btn_lv_home_reset",
                icon=":material/movie:",
                type="secondary",
                use_container_width=True,
                help=lv_help,
                on_click=_reset_lv_project_cb,
            )
            lv_rendered = True
        else:
            for path in ["pages/1_🎬_Long_Video.py", "1_🎬_Long_Video.py", "webui/pages/1_🎬_Long_Video.py"]:
                try:
                    st.page_link(path, label=lv_label, icon=":material/movie:", help=lv_help)
                    lv_rendered = True
                    break
                except Exception:
                    continue

        if not lv_rendered:
            if st.button(
                lv_label,
                key=f"efs_btn_lv_{current_page}",
                icon=":material/movie:",
                type="primary" if is_lv else "secondary",
                use_container_width=True,
                help=lv_help,
                on_click=_reset_lv_project_cb,
            ):
                if not is_lv:
                    try:
                        st.switch_page("pages/1_🎬_Long_Video.py")
                    except Exception:
                        pass

        is_main = (current_page == "main")
        main_label = f"**{t('nav_short_video')}**" if is_main else t("nav_short_video")
        main_help = t("nav_short_video_help")
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
                main_label,
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

        # 3.4 Bantuan / Help
        if st.button(
            t("nav_help"),
            key=f"efs_btn_help_{current_page}",
            icon=":material/help:",
            use_container_width=True,
            help=t("nav_help_tooltip"),
        ):
            show_help_dialog()

        # 4. Compact footer: clickable dimmed version line + update badge + Tentang/About button (C7, E11.3)
        if st.button(
            f"v{APP_VERSION}",
            key=f"efs_btn_version_{current_page}",
            help=t("sidebar_version_help"),
        ):
            show_about_dialog()

        try:
            from app.services.longform import update_checker as _sb_uc
            from app.services.branding.lifecycle_ui import show_update_release_dialog

            _sb_uc.start_background_update_check()
            _sb_upd = _sb_uc.get_active_update_banner(ignore_session_dismiss=True)
            if _sb_upd and _sb_upd.get("remote_version"):
                if st.button(
                    t("sidebar_update_badge", ver=_sb_upd["remote_version"]),
                    key=f"efs_btn_sidebar_update_{current_page}",
                    help=t("sidebar_update_badge_help"),
                ):
                    show_update_release_dialog(_sb_upd)
        except Exception:
            pass

        if st.button(
            t("nav_about"),
            key=f"efs_btn_about_{current_page}",
            icon=":material/info:",
            use_container_width=True,
            help=t("nav_about_help"),
        ):
            show_about_dialog()

        if st.button(
            t("nav_exit_app"),
            key=f"efs_btn_exit_app_{current_page}",
            icon=":material/power_settings_new:",
            use_container_width=True,
            help=t("nav_exit_app_help"),
        ):
            show_exit_app_dialog()
