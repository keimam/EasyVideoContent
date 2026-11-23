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
import traceback
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
from app.services.longform.script_import import (
    detect_leaked_chapter_headings,
    estimate_script_duration,
    fix_project_headings,
    parse_markdown_script,
)
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
    ProjectTrashError,
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
    fetch_openrouter_key_quota,
    get_llm_readiness,
    get_openrouter_model_metadata,
    is_openrouter_reasoning_model,
    list_llm_models,
    list_openrouter_free_models,
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
from app.services.longform.ui.i18n import get_ui_language, t
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
    if "[empty_after_http_ok]" in low:
        return "empty_http_ok"
    if (
        "[empty_reasoning]" in low
        or (
            ("empty text content" in low or "[empty_response]" in low)
            and ("reasoning_tokens" in low or "finish_reason=length" in low or "reasoning" in low)
        )
    ):
        return "empty_reasoning"
    if (
        "[auth_error]" in low
        or "error code: 401" in low
        or "http 401" in low
        or "invalid_api_key" in low
        or "invalid api key" in low
        or "incorrect api key" in low
        or "unauthorized" in low
    ):
        return "auth"
    if (
        "[daily_quota]" in low
        or "free-models-per-day" in low
        or "free models per day" in low
        or "requests per day" in low
        or "daily quota" in low
        or "daily limit" in low
        or "per-day" in low
    ):
        return "daily_quota"
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
    if "[empty_after_http_ok]" in low or "[empty_response]" in low or "empty text content" in low:
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
    show_raw_expander: bool = True,
    generic_is_job: bool = False,
) -> None:
    """Render user-friendly guidance + Settings button + Detail expander for LLM/job errors (F5/F6/C8.2)."""
    kind = _classify_ui_llm_error_kind(raw_err)
    if kind == "empty_reasoning":
        st.error(t("llm_err_empty_reasoning"))
        if st.button(t("btn_open_llm_settings"), key=f"btn_llm_set_{key_suffix}_{project_id}"):
            _open_mpt_settings_dialog("llm")
    elif kind == "auth":
        st.error(t("llm_err_invalid_key_401"))
        if st.button(t("btn_open_llm_settings"), key=f"btn_llm_set_{key_suffix}_{project_id}"):
            _open_mpt_settings_dialog("llm")
    elif kind == "daily_quota":
        st.error(t("llm_err_daily_quota_openrouter"))
    elif kind == "credit":
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
    elif generic_is_job:
        clean_reason = (raw_err or "").strip().splitlines()[0][:240]
        if clean_reason:
            st.error(t("llm_err_job_failed_summary", reason=clean_reason))
    else:
        st.error(t("llm_connection_failed", msg=raw_err))

    if show_raw_expander and raw_err:
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


def get_failed_project_loads() -> List[Dict[str, Any]]:
    """Return projects whose manifest.json failed validation/parsing so UI can surface them cleanly (D8.1)."""
    root = get_projects_root()
    failed: List[Dict[str, Any]] = []
    if not root.is_dir():
        return failed

    for p in sorted(root.iterdir()):
        if p.name.startswith("_"):
            continue
        if p.is_dir() and (p / "manifest.json").is_file():
            try:
                compute_project_step_status(p)
            except Exception as exc:
                failed.append({
                    "project_id": p.name,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                })
    return failed


def _copy_text_to_clipboard_best_effort(text: str) -> bool:
    """Copy text cleanly via platform_utils without scattered OS checks (D6, D8.1)."""
    from app.services.longform.platform_utils import copy_text_to_clipboard

    return copy_text_to_clipboard(text)


