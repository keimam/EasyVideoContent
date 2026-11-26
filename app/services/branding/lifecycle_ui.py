"""Studio lifecycle UI: Update Banner (E2), First-Run Wizard (E1/E3), Settings controls, and Credits Viewer (E5)."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st
from loguru import logger

from app.services.branding.constants import (
    APP_VERSION,
    CAPCUT_DOWNLOAD_URL,
    COVERR_API_URL,
    OPENROUTER_KEYS_URL,
    PIXABAY_API_DOCS_URL,
)
from app.services.longform import first_run, platform_utils, update_checker
from app.services.branding.language import (
    ensure_ui_language_initialized,
    on_language_widget_change,
    set_ui_language,
    to_display_name,
    to_wizard_label,
)
from app.services.longform.ui.i18n import get_ui_language, t


def format_capcut_status_text(doc_report: Dict[str, Any]) -> str:
    """Return localized CapCut status badge text (`teruji ✓`, `belum diuji`, or not installed) (E3)."""
    kind = str(doc_report.get("status_kind") or "not_installed")
    ver = doc_report.get("installed_version") or "N/A"
    if kind == "tested":
        return t("capcut_status_tested_badge", ver=ver)
    if kind == "untested":
        return t("capcut_status_untested_badge", ver=ver)
    if kind == "unsupported_os":
        return t("capcut_unsupported_os_note", reason=doc_report.get("unsupported_os_reason") or "")
    return t("capcut_status_not_installed")


def _extract_manifest_display(manifest: Dict[str, Any]) -> tuple[str, str, str]:
    """Return `(released, localized_notes, download_url)` from update manifest dict."""
    lang = get_ui_language()
    notes = (
        (manifest.get("notes_id") or manifest.get("notes_en") or "")
        if lang == "id"
        else (manifest.get("notes_en") or manifest.get("notes_id") or "")
    )
    released = str(manifest.get("released") or "").strip()
    download_url = str(manifest.get("download_url") or "").strip()
    return released, notes, download_url


def _render_download_update_button(*, download_url: str, key: str) -> None:
    if st.button(
        t("btn_update_download"),
        type="primary",
        key=key,
        width="stretch",
        disabled=not bool(download_url),
    ):
        platform_utils.open_url_in_browser(download_url)
        st.toast(t("update_opening_url", url=download_url))


def render_update_banner(*, key_prefix: str = "home") -> None:
    """Start background 24h update check and render actionable update banner when newer version exists (E2)."""
    update_checker.start_background_update_check()
    banner = update_checker.get_active_update_banner()
    if not banner:
        return

    manifest = banner.get("manifest") or {}
    remote_ver = str(banner.get("remote_version") or "").strip()
    if not remote_ver:
        return
    if st.session_state.get("efs_update_dismissed_session") == remote_ver:
        return

    released, notes, download_url = _extract_manifest_display(manifest)

    with st.container(border=True):
        st.markdown(f"#### {t('update_banner_title', version=remote_ver)}")
        if released:
            st.caption(t("update_banner_released", date=released))
        if notes:
            st.markdown(notes)

        col_dl, col_later, col_skip = st.columns([1.4, 1.0, 1.3])
        with col_dl:
            _render_download_update_button(download_url=download_url, key=f"btn_upd_dl_{key_prefix}")
        with col_later:
            if st.button(
                t("btn_update_later"),
                key=f"btn_upd_later_{key_prefix}",
                width="stretch",
            ):
                st.session_state["efs_update_dismissed_session"] = remote_ver
                st.rerun()
        with col_skip:
            if st.button(
                t("btn_update_skip"),
                key=f"btn_upd_skip_{key_prefix}",
                width="stretch",
            ):
                update_checker.skip_update_version(remote_ver)
                st.rerun()


def _render_friendly_update_error(manual_res: Dict[str, Any]) -> None:
    """Display friendly localized error message plus expandable technical details (E11.3)."""
    err_kind = str(manual_res.get("error_kind") or "").strip()
    raw_err = str(manual_res.get("error") or "").strip()
    if err_kind == "http_404":
        st.warning(t("update_err_404"))
    elif err_kind == "offline":
        st.warning(t("update_err_offline"))
    elif err_kind == "no_url":
        st.info(t("update_err_no_url"))
    elif err_kind == "invalid_manifest":
        st.warning(t("update_err_invalid"))
    else:
        st.warning(t("update_check_failed_reason", reason=raw_err))
    if raw_err:
        with st.expander(t("update_err_tech_details"), expanded=False):
            st.code(raw_err, language="text")


def render_update_check_controls(*, key_prefix: str = "settings", include_wizard_rerun: bool = False) -> None:
    """Shared auto-check toggle + Check for updates now + optional Wizard re-run controls (E1, E2, E11.3)."""
    state = update_checker.read_update_state()
    auto_enabled = bool(state.get("auto_check_enabled", True))
    new_auto = st.checkbox(
        t("settings_auto_update_check"),
        value=auto_enabled,
        key=f"chk_auto_update_{key_prefix}",
    )
    if new_auto != auto_enabled:
        update_checker.set_auto_check_enabled(new_auto)

    if include_wizard_rerun:
        col_chk, col_wiz = st.columns(2)
        with col_chk:
            if st.button(
                t("btn_check_updates_now"),
                key=f"btn_check_updates_{key_prefix}",
                width="stretch",
            ):
                res = update_checker.check_for_updates(force=True)
                if res.get("has_update"):
                    st.session_state.pop("efs_update_dismissed_session", None)
                st.session_state["efs_manual_update_result"] = res
        with col_wiz:
            if st.button(
                t("btn_run_wizard_again"),
                key=f"btn_rerun_wizard_{key_prefix}",
                width="stretch",
            ):
                first_run.reset_setup_marker()
                st.session_state["efs_wizard_skipped_session"] = False
                st.session_state["efs_wizard_force_open"] = True
                st.session_state["efs_wizard_step"] = 1
                st.session_state["settings_dialog_open"] = False
                try:
                    st.switch_page("Main.py")
                except Exception:
                    st.rerun()
    else:
        if st.button(
            t("btn_check_updates_now"),
            key=f"btn_check_updates_{key_prefix}",
            width="stretch",
        ):
            res = update_checker.check_for_updates(force=True)
            if res.get("has_update"):
                st.session_state.pop("efs_update_dismissed_session", None)
            st.session_state["efs_manual_update_result"] = res

    manual_res = st.session_state.get("efs_manual_update_result")
    if isinstance(manual_res, dict) and manual_res.get("checked_now"):
        if manual_res.get("error"):
            _render_friendly_update_error(manual_res)
        elif manual_res.get("has_update"):
            remote_ver = str(manual_res.get("remote_version") or "").strip()
            manifest = manual_res.get("manifest") or {}
            released, notes, download_url = _extract_manifest_display(manifest)
            st.info(t("update_available_msg", ver=remote_ver))
            if released:
                st.caption(t("update_banner_released", date=released))
            if notes:
                st.markdown(notes)
            _render_download_update_button(
                download_url=download_url,
                key=f"btn_upd_manual_dl_{key_prefix}",
            )
        else:
            st.success(t("update_up_to_date", ver=APP_VERSION))


def _render_update_release_dialog_body(banner: Dict[str, Any]) -> None:
    """Modal body showing release notes, Download, and Skip buttons from sidebar update badge (E11.3)."""
    manifest = banner.get("manifest") or {}
    remote_ver = str(banner.get("remote_version") or "").strip()
    released, notes, download_url = _extract_manifest_display(manifest)
    if released:
        st.caption(t("update_banner_released", date=released))
    if notes:
        st.markdown(notes)
    st.divider()
    c_dl, c_skip, c_close = st.columns(3)
    with c_dl:
        _render_download_update_button(download_url=download_url, key="btn_upd_dlg_download")
    with c_skip:
        if st.button(t("btn_update_skip"), key="btn_upd_dlg_skip", width="stretch"):
            update_checker.skip_update_version(remote_ver)
            st.rerun()
    with c_close:
        if st.button(t("dialog_close"), key="btn_upd_dlg_close", width="stretch"):
            st.rerun()


def show_update_release_dialog(banner: Dict[str, Any]) -> None:
    """Open release notes + download dialog when sidebar update badge is clicked (E11.3)."""
    st.session_state["_efs_dialog_opened_this_run"] = True
    remote_ver = str(banner.get("remote_version") or "").strip()
    st.dialog(t("update_dialog_title", ver=remote_ver))(_render_update_release_dialog_body)(banner)


def render_settings_studio_controls(*, key_prefix: str = "settings") -> None:
    """Render Language selector, Wizard re-run & App Update controls inside Settings General tab (E1, E2, E9, E11)."""
    ensure_ui_language_initialized()
    st.divider()
    st.markdown(f"#### {t('settings_studio_heading')}")

    cur_lang = get_ui_language()
    if st.session_state.get("settings_ui_language_select") not in ("id", "en"):
        st.session_state["settings_ui_language_select"] = cur_lang
    st.selectbox(
        t("settings_ui_language_label"),
        options=["id", "en"],
        format_func=to_wizard_label,
        help=t("settings_ui_language_help"),
        key="settings_ui_language_select",
        on_change=on_language_widget_change,
        args=("settings_ui_language_select",),
    )

    try:
        from app.services.longform.config import (
            get_network_profile_settings,
            normalize_network_profile,
            save_network_profile,
        )

        cur_prof = normalize_network_profile(get_network_profile_settings().get("profile"))
        np_key = f"{key_prefix}_network_profile_select"
        if st.session_state.get(np_key) not in ("normal", "lambat"):
            st.session_state[np_key] = cur_prof

        def _on_network_profile_change() -> None:
            chosen = normalize_network_profile(st.session_state.get(np_key))
            save_network_profile(chosen)

        st.selectbox(
            t("settings_network_profile_label"),
            options=["normal", "lambat"],
            format_func=lambda opt: t("settings_network_profile_lambat") if opt == "lambat" else t("settings_network_profile_normal"),
            help=t("settings_network_profile_help"),
            key=np_key,
            on_change=_on_network_profile_change,
        )
    except Exception as exc:
        logger.debug(f"Network profile setting control warning: {exc}")

    try:
        from app.config import config as app_config
        from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider
        from app.services.longform.config import (
            DEFAULT_FAILOVER_CANDIDATES,
            get_llm_failover_chain,
            save_llm_failover_chain,
        )
        from app.services.longform.llm_adapter import (
            get_provider_chain_status,
            sanitize_error,
            test_all_llm_providers,
        )

        with st.container(border=True):
            st.markdown(f"**🔄 {t('settings_failover_heading')}**")
            st.caption(t("settings_failover_help"))

            active_primary = str(app_config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID).lower().strip()
            candidate_pool: list[str] = []
            for cid in (*DEFAULT_FAILOVER_CANDIDATES, "openai", "moonshot", "zhipu", "deepseek", "groq", "gemini", "openrouter"):
                if cid and cid != active_primary and cid not in candidate_pool:
                    candidate_pool.append(cid)

            saved_chain = [c for c in get_llm_failover_chain() if c != active_primary]
            for extra_c in saved_chain:
                if extra_c not in candidate_pool:
                    candidate_pool.append(extra_c)

            def _fmt_prov(pid: str) -> str:
                pdef = get_llm_provider(pid)
                return pdef.default_label if pdef else pid

            ms_key = f"{key_prefix}_llm_failover_multiselect"
            if ms_key not in st.session_state or not isinstance(st.session_state.get(ms_key), list):
                st.session_state[ms_key] = list(saved_chain)

            def _on_failover_change() -> None:
                chosen = list(st.session_state.get(ms_key) or [])
                save_llm_failover_chain(chosen)

            st.multiselect(
                t("settings_failover_order_label"),
                options=candidate_pool,
                format_func=_fmt_prov,
                key=ms_key,
                on_change=_on_failover_change,
            )

            statuses = get_provider_chain_status()
            if statuses:
                st.caption(t("settings_failover_status_title"))
                for idx_s, st_item in enumerate(statuses):
                    pid_s = st_item["provider"]
                    lbl_s = str(st_item.get("label") or pid_s)
                    mdl_s = str(st_item.get("model") or "")
                    code_s = str(st_item.get("status") or "no_key")
                    if code_s == "ready":
                        icon = "✅"
                        detail = f"{t('settings_failover_ready')} · `{mdl_s}`" if mdl_s else t("settings_failover_ready")
                        if st_item.get("format_warning"):
                            detail = f"{detail} ({t('settings_failover_format_warn_badge')})"
                    elif code_s in ("paused_quota", "paused_auth", "paused"):
                        icon = "⏸"
                        detail = t("settings_failover_paused", until=st_item.get("pause_label") or "-")
                        if code_s == "paused_auth" and st_item.get("message"):
                            detail = f"{detail} · {st_item.get('message')}"
                    elif code_s == "needs_model":
                        icon = "⚠️"
                        detail = t("settings_failover_needs_model")
                    elif code_s == "invalid_key":
                        icon = "⚠️"
                        detail = (
                            t("settings_failover_invalid_key_gemini")
                            if ("gemini" in pid_s or "google" in pid_s)
                            else t("settings_failover_invalid_key")
                        )
                    else:
                        icon = "⚠️"
                        detail = t("settings_failover_no_key")
                    role_tag = f" {t('settings_failover_primary_badge')}" if idx_s == 0 else ""
                    prefix_s = "📌" if idx_s == 0 else f"{idx_s}."
                    st.markdown(f"{prefix_s} {icon} **{lbl_s}**{role_tag} — {detail}")

            test_res_key = f"{key_prefix}_failover_test_results"
            if st.button(t("settings_failover_test_all_btn"), key=f"{key_prefix}_btn_test_all_llm"):
                with st.spinner(t("settings_failover_test_spinner")):
                    st.session_state[test_res_key] = test_all_llm_providers()

            for tr in st.session_state.get(test_res_key) or []:
                if not isinstance(tr, dict):
                    continue
                if tr.get("ok"):
                    st.success(
                        t(
                            "settings_failover_test_ok",
                            label=tr.get("label") or tr.get("provider"),
                            model=tr.get("model") or "-",
                            latency=tr.get("latency", 0.0),
                        )
                    )
                else:
                    tr_status = str(tr.get("status") or "")
                    clean_m = sanitize_error(str(tr.get("message") or ""))[:140]
                    if tr_status == "invalid_key_format":
                        disp_msg = t("settings_failover_test_fmt_invalid", msg=clean_m)
                    elif tr_status == "auth_rejected":
                        disp_msg = t("settings_failover_test_auth_rejected", msg=clean_m)
                    else:
                        disp_msg = clean_m
                    st.warning(
                        t(
                            "settings_failover_test_fail",
                            label=tr.get("label") or tr.get("provider"),
                            msg=disp_msg,
                        )
                    )
    except Exception as exc:
        logger.debug(f"Failover settings control warning: {exc}")

    render_update_check_controls(key_prefix=key_prefix, include_wizard_rerun=True)


def _apply_wizard_language(lang_code: str) -> None:
    set_ui_language(lang_code, persist=True)


def is_wizard_active() -> bool:
    """Return True when the First-Run Setup Wizard owns the viewport (E1, E9.2)."""
    if st.session_state.get("efs_wizard_force_open", False):
        return True
    if st.session_state.get("efs_wizard_skipped_session", False):
        return False
    return not first_run.is_setup_completed()


def _should_show_wizard() -> bool:
    return is_wizard_active()


def _spec_badge_icon(status: str) -> str:
    if status == "green":
        return "🟢"
    if status == "yellow":
        return "🟡"
    return "🔴"


def render_first_run_wizard(*, key_prefix: str = "home") -> bool:
    """Render the 6-step First-Run Setup Wizard inline card when active; returns True if shown (E1)."""
    if not _should_show_wizard():
        return False

    step = int(st.session_state.get("efs_wizard_step", 1) or 1)
    step = max(1, min(6, step))
    st.session_state["efs_wizard_step"] = step

    with st.container(border=True):
        col_title, col_skip = st.columns([4, 1])
        with col_title:
            st.markdown(f"### ✨ {t('wizard_title')} — {t('wizard_step_counter', cur=step, total=6)}")
            st.caption(t("wizard_subtitle"))
        with col_skip:
            st.write("")
            if st.button(t("wizard_btn_skip"), key=f"btn_wiz_skip_{key_prefix}", width="stretch"):
                first_run.mark_setup_completed(skipped=True)
                st.session_state["efs_wizard_skipped_session"] = True
                st.session_state.pop("efs_wizard_force_open", None)
                try:
                    st.switch_page("pages/1_🎬_Long_Video.py")
                except Exception:
                    st.rerun()

        st.progress(step / 6.0)

        step_labels = [
            t("wizard_step1_name"),
            t("wizard_step2_name"),
            t("wizard_step3_name"),
            t("wizard_step4_name"),
            t("wizard_step5_name"),
            t("wizard_step6_name"),
        ]
        strip_html = " · ".join(
            f"<strong>{html.escape(lbl)}</strong>" if (idx + 1) == step else html.escape(lbl)
            for idx, lbl in enumerate(step_labels)
        )
        st.markdown(f'<div style="font-size: 0.85rem; color: var(--efs-muted); margin-bottom: 0.65rem;">{strip_html}</div>', unsafe_allow_html=True)

        # --- Step 1: Display Language ---
        if step == 1:
            ensure_ui_language_initialized()
            st.markdown(f"#### {t('wizard_lang_heading')}")
            st.caption(t("wizard_lang_desc"))
            cur_lang = get_ui_language()
            wiz_lang_key = f"wiz_lang_radio_{key_prefix}"
            if st.session_state.get(wiz_lang_key) not in ("id", "en"):
                st.session_state[wiz_lang_key] = cur_lang
            st.radio(
                t("wizard_lang_label"),
                options=["id", "en"],
                format_func=to_wizard_label,
                key=wiz_lang_key,
                on_change=on_language_widget_change,
                args=(wiz_lang_key,),
                horizontal=True,
            )

        # --- Step 2: System Check ---
        elif step == 2:
            col_s_hdr, col_s_btn = st.columns([3, 1])
            with col_s_hdr:
                st.markdown(f"#### {t('wizard_sys_heading')}")
                st.caption(t("wizard_sys_desc"))
            with col_s_btn:
                if st.button(t("wizard_btn_recheck_sys"), key=f"btn_wiz_recheck_{key_prefix}", width="stretch"):
                    st.session_state.pop("efs_wizard_specs", None)
                    st.rerun()

            specs = st.session_state.get("efs_wizard_specs")
            if not isinstance(specs, dict):
                from app.services.longform.system_specs import evaluate_system_specs

                specs = evaluate_system_specs()
                st.session_state["efs_wizard_specs"] = specs

            overall = specs.get("overall", "yellow")
            if overall == "green":
                st.success(t("wizard_sys_overall_green"))
            elif overall == "red":
                st.error(t("wizard_sys_overall_red"))
            else:
                st.warning(t("wizard_sys_overall_yellow"))

            for item in specs.get("checks", []):
                icon = _spec_badge_icon(str(item.get("status") or "yellow"))
                k = item.get("key")
                if k == "ram":
                    st.markdown(f"{icon} {t('wizard_spec_ram', val=item.get('value', 0.0))}")
                elif k == "cpu":
                    st.markdown(
                        f"{icon} {t('wizard_spec_cpu', model=item.get('model') or '-', phys=item.get('physical', 1), val=item.get('value', 1))}"
                    )
                elif k == "disk":
                    st.markdown(f"{icon} {t('wizard_spec_disk', path=item.get('path') or '-', val=item.get('value', 0.0))}")
                elif k == "ffmpeg":
                    if item.get("available"):
                        ver_short = str(item.get("version") or "ffmpeg").split(" Copyright")[0]
                        st.markdown(f"{icon} {t('wizard_spec_ffmpeg_ok', ver=ver_short)}")
                    else:
                        st.markdown(f"{icon} {t('wizard_spec_ffmpeg_missing')}")
                elif k == "internet":
                    st.markdown(f"{icon} {t('wizard_spec_net_ok' if item.get('available') else 'wizard_spec_net_off')}")
                elif k == "capcut":
                    cc_rep = item.get("report") or {}
                    cc_txt = format_capcut_status_text(cc_rep)
                    st.markdown(f"{icon} {t('wizard_spec_capcut_label', text=cc_txt)}")
                    if cc_rep.get("status_kind") == "not_installed":
                        st.caption(t("capcut_download_guide", url=cc_rep.get("download_url") or CAPCUT_DOWNLOAD_URL))

        # --- Step 3: AI Script (OpenRouter) ---
        elif step == 3:
            from app.config import config
            from app.services.longform.llm_adapter import (
                OPENROUTER_SAFE_DEFAULT_MODEL,
                fetch_openrouter_key_quota,
                test_llm_connection,
            )

            st.markdown(f"#### {t('wizard_or_heading')}")
            st.markdown(t("wizard_or_desc"))
            st.markdown(t("wizard_or_signup_link", url=OPENROUTER_KEYS_URL))
            st.info(t("wizard_or_free_limits"))

            cur_or_key = str(config.app.get("openrouter_api_key") or "").strip()
            new_or_key = st.text_input(
                t("wizard_or_key_label"),
                value=cur_or_key,
                type="password",
                placeholder=t("wizard_or_key_placeholder"),
                key=f"wiz_or_key_{key_prefix}",
            )
            col_sv, col_tc, col_qt = st.columns(3)
            with col_sv:
                if st.button(t("wizard_btn_save_or_key"), key=f"btn_wiz_save_or_{key_prefix}", width="stretch"):
                    cleaned_or = new_or_key.strip()
                    config.app["openrouter_api_key"] = cleaned_or
                    if cleaned_or:
                        config.app["llm_provider"] = "openrouter"
                        if not str(config.app.get("openrouter_model_name") or "").strip():
                            config.app["openrouter_model_name"] = OPENROUTER_SAFE_DEFAULT_MODEL
                    config.save_config()
                    st.success(t("wizard_or_key_saved"))
            with col_tc:
                if st.button(t("wizard_btn_test_or"), key=f"btn_wiz_test_or_{key_prefix}", width="stretch"):
                    if new_or_key.strip() != cur_or_key:
                        config.app["openrouter_api_key"] = new_or_key.strip()
                        if new_or_key.strip():
                            config.app["llm_provider"] = "openrouter"
                            if not str(config.app.get("openrouter_model_name") or "").strip():
                                config.app["openrouter_model_name"] = OPENROUTER_SAFE_DEFAULT_MODEL
                        config.save_config()
                    ok, msg, lat = test_llm_connection()
                    st.session_state["efs_wiz_or_test"] = (ok, msg, lat)
            with col_qt:
                if st.button(t("wizard_btn_quota_or"), key=f"btn_wiz_quota_or_{key_prefix}", width="stretch"):
                    if new_or_key.strip() != cur_or_key:
                        config.app["openrouter_api_key"] = new_or_key.strip()
                        config.save_config()
                    q_data, q_err = fetch_openrouter_key_quota()
                    st.session_state["efs_wiz_or_quota"] = (q_data, q_err)

            test_res = st.session_state.get("efs_wiz_or_test")
            if isinstance(test_res, tuple) and len(test_res) == 3:
                ok, msg, lat = test_res
                if ok:
                    st.success(t("wizard_or_test_ok", latency=lat, msg=msg))
                else:
                    st.error(t("wizard_or_test_fail", msg=msg))

            quota_res = st.session_state.get("efs_wiz_or_quota")
            if isinstance(quota_res, tuple) and len(quota_res) == 2:
                q_data, q_err = quota_res
                if q_err:
                    st.warning(t("or_quota_error", error=q_err))
                elif isinstance(q_data, dict):
                    tier_lbl = "Free Tier" if q_data.get("is_free_tier") else "Standard"
                    rem_val = q_data.get("limit_remaining")
                    lim_rem_str = "∞" if rem_val is None else f"{rem_val}"
                    u_daily = q_data.get("usage_daily")
                    u_daily_str = "0" if u_daily is None else f"{u_daily}"
                    st.info(
                        t(
                            "or_quota_summary",
                            tier=tier_lbl,
                            usage_daily=u_daily_str,
                            limit_remaining=lim_rem_str,
                        )
                    )

        # --- Step 4: Footage Sources ---
        elif step == 4:
            from app.config import config

            st.markdown(f"#### {t('wizard_sources_heading')}")
            st.success(t("wizard_sources_builtin_info"))

            cur_pix = config.app.get("pixabay_api_keys") or []
            cur_pix_str = ", ".join(str(x) for x in cur_pix) if isinstance(cur_pix, list) else str(cur_pix or "")
            cur_cov_str = str(config.app.get("coverr_api_key") or "").strip()

            col_p, col_c = st.columns(2)
            with col_p:
                new_pix = st.text_input(
                    t("wizard_pixabay_label"),
                    value=cur_pix_str,
                    type="password",
                    key=f"wiz_pix_key_{key_prefix}",
                )
                st.caption(t("wizard_pixabay_link", url=PIXABAY_API_DOCS_URL))
            with col_c:
                new_cov = st.text_input(
                    t("wizard_coverr_label"),
                    value=cur_cov_str,
                    type="password",
                    key=f"wiz_cov_key_{key_prefix}",
                )
                st.caption(t("wizard_coverr_link", url=COVERR_API_URL))

            if st.button(t("wizard_btn_save_sources"), key=f"btn_wiz_save_src_{key_prefix}"):
                pix_list = [x.strip() for x in new_pix.split(",") if x.strip()]
                config.app["pixabay_api_keys"] = pix_list
                config.app["coverr_api_key"] = new_cov.strip()
                config.save_config()
                st.success(t("wizard_sources_saved"))

        # --- Step 5: CapCut ---
        elif step == 5:
            from app.services.longform.capcut.detector import get_capcut_doctor_report

            st.markdown(f"#### {t('wizard_capcut_heading')}")
            cc_rep = get_capcut_doctor_report()
            cc_txt = format_capcut_status_text(cc_rep)
            kind = cc_rep.get("status_kind")
            if kind == "tested":
                st.success(cc_txt)
            elif kind == "untested":
                st.warning(cc_txt)
            else:
                st.info(cc_txt)
                st.markdown(t("capcut_download_guide", url=cc_rep.get("download_url") or CAPCUT_DOWNLOAD_URL))

            detected_dir = str(
                first_run.get_custom_capcut_drafts_dir()
                or cc_rep.get("draft_folder")
                or platform_utils.get_capcut_drafts_dir()
                or ""
            )
            manual_cc = st.text_input(
                t("wizard_capcut_folder_label"),
                value=detected_dir,
                help=t("wizard_capcut_folder_help"),
                key=f"wiz_capcut_dir_{key_prefix}",
            )
            if st.button(t("wizard_btn_save_capcut_dir"), key=f"btn_wiz_save_cc_{key_prefix}"):
                saved_p = first_run.set_custom_capcut_drafts_dir(manual_cc)
                if saved_p and saved_p.is_dir():
                    st.success(t("wizard_capcut_dir_saved", path=str(saved_p)))
                elif saved_p:
                    st.warning(t("wizard_capcut_dir_warn", path=str(saved_p)))

        # --- Step 6: Finish & Starter Sample Project ---
        elif step == 6:
            from app.config import config
            from app.services.longform.capcut.detector import get_capcut_doctor_report

            st.markdown(f"#### 🎉 {t('wizard_finish_heading')}")
            st.write(t("wizard_finish_desc"))

            cur_lang = get_ui_language()
            has_or = bool(str(config.app.get("openrouter_api_key") or "").strip())
            has_pix = bool(config.app.get("pixabay_api_keys"))
            cc_rep = get_capcut_doctor_report()

            st.markdown(
                "\n".join([
                    t("wizard_finish_summary_lang", language_label=to_display_name(cur_lang)),
                    t("wizard_finish_summary_or", status=t("wizard_configured") if has_or else t("wizard_skipped_optional")),
                    t("wizard_finish_summary_sources", extra=" + Pixabay" if has_pix else ""),
                    t("wizard_finish_summary_capcut", status=format_capcut_status_text(cc_rep)),
                ])
            )

            col_s1, col_s2 = st.columns(2)
            with col_s1:
                if st.button(
                    t("wizard_btn_create_sample"),
                    type="primary",
                    key=f"btn_wiz_finish_sample_{key_prefix}",
                    width="stretch",
                ):
                    pid, _ = first_run.create_starter_sample_project(lang=cur_lang)
                    first_run.mark_setup_completed(skipped=False, extra={"created_sample_project": pid})
                    st.session_state.pop("efs_wizard_force_open", None)
                    st.success(t("wizard_sample_created", id=pid))
                    try:
                        st.switch_page("pages/1_🎬_Long_Video.py", query_params={"project": pid})
                    except Exception:
                        st.query_params["project"] = pid
                        st.rerun()
            with col_s2:
                if st.button(
                    t("wizard_btn_finish_only"),
                    key=f"btn_wiz_finish_only_{key_prefix}",
                    width="stretch",
                ):
                    first_run.mark_setup_completed(skipped=False)
                    st.session_state.pop("efs_wizard_force_open", None)
                    try:
                        st.switch_page("pages/1_🎬_Long_Video.py")
                    except Exception:
                        st.rerun()

        # --- Footer Step Navigation (Back / Next) ---
        st.divider()
        col_prev, col_mid, col_next = st.columns([1, 2, 1])
        with col_prev:
            if step > 1:
                if st.button(t("wizard_btn_prev"), key=f"btn_wiz_prev_{key_prefix}", width="stretch"):
                    st.session_state["efs_wizard_step"] = step - 1
                    st.rerun()
        with col_next:
            if step < 6:
                if st.button(t("wizard_btn_next"), type="primary", key=f"btn_wiz_next_{key_prefix}", width="stretch"):
                    st.session_state["efs_wizard_step"] = step + 1
                    st.rerun()

    return True


def render_studio_lifecycle_ui(*, key_prefix: str = "home") -> None:
    """Render background Update Banner (E2) and First-Run Wizard (E1) cleanly at top of studio page."""
    render_update_banner(key_prefix=key_prefix)
    render_first_run_wizard(key_prefix=key_prefix)


def _render_credits_dialog_body(project_dir: Path, project_id: str) -> None:
    """Dialog body showing `export/credits.md` with Copy & Download actions (E5)."""
    credits_path = Path(project_dir) / "export" / "credits.md"
    st.caption(t("credits_file_path_caption", path=str(credits_path)))

    if not credits_path.is_file():
        st.warning(t("credits_file_missing"))
        if st.button(t("dialog_close"), key=f"btn_credits_close_missing_{project_id}", width="stretch"):
            st.rerun()
        return

    content = credits_path.read_text(encoding="utf-8", errors="replace")
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        if st.button(t("credits_btn_copy"), type="primary", key=f"btn_credits_copy_{project_id}", width="stretch"):
            copied = platform_utils.copy_text_to_clipboard(content)
            st.session_state[f"_credits_copy_state_{project_id}"] = "ok" if copied else "fallback"
    with col_c2:
        st.download_button(
            t("credits_btn_download"),
            data=content,
            file_name=f"{project_id}_credits.md",
            mime="text/markdown",
            width="stretch",
            key=f"btn_credits_dl_{project_id}",
        )

    copy_state = st.session_state.pop(f"_credits_copy_state_{project_id}", None)
    if copy_state == "ok":
        st.success(t("credits_copied_ok"))
    elif copy_state == "fallback":
        st.info(t("credits_copy_fallback"))
        st.code(content, language="markdown")

    with st.container(border=True):
        st.markdown(content)


def should_show_short_video_tour() -> bool:
    """Return True only when setup wizard is finished and no dialog is active (E8)."""
    if not first_run.is_setup_completed():
        return False
    if st.session_state.get("efs_wizard_force_open", False):
        return False
    if st.session_state.get("settings_dialog_open", False):
        return False
    if st.session_state.get("_efs_dialog_opened_this_run", False):
        return False
    if st.session_state.get("_efs_settings_library_only", False):
        return False
    return True


def run_short_video_onboarding_tour(tour_cls: Any, *, key: str = "efs-onboarding-v1") -> bool:
    """Render Short Video guided tour using i18n t() strings only after wizard & without dialog overlap (E8)."""
    if not should_show_short_video_tour():
        return False

    steps = [
        tour_cls.bind(
            "open_settings_dialog_button",
            title=t("tour_step1_title"),
            desc=t("tour_step1_desc"),
            side="bottom",
            align="end",
        ),
        tour_cls.bind(
            "main_settings_grid",
            title=t("tour_step2_title"),
            desc=t("tour_step2_desc"),
            side="top",
            align="center",
        ),
        tour_cls.bind(
            "generate_video_button",
            title=t("tour_step3_title"),
            desc=t("tour_step3_desc"),
            side="top",
            align="center",
        ),
    ]

    prev_txt = html.escape(t("tour_prev"))
    next_txt = html.escape(t("tour_next"))
    done_txt = html.escape(t("tour_done"))
    for idx, step in enumerate(steps):
        step.popover["prevBtnText"] = f"&larr; {prev_txt}"
        step.popover["progressText"] = f"{idx + 1} / {len(steps)}"
        step.popover["doneBtnText" if idx == len(steps) - 1 else "nextBtnText"] = (
            done_txt if idx == len(steps) - 1 else f"{next_txt} &rarr;"
        )

    tour = tour_cls(
        steps=steps,
        key=key,
        show_progress=True,
        animate=True,
        overlay_opacity=0.55,
        one_time_tour=True,
    )
    auto_start_key = f"{key}-auto-started"
    if not st.session_state.get(auto_start_key, False):
        st.session_state[auto_start_key] = True
        tour.start()
    return True


def show_credits_dialog(project_dir: Path, project_id: str) -> None:
    """Open localized in-app `credits.md` attribution viewer dialog (E5)."""
    st.session_state["_efs_dialog_opened_this_run"] = True
    st.dialog(t("credits_dialog_title"), width="large")(_render_credits_dialog_body)(project_dir, project_id)
