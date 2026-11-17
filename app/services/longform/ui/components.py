"""Streamlit UI components for Long Video WebUI (Stage 7A.1, C1-C5).

Implements step-flow UX:
- Beranda: Project cards with clear next step & quick creation form (C4.1, C4.2)
- Halaman Proyek: 5-step workflow stepper (Naskah -> Suara -> Visual -> Render -> CapCut) (C4.3)
- Single adaptive primary action button per step (C4.3)
- Background job monitor with refresh survival and stopped-state resumption (C1)
- Non-jargon, clean human messaging (C4.4)
- URL query-parameter project persistence across browser refresh (C4.5)
"""

from __future__ import annotations

from datetime import datetime
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from loguru import logger

from app.services.longform.hashing import compute_audio_group_hash
from app.services.longform.presets import (
    create_custom_niche_hierarchy,
    delete_custom_preset,
    format_preset_tree,
    get_all_preset_overrides,
    get_available_niches_for_ui,
    get_builtin_presets,
    get_custom_presets,
    get_preset,
    get_preset_directories,
    get_preset_inheritance_chain,
    get_sub_niches_for_ui,
    has_preset_override,
    list_presets,
    save_custom_preset,
    suggest_niche_keywords,
)
from app.services.longform.quality_checker import ScriptQualityChecker
from app.services.longform.schema import ItemStatus, ProjectManifest, Shot
from app.services.longform.script_import import estimate_script_duration, parse_markdown_script
from app.services.longform.status import (
    compute_project_step_status,
    find_latest_capcut_draft,
    get_impactful_preset_diffs,
)
from app.services.longform.store import (
    ProjectLock,
    create_project,
    get_project_dir,
    get_projects_root,
    load_manifest,
    refresh_project_preset,
    save_manifest,
    update_project_script,
)
from app.services.longform.trash import (
    empty_trash,
    get_project_delete_preview,
    list_trashed_projects,
    permanently_delete_trashed_project,
    restore_trashed_project,
    soft_delete_project,
)
from app.services.branding.theme import (
    render_5_segment_progress_bar_html,
    render_badge,
    render_card,
    render_metric_tile,
    render_stepper_card_html,
    render_success_banner,
)
from app.services.longform.llm_adapter import (
    get_llm_readiness,
    list_llm_models,
    set_active_llm_model,
    supports_model_listing,
    test_llm_connection,
)
from app.services.longform.tts_adapter import (
    TTSAdapter,
    clean_narration_text,
    list_configured_tts_providers,
    normalize_tts_provider_id,
    provider_has_word_timings,
)
from app.services.longform.ui import jobs
from app.services.longform.ui.i18n import t
from app.services.longform.ui.security import check_llm_status, mask_api_key, safe_windows_path
from app.services.longform.voice_service import VoiceService, list_available_voices


def _open_mpt_settings_dialog(target_tab: str = "llm") -> None:
    """Trigger MoneyPrinterTurbo's native Settings dialog on the active page at the requested tab."""
    st.session_state["settings_dialog_open"] = True
    st.session_state["settings_dialog_target_tab"] = target_tab
    st.rerun()


def _format_model_with_tier(model_name: str, billing_tier: Optional[str]) -> str:
    """Format active model string with localized Free/Paid indicator when known (F5)."""
    base = str(model_name or "-").strip() or "-"
    tier = (billing_tier or "unknown").strip().lower()
    if tier in ("free", "paid", "subscription"):
        return f"{base} ({t(f'tier_badge_{tier}')})"
    return base


def _classify_ui_llm_error_kind(raw_err: str) -> str:
    low = (raw_err or "").lower()
    if (
        "[credit_error]" in low
        or "402" in low
        or "requires more credits" in low
        or "can only afford" in low
        or "insufficient credits" in low
        or "insufficient balance" in low
    ):
        return "credit"
    if "unavailable for free" in low or "paid version is available now" in low:
        return "unavailable_free"
    if "[model_not_found]" in low or "model_not_found" in low or "does not exist" in low or ("404" in low and "model" in low):
        return "model_not_found"
    if "[empty_after_http_ok]" in low:
        return "empty_http_ok"
    if "[rate_limit]" in low or "429" in low or "rate limit" in low:
        return "rate_limit"
    return "generic"


def _render_llm_friendly_error_box(
    project_id: str,
    raw_err: str,
    *,
    active_model: str = "",
    key_suffix: str = "llm",
) -> None:
    """Render user-friendly guidance + Settings button + Detail expander for LLM errors (F5/F6)."""
    kind = _classify_ui_llm_error_kind(raw_err)
    if kind == "credit":
        st.error(t("llm_err_credit_insufficient"))
        if st.button(t("btn_open_llm_settings"), key=f"btn_llm_set_{key_suffix}_{project_id}"):
            _open_mpt_settings_dialog("llm")
    elif kind == "unavailable_free":
        st.error(t("llm_err_model_unavailable_free", model=active_model or "minimax/minimax-m3:free"))
        if st.button(t("btn_open_llm_settings"), key=f"btn_llm_set_{key_suffix}_{project_id}"):
            _open_mpt_settings_dialog("llm")
    elif kind == "model_not_found":
        st.error(t("llm_err_model_not_found", model=active_model or "selected"))
        if st.button(t("btn_change_llm_provider"), key=f"btn_llm_chg_{key_suffix}_{project_id}"):
            _open_mpt_settings_dialog("llm")
    elif kind == "empty_http_ok":
        st.warning(t("llm_connection_empty_after_http_ok"))
        if st.button(t("btn_open_llm_settings"), key=f"btn_llm_set_{key_suffix}_{project_id}"):
            _open_mpt_settings_dialog("llm")
    elif kind == "rate_limit":
        st.warning(t("llm_err_rate_limit"))
    else:
        st.error(t("llm_connection_failed", msg=raw_err))

    if raw_err:
        with st.expander(t("error_details_expander"), expanded=False):
            st.code(raw_err, language="text")


def _get_step_subtitle(step_key: str, status: str, is_job_running: bool = False) -> str:
    """Return status-aware localized subtitle for the given workflow step."""
    eff = "berjalan" if is_job_running else (status or "belum")
    if eff not in {"belum", "berjalan", "terhenti", "perlu_diperbarui", "selesai"}:
        eff = "belum"
    return t(f"{step_key}_subtitle_{eff}")


def _render_friendly_error(friendly_msg: str, exc: Optional[BaseException | str] = None, expanded: bool = False):
    """Display a user-friendly error message and place technical details inside an expander (V7)."""
    st.error(friendly_msg)
    if exc is not None:
        detail_txt = str(exc).strip()
        if detail_txt:
            with st.expander(t("error_details_expander"), expanded=expanded):
                st.code(detail_txt, language="text")


def _start_job_safe(
    project_id: str,
    step_name: str,
    cmd: List[str],
    sleep_after: float = 0.0,
) -> bool:
    """Start a background CLI job and show friendly error + Detail on failure instead of crashing."""
    try:
        jobs.start_job(project_id, step_name, cmd)
        if sleep_after > 0:
            time.sleep(sleep_after)
        st.rerun()
        return True
    except Exception as exc:
        _render_friendly_error(t("err_job_start_friendly"), exc)
        return False


def get_project_thumbnail_path(p_dir: Path) -> Optional[Path]:
    """Find or extract a representative thumbnail for the project card (L1)."""
    # 1. Output thumbnail
    t1 = p_dir / "export" / "thumbnail.jpg"
    if t1.is_file() and t1.stat().st_size > 0:
        return t1
    t2 = p_dir / "output" / "thumbnail.jpg"
    if t2.is_file() and t2.stat().st_size > 0:
        return t2
    # 2. Assets visual thumbnails
    t_dir = p_dir / "assets" / "visual" / "thumbnails"
    if t_dir.is_dir():
        for f in sorted(t_dir.glob("*.jpg")):
            if f.is_file() and f.stat().st_size > 0 and not f.name.startswith("candidate"):
                return f
    # 3. If final video exists, try to extract a quick frame
    final_vid = p_dir / "export" / "final.mp4"
    if not final_vid.is_file():
        final_vid = p_dir / "output" / "final.mp4"
    if final_vid.is_file() and final_vid.stat().st_size > 0:
        try:
            t1.parent.mkdir(parents=True, exist_ok=True)
            cmd = ["ffmpeg", "-y", "-ss", "00:00:03", "-i", str(final_vid), "-vframes", "1", "-vf", "scale=320:-1", str(t1)]
            subprocess.run(cmd, capture_output=True, timeout=5)
            if t1.is_file() and t1.stat().st_size > 0:
                return t1
        except Exception:
            pass
    return None


def render_project_thumbnail_html(thumb_path: Optional[Path], duration_str: str) -> str:
    """Render 16:9 thumbnail container with duration badge overlay (L1)."""
    import html
    dur_badge = f'<div class="efs-project-card-duration">{html.escape(duration_str)}</div>'
    if thumb_path and thumb_path.is_file():
        try:
            import base64
            b64 = base64.b64encode(thumb_path.read_bytes()).decode("ascii")
            return f'<div class="efs-project-card-thumb"><img src="data:image/jpeg;base64,{b64}" alt="Thumb" />{dur_badge}</div>'
        except Exception:
            pass
    return (
        f'<div class="efs-project-card-thumb" style="background: radial-gradient(circle at center, #1F2633 0%, #0F141E 100%);">'
        f'<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#5A6478" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f'<rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"></rect>'
        f'<line x1="7" y1="2" x2="7" y2="22"></line><line x1="17" y1="2" x2="17" y2="22"></line>'
        f'<line x1="2" y1="12" x2="22" y2="12"></line><line x1="2" y1="7" x2="7" y2="7"></line>'
        f'<line x1="2" y1="17" x2="7" y2="17"></line><line x1="17" y1="17" x2="22" y2="17"></line>'
        f'<line x1="17" y1="7" x2="22" y2="7"></line></svg>{dur_badge}</div>'
    )


# --- Project Discovery via Single Status Resolver (C2, D5) ---

def check_has_llm_key() -> bool:
    """Check if active MPT LLM provider is configured via unified readiness helper (8B.1)."""
    try:
        return bool(get_llm_readiness().get("configured", False))
    except Exception:
        return False


def get_project_summaries() -> List[Dict[str, Any]]:
    """Scan projects directory and build status summaries via compute_project_step_status (C2, D5)."""
    root = get_projects_root()
    projects = []
    if not root.is_dir():
        return projects

    for p in root.iterdir():
        if p.name.startswith("_"):
            continue
        if p.is_dir() and (p / "manifest.json").is_file():
            try:
                st_info = compute_project_step_status(p)
                st_info["mtime"] = (p / "manifest.json").stat().st_mtime
                projects.append(st_info)
            except Exception as exc:
                logger.warning(f"Failed to inspect project at {p}: {exc}")

    # Sort newest first by last modified time (D5)
    projects.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return projects


# --- Job Monitor Widget with Fragment (D1) ---

def _render_job_monitor_content(project_id: str, state: Dict[str, Any], current_step: Optional[str] = None, hide_completed: bool = False):
    status = state.get("status")
    step = state.get("step", "")
    pid = state.get("pid")

    if state.get("dismissed"):
        return

    if hide_completed and status == "completed":
        return

    # Check if this job belongs to an earlier workflow step (W3)
    step_order = {
        "script_outline": 1, "script_generate": 1, "script": 1,
        "audio_synthesize": 2, "voice": 2,
        "visual_pipeline": 3, "visual_fetch": 3, "visual_build": 3, "visual_generate": 3, "visual": 3,
        "render_final": 4, "render": 4,
        "capcut_export": 5, "capcut": 5,
        "pipeline_auto": 99,
    }
    if current_step and status in ("completed", "cancelled", "failed", "stopped"):
        job_rank = step_order.get(step, 0)
        curr_rank = step_order.get(current_step, 0)
        if curr_rank > job_rank:
            # Job belongs to an earlier step and user is already on a subsequent step
            return

    step_display_names = {
        "script_outline": t("job_step_script_outline"),
        "script_generate": t("job_step_script_generate"),
        "audio_synthesize": t("job_step_audio_synthesize"),
        "visual_generate": t("job_step_visual_generate"),
        "visual_pipeline": t("job_step_visual_pipeline"),
        "visual_fetch": t("job_step_visual_fetch"),
        "visual_build": t("job_step_visual_build"),
        "visual_ai_video": t("job_step_visual_ai_video"),
        "visual_ai_video_batch": t("job_step_visual_ai_video_batch"),
        "visual_ai_relevance": t("job_step_visual_ai_relevance"),
        "visual_ai_keywords": t("job_step_visual_ai_keywords"),
        "render_final": t("job_step_render_final"),
        "capcut_export": t("job_step_capcut_export"),
        "pipeline_auto": t("job_step_pipeline_auto"),
    }
    step_display = step_display_names.get(step, step)

    if status == "running":
        st.info(t("job_status_running", step=step_display, pid=pid))
        prog = jobs.get_project_progress(project_id, step)

        # Elapsed time formatting (Stage 7B.2 P)
        started_at = state.get("started_at")
        elapsed_str = ""
        if started_at:
            try:
                dt = datetime.fromisoformat(started_at)
                elapsed_sec = max(0, int((datetime.now() - dt).total_seconds()))
                m = elapsed_sec // 60
                s = elapsed_sec % 60
                elapsed_str = f"{m:02d}:{s:02d}"
            except Exception:
                pass

        phase_txt = prog.get("phase") or step_display
        pct_val = min(float(prog.get("percentage", 0.0)), 99.0)
        is_indet = bool(prog.get("is_indeterminate"))

        if is_indet:
            display_txt = f"{phase_txt} • {elapsed_str}" if elapsed_str else phase_txt
            # Indeterminate bar capped at max 95% (never 100% while running)
            bar_val = min(max(pct_val / 100.0, 0.1), 0.95)
            st.progress(bar_val, text=display_txt)
        else:
            tot = prog.get("total", 0)
            comp = prog.get("completed", 0)
            if tot > 0:
                bar_text = f"{phase_txt} • {comp}/{tot} ({pct_val:.1f}%)"
            else:
                bar_text = f"{phase_txt} ({pct_val:.1f}%)"
            if elapsed_str:
                bar_text += f" • {elapsed_str}"
            st.progress(min(pct_val / 100.0, 0.99), text=bar_text)

        col_cancel, col_space = st.columns([1, 4])
        with col_cancel:
            if st.button(t("job_btn_cancel"), key=f"btn_cancel_{project_id}"):
                cancelled = jobs.cancel_job(project_id)
                if cancelled:
                    st.warning(t("job_msg_cancelled"))
                    st.rerun(scope="app")

        logs = jobs.get_job_logs(project_id, max_lines=30)
        with st.expander(t("job_log_expander"), expanded=True):
            if logs:
                st.code("\n".join(logs), language="text")
            else:
                st.caption(t("job_log_waiting"))

    elif status == "stopped":
        st.warning(t("job_status_stopped", step=step_display))
        prog = jobs.get_project_progress(project_id, step)
        phase_txt = prog.get("phase") or step_display
        pct_val = min(float(prog.get("percentage", 0.0)), 99.0)
        tot = prog.get("total", 0)
        comp = prog.get("completed", 0)
        if tot > 0:
            bar_text = f"{phase_txt} • {comp}/{tot} ({pct_val:.1f}%)"
        else:
            bar_text = f"{phase_txt} ({pct_val:.1f}%)"
        st.progress(min(pct_val / 100.0, 0.99), text=bar_text)
        logs = jobs.get_job_logs(project_id, max_lines=20)
        if logs:
            with st.expander(t("job_log_stopped_expander"), expanded=False):
                st.code("\n".join(logs), language="text")

    elif status in ("completed", "cancelled", "failed"):
        col_msg, col_x = st.columns([12, 1])
        with col_msg:
            if status == "completed":
                st.success(t("job_status_completed", step=step_display))
            elif status == "cancelled":
                st.warning(t("job_status_cancelled", step=step_display))
            elif status == "failed":
                st.error(t("job_status_failed", step=step_display))
        with col_x:
            dismiss_key = f"btn_dismiss_job_{project_id}_{state.get('finished_at', state.get('started_at', ''))}"
            if st.button("✕", key=dismiss_key, help=t("job_btn_dismiss_help")):
                jobs.dismiss_job(project_id)
                st.rerun()

        if status == "cancelled":
            prog = jobs.get_project_progress(project_id, step)
            phase_txt = prog.get("phase") or step_display
            pct_val = min(float(prog.get("percentage", 0.0)), 99.0)
            tot = prog.get("total", 0)
            comp = prog.get("completed", 0)
            if tot > 0:
                bar_text = f"{phase_txt} • {comp}/{tot} ({pct_val:.1f}%)"
            else:
                bar_text = f"{phase_txt} ({pct_val:.1f}%)"
            st.progress(min(pct_val / 100.0, 0.99), text=bar_text)

        if status == "failed":
            failed_step_name = state.get("failed_step")
            if failed_step_name:
                st.caption(t("job_failed_step_caption", step=failed_step_name.capitalize()))

            err_detail = str(state.get("error_message") or f"{t('job_exit_code_label')}: {state.get('exit_code', 'unknown')}")
            logs_tail = jobs.get_job_logs(project_id, max_lines=20)
            combined_err = "\n".join([err_detail] + list(logs_tail or []))

            # Detect structured LLM failure reasons (Credit / Unavailable free / Model not found / Rate limit)
            model_match = re.search(r"\[MODEL_NOT_FOUND\]\s*Model=([^\s|]+)", combined_err)
            bad_model = model_match.group(1) if model_match else (get_llm_readiness().get("model") or "selected")
            err_kind = _classify_ui_llm_error_kind(combined_err)
            if err_kind == "credit":
                st.error(t("llm_err_credit_insufficient"))
                if st.button(t("btn_open_llm_settings"), key=f"btn_err_chg_prov_{project_id}"):
                    _open_mpt_settings_dialog("llm")
            elif err_kind == "unavailable_free":
                st.error(t("llm_err_model_unavailable_free", model=bad_model))
                if st.button(t("btn_open_llm_settings"), key=f"btn_err_chg_prov_{project_id}"):
                    _open_mpt_settings_dialog("llm")
            elif err_kind == "model_not_found":
                st.error(t("llm_err_model_not_found", model=bad_model))
                if st.button(t("btn_change_llm_provider"), key=f"btn_err_chg_prov_{project_id}"):
                    _open_mpt_settings_dialog("llm")
            elif err_kind == "rate_limit":
                st.warning(t("llm_err_rate_limit"))

            if failed_step_name in ("script_outline", "script_generate", "script"):
                script_dir = get_project_dir(project_id) / "script"
                if script_dir.is_dir() and any(script_dir.glob("chapter_*.md")):
                    st.info(t("llm_completed_chapters_preserved"))

            if err_detail:
                with st.expander(t("error_details_expander"), expanded=False):
                    st.code(str(err_detail), language="text")

            col_retry, _ = st.columns([1, 4])
            with col_retry:
                if st.button(t("job_btn_retry"), key=f"btn_retry_job_{project_id}", type="primary"):
                    cmd = state.get("command")
                    if cmd:
                        jobs.dismiss_job(project_id)
                        _start_job_safe(project_id, state.get("step", "pipeline_auto"), cmd, sleep_after=0.5)

            if logs_tail:
                with st.expander(t("job_error_log_expander"), expanded=False):
                    st.code("\n".join(logs_tail), language="text")