def _render_project_load_error(project_id: str, exc: BaseException, tb_str: str = "") -> None:
    """Display friendly load error message, suggestion, 'Salin detail' button, and traceback expander (D8.1)."""
    st.error(t("err_project_load_corrupt_or_legacy", id=project_id))
    st.info(t("err_project_load_suggestion"))

    full_detail = "\n\n".join(part for part in (str(exc).strip(), tb_str.strip()) if part)
    copy_flag_key = f"copy_load_err_{project_id}"
    if st.button(t("btn_copy_error_details"), key=f"btn_copy_load_err_{project_id}"):
        _copy_text_to_clipboard_best_effort(full_detail)
        st.session_state[copy_flag_key] = True
        st.toast(t("copied_error_details_toast"))

    with st.expander(
        t("error_details_expander"),
        expanded=bool(st.session_state.get(copy_flag_key, False)),
    ):
        st.code(full_detail, language="text")


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
        "script_outline": 1, "script_generate": 1, "script": 1, "script_ai": 1,
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
        skipped_cnt = int(prog.get("skipped", 0) or 0)
        skipped_suffix = f" • {t('prog_skipped_valid_locked', count=skipped_cnt)}" if skipped_cnt > 0 else ""

        if is_indet:
            display_txt = f"{phase_txt}{skipped_suffix} • {elapsed_str}" if elapsed_str else f"{phase_txt}{skipped_suffix}"
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
            if skipped_suffix:
                bar_text += skipped_suffix
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

            inferred_log_err = jobs.extract_last_error_from_logs(project_id) if not state.get("error_message") else None
            err_detail = str(
                state.get("error_message")
                or inferred_log_err
                or f"{t('job_exit_code_label')}: {state.get('exit_code', 'unknown')}"
            )
            logs_tail = jobs.get_job_logs(project_id, max_lines=20)
            combined_err = "\n".join([err_detail] + list(logs_tail or []))

            # Detect structured failure reasons (Empty reasoning / 401 Auth / Daily Quota / Credit / 404 / RPM 429 / Generic)
            model_match = re.search(r"\[MODEL_NOT_FOUND\]\s*Model=([^\s|]+)", combined_err)
            bad_model = model_match.group(1) if model_match else (get_llm_readiness().get("model") or "selected")
            _render_llm_friendly_error_box(
                project_id,
                combined_err,
                active_model=bad_model,
                key_suffix="job_err",
                show_raw_expander=False,
                generic_is_job=True,
            )

            if failed_step_name in ("script_outline", "script_generate", "script", "script_ai"):
                script_dir = get_project_dir(project_id) / "script"
                if script_dir.is_dir() and any(script_dir.glob("chapter_*.md")):
                    st.info(t("llm_completed_chapters_preserved"))

            if err_detail:
                friendly_map = {
                    "empty_reasoning": t("llm_err_empty_reasoning"),
                    "auth": t("llm_err_invalid_key_401"),
                    "daily_quota": t("llm_err_daily_quota_openrouter"),
                    "credit": t("llm_err_credit_insufficient"),
                    "unavailable_free": t("llm_err_model_unavailable_free", model=bad_model),
                    "model_not_found": t("llm_err_model_not_found", model=bad_model),
                    "empty_http_ok": t("llm_connection_empty_after_http_ok"),
                    "rate_limit": t("llm_err_rate_limit"),
                }
                err_kind = _classify_ui_llm_error_kind(combined_err)
                with st.expander(t("error_details_expander"), expanded=False):
                    if err_kind in friendly_map:
                        st.markdown(friendly_map[err_kind])
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
    """Render shared safe project delete confirmation modal (F7 / F10)."""
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
        pid = preview["project_id"]
        st.markdown(
            t(
                "dlg_delete_project_meta",
                title=preview["title"],
                pid=pid,
                mb=f"{preview['size_mb']:.2f}",
                cc_count=preview["capcut_draft_count"],
            )
        )
        st.caption(t("dlg_delete_project_soft_note"))

        is_running_proj = bool(preview["is_locked_or_running"])
        if is_running_proj:
            running_step = preview.get("running_step") or preview.get("lock_reason") or "running"
            st.warning(t("dlg_delete_running_warn", step=running_step))

        with st.form(key=f"form_del_proj_{pid}", clear_on_submit=False):
            typed_val = st.text_input(
                t("dlg_delete_confirm_label", pid=pid),
                key=f"txt_del_confirm_{pid}",
            )
            chk_capcut = st.checkbox(
                t("chk_delete_with_capcut"),
                value=False,
                key=f"chk_del_capcut_{pid}",
            )
            submitted = st.form_submit_button(
                t("btn_confirm_delete_project"),
                type="primary",
                use_container_width=True,
            )

        if submitted:
            typed_clean = (typed_val or "").strip()
            matches_confirm = bool(
                typed_clean and (typed_clean == pid or typed_clean == preview["title"].strip())
            )
            if not matches_confirm:
                st.error(t("dlg_delete_mismatch_err", pid=pid))
            else:
                prog_ph = st.progress(0, text=t("dlg_delete_stopping_status")) if is_running_proj else None

                def _on_delete_progress(frac: float, msg: str) -> None:
                    if prog_ph is not None:
                        pct = max(0, min(100, int(round(frac * 100))))
                        prog_ph.progress(pct, text=msg or t("dlg_delete_stopping_status"))

                try:
                    soft_delete_project(
                        pid,
                        delete_capcut_drafts=bool(chk_capcut),
                        stop_running_job=True,
                        max_wait_seconds=20.0,
                        progress_cb=_on_delete_progress,
                    )
                    st.session_state["confirm_delete_project_id"] = None
                    st.session_state["last_deleted_notice"] = t("delete_project_success", title=preview["title"])
                    if on_deleted_redirect_home:
                        st.query_params.clear()
                        st.session_state.pop(f"active_step_{pid}", None)
                    st.rerun()
                except Exception as exc:
                    if prog_ph is not None:
                        prog_ph.empty()
                    _render_friendly_error(t("err_delete_project_friendly"), exc)

        if st.button(
            t("btn_cancel_dialog"),
            key=f"btn_dlg_canc_del_{pid}",
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
    # C6: Studio sidebar navigation (sidebar already provides Short Video menu entry)
    from app.services.longform.ui.nav import render_mpt_entry
    render_mpt_entry(show_top_return=False)

    from app.services.branding.lifecycle_ui import is_wizard_active, render_studio_lifecycle_ui
    render_studio_lifecycle_ui(key_prefix="home")
    if is_wizard_active():
        return

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
    failed_loads = get_failed_project_loads()
    if failed_loads:
        with st.container(border=True):
            st.warning(t("home_corrupt_projects_warn", count=len(failed_loads)))
            for f_item in failed_loads[:5]:
                f_pid = f_item["project_id"]
                if st.button(t("btn_inspect_broken_project", id=f_pid), key=f"btn_open_broken_{f_pid}"):
                    st.query_params["project"] = f_pid
                    st.rerun()

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

                if comp_steps == 5:
                    col_co1, col_co2 = st.columns(2)
                    with col_co1:
                        if st.button(
                            t("btn_open_output_folder"),
                            key=f"btn_card_output_{p['project_id']}",
                            use_container_width=True,
                        ):
                            out_target = (p_dir / "export") if (p_dir / "export").is_dir() else p_dir
                            _safe_open_path(out_target, is_folder=True)
                    with col_co2:
                        if st.button(
                            t("btn_open_credits_md"),
                            key=f"btn_card_credits_{p['project_id']}",
                            use_container_width=True,
                        ):
                            from app.services.branding.lifecycle_ui import show_credits_dialog
                            show_credits_dialog(p_dir, p["project_id"])

    _render_delete_project_dialog(on_deleted_redirect_home=False)


# --- Halaman Proyek (Project Detail Page) Components (C4.3, C4.4, C4.5) ---

def render_project_page(project_id: str):
    """Render Halaman Proyek with Stepper and Single Adaptive Action (C4.3, D8.1)."""
    from app.services.longform.ui.nav import render_mpt_entry

    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir() or not (p_dir / "manifest.json").is_file():
        render_mpt_entry(show_top_return=False)
        st.error(t("err_project_not_found", id=project_id))
        if st.button("← " + t("btn_back_to_projects"), key=f"btn_notfound_back_{project_id}"):
            st.query_params.clear()
            st.rerun()
        return

    try:
        st_info = compute_project_step_status(p_dir)
    except Exception as exc:
        render_mpt_entry(show_top_return=False)
        if st.button("← " + t("btn_all_projects"), key=f"btn_err_back_{project_id}"):
            st.query_params.clear()
            st.rerun()
        _render_project_load_error(project_id, exc, traceback.format_exc())
        return

    manifest = st_info["manifest"]

    # C6: Studio sidebar navigation without redundant top back link
    render_mpt_entry(show_top_return=False)

    try:
        from app.services.branding.lifecycle_ui import render_update_banner
        render_update_banner(key_prefix=f"proj_{project_id}")
    except Exception:
        pass

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

                from app.services.longform.visual.base import has_real_visual_source
                visual_done_auto = (st_info["steps"]["visual"]["status"] == "selesai")
                auto_enabled_map = {
                    p_k: bool(st.session_state.get(f"cb_prov_{'ai' if p_k == 'ai_image' else p_k}_{project_id}", dflt))
                    for p_k, dflt in (
                        ("local", True),
                        ("pixabay", True),
                        ("coverr", True),
                        ("pexels", True),
                        ("wikimedia", True),
                        ("nasa", True),
                        ("openverse", True),
                        ("ai_image", False),
                    )
                }
                auto_enabled_sources = _resolve_ordered_selected_sources(auto_enabled_map, manifest)
                has_vis_src_auto = has_real_visual_source(p_dir, enabled_sources=auto_enabled_sources)
                auto_missing_vis_src = (not visual_done_auto) and (not has_vis_src_auto)
                if auto_missing_vis_src:
                    st.warning(t("vis_no_source_warning"))
                    if st.button(
                        t("btn_open_provider_settings"),
                        key=f"btn_auto_open_prov_{project_id}",
                        use_container_width=True,
                    ):
                        _open_mpt_settings_dialog("stock")

                auto_providers_order = list(auto_enabled_sources)
                auto_providers_csv = ",".join(auto_providers_order)
                auto_check_ai_rel = bool(st.session_state.get(f"cb_ai_rel_{project_id}", False))

                if is_review_paused:
                    phase_msg = f" ({job_state.get('phase')})" if job_state.get("phase") else ""
                    st.warning(t("auto_review_paused_warn", phase=phase_msg))
                    if st.button(t("btn_resume_auto_skip"), key=f"btn_resume_auto_{project_id}", disabled=(is_job_running or auto_missing_vis_src), type="primary", use_container_width=True):
                        auto_args = ["pipeline", "auto", project_id, "--skip-review-check"]
                        if auto_providers_csv:
                            auto_args.extend(["--providers", auto_providers_csv])
                        if auto_check_ai_rel:
                            auto_args.append("--check-ai-relevance")
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
                    if st.button(f"▷ {t('btn_auto_run_until_done')}", disabled=(is_job_running or auto_missing_vis_src), type="primary", key=f"btn_start_auto_{project_id}", use_container_width=True):
                        auto_args = ["pipeline", "auto", project_id]
                        if auto_providers_csv:
                            auto_args.extend(["--providers", auto_providers_csv])
                        if auto_check_ai_rel:
                            auto_args.append("--check-ai-relevance")
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

    # F12: Project Advanced Settings (Editable anchor_terms, avoid_terms & visual_summary)
    with st.expander(t("expander_project_advanced_settings"), expanded=False):
        from app.services.longform.llm_adapter import LLMCreditError, LLMModelNotFoundError
        from app.services.longform.visual.ai_keywords import (
            _clean_term_list,
            ensure_project_visual_anchors,
        )

        adv_notice = st.session_state.pop(f"proj_anchors_notice_{project_id}", None)
        if adv_notice:
            st.success(adv_notice)

        if st.session_state.pop(f"_reset_proj_anchors_{project_id}", False):
            st.session_state.pop(f"txt_proj_anchors_{project_id}", None)
            st.session_state.pop(f"txt_proj_avoids_{project_id}", None)
            st.session_state.pop(f"txt_proj_vis_sum_{project_id}", None)

        cur_anchors_str = ", ".join(getattr(manifest, "visual_anchor_terms", []) or [])
        cur_avoids_str = ", ".join(getattr(manifest, "visual_avoid_terms", []) or [])
        cur_vis_summary = getattr(manifest, "visual_summary", None) or ""

        col_anc1, col_anc2 = st.columns(2)
        with col_anc1:
            txt_anchors = st.text_input(
                t("project_anchor_terms_label"),
                value=cur_anchors_str,
                help=t("project_anchor_terms_help"),
                key=f"txt_proj_anchors_{project_id}",
                disabled=is_job_running,
            )
        with col_anc2:
            txt_avoids = st.text_input(
                t("project_avoid_terms_label"),
                value=cur_avoids_str,
                help=t("project_avoid_terms_help"),
                key=f"txt_proj_avoids_{project_id}",
                disabled=is_job_running,
            )

        txt_vis_sum = st.text_area(
            t("project_visual_summary_label"),
            value=cur_vis_summary,
            height=68,
            key=f"txt_proj_vis_sum_{project_id}",
            disabled=is_job_running,
        )

        col_anc_b1, col_anc_b2 = st.columns([1, 1])
        with col_anc_b1:
            if st.button(
                t("btn_save_project_anchors"),
                key=f"btn_save_proj_anchors_{project_id}",
                disabled=is_job_running,
                use_container_width=True,
            ):
                manifest.visual_anchor_terms = _clean_term_list(txt_anchors)
                manifest.visual_avoid_terms = _clean_term_list(txt_avoids)
                manifest.visual_summary = (txt_vis_sum or "").strip() or None
                save_manifest(manifest, p_dir)
                st.session_state[f"proj_anchors_notice_{project_id}"] = t("project_anchors_saved")
                st.rerun()
        with col_anc_b2:
            if st.button(
                t("btn_regen_project_anchors"),
                key=f"btn_regen_proj_anchors_{project_id}",
                disabled=is_job_running,
                use_container_width=True,
            ):
                try:
                    ensure_project_visual_anchors(manifest, force=True)
                    save_manifest(manifest, p_dir)
                    st.session_state[f"_reset_proj_anchors_{project_id}"] = True
                    st.session_state[f"proj_anchors_notice_{project_id}"] = t("project_anchors_regen_ok")
                    st.rerun()
                except (LLMCreditError, LLMModelNotFoundError) as exc:
                    llm_adv = check_llm_status()
                    _render_llm_friendly_error_box(
                        project_id,
                        str(exc),
                        active_model=str(llm_adv.get("model") or ""),
                        key_suffix="anchors",
                    )
                except Exception as exc:
                    _render_friendly_error(t("err_project_anchors_friendly"), exc)

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
    fix_h_notice = st.session_state.pop(f"fix_headings_notice_{project_id}", None)
    if fix_h_notice:
        st.success(fix_h_notice)

    if status == "selesai":
        st.success(t("script_summary_format", chapters=len(manifest.chapters)))
        leaked_hits = detect_leaked_chapter_headings(manifest)
        if leaked_hits:
            st.warning(t("script_leaked_headings_warn", count=len(leaked_hits)))
            if st.button(
                t("btn_fix_chapter_headings"),
                key=f"btn_fix_headings_{project_id}",
                disabled=is_job_running,
            ):
                try:
                    res_fix = fix_project_headings(p_dir)
                    st.session_state[f"fix_headings_notice_{project_id}"] = t(
                        "fix_headings_success",
                        groups=res_fix["fixed_groups"],
                        shots=res_fix["fixed_shots"],
                    )
                    st.rerun()
                except Exception as exc:
                    _render_friendly_error(t("err_fix_headings_friendly"), exc)

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
            if active_prov_id == "openrouter":
                if active_model.strip().lower() == "openrouter/free":
                    st.caption(t("openrouter_free_router_note"))
                elif is_openrouter_reasoning_model(active_model):
                    st.warning(t("openrouter_reasoning_model_warning", model=active_model))
            if llm_ok:
                st.caption(t("ai_provider_ready_badge", masked=llm_status.get("masked_key") or "OK"))
            elif llm_status.get("is_invalid"):
                st.warning(f"{llm_status['message']}")
            elif not use_fake:
                st.warning(llm_status.get("message") or t("ai_missing_key_warning"))

            if active_prov_id == "openrouter":
                col_llm_test, col_llm_quota, col_llm_cfg = st.columns([1.0, 0.95, 1.45])
                with col_llm_quota:
                    if st.button(
                        t("btn_check_or_quota"),
                        key=f"btn_or_quota_{project_id}",
                        disabled=(not llm_ok or is_job_running),
                        use_container_width=True,
                    ):
                        q_data, q_err = fetch_openrouter_key_quota()
                        st.session_state[f"or_quota_res_{project_id}"] = (q_data, q_err)
            else:
                col_llm_test, col_llm_cfg = st.columns(2)
            with col_llm_test:
                if st.button(t("btn_test_llm_connection"), key=f"btn_test_llm_{project_id}", disabled=(not llm_ok or is_job_running), use_container_width=True):
                    ok_conn, conn_msg, conn_lat = test_llm_connection()
                    st.session_state[f"llm_conn_res_{project_id}"] = (ok_conn, conn_msg, conn_lat)
            with col_llm_cfg:
                if st.button(t("btn_change_llm_provider"), key=f"btn_chg_llm_{project_id}", use_container_width=True):
                    _open_mpt_settings_dialog("llm")

            if active_prov_id == "openrouter":
                or_quota_res = st.session_state.get(f"or_quota_res_{project_id}")
                if or_quota_res:
                    q_data, q_err = or_quota_res
                    if q_err:
                        st.warning(t("or_quota_error", error=q_err))
                    elif isinstance(q_data, dict):
                        tier_lbl = "Free Tier" if q_data.get("is_free_tier") else "Standard"
                        rem_val = q_data.get("limit_remaining")
                        lim_rem_str = "∞" if rem_val is None else f"{rem_val}"
                        u_daily = q_data.get("usage_daily")
                        u_daily_str = "0" if u_daily is None else f"{u_daily}"
                        msg_parts = [
                            t(
                                "or_quota_summary",
                                tier=tier_lbl,
                                usage_daily=u_daily_str,
                                limit_remaining=lim_rem_str,
                            )
                        ]
                        if q_data.get("free_daily_limit") is not None or q_data.get("free_daily_used") is not None:
                            msg_parts.append(
                                t(
                                    "or_quota_free_daily_line",
                                    used=q_data.get("free_daily_used") if q_data.get("free_daily_used") is not None else "?",
                                    limit=q_data.get("free_daily_limit") if q_data.get("free_daily_limit") is not None else "?",
                                    remaining=q_data.get("free_daily_remaining") if q_data.get("free_daily_remaining") is not None else "?",
                                )
                            )
                        msg_parts.append(
                            t("or_quota_free_tier_hint" if q_data.get("is_free_tier") else "or_quota_paid_tier_hint")
                        )
                        st.info("\n\n".join(msg_parts))

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
                        if active_prov_id == "openrouter":
                            free_entries, _ = list_openrouter_free_models(force_refresh=False)
                            st.session_state["llm_or_free_cache"] = free_entries

                    cached_models, fetch_err = st.session_state.get(f"llm_models_cache_{active_prov_id}", ([], None))
                    if fetch_err:
                        st.caption(t("llm_models_load_fallback", error=fetch_err))
                    elif cached_models:
                        st.caption(t("llm_models_loaded_count", count=len(cached_models), provider=active_prov_label))
                        display_models = list(cached_models)
                        if active_prov_id == "openrouter":
                            free_only = st.checkbox(
                                t("openrouter_filter_free_only"),
                                value=True,
                                key=f"cb_or_free_only_{project_id}",
                            )
                            if free_only:
                                free_entries = st.session_state.get("llm_or_free_cache")
                                if free_entries:
                                    display_models = [e["id"] for e in free_entries]
                                else:
                                    display_models = [
                                        m for m in cached_models
                                        if ":free" in m.lower() or m.lower() == "openrouter/free"
                                    ]
                                if active_model and active_model != "-" and active_model not in display_models:
                                    display_models.append(active_model)
                        if not display_models:
                            display_models = list(cached_models)

                        def _fmt_model_opt(mid: str) -> str:
                            if active_prov_id != "openrouter":
                                return mid
                            meta = get_openrouter_model_metadata(mid)
                            if meta.get("is_random_router"):
                                return f"{mid} [{t('openrouter_tag_random')}]"
                            if meta.get("is_reasoning"):
                                return f"{mid} [{t('openrouter_tag_reasoning')}]"
                            return mid

                        sel_idx = display_models.index(active_model) if active_model in display_models else 0
                        picked_model = st.selectbox(
                            t("field_llm_model_select"),
                            options=display_models,
                            index=sel_idx,
                            format_func=_fmt_model_opt,
                            key=f"sel_llm_model_{project_id}_{active_prov_id}",
                        )
                        if active_prov_id == "openrouter" and picked_model and is_openrouter_reasoning_model(picked_model):
                            st.caption(t("openrouter_reasoning_model_warning", model=picked_model))
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


def get_shot_thumbnail_path(project_dir: Path, shot: Shot, fps: int = 25) -> Optional[Path]:
    """Get hash-keyed thumbnail image path matching the shot's single visual source of truth (W2, F14).

    1. Resolves canonical source via `resolve_shot_visual_source` (`rendered_clip` first, else `asset_path`).
    2. Keys cache filename by content hash (`assets/visual/thumbnails/<shot_id>_<hash16>.jpg`).
    3. Removes stale unkeyed or outdated hash thumbnails whenever source changes.
    """
    from app.services.longform.visual.base import (
        invalidate_shot_thumbnail_cache,
        resolve_shot_visual_source,
    )

    vis_src = resolve_shot_visual_source(project_dir, shot, fps=fps)
    src_file = vis_src.get("source_path")
    src_hash = vis_src.get("source_hash")
    if not src_file or not src_file.is_file():
        return None

    t_dir = project_dir / "assets" / "visual" / "thumbnails"
    t_file = t_dir / (f"{shot.shot_id}_{src_hash}.jpg" if src_hash else f"{shot.shot_id}.jpg")
    if t_file.is_file() and t_file.stat().st_size > 0:
        return t_file

    # If a legacy unkeyed thumbnail exists and is newer/equal to current source (not stale), promote without invoking ffmpeg
    legacy_file = t_dir / f"{shot.shot_id}.jpg"
    if (
        legacy_file.is_file()
        and legacy_file.stat().st_size > 0
        and t_file != legacy_file
        and not vis_src.get("needs_rebuild")
        and getattr(shot, "status", None) != ItemStatus.stale
    ):
        try:
            if legacy_file.stat().st_mtime >= src_file.stat().st_mtime:
                import shutil as _shutil
                t_dir.mkdir(parents=True, exist_ok=True)
                _shutil.copy2(legacy_file, t_file)
                legacy_file.unlink(missing_ok=True)
                return t_file
        except OSError:
            pass

    # Clean older/unkeyed thumbnails for this shot before extracting fresh frame
    invalidate_shot_thumbnail_cache(project_dir, shot.shot_id)

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


def _download_image_thumbnail(image_url: str, dest_jpg: Path) -> bool:
    """Download remote image/thumbnail via HTTP with EasyFaceless User-Agent and resize to 320px JPEG (A1.2)."""
    if not image_url or not str(image_url).strip().lower().startswith(("http://", "https://")):
        return False
    try:
        import io
        import requests
        from PIL import Image
        from app.services.longform.visual.free_providers import EASYFACELESS_USER_AGENT

        resp = requests.get(
            str(image_url).strip(),
            headers={"User-Agent": EASYFACELESS_USER_AGENT},
            timeout=12,
        )
        if resp.status_code != 200 or not resp.content:
            return False
        with Image.open(io.BytesIO(resp.content)) as im:
            im_rgb = im.convert("RGB")
            w, h = im_rgb.size
            if w <= 0 or h <= 0:
                return False
            new_h = max(1, int(h * (320 / w)))
            im_rgb.resize((320, new_h), Image.Resampling.LANCZOS).save(dest_jpg, "JPEG", quality=85)
        return dest_jpg.is_file() and dest_jpg.stat().st_size > 0
    except Exception:
        return False


def get_candidate_thumbnail_path(project_dir: Path, candidate: Dict[str, Any]) -> Optional[Path]:
    """Get or extract 320px cached thumbnail for an alternative visual candidate (X2, A1.2)."""
    cand_id = candidate.get("candidate_id") or str(abs(hash(candidate.get("url", ""))))
    safe_cid = re.sub(r"[^A-Za-z0-9._-]+", "_", str(cand_id))
    cand_t_dir = project_dir / "assets" / "visual" / "thumbnails" / "candidates"
    cand_t_file = cand_t_dir / f"{safe_cid}.jpg"
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
    thumb_url = candidate.get("thumbnail_url") or (candidate.get("source_info") or {}).get("thumb_url")
    src_type = str(candidate.get("source_type") or "").lower().strip()
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
                    im_rgb.resize((320, max(new_h, 1)), Image.Resampling.LANCZOS).save(cand_t_file, "JPEG", quality=85)
        else:
            # 2. Remote image thumbnail URL first (Wikimedia thumburl, Openverse/NASA/Pixabay thumbnails)
            candidate_img_urls: List[str] = []
            if thumb_url:
                candidate_img_urls.append(str(thumb_url))
            url_low = str(url or "").lower().split("?")[0]
            if url and (src_type == "stock_image" or url_low.endswith((".jpg", ".jpeg", ".png", ".webp"))):
                if str(url) not in candidate_img_urls:
                    candidate_img_urls.append(str(url))

            for img_u in candidate_img_urls:
                if _download_image_thumbnail(img_u, cand_t_file):
                    return cand_t_file

            # 3. Video URL fallback via ffmpeg when no static thumbnail URL succeeded
            if url and src_type != "stock_image":
                from app.services.longform.visual.free_providers import EASYFACELESS_USER_AGENT
                cmd = [
                    "ffmpeg", "-y",
                    "-user_agent", EASYFACELESS_USER_AGENT,
                    "-ss", f"{ss:.2f}",
                    "-i", str(url),
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


_DEFAULT_VISUAL_ORDER: tuple[str, ...] = (
    "local",
    "pixabay",
    "coverr",
    "pexels",
    "wikimedia",
    "nasa",
    "openverse",
    "ai_image",
)

_VISUAL_SOURCE_LABELS: Dict[str, str] = {
    "local": "Local files",
    "pixabay": "Pixabay",
    "coverr": "Coverr",
    "pexels": "Pexels",
    "wikimedia": "Wikimedia",
    "nasa": "NASA",
    "openverse": "Openverse",
    "ai_image": "AI Image",
}


def _format_visual_source_label(src: Optional[str]) -> str:
    """Format an asset's actual_source identifier for user-facing display."""
    low = str(src or "").strip().lower()
    if low == "fake":
        return t("source_placeholder")
    if low == "neutral_backdrop":
        return t("source_neutral_backdrop")
    if low == "external_image":
        return t("source_external_image")
    if low in _VISUAL_SOURCE_LABELS:
        return "Local" if low == "local" else _VISUAL_SOURCE_LABELS[low]
    if str(src or "").startswith("AI Video"):
        return str(src)
    return str(src or "stock").capitalize()


def _get_project_source_order(manifest: ProjectManifest) -> List[str]:
    """Return complete deduplicated source order persisted on manifest."""
    raw = [str(p or "").lower().strip() for p in (getattr(manifest, "visual_source_order", []) or []) if str(p or "").strip()]
    order: List[str] = []
    for p in raw + list(_DEFAULT_VISUAL_ORDER):
        if p in _DEFAULT_VISUAL_ORDER and p not in order:
            order.append(p)
    return order


def _resolve_ordered_selected_sources(enabled_map: Dict[str, bool], manifest: ProjectManifest) -> List[str]:
    """Return checked visual sources ordered by manifest.visual_source_order."""
    return [p for p in _get_project_source_order(manifest) if enabled_map.get(p, False)]


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

    # --- V1 / D9.2: Source Selection Configuration (Pixabay -> Coverr -> Wikimedia -> NASA -> Openverse) ---
    from app.config import config
    pixabay_keys = config.app.get("pixabay_api_keys", []) or []
    has_pixabay = bool((isinstance(pixabay_keys, list) and any(k.strip() for k in pixabay_keys)) or (isinstance(pixabay_keys, str) and pixabay_keys.strip()))
    raw_pk = pixabay_keys[0] if (isinstance(pixabay_keys, list) and pixabay_keys) else str(pixabay_keys)
    mask_pixabay = mask_api_key(raw_pk) if has_pixabay else ""

    coverr_keys = config.app.get("coverr_api_keys", []) or []
    has_coverr = bool((isinstance(coverr_keys, list) and any(k.strip() for k in coverr_keys)) or (isinstance(coverr_keys, str) and coverr_keys.strip()))
    raw_ck = coverr_keys[0] if (isinstance(coverr_keys, list) and coverr_keys) else str(coverr_keys)
    mask_coverr = mask_api_key(raw_ck) if has_coverr else ""

    pexels_keys = config.app.get("pexels_api_keys", []) or []
    has_pexels = bool((isinstance(pexels_keys, list) and any(k.strip() for k in pexels_keys)) or (isinstance(pexels_keys, str) and pexels_keys.strip()))
    raw_pxk = pexels_keys[0] if (isinstance(pexels_keys, list) and pexels_keys) else str(pexels_keys)
    mask_pexels = mask_api_key(raw_pxk) if has_pexels else ""

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
        format_keyword_reason,
        infer_keyword_source,
    )
    from app.services.longform.language_guard import resolve_project_language
    from app.services.longform.visual.keyword_translator import (
        is_non_english_language,
        translate_single_query_to_english,
    )

    from app.services.longform.visual.base import (
        has_real_visual_source,
        inspect_project_missing_visuals,
        is_dev_or_test_mode,
    )

    ai_readiness = get_ai_image_readiness()
    has_ai_image = bool(ai_readiness.get("ready", False))
    vision_info = get_vision_readiness()
    ai_vid_providers = list_configured_ai_video_providers()

    _cb_pix = st.session_state.get(f"cb_prov_pixabay_{project_id}", has_pixabay) if has_pixabay else False
    _cb_cov = st.session_state.get(f"cb_prov_coverr_{project_id}", has_coverr) if has_coverr else False
    _cb_pxl = st.session_state.get(f"cb_prov_pexels_{project_id}", has_pexels) if has_pexels else False
    _cb_wiki = bool(st.session_state.get(f"cb_prov_wikimedia_{project_id}", True))
    _cb_nasa = bool(st.session_state.get(f"cb_prov_nasa_{project_id}", True))
    _cb_ov = bool(st.session_state.get(f"cb_prov_openverse_{project_id}", True))
    _cb_ai = st.session_state.get(f"cb_prov_ai_{project_id}", False) if has_ai_image else False
    _cb_loc = bool(st.session_state.get(f"cb_prov_local_{project_id}", True))
    _cb_ai_rel = bool(st.session_state.get(f"cb_ai_rel_{project_id}", False)) if vision_info.get("vision_supported") else False

    selected_sources = _resolve_ordered_selected_sources(
        {
            "pixabay": bool(_cb_pix),
            "coverr": bool(_cb_cov),
            "pexels": bool(_cb_pxl),
            "wikimedia": bool(_cb_wiki),
            "nasa": bool(_cb_nasa),
            "openverse": bool(_cb_ov),
            "local": bool(_cb_loc),
            "ai_image": bool(_cb_ai),
        },
        manifest,
    )

    has_vis_source = has_real_visual_source(p_dir, enabled_sources=selected_sources)
    start_vis_blocked = (not has_vis_source) and (not is_dev_or_test_mode())

    only_ai_selected = bool(_cb_ai and not (_cb_pix or _cb_cov or _cb_pxl or _cb_wiki or _cb_nasa or _cb_ov))
    target_prov = "ai_image" if only_ai_selected else None
    providers_csv = ",".join(selected_sources) if selected_sources else ""

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
                if st.button(t("visual_btn_resume"), type="primary", disabled=start_vis_blocked, key=f"btn_resume_vis_{project_id}", use_container_width=True):
                    vis_args = ["visual", "pipeline", project_id]
                    if target_prov:
                        vis_args.extend(["--provider", target_prov])
                    elif providers_csv:
                        vis_args.extend(["--providers", providers_csv])
                    if _cb_ai_rel:
                        vis_args.append("--check-ai-relevance")
                    _start_job_safe(project_id, "visual_pipeline", jobs.build_longform_cli_command(*vis_args))
            elif st_val == "perlu_diperbarui":
                if st.button(t("visual_btn_rebuild_stale"), type="primary", disabled=start_vis_blocked, key=f"btn_update_vis_{project_id}", use_container_width=True):
                    vis_args = ["visual", "pipeline", project_id]
                    if target_prov:
                        vis_args.extend(["--provider", target_prov])
                    elif providers_csv:
                        vis_args.extend(["--providers", providers_csv])
                    if _cb_ai_rel:
                        vis_args.append("--check-ai-relevance")
                    _start_job_safe(project_id, "visual_pipeline", jobs.build_longform_cli_command(*vis_args))
            else:  # belum
                st_all = compute_project_step_status(p_dir)
                script_st = st_all["steps"]["script"]["status"]
                voice_st = st_all["steps"]["voice"]["status"]
                script_done = (script_st == "selesai")
                voice_done = (voice_st == "selesai")
                btn_disabled = not (script_done and voice_done) or start_vis_blocked

                if not (script_done and voice_done):
                    if not script_done and not voice_done:
                        st.caption(t("visual_prereq_both"))
                    elif not script_done:
                        st.caption(t("visual_prereq_script"))
                    else:
                        st.caption(t("visual_prereq_voice"))

                if st.button(t("visual_btn_start"), type="primary", disabled=btn_disabled, key=f"btn_start_vis_{project_id}", use_container_width=True):
                    vis_args = ["visual", "pipeline", project_id]
                    if target_prov:
                        vis_args.extend(["--provider", target_prov])
                    elif providers_csv:
                        vis_args.extend(["--providers", providers_csv])
                    if _cb_ai_rel:
                        vis_args.append("--check-ai-relevance")
                    _start_job_safe(project_id, "visual_pipeline", jobs.build_longform_cli_command(*vis_args))

    # --- D8: Clear warning when no stock/local visual source is configured ---
    if not has_vis_source:
        col_vw, col_vb = st.columns([3.2, 1.2])
        with col_vw:
            st.warning(t("vis_no_source_warning"))
        with col_vb:
            st.write("")
            if st.button(
                t("btn_open_provider_settings"),
                key=f"btn_vis_open_prov_{project_id}",
                use_container_width=True,
            ):
                _open_mpt_settings_dialog("stock")

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

    used_sources_counts: Dict[str, int] = {}
    for ch in manifest.chapters:
        for s in ch.shots:
            has_asset = bool(s.asset_path and (p_dir / s.asset_path).is_file())
            has_clip = bool(s.rendered_clip and (p_dir / s.rendered_clip).is_file())
            if has_asset or has_clip:
                src_lbl = _format_visual_source_label(
                    "fake" if getattr(s, "is_fake", False) else (s.actual_source or s.requested_source or "stock")
                )
                used_sources_counts[src_lbl] = used_sources_counts.get(src_lbl, 0) + 1
    sources_str = ", ".join(sorted(used_sources_counts.keys())) if used_sources_counts else t("source_none_yet")
    source_dist_items = [
        f"{k} {v}"
        for k, v in sorted(used_sources_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    unready_vis_info = inspect_project_missing_visuals(p_dir, manifest) if total_shots > 0 else {
        "missing_count": 0,
        "legacy_fake_count": 0,
        "unready_count": 0,
    }

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    with col_m1:
        render_metric_tile(t("metric_clips_ready"), f"{built_shots} / {total_shots}", status="success" if built_shots == total_shots and total_shots > 0 else "")
    with col_m2:
        render_metric_tile(t("metric_needs_review"), str(needs_review_count), status="warning" if needs_review_count > 0 else "")
    with col_m3:
        render_metric_tile(t("metric_locked"), str(locked_count), status="accent" if locked_count > 0 else "")
    with col_m4:
        render_metric_tile(t("metric_sources_used"), sources_str)
    if source_dist_items:
        st.caption(t("source_distribution_summary", items=" • ".join(source_dist_items)))

    if unready_vis_info["missing_count"] > 0:
        st.warning(t("vis_missing_summary_warn", missing=unready_vis_info["missing_count"]))
    if unready_vis_info["legacy_fake_count"] > 0:
        st.warning(t("vis_legacy_fake_summary_warn", fake=unready_vis_info["legacy_fake_count"]))
    if unready_vis_info["unready_count"] > 0:
        if st.button(
            t("btn_refetch_unready"),
            key=f"btn_refetch_unready_{project_id}",
            disabled=(is_job_running or start_vis_blocked),
        ):
            ref_args = ["visual", "pipeline", project_id]
            if target_prov:
                ref_args.extend(["--provider", target_prov])
            elif providers_csv:
                ref_args.extend(["--providers", providers_csv])
            if _cb_ai_rel:
                ref_args.append("--check-ai-relevance")
            _start_job_safe(project_id, "visual_pipeline", jobs.build_longform_cli_command(*ref_args))

    # --- Footage Sources Expander (V3 + D9.2 Free Sources) ---
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
            t("prov_pexels_key", mask=mask_pexels) if has_pexels else t("prov_pexels_no_key"),
            value=has_pexels,
            disabled=not has_pexels,
            help=t("prov_pexels_closed_note"),
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
        col_f1, col_f2, col_f3 = st.columns(3)
        col_f1.checkbox(
            t("prov_wikimedia_free"),
            value=True,
            disabled=False,
            key=f"cb_prov_wikimedia_{project_id}",
        )
        col_f2.checkbox(
            t("prov_nasa_free"),
            value=True,
            disabled=False,
            key=f"cb_prov_nasa_{project_id}",
        )
        col_f3.checkbox(
            t("prov_openverse_free"),
            value=True,
            disabled=False,
            key=f"cb_prov_openverse_{project_id}",
        )
        st.caption(t("prov_source_order_caption"))
        st.caption(t("prov_pexels_closed_note"))

        # V0: Explicit opt-in random fallback for Local files
        cur_fill_random = bool(getattr(manifest, "fill_local_random", False))
        new_fill_random = st.checkbox(
            t("chk_fill_local_random"),
            value=cur_fill_random,
            help=t("chk_fill_local_random_help"),
            disabled=is_job_running,
            key=f"chk_fill_local_rnd_{project_id}",
        )
        if new_fill_random != cur_fill_random:
            manifest.fill_local_random = bool(new_fill_random)
            save_manifest(manifest, p_dir)

        # V2: Local files UX (count, Open folder button, multi-file uploader with collision handling & guide)
        from app.services.longform.visual.local import (
            ALL_SUPPORTED_EXTS,
            SUPPORTED_IMAGE_EXTS,
            SUPPORTED_VIDEO_EXTS,
            save_uploaded_library_files,
        )
        from app.services.longform.platform_utils import open_path_in_os

        loc_mgr = LocalLibraryManager(p_dir)
        loc_assets = loc_mgr.list_assets()
        col_loc1, col_loc2 = st.columns([2.2, 1.4])
        with col_loc1:
            st.caption(t("local_lib_count_label", count=len(loc_assets)))
            st.caption(
                t(
                    "local_upload_guide_text",
                    videos=", ".join(sorted(SUPPORTED_VIDEO_EXTS)),
                    images=", ".join(sorted(SUPPORTED_IMAGE_EXTS)),
                )
            )
        with col_loc2:
            if st.button(
                t("btn_open_local_folder"),
                key=f"btn_open_local_lib_{project_id}",
                use_container_width=True,
            ):
                if open_path_in_os(loc_mgr.library_dir, is_folder=True):
                    st.toast(t("local_folder_opened_ok", path=str(loc_mgr.library_dir)))
                else:
                    st.info(t("local_folder_open_fail", path=str(loc_mgr.library_dir)))

        local_uploads = st.file_uploader(
            t("local_multi_upload_label"),
            accept_multiple_files=True,
            disabled=is_job_running,
            key=f"up_local_lib_multi_{project_id}",
        )
        conflict_key = f"local_lib_conflicts_{project_id}"
        if local_uploads:
            if st.button(
                t("btn_save_local_uploads"),
                key=f"btn_save_local_lib_{project_id}",
                disabled=is_job_running,
            ):
                items_payload = [(uf.name, uf.getvalue()) for uf in local_uploads]
                up_res = save_uploaded_library_files(loc_mgr, items_payload, conflict_policy="ask")
                for bad_name, bad_reason in up_res["rejected"]:
                    st.error(
                        t(
                            "local_upload_invalid_ext",
                            name=bad_name,
                            reason=bad_reason,
                            formats=", ".join(sorted(ALL_SUPPORTED_EXTS)),
                        )
                    )
                if up_res["saved"]:
                    saved_names = ", ".join(x["filename"] for x in up_res["saved"])
                    st.success(t("local_upload_saved_ok", count=len(up_res["saved"]), names=saved_names))
                if up_res["conflicts"]:
                    st.session_state[conflict_key] = up_res["conflicts"]
                elif up_res["saved"] and not up_res["rejected"]:
                    st.rerun()

        pending_conflicts = st.session_state.get(conflict_key) or []
        if pending_conflicts:
            conflict_names = ", ".join(c_name for c_name, _ in pending_conflicts)
            st.warning(t("local_upload_conflict_prompt", names=conflict_names))
            c_action = st.radio(
                t("local_upload_conflict_prompt", names=conflict_names),
                options=["rename", "overwrite", "skip"],
                format_func=lambda opt: t(f"local_conflict_opt_{opt}"),
                horizontal=True,
                label_visibility="collapsed",
                key=f"rad_local_conflict_{project_id}",
            )
            if st.button(
                t("btn_apply_local_conflicts"),
                key=f"btn_apply_local_conflict_{project_id}",
                disabled=is_job_running,
            ):
                c_res = save_uploaded_library_files(loc_mgr, pending_conflicts, conflict_policy=c_action)
                st.session_state.pop(conflict_key, None)
                if c_res["saved"]:
                    saved_names = ", ".join(x["filename"] for x in c_res["saved"])
                    st.success(t("local_upload_saved_ok", count=len(c_res["saved"]), names=saved_names))
                st.rerun()

        # V1: Per-project Search Mode ('mix_best' vs 'priority'), diversity controls, and reorderable source order
        st.divider()
        cur_mode = str(getattr(manifest, "visual_search_mode", "priority") or "priority").lower().strip()
        if cur_mode not in ("mix_best", "priority"):
            cur_mode = "priority"
        new_mode = st.radio(
            t("visual_search_mode_label"),
            options=["mix_best", "priority"],
            index=0 if cur_mode == "mix_best" else 1,
            format_func=lambda m: t("visual_search_mode_mix_best") if m == "mix_best" else t("visual_search_mode_priority"),
            horizontal=True,
            help=t("visual_search_mode_help"),
            disabled=is_job_running,
            key=f"rad_vis_mode_{project_id}",
        )
        if new_mode != cur_mode:
            manifest.visual_search_mode = new_mode
            save_manifest(manifest, p_dir)
            st.rerun()

        if new_mode == "mix_best":
            cur_share = float(getattr(manifest, "visual_max_source_share", 0.6) or 0.6)
            cur_pct = int(round(cur_share * 100))
            cur_cap_enabled = cur_pct < 100
            cur_avoid_consec = bool(getattr(manifest, "visual_avoid_consecutive_same_source", True))
            st.caption(f"**{t('visual_diversity_heading')}**")
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                new_cap_enabled = st.checkbox(
                    t("chk_limit_source_share", pct=cur_pct if cur_cap_enabled else 60),
                    value=cur_cap_enabled,
                    disabled=is_job_running,
                    key=f"chk_div_cap_{project_id}",
                )
                if new_cap_enabled:
                    new_pct = st.slider(
                        t("slider_max_source_share"),
                        min_value=25,
                        max_value=95,
                        value=min(95, max(25, cur_pct if cur_pct < 100 else 60)),
                        step=5,
                        disabled=is_job_running,
                        key=f"sld_div_pct_{project_id}",
                    )
                    new_share = round(new_pct / 100.0, 2)
                else:
                    new_share = 1.0
                if abs(new_share - cur_share) > 1e-4:
                    manifest.visual_max_source_share = new_share
                    save_manifest(manifest, p_dir)
            with col_d2:
                new_avoid_consec = st.checkbox(
                    t("chk_avoid_consec_same_source"),
                    value=cur_avoid_consec,
                    disabled=is_job_running,
                    key=f"chk_div_consec_{project_id}",
                )
                if new_avoid_consec != cur_avoid_consec:
                    manifest.visual_avoid_consecutive_same_source = bool(new_avoid_consec)
                    save_manifest(manifest, p_dir)
        else:
            st.caption(f"**{t('visual_source_order_title')}**")
            proj_order = _get_project_source_order(manifest)
            # Reorderable active non-local sources (Local is always checked first when passing)
            reorderable = [p for p in proj_order if p in selected_sources and p != "local"]
            for idx_r, src_k in enumerate(reorderable):
                rc1, rc2, rc3 = st.columns([4, 0.7, 0.7])
                rc1.markdown(f"`{idx_r + 1}.` **{_VISUAL_SOURCE_LABELS.get(src_k, src_k)}**")
                if rc2.button(
                    t("visual_source_move_up"),
                    key=f"btn_src_up_{project_id}_{src_k}",
                    disabled=is_job_running or idx_r == 0,
                ):
                    new_re = list(reorderable)
                    new_re[idx_r - 1], new_re[idx_r] = new_re[idx_r], new_re[idx_r - 1]
                    rest = [p for p in proj_order if p not in new_re]
                    manifest.visual_source_order = (["local"] if "local" in rest else []) + new_re + [p for p in rest if p != "local"]
                    save_manifest(manifest, p_dir)
                    st.rerun()
                if rc3.button(
                    t("visual_source_move_down"),
                    key=f"btn_src_dn_{project_id}_{src_k}",
                    disabled=is_job_running or idx_r == len(reorderable) - 1,
                ):
                    new_re = list(reorderable)
                    new_re[idx_r], new_re[idx_r + 1] = new_re[idx_r + 1], new_re[idx_r]
                    rest = [p for p in proj_order if p not in new_re]
                    manifest.visual_source_order = (["local"] if "local" in rest else []) + new_re + [p for p in rest if p != "local"]
                    save_manifest(manifest, p_dir)
                    st.rerun()
        if has_ai_image and cb_ai:
            ai_est = estimate_ai_image_usage(manifest, p_dir)
            st.info(
                t(
                    "prov_ai_estimate_notice",
                    provider=ai_readiness["backend_label"],
                    count=ai_est["estimated_count"],
                )
            )
        if has_ai_image and str(ai_readiness.get("backend") or "") == "openai_image":
            from app.services.longform.visual.image_presets import test_single_openai_image

            if st.button(
                t("btn_test_single_image"),
                key=f"btn_lf_test_single_img_{project_id}",
                disabled=is_job_running,
            ):
                with st.spinner(t("spinner_testing_single_image")):
                    st.session_state[f"lf_img_test_res_{project_id}"] = test_single_openai_image()
            lf_img_res = st.session_state.get(f"lf_img_test_res_{project_id}")
            if isinstance(lf_img_res, dict):
                if lf_img_res.get("ok"):
                    st.success(t("img_test_ok", w=lf_img_res["width"], h=lf_img_res["height"], model=lf_img_res["model"]))
                    st.image(lf_img_res["image_bytes"], width=320)
                elif lf_img_res.get("message"):
                    st.error(lf_img_res["message"])
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

    # --- V3: External Image Bridge (Export prompts & bulk import) ---
    if total_shots > 0:
        from app.services.longform.visual.external_bridge import (
            EXTERNAL_IMPORT_EXTENSIONS,
            apply_bulk_external_import,
            export_external_image_prompts,
            plan_bulk_external_import,
        )

        with st.expander(t("expander_external_bridge"), expanded=False):
            col_exp, col_imp = st.columns(2)
            all_shot_ids = [s.shot_id for ch in manifest.chapters for s in ch.shots]

            with col_exp:
                st.markdown(f"**{t('ext_export_heading')}**")
                cur_sel_sid = st.session_state.get(f"selected_shot_{project_id}")
                exp_scope = st.radio(
                    t("ext_export_scope_label"),
                    options=["missing_visual", "selected", "all"],
                    index=0,
                    format_func=lambda s: {
                        "missing_visual": t("ext_scope_missing"),
                        "selected": t("ext_scope_selected"),
                        "all": t("ext_scope_all"),
                    }[s],
                    key=f"rad_ext_scope_{project_id}",
                )
                chosen_sids: List[str] = []
                if exp_scope == "selected":
                    dflt_sids = [cur_sel_sid] if (cur_sel_sid and cur_sel_sid in all_shot_ids) else (all_shot_ids[:1])
                    chosen_sids = st.multiselect(
                        t("ext_export_select_shots"),
                        options=all_shot_ids,
                        default=dflt_sids,
                        key=f"ms_ext_sids_{project_id}",
                    )

                exp_cache_key = f"ext_export_payload_{project_id}"
                if st.button(
                    t("btn_prepare_prompt_export"),
                    key=f"btn_ext_prepare_{project_id}",
                    disabled=is_job_running,
                ):
                    res_exp = export_external_image_prompts(
                        manifest,
                        p_dir,
                        scope=exp_scope,
                        selected_shot_ids=chosen_sids,
                    )
                    st.session_state[exp_cache_key] = res_exp

                cached_exp = st.session_state.get(exp_cache_key)
                if isinstance(cached_exp, dict) and cached_exp.get("count", 0) > 0:
                    st.success(t("ext_export_ready", count=cached_exp["count"]))
                    st.download_button(
                        t("btn_download_prompts_txt"),
                        data=cached_exp["txt"],
                        file_name=f"{project_id}_prompts.txt",
                        mime="text/plain",
                        key=f"dl_ext_txt_{project_id}",
                    )
                    st.download_button(
                        t("btn_download_prompts_csv"),
                        data=cached_exp["csv"].encode("utf-8"),
                        file_name=f"{project_id}_prompts.csv",
                        mime="text/csv",
                        key=f"dl_ext_csv_{project_id}",
                    )

            with col_imp:
                st.markdown(f"**{t('ext_import_heading')}**")
                st.caption(t("ext_import_hint"))
                ext_uploads = st.file_uploader(
                    t("ext_import_uploader"),
                    accept_multiple_files=True,
                    disabled=is_job_running,
                    key=f"up_ext_bulk_{project_id}",
                )
                if ext_uploads:
                    raw_files = [(uf.name, uf.getvalue()) for uf in ext_uploads]
                    plan_rows = plan_bulk_external_import(manifest, raw_files)
                    st.markdown(f"**{t('ext_import_preview_heading')}**")

                    unmatched_names = [
                        r["filename"] for r in plan_rows
                        if (not r["supported"]) or (not r["matched_shot_id"])
                    ]
                    if unmatched_names:
                        st.warning(
                            t(
                                "ext_import_unmatched_warn",
                                count=len(unmatched_names),
                                names=", ".join(unmatched_names),
                            )
                        )

                    shot_select_opts = [""] + all_shot_ids
                    edited_mappings: List[Dict[str, Any]] = []
                    for r in plan_rows:
                        r_idx = r["index"]
                        mc1, mc2 = st.columns([1.3, 1.7])
                        with mc1:
                            st.caption(f"**{r['filename']}**")
                            if not r["supported"]:
                                st.error(r["error"])
                        with mc2:
                            if r["supported"]:
                                cur_sid = r["matched_shot_id"] or ""
                                d_idx = shot_select_opts.index(cur_sid) if cur_sid in shot_select_opts else 0
                                chosen_sid = st.selectbox(
                                    t("ext_import_col_target"),
                                    options=shot_select_opts,
                                    index=d_idx,
                                    format_func=lambda s: t("ext_import_skip_opt") if s == "" else s,
                                    label_visibility="collapsed",
                                    key=f"sel_ext_map_{project_id}_{r_idx}_{r['filename']}",
                                )
                                edited_mappings.append({**r, "matched_shot_id": chosen_sid or None})

                    if st.button(
                        t("btn_apply_external_import"),
                        type="primary",
                        disabled=is_job_running,
                        key=f"btn_ext_apply_{project_id}",
                    ):
                        to_apply = [m for m in edited_mappings if m.get("matched_shot_id")]
                        if not to_apply:
                            st.warning(t("ext_import_nothing_selected"))
                        else:
                            with st.spinner(t("spinner_applying_external")):
                                app_res = apply_bulk_external_import(p_dir, manifest, to_apply, rebuild=True)
                            if app_res["applied_shots"]:
                                st.success(t("ext_import_applied_ok", count=len(app_res["applied_shots"])))
                                time.sleep(0.4)
                                st.rerun()

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

        ai_kw_notice = st.session_state.pop(f"ai_kw_last_notice_{project_id}", None)
        if isinstance(ai_kw_notice, dict) and ai_kw_notice.get("text"):
            if ai_kw_notice.get("level") == "warning":
                st.warning(ai_kw_notice["text"])
            else:
                st.success(ai_kw_notice["text"])

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
                    msg_kw_ok = t(
                        "ai_kw_updated_success",
                        updated=kw_res["updated"],
                        stale=kw_res["stale_marked"],
                    )
                    if kw_res.get("fallback_used") or kw_res.get("warnings"):
                        warn_txt = " | ".join(str(w) for w in (kw_res.get("warnings") or []) if w)
                        full_warn = f"{msg_kw_ok} — {warn_txt}" if warn_txt else msg_kw_ok
                        st.session_state[f"ai_kw_last_notice_{project_id}"] = {
                            "level": "warning",
                            "text": full_warn,
                        }
                    else:
                        st.session_state[f"ai_kw_last_notice_{project_id}"] = {
                            "level": "success",
                            "text": msg_kw_ok,
                        }
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

        # All shots flattened & mapped + single visual source-of-truth state (F14)
        from app.services.longform.visual.base import (
            invalidate_shot_thumbnail_cache,
            resolve_shot_visual_source,
        )

        all_shots_list = [s for ch in manifest.chapters for s in ch.shots]
        all_ch_map = {s.shot_id: ch for ch in manifest.chapters for s in ch.shots}
        proj_fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
        vis_state_map: Dict[str, Dict[str, Any]] = {}
        needs_update_count = 0
        for _s in all_shots_list:
            _vst = resolve_shot_visual_source(p_dir, _s, fps=proj_fps)
            if _vst["needs_rebuild"] and getattr(_s, "status", None) != ItemStatus.stale:
                _s.status = ItemStatus.stale
                _vst["is_needs_update"] = True
            vis_state_map[_s.shot_id] = _vst
            if _vst["is_needs_update"]:
                needs_update_count += 1

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

            # Filter Mode Chips with Exact Counts (Mockup 2 V3 + F14 Perlu diperbarui + D8 Belum ada visual)
            missing_visual_count = int(unready_vis_info.get("missing_count", 0))
            opt_all_f = f"{t('filter_all')} ({total_shots})"
            opt_no_vis_f = t("filter_no_visual", count=missing_visual_count)
            opt_upd_f = t("filter_needs_update", count=needs_update_count)
            opt_rev_f = f"{t('filter_needs_review')} ({needs_review_count})"
            opt_lock_f = f"{t('filter_locked')} ({locked_count})"
            opt_ph_f = f"{t('filter_placeholder')} ({placeholder_count})"
            filter_mode = st.pills(
                t("label_filter_status"),
                options=[opt_all_f, opt_no_vis_f, opt_upd_f, opt_rev_f, opt_lock_f, opt_ph_f],
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
                    if filter_mode == opt_no_vis_f and not vis_state_map.get(s.shot_id, {}).get("is_missing_visual"):
                        continue
                    if filter_mode == opt_upd_f and not vis_state_map.get(s.shot_id, {}).get("is_needs_update"):
                        continue
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
                            shot_vst = vis_state_map.get(shot.shot_id) or resolve_shot_visual_source(p_dir, shot, fps=proj_fps)

                            with st.container(border=True):
                                thumb_p = get_shot_thumbnail_path(p_dir, shot)
                                dur_s = f"{shot.duration_seconds:.1f}s" if shot.duration_seconds else ""
                                st.markdown(render_project_thumbnail_html(thumb_p, dur_s), unsafe_allow_html=True)

                                badges = []
                                if shot_vst.get("is_missing_visual"):
                                    badges.append(t("badge_no_visual"))
                                elif shot_vst.get("needs_rebuild"):
                                    badges.append(t("badge_needs_rebuild"))
                                elif shot_vst.get("not_built"):
                                    badges.append(t("badge_not_built"))
                                elif shot_vst.get("is_needs_update"):
                                    badges.append(t("badge_needs_update"))
                                if getattr(shot, "needs_review", False):
                                    badges.append(t("badge_needs_review"))
                                if getattr(shot, "locked", False):
                                    badges.append(t("badge_locked"))
                                if shot_vst.get("is_legacy_fake"):
                                    badges.append(t("badge_legacy_fake"))
                                badge_str = f" {' '.join(badges)}" if badges else ""

                                if shot_vst.get("is_missing_visual"):
                                    # D9.7: Single clean status without duplicate `None yet` code chip
                                    st.caption(f"**#{shot.shot_index}** ({dur_s}){badge_str}")
                                elif shot.actual_source == "neutral_backdrop":
                                    src = t("source_neutral_backdrop")
                                    st.caption(f"**#{shot.shot_index}** ({dur_s}) • `{src}`{badge_str}")
                                elif shot.actual_source == "external_image":
                                    src = t("source_external_image")
                                    st.caption(f"**#{shot.shot_index}** ({dur_s}) • `{src}`{badge_str}")
                                else:
                                    src = shot.actual_source or shot.requested_source or ("fake" if shot.is_fake else "")
                                    src_part = f" • `{src}`" if src else ""
                                    st.caption(f"**#{shot.shot_index}** ({dur_s}){src_part}{badge_str}")

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
                det_vst = vis_state_map.get(target_shot.shot_id) or resolve_shot_visual_source(p_dir, target_shot, fps=proj_fps)
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

                    # Video / Image Large Preview (same canonical source as gallery thumbnail, F14)
                    prev_p = det_vst.get("source_path")
                    with st.spinner(t("spinner_loading_video_preview")):
                        if prev_p and prev_p.is_file() and prev_p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                            st.video(str(prev_p))
                        elif prev_p and prev_p.is_file():
                            st.image(str(prev_p), use_container_width=True)
                        elif det_vst.get("is_missing_visual"):
                            st.warning(t("shot_no_visual_notice"))
                        else:
                            st.info(t("shot_clip_not_built"))

                    if det_vst.get("is_legacy_fake"):
                        st.warning(t("vis_legacy_fake_summary_warn", fake=1))
                    elif det_vst.get("needs_rebuild"):
                        st.warning(t("shot_needs_rebuild_notice"))
                    elif det_vst.get("not_built"):
                        st.info(t("shot_preview_from_asset_not_built"))

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
                    kw_reason_raw = getattr(target_shot, "search_query_reason", None)
                    kw_reason_disp = format_keyword_reason(kw_reason_raw, lang=get_ui_language())
                    proj_lang_vis = resolve_project_language(manifest)

                    if kw_en_val:
                        st.markdown(t("shot_keywords_en_search_label", kw=html.escape(kw_en_val)))
                        alt_qs = [
                            str(q).strip()
                            for q in (getattr(target_shot, "search_queries_en", None) or [])[1:]
                            if str(q or "").strip() and str(q).strip().lower() != kw_en_val.strip().lower()
                        ]
                        if alt_qs:
                            st.caption(t("shot_keywords_alts_caption", alts=" | ".join(alt_qs)))
                    elif not is_non_english_language(proj_lang_vis):
                        st.markdown(t("shot_keywords_en_search_label", kw=html.escape(short_q or "-")))
                    else:
                        st.markdown(
                            t(
                                "shot_keywords_lang_label",
                                proj_lang=html.escape(str(proj_lang_vis)),
                                kw=html.escape(short_q or "-"),
                            )
                        )

                    if kw_reason_disp:
                        st.markdown(
                            t(
                                "shot_keywords_source_with_reason",
                                src=html.escape(kw_source_disp),
                                reason=html.escape(kw_reason_disp),
                            )
                        )
                    else:
                        st.markdown(t("shot_keywords_source_label", src=html.escape(kw_source_disp)))

                    if kw_en_val and short_q and short_q.strip().lower() != kw_en_val.strip().lower():
                        if is_non_english_language(proj_lang_vis):
                            st.markdown(t("shot_keywords_orig_summary", orig=html.escape(short_q)))

                    eff_q_disp = getattr(target_shot, "effective_query", None)
                    if eff_q_disp:
                        st.markdown(t("shot_found_with_query", q=html.escape(eff_q_disp)))

                    # D9.1: Show concise no-visual reason & searchable tier diagnostics
                    no_vis_reason = getattr(target_shot, "no_visual_reason_summary", None)
                    search_attempts = getattr(target_shot, "search_attempts", None) or []
                    if det_vst.get("is_missing_visual") and no_vis_reason:
                        st.markdown(t("shot_no_visual_reason_label", summary=html.escape(no_vis_reason)))
                    if search_attempts:
                        with st.expander(t("shot_search_attempts_expander", count=len(search_attempts)), expanded=False):
                            attempt_lines = []
                            for att in search_attempts[:12]:
                                ap = str(att.get("provider") or "-")
                                aq = str(att.get("query") or "-")
                                ar = int(att.get("raw_count") or 0)
                                aa = int(att.get("accepted_count") or 0)
                                a_err = str(att.get("error") or "").strip()
                                a_rej = ", ".join(str(x) for x in (att.get("rejected_reasons") or [])[:2])
                                extra = a_err or a_rej
                                line = f"- [{ap}] \"{aq}\" -> raw={ar}, ok={aa}" + (f" ({extra})" if extra else "")
                                attempt_lines.append(line)
                            st.code("\n".join(attempt_lines), language="text")

                    # D9.3: Show asset attribution & mandatory attribution badge (CC BY / BY-SA / NASA)
                    attr_title = getattr(target_shot, "attribution_title", None)
                    attr_author = getattr(target_shot, "attribution_author", None)
                    attr_lic = getattr(target_shot, "attribution_license", None)
                    attr_url = getattr(target_shot, "attribution_url", None)
                    req_attr = bool(getattr(target_shot, "requires_attribution", False))
                    if attr_lic or attr_author or attr_title:
                        st.markdown(
                            t(
                                "shot_attribution_heading",
                                title=html.escape(str(attr_title or target_shot.shot_id)),
                                author=html.escape(str(attr_author or "-")),
                                license=html.escape(str(attr_lic or "-")),
                            )
                        )
                        if attr_url and str(attr_url).startswith("http"):
                            st.markdown(t("shot_attribution_link", url=attr_url))
                        if req_attr:
                            st.info(t("badge_requires_attribution", license=html.escape(str(attr_lic or "CC BY"))))

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

                    vision_ok = bool(vision_info.get("vision_supported"))
                    if st.button(
                        t("btn_recheck_ai_relevance"),
                        key=f"btn_recheck_ai_{project_id}_{target_shot.shot_id}",
                        disabled=(not vision_ok) or is_job_running,
                        help=t("ai_relevance_no_vision_notice") if not vision_ok else None,
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

                    if not vision_ok:
                        st.caption(t("ai_relevance_vision_hint_below"))
                        if st.button(
                            t("btn_open_llm_settings"),
                            key=f"btn_recheck_cfg_{project_id}_{target_shot.shot_id}",
                        ):
                            _open_mpt_settings_dialog("llm")

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
                        from app.services.longform.visual.base import canonical_candidate_dedup_key
                        from app.services.longform.visual.stock import filter_candidates_by_anchors_and_avoids

                        raw_cands_all = target_shot.candidates or []
                        raw_cands = filter_candidates_by_anchors_and_avoids(
                            raw_cands_all,
                            anchor_terms=list(getattr(manifest, "visual_anchor_terms", []) or []),
                            avoid_terms=list(getattr(manifest, "visual_avoid_terms", []) or []),
                            blocked_terms=list((preset_snap or {}).get("blocked_terms") or []),
                        )
                        cur_asset_file = Path(target_shot.asset_path).name if target_shot.asset_path else ""
                        active_cid = str(getattr(target_shot, "source_candidate_id", None) or "").strip()
                        active_attr_url = str(getattr(target_shot, "attribution_url", None) or "").strip()
                        active_canon = (
                            canonical_candidate_dedup_key(
                                {
                                    "candidate_id": active_cid,
                                    "provider": getattr(target_shot, "actual_source", "") or "",
                                    "source_url": active_attr_url,
                                    "url": active_attr_url,
                                }
                            )
                            if (active_cid or active_attr_url)
                            else ""
                        )

                        active_base_id = ""
                        m_act = re.search(r"(\d{4,})", f"{target_shot.asset_path or ''} {active_cid}")
                        if m_act:
                            active_base_id = m_act.group(1)

                        unique_cands = []
                        seen_ids = set()
                        seen_canon_keys = set()
                        for orig_idx, cand in enumerate(raw_cands):
                            cid = str(cand.get("candidate_id") or "").strip()
                            curl = str(cand.get("url") or cand.get("source_url") or "").strip()
                            c_src_url = str(cand.get("source_url") or "").strip()
                            canon_k = canonical_candidate_dedup_key(cand)

                            m_base = re.search(r"(\d{4,})", f"{cid} {curl}")
                            base_id = m_base.group(1) if m_base else (cid or curl)

                            if not base_id and not canon_k:
                                continue
                            if active_cid and cid and cid == active_cid:
                                continue
                            if active_attr_url and c_src_url and c_src_url == active_attr_url:
                                continue
                            if active_canon and canon_k and canon_k == active_canon:
                                continue
                            if active_base_id and base_id == active_base_id:
                                continue
                            if orig_idx == 0 and target_shot.asset_path and not getattr(target_shot, "locked", False):
                                continue
                            if cur_asset_file and base_id and base_id in cur_asset_file:
                                continue
                            if base_id and base_id in seen_ids:
                                continue
                            if canon_k and canon_k in seen_canon_keys:
                                continue

                            if base_id:
                                seen_ids.add(base_id)
                            if canon_k:
                                seen_canon_keys.add(canon_k)
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
                                            prov_txt = _format_visual_source_label(cand.get("provider") or "stock")
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
                                                            invalidate_shot_thumbnail_cache(p_dir, target_shot.shot_id)
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
                        p_opts = ["auto", "local"] + [
                            p for p in ("pixabay", "coverr", "pexels", "wikimedia", "nasa", "openverse")
                            if (p == "pixabay" and has_pixabay)
                            or (p == "coverr" and has_coverr)
                            or (p == "pexels" and has_pexels)
                            or p in ("wikimedia", "nasa", "openverse")
                        ]
                        sel_p = st.selectbox(
                            t("field_provider"),
                            p_opts,
                            format_func=lambda p: (
                                t("prov_search_all_active")
                                if p == "auto"
                                else (t("prov_local_opt") if p == "local" else _VISUAL_SOURCE_LABELS.get(p, p.capitalize()))
                            ),
                            key=f"sel_p_{project_id}_{target_shot.shot_id}",
                        )
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
                                        check_ai_relevance=bool(st.session_state.get(f"cb_ai_rel_{project_id}", False)),
                                    )
                                    invalidate_shot_thumbnail_cache(p_dir, target_shot.shot_id)
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
                                        invalidate_shot_thumbnail_cache(p_dir, target_shot.shot_id)
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
    from app.services.longform import platform_utils

    platform_utils.open_path_in_os(path, is_folder=is_folder)


# --- Step 4: Render Flow (Stage 7B.2 R1-R5) ---

def _render_missing_visual_confirmation(
    project_id: str,
    p_dir: Path,
    manifest: ProjectManifest,
    mode: str,
    job_step_name: str,
    cli_base_args: List[str],
) -> None:
    """Display confirmation prompt when Render or CapCut encounters missing/fake visuals (D8)."""
    confirm_key = f"{mode}_missing_confirm_{project_id}"
    if not st.session_state.get(confirm_key):
        return

    from app.services.longform.visual.base import inspect_project_missing_visuals

    info = inspect_project_missing_visuals(p_dir, manifest)
    if info["unready_count"] <= 0:
        st.session_state[confirm_key] = False
        return

    items = info["unready_shots"]
    bullets = [
        f"• #{e['global_idx']} ({e['shot_id']}) — {e['snippet']}"
        for e in items[:8]
    ]
    if len(items) > 8:
        bullets.append(f"• +{len(items) - 8} …")
    list_text = "\n".join(bullets)

    with st.container(border=True):
        st.warning(t("vis_confirm_missing_title", count=info["unready_count"]))
        st.markdown(t("vis_confirm_missing_body", count=info["unready_count"], list_text=list_text))
        col_c1, col_c2, col_c3 = st.columns([2, 2, 1])
        with col_c1:
            if st.button(
                t("btn_search_again_first"),
                type="primary",
                key=f"btn_{mode}_search_first_{project_id}",
                use_container_width=True,
            ):
                st.session_state[confirm_key] = False
                st.session_state[f"active_step_{project_id}"] = "visual"
                st.rerun()
        with col_c2:
            if st.button(
                t("btn_continue_neutral"),
                type="secondary",
                key=f"btn_{mode}_continue_neutral_{project_id}",
                use_container_width=True,
            ):
                st.session_state[confirm_key] = False
                _start_job_safe(
                    project_id,
                    job_step_name,
                    jobs.build_longform_cli_command(*cli_base_args, "--use-neutral-fallback"),
                    sleep_after=0.5,
                )
        with col_c3:
            if st.button(
                t("btn_cancel_confirm"),
                key=f"btn_{mode}_cancel_confirm_{project_id}",
                use_container_width=True,
            ):
                st.session_state[confirm_key] = False
                st.rerun()


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
    from app.services.longform.visual.base import inspect_project_missing_visuals

    job_state = jobs.get_job_state(project_id)
    is_job_running = is_job_running or bool(job_state and job_state.get("status") == "running")

    st_info = compute_project_step_status(p_dir)
    voice_done = (st_info["steps"]["voice"]["status"] == "selesai")
    visual_done = (st_info["steps"]["visual"]["status"] == "selesai")
    total_shots_r = sum(len(ch.shots) for ch in manifest.chapters)
    can_render = voice_done and total_shots_r > 0
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

        col_a1, col_a2, col_a3 = st.columns(3)
        with col_a1:
            if st.button(t("btn_open_system_player"), key=f"btn_play_sys_{project_id}", use_container_width=True):
                if final_vid_path:
                    _safe_open_path(final_vid_path)
        with col_a2:
            if st.button(t("btn_open_output_folder"), key=f"btn_open_exp_{project_id}", use_container_width=True):
                out_target = final_vid_path.parent if final_vid_path else (p_dir / "export")
                _safe_open_path(out_target, is_folder=True)
        with col_a3:
            if st.button(t("btn_open_credits_md"), key=f"btn_open_credits_done_{project_id}", use_container_width=True):
                from app.services.branding.lifecycle_ui import show_credits_dialog
                show_credits_dialog(p_dir, project_id)

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
                    vis_unready = inspect_project_missing_visuals(p_dir, manifest)
                    if vis_unready["unready_count"] > 0:
                        st.session_state[f"render_missing_confirm_{project_id}"] = True
                        st.rerun()
                    else:
                        _start_job_safe(
                            project_id,
                            "render_all",
                            jobs.build_longform_cli_command("render", "all", project_id),
                            sleep_after=0.5,
                        )

        _render_missing_visual_confirmation(
            project_id,
            p_dir,
            manifest,
            mode="render",
            job_step_name="render_all",
            cli_base_args=["render", "all", project_id],
        )

        if not can_render:
            if not voice_done and total_shots_r == 0:
                st.warning(t("render_prereq_both"))
            elif not voice_done:
                st.warning(t("render_prereq_voice"))
            elif total_shots_r == 0:
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

            col_a1, col_a2, col_a3, col_a4, col_a5 = st.columns(5)
            with col_a1:
                if st.button(t("btn_open_system_player"), key=f"btn_play_sys_{project_id}", use_container_width=True):
                    _safe_open_path(final_vid_path)
            with col_a2:
                if st.button(t("btn_open_output_folder"), key=f"btn_open_exp_{project_id}", use_container_width=True):
                    _safe_open_path(final_vid_path.parent, is_folder=True)
            with col_a3:
                if st.button(t("btn_open_credits_md"), key=f"btn_open_credits_rend_{project_id}", use_container_width=True):
                    from app.services.branding.lifecycle_ui import show_credits_dialog
                    show_credits_dialog(p_dir, project_id)
            with col_a4:
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
            with col_a5:
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
    can_export = voice_done and total_shots > 0 and not capcut_running

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

        from app.services.branding.lifecycle_ui import format_capcut_status_text
        cc_kind = doc_report.get("status_kind", "not_installed")
        status_capcut_label = format_capcut_status_text(doc_report)

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            render_metric_tile(
                t("capcut_metric_status"),
                status_capcut_label,
                status="success" if cc_kind == "tested" else "warning",
            )
        with col_d2:
            status_app_label = t("capcut_app_running") if capcut_running else t("capcut_app_closed_safe")
            render_metric_tile(t("capcut_metric_app_label"), status_app_label, status="warning" if capcut_running else "success")

        if cc_kind == "untested":
            st.warning(t("capcut_untested_warn", ver=installed_ver or "?", tested=", ".join(doc_report.get("tested_versions") or [tmpl_ver])))
        elif cc_kind == "not_installed":
            st.info(t("capcut_download_guide", url=doc_report.get("download_url") or "https://www.capcut.com/"))
        elif cc_kind == "unsupported_os":
            st.info(t("capcut_unsupported_os_note", reason=doc_report.get("unsupported_os_reason") or ""))

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
            from app.services.longform.visual.base import inspect_project_missing_visuals
            vis_unready = inspect_project_missing_visuals(p_dir, manifest)
            if vis_unready["unready_count"] > 0:
                st.session_state[f"capcut_missing_confirm_{project_id}"] = True
                st.rerun()
            else:
                _start_job_safe(
                    project_id,
                    "capcut_export",
                    jobs.build_longform_cli_command("capcut", "export", project_id, "--install"),
                    sleep_after=0.5,
                )

        _render_missing_visual_confirmation(
            project_id,
            p_dir,
            manifest,
            mode="capcut",
            job_step_name="capcut_export",
            cli_base_args=["capcut", "export", project_id, "--install"],
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
                from app.services.longform.visual.base import inspect_project_missing_visuals
                vis_unready = inspect_project_missing_visuals(p_dir, manifest)
                if vis_unready["unready_count"] > 0:
                    st.session_state[f"capcut_missing_confirm_{project_id}"] = True
                    st.rerun()
                else:
                    _start_job_safe(
                        project_id,
                        "capcut_export",
                        jobs.build_longform_cli_command("capcut", "export", project_id, "--install"),
                        sleep_after=0.5,
                    )

        _render_missing_visual_confirmation(
            project_id,
            p_dir,
            manifest,
            mode="capcut",
            job_step_name="capcut_export",
            cli_base_args=["capcut", "export", project_id, "--install"],
        )

        with st.container(border=True):
            col_cch, col_cc_btn = st.columns([3, 1])
            with col_cch:
                st.markdown(t("capcut_check_heading"))
            with col_cc_btn:
                if st.button(t("btn_recheck_capcut"), key=f"btn_recheck_capcut_{project_id}"):
                    st.rerun()

            from app.services.branding.lifecycle_ui import format_capcut_status_text
            cc_kind = doc_report.get("status_kind", "not_installed")

            col_d1, col_d2, col_d3 = st.columns(3)
            with col_d1:
                render_metric_tile(
                    t("capcut_metric_status"),
                    format_capcut_status_text(doc_report),
                    status="success" if cc_kind == "tested" else "warning",
                )
            with col_d2:
                render_metric_tile(t("capcut_metric_version_label"), str(installed_ver or "N/A"))
            with col_d3:
                render_metric_tile(t("capcut_metric_app_label"), t("capcut_app_running") if capcut_running else t("capcut_app_closed_safe"), status="warning" if capcut_running else "success")

            if draft_folder:
                st.caption(t("capcut_draft_folder_caption", folder=draft_folder))
            else:
                st.caption(t("capcut_draft_folder_missing"))

            if cc_kind == "untested":
                st.warning(t("capcut_untested_warn", ver=installed_ver or "?", tested=", ".join(doc_report.get("tested_versions") or [tmpl_ver])))
            elif cc_kind == "not_installed":
                st.info(t("capcut_download_guide", url=doc_report.get("download_url") or "https://www.capcut.com/"))
            elif cc_kind == "unsupported_os":
                st.info(t("capcut_unsupported_os_note", reason=doc_report.get("unsupported_os_reason") or ""))

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
