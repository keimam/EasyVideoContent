"""Studio lifecycle UI: Update Banner (E2), First-Run Wizard (E1/E3), Settings controls, and Credits Viewer (E5)."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit as st

from app.services.branding.constants import (
    APP_VERSION,
    CAPCUT_DOWNLOAD_URL,
    COVERR_API_URL,
    OPENROUTER_KEYS_URL,
    PIXABAY_API_DOCS_URL,
)
from app.services.longform import first_run, platform_utils, update_checker
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

    lang = get_ui_language()
    notes = (
        (manifest.get("notes_id") or manifest.get("notes_en") or "")
        if lang == "id"
        else (manifest.get("notes_en") or manifest.get("notes_id") or "")
    )
    released = str(manifest.get("released") or "").strip()
    download_url = str(manifest.get("download_url") or "").strip()

    with st.container(border=True):
        st.markdown(f"#### {t('update_banner_title', version=remote_ver)}")
        if released:
            st.caption(t("update_banner_released", date=released))
        if notes:
            st.markdown(notes)

        col_dl, col_later, col_skip = st.columns([1.4, 1.0, 1.3])
        with col_dl:
            if st.button(
                t("btn_update_download"),
                type="primary",
                key=f"btn_upd_dl_{key_prefix}",
                use_container_width=True,
                disabled=not bool(download_url),
            ):
                platform_utils.open_url_in_browser(download_url)
                st.toast(t("update_opening_url", url=download_url))
        with col_later:
            if st.button(
                t("btn_update_later"),
                key=f"btn_upd_later_{key_prefix}",
                use_container_width=True,
            ):
                st.session_state["efs_update_dismissed_session"] = remote_ver
                st.rerun()
        with col_skip:
            if st.button(
                t("btn_update_skip"),
                key=f"btn_upd_skip_{key_prefix}",
                use_container_width=True,
            ):
                update_checker.skip_update_version(remote_ver)
                st.rerun()


def render_settings_studio_controls(*, key_prefix: str = "settings") -> None:
    """Render Wizard re-run & App Update controls inside Settings General tab (E1, E2)."""
    st.divider()
    st.markdown(f"#### {t('settings_studio_heading')}")

    state = update_checker.read_update_state()
    auto_enabled = bool(state.get("auto_check_enabled", True))
    new_auto = st.checkbox(
        t("settings_auto_update_check"),
        value=auto_enabled,
        key=f"chk_auto_update_{key_prefix}",
    )
    if new_auto != auto_enabled:
        update_checker.set_auto_check_enabled(new_auto)

    col_chk, col_wiz = st.columns(2)
    with col_chk:
        if st.button(
            t("btn_check_updates_now"),
            key=f"btn_check_updates_{key_prefix}",
            use_container_width=True,
        ):
            res = update_checker.check_for_updates(force=True)
            st.session_state["efs_manual_update_result"] = res
    with col_wiz:
        if st.button(
            t("btn_run_wizard_again"),
            key=f"btn_rerun_wizard_{key_prefix}",
            use_container_width=True,
        ):
            first_run.reset_setup_marker()
            st.session_state["efs_wizard_skipped_session"] = False
            st.session_state["efs_wizard_force_open"] = True
            st.session_state["efs_wizard_step"] = 1
            st.session_state["settings_dialog_open"] = False
            st.rerun()

    manual_res = st.session_state.get("efs_manual_update_result")
    if isinstance(manual_res, dict) and manual_res.get("checked_now"):
        if manual_res.get("error"):
            st.warning(t("update_check_offline_note"))
        elif manual_res.get("has_update"):
            st.info(t("update_available_msg", ver=manual_res.get("remote_version")))
        else:
            st.success(t("update_up_to_date", ver=APP_VERSION))


def _apply_wizard_language(lang_code: str) -> None:
    from app.config import config

    norm = "en" if str(lang_code).lower().startswith("en") else "id"
    st.session_state["ui_language"] = norm
    config.ui["language"] = "en" if norm == "en" else "id"
    config.save_config()


def _should_show_wizard() -> bool:
    if st.session_state.get("efs_wizard_force_open", False):
        return True
    if st.session_state.get("efs_wizard_skipped_session", False):
        return False
    return not first_run.is_setup_completed()


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
            if st.button(t("wizard_btn_skip"), key=f"btn_wiz_skip_{key_prefix}", use_container_width=True):
                first_run.mark_setup_completed(skipped=True)
                st.session_state["efs_wizard_skipped_session"] = True
                st.session_state.pop("efs_wizard_force_open", None)
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
            st.markdown(f"#### {t('wizard_lang_heading')}")
            st.caption(t("wizard_lang_desc"))
            cur_lang = get_ui_language()
            lang_opts = ["id", "en"]
            chosen_lang = st.radio(
                t("wizard_lang_label"),
                options=lang_opts,
                index=0 if cur_lang == "id" else 1,
                format_func=lambda x: "Bahasa Indonesia (id)" if x == "id" else "English (en)",
                key=f"wiz_lang_radio_{key_prefix}",
                horizontal=True,
            )
            if chosen_lang != cur_lang:
                _apply_wizard_language(chosen_lang)
                st.rerun()

        # --- Step 2: System Check ---
        elif step == 2:
            col_s_hdr, col_s_btn = st.columns([3, 1])
            with col_s_hdr:
                st.markdown(f"#### {t('wizard_sys_heading')}")
                st.caption(t("wizard_sys_desc"))
            with col_s_btn:
                if st.button(t("wizard_btn_recheck_sys"), key=f"btn_wiz_recheck_{key_prefix}", use_container_width=True):
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
                if st.button(t("wizard_btn_save_or_key"), key=f"btn_wiz_save_or_{key_prefix}", use_container_width=True):
                    cleaned_or = new_or_key.strip()
                    config.app["openrouter_api_key"] = cleaned_or
                    if cleaned_or:
                        config.app["llm_provider"] = "openrouter"
                        if not str(config.app.get("openrouter_model_name") or "").strip():
                            config.app["openrouter_model_name"] = OPENROUTER_SAFE_DEFAULT_MODEL
                    config.save_config()
                    st.success(t("wizard_or_key_saved"))
            with col_tc:
                if st.button(t("wizard_btn_test_or"), key=f"btn_wiz_test_or_{key_prefix}", use_container_width=True):
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
                if st.button(t("wizard_btn_quota_or"), key=f"btn_wiz_quota_or_{key_prefix}", use_container_width=True):
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
                    t("wizard_finish_summary_lang", language_label="Bahasa Indonesia" if cur_lang == "id" else "English"),
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
                    use_container_width=True,
                ):
                    pid, _ = first_run.create_starter_sample_project(lang=cur_lang)
                    first_run.mark_setup_completed(skipped=False, extra={"created_sample_project": pid})
                    st.session_state.pop("efs_wizard_force_open", None)
                    st.query_params["project"] = pid
                    st.success(t("wizard_sample_created", id=pid))
                    try:
                        st.switch_page("pages/1_🎬_Long_Video.py")
                    except Exception:
                        st.rerun()
            with col_s2:
                if st.button(
                    t("wizard_btn_finish_only"),
                    key=f"btn_wiz_finish_only_{key_prefix}",
                    use_container_width=True,
                ):
                    first_run.mark_setup_completed(skipped=False)
                    st.session_state.pop("efs_wizard_force_open", None)
                    st.rerun()

        # --- Footer Step Navigation (Back / Next) ---
        st.divider()
        col_prev, col_mid, col_next = st.columns([1, 2, 1])
        with col_prev:
            if step > 1:
                if st.button(t("wizard_btn_prev"), key=f"btn_wiz_prev_{key_prefix}", use_container_width=True):
                    st.session_state["efs_wizard_step"] = step - 1
                    st.rerun()
        with col_next:
            if step < 6:
                if st.button(t("wizard_btn_next"), type="primary", key=f"btn_wiz_next_{key_prefix}", use_container_width=True):
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
        if st.button(t("dialog_close"), key=f"btn_credits_close_missing_{project_id}", use_container_width=True):
            st.rerun()
        return

    content = credits_path.read_text(encoding="utf-8", errors="replace")
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        if st.button(t("credits_btn_copy"), type="primary", key=f"btn_credits_copy_{project_id}", use_container_width=True):
            copied = platform_utils.copy_text_to_clipboard(content)
            st.session_state[f"_credits_copy_state_{project_id}"] = "ok" if copied else "fallback"
    with col_c2:
        st.download_button(
            t("credits_btn_download"),
            data=content,
            file_name=f"{project_id}_credits.md",
            mime="text/markdown",
            use_container_width=True,
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


def show_credits_dialog(project_dir: Path, project_id: str) -> None:
    """Open localized in-app `credits.md` attribution viewer dialog (E5)."""
    st.dialog(t("credits_dialog_title"), width="large")(_render_credits_dialog_body)(project_dir, project_id)