@st.fragment(run_every=2)
def _active_job_monitor_fragment(project_id: str, current_step: Optional[str] = None, hide_completed: bool = False):
    """Auto-refreshing fragment monitor that polls job state while running (D1)."""
    state = jobs.get_job_state(project_id)
    if not state or state.get("status") != "running":
        # Job transitioned from running to completed / stopped / cancelled / failed.
        # Trigger full app rerun so stepper, buttons, and badges update!
        st.rerun(scope="app")
        return

    _render_job_monitor_content(project_id, state, current_step=current_step, hide_completed=hide_completed)


def render_job_monitor_widget(project_id: str, current_step: Optional[str] = None, hide_completed: bool = False):
    """Real-time background job monitor widget (D1, W3)."""
    state = jobs.get_job_state(project_id)
    if not state:
        return

    status = state.get("status")
    if status == "running":
        _active_job_monitor_fragment(project_id, current_step=current_step, hide_completed=hide_completed)
    else:
        _render_job_monitor_content(project_id, state, current_step=current_step, hide_completed=hide_completed)


# --- Beranda (Home Page) Components (D5) ---

def render_niche_management_section():
    """Render Section 'Kelola Niche': Built-in (view-only), Custom (edit, delete) (D8)."""
    with st.container(border=True):
        st.subheader(t("niche_mgmt_title"))
        tab_builtin, tab_custom = st.tabs([t("niche_tab_builtin"), t("niche_tab_custom")])

        with tab_builtin:
            st.caption(t("niche_builtin_caption"))
            builtin_presets = get_builtin_presets()
            for pid, p in sorted(builtin_presets.items()):
                with st.expander(f"{p.name} ({pid})", expanded=False):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(t("niche_field_kind", val=p.kind))
                        st.write(t("niche_field_lang_line", val=p.language))
                        st.write(t("niche_field_wpm", val=p.words_per_minute))
                    with col2:
                        st.write(t("niche_field_extends", val=p.extends or t("niche_root_label")))
                        st.write(t("niche_field_audience", val=p.audience or "-"))
                        st.write(t("niche_field_style", val=p.narration_style or "-"))

        with tab_custom:
            custom_presets = get_custom_presets()
            if not custom_presets:
                st.info(t("niche_no_custom_info"))
            else:
                for pid, p in sorted(custom_presets.items()):
                    with st.expander(f"{p.name} ({pid})", expanded=False):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write(t("niche_field_kind", val=p.kind))
                            st.write(t("niche_field_lang_line", val=p.language))
                            st.write(t("niche_field_extends", val=p.extends or t("niche_root_label")))
                        with col2:
                            st.write(t("niche_field_topic_kw", val=", ".join(getattr(p, "topic_keywords", []) or []) or "-"))
                            st.write(t("niche_field_visual_kw", val=", ".join(getattr(p, "visual_keywords", []) or []) or "-"))

                        col_del, _ = st.columns([1, 4])
                        with col_del:
                            if st.button(t("btn_delete_niche"), key=f"btn_del_preset_{pid}", type="secondary"):
                                try:
                                    delete_custom_preset(pid)
                                    st.success(t("niche_delete_success", id=pid))
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as exc:
                                    _render_friendly_error(t("err_niche_delete_friendly"), exc)


def _render_delete_project_dialog(*, on_deleted_redirect_home: bool = False) -> None:
    """Render shared safe project delete confirmation modal (F7)."""
    target_pid = st.session_state.get("confirm_delete_project_id")
    if not target_pid:
        return

    try:
        preview = get_project_delete_preview(target_pid)
    except Exception as exc:
        st.session_state["confirm_delete_project_id"] = None
        _render_friendly_error(t("err_delete_project_friendly"), exc)
        return

    @st.dialog(t("dlg_delete_project_title"))
    def _delete_project_modal():
        st.markdown(
            t(
                "dlg_delete_project_meta",
                title=preview["title"],
                pid=preview["project_id"],
                mb=f"{preview['size_mb']:.2f}",
                cc_count=preview["capcut_draft_count"],
            )
        )
        st.caption(t("dlg_delete_project_soft_note"))

        if preview["is_locked_or_running"]:
            st.warning(t("dlg_delete_locked_warn", reason=preview.get("lock_reason") or "running"))

        typed_val = st.text_input(
            t("dlg_delete_confirm_label", pid=preview["project_id"]),
            key=f"txt_del_confirm_{preview['project_id']}",
        )
        chk_capcut = st.checkbox(
            t("chk_delete_with_capcut"),
            value=False,
            key=f"chk_del_capcut_{preview['project_id']}",
        )
        typed_clean = (typed_val or "").strip()
        matches_confirm = bool(
            typed_clean
            and (typed_clean == preview["project_id"] or typed_clean == preview["title"].strip())
        )
        can_delete = matches_confirm and not preview["is_locked_or_running"]

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            if st.button(
                t("btn_confirm_delete_project"),
                type="primary",
                disabled=not can_delete,
                key=f"btn_dlg_conf_del_{preview['project_id']}",
                use_container_width=True,
            ):
                try:
                    soft_delete_project(
                        preview["project_id"],
                        delete_capcut_drafts=bool(chk_capcut),
                    )
                    st.session_state["confirm_delete_project_id"] = None
                    st.session_state["last_deleted_notice"] = t("delete_project_success", title=preview["title"])
                    if on_deleted_redirect_home:
                        st.query_params.clear()
                        st.session_state.pop(f"active_step_{preview['project_id']}", None)
                    st.rerun()
                except Exception as exc:
                    _render_friendly_error(t("err_delete_project_friendly"), exc)
        with col_d2:
            if st.button(
                t("btn_cancel_dialog"),
                key=f"btn_dlg_canc_del_{preview['project_id']}",
                use_container_width=True,
            ):
                st.session_state["confirm_delete_project_id"] = None
                st.rerun()

    _delete_project_modal()


def _render_trash_management_view(trashed_items: List[Dict[str, Any]]) -> None:
    """Render Trash management list with Restore, Permanent Delete, and Empty Trash (F7)."""
    st.markdown(t("trash_heading"))
    if not trashed_items:
        st.info(t("trash_empty_state"))
        return

    total_mb = round(sum(float(x.get("size_mb", 0.0)) for x in trashed_items), 2)
    col_tl, col_tr = st.columns([3, 1.5])
    with col_tr:
        if st.button(
            t("btn_empty_trash"),
            key="btn_open_empty_trash_dlg",
            type="secondary",
            use_container_width=True,
        ):
            st.session_state["confirm_empty_trash_open"] = True

    for item in trashed_items:
        t_id = item["trash_id"]
        with st.container(border=True):
            c_info, c_rest, c_perm = st.columns([3.5, 1.1, 1.3])
            with c_info:
                st.markdown(f"**{html.escape(item['title'])}**")
                del_dt = str(item.get("deleted_at") or "")[:19].replace("T", " ")
                st.caption(
                    t(
                        "trash_item_meta",
                        pid=item["project_id"],
                        date=del_dt,
                        mb=f"{float(item.get('size_mb', 0.0)):.2f}",
                    )
                )
            with c_rest:
                if st.button(t("btn_restore_project"), key=f"btn_restore_{t_id}", use_container_width=True):
                    try:
                        res = restore_trashed_project(t_id)
                        st.success(t("restore_project_success", title=res["title"]))
                        time.sleep(0.3)
                        st.rerun()
                    except Exception as exc:
                        _render_friendly_error(t("err_restore_project_friendly"), exc)
            with c_perm:
                if st.button(t("btn_perm_delete_project"), key=f"btn_perm_del_{t_id}", use_container_width=True):
                    try:
                        res = permanently_delete_trashed_project(t_id)
                        st.success(t("perm_delete_success", title=res["title"], mb=f"{res['mb_freed']:.2f}"))
                        time.sleep(0.3)
                        st.rerun()
                    except Exception as exc:
                        _render_friendly_error(t("err_delete_project_friendly"), exc)

    if st.session_state.get("confirm_empty_trash_open"):
        @st.dialog(t("dlg_empty_trash_title"))
        def _empty_trash_modal():
            st.warning(t("dlg_empty_trash_warn", count=len(trashed_items), mb=f"{total_mb:.2f}"))
            col_e1, col_e2 = st.columns(2)
            with col_e1:
                if st.button(
                    t("btn_confirm_empty_trash"),
                    type="primary",
                    key="btn_conf_empty_trash",
                    use_container_width=True,
                ):
                    try:
                        res = empty_trash()
                        st.session_state["confirm_empty_trash_open"] = False
                        st.session_state["last_deleted_notice"] = t(
                            "empty_trash_success",
                            count=res["deleted_count"],
                            mb=f"{res['mb_freed']:.2f}",
                        )
                        st.rerun()
                    except Exception as exc:
                        _render_friendly_error(t("err_delete_project_friendly"), exc)
            with col_e2:
                if st.button(t("btn_cancel_dialog"), key="btn_canc_empty_trash", use_container_width=True):
                    st.session_state["confirm_empty_trash_open"] = False
                    st.rerun()

        _empty_trash_modal()


def render_home_page():
    """Render Beranda Long Video matching Mockup 1 (L1, 8A.2)."""
    # G5: Return navigation link to MoneyPrinterTurbo Main
    from app.services.longform.ui.nav import render_mpt_entry
    render_mpt_entry()

    notice_msg = st.session_state.pop("last_deleted_notice", None)
    if notice_msg:
        st.success(notice_msg)

    # 1. Header with title, subtitle, and search box on the right (Mockup 1)
    col_h, col_actions = st.columns([3.5, 2.0])
    with col_h:
        st.markdown(
            f"""
            <div style="margin-bottom: 0.5rem;">
                <h1 style="font-size: 1.85rem; font-weight: 700; margin: 0 0 0.35rem 0; color: var(--efs-text);">{t("home_title")}</h1>
                <p style="font-size: 0.95rem; color: var(--efs-muted); margin: 0;">{t("home_subtitle")}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_actions:
        st.write("")
        col_s_box, col_s_btn = st.columns([3, 2])
        with col_s_box:
            search_kw = st.text_input(
                t("home_search_label"),
                placeholder=t("home_search_placeholder"),
                label_visibility="collapsed",
                key="home_search_kw",
            )
        with col_s_btn:
            # Preserves compatibility for test_stage7_g_fixes.py line 205
            if st.button(t("btn_new_project"), key="btn_toggle_new_proj", use_container_width=True):
                st.session_state["focus_new_project"] = True

    # Niche Management Section (shown when toggled from sidebar or button)
    if st.session_state.get("show_niche_mgmt"):
        render_niche_management_section()

    # 2. Card "Mulai video baru" (Permanently open at top, Mockup 1)
    with st.container(border=True):
        st.markdown(f"#### {t('card_new_video_title')}")
        st.caption(t("card_new_video_note"))

        col_topik, col_dur, col_lang, col_btn = st.columns([3.2, 1.2, 1.4, 1.3])
        with col_topik:
            new_topic = st.text_input(
                t("field_topic"),
                placeholder=t("field_topic_placeholder"),
                key="input_new_topic",
            )
        with col_dur:
            new_duration = st.slider(
                t("field_duration"),
                min_value=10,
                max_value=20,
                value=12,
                step=1,
                format=f"%d {t('unit_minutes')}",
                key="slider_new_duration",
            )
        with col_lang:
            new_lang = st.selectbox(
                t("field_narration_lang"),
                options=["id-ID (Bahasa Indonesia)", "en-US (English)"],
                index=0,
                key="sel_new_proj_lang",
            )
        eff_lang_code = "en-US" if "en-US" in new_lang else "id-ID"

        with col_btn:
            st.write("")
            st.write("")
            submitted = st.button(t("btn_create_project"), type="primary", use_container_width=True, key="btn_create_project_submit")

        # Expander "Pengaturan lanjutan"
        with st.expander(t("expander_advanced_settings"), expanded=False):
            root_ids, root_labels = get_available_niches_for_ui(eff_lang_code)

            def _fmt_root_niche(pid: str) -> str:
                if pid == "+ Niche baru…":
                    return t("new_root_niche_option")
                return root_labels.get(pid, pid)

            col_n, col_sub = st.columns(2)
            with col_n:
                if st.session_state.get("sel_root_niche") not in root_ids:
                    st.session_state["sel_root_niche"] = root_ids[0]
                chosen_root = st.selectbox(
                    t("field_main_niche"),
                    options=root_ids,
                    format_func=_fmt_root_niche,
                    index=0,
                    key="sel_root_niche",
                )

            is_creating_new_root = (chosen_root == "+ Niche baru…")
            new_niche_name = ""
            new_sub_name = ""
            chosen_sub = "(Gunakan Niche Utama)"
            is_creating_new_sub = False
            custom_footage_kw = ""
            custom_avoid_kw = ""

            if is_creating_new_root:
                with col_sub:
                    st.caption(t("new_root_niche_option"))
                col_nname, col_subname = st.columns(2)
                with col_nname:
                    new_niche_name = st.text_input(
                        t("new_niche_name_label"),
                        placeholder=t("new_niche_name_placeholder"),
                        key="input_new_niche_name",
                    )
                with col_subname:
                    new_sub_name = st.text_input(
                        t("new_sub_name_label"),
                        placeholder=t("new_sub_name_placeholder"),
                        key="input_new_sub_name",
                    )
                col_kw1, col_kw2 = st.columns(2)
                with col_kw1:
                    custom_footage_kw = st.text_input(
                        t("custom_footage_kw_label"),
                        placeholder=t("custom_footage_kw_placeholder"),
                        key="input_custom_footage_kw",
                    )
                with col_kw2:
                    custom_avoid_kw = st.text_input(
                        t("custom_avoid_kw_label"),
                        placeholder=t("custom_avoid_kw_placeholder"),
                        key="input_custom_avoid_kw",
                    )
            else:
                sub_ids, sub_labels = get_sub_niches_for_ui(chosen_root, eff_lang_code)

                def _fmt_sub_niche(pid: str) -> str:
                    if pid == "(Gunakan Niche Utama)":
                        return t("use_root_niche_option")
                    if pid == "+ Sub-niche baru…":
                        return t("new_sub_niche_option")
                    return sub_labels.get(pid, pid)

                with col_sub:
                    chosen_sub = st.selectbox(
                        t("field_sub_niche"),
                        options=sub_ids,
                        format_func=_fmt_sub_niche,
                        index=0,
                        key="sel_sub_niche",
                    )

                is_creating_new_sub = (chosen_sub == "+ Sub-niche baru…")
                if is_creating_new_sub:
                    new_sub_name = st.text_input(
                        t("new_sub_name_label"),
                        placeholder=t("new_sub_name_placeholder"),
                        key="input_new_sub_name",
                    )
                    col_kw1, col_kw2 = st.columns(2)
                    with col_kw1:
                        custom_footage_kw = st.text_input(
                            t("custom_footage_kw_label"),
                            placeholder=t("custom_footage_kw_placeholder"),
                            key="input_custom_footage_kw",
                        )
                    with col_kw2:
                        custom_avoid_kw = st.text_input(
                            t("custom_avoid_kw_label"),
                            placeholder=t("custom_avoid_kw_placeholder"),
                            key="input_custom_avoid_kw",
                        )

            suggested_id = re.sub(r"[^a-zA-Z0-9_\-]+", "_", new_topic.lower()).strip("_")
            col_id, col_foc, col_asp = st.columns([1.5, 2, 1.5])
            with col_id:
                custom_id = st.text_input(t("field_project_id"), value=suggested_id)
            with col_foc:
                new_focus = st.text_input(t("field_narrative_focus"), placeholder=t("field_narrative_focus_placeholder"))
            with col_asp:
                aspect_ratio = st.selectbox(
                    t("field_aspect_ratio"),
                    ["16:9", "9:16"],
                    format_func=lambda v: t("aspect_ratio_16_9") if v == "16:9" else t("aspect_ratio_9_16"),
                    index=0,
                )

        if submitted:
            if not new_topic.strip():
                st.error(t("err_topic_required"))
            elif is_creating_new_root and not new_niche_name.strip():
                st.error(t("err_niche_name_required"))
            elif is_creating_new_sub and not new_sub_name.strip():
                st.error(t("err_sub_name_required"))
            else:
                eff_id = custom_id.strip() if custom_id.strip() else re.sub(r"[^a-zA-Z0-9_\-]+", "_", new_topic.lower()).strip("_")
                if not eff_id:
                    eff_id = f"project_{int(time.time())}"

                if get_project_dir(eff_id).exists():
                    st.error(t("err_project_exists", id=eff_id))
                else:
                    try:
                        extra_vis = [k.strip() for k in custom_footage_kw.split(",") if k.strip()] if (is_creating_new_root or is_creating_new_sub) else []
                        extra_neg = [k.strip() for k in custom_avoid_kw.split(",") if k.strip()] if (is_creating_new_root or is_creating_new_sub) else []

                        if is_creating_new_root:
                            target_preset_id, _ = create_custom_niche_hierarchy(
                                niche_name=new_niche_name.strip(),
                                sub_niche_name=new_sub_name.strip() if new_sub_name.strip() else None,
                                language=eff_lang_code,
                                visual_keywords=extra_vis,
                                negative_terms=extra_neg,
                            )
                        elif is_creating_new_sub:
                            target_preset_id, _ = create_custom_niche_hierarchy(
                                niche_name="",
                                sub_niche_name=new_sub_name.strip(),
                                parent_niche_id=chosen_root,
                                language=eff_lang_code,
                                visual_keywords=extra_vis,
                                negative_terms=extra_neg,
                            )
                        elif chosen_sub != "(Gunakan Niche Utama)":
                            target_preset_id = chosen_sub
                        else:
                            target_preset_id = chosen_root

                        create_project(
                            project_id=eff_id,
                            title=new_topic.strip(),
                            topic=new_topic.strip(),
                            target_duration_minutes=new_duration,
                            preset_id=target_preset_id,
                            sub_niche_focus=new_focus.strip() if new_focus else None,
                            aspect_ratio="16:9" if "16:9" in aspect_ratio else "9:16",
                            language=eff_lang_code,
                        )
                        st.query_params["project"] = eff_id
                        st.session_state["show_new_proj_form"] = False
                        st.rerun()
                    except Exception as exc:
                        _render_friendly_error(t("err_project_create_friendly"), exc)

    st.write("")

    # 3. Project Filter Chips and Grid (Mockup 1)
    projects = get_project_summaries()

    trashed_items = list_trashed_projects()
    total_cnt = len(projects)
    comp_cnt = sum(
        1 for p in projects
        if all(p.get("steps", {}).get(s, {}).get("status") == "selesai" for s in ["script", "voice", "visual", "render", "capcut"])
    )
    in_prog_cnt = total_cnt - comp_cnt

    opt_all = f"{t('filter_all')} · {total_cnt}"
    opt_prog = f"{t('filter_in_progress')} · {in_prog_cnt}"
    opt_done = f"{t('filter_completed')} · {comp_cnt}"
    opt_trash = f"{t('filter_trash')} · {len(trashed_items)}"

    if st.session_state.pop("home_open_trash_requested", False):
        st.session_state["home_filter_pills"] = opt_trash

    filter_choice = st.pills(
        t("home_filter_label"),
        options=[opt_all, opt_prog, opt_done, opt_trash],
        default=opt_all,
        key="home_filter_pills",
        label_visibility="collapsed",
    )

    if filter_choice == opt_trash:
        _render_trash_management_view(trashed_items)
        _render_delete_project_dialog(on_deleted_redirect_home=False)
        return

    filtered_projects = projects
    if filter_choice == opt_prog:
        filtered_projects = [
            p for p in projects
            if not all(p.get("steps", {}).get(s, {}).get("status") == "selesai" for s in ["script", "voice", "visual", "render", "capcut"])
        ]
    elif filter_choice == opt_done:
        filtered_projects = [
            p for p in projects
            if all(p.get("steps", {}).get(s, {}).get("status") == "selesai" for s in ["script", "voice", "visual", "render", "capcut"])
        ]

    if search_kw:
        kw = search_kw.strip().lower()
        filtered_projects = [p for p in filtered_projects if kw in p["title"].lower() or kw in p["project_id"].lower()]

    if not filtered_projects:
        if search_kw:
            st.info(t("no_projects_found"))
        else:
            st.info(t("no_projects_yet"))
        return

    # 3-Column Project Cards Grid (Mockup 1)
    cols = st.columns(3)
    _step_name_map = {
        "Naskah": t("step_script"),
        "Suara": t("step_voice"),
        "Visual": t("step_visual"),
        "Render": t("step_render"),
        "CapCut": t("step_capcut"),
    }
    for idx, p in enumerate(filtered_projects):
        with cols[idx % 3]:
            with st.container(border=True):
                p_dir = get_project_dir(p["project_id"])
                thumb_p = get_project_thumbnail_path(p_dir)
                dur_minutes = p.get("duration_minutes", 12)
                dur_str = f"≈ {dur_minutes} {t('unit_minutes')}"
                st.markdown(render_project_thumbnail_html(thumb_p, dur_str), unsafe_allow_html=True)

                raw_title = p["title"].strip()
                st.markdown(
                    f'<div class="efs-project-card-title" title="{html.escape(raw_title)}">{html.escape(raw_title)}</div>',
                    unsafe_allow_html=True,
                )

                niche_text = p["preset_id"]
                if p.get("sub_niche_focus"):
                    niche_text += f" › {p['sub_niche_focus']}"
                st.markdown(
                    f'<div class="efs-project-card-niche">{html.escape(niche_text)}</div>',
                    unsafe_allow_html=True,
                )

                # 5-segment progress bar
                st.markdown(render_5_segment_progress_bar_html(p.get("steps", {})), unsafe_allow_html=True)

                # Next step and progress count
                comp_steps = sum(
                    1 for s in ["script", "voice", "visual", "render", "capcut"]
                    if p.get("steps", {}).get(s, {}).get("status") == "selesai"
                )
                if comp_steps == 5:
                    next_str = t("project_all_done")
                else:
                    raw_next = p.get("next_step", "Naskah")
                    localized_next = _step_name_map.get(raw_next, raw_next)
                    next_str = t("project_next_step", step=localized_next)

                prog_str = t("project_step_progress", completed=comp_steps)
                st.markdown(
                    f'<div class="efs-project-card-status"><span>{html.escape(next_str)}</span><span>{html.escape(prog_str)}</span></div>',
                    unsafe_allow_html=True,
                )

                col_card_open, col_card_more = st.columns([4, 1])
                with col_card_open:
                    if st.button(t("btn_open_project"), key=f"btn_open_{p['project_id']}", use_container_width=True):
                        st.query_params["project"] = p["project_id"]
                        st.rerun()
                with col_card_more:
                    with st.popover("⋯", help=t("popover_more_help"), use_container_width=True):
                        if st.button(
                            t("btn_delete_project"),
                            key=f"btn_del_card_{p['project_id']}",
                            use_container_width=True,
                        ):
                            st.session_state["confirm_delete_project_id"] = p["project_id"]
                            st.rerun()

    _render_delete_project_dialog(on_deleted_redirect_home=False)


# --- Halaman Proyek (Project Detail Page) Components (C4.3, C4.4, C4.5) ---

def render_project_page(project_id: str):
    """Render Halaman Proyek with Stepper and Single Adaptive Action (C4.3)."""
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir() or not (p_dir / "manifest.json").is_file():
        st.error(t("err_project_not_found", id=project_id))
        if st.button("← " + t("btn_back_to_projects")):
            st.query_params.clear()
            st.rerun()
        return

    st_info = compute_project_step_status(p_dir)
    manifest = st_info["manifest"]

    # G5, H3, H5: Sidebar studio navigation and CSS
    from app.services.longform.ui.nav import render_mpt_entry
    render_mpt_entry(show_top_return=False)

    # Navigation header & top meta row (Mockup 2)
    col_back, col_title, col_actions = st.columns([1.2, 3.6, 2.2])
    with col_back:
        if st.button("← " + t("btn_all_projects"), key=f"btn_back_{project_id}"):
            st.query_params.clear()
            st.session_state.pop(f"active_step_{project_id}", None)
            st.rerun()

    with col_title:
        st.title(manifest.title)
        niche_text = manifest.preset_id
        if manifest.sub_niche_focus:
            niche_text += f" › {manifest.sub_niche_focus}"
        dur_badge = f"≈ {st_info['duration_minutes']} {t('unit_minutes')}"
        manifest_lang = getattr(manifest, "language", None) or (manifest.preset_snapshot or {}).get("language") or "id-ID"
        asp_badge = f"{manifest.aspect_ratio} · {manifest_lang}"
        st.markdown(
            f'{render_badge(niche_text, "neutral")} '
            f'{render_badge(dur_badge, "neutral")} '
            f'{render_badge(asp_badge, "neutral")}',
            unsafe_allow_html=True,
        )

    # Automated Pipeline Section ("Otomatis Sampai Selesai" - A1, A2, A3)
    script_done = (st_info["steps"]["script"]["status"] == "selesai")
    all_done = (
        st_info["steps"]["script"]["status"] == "selesai"
        and st_info["steps"]["voice"]["status"] == "selesai"
        and st_info["steps"]["visual"]["status"] == "selesai"
        and st_info["steps"]["render"]["status"] == "selesai"
    )

    with col_actions:
        st.write("")
        col_auto_btn, col_opt_btn = st.columns([4, 1])
        with col_auto_btn:
            with st.popover(f"▷ {t('btn_auto_run_until_done')}", use_container_width=True):
                st.markdown(f"**{t('auto_pipeline_title')}**")
                st.caption(t("auto_pipeline_desc"))
                auto_with_capcut = st.checkbox(t("auto_include_capcut"), value=False, key=f"auto_cc_{project_id}")
                auto_stop_review = st.checkbox(t("auto_stop_on_review"), value=True, key=f"auto_rev_{project_id}")

                job_state = jobs.get_job_state(project_id)
                is_job_running = bool(job_state and job_state.get("status") == "running")
                is_review_paused = bool(
                    job_state
                    and job_state.get("status") == "stopped"
                    and "Menunggu pengecekan visual" in (job_state.get("phase") or "")
                )

                if is_review_paused:
                    phase_msg = f" ({job_state.get('phase')})" if job_state.get("phase") else ""
                    st.warning(t("auto_review_paused_warn", phase=phase_msg))
                    if st.button(t("btn_resume_auto_skip"), key=f"btn_resume_auto_{project_id}", type="primary", use_container_width=True):
                        auto_args = ["pipeline", "auto", project_id, "--skip-review-check"]
                        if auto_with_capcut:
                            auto_args.append("--with-capcut")
                        cmd = jobs.build_longform_cli_command(*auto_args)
                        _start_job_safe(project_id, "pipeline_auto", cmd, sleep_after=0.5)
                elif not script_done:
                    st.caption(t("auto_need_script_caption"))
                    st.button(f"▷ {t('btn_auto_run_until_done')}", disabled=True, key=f"btn_auto_dis_{project_id}", use_container_width=True)
                elif all_done and not auto_with_capcut:
                    st.button(t("status_done"), disabled=True, key=f"btn_auto_done_{project_id}", use_container_width=True)
                else:
                    if st.button(f"▷ {t('btn_auto_run_until_done')}", disabled=is_job_running, type="primary", key=f"btn_start_auto_{project_id}", use_container_width=True):
                        auto_args = ["pipeline", "auto", project_id]
                        if auto_with_capcut:
                            auto_args.append("--with-capcut")
                        if auto_stop_review:
                            auto_args.append("--stop-on-review")
                        cmd = jobs.build_longform_cli_command(*auto_args)
                        _start_job_safe(project_id, "pipeline_auto", cmd, sleep_after=0.5)

        with col_opt_btn:
            with st.popover("⋯", help=t("popover_project_settings_help"), use_container_width=True):
                st.markdown(f"**{t('popover_project_settings_title')}**")
                all_presets = list_presets()
                preset_options = sorted(all_presets.keys())
                current_idx = preset_options.index(manifest.preset_id) if manifest.preset_id in preset_options else 0
                new_selected_preset = st.selectbox(
                    t("relink_preset_select"),
                    options=preset_options,
                    format_func=lambda pid: f"{all_presets[pid].name} ({pid})",
                    index=current_idx,
                    key=f"sel_relink_preset_{project_id}",
                )
                if st.button(t("btn_apply_relink"), key=f"btn_apply_relink_{project_id}"):
                    if new_selected_preset != manifest.preset_id:
                        manifest.preset_id = new_selected_preset
                        save_manifest(manifest, p_dir)
                        refresh_project_preset(p_dir)
                        st.success(t("relink_success", preset=new_selected_preset))
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.info(t("relink_no_change"))
                st.divider()
                if st.button(
                    t("btn_delete_project"),
                    key=f"btn_del_proj_{project_id}",
                    use_container_width=True,
                ):
                    st.session_state["confirm_delete_project_id"] = project_id
                    st.rerun()

    _render_delete_project_dialog(on_deleted_redirect_home=True)

    # Stepper 5 Langkah (C4.3)
    step_keys = ["script", "voice", "visual", "render", "capcut"]
    step_titles = {
        "script": t("step_script"),
        "voice": t("step_voice"),
        "visual": t("step_visual"),
        "render": t("step_render"),
        "capcut": t("step_capcut"),
    }
    step_labels = {
        "script": f"① {t('step_script')}",
        "voice": f"② {t('step_voice')}",
        "visual": f"③ {t('step_visual')}",
        "render": f"④ {t('step_render')}",
        "capcut": f"⑤ {t('step_capcut')}",
    }
    step_status_badges = {
        "selesai": "✅ " + t("status_done"),
        "berjalan": "⏳ " + t("status_in_progress"),
        "terhenti": "⚠️ " + t("status_interrupted"),
        "perlu_diperbarui": "🔄 " + t("status_needs_update"),
        "belum": "⚪ " + t("status_not_started"),
    }

    core_4_completed = (
        st_info["steps"]["script"]["status"] == "selesai"
        and st_info["steps"]["voice"]["status"] == "selesai"
        and st_info["steps"]["visual"]["status"] == "selesai"
        and st_info["steps"]["render"]["status"] == "selesai"
    )

    # Auto-select first uncompleted step if not explicitly selected
    default_active_step = "script"
    for k in step_keys:
        if st_info["steps"][k]["status"] != "selesai":
            default_active_step = k
            break

    session_key = f"active_step_{project_id}"
    query_step = st.query_params.get("step")
    if query_step in step_keys:
        st.session_state[session_key] = query_step
    elif session_key not in st.session_state:
        st.session_state[session_key] = default_active_step

    current_active_step = st.session_state[session_key]

    # Always Visible Top Job Monitor (Mockup 2: Placed ABOVE stepper)
    # Hide "Pekerjaan Selesai" when core_4_completed so only 1 banner shows (V4)
    render_job_monitor_widget(project_id, current_step=current_active_step, hide_completed=core_4_completed)

    if core_4_completed:
        banner_msg = f"{t('success_banner_title')} {t('success_banner_subtitle')}"
        render_success_banner(banner_msg)

    # Preset Impactful Difference Banner (C3, H1)
    if st_info.get("preset_has_impactful_diff"):
        diff_count = len(st_info["preset_diff_details"])
        col_w, col_wb = st.columns([4, 1])
        has_lang_mismatch = any(df.get("field") == "language" for df in st_info["preset_diff_details"])
        with col_w:
            st.warning(f"**{t('preset_diff_banner_title')}**: {t('preset_diff_banner_desc', count=diff_count)}")
            with st.expander(t("preset_diff_details_expander"), expanded=False):
                for df in st_info["preset_diff_details"]:
                    st.write(f"- **{df['field']}**: {df['description']}")
                    st.caption(f"  {df['snapshot_val']}  →  {df['current_val']}")
        with col_wb:
            st.write("")
            if has_lang_mismatch:
                st.caption(t("preset_diff_lang_mismatch"))
            elif st.button(t("preset_diff_btn_apply"), key=f"btn_refresh_preset_{project_id}"):
                refresh_project_preset(p_dir)
                st.success(t("preset_refresh_success"))
                time.sleep(0.5)
                st.rerun()

    # Render horizontal stepper pill selector (5 cards, Mockup 2 & 3)
    stepper_cols = st.columns(5)
    for idx, k in enumerate(step_keys):
        st_val = st_info["steps"][k]["status"]
        badge = step_status_badges.get(st_val, "⚪ " + t("status_not_started"))
        is_selected = (k == current_active_step)
        btn_type = "primary" if is_selected else "secondary"

        # Friendly status state & clean label for mockup card
        status_state = "done" if st_val == "selesai" else ("active" if is_selected else ("running" if st_val == "berjalan" else ("warning" if st_val in ("terhenti", "perlu_diperbarui") else "pending")))
        status_label_clean = t("status_done") if st_val == "selesai" else (t("status_in_progress") if st_val == "berjalan" else (t("status_interrupted") if st_val == "terhenti" else (t("status_needs_update") if st_val == "perlu_diperbarui" else t("status_not_started"))))

        with stepper_cols[idx]:
            card_html = render_stepper_card_html(
                idx + 1,
                step_titles[k],
                status_label_clean,
                status_state,
                is_selected,
            )
            st.markdown(card_html, unsafe_allow_html=True)
            if st.button(f"{step_labels[k]}\n{badge}", key=f"stepper_btn_{k}_{project_id}", type=btn_type, use_container_width=True):
                st.session_state[session_key] = k
                st.rerun()

    st.write("")

    # Active Step Content Container
    active_step = st.session_state[session_key]
    active_info = st_info["steps"][active_step]
    active_status = active_info["status"]

    job_state = jobs.get_job_state(project_id)
    is_job_running = bool(job_state and job_state.get("status") == "running")

    # Mockup 3: If core steps are completed and on Render or CapCut step, render side-by-side view
    if core_4_completed and active_step in ("render", "capcut"):
        col_side_rend, col_side_cc = st.columns(2)
        with col_side_rend:
            with st.container(border=True):
                render_render_step_flow(project_id, p_dir, manifest, st_info["steps"]["render"], is_job_running=is_job_running, is_completed_view=True)
        with col_side_cc:
            with st.container(border=True):
                render_capcut_step_flow(project_id, p_dir, manifest, st_info["steps"]["capcut"], is_job_running=is_job_running, is_completed_view=True)
    else:
        with st.container(border=True):
            if active_step == "script":
                render_script_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running)
            elif active_step == "voice":
                render_voice_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running)
            elif active_step == "visual":
                render_visual_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running)
            elif active_step == "render":
                render_render_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running, is_completed_view=False)
            elif active_step == "capcut":
                render_capcut_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running, is_completed_view=False)


# --- Step 1: Naskah Flow (C4.3) ---

def render_script_step_flow(
    project_id: str,
    p_dir: Path,
    manifest: ProjectManifest,
    step_info: Dict[str, Any],
    is_job_running: bool = False,
    is_running: bool = False,
):
    """Render Step 1: Naskah with two clear side-by-side cards (D3)."""
    job_state = jobs.get_job_state(project_id)
    is_job_running = is_job_running or is_running or bool(job_state and job_state.get("status") == "running")

    status = step_info["status"]

    # Clean Header (Mockup style)
    col_hdr, col_act = st.columns([3, 1])
    with col_hdr:
        st.markdown(f"### {t('script_step_title')}")
        st.caption(_get_step_subtitle("script", status, is_job_running))
    with col_act:
        st.write("")
        if status == "selesai":
            if st.button(t("btn_proceed_voice"), type="primary", key=f"btn_next_voice_{project_id}", use_container_width=True):
                st.session_state[f"active_step_{project_id}"] = "voice"
                st.rerun()

    # If script is already finished: show chapter list and continue button
    if status == "selesai":
        st.success(t("script_summary_format", chapters=len(manifest.chapters)))
        for ch in manifest.chapters:
            if getattr(ch, "needs_language_review", False):
                st.warning(
                    t(
                        "chapter_lang_review_warning",
                        reason=f"Bab {ch.chapter_index} ({ch.chapter_title}): {getattr(ch, 'language_warning', '') or ''}",
                    )
                )

        with st.expander(t("expander_view_script"), expanded=False):
            for ch in manifest.chapters:
                st.markdown(t("script_chapter_meta", idx=ch.chapter_index, title=ch.chapter_title, groups=len(ch.tts_groups)))
                ch_txt = "\n\n".join(g.paragraph_text for g in ch.tts_groups if g.paragraph_text)
                st.text_area(t("script_chapter_text_label", idx=ch.chapter_index), value=ch_txt, height=120, disabled=True, key=f"view_ch_{project_id}_{ch.chapter_index}")

        st.write("---")
        st.markdown(f"##### {t('script_reimport_question')}")

    # Two clear side-by-side cards (D3)
    col_import, col_ai = st.columns(2)

    with col_import:
        with st.container(border=True):
            st.markdown(f"### {t('card_import_script')}")
            st.caption(t("card_import_script_desc"))

            uploaded_file = st.file_uploader(
                t("field_upload_script"),
                type=["md", "txt"],
                key=f"file_uploader_{project_id}",
            )
            pasted_text = st.text_area(
                t("field_paste_script"),
                height=130,
                placeholder=t("field_paste_script_placeholder"),
                key=f"pasted_script_{project_id}",
            )

            with st.expander(t("expander_script_format")):
                st.markdown(t("script_format_example"))

            if st.button(t("btn_submit_import"), type="primary", key=f"btn_submit_import_{project_id}", disabled=is_job_running):
                content = ""
                if uploaded_file is not None:
                    content = uploaded_file.getvalue().decode("utf-8", errors="replace")
                elif pasted_text and pasted_text.strip():
                    content = pasted_text.strip()

                if not content.strip():
                    st.error(t("err_import_empty"))
                else:
                    try:
                        stats = update_project_script(p_dir, content.strip())
                        st.success(t("script_import_success", count=stats["chapters_added"] + stats["chapters_modified"]))
                        time.sleep(0.5)
                        st.rerun()
                    except Exception as exc:
                        _render_friendly_error(t("err_script_import_friendly"), exc)

    with col_ai:
        with st.container(border=True):
            st.markdown(f"### {t('ai_script_title')}")
            st.caption(t("ai_script_desc"))

            llm_status = check_llm_status()
            llm_ok = bool(llm_status.get("configured", False))
            active_prov_id = str(llm_status.get("provider") or "")
            active_prov_label = str(llm_status.get("provider_label") or active_prov_id)
            active_model = str(llm_status.get("model") or "-")
            active_model_disp = _format_model_with_tier(active_model, llm_status.get("billing_tier"))
            use_fake = st.session_state.get(f"fake_toggle_{project_id}", False)

            st.markdown(t("ai_active_provider_model", provider=active_prov_label, model=active_model_disp))
            if llm_status.get("used_safe_default"):
                st.caption(t("ai_model_safe_default_note", model=active_model))
            if llm_ok:
                st.caption(t("ai_provider_ready_badge", masked=llm_status.get("masked_key") or "OK"))
            elif llm_status.get("is_invalid"):
                st.warning(f"{llm_status['message']}")
            elif not use_fake:
                st.warning(llm_status.get("message") or t("ai_missing_key_warning"))

            col_llm_test, col_llm_cfg = st.columns(2)
            with col_llm_test:
                if st.button(t("btn_test_llm_connection"), key=f"btn_test_llm_{project_id}", disabled=(not llm_ok or is_job_running), use_container_width=True):
                    ok_conn, conn_msg, conn_lat = test_llm_connection()
                    st.session_state[f"llm_conn_res_{project_id}"] = (ok_conn, conn_msg, conn_lat)
            with col_llm_cfg:
                if st.button(t("btn_change_llm_provider"), key=f"btn_chg_llm_{project_id}", use_container_width=True):
                    _open_mpt_settings_dialog("llm")

            conn_res = st.session_state.get(f"llm_conn_res_{project_id}")
            if conn_res:
                ok_conn, conn_msg, conn_lat = conn_res
                if ok_conn:
                    clean_msg = (conn_msg or "").strip()
                    if clean_msg:
                        st.success(t("llm_connection_ok", latency=conn_lat, msg=clean_msg))
                    else:
                        st.success(t("llm_connection_ok_plain", latency=conn_lat))
                else:
                    _render_llm_friendly_error_box(
                        project_id,
                        conn_msg,
                        active_model=active_model,
                        key_suffix="conn",
                    )

            with st.expander(t("expander_ai_options"), expanded=False):
                custom_topic = st.text_input(t("field_ai_topic"), value=getattr(manifest, "topic", None) or manifest.sub_niche_focus or "", key=f"ai_topic_{project_id}")

                if supports_model_listing(active_prov_id):
                    if st.button(t("btn_load_llm_models"), key=f"btn_load_models_{project_id}", disabled=is_job_running):
                        fetched_models, fetch_err = list_llm_models(active_prov_id, force_refresh=True)
                        st.session_state[f"llm_models_cache_{active_prov_id}"] = (fetched_models, fetch_err)

                    cached_models, fetch_err = st.session_state.get(f"llm_models_cache_{active_prov_id}", ([], None))
                    if fetch_err:
                        st.caption(t("llm_models_load_fallback", error=fetch_err))
                    elif cached_models:
                        st.caption(t("llm_models_loaded_count", count=len(cached_models), provider=active_prov_label))
                        sel_idx = cached_models.index(active_model) if active_model in cached_models else 0
                        picked_model = st.selectbox(
                            t("field_llm_model_select"),
                            options=cached_models,
                            index=sel_idx,
                            key=f"sel_llm_model_{project_id}_{active_prov_id}",
                        )
                    else:
                        picked_model = ""
                else:
                    picked_model = ""

                manual_model = st.text_input(
                    t("field_llm_model_custom"),
                    value=picked_model or ("" if active_model == "-" else active_model),
                    key=f"txt_llm_model_{project_id}_{active_prov_id}",
                )
                if st.button(t("btn_apply_llm_model"), key=f"btn_apply_model_{project_id}", disabled=is_job_running):
                    chosen_model = (manual_model or picked_model or "").strip()
                    if chosen_model:
                        set_active_llm_model(chosen_model, active_prov_id)
                        st.success(t("llm_model_saved_success", model=chosen_model))
                        st.rerun()

                fake_val = st.checkbox(t("field_use_fake_provider"), value=use_fake, key=f"fake_cb_{project_id}")
                if fake_val != use_fake:
                    st.session_state[f"fake_toggle_{project_id}"] = fake_val
                    st.rerun()

            if use_fake:
                st.error(t("ai_fake_mode_banner"))

            outline_exists = (p_dir / "script" / "outline.json").is_file()
            script_partial = outline_exists and status != "selesai"
            if script_partial and any((p_dir / "script").glob("chapter_*.md")):
                st.info(t("llm_completed_chapters_preserved"))

            ai_disabled = is_job_running or (not llm_ok and not use_fake)
            if is_job_running:
                ai_btn_label = t("btn_ai_writing")
            elif script_partial:
                ai_btn_label = t("btn_ai_resume_chapters")
            elif len(manifest.chapters) == 0:
                ai_btn_label = t("btn_ai_start")
            else:
                ai_btn_label = t("btn_ai_regenerate")

            if st.button(ai_btn_label, type="primary" if not ai_disabled else "secondary", disabled=ai_disabled, key=f"btn_start_ai_{project_id}"):
                provider_arg = ["--provider", "fake"] if use_fake else []
                if script_partial:
                    cmd = jobs.build_longform_cli_command("script", "write", project_id, *provider_arg)
                    step_name = "script_generate"
                else:
                    topic = custom_topic.strip() if custom_topic.strip() else (getattr(manifest, "topic", None) or manifest.title)
                    dur_val = getattr(manifest, "target_duration_minutes", None)
                    dur_args = ["--minutes", str(dur_val)] if dur_val else []
                    cmd = jobs.build_longform_cli_command("script", "outline", project_id, "--topic", topic, *dur_args, "--write", *provider_arg)
                    step_name = "script_outline"
                _start_job_safe(project_id, step_name, cmd)

    # Chapter Editor & Quality Report if chapters exist
    if manifest.chapters:
        with st.expander(t("expander_chapter_editor"), expanded=False):
            st.write(t("chapter_editor_heading"))
            ch_indices = [ch.chapter_index for ch in manifest.chapters]
            sel_ch_idx = st.selectbox(
                t("field_select_chapter_edit"),
                ch_indices,
                format_func=lambda idx: t("chapter_option_format", idx=idx, title=next((c.chapter_title for c in manifest.chapters if c.chapter_index == idx), "")),
                key=f"ch_ed_sel_{project_id}",
            )
            ch_obj = next((c for c in manifest.chapters if c.chapter_index == sel_ch_idx), None)
            if ch_obj:
                from app.services.longform.language_guard import (
                    check_language_compliance,
                    resolve_project_language,
                )

                ch_paras = "\n\n".join(g.paragraph_text for g in ch_obj.tts_groups if g.paragraph_text)
                proj_lang = resolve_project_language(manifest)
                live_lang = check_language_compliance(ch_paras, proj_lang, title_text=ch_obj.chapter_title)
                if getattr(ch_obj, "needs_language_review", False) or not live_lang["ok"]:
                    lang_reason = getattr(ch_obj, "language_warning", None) or live_lang["reason"]
                    st.warning(t("chapter_lang_review_warning", reason=lang_reason))

                new_ch_text = st.text_area(t("field_chapter_narration_text"), value=ch_paras, height=180, key=f"ch_txt_{project_id}_{sel_ch_idx}")
                col_ch_save, col_ch_rewrite = st.columns(2)
                with col_ch_save:
                    if st.button(t("btn_save_chapter"), key=f"btn_save_ch_{project_id}_{sel_ch_idx}", use_container_width=True):
                        if new_ch_text.strip():
                            ch_file = p_dir / "script" / f"chapter_{sel_ch_idx:02d}.md"
                            ch_file.parent.mkdir(parents=True, exist_ok=True)
                            ch_file.write_text(f"## {ch_obj.chapter_title}\n\n{new_ch_text.strip()}\n", encoding="utf-8")
                            st.success(t("chapter_saved_success", idx=sel_ch_idx))
                            time.sleep(0.5)
                            st.rerun()
                with col_ch_rewrite:
                    rewrite_disabled = is_job_running or (not check_has_llm_key() and not st.session_state.get(f"fake_toggle_{project_id}", False))
                    if st.button(
                        t("btn_rewrite_chapter"),
                        disabled=rewrite_disabled,
                        key=f"btn_rewrite_ch_{project_id}_{sel_ch_idx}",
                        use_container_width=True,
                    ):
                        provider_arg = ["--provider", "fake"] if st.session_state.get(f"fake_toggle_{project_id}", False) else []
                        cmd = jobs.build_longform_cli_command(
                            "script", "rewrite", project_id, "--chapter", str(sel_ch_idx), *provider_arg
                        )
                        _start_job_safe(project_id, "script_generate", cmd)

            st.write("---")
            st.write(t("quality_report_heading"))
            try:
                checker = ScriptQualityChecker(p_dir)
                q_report = checker.inspect()
                col_q1, col_q2 = st.columns(2)
                with col_q1:
                    render_metric_tile(t("metric_language_warnings"), str(len(q_report.get("language_warnings", []))))
                    render_metric_tile(t("metric_hook_violations"), str(len(q_report.get("hook_violations", []))))
                with col_q2:
                    render_metric_tile(t("metric_cliche_matches"), str(len(q_report.get("cliche_matches", []))))
                    render_metric_tile(t("metric_verify_claims"), str(len(q_report.get("verify_claims", []))))
                for lw in q_report.get("language_warnings", []):
                    st.warning(t("chapter_lang_review_warning", reason=f"Ch {lw['chapter']} ({lw['title']}): {lw['reason']}"))
            except Exception as exc:
                logger.warning(f"Script quality report failed: {exc}")
                st.caption(t("err_quality_report_unavailable"))


# --- Step 2: Suara Flow (C4.3) ---

def render_voice_step_flow(
    project_id: str,
    p_dir: Path,
    manifest: ProjectManifest,
    step_info: Dict[str, Any],
    is_job_running: bool = False,
    is_running: bool = False,
):
    """Render Step 2: Suara with single adaptive primary button and voice options expander."""
    job_state = jobs.get_job_state(project_id)
    is_job_running = is_job_running or is_running or bool(job_state and job_state.get("status") == "running")

    status = step_info["status"]
    voice_svc = VoiceService(p_dir)

    # Clean Header (Mockup style)
    col_hdr, col_act = st.columns([3, 1])
    with col_hdr:
        st.markdown(f"### {t('voice_step_title')}")
        st.caption(_get_step_subtitle("voice", status, is_job_running))
    with col_act:
        st.write("")
        if is_job_running:
            st.button(t("btn_running_voice"), disabled=True, type="primary", use_container_width=True)
        elif status == "selesai":
            if st.button(t("btn_proceed_visual"), type="primary", key=f"btn_next_visual_{project_id}", use_container_width=True):
                st.session_state[f"active_step_{project_id}"] = "visual"
                st.rerun()
        elif status == "terhenti":
            if st.button(t("btn_resume_voice"), type="primary", key=f"btn_resume_voice_{project_id}", use_container_width=True):
                cmd = jobs.build_longform_cli_command("audio", "synth", project_id)
                _start_job_safe(project_id, "audio_synthesize", cmd)
        elif status == "perlu_diperbarui":
            if st.button(t("btn_stale_voice"), type="primary", key=f"btn_stale_voice_{project_id}", use_container_width=True):
                cmd = jobs.build_longform_cli_command("audio", "synth", project_id)
                _start_job_safe(project_id, "audio_synthesize", cmd)
        else:  # belum
            btn_disabled = bool(len(manifest.chapters) == 0) or is_job_running
            if st.button(t("btn_start_voice"), type="primary", disabled=btn_disabled, key=f"btn_start_voice_{project_id}", use_container_width=True):
                cmd = jobs.build_longform_cli_command("audio", "synth", project_id)
                _start_job_safe(project_id, "audio_synthesize", cmd)

    if status == "belum" and len(manifest.chapters) == 0:
        st.caption(t("voice_missing_script_warning"))

    # Options in Expander
    with st.expander(t("expander_voice_options"), expanded=False):
        configured_tts = list_configured_tts_providers()
        tts_ids = [item["id"] for item in configured_tts] or ["edge"]
        tts_labels = {item["id"]: item["label"] for item in configured_tts}
        curr_tts_prov = normalize_tts_provider_id(manifest.preset_snapshot.get("tts_provider", "edge"))
        if curr_tts_prov not in tts_ids:
            curr_tts_prov = "edge"

        col_tp, col_tcfg = st.columns([3, 1])
        with col_tp:
            sel_tts_provider = st.selectbox(
                t("field_tts_provider"),
                options=tts_ids,
                index=tts_ids.index(curr_tts_prov),
                format_func=lambda pid: tts_labels.get(pid, pid),
                key=f"tts_prov_sel_{project_id}",
            )
        with col_tcfg:
            st.write("")
            if st.button(t("btn_open_tts_settings"), key=f"btn_tts_settings_{project_id}", use_container_width=True):
                _open_mpt_settings_dialog("general")

        if not provider_has_word_timings(sel_tts_provider):
            st.info(t("voice_whisper_sync_note"))

        col_vf, col_vr = st.columns([2, 1])
        with col_vf:
            if sel_tts_provider in ("edge", "azure-tts-v2"):
                all_opt = t("voice_lang_all")
                lang_filter = st.selectbox(t("field_voice_lang_filter"), ["en-US", "id-ID", all_opt], index=0, key=f"vf_lang_{project_id}")
                filter_val = None if lang_filter == all_opt else lang_filter
            else:
                filter_val = None

            voices = list_available_voices(filter_val, provider=sel_tts_provider)
            if not voices:
                voices = ["en-US-ChristopherNeural-Male", "en-US-GuyNeural-Male", "id-ID-ArdiNeural-Male"]

            curr_v = manifest.preset_snapshot.get("default_voice", voices[0])
            v_idx = voices.index(curr_v) if curr_v in voices else 0
            sel_voice = st.selectbox(t("field_voice_character"), voices, index=v_idx, key=f"voice_picker_{project_id}_{sel_tts_provider}")

        with col_vr:
            curr_rate = float(manifest.preset_snapshot.get("voice_rate", 1.0) or 1.0)
            sel_rate = st.slider(t("field_voice_speed"), min_value=0.8, max_value=1.5, value=curr_rate, step=0.05, key=f"vr_slider_{project_id}")

        col_samp, col_save = st.columns(2)
        with col_samp:
            if st.button(t("btn_listen_sample"), key=f"btn_test_sample_{project_id}"):
                with st.spinner(t("spinner_synth_sample")):
                    try:
                        with tempfile.TemporaryDirectory() as tmp_dir:
                            adapter = TTSAdapter(
                                provider=sel_tts_provider,
                                voice_name=sel_voice,
                                voice_rate=sel_rate,
                                output_dir=Path(tmp_dir),
                            )
                            from app.services.longform.schema import TtsGroup
                            dummy_grp = TtsGroup(group_index=1, paragraph_text="This is a narrator sample for the longform documentary.")
                            res = adapter.synthesize_group(dummy_grp, chapter_index=1, force_refresh=True)
                            st.audio(res.wav_file.read_bytes(), format="audio/wav")
                    except Exception as exc:
                        _render_friendly_error(t("err_voice_sample_friendly"), exc)

        with col_save:
            if st.button(t("btn_apply_voice"), key=f"btn_apply_voice_{project_id}"):
                voice_svc.set_voice(sel_voice, sel_rate, tts_provider=sel_tts_provider)
                st.success(t("voice_applied_success"))
                time.sleep(0.5)
                st.rerun()

        # Video Preview Section
        if manifest.chapters:
            st.write("---")
            st.write(t("voice_preview_heading"))

            ch1 = next((c for c in manifest.chapters if c.chapter_index == 1), manifest.chapters[0])
            ch1_audio_ready = bool(
                ch1
                and ch1.tts_groups
                and all(
                    (p_dir / (g.audio.file_path or f"assets/audio/groups/ch{ch1.chapter_index}_g{g.group_index}.wav")).is_file()
                    for g in ch1.tts_groups
                )
            )

            if st.button(t("btn_create_voice_preview"), disabled=(not ch1_audio_ready or is_job_running), key=f"btn_prev_video_{project_id}"):
                with st.spinner(t("spinner_render_preview")):
                    try:
                        wav_p, srt_p, mp4_p = voice_svc.generate_preview(1)
                        if mp4_p.is_file():
                            st.session_state[f"preview_mp4_{project_id}"] = str(mp4_p)
                            st.success(t("voice_preview_ready"))
                        else:
                            st.error(t("err_voice_preview_friendly"))
                    except Exception as exc:
                        _render_friendly_error(t("err_voice_preview_friendly"), exc)

            if not ch1_audio_ready:
                st.caption(t("voice_preview_prereq_caption"))

            cached_prev = st.session_state.get(f"preview_mp4_{project_id}")
            if cached_prev and Path(cached_prev).is_file():
                st.video(cached_prev)


# --- Step 3: Visual Flow (7B.1) ---

def get_shot_narration(chapter: Chapter, shot: Shot) -> str:
    """Extract narration text associated with shot segment_indices or query (V2)."""
    if shot.segment_indices and getattr(chapter, "tts_groups", None):
        matched = []
        for g in chapter.tts_groups:
            for s in getattr(g, "segments", []):
                if s.segment_index in shot.segment_indices and s.narration_text:
                    matched.append(s.narration_text.strip())
        if matched:
            return " ".join(matched)
    return shot.effective_query or shot.search_query or shot.visual_prompt or ""


def get_shot_thumbnail_path(project_dir: Path, shot: Shot) -> Optional[Path]:
    """Get fast real thumbnail image path for gallery preview (W2).

    1. Checks project cache: assets/visual/thumbnails/<shot_id>.jpg
    2. If missing, extracts a real frame at the middle of duration (width 320 px)
       from shot.rendered_clip (or shot.asset_path if clip not built yet).
    3. Cached once and reused across subsequent gallery opens.
    """
    t_dir = project_dir / "assets" / "visual" / "thumbnails"
    t_file = t_dir / f"{shot.shot_id}.jpg"
    if t_file.is_file() and t_file.stat().st_size > 0:
        return t_file

    # 1. Primary source: rendered clip
    src_file = None
    if shot.rendered_clip:
        vc = project_dir / shot.rendered_clip
        if vc.is_file():
            src_file = vc

    # 2. Secondary source: raw asset
    if not src_file and shot.asset_path:
        va = project_dir / shot.asset_path
        if va.is_file():
            src_file = va

    if not src_file:
        return None

    try:
        t_dir.mkdir(parents=True, exist_ok=True)
        ext = src_file.suffix.lower()
        if ext in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
            dur = shot.duration_seconds if shot.duration_seconds and shot.duration_seconds > 0 else 3.0
            ss = max(dur / 2.0, 0.1)
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{ss:.2f}",
                "-i", str(src_file),
                "-vframes", "1",
                "-vf", "scale=320:-1",
                str(t_file),
            ]
            subprocess.run(cmd, capture_output=True, timeout=10)
            if t_file.is_file() and t_file.stat().st_size > 0:
                return t_file
        elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            from PIL import Image
            with Image.open(src_file) as im:
                im_rgb = im.convert("RGB")
                w, h = im_rgb.size
                if w > 0:
                    new_h = int(h * (320 / w))
                    im_resized = im_rgb.resize((320, max(new_h, 1)), Image.Resampling.LANCZOS)
                    im_resized.save(t_file, "JPEG", quality=85)
                    return t_file
    except Exception as exc:
        logger.warning(f"Failed to extract thumbnail for {shot.shot_id} from {src_file}: {exc}")

    return None


def get_candidate_thumbnail_path(project_dir: Path, candidate: Dict[str, Any]) -> Optional[Path]:
    """Get or extract 320px cached thumbnail for an alternative visual candidate (X2)."""
    cand_id = candidate.get("candidate_id") or str(abs(hash(candidate.get("url", ""))))
    cand_t_dir = project_dir / "assets" / "visual" / "thumbnails" / "candidates"
    cand_t_file = cand_t_dir / f"{cand_id}.jpg"
    if cand_t_file.is_file() and cand_t_file.stat().st_size > 0:
        return cand_t_file

    cand_t_dir.mkdir(parents=True, exist_ok=True)

    # 1. Local path
    local_p = candidate.get("local_path") or candidate.get("file_path")
    src = None
    if local_p:
        lp = Path(local_p)
        if lp.is_file():
            src = lp
        elif (project_dir / local_p).is_file():
            src = project_dir / local_p

    url = candidate.get("url")
    dur = float(candidate.get("duration_seconds") or 3.0)
    ss = max(dur / 2.0, 0.5)

    try:
        if src:
            ext = src.suffix.lower()
            if ext in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{ss:.2f}",
                    "-i", str(src),
                    "-vframes", "1",
                    "-vf", "scale=320:-1",
                    str(cand_t_file),
                ]
                subprocess.run(cmd, capture_output=True, timeout=10)
            elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                from PIL import Image
                with Image.open(src) as im:
                    im_rgb = im.convert("RGB")
                    w, h = im_rgb.size
                    new_h = int(h * (320 / w)) if w > 0 else 180
                    im.resize((320, max(new_h, 1)), Image.Resampling.LANCZOS).save(cand_t_file, "JPEG", quality=85)
        elif url:
            cmd = [
                "ffmpeg", "-y",
                "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "-ss", f"{ss:.2f}",
                "-i", url,
                "-vframes", "1",
                "-vf", "scale=320:-1",
                str(cand_t_file),
            ]
            subprocess.run(cmd, capture_output=True, timeout=10)

        if cand_t_file.is_file() and cand_t_file.stat().st_size > 0:
            return cand_t_file
    except Exception as exc:
        logger.warning(f"Could not generate candidate thumbnail for {cand_id}: {exc}")

    return None


def render_visual_step_flow(
    project_id: str,
    p_dir: Path,
    manifest: ProjectManifest,
    step_info: Dict[str, Any],
    is_job_running: bool = False,
):
    """Render Step 3: Visual pipeline, interactive gallery, replacement panel, and cache maintenance (7B.1)."""
    from app.services.longform.visual.cache import get_cache_status, prune_cache
    from app.services.longform.visual.local import LocalLibraryManager
    from app.services.longform.visual.service import VisualService

    job_state = jobs.get_job_state(project_id)
    is_job_running = is_job_running or bool(job_state and job_state.get("status") == "running")

    # --- V1: Source Selection Configuration (checked first to pass target_prov to action button) ---
    from app.config import config
    pixabay_keys = config.app.get("pixabay_api_keys", []) or []
    has_pixabay = bool((isinstance(pixabay_keys, list) and any(k.strip() for k in pixabay_keys)) or (isinstance(pixabay_keys, str) and pixabay_keys.strip()))
    raw_pk = pixabay_keys[0] if (isinstance(pixabay_keys, list) and pixabay_keys) else str(pixabay_keys)
    mask_pixabay = mask_api_key(raw_pk) if has_pixabay else ""

    coverr_keys = config.app.get("coverr_api_keys", []) or []
    has_coverr = bool((isinstance(coverr_keys, list) and any(k.strip() for k in coverr_keys)) or (isinstance(coverr_keys, str) and coverr_keys.strip()))
    raw_ck = coverr_keys[0] if (isinstance(coverr_keys, list) and coverr_keys) else str(coverr_keys)
    mask_coverr = mask_api_key(raw_ck) if has_coverr else ""

    from app.services.longform.visual.ai_image import estimate_ai_image_usage, get_ai_image_readiness
    from app.services.longform.visual.ai_relevance import (
        check_shot_relevance,
        estimate_relevance_check_calls,
        get_vision_readiness,
    )
    from app.services.longform.visual.ai_video import (
        AI_VIDEO_PROVIDER_ORDER,
        build_ai_video_prompt,
        estimate_ai_video_cost,
        get_ai_video_provider_meta,
        get_model_supported_durations,
        list_configured_ai_video_providers,
        list_supported_ai_video_providers,
        plan_needs_review_ai_video_batch,
        round_up_supported_duration,
    )
    from app.services.longform.visual.ai_keywords import (
        estimate_ai_keyword_calls,
        infer_keyword_source,
    )
    from app.services.longform.language_guard import resolve_project_language
    from app.services.longform.visual.keyword_translator import (
        is_non_english_language,
        translate_single_query_to_english,
    )

    ai_readiness = get_ai_image_readiness()
    has_ai_image = bool(ai_readiness.get("ready", False))
    vision_info = get_vision_readiness()
    ai_vid_providers = list_configured_ai_video_providers()

    _cb_pix = st.session_state.get(f"cb_prov_pixabay_{project_id}", has_pixabay) if has_pixabay else False
    _cb_cov = st.session_state.get(f"cb_prov_coverr_{project_id}", has_coverr) if has_coverr else False
    _cb_ai = st.session_state.get(f"cb_prov_ai_{project_id}", False) if has_ai_image else False
    _cb_loc = st.session_state.get(f"cb_prov_local_{project_id}", True)
    _cb_ai_rel = bool(st.session_state.get(f"cb_ai_rel_{project_id}", False)) if vision_info.get("vision_supported") else False
    target_prov = "ai_image" if _cb_ai and not (_cb_pix or _cb_cov) else (
        "pixabay" if _cb_pix else ("coverr" if _cb_cov else ("ai_image" if _cb_ai else ("local" if _cb_loc else None)))
    )

    # --- Clean Header (Mockup 2 V3) ---
    col_hdr, col_act = st.columns([3, 1])
    with col_hdr:
        st.markdown(f"### {t('visual_step_title')}")
        st.caption(_get_step_subtitle("visual", step_info["status"], is_job_running))
    with col_act:
        st.write("")
        if is_job_running:
            st.button(t("visual_btn_running"), disabled=True, type="primary", key=f"btn_running_vis_{project_id}", use_container_width=True)
        else:
            st_val = step_info["status"]
            if st_val == "selesai":
                if st.button(t("visual_btn_proceed_render"), type="primary", key=f"btn_next_render_{project_id}", use_container_width=True):
                    st.session_state[f"active_step_{project_id}"] = "render"
                    st.rerun()
            elif st_val == "terhenti":
                if st.button(t("visual_btn_resume"), type="primary", key=f"btn_resume_vis_{project_id}", use_container_width=True):
                    vis_args = ["visual", "pipeline", project_id, "--allow-fallback"]
                    if target_prov:
                        vis_args.extend(["--provider", target_prov])
                    if _cb_ai_rel:
                        vis_args.append("--check-ai-relevance")
                    _start_job_safe(project_id, "visual_pipeline", jobs.build_longform_cli_command(*vis_args))
            elif st_val == "perlu_diperbarui":
                if st.button(t("visual_btn_rebuild_stale"), type="primary", key=f"btn_update_vis_{project_id}", use_container_width=True):
                    vis_args = ["visual", "pipeline", project_id, "--allow-fallback"]
                    if target_prov:
                        vis_args.extend(["--provider", target_prov])
                    if _cb_ai_rel:
                        vis_args.append("--check-ai-relevance")
                    _start_job_safe(project_id, "visual_pipeline", jobs.build_longform_cli_command(*vis_args))
            else:  # belum
                st_all = compute_project_step_status(p_dir)
                script_st = st_all["steps"]["script"]["status"]
                voice_st = st_all["steps"]["voice"]["status"]
                script_done = (script_st == "selesai")
                voice_done = (voice_st == "selesai")
                btn_disabled = not (script_done and voice_done)

                if btn_disabled:
                    if not script_done and not voice_done:
                        st.caption(t("visual_prereq_both"))
                    elif not script_done:
                        st.caption(t("visual_prereq_script"))
                    else:
                        st.caption(t("visual_prereq_voice"))

                if st.button(t("visual_btn_start"), type="primary", disabled=btn_disabled, key=f"btn_start_vis_{project_id}", use_container_width=True):
                    vis_args = ["visual", "pipeline", project_id, "--allow-fallback"]
                    if target_prov:
                        vis_args.extend(["--provider", target_prov])
                    if _cb_ai_rel:
                        vis_args.append("--check-ai-relevance")
                    _start_job_safe(project_id, "visual_pipeline", jobs.build_longform_cli_command(*vis_args))

    # --- V1: Summary Metric Tiles (Mockup 2 V3) ---
    total_shots = sum(len(ch.shots) for ch in manifest.chapters)
    built_shots = sum(
        1 for ch in manifest.chapters for s in ch.shots
        if s.rendered_clip and (p_dir / s.rendered_clip).is_file()
    )
    needs_review_count = sum(
        1 for ch in manifest.chapters for s in ch.shots
        if getattr(s, "needs_review", False)
    )
    locked_count = sum(
        1 for ch in manifest.chapters for s in ch.shots
        if getattr(s, "locked", False)
    )
    placeholder_count = sum(
        1 for ch in manifest.chapters for s in ch.shots
        if getattr(s, "is_fake", False) or getattr(s, "actual_source", "") == "fake"
    )

    used_sources_set = set()
    for ch in manifest.chapters:
        for s in ch.shots:
            has_asset = bool(s.asset_path and (p_dir / s.asset_path).is_file())
            has_clip = bool(s.rendered_clip and (p_dir / s.rendered_clip).is_file())
            if has_asset or has_clip:
                if getattr(s, "is_fake", False) or s.actual_source == "fake":
                    used_sources_set.add(t("source_placeholder"))
                else:
                    src = s.actual_source or s.requested_source or "stock"
                    used_sources_set.add(src if str(src).startswith("AI Video") else str(src).capitalize())
    sources_str = ", ".join(sorted(used_sources_set)) if used_sources_set else t("source_none_yet")

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    with col_m1:
        render_metric_tile(t("metric_clips_ready"), f"{built_shots} / {total_shots}", status="success" if built_shots == total_shots and total_shots > 0 else "")
    with col_m2:
        render_metric_tile(t("metric_needs_review"), str(needs_review_count), status="warning" if needs_review_count > 0 else "")
    with col_m3:
        render_metric_tile(t("metric_locked"), str(locked_count), status="accent" if locked_count > 0 else "")
    with col_m4:
        render_metric_tile(t("metric_sources_used"), sources_str)

    # --- Footage Sources Expander (V3: Collapsible, saves vertical space) ---
    with st.expander(t("expander_footage_sources"), expanded=False):
        col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)
        cb_pixabay = col_s1.checkbox(
            t("prov_pixabay_key", mask=mask_pixabay) if has_pixabay else t("prov_pixabay_no_key"),
            value=has_pixabay,
            disabled=not has_pixabay,
            key=f"cb_prov_pixabay_{project_id}",
        )
        cb_coverr = col_s2.checkbox(
            t("prov_coverr_key", mask=mask_coverr) if has_coverr else t("prov_coverr_no_key"),
            value=has_coverr,
            disabled=not has_coverr,
            key=f"cb_prov_coverr_{project_id}",
        )
        col_s3.checkbox(
            t("prov_pexels_no_key"),
            value=False,
            disabled=True,
            key=f"cb_prov_pexels_{project_id}",
        )
        cb_local = col_s4.checkbox(
            t("prov_local_ready"),
            value=True,
            disabled=False,
            key=f"cb_prov_local_{project_id}",
        )
        cb_ai = col_s5.checkbox(
            t("prov_ai_ready", provider=ai_readiness["backend_label"]) if has_ai_image else t("prov_ai_no_mpt_key"),
            value=bool(st.session_state.get(f"cb_prov_ai_{project_id}", False)) if has_ai_image else False,
            disabled=not has_ai_image,
            help=None if has_ai_image else t("prov_ai_no_mpt_key_help"),
            key=f"cb_prov_ai_{project_id}",
        )
        if has_ai_image and cb_ai:
            ai_est = estimate_ai_image_usage(manifest, p_dir)
            st.info(
                t(
                    "prov_ai_estimate_notice",
                    provider=ai_readiness["backend_label"],
                    count=ai_est["estimated_count"],
                )
            )
        elif not has_ai_image:
            if col_s5.button(t("btn_open_image_settings"), key=f"btn_ai_img_cfg_{project_id}"):
                _open_mpt_settings_dialog("image")

        st.divider()
        vision_ok = bool(vision_info.get("vision_supported", False))
        vision_model_raw = str(vision_info.get("model") or vision_info.get("provider") or "-")
        vision_model_disp = _format_model_with_tier(vision_model_raw, vision_info.get("billing_tier"))
        cb_ai_rel = st.checkbox(
            t("chk_ai_relevance_vision"),
            value=bool(st.session_state.get(f"cb_ai_rel_{project_id}", False)) if vision_ok else False,
            disabled=not vision_ok,
            key=f"cb_ai_rel_{project_id}",
        )
        st.caption(t("ai_relevance_active_model_caption", model=vision_model_disp))
        if not vision_ok:
            st.caption(t("ai_relevance_no_vision_notice"))
        elif cb_ai_rel:
            est_rel = estimate_relevance_check_calls(manifest)
            st.info(
                t(
                    "ai_relevance_estimate_notice",
                    model=vision_model_disp,
                    calls=est_rel["estimated_calls"],
                    cached=est_rel["cached"],
                )
            )

    if needs_review_count > 0 and ai_vid_providers:
        if st.button(
            t("btn_bulk_ai_video_review", count=needs_review_count),
            key=f"btn_bulk_ai_video_{project_id}",
            disabled=is_job_running,
        ):
            st.session_state[f"ai_video_confirm_single_{project_id}"] = None
            st.session_state[f"ai_video_confirm_batch_{project_id}"] = True
            st.rerun()

    # --- F8: Update Visual Keywords with AI (per chapter / all chapters) ---
    if total_shots > 0:
        from app.services.longform.llm_adapter import LLMCreditError, LLMModelNotFoundError

        with st.popover(t("btn_update_keywords_ai"), disabled=is_job_running):
            cur_ch_pill = st.session_state.get(f"vis_ch_sel_{project_id}")
            cur_ch_idx = None
            for _ch in manifest.chapters:
                if cur_ch_pill == t("chapter_pill_format", idx=_ch.chapter_index):
                    cur_ch_idx = _ch.chapter_index
                    break

            scope_opts = ["all"] + ([f"ch:{cur_ch_idx}"] if cur_ch_idx is not None else [])
            if len(scope_opts) == 1 and manifest.chapters:
                first_ch = manifest.chapters[0].chapter_index
                scope_opts.append(f"ch:{first_ch}")

            def _fmt_kw_scope(val: str) -> str:
                if val == "all":
                    return t("ai_kw_scope_all")
                ch_n = val.split(":", 1)[1]
                return t("ai_kw_scope_chapter", ch=ch_n)

            kw_scope = st.radio(
                t("ai_kw_scope_label"),
                options=scope_opts,
                format_func=_fmt_kw_scope,
                key=f"radio_ai_kw_scope_{project_id}",
            )
            sel_ch_filter = int(kw_scope.split(":", 1)[1]) if kw_scope.startswith("ch:") else None
            kw_est = estimate_ai_keyword_calls(manifest, chapter_index=sel_ch_filter, force=True)
            st.info(
                t(
                    "ai_kw_estimate_notice",
                    calls=kw_est["estimated_llm_calls"],
                    shots=kw_est["unlocked_shots"],
                    tags=kw_est["tag_shots"],
                )
            )
            llm_kw_status = check_llm_status()
            if not llm_kw_status.get("configured") or llm_kw_status.get("is_invalid"):
                st.warning(t("ai_kw_no_llm_fallback_notice"))

            if st.button(
                t("btn_confirm_update_ai_kw"),
                type="primary",
                key=f"btn_run_ai_kw_{project_id}",
                disabled=is_job_running or kw_est["unlocked_shots"] == 0,
                use_container_width=True,
            ):
                try:
                    with st.spinner(t("job_step_visual_ai_keywords")):
                        with ProjectLock(p_dir):
                            vis_kw_svc = VisualService(p_dir)
                            _m_kw, kw_res = vis_kw_svc.update_keywords_with_ai(
                                chapter_index=sel_ch_filter,
                                force=True,
                                mark_stale=True,
                            )
                    st.success(
                        t(
                            "ai_kw_updated_success",
                            updated=kw_res["updated"],
                            stale=kw_res["stale_marked"],
                        )
                    )
                    st.rerun()
                except (LLMCreditError, LLMModelNotFoundError) as exc:
                    _render_llm_friendly_error_box(
                        project_id,
                        str(exc),
                        active_model=str(llm_kw_status.get("model") or ""),
                        key_suffix="aikw",
                    )
                except Exception as exc:
                    _render_friendly_error(t("err_ai_kw_friendly"), exc)

    # --- V2: Interactive 2-Column Side-by-Side Gallery & Detail Layout (Mockup 2 V3) ---
    if total_shots > 0:
        st.write("---")
        col_gallery, col_detail = st.columns([1.6, 1.0])

        # All shots flattened & mapped
        all_shots_list = [s for ch in manifest.chapters for s in ch.shots]
        all_ch_map = {s.shot_id: ch for ch in manifest.chapters for s in ch.shots}

        # Resolve selected shot ID or default to first shot
        current_sel_id = st.session_state.get(f"selected_shot_{project_id}")
        if not current_sel_id or current_sel_id not in all_ch_map:
            current_sel_id = all_shots_list[0].shot_id if all_shots_list else None
            st.session_state[f"selected_shot_{project_id}"] = current_sel_id

        with col_gallery:
            st.markdown(f"#### {t('gallery_heading')}")

            # Compact Chapter Pills Selector (Mockup 2: Bab 1, Bab 2...)
            ch_names = [t("all_chapters_option")] + [
                t("chapter_pill_format", idx=ch.chapter_index)
                for ch in manifest.chapters
            ]
            ch_choice = st.pills(
                t("label_select_chapter"),
                options=ch_names,
                default=t("all_chapters_option"),
                key=f"vis_ch_sel_{project_id}",
                label_visibility="collapsed",
            )

            # Filter Mode Chips with Exact Counts (Mockup 2 V3)
            opt_all_f = f"{t('filter_all')} ({total_shots})"
            opt_rev_f = f"{t('filter_needs_review')} ({needs_review_count})"
            opt_lock_f = f"{t('filter_locked')} ({locked_count})"
            opt_ph_f = f"{t('filter_placeholder')} ({placeholder_count})"
            filter_mode = st.pills(
                t("label_filter_status"),
                options=[opt_all_f, opt_rev_f, opt_lock_f, opt_ph_f],
                default=opt_all_f,
                key=f"vis_filter_{project_id}",
                label_visibility="collapsed",
            )

            # Filter shots by chapter and status
            visible_shots = []
            for ch in manifest.chapters:
                ch_label = t("chapter_pill_format", idx=ch.chapter_index)
                if ch_choice != t("all_chapters_option") and ch_choice != ch_label:
                    continue
                for s in ch.shots:
                    if filter_mode == opt_rev_f and not getattr(s, "needs_review", False):
                        continue
                    if filter_mode == opt_lock_f and not getattr(s, "locked", False):
                        continue
                    if filter_mode == opt_ph_f and not (getattr(s, "is_fake", False) or getattr(s, "actual_source", "") == "fake"):
                        continue
                    visible_shots.append((ch, s))

            if not visible_shots:
                st.caption(t("no_shots_match_filter"))
            else:
                # 3-column cards grid inside col_gallery (Mockup 2 V3)
                for r_idx in range(0, len(visible_shots), 3):
                    g_cols = st.columns(3)
                    for c_idx, (ch, shot) in enumerate(visible_shots[r_idx:r_idx+3]):
                        with g_cols[c_idx]:
                            is_active_shot = (shot.shot_id == current_sel_id)

                            with st.container(border=True):
                                thumb_p = get_shot_thumbnail_path(p_dir, shot)
                                dur_s = f"{shot.duration_seconds:.1f}s" if shot.duration_seconds else ""
                                st.markdown(render_project_thumbnail_html(thumb_p, dur_s), unsafe_allow_html=True)

                                badges = []
                                if getattr(shot, "needs_review", False):
                                    badges.append(t("badge_needs_review"))
                                if getattr(shot, "locked", False):
                                    badges.append(t("badge_locked"))
                                if getattr(shot, "is_fake", False) or (getattr(shot, "actual_source", "") == "fake"):
                                    badges.append(t("badge_placeholder"))
                                badge_str = f" {' '.join(badges)}" if badges else ""

                                src = shot.actual_source or shot.requested_source or ("fake" if shot.is_fake else "stock")
                                st.caption(f"**#{shot.shot_index}** ({dur_s}) • `{src}`{badge_str}")

                                narr = get_shot_narration(ch, shot)
                                snippet = (narr[:40] + "...") if len(narr) > 40 else narr
                                if snippet:
                                    st.caption(f'"{html.escape(snippet)}"')

                                btn_lbl = t("btn_shot_selected", idx=shot.shot_index) if is_active_shot else t("btn_shot_detail", idx=shot.shot_index)
                                if st.button(
                                    btn_lbl,
                                    key=f"btn_sh_{project_id}_{shot.shot_id}",
                                    type="primary" if is_active_shot else "secondary",
                                    use_container_width=True,
                                ):
                                    st.session_state[f"selected_shot_{project_id}"] = shot.shot_id
                                    st.rerun()

        # Right Column: Detail Panel (Persistent side-by-side)
        with col_detail:
            target_shot = None
            target_chapter = None
            if current_sel_id in all_ch_map:
                target_shot = next((s for s in all_shots_list if s.shot_id == current_sel_id), None)
                target_chapter = all_ch_map.get(current_sel_id)

            if target_shot and target_chapter:
                with st.container(border=True):
                    col_dh, col_lock = st.columns([3.2, 1.8])
                    with col_dh:
                        dur_disp = f"{target_shot.duration_seconds:.1f}s" if target_shot.duration_seconds else ""
                        st.markdown(t("shot_detail_heading", idx=target_shot.shot_index, ch=target_chapter.chapter_index, dur=dur_disp))
                    with col_lock:
                        is_locked = getattr(target_shot, "locked", False)
                        lock_lbl = t("btn_unlock_shot") if is_locked else t("btn_lock_shot")
                        if st.button(lock_lbl, key=f"btn_lock_toggle_{project_id}_{target_shot.shot_id}", use_container_width=True):
                            vis_svc = VisualService(p_dir)
                            vis_svc.set_lock(manifest, target_shot.shot_id, not is_locked)
                            save_manifest(manifest, p_dir)
                            st.rerun()

                    # Video / Image Large Preview
                    clip_p = (p_dir / target_shot.rendered_clip) if target_shot.rendered_clip else None
                    asset_p = (p_dir / target_shot.asset_path) if target_shot.asset_path else None
                    with st.spinner(t("spinner_loading_video_preview")):
                        if clip_p and clip_p.is_file():
                            st.video(str(clip_p))
                        elif asset_p and asset_p.is_file() and asset_p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                            st.video(str(asset_p))
                        elif asset_p and asset_p.is_file():
                            st.image(str(asset_p), use_container_width=True)
                        else:
                            st.info(t("shot_clip_not_built"))

                    target_narr = get_shot_narration(target_chapter, target_shot)
                    st.markdown(t("shot_narration_label", text=html.escape(target_narr or "-")))

                    from app.services.longform.visual.query_extractor import extract_short_search_query
                    preset_snap = manifest.preset_snapshot or {}
                    short_q = target_shot.search_query or ""
                    if not short_q or len(short_q.split()) > 6 or short_q.strip() == (target_narr or "").strip():
                        short_q = extract_short_search_query(
                            target_narr,
                            visual_keywords=preset_snap.get("visual_keywords"),
                            topic_keywords=preset_snap.get("topic_keywords"),
                            required_subject_terms=preset_snap.get("required_subject_terms"),
                        )

                    kw_en_val = getattr(target_shot, "search_query_en", None)
                    kw_source_code = infer_keyword_source(target_chapter, target_shot)
                    kw_source_disp = t(f"kw_source_{kw_source_code}")
                    proj_lang_vis = resolve_project_language(manifest)

                    if kw_en_val:
                        st.markdown(t("shot_keywords_en_search_label", kw=html.escape(kw_en_val)))
                        st.markdown(t("shot_keywords_source_label", src=html.escape(kw_source_disp)))
                        if short_q and short_q.strip().lower() != kw_en_val.strip().lower():
                            if is_non_english_language(proj_lang_vis):
                                st.markdown(t("shot_keywords_orig_summary", orig=html.escape(short_q)))
                    else:
                        st.markdown(t("shot_keywords_en_search_label", kw=html.escape(short_q or "-")))
                        st.markdown(t("shot_keywords_source_label", src=html.escape(kw_source_disp)))

                    # F1: AI Relevance verdict & single-shot re-check button
                    rel_status = getattr(target_shot, "ai_relevance_status", None)
                    rel_reason = getattr(target_shot, "ai_relevance_reason", None) or "-"
                    if rel_status in ("ya", "tidak", "ragu"):
                        msg_rel = t(f"ai_relevance_result_{rel_status}", reason=rel_reason)
                        if rel_status == "ya":
                            st.success(msg_rel)
                        elif rel_status == "tidak":
                            st.error(msg_rel)
                        else:
                            st.warning(msg_rel)

                    if st.button(
                        t("btn_recheck_ai_relevance"),
                        key=f"btn_recheck_ai_{project_id}_{target_shot.shot_id}",
                        disabled=not bool(vision_info.get("vision_supported")) or is_job_running,
                    ):
                        try:
                            vis_svc_rel = VisualService(p_dir)
                            _ = vis_svc_rel
                            check_shot_relevance(
                                target_shot,
                                target_chapter,
                                p_dir,
                                manifest,
                                force_refresh=True,
                                clear_on_yes=True,
                            )
                            save_manifest(manifest, p_dir)
                            st.session_state.pop(f"chk_rev_{project_id}_{target_shot.shot_id}", None)
                            st.rerun()
                        except Exception as exc:
                            _render_friendly_error(t("err_ai_relevance_friendly"), exc)

                    # Needs review checkbox
                    is_rev = getattr(target_shot, "needs_review", False)
                    chk_rev = st.checkbox(t("chk_needs_review"), value=is_rev, key=f"chk_rev_{project_id}_{target_shot.shot_id}")
                    if chk_rev != is_rev:
                        target_shot.needs_review = chk_rev
                        save_manifest(manifest, p_dir)
                        st.rerun()

                    st.markdown("---")
                    st.markdown(t("visual_replace_heading"))
                    tab_cands, tab_srch, tab_up, tab_ai_vid = st.tabs([
                        t("tab_cand"),
                        t("tab_search"),
                        t("tab_upload"),
                        t("tab_ai_video"),
                    ])

                    with tab_cands:
                        raw_cands = target_shot.candidates or []
                        cur_asset_file = Path(target_shot.asset_path).name if target_shot.asset_path else ""

                        active_base_id = ""
                        m_act = re.search(r"(\d{4,})", f"{target_shot.asset_path or ''} {getattr(target_shot, 'source_candidate_id', '')}")
                        if m_act:
                            active_base_id = m_act.group(1)

                        unique_cands = []
                        seen_ids = set()
                        for orig_idx, cand in enumerate(raw_cands):
                            cid = str(cand.get("candidate_id") or "")
                            curl = str(cand.get("url") or cand.get("source_url") or "")
                            m_base = re.search(r"(\d{4,})", f"{cid} {curl}")
                            base_id = m_base.group(1) if m_base else (cid or curl)

                            if not base_id:
                                continue
                            if active_base_id and base_id == active_base_id:
                                continue
                            if orig_idx == 0 and target_shot.asset_path and not getattr(target_shot, "locked", False):
                                continue
                            if cur_asset_file and base_id in cur_asset_file:
                                continue
                            if base_id in seen_ids:
                                continue

                            seen_ids.add(base_id)
                            unique_cands.append((orig_idx, cand))

                        if not unique_cands:
                            st.caption(t("no_alt_candidates"))
                        else:
                            st.caption(t("found_alt_candidates", count=len(unique_cands)))

                            active_cp = st.session_state.get(f"preview_cand_url_{project_id}")
                            if active_cp:
                                with st.container(border=True):
                                    st.markdown(t("cand_preview_heading"))
                                    with st.spinner(t("spinner_loading_cand_preview")):
                                        st.video(active_cp)
                                    if st.button(t("btn_close_preview"), key=f"btn_close_cp_{project_id}"):
                                        st.session_state[f"preview_cand_url_{project_id}"] = None
                                        st.rerun()

                            for r_i in range(0, len(unique_cands), 2):
                                cand_cols = st.columns(2)
                                for c_col_idx, (orig_idx, cand) in enumerate(unique_cands[r_i:r_i+2]):
                                    with cand_cols[c_col_idx]:
                                        with st.container(border=True):
                                            c_thumb = get_candidate_thumbnail_path(p_dir, cand)
                                            if c_thumb and c_thumb.is_file():
                                                st.image(str(c_thumb), use_container_width=True)
                                            else:
                                                st.caption(t("cand_thumb_fallback"))

                                            score_txt = f"{cand.get('relevance_score', 0):.2f}"
                                            dur_txt = f"{cand.get('duration_seconds', 0):.1f}s"
                                            prov_txt = cand.get('provider', 'stock').capitalize()
                                            st.caption(f"**{prov_txt}** • `{score_txt}` • {dur_txt}")

                                            c_url = cand.get("url") or cand.get("source_url")
                                            btn_col1, btn_col2 = st.columns(2)
                                            with btn_col1:
                                                if c_url and st.button(t("btn_preview"), key=f"btn_prev_cand_{project_id}_{target_shot.shot_id}_{orig_idx}", use_container_width=True):
                                                    st.session_state[f"preview_cand_url_{project_id}"] = c_url
                                                    st.rerun()
                                            with btn_col2:
                                                if st.button(t("btn_use_this"), key=f"btn_apply_cand_{project_id}_{target_shot.shot_id}_{orig_idx}", type="primary", use_container_width=True):
                                                    with st.spinner(t("spinner_applying_cand")):
                                                        try:
                                                            vis_svc = VisualService(p_dir)
                                                            vis_svc.replace_shot(manifest, target_shot.shot_id, candidate_index=orig_idx+1, rebuild=True)
                                                            t_cache = p_dir / "assets" / "visual" / "thumbnails" / f"{target_shot.shot_id}.jpg"
                                                            t_cache.unlink(missing_ok=True)
                                                            save_manifest(manifest, p_dir)
                                                            get_shot_thumbnail_path(p_dir, target_shot)
                                                            st.session_state[f"preview_cand_url_{project_id}"] = None
                                                            st.session_state.pop(f"chk_rev_{project_id}_{target_shot.shot_id}", None)
                                                            st.success(t("cand_applied_success", idx=orig_idx+1))
                                                            time.sleep(0.5)
                                                            st.rerun()
                                                        except Exception as exc:
                                                            _render_friendly_error(t("err_replace_cand_friendly"), exc)

                    with tab_srch:
                        search_prefill = kw_en_val or short_q
                        q_input = st.text_input(
                            t("field_new_search_query"),
                            value=search_prefill,
                            key=f"txt_srch_{project_id}_{target_shot.shot_id}",
                        )
                        p_opts = ["pixabay", "coverr"] if has_coverr else ["pixabay"]
                        sel_p = st.selectbox(t("field_provider"), p_opts, key=f"sel_p_{project_id}_{target_shot.shot_id}")
                        if st.button(t("btn_search_apply_footage"), key=f"btn_srch_apply_{project_id}_{target_shot.shot_id}", use_container_width=True):
                            with st.spinner(t("spinner_searching_footage")):
                                try:
                                    eff_prompt = q_input
                                    proj_lang = resolve_project_language(manifest)
                                    if is_non_english_language(proj_lang) and (not kw_en_val or q_input.strip() != kw_en_val.strip()):
                                        translated_q, tr_warn = translate_single_query_to_english(
                                            q_input,
                                            preset_snapshot=preset_snap,
                                            project_lang=proj_lang,
                                        )
                                        if translated_q:
                                            eff_prompt = translated_q
                                            target_shot.search_query_en = translated_q
                                        if tr_warn:
                                            st.warning(t("f2_translation_warning_prefix", msg=tr_warn))
                                    vis_svc = VisualService(p_dir)
                                    vis_svc.replace_shot(
                                        manifest=manifest,
                                        shot_id=target_shot.shot_id,
                                        prompt=eff_prompt,
                                        provider_name=sel_p,
                                        rebuild=True,
                                    )
                                    t_cache = p_dir / "assets" / "visual" / "thumbnails" / f"{target_shot.shot_id}.jpg"
                                    t_cache.unlink(missing_ok=True)
                                    save_manifest(manifest, p_dir)
                                    get_shot_thumbnail_path(p_dir, target_shot)
                                    st.session_state.pop(f"chk_rev_{project_id}_{target_shot.shot_id}", None)
                                    st.success(t("search_applied_success"))
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as exc:
                                    _render_friendly_error(t("err_search_footage_friendly"), exc)

                    with tab_up:
                        uploaded_f = st.file_uploader(
                            t("field_upload_visual"),
                            type=["mp4", "mov", "mkv", "webm", "jpg", "jpeg", "png", "webp"],
                            key=f"up_f_{project_id}_{target_shot.shot_id}",
                        )
                        if uploaded_f:
                            if st.button(t("btn_upload_apply_footage"), key=f"btn_up_apply_{project_id}_{target_shot.shot_id}", use_container_width=True):
                                with st.spinner(t("spinner_uploading_footage")):
                                    try:
                                        mgr = LocalLibraryManager(p_dir)
                                        asset_info = mgr.add_file(uploaded_f.getvalue(), uploaded_f.name)
                                        vis_svc = VisualService(p_dir)
                                        vis_svc.replace_shot(
                                            manifest=manifest,
                                            shot_id=target_shot.shot_id,
                                            file_path=asset_info["file_path"],
                                            rebuild=True,
                                        )
                                        t_cache = p_dir / "assets" / "visual" / "thumbnails" / f"{target_shot.shot_id}.jpg"
                                        t_cache.unlink(missing_ok=True)
                                        save_manifest(manifest, p_dir)
                                        get_shot_thumbnail_path(p_dir, target_shot)
                                        st.session_state.pop(f"chk_rev_{project_id}_{target_shot.shot_id}", None)
                                        st.success(t("upload_applied_success", name=uploaded_f.name))
                                        time.sleep(0.5)
                                        st.rerun()
                                    except Exception as exc:
                                        _render_friendly_error(t("err_upload_footage_friendly"), exc)

                    with tab_ai_vid:
                        if not ai_vid_providers:
                            st.info(t("ai_video_not_configured"))
                            st.markdown(t("ai_video_supported_list_title"))
                            for sup_p in list_supported_ai_video_providers():
                                st.markdown(f"- **{sup_p['label']}** (`{sup_p['default_model']}`)")
                            if st.button(t("btn_open_video_ai_settings"), key=f"btn_cfg_aiv_{project_id}_{target_shot.shot_id}"):
                                _open_mpt_settings_dialog("video")
                        else:
                            prov_ids = [p["provider_id"] for p in ai_vid_providers]
                            prov_lbls = {p["provider_id"]: p["label"] for p in ai_vid_providers}
                            sel_aiv_prov = st.selectbox(
                                t("field_ai_video_provider"),
                                options=prov_ids,
                                format_func=lambda x: prov_lbls.get(x, x),
                                key=f"sel_aiv_prov_{project_id}_{target_shot.shot_id}",
                            )
                            aiv_meta = get_ai_video_provider_meta(sel_aiv_prov)
                            sel_aiv_model = st.selectbox(
                                t("field_ai_video_model"),
                                options=aiv_meta["models"],
                                key=f"sel_aiv_model_{project_id}_{target_shot.shot_id}_{sel_aiv_prov}",
                            )
                            sup_durs = get_model_supported_durations(sel_aiv_prov, sel_aiv_model)
                            def_dur = round_up_supported_duration(target_shot.duration_seconds, sup_durs)
                            col_vr1, col_vr2 = st.columns(2)
                            with col_vr1:
                                sel_aiv_res = st.selectbox(
                                    t("field_ai_video_resolution"),
                                    options=aiv_meta["resolutions"],
                                    index=min(1, len(aiv_meta["resolutions"]) - 1),
                                    key=f"sel_aiv_res_{project_id}_{target_shot.shot_id}_{sel_aiv_prov}",
                                )
                            with col_vr2:
                                sel_aiv_dur = st.selectbox(
                                    t("field_ai_video_duration"),
                                    options=sup_durs,
                                    index=sup_durs.index(def_dur) if def_dur in sup_durs else 0,
                                    key=f"sel_aiv_dur_{project_id}_{target_shot.shot_id}_{sel_aiv_prov}",
                                )
                            st.caption(
                                t(
                                    "ai_video_duration_trim_note",
                                    shot_dur=f"{target_shot.duration_seconds:.1f}",
                                    gen_dur=sel_aiv_dur,
                                )
                            )
                            default_aiv_prompt = build_ai_video_prompt(
                                narration=target_narr,
                                keywords=kw_en_val or short_q,
                                preset_snapshot=preset_snap,
                            )
                            aiv_prompt = st.text_area(
                                t("field_ai_video_prompt"),
                                value=default_aiv_prompt,
                                height=90,
                                key=f"txt_aiv_prompt_{project_id}_{target_shot.shot_id}",
                            )
                            cost_est = estimate_ai_video_cost(sel_aiv_model, sel_aiv_dur, sel_aiv_res)
                            if cost_est.get("known") and cost_est.get("estimated_usd") is not None:
                                st.info(
                                    t(
                                        "ai_video_cost_known",
                                        usd=f"{cost_est['estimated_usd']:.2f}",
                                        date=cost_est["updated_at"],
                                    )
                                )
                                if cost_est.get("url"):
                                    st.caption(
                                        t(
                                            "ai_video_cost_source",
                                            url=cost_est["url"],
                                            date=cost_est.get("updated_at", ""),
                                        )
                                    )
                            else:
                                st.info(t("ai_video_cost_unknown"))

                            if st.button(
                                t("btn_generate_ai_video_shot"),
                                type="primary",
                                disabled=is_job_running or not aiv_prompt.strip(),
                                key=f"btn_gen_aiv_{project_id}_{target_shot.shot_id}",
                                use_container_width=True,
                            ):
                                st.session_state[f"ai_video_confirm_batch_{project_id}"] = False
                                st.session_state[f"ai_video_confirm_single_{project_id}"] = {
                                    "shot_id": target_shot.shot_id,
                                    "shot_duration": target_shot.duration_seconds,
                                    "provider_id": sel_aiv_prov,
                                    "provider_label": prov_lbls.get(sel_aiv_prov, sel_aiv_prov),
                                    "model_id": sel_aiv_model,
                                    "resolution": sel_aiv_res,
                                    "duration_seconds": int(sel_aiv_dur),
                                    "prompt": aiv_prompt.strip(),
                                    "cost_known": bool(cost_est.get("known")),
                                    "estimated_usd": cost_est.get("estimated_usd"),
                                    "updated_at": cost_est.get("updated_at"),
                                    "url": cost_est.get("url", ""),
                                }
                                st.rerun()

    # --- F3: Mandatory Cost Confirmation Dialogs (Single Shot & Bulk Needs-Review) ---
    if not st.session_state.get("settings_dialog_open", False):
        single_req = st.session_state.get(f"ai_video_confirm_single_{project_id}")
        batch_open = bool(st.session_state.get(f"ai_video_confirm_batch_{project_id}", False))

        if isinstance(single_req, dict):
            @st.dialog(t("dlg_confirm_ai_video_title"))
            def _confirm_single_ai_video_dialog():
                st.warning(t("dlg_confirm_ai_video_warn"))
                st.markdown(
                    t(
                        "dlg_confirm_ai_video_detail",
                        shot=single_req["shot_id"],
                        provider=single_req["provider_label"],
                        model=single_req["model_id"],
                        res=single_req["resolution"],
                        dur=single_req["duration_seconds"],
                        shot_dur=f"{single_req['shot_duration']:.1f}",
                    )
                )
                st.code(single_req["prompt"], language="text")
                if single_req.get("cost_known") and single_req.get("estimated_usd") is not None:
                    st.info(
                        t(
                            "ai_video_cost_known",
                            usd=f"{single_req['estimated_usd']:.2f}",
                            date=single_req.get("updated_at", ""),
                        )
                    )
                    if single_req.get("url"):
                        st.caption(
                            t(
                                "ai_video_cost_source",
                                url=single_req["url"],
                                date=single_req.get("updated_at", ""),
                            )
                        )
                else:
                    st.info(t("ai_video_cost_unknown"))
                col_c1, col_c2 = st.columns(2)
                with col_c1:
                    if st.button(
                        t("btn_confirm_generate"),
                        type="primary",
                        key=f"btn_dlg_conf_aiv_{project_id}",
                        use_container_width=True,
                    ):
                        req_copy = dict(single_req)
                        st.session_state[f"ai_video_confirm_single_{project_id}"] = None
                        cmd = jobs.build_longform_cli_command(
                            "visual",
                            "ai-video",
                            project_id,
                            "--shot",
                            req_copy["shot_id"],
                            "--provider",
                            req_copy["provider_id"],
                            "--model",
                            req_copy["model_id"],
                            "--resolution",
                            req_copy["resolution"],
                            "--duration",
                            str(req_copy["duration_seconds"]),
                            "--prompt",
                            req_copy["prompt"],
                        )
                        _start_job_safe(project_id, "visual_ai_video", cmd)
                with col_c2:
                    if st.button(
                        t("btn_cancel_dialog"),
                        key=f"btn_dlg_canc_aiv_{project_id}",
                        use_container_width=True,
                    ):
                        st.session_state[f"ai_video_confirm_single_{project_id}"] = None
                        st.rerun()

            _confirm_single_ai_video_dialog()

        elif batch_open and ai_vid_providers:
            @st.dialog(t("dlg_confirm_ai_video_batch_title"))
            def _confirm_batch_ai_video_dialog():
                st.warning(t("dlg_confirm_ai_video_warn"))
                b_prov_ids = [p["provider_id"] for p in ai_vid_providers]
                b_prov_lbls = {p["provider_id"]: p["label"] for p in ai_vid_providers}
                sel_bp = st.selectbox(
                    t("field_ai_video_provider"),
                    options=b_prov_ids,
                    format_func=lambda x: b_prov_lbls.get(x, x),
                    key=f"sel_batch_aiv_prov_{project_id}",
                )
                b_meta = get_ai_video_provider_meta(sel_bp)
                sel_bm = st.selectbox(
                    t("field_ai_video_model"),
                    options=b_meta["models"],
                    key=f"sel_batch_aiv_model_{project_id}_{sel_bp}",
                )
                sel_br = st.selectbox(
                    t("field_ai_video_resolution"),
                    options=b_meta["resolutions"],
                    index=min(1, len(b_meta["resolutions"]) - 1),
                    key=f"sel_batch_aiv_res_{project_id}_{sel_bp}",
                )
                b_plan = plan_needs_review_ai_video_batch(manifest, sel_bp, sel_bm, sel_br)
                st.markdown(t("dlg_confirm_batch_summary", count=b_plan["count"]))
                budget_cap = st.number_input(
                    t("field_ai_video_budget_cap"),
                    min_value=0.10,
                    max_value=500.0,
                    value=5.0,
                    step=0.50,
                    key=f"num_batch_aiv_budget_{project_id}",
                )
                tot_est = b_plan.get("total_estimated_usd")
                exceeds = False
                if tot_est is not None:
                    ref_cost = estimate_ai_video_cost(sel_bm, 4, sel_br)
                    st.info(
                        t(
                            "ai_video_cost_known",
                            usd=f"{tot_est:.2f}",
                            date=ref_cost.get("updated_at", ""),
                        )
                    )
                    if ref_cost.get("url"):
                        st.caption(
                            t(
                                "ai_video_cost_source",
                                url=ref_cost["url"],
                                date=ref_cost.get("updated_at", ""),
                            )
                        )
                    if tot_est > float(budget_cap):
                        exceeds = True
                        st.error(
                            t(
                                "dlg_budget_exceeded_warn",
                                est=f"{tot_est:.2f}",
                                cap=f"{float(budget_cap):.2f}",
                            )
                        )
                else:
                    st.info(t("ai_video_cost_unknown"))

                col_bc1, col_bc2 = st.columns(2)
                with col_bc1:
                    if st.button(
                        t("btn_confirm_generate"),
                        type="primary",
                        disabled=exceeds or b_plan["count"] == 0,
                        key=f"btn_dlg_conf_batch_aiv_{project_id}",
                        use_container_width=True,
                    ):
                        st.session_state[f"ai_video_confirm_batch_{project_id}"] = False
                        cmd = jobs.build_longform_cli_command(
                            "visual",
                            "ai-video-batch",
                            project_id,
                            "--provider",
                            sel_bp,
                            "--model",
                            sel_bm,
                            "--resolution",
                            sel_br,
                            "--max-budget",
                            f"{float(budget_cap):.2f}",
                        )
                        _start_job_safe(project_id, "visual_ai_video_batch", cmd)
                with col_bc2:
                    if st.button(
                        t("btn_cancel_dialog"),
                        key=f"btn_dlg_canc_batch_aiv_{project_id}",
                        use_container_width=True,
                    ):
                        st.session_state[f"ai_video_confirm_batch_{project_id}"] = False
                        st.rerun()

            _confirm_batch_ai_video_dialog()

    # --- V4: Cache & Pemeliharaan ---
    with st.expander(t("expander_cache_maintenance"), expanded=False):
        c_stat = get_cache_status()
        col_c1, col_c2, col_c3 = st.columns(3)
        with col_c1:
            render_metric_tile(t("metric_cache_size"), f"{c_stat['total_mb']} MB ({c_stat['total_gb']} GB)")
        with col_c2:
            render_metric_tile(t("metric_cache_files"), t("cache_files_unit", count=c_stat["total_files"]))
        with col_c3:
            render_metric_tile(t("metric_cache_protected"), t("cache_files_unit", count=c_stat["locked_files_count"]))

        st.caption(t("cache_desc"))

        confirm_prune = st.checkbox(t("chk_confirm_prune"), key=f"confirm_prune_{project_id}")
        if st.button(t("btn_prune_cache"), disabled=not confirm_prune, key=f"btn_prune_cache_{project_id}"):
            with st.spinner(t("spinner_pruning_cache")):
                try:
                    prune_res = prune_cache(older_than_days=14)
                    st.success(t("prune_success", removed=prune_res["files_removed"], mb=prune_res["mb_freed"], skipped=prune_res["locked_files_skipped"]))
                    time.sleep(1)
                    st.rerun()
                except Exception as exc:
                    _render_friendly_error(t("err_prune_cache_friendly"), exc)

        st.divider()
        trash_cache_items = list_trashed_projects()
        trash_cache_mb = round(sum(float(x.get("size_mb", 0.0)) for x in trash_cache_items), 2)
        st.caption(t("cache_trash_summary", count=len(trash_cache_items), mb=f"{trash_cache_mb:.2f}"))
        if st.button(t("btn_manage_trash", count=len(trash_cache_items)), key=f"btn_cache_open_trash_{project_id}"):
            st.query_params.clear()
            st.session_state["home_open_trash_requested"] = True
            st.rerun()


def _safe_open_path(path: Path | str, is_folder: bool = False):
    """Safely open a file or directory using the OS default shell handler."""
    p = Path(path).resolve()
    if sys.platform == "win32":
        try:
            if is_folder and p.is_file():
                subprocess.Popen(["explorer.exe", f"/select,{str(p)}"])
            else:
                os.startfile(str(p))
        except Exception as exc:
            logger.warning(f"Could not open path {p}: {exc}")
    else:
        try:
            subprocess.Popen(["xdg-open", str(p)])
        except Exception:
            pass


# --- Step 4: Render Flow (Stage 7B.2 R1-R5) ---

def render_render_step_flow(
    project_id: str,
    p_dir: Path,
    manifest: ProjectManifest,
    step_info: Dict[str, Any],
    is_job_running: bool = False,
    is_completed_view: bool = False,
):
    """Render Step 4: Render Video Final + Subtitle + BGM (Stage 7B.2 R1-R5, Mockup 3 V4)."""
    from app.services.longform.render.service import RenderService

    job_state = jobs.get_job_state(project_id)
    is_job_running = is_job_running or bool(job_state and job_state.get("status") == "running")

    st_info = compute_project_step_status(p_dir)
    voice_done = (st_info["steps"]["voice"]["status"] == "selesai")
    visual_done = (st_info["steps"]["visual"]["status"] == "selesai")
    can_render = voice_done and visual_done
    st_val = step_info["status"]

    final_vid = step_info.get("final_video_file") or manifest.final_video_file
    final_vid_path = (p_dir / final_vid) if final_vid else None

    # Load render_stats.json if present
    stats_file = p_dir / "export" / "render_stats.json"
    stats: Dict[str, Any] = {}
    if stats_file.is_file():
        try:
            stats = json.loads(stats_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    vid_size_mb = 0.0
    dur_sec = 0.0
    if final_vid_path and final_vid_path.is_file():
        vid_size_bytes = final_vid_path.stat().st_size
        vid_size_mb = vid_size_bytes / (1024 * 1024)
        dur_sec = float(stats.get("final_video_duration") or 0.0)

        # Fallback ffprobe if duration not in stats
        if dur_sec <= 0:
            try:
                pres = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final_vid_path)],
                    capture_output=True, text=True, check=True
                )
                dur_sec = float(pres.stdout.strip())
            except Exception:
                dur_sec = 0.0

    dur_fmt = f"{int(dur_sec // 60):02d}:{int(dur_sec % 60):02d} ({dur_sec:.1f}s)" if dur_sec > 0 else "N/A"
    lufs_val = stats.get("integrated_lufs")
    tp_val = stats.get("true_peak_dbtp")
    lufs_fmt = f"{lufs_val:.1f} LUFS" if lufs_val is not None else "-14.0 LUFS"
    tp_fmt = f"{tp_val:.1f} dBTP" if tp_val is not None else "-1.0 dBTP"
    fps = float(getattr(manifest.encoder_settings, "fps", 25.0) or 25.0)
    total_frames = int(dur_sec * fps) if dur_sec > 0 else 0

    yt_path = p_dir / "export" / "youtube_chapters.txt"
    yt_text = ""
    if yt_path.is_file():
        yt_text = yt_path.read_text(encoding="utf-8", errors="replace").strip()
    elif manifest.chapters:
        lines = []
        curr_sec = 0.0
        for ch in manifest.chapters:
            mm = int(curr_sec // 60)
            ss = int(curr_sec % 60)
            lines.append(f"{mm:02d}:{ss:02d} {ch.chapter_title}")
            curr_sec += float(ch.audio_duration_seconds or 0.0)
        yt_text = "\n".join(lines)

    preset_snap = dict(manifest.preset_snapshot or {})
    sub_style = dict(preset_snap.get("subtitle_style", {}))

    # Helper function to render BGM & Subtitle settings
    def _render_settings_body():
        st.markdown(t("bgm_heading"))
        songs_dir = Path("resource/songs")
        available_bgm = []
        if songs_dir.is_dir():
            available_bgm = sorted([f.name for f in songs_dir.glob("*.mp3")])

        local_bgm_dir = p_dir / "assets" / "audio" / "bgm"
        local_bgm_files = []
        if local_bgm_dir.is_dir():
            local_bgm_files = [f.name for f in local_bgm_dir.iterdir() if f.is_file() and f.suffix.lower() in (".mp3", ".wav", ".m4a")]

        curr_bgm_choice = "preset"
        if preset_snap.get("default_bgm") is None and not local_bgm_files:
            curr_bgm_choice = "none"
        elif local_bgm_files:
            curr_bgm_choice = "upload"

        bgm_mode = st.radio(
            t("bgm_source_label"),
            options=["preset", "none", "upload"],
            format_func=lambda x: {
                "preset": t("bgm_opt_preset"),
                "none": t("bgm_opt_none"),
                "upload": t("bgm_opt_upload"),
            }[x],
            index=["preset", "none", "upload"].index(curr_bgm_choice),
            key=f"bgm_mode_{project_id}",
        )

        selected_bgm_name = preset_snap.get("default_bgm") or (available_bgm[0] if available_bgm else None)
        if bgm_mode == "preset":
            if available_bgm:
                selected_bgm_name = st.selectbox(
                    t("bgm_select_song"),
                    options=available_bgm,
                    index=available_bgm.index(selected_bgm_name) if selected_bgm_name in available_bgm else 0,
                    key=f"sel_bgm_{project_id}",
                )
            else:
                st.caption(t("bgm_no_files_caption"))
        elif bgm_mode == "upload":
            up_bgm = st.file_uploader(t("bgm_upload_label"), type=["mp3", "wav", "m4a"], key=f"up_bgm_{project_id}")
            if up_bgm:
                local_bgm_dir.mkdir(parents=True, exist_ok=True)
                target_bgm = local_bgm_dir / up_bgm.name
                target_bgm.write_bytes(up_bgm.getvalue())
                st.success(t("bgm_upload_success", name=up_bgm.name))
                selected_bgm_name = up_bgm.name
            elif local_bgm_files:
                st.info(t("bgm_active_file", name=local_bgm_files[0]))
        else:
            selected_bgm_name = None

        col_v1, col_v2 = st.columns(2)
        with col_v1:
            bgm_volume = st.slider(
                t("bgm_volume_label"),
                min_value=0.0,
                max_value=1.0,
                value=float(preset_snap.get("bgm_volume", 0.20)),
                step=0.05,
                key=f"sl_bgm_vol_{project_id}",
            )
        with col_v2:
            st.caption(t("bgm_ducking_title"))
            st.caption(t("bgm_ducking_desc"))

        st.markdown("---")
        st.markdown(t("subtitle_style_heading"))
        col_st1, col_st2 = st.columns(2)
        with col_st1:
            curr_fsize = int(sub_style.get("font_size", 20))
            new_fsize = st.number_input(
                t("subtitle_font_size_label"),
                min_value=10,
                max_value=120,
                value=min(max(10, curr_fsize), 120),
                step=1,
                key=f"sub_fs_{project_id}",
            )
            st.caption(t("subtitle_font_meta", font=sub_style.get("font_name", "BeVietnamPro-Bold"), color=sub_style.get("primary_color", "&H00FFFFFF")))
        with col_st2:
            curr_marginv = int(sub_style.get("margin_v", 40))
            new_marginv = st.number_input(
                t("subtitle_margin_v_label"),
                min_value=10,
                max_value=300,
                value=min(max(10, curr_marginv), 300),
                step=5,
                key=f"sub_mv_{project_id}",
            )
            st.caption(t("subtitle_outline_meta", color=sub_style.get("outline_color", "&H00000000"), width=sub_style.get("outline_width", 2)))

        col_save_cfg, col_prev_sub = st.columns([1, 1])
        with col_save_cfg:
            if st.button(t("btn_save_render_settings"), disabled=is_job_running, key=f"btn_save_render_cfg_{project_id}"):
                sub_style["font_size"] = int(new_fsize)
                sub_style["margin_v"] = int(new_marginv)
                preset_snap["subtitle_style"] = sub_style
                preset_snap["bgm_volume"] = bgm_volume
                preset_snap["default_bgm"] = selected_bgm_name
                manifest.preset_snapshot = preset_snap
                save_manifest(manifest, p_dir)
                st.success(t("render_settings_saved"))
                time.sleep(0.5)
                st.rerun()

        with col_prev_sub:
            if st.button(t("btn_preview_subtitle"), disabled=(not visual_done or is_job_running), key=f"btn_prev_sub_{project_id}"):
                with st.spinner(t("spinner_preview_subtitle")):
                    try:
                        rnd_svc = RenderService(p_dir)
                        prev_pngs = rnd_svc.preview_subtitles(
                            manifest=manifest,
                            chapter_index=1,
                            timestamps_seconds=[5.0, 10.0],
                            font_size=new_fsize,
                            margin_v=new_marginv,
                        )
                        if prev_pngs and prev_pngs[0].is_file():
                            st.session_state[f"sub_prev_img_{project_id}"] = str(prev_pngs[0])
                            st.rerun()
                        else:
                            st.warning(t("subtitle_no_frame_warn"))
                    except Exception as exc:
                        _render_friendly_error(t("err_subtitle_preview_friendly"), exc)

        sub_prev_path = st.session_state.get(f"sub_prev_img_{project_id}")
        if sub_prev_path and Path(sub_prev_path).is_file():
            st.markdown(t("subtitle_preview_heading"))
            st.image(sub_prev_path, use_container_width=True)
            if st.button(t("btn_close_sub_preview"), key=f"btn_close_sub_prev_{project_id}"):
                st.session_state.pop(f"sub_prev_img_{project_id}", None)
                st.rerun()

        st.markdown("---")
        st.markdown(t("force_rerender_heading"))
        confirm_rerender_all = st.checkbox(
            t("chk_confirm_rerender"),
            key=f"chk_confirm_rerender_{project_id}",
        )
        if st.button(
            t("btn_rerender_all_chapters"),
            disabled=(not confirm_rerender_all or is_job_running),
            key=f"btn_rerender_all_exp_{project_id}",
        ):
            _start_job_safe(
                project_id,
                "render_all",
                jobs.build_longform_cli_command("render", "all", project_id, "--force"),
                sleep_after=0.5,
            )

    # --- Render Mockup 3 Completed Side-by-Side View (V4) ---
    if is_completed_view:
        st.markdown(f"### {t('final_video_heading')}")

        if final_vid_path and final_vid_path.is_file():
            if vid_size_mb < 200.0:
                st.video(str(final_vid_path.resolve()))
            else:
                st.info(t("final_video_too_large_info", size=f"{vid_size_mb:.1f}"))
        else:
            st.info(t("final_video_not_ready"))

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            render_metric_tile(t("metric_video_duration"), dur_fmt)
        with col_m2:
            render_metric_tile(t("metric_file_size"), f"{vid_size_mb:.1f} MB")

        col_m3, col_m4 = st.columns(2)
        with col_m3:
            render_metric_tile(t("metric_loudness_full"), f"{lufs_fmt} • {tp_fmt}")
        with col_m4:
            render_metric_tile(t("metric_frame_count"), f"{total_frames} frames ({fps:.0f} fps)")

        col_a1, col_a2 = st.columns(2)
        with col_a1:
            if st.button(t("btn_open_system_player"), key=f"btn_play_sys_{project_id}", use_container_width=True):
                if final_vid_path:
                    _safe_open_path(final_vid_path)
        with col_a2:
            if st.button(t("btn_open_export_folder"), key=f"btn_open_exp_{project_id}", use_container_width=True):
                if final_vid_path:
                    _safe_open_path(final_vid_path.parent, is_folder=True)

        col_a3, col_a4 = st.columns(2)
        with col_a3:
            srt_path = p_dir / "export" / "subtitles" / "full_timeline.srt"
            if srt_path.is_file():
                srt_data = srt_path.read_text(encoding="utf-8", errors="replace")
                st.download_button(
                    t("btn_download_sub_srt"),
                    data=srt_data,
                    file_name=f"{project_id}_subtitles.srt",
                    mime="text/plain",
                    use_container_width=True,
                    key=f"dl_srt_{project_id}",
                )
            else:
                st.button(t("btn_sub_not_ready"), disabled=True, use_container_width=True, key=f"dl_srt_dis_{project_id}")
        with col_a4:
            if yt_path.is_file():
                yt_data = yt_path.read_text(encoding="utf-8", errors="replace")
                st.download_button(
                    t("btn_download_yt_chapters"),
                    data=yt_data,
                    file_name=f"{project_id}_youtube_chapters.txt",
                    mime="text/plain",
                    use_container_width=True,
                    key=f"dl_yt_{project_id}",
                )
            else:
                st.button(t("btn_yt_not_ready"), disabled=True, use_container_width=True, key=f"dl_yt_dis_{project_id}")

        if yt_text:
            st.markdown(f"**{t('youtube_chapters_title')}**")
            st.code(yt_text, language="text")
            if st.button(t("btn_copy_timestamps"), key=f"btn_copy_yt_{project_id}", use_container_width=True):
                st.toast(t("btn_copied_timestamps"))

        with st.expander(t("expander_render_again"), expanded=False):
            _render_settings_body()

    # --- Render Normal / In-Progress Step View ---
    else:
        col_hdr, col_act = st.columns([3, 1])
        with col_hdr:
            st.markdown(f"### {t('render_step_title')}")
            st.caption(_get_step_subtitle("render", st_val, is_job_running))
        with col_act:
            st.write("")
            if st_val == "belum":
                btn_label = t("btn_render_final")
            elif st_val == "terhenti":
                btn_label = t("btn_resume_render")
            elif st_val == "perlu_diperbarui":
                btn_label = t("btn_rerender_changed")
            else:
                btn_label = t("btn_render_done_next")

            if st.button(
                btn_label,
                disabled=(not can_render or is_job_running),
                type="primary",
                key=f"btn_render_main_{project_id}",
                use_container_width=True,
            ):
                if st_val == "selesai":
                    st.session_state[f"active_step_{project_id}"] = "capcut"
                    st.rerun()
                else:
                    _start_job_safe(
                        project_id,
                        "render_all",
                        jobs.build_longform_cli_command("render", "all", project_id),
                        sleep_after=0.5,
                    )

        if not can_render:
            if not voice_done and not visual_done:
                st.warning(t("render_prereq_both"))
            elif not voice_done:
                st.warning(t("render_prereq_voice"))
            elif not visual_done:
                st.warning(t("render_prereq_visual", comp=st_info["steps"]["visual"]["completed"], tot=st_info["steps"]["visual"]["total"]))

        with st.expander(t("expander_render_settings"), expanded=False):
            _render_settings_body()

        if final_vid_path and final_vid_path.is_file():
            st.write("---")
            st.markdown(t("render_result_heading"))

            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            with col_m1:
                render_metric_tile(t("metric_video_duration"), dur_fmt)
            with col_m2:
                render_metric_tile(t("metric_file_size"), f"{vid_size_mb:.1f} MB")
            with col_m3:
                render_metric_tile(t("metric_loudness_full"), f"{lufs_fmt} • {tp_fmt}")
            with col_m4:
                render_metric_tile(t("metric_frame_count"), f"{total_frames} frames ({fps:.0f} fps)")

            col_a1, col_a2, col_a3, col_a4 = st.columns(4)
            with col_a1:
                if st.button(t("btn_open_system_player"), key=f"btn_play_sys_{project_id}", use_container_width=True):
                    _safe_open_path(final_vid_path)
            with col_a2:
                if st.button(t("btn_open_export_folder"), key=f"btn_open_exp_{project_id}", use_container_width=True):
                    _safe_open_path(final_vid_path.parent, is_folder=True)
            with col_a3:
                srt_path = p_dir / "export" / "subtitles" / "full_timeline.srt"
                if srt_path.is_file():
                    srt_data = srt_path.read_text(encoding="utf-8", errors="replace")
                    st.download_button(
                        t("btn_download_sub_srt"),
                        data=srt_data,
                        file_name=f"{project_id}_subtitles.srt",
                        mime="text/plain",
                        use_container_width=True,
                        key=f"dl_srt_{project_id}",
                    )
                else:
                    st.button(t("btn_sub_not_ready"), disabled=True, use_container_width=True, key=f"dl_srt_dis_{project_id}")
            with col_a4:
                if yt_path.is_file():
                    yt_data = yt_path.read_text(encoding="utf-8", errors="replace")
                    st.download_button(
                        t("btn_download_yt_chapters"),
                        data=yt_data,
                        file_name=f"{project_id}_youtube_chapters.txt",
                        mime="text/plain",
                        use_container_width=True,
                        key=f"dl_yt_{project_id}",
                    )
                else:
                    st.button(t("btn_yt_not_ready"), disabled=True, use_container_width=True, key=f"dl_yt_dis_{project_id}")

            if vid_size_mb < 200.0:
                st.video(str(final_vid_path.resolve()))
            else:
                st.info(t("final_video_too_large_info", size=f"{vid_size_mb:.1f}"))

            if yt_text:
                st.markdown(f"**{t('youtube_chapters_title')}**")
                st.code(yt_text, language="text")


# --- Step 5: CapCut Desktop Export Flow (Stage 7B.2 C1-C4, Mockup 3 V4) ---

def render_capcut_step_flow(
    project_id: str,
    p_dir: Path,
    manifest: ProjectManifest,
    step_info: Dict[str, Any],
    is_job_running: bool = False,
    is_completed_view: bool = False,
):
    """Render Step 5: CapCut Desktop Export & Calibration (Stage 7B.2 C1-C4, Mockup 3 V4)."""
    from app.services.longform.capcut.detector import get_capcut_doctor_report
    from app.services.longform.capcut.service import (
        build_calibration_draft,
        resolve_capcut_text_size,
        set_capcut_text_size,
    )

    job_state = jobs.get_job_state(project_id)
    is_job_running = is_job_running or bool(job_state and job_state.get("status") == "running")

    st_info = compute_project_step_status(p_dir)
    voice_done = (st_info["steps"]["voice"]["status"] == "selesai")
    visual_done = (st_info["steps"]["visual"]["status"] == "selesai")

    # C1: CapCut Doctor Diagnostic Report
    doc_report = get_capcut_doctor_report()
    capcut_running = doc_report.get("is_running", False)
    capcut_installed = doc_report.get("installed", False)
    installed_ver = doc_report.get("installed_version")
    tmpl_ver = doc_report.get("template_version", "8.9.0")
    draft_folder = doc_report.get("draft_folder")

    draft_info = step_info.get("draft_info") or find_latest_capcut_draft(p_dir, project_id)
    is_exported = bool(draft_info)
    total_shots = sum(len(ch.shots) for ch in manifest.chapters)
    can_export = voice_done and visual_done and not capcut_running

    # Export History List
    exp_root = p_dir / "export" / "capcut"
    prev_exports = []
    if exp_root.is_dir():
        for d in sorted(exp_root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if d.is_dir() and (d.name.startswith("EFS_") or d.name.startswith("MPT_")):
                mtime_str = datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                prev_exports.append({
                    t("capcut_tbl_col_draft"): d.name,
                    t("capcut_tbl_col_created"): mtime_str,
                    t("capcut_tbl_col_path"): str(d),
                })

    current_sz = resolve_capcut_text_size(manifest)

    # Helper function to render text size calibration body
    def _render_text_size_body():
        st.write(t("capcut_text_size_info", size=current_sz, preset=manifest.preset_id))
        st.caption(t("capcut_calib_desc"))

        col_cal1, col_cal2 = st.columns([1, 1])
        with col_cal1:
            if st.button(t("btn_create_calib_draft"), disabled=(capcut_running or not visual_done or is_job_running), key=f"btn_calib_{project_id}"):
                with st.spinner(t("spinner_creating_calib")):
                    try:
                        calib_res = build_calibration_draft(project_id, install=True)
                        st.success(t("calib_created_success", name=calib_res["draft_name"]))
                    except Exception as exc:
                        _render_friendly_error(t("err_capcut_calib_friendly"), exc)

        with col_cal2:
            new_sz_val = st.number_input(
                t("field_choose_text_size"),
                min_value=5,
                max_value=10,
                value=int(current_sz),
                step=1,
                key=f"num_sz_{project_id}",
            )
            if st.button(t("btn_save_text_size"), disabled=is_job_running, key=f"btn_save_sz_{project_id}"):
                try:
                    set_capcut_text_size(manifest.preset_id, new_sz_val)
                    manifest.preset_snapshot["capcut_text_size"] = new_sz_val
                    save_manifest(manifest, p_dir)
                    st.success(t("text_size_saved_success", size=new_sz_val, preset=manifest.preset_id))
                    time.sleep(0.5)
                    st.rerun()
                except Exception as exc:
                    _render_friendly_error(t("err_save_text_size_friendly"), exc)

    # --- Render Mockup 3 Completed Side-by-Side View (V4) ---
    if is_completed_view:
        col_cch, col_cc_btn = st.columns([3, 1])
        with col_cch:
            st.markdown(f"### {t('capcut_heading')}")
            st.caption(_get_step_subtitle("capcut", step_info["status"], is_job_running))
        with col_cc_btn:
            st.write("")
            if st.button(t("btn_recheck"), key=f"btn_recheck_capcut_{project_id}", use_container_width=True):
                st.rerun()

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            status_capcut_label = t("capcut_status_installed_ver", ver=installed_ver or "N/A") if capcut_installed else t("capcut_status_not_installed")
            render_metric_tile(t("capcut_metric_status"), status_capcut_label, status="success" if capcut_installed else "warning")
        with col_d2:
            status_app_label = t("capcut_app_running") if capcut_running else t("capcut_app_closed_safe")
            render_metric_tile(t("capcut_metric_app_label"), status_app_label, status="warning" if capcut_running else "success")

        if draft_folder:
            st.caption(t("capcut_draft_folder_caption", folder=draft_folder))
        if capcut_running:
            st.error(t("capcut_err_running_active"))

        if draft_info:
            draft_name = draft_info["draft_name"]
            draft_dir_path = Path(draft_info["draft_dir"])
            with st.container(border=True):
                st.markdown(t("capcut_latest_draft_label", name=draft_name))
                col_dr1, col_dr2 = st.columns(2)
                with col_dr1:
                    st.caption(t("capcut_export_time_label", time=draft_info.get("exported_at") or t("capcut_just_now")))
                with col_dr2:
                    st.caption(t("capcut_total_clips_label", count=total_shots))

        btn_label = t("btn_reexport_capcut") if is_exported else t("btn_export_capcut_install")
        if st.button(
            btn_label,
            disabled=(not can_export or is_job_running),
            type="primary",
            key=f"btn_capcut_main_{project_id}",
            use_container_width=True,
        ):
            _start_job_safe(
                project_id,
                "capcut_export",
                jobs.build_longform_cli_command("capcut", "export", project_id, "--install"),
                sleep_after=0.5,
            )

        if draft_info:
            if st.button(t("btn_open_draft_folder"), key=f"btn_open_draft_{project_id}", use_container_width=True):
                _safe_open_path(draft_dir_path, is_folder=True)

        with st.expander(t("capcut_expander_subtitle_size"), expanded=False):
            _render_text_size_body()

        if prev_exports:
            with st.expander(t("capcut_expander_history", count=len(prev_exports)), expanded=False):
                st.table(prev_exports)

    # --- Render Normal / In-Progress Step View ---
    else:
        col_hdr, col_act = st.columns([3, 1])
        with col_hdr:
            st.markdown(f"### {t('step_capcut')}")
            st.caption(_get_step_subtitle("capcut", step_info["status"], is_job_running))
        with col_act:
            st.write("")
            btn_label = t("btn_reexport_capcut") if is_exported else t("btn_export_capcut_install")
            if st.button(
                btn_label,
                disabled=(not can_export or is_job_running),
                type="primary",
                key=f"btn_capcut_main_{project_id}",
                use_container_width=True,
            ):
                _start_job_safe(
                    project_id,
                    "capcut_export",
                    jobs.build_longform_cli_command("capcut", "export", project_id, "--install"),
                    sleep_after=0.5,
                )

        with st.container(border=True):
            col_cch, col_cc_btn = st.columns([3, 1])
            with col_cch:
                st.markdown(t("capcut_check_heading"))
            with col_cc_btn:
                if st.button(t("btn_recheck_capcut"), key=f"btn_recheck_capcut_{project_id}"):
                    st.rerun()

            col_d1, col_d2, col_d3 = st.columns(3)
            with col_d1:
                render_metric_tile(t("capcut_metric_status"), t("capcut_status_installed") if capcut_installed else t("capcut_status_not_installed"), status="success" if capcut_installed else "warning")
            with col_d2:
                render_metric_tile(t("capcut_metric_version_label"), str(installed_ver or "N/A"))
            with col_d3:
                render_metric_tile(t("capcut_metric_app_label"), t("capcut_app_running") if capcut_running else t("capcut_app_closed_safe"), status="warning" if capcut_running else "success")

            if draft_folder:
                st.caption(t("capcut_draft_folder_caption", folder=draft_folder))
            else:
                st.caption(t("capcut_draft_folder_missing"))

            if capcut_installed and installed_ver != tmpl_ver:
                st.warning(t("capcut_version_mismatch_warn", inst=installed_ver, tmpl=tmpl_ver))

            if capcut_running:
                st.error(t("capcut_err_running_active"))

        with st.expander(t("capcut_expander_calib_full"), expanded=False):
            _render_text_size_body()

        if not can_export:
            if not voice_done or not visual_done:
                st.warning(t("capcut_prereq_warn"))
            elif capcut_running:
                st.warning(t("capcut_running_warn"))

        if draft_info:
            st.write("---")
            st.markdown(t("capcut_installed_draft_heading"))

            draft_name = draft_info["draft_name"]
            draft_dir_path = Path(draft_info["draft_dir"])

            col_i1, col_i2, col_i3 = st.columns(3)
            with col_i1:
                render_metric_tile(t("capcut_metric_draft_name"), draft_name)
            with col_i2:
                render_metric_tile(t("capcut_metric_export_time"), draft_info.get("exported_at") or t("capcut_just_now"))
            with col_i3:
                render_metric_tile(t("capcut_metric_total_clips"), t("capcut_total_clips_value", count=total_shots))

            st.caption(t("capcut_draft_location_caption", path=draft_dir_path))

            col_b1, _ = st.columns([1, 2])
            with col_b1:
                if st.button(t("btn_open_draft_folder"), key=f"btn_open_draft_{project_id}"):
                    _safe_open_path(draft_dir_path, is_folder=True)

            if prev_exports:
                with st.expander(t("capcut_expander_history", count=len(prev_exports)), expanded=False):
                    st.table(prev_exports)


# --- Backwards Compatibility Helpers ---

def render_projects_tab(selected_project_id: Optional[str]) -> Optional[str]:
    """Compatibility wrapper for tests referencing render_projects_tab."""
    render_home_page()
    return st.query_params.get("project") or selected_project_id


def render_script_tab(project_id: Optional[str]):
    """Compatibility wrapper for tests referencing render_script_tab."""
    if project_id:
        p_dir = get_project_dir(project_id)
        if p_dir.is_dir():
            st_info = compute_project_step_status(p_dir)
            render_script_step_flow(project_id, p_dir, st_info["manifest"], st_info["steps"]["script"], False)


def render_voice_tab(project_id: Optional[str]):
    """Compatibility wrapper for tests referencing render_voice_tab."""
    if project_id:
        p_dir = get_project_dir(project_id)
        if p_dir.is_dir():
            st_info = compute_project_step_status(p_dir)
            render_voice_step_flow(project_id, p_dir, st_info["manifest"], st_info["steps"]["voice"], False)
