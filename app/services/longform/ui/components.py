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
    create_project,
    get_project_dir,
    get_projects_root,
    load_manifest,
    refresh_project_preset,
    save_manifest,
    update_project_script,
)
from app.services.longform.tts_adapter import TTSAdapter, clean_narration_text
from app.services.longform.ui import jobs
from app.services.longform.ui.i18n import t
from app.services.longform.ui.security import check_llm_status, mask_api_key, safe_windows_path
from app.services.longform.voice_service import VoiceService, list_available_voices


# --- Project Discovery via Single Status Resolver (C2, D5) ---

def check_has_llm_key() -> bool:
    """Check if an actual LLM API key is configured in MoneyPrinterTurbo config (D3, E5)."""
    try:
        from app.config import config
        prov = (config.app.get("llm_provider") or "openai").lower().strip()
        key = (config.app.get(f"{prov}_api_key") or "").strip()
        if not key:
            return False
        if ("gemini" in prov or "google" in prov) and not key.startswith("AIza"):
            return False
        return True
    except Exception:
        return False


def get_project_summaries() -> List[Dict[str, Any]]:
    """Scan projects directory and build status summaries via compute_project_step_status (C2, D5)."""
    root = get_projects_root()
    projects = []
    if not root.is_dir():
        return projects

    for p in root.iterdir():
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

def _render_job_monitor_content(project_id: str, state: Dict[str, Any], current_step: Optional[str] = None):
    status = state.get("status")
    step = state.get("step", "")
    pid = state.get("pid")

    if state.get("dismissed"):
        return

    # Check if this job belongs to an earlier workflow step (W3)
    step_order = {
        "script_outline": 1, "script_generate": 1, "script": 1,
        "audio_synthesize": 2, "voice": 2,
        "visual_pipeline": 3, "visual_fetch": 3, "visual_build": 3, "visual_generate": 3, "visual": 3,
        "render_final": 4, "render": 4,
        "capcut_export": 5, "capcut": 5,
    }
    if current_step and status in ("completed", "cancelled", "failed", "stopped"):
        job_rank = step_order.get(step, 0)
        curr_rank = step_order.get(current_step, 0)
        if curr_rank > job_rank:
            # Job belongs to an earlier step and user is already on a subsequent step
            return

    step_display_names = {
        "script_outline": "Penulisan Kerangka Naskah (Outline)",
        "script_generate": "Penulisan Naskah Lengkap",
        "audio_synthesize": "Sintesis Narasi Suara",
        "visual_generate": "Pencarian & Penyiapan Visual",
        "visual_pipeline": "Pencarian & Penyusunan Visual",
        "visual_fetch": "Pencarian Footage Visual",
        "visual_build": "Penyusunan Klip Visual",
        "render_final": "Render Video Final",
        "capcut_export": "Ekspor Proyek CapCut",
    }
    step_display = step_display_names.get(step, step)

    if status == "running":
        st.info(f"⏳ **Pekerjaan Sedang Berjalan**: `{step_display}` (PID: {pid})")
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
                elapsed_str = f"⏱️ {m:02d}:{s:02d}"
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
            if st.button("🛑 Batalkan Pekerjaan", key=f"btn_cancel_{project_id}"):
                cancelled = jobs.cancel_job(project_id)
                if cancelled:
                    st.warning("Pekerjaan telah dibatalkan.")
                    st.rerun(scope="app")

        logs = jobs.get_job_logs(project_id, max_lines=30)
        with st.expander("📋 Log Keluaran Proses (30 baris terakhir)", expanded=True):
            if logs:
                st.code("\n".join(logs), language="text")
            else:
                st.caption("Menunggu keluaran log...")

    elif status == "stopped":
        st.warning(f"⚠️ **Pekerjaan Terhenti**: `{step_display}` (Pekerjaan belum selesai). Klik tombol **Lanjutkan** di bawah untuk meneruskan.")
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
            with st.expander("📋 Log Terakhir Sebelum Terhenti", expanded=False):
                st.code("\n".join(logs), language="text")

    elif status in ("completed", "cancelled", "failed"):
        col_msg, col_x = st.columns([12, 1])
        with col_msg:
            if status == "completed":
                st.success(f"✅ **Pekerjaan Selesai**: `{step_display}`")
            elif status == "cancelled":
                st.warning(f"🛑 **Pekerjaan Dibatalkan**: `{step_display}`")
            elif status == "failed":
                st.error(f"❌ **Pekerjaan Gagal**: `{step_display}` (Kode Keluar: {state.get('exit_code', 'unknown')})")
        with col_x:
            dismiss_key = f"btn_dismiss_job_{project_id}_{state.get('finished_at', state.get('started_at', ''))}"
            if st.button("✕", key=dismiss_key, help="Tutup pemberitahuan pekerjaan"):
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
            logs = jobs.get_job_logs(project_id, max_lines=20)
            if logs:
                with st.expander("Detail Error Log", expanded=False):
                    st.code("\n".join(logs), language="text")


@st.fragment(run_every=2)
def _active_job_monitor_fragment(project_id: str, current_step: Optional[str] = None):
    """Auto-refreshing fragment monitor that polls job state while running (D1)."""
    state = jobs.get_job_state(project_id)
    if not state or state.get("status") != "running":
        # Job transitioned from running to completed / stopped / cancelled / failed.
        # Trigger full app rerun so stepper, buttons, and badges update!
        st.rerun(scope="app")
        return

    _render_job_monitor_content(project_id, state, current_step=current_step)


def render_job_monitor_widget(project_id: str, current_step: Optional[str] = None):
    """Real-time background job monitor widget (D1, W3)."""
    state = jobs.get_job_state(project_id)
    if not state:
        return

    status = state.get("status")
    if status == "running":
        _active_job_monitor_fragment(project_id, current_step=current_step)
    else:
        _render_job_monitor_content(project_id, state, current_step=current_step)


# --- Beranda (Home Page) Components (D5) ---

def render_niche_management_section():
    """Render Section 'Kelola Niche': Built-in (view-only), Custom (edit, delete) (D8)."""
    with st.container(border=True):
        st.subheader("📁 Kelola Niche & Sub-Niche")
        tab_builtin, tab_custom = st.tabs(["🏛️ Niche Bawaan (Built-in)", "✨ Niche Kustom"])

        with tab_builtin:
            st.caption("Preset bawaan sistem bersifat baca-saja (read-only). Untuk memodifikasi, buat niche kustom baru.")
            builtin_presets = get_builtin_presets()
            for pid, p in sorted(builtin_presets.items()):
                with st.expander(f"📦 {p.name} (`{pid}`)", expanded=False):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"- **Jenis:** `{p.kind}`")
                        st.write(f"- **Bahasa:** `{p.language}`")
                        st.write(f"- **Kecepatan Narasi:** `{p.words_per_minute} WPM`")
                    with col2:
                        st.write(f"- **Turunan (Extends):** `{p.extends or '(Root niche)'}`")
                        st.write(f"- **Audience:** {p.audience or '-'}")
                        st.write(f"- **Gaya Narasi:** {p.narration_style or '-'}")

        with tab_custom:
            custom_presets = get_custom_presets()
            if not custom_presets:
                st.info("Belum ada niche kustom. Anda dapat membuat niche baru langsung melalui form pembuatan proyek di atas.")
            else:
                for pid, p in sorted(custom_presets.items()):
                    with st.expander(f"✨ {p.name} (`{pid}`)", expanded=False):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write(f"- **Jenis:** `{p.kind}`")
                            st.write(f"- **Bahasa:** `{p.language}`")
                            st.write(f"- **Turunan:** `{p.extends or '(Root niche)'}`")
                        with col2:
                            st.write(f"- **Topik Keywords:** {', '.join(getattr(p, 'topic_keywords', []) or []) or '-'}")
                            st.write(f"- **Visual Keywords:** {', '.join(getattr(p, 'visual_keywords', []) or []) or '-'}")

                        col_del, _ = st.columns([1, 4])
                        with col_del:
                            if st.button("🗑️ Hapus Niche", key=f"btn_del_preset_{pid}", type="secondary"):
                                try:
                                    delete_custom_preset(pid)
                                    st.success(f"Preset kustom '{pid}' berhasil dihapus.")
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Gagal menghapus preset: {exc}")


def render_home_page():
    """Render Beranda: Compact 2-column cards grid & quick creation (D5, D7, D8, G5)."""
    # G5: Return navigation link to MoneyPrinterTurbo Main
    from app.services.longform.ui.nav import render_mpt_entry
    render_mpt_entry()

    # 1. Top bar: Title + "📁 Kelola Niche" + "+ Proyek Baru" button at top right
    col_h, col_niche_mgmt, col_new = st.columns([3.5, 1.3, 1.2])
    with col_h:
        st.title("🎬 Studio Video Panjang")
        st.caption("Alur otomatis video dokumenter 10–20 menit: Naskah › Suara › Visual › Render › CapCut.")
    with col_niche_mgmt:
        st.write("")
        show_niche_mgmt = st.session_state.get("show_niche_mgmt", False)
        nm_label = "✕ Tutup Niche" if show_niche_mgmt else "📁 Kelola Niche"
        if st.button(nm_label, use_container_width=True, key="btn_toggle_niche_mgmt"):
            st.session_state["show_niche_mgmt"] = not show_niche_mgmt
            st.session_state["show_new_proj_form"] = False
            st.rerun()
    with col_new:
        st.write("")
        show_form = st.session_state.get("show_new_proj_form", False)
        btn_label = "✕ Tutup Form" if show_form else "➕ Proyek Baru"
        if st.button(btn_label, type="primary", use_container_width=True, key="btn_toggle_new_proj"):
            st.session_state["show_new_proj_form"] = not show_form
            st.session_state["show_niche_mgmt"] = False
            st.rerun()

    # Niche Management Section (shown when toggled) (D8)
    if st.session_state.get("show_niche_mgmt"):
        render_niche_management_section()

    # 2. New Project Form ala MoneyPrinterTurbo asli (shown when toggled) (D7, D8, G2, G3)
    if st.session_state.get("show_new_proj_form"):
        with st.container(border=True):
            st.subheader("✨ Buat Proyek Video Baru")
            # D7: Main fields outside expander
            new_topic = st.text_input(
                "Topik / Judul Video (Wajib):",
                placeholder="Mis. Kisah Legenda Dodge Charger 1969 atau Sejarah Perkembangan AI",
                key="input_new_topic",
            )

            col_dur, col_lang = st.columns(2)
            with col_dur:
                new_duration = st.slider("Target Durasi (menit):", min_value=10, max_value=20, value=12)
            with col_lang:
                new_lang = st.selectbox("Bahasa Narasi:", ["id-ID (Bahasa Indonesia)", "en-US (Bahasa Inggris)"], index=0, key="sel_new_proj_lang")

            eff_lang_code = "en-US" if "en-US" in new_lang else "id-ID"

            # D7, G3: Niche, Sub-niche, Fokus inside expander "Pengaturan lanjutan"
            with st.expander("⚙️ Pengaturan Lanjutan (Niche, Sub-Niche, Rasio)", expanded=False):
                root_ids, root_labels = get_available_niches_for_ui(eff_lang_code)

                col_n, col_sub = st.columns(2)
                with col_n:
                    chosen_root = st.selectbox(
                        "Niche Utama:",
                        options=root_ids,
                        format_func=lambda pid: root_labels.get(pid, pid),
                        index=0,
                        help="Pilih niche yang tersedia atau pilih '+ Niche baru…' untuk membuat baru.",
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
                        st.caption("✨ Membuat Niche Utama Baru")
                    col_nname, col_subname = st.columns(2)
                    with col_nname:
                        new_niche_name = st.text_input(
                            "Nama Niche Baru (Wajib):",
                            placeholder="Mis. Kuliner atau Wisata Alam",
                            key="input_new_niche_name",
                        )
                    with col_subname:
                        new_sub_name = st.text_input(
                            "Nama Sub-Niche (Opsional):",
                            placeholder="Mis. Street food Jepang",
                            key="input_new_sub_name",
                        )
                    col_kw1, col_kw2 = st.columns(2)
                    with col_kw1:
                        custom_footage_kw = st.text_input(
                            "Kata Kunci Footage / Visual (pisahkan koma):",
                            placeholder="Mis. ramen, yatai, tokyo street",
                            key="input_custom_footage_kw",
                        )
                    with col_kw2:
                        custom_avoid_kw = st.text_input(
                            "Hindari Kata / Filter Negatif (pisahkan koma):",
                            placeholder="Mis. cartoon, 3d render, watermark",
                            key="input_custom_avoid_kw",
                        )
                else:
                    sub_ids, sub_labels = get_sub_niches_for_ui(chosen_root, eff_lang_code)
                    with col_sub:
                        chosen_sub = st.selectbox(
                            "Sub-Niche (Opsional):",
                            options=sub_ids,
                            format_func=lambda pid: sub_labels.get(pid, pid),
                            index=0,
                            help="Pilih sub-niche atau pilih '+ Sub-niche baru…' untuk menambah sub-niche.",
                            key="sel_sub_niche",
                        )

                    is_creating_new_sub = (chosen_sub == "+ Sub-niche baru…")
                    if is_creating_new_sub:
                        new_sub_name = st.text_input(
                            "Nama Sub-Niche Baru (Wajib):",
                            placeholder="Mis. Street food Jepang",
                            key="input_new_sub_name",
                        )
                        col_kw1, col_kw2 = st.columns(2)
                        with col_kw1:
                            custom_footage_kw = st.text_input(
                                "Kata Kunci Footage / Visual (pisahkan koma):",
                                placeholder="Mis. ramen, yatai, tokyo street",
                                key="input_custom_footage_kw",
                            )
                        with col_kw2:
                            custom_avoid_kw = st.text_input(
                                "Hindari Kata / Filter Negatif (pisahkan koma):",
                                placeholder="Mis. cartoon, 3d render, watermark",
                                key="input_custom_avoid_kw",
                            )

                suggested_id = re.sub(r"[^a-zA-Z0-9_\-]+", "_", new_topic.lower()).strip("_")
                custom_id = st.text_input("ID Proyek (Folder):", value=suggested_id, help="Biarkan default jika ingin dibuat otomatis dari topik.")
                new_focus = st.text_input("Sudut Fokus Khusus:", placeholder="Mis. Mesin 426 Hemi dan dominasi NASCAR")
                aspect_ratio = st.selectbox("Rasio Aspek:", ["16:9 (Horizontal YouTube)", "9:16 (Vertikal Shorts/Reels)"], index=0)

            col_sub_btn, col_sub_cancel = st.columns([1, 4])
            with col_sub_btn:
                submitted = st.button("🚀 Buat Proyek", type="primary", key="btn_create_project_submit")

            if submitted:
                if not new_topic.strip():
                    st.error("Topik / Judul video wajib diisi.")
                elif is_creating_new_root and not new_niche_name.strip():
                    st.error("Nama Niche Baru wajib diisi saat memilih opsi '+ Niche baru…'.")
                elif is_creating_new_sub and not new_sub_name.strip():
                    st.error("Nama Sub-Niche Baru wajib diisi saat memilih opsi '+ Sub-niche baru…'.")
                else:
                    eff_id = custom_id.strip() if custom_id.strip() else re.sub(r"[^a-zA-Z0-9_\-]+", "_", new_topic.lower()).strip("_")
                    if not eff_id:
                        eff_id = f"project_{int(time.time())}"

                    if get_project_dir(eff_id).exists():
                        st.error(f"Proyek dengan ID '{eff_id}' sudah ada. Ubah topik atau ID proyek.")
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
                            st.error(f"Gagal membuat proyek: {exc}")

    st.write("---")

    # 3. Project Cards Grid (D5)
    projects = get_project_summaries()

    col_search, col_stats = st.columns([4, 1.2])
    with col_search:
        search_kw = st.text_input("🔍 Cari proyek...", placeholder="Ketik kata kunci judul atau ID proyek...", label_visibility="collapsed")
    with col_stats:
        st.caption(f"Total: **{len(projects)} Proyek**")

    if search_kw:
        kw = search_kw.strip().lower()
        projects = [p for p in projects if kw in p["title"].lower() or kw in p["project_id"].lower()]

    if not projects:
        st.info("Tidak ada proyek yang sesuai dengan pencarian." if search_kw else "Belum ada proyek video panjang. Klik **➕ Proyek Baru** di kanan atas.")
        return

    cols = st.columns(2)
    step_icons = {
        "selesai": "✔",
        "berjalan": "⏳",
        "terhenti": "⚠️",
        "perlu_diperbarui": "🔄",
        "belum": "○",
    }

    for idx, p in enumerate(projects):
        with cols[idx % 2]:
            with st.container(border=True):
                # 1-line title truncated with ellipsis
                raw_title = p["title"].strip()
                trunc_title = (raw_title[:32] + "…") if len(raw_title) > 34 else raw_title
                st.markdown(f"#### {trunc_title}", help=raw_title)

                # Niche & Duration
                niche_text = p["preset_id"]
                if p.get("sub_niche_focus"):
                    niche_text += f" › {p['sub_niche_focus']}"
                if len(niche_text) > 40:
                    niche_text = niche_text[:38] + "…"
                st.caption(f"📁 {niche_text}  •  ⏱️ ~{p['duration_minutes']} mnt")

                # 5 status icons in 1 row
                sc = p['steps']['script']['status']
                vc = p['steps']['voice']['status']
                vs = p['steps']['visual']['status']
                rd = p['steps']['render']['status']
                cc = p['steps']['capcut']['status']

                diff_icon = " ⚠️" if p.get("preset_has_impactful_diff") else ""
                st.markdown(
                    f"`{step_icons.get(sc, '○')}` Naskah &nbsp; "
                    f"`{step_icons.get(vc, '○')}` Suara &nbsp; "
                    f"`{step_icons.get(vs, '○')}` Visual &nbsp; "
                    f"`{step_icons.get(rd, '○')}` Render &nbsp; "
                    f"`{step_icons.get(cc, '○')}` CapCut{diff_icon}",
                    help="Status 5 langkah workflow proyek" if not diff_icon else "Pengaturan niche diperbarui sejak proyek dibuat"
                )

                # Clean Next Step line: NO double words!
                next_step = p.get("next_step", "Naskah")
                if next_step == "Semua Selesai ✔":
                    st.markdown("✅ **Semua Selesai**")
                else:
                    st.markdown(f"👉 **Berikutnya:** {next_step}")

                # Open button
                if st.button("📂 Buka", key=f"btn_open_{p['project_id']}", use_container_width=True):
                    st.query_params["project"] = p["project_id"]
                    st.rerun()


# --- Halaman Proyek (Project Detail Page) Components (C4.3, C4.4, C4.5) ---

def render_project_page(project_id: str):
    """Render Halaman Proyek with Stepper and Single Adaptive Action (C4.3)."""
    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir() or not (p_dir / "manifest.json").is_file():
        st.error(f"Proyek '{project_id}' tidak ditemukan di penyimpanan.")
        if st.button("← Kembali ke Beranda"):
            st.query_params.clear()
            st.rerun()
        return

    st_info = compute_project_step_status(p_dir)
    manifest = st_info["manifest"]

    # G5, H3, H5: Sidebar studio navigation and CSS
    from app.services.longform.ui.nav import render_mpt_entry
    render_mpt_entry(show_top_return=False)

    # Navigation header
    col_back, col_title = st.columns([1, 5])
    with col_back:
        if st.button("← Kembali ke Beranda", key=f"btn_back_{project_id}"):
            st.query_params.clear()
            st.session_state.pop(f"active_step_{project_id}", None)
            st.rerun()

    with col_title:
        st.title(f"🎬 {manifest.title}")
        meta_sub = ""
        if getattr(manifest, "topic", None):
            meta_sub += f"**Topik:** {manifest.topic} | "
        meta_sub += f"Preset: `{manifest.preset_id}`"
        if manifest.sub_niche_focus:
            meta_sub += f" › *{manifest.sub_niche_focus}*"
        meta_sub += f" | Rasio: `{manifest.aspect_ratio}` | Est. Durasi: ~{st_info['duration_minutes']} menit"
        st.caption(meta_sub)

    # Preset Impactful Difference Banner (C3, H1)
    if st_info.get("preset_has_impactful_diff"):
        diff_count = len(st_info["preset_diff_details"])
        col_w, col_wb = st.columns([4, 1])
        has_lang_mismatch = any(df.get("field") == "language" for df in st_info["preset_diff_details"])
        with col_w:
            st.warning(f"⚠️ **Pengaturan Niche Diperbarui**: Terdapat {diff_count} perubahan pengaturan niche sejak proyek dibuat.")
            with st.expander("Lihat Rincian Perubahan Pengaturan", expanded=False):
                for df in st_info["preset_diff_details"]:
                    st.write(f"- **{df['field']}**: {df['description']}")
                    st.caption(f"  Proyek: `{df['snapshot_val']}`  vs  Preset Baru: `{df['current_val']}`")
        with col_wb:
            st.write("")
            if has_lang_mismatch:
                st.caption("🔒 Perubahan bahasa narasi harus dilakukan secara terpisah.")
            elif st.button("Pakai Pengaturan Terbaru", key=f"btn_refresh_preset_{project_id}"):
                refresh_project_preset(p_dir)
                st.success("Pengaturan proyek diperbarui ke versi terbaru.")
                time.sleep(0.5)
                st.rerun()

    # Expander Relink / Ubah Preset Proyek (G2)
    with st.expander("⚙️ Ganti / Hubungkan Ulang (Relink) Niche Proyek", expanded=False):
        all_presets = list_presets()
        preset_options = sorted(all_presets.keys())
        current_idx = preset_options.index(manifest.preset_id) if manifest.preset_id in preset_options else 0
        new_selected_preset = st.selectbox(
            "Pilih Preset Baru untuk Proyek ini:",
            options=preset_options,
            format_func=lambda pid: f"{all_presets[pid].name} ({pid})",
            index=current_idx,
            key=f"sel_relink_preset_{project_id}",
        )
        col_rel, _ = st.columns([1, 4])
        with col_rel:
            if st.button("Terapkan Preset", key=f"btn_apply_relink_{project_id}"):
                if new_selected_preset != manifest.preset_id:
                    manifest.preset_id = new_selected_preset
                    save_manifest(manifest, p_dir)
                    refresh_project_preset(p_dir)
                    st.success(f"Preset proyek berhasil diubah ke '{new_selected_preset}'.")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.info("Preset yang dipilih sama dengan preset proyek saat ini.")

    # Stepper 5 Langkah (C4.3)
    step_keys = ["script", "voice", "visual", "render", "capcut"]
    step_labels = {
        "script": "① Naskah",
        "voice": "② Suara",
        "visual": "③ Visual",
        "render": "④ Render",
        "capcut": "⑤ CapCut",
    }
    step_status_badges = {
        "selesai": "✅ Selesai",
        "berjalan": "⏳ Berjalan",
        "terhenti": "⚠️ Terhenti",
        "perlu_diperbarui": "🔄 Perlu Diperbarui",
        "belum": "⚪ Belum",
    }

    # Auto-select first uncompleted step if not explicitly selected
    default_active_step = "script"
    for k in step_keys:
        if st_info["steps"][k]["status"] != "selesai":
            default_active_step = k
            break

    session_key = f"active_step_{project_id}"
    if session_key not in st.session_state:
        st.session_state[session_key] = default_active_step

    current_active_step = st.session_state[session_key]

    # Always Visible Top Job Monitor (C1, C4.3, W3)
    render_job_monitor_widget(project_id, current_step=current_active_step)

    # Automated Pipeline Section ("Otomatis Sampai Selesai" - A1, A2, A3)
    script_done = (st_info["steps"]["script"]["status"] == "selesai")
    all_done = (
        st_info["steps"]["script"]["status"] == "selesai"
        and st_info["steps"]["voice"]["status"] == "selesai"
        and st_info["steps"]["visual"]["status"] == "selesai"
        and st_info["steps"]["render"]["status"] == "selesai"
    )

    with st.container(border=True):
        col_a1, col_a2, col_a3 = st.columns([3, 1, 1])
        with col_a1:
            st.markdown("##### 🚀 Otomatis Sampai Selesai")
            st.caption("Jalankan langkah yang tersisa secara berurutan dalam satu antrean pekerjaan latar belakang (Suara → Visual → Render → CapCut).")
        with col_a2:
            auto_with_capcut = st.checkbox("Sertakan CapCut", value=False, key=f"auto_cc_{project_id}")
        with col_a3:
            auto_stop_review = st.checkbox("Berhenti jika Perlu Dicek", value=True, key=f"auto_rev_{project_id}")

        job_state = jobs.get_job_state(project_id)
        is_job_running = bool(job_state and job_state.get("status") == "running")
        is_review_paused = bool(
            job_state
            and job_state.get("status") == "stopped"
            and "Menunggu pengecekan visual" in (job_state.get("phase") or "")
        )

        if is_review_paused:
            st.warning("⚠️ **Pekerjaan Otomatis Terhenti**: Terdapat shot yang ditandai 'Perlu dicek' pada Langkah ③ Visual. Silakan tinjau visual di tab ③ atau klik tombol di bawah untuk melanjutkan render.")
            col_b1, _ = st.columns([2, 3])
            with col_b1:
                if st.button("▶️ Lanjutkan Otomatis (Abaikan Pengecekan)", key=f"btn_resume_auto_{project_id}", type="primary", use_container_width=True):
                    cmd = ["python", "cli_longform.py", "pipeline", "auto", project_id, "--skip-review-check"]
                    if auto_with_capcut:
                        cmd.append("--with-capcut")
                    jobs.start_job(project_id, "pipeline_auto", cmd)
                    time.sleep(0.5)
                    st.rerun()
        elif not script_done:
            st.caption("🔒 *Langkah ① Naskah harus selesai terlebih dahulu sebelum dapat menjalankan otomatisasi.*")
            st.button("🚀 Jalankan Otomatis Sampai Selesai", disabled=True, key=f"btn_auto_dis_{project_id}")
        elif all_done and not auto_with_capcut:
            st.button("✅ Semua Langkah Selesai ✔", disabled=True, key=f"btn_auto_done_{project_id}")
        else:
            if st.button("🚀 Jalankan Otomatis Sampai Selesai", disabled=is_job_running, type="primary", key=f"btn_start_auto_{project_id}"):
                cmd = ["python", "cli_longform.py", "pipeline", "auto", project_id]
                if auto_with_capcut:
                    cmd.append("--with-capcut")
                if auto_stop_review:
                    cmd.append("--stop-on-review")
                jobs.start_job(project_id, "pipeline_auto", cmd)
                time.sleep(0.5)
                st.rerun()

    st.write("---")

    # Render horizontal stepper pill selector
    stepper_cols = st.columns(5)
    for idx, k in enumerate(step_keys):
        st_val = st_info["steps"][k]["status"]
        badge = step_status_badges.get(st_val, "⚪ Belum")
        is_selected = (k == current_active_step)
        btn_type = "primary" if is_selected else "secondary"
        with stepper_cols[idx]:
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

    with st.container(border=True):
        if active_step == "script":
            render_script_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running)
        elif active_step == "voice":
            render_voice_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running)
        elif active_step == "visual":
            render_visual_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running)
        elif active_step == "render":
            render_render_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running)
        elif active_step == "capcut":
            render_capcut_step_flow(project_id, p_dir, manifest, active_info, is_job_running=is_job_running)


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
    st.subheader("① Naskah Cerita & Pembagian Bab")
    st.write(f"**Status:** `{step_info['label']}`")

    # If script is already finished: show chapter list and continue button
    if status == "selesai":
        st.success(f"✅ Naskah telah selesai ({len(manifest.chapters)} bab). Anda dapat melanjutkan ke langkah pembuatan suara.")
        if st.button("Lanjut ke Suara 👉", type="primary", key=f"btn_next_voice_{project_id}"):
            st.session_state[f"active_step_{project_id}"] = "voice"
            st.rerun()

        with st.expander("📖 Lihat Rincian Naskah Bab", expanded=False):
            for ch in manifest.chapters:
                st.markdown(f"**Bab {ch.chapter_index}: {ch.chapter_title}** ({len(ch.tts_groups)} grup narasi)")
                ch_txt = "\n\n".join(g.paragraph_text for g in ch.tts_groups if g.paragraph_text)
                st.text_area(f"Teks Bab {ch.chapter_index}:", value=ch_txt, height=120, disabled=True, key=f"view_ch_{project_id}_{ch.chapter_index}")

        st.write("---")
        st.markdown("##### Ingin mengganti atau mengimpor ulang naskah?")

    # Two clear side-by-side cards (D3)
    col_import, col_ai = st.columns(2)

    with col_import:
        with st.container(border=True):
            st.markdown("### 📄 Impor Naskah Sendiri")
            st.caption("Gunakan naskah milik Anda sendiri dari berkas markdown/teks atau tempel langsung.")

            uploaded_file = st.file_uploader(
                "Unggah berkas naskah (.md atau .txt):",
                type=["md", "txt"],
                key=f"file_uploader_{project_id}",
            )
            pasted_text = st.text_area(
                "Atau tempel teks naskah di sini:",
                height=130,
                placeholder="# Bab 1: Judul Bab\nParagraf narasi bab 1...\n\n# Bab 2: Judul Bab\nParagraf narasi bab 2...",
                key=f"pasted_script_{project_id}",
            )

            with st.expander("ℹ️ Contoh & Format Tag Naskah"):
                st.markdown(
                    "Format naskah menggunakan heading Markdown atau tag bab:\n\n"
                    "```markdown\n"
                    "# Bab 1: The Detroit Street Legend\n"
                    "The asphalt trembled under Detroit steel as the big-block V8 roared...\n\n"
                    "# Bab 2: Homologation Battles\n"
                    "In 1969, NASCAR homologation rules required 500 production cars...\n"
                    "```"
                )

            if st.button("📥 Impor Naskah Ini", type="primary", key=f"btn_submit_import_{project_id}", disabled=is_job_running):
                content = ""
                if uploaded_file is not None:
                    content = uploaded_file.getvalue().decode("utf-8", errors="replace")
                elif pasted_text and pasted_text.strip():
                    content = pasted_text.strip()

                if not content.strip():
                    st.error("Silakan unggah berkas atau tempel teks naskah terlebih dahulu.")
                else:
                    try:
                        stats = update_project_script(p_dir, content.strip())
                        st.success(f"Naskah berhasil diimpor! ({stats['chapters_added'] + stats['chapters_modified']} bab siap)")
                        time.sleep(0.5)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Gagal mengimpor naskah: {exc}")

    with col_ai:
        with st.container(border=True):
            st.markdown("### ✨ Tulis dengan AI")
            st.caption("AI menyusun kerangka dan menulis narasi dokumenter otomatis berdasarkan niche.")

            llm_status = check_llm_status()
            llm_ok = check_has_llm_key()
            use_fake = st.session_state.get(f"fake_toggle_{project_id}", False)

            if llm_status.get("is_invalid"):
                st.warning(f"⚠️ {llm_status['message']}")
            elif not llm_ok and not use_fake:
                st.warning("⚠️ Belum ada API key AI. Isi di pengaturan MoneyPrinterTurbo, atau impor naskah sendiri.")

            with st.expander("⚙️ Opsi Penulisan AI", expanded=False):
                custom_topic = st.text_input("Topik Tambahan / Fokus:", value=getattr(manifest, "topic", None) or manifest.sub_niche_focus or "", key=f"ai_topic_{project_id}")
                fake_val = st.checkbox("Gunakan Provider Simulasi / Pengujian (FAKE)", value=use_fake, key=f"fake_cb_{project_id}")
                if fake_val != use_fake:
                    st.session_state[f"fake_toggle_{project_id}"] = fake_val
                    st.rerun()

            if use_fake:
                st.error("⚠️ **MODE PENGUJIAN FAKE AKTIF**: Naskah dibuat secara tiruan tanpa memanggil API AI nyata.")

            ai_disabled = is_job_running or (not llm_ok and not use_fake)
            ai_btn_label = "⏳ Sedang Menulis..." if is_job_running else ("✨ Mulai Tulis Naskah AI" if len(manifest.chapters) == 0 else "✨ Buat Ulang Naskah AI")
            if st.button(ai_btn_label, type="primary" if not ai_disabled else "secondary", disabled=ai_disabled, key=f"btn_start_ai_{project_id}"):
                provider_arg = ["--provider", "fake"] if use_fake else []
                topic = custom_topic.strip() if custom_topic.strip() else (getattr(manifest, "topic", None) or manifest.title)
                cmd = ["script", "outline", project_id, "--topic", topic] + provider_arg
                jobs.start_job(project_id, "script_outline", cmd)
                st.rerun()

    # Chapter Editor & Quality Report if chapters exist
    if manifest.chapters:
        with st.expander("📝 Editor Per Bab & Kualitas Naskah", expanded=False):
            st.write("#### Editor Teks Bab")
            ch_indices = [ch.chapter_index for ch in manifest.chapters]
            sel_ch_idx = st.selectbox(
                "Pilih Bab untuk Disunting:",
                ch_indices,
                format_func=lambda idx: f"Bab {idx}: {next((c.chapter_title for c in manifest.chapters if c.chapter_index == idx), '')}",
                key=f"ch_ed_sel_{project_id}",
            )
            ch_obj = next((c for c in manifest.chapters if c.chapter_index == sel_ch_idx), None)
            if ch_obj:
                ch_paras = "\n\n".join(g.paragraph_text for g in ch_obj.tts_groups if g.paragraph_text)
                new_ch_text = st.text_area("Teks Narasi Bab Ini:", value=ch_paras, height=180, key=f"ch_txt_{project_id}_{sel_ch_idx}")
                if st.button("Simpan Perubahan Bab", key=f"btn_save_ch_{project_id}_{sel_ch_idx}"):
                    if new_ch_text.strip():
                        ch_file = p_dir / "script" / f"chapter_{sel_ch_idx:02d}.md"
                        ch_file.parent.mkdir(parents=True, exist_ok=True)
                        ch_file.write_text(f"## {ch_obj.chapter_title}\n\n{new_ch_text.strip()}\n", encoding="utf-8")
                        st.success(f"Bab {sel_ch_idx} berhasil disimpan.")
                        time.sleep(0.5)
                        st.rerun()

            st.write("---")
            st.write("#### Laporan Kualitas Naskah")
            try:
                preset_obj = get_preset(manifest.preset_id)
                checker = ScriptQualityChecker(preset_obj)
                full_script_text = "\n\n".join(
                    g.paragraph_text for ch in manifest.chapters for g in ch.tts_groups if g.paragraph_text
                )
                if full_script_text.strip():
                    q_report = checker.check_script(full_script_text, manifest.chapters)
                    col_q1, col_q2 = st.columns(2)
                    with col_q1:
                        st.metric("Skor Kualitas Hook", f"{q_report.get('hook_score', 85)}/100")
                        st.metric("Pelanggaran Hook", len(q_report.get("hook_violations", [])))
                    with col_q2:
                        st.metric("Frasa Klise Terdeteksi", len(q_report.get("cliche_matches", [])))
                        st.metric("Klaim Butuh Verifikasi [verify]", len(q_report.get("verify_claims", [])))
            except Exception as exc:
                st.caption(f"Laporan kualitas belum tersedia: {exc}")


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
    st.subheader("② Suara & Sintesis Narasi Audio")
    st.write(f"**Status:** `{step_info['label']}`")

    voice_svc = VoiceService(p_dir)

    # 1. Single Adaptive Primary Action Button (C4.3)
    if is_job_running:
        st.button("⏳ Sedang Menyintesis Audio...", disabled=True, type="primary")
    elif status == "selesai":
        if st.button("Selesai ✔ — Lanjut ke Visual", type="primary", key=f"btn_next_visual_{project_id}"):
            st.session_state[f"active_step_{project_id}"] = "visual"
            st.rerun()
    elif status == "terhenti":
        btn_label = "▶️ Lanjutkan Sintesis Audio"
        if st.button(btn_label, type="primary", key=f"btn_resume_voice_{project_id}"):
            cmd = [sys.executable, "cli_longform.py", "audio", "synth", project_id]
            jobs.start_job(project_id, "audio_synthesize", cmd)
            st.rerun()
    elif status == "perlu_diperbarui":
        btn_label = "🔄 Ulangi Sintesis (Perlu Diperbarui)"
        if st.button(btn_label, type="primary", key=f"btn_stale_voice_{project_id}"):
            cmd = [sys.executable, "cli_longform.py", "audio", "synth", project_id]
            jobs.start_job(project_id, "audio_synthesize", cmd)
            st.rerun()
    else:  # belum
        btn_label = "🎙️ Mulai Sintesis Audio"
        btn_disabled = bool(len(manifest.chapters) == 0) or is_job_running
        if st.button(btn_label, type="primary", disabled=btn_disabled, key=f"btn_start_voice_{project_id}"):
            cmd = [sys.executable, "cli_longform.py", "audio", "synth", project_id]
            jobs.start_job(project_id, "audio_synthesize", cmd)
            st.rerun()
        if btn_disabled:
            st.caption("⚠️ Belum ada naskah. Selesaikan langkah **① Naskah** terlebih dahulu.")

    # 2. Hidden Options in Expander (C4.3)
    with st.expander("⚙️ Pilihan Suara, Kecepatan & Pratinjau", expanded=False):
        col_vf, col_vr = st.columns([2, 1])
        with col_vf:
            lang_filter = st.selectbox("Filter Bahasa Suara:", ["en-US", "id-ID", "Semua"], index=0, key=f"vf_lang_{project_id}")
            filter_val = None if lang_filter == "Semua" else lang_filter
            voices = list_available_voices(filter_val)
            if not voices:
                voices = ["en-US-ChristopherNeural-Male", "en-US-GuyNeural-Male", "id-ID-ArdiNeural-Male"]

            curr_v = manifest.preset_snapshot.get("default_voice", voices[0])
            v_idx = voices.index(curr_v) if curr_v in voices else 0
            sel_voice = st.selectbox("Pilih Karakter Suara:", voices, index=v_idx, key=f"voice_picker_{project_id}")

        with col_vr:
            curr_rate = float(manifest.preset_snapshot.get("voice_rate", 1.0) or 1.0)
            sel_rate = st.slider("Kecepatan Bicara:", min_value=0.8, max_value=1.5, value=curr_rate, step=0.05, key=f"vr_slider_{project_id}")

        col_samp, col_save = st.columns(2)
        with col_samp:
            if st.button("🔊 Dengarkan Sampel Suara", key=f"btn_test_sample_{project_id}"):
                with st.spinner("Mensintesis sampel..."):
                    try:
                        with tempfile.TemporaryDirectory() as tmp_dir:
                            adapter = TTSAdapter(
                                provider="edge",
                                voice_name=sel_voice,
                                voice_rate=sel_rate,
                                output_dir=Path(tmp_dir),
                            )
                            from app.services.longform.schema import TtsGroup
                            dummy_grp = TtsGroup(group_index=1, paragraph_text="This is a narrator sample for the longform documentary.")
                            res = adapter.synthesize_group(dummy_grp, chapter_index=1, force_refresh=True)
                            st.audio(res.wav_file.read_bytes(), format="audio/wav")
                    except Exception as exc:
                        st.error(f"Gagal memutar sampel: {exc}")

        with col_save:
            if st.button("💾 Terapkan Suara ke Proyek", key=f"btn_apply_voice_{project_id}"):
                voice_svc.set_voice(sel_voice, sel_rate)
                st.success("Suara baru berhasil diterapkan ke proyek.")
                time.sleep(0.5)
                st.rerun()

        # Video Preview Section
        if manifest.chapters:
            st.write("---")
            st.write("#### Video Pratinjau Bab 1 (5 Detik)")
            if st.button("🎬 Buat Video Pratinjau Bab 1", key=f"btn_prev_video_{project_id}"):
                with st.spinner("Merender klip pratinjau MP4..."):
                    try:
                        wav_p, srt_p, mp4_p = voice_svc.generate_preview(1)
                        if mp4_p.is_file():
                            st.session_state[f"preview_mp4_{project_id}"] = str(mp4_p)
                            st.success("Pratinjau siap!")
                        else:
                            st.error("Gagal menghasilkan video pratinjau.")
                    except Exception as exc:
                        st.error(f"Error pratinjau: {exc}")

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

    st.subheader("③ Alokasi & Penyiapan Klip Visual")
    st.write(f"**Status Saat Ini:** `{step_info['label']}`")

    # --- V1: Summary Metrics ---
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

    used_sources_set = set()
    for ch in manifest.chapters:
        for s in ch.shots:
            has_asset = bool(s.asset_path and (p_dir / s.asset_path).is_file())
            has_clip = bool(s.rendered_clip and (p_dir / s.rendered_clip).is_file())
            if has_asset or has_clip:
                if getattr(s, "is_fake", False) or s.actual_source == "fake":
                    used_sources_set.add("Placeholder")
                else:
                    src = s.actual_source or s.requested_source or "stock"
                    used_sources_set.add(src.capitalize())
    sources_str = ", ".join(sorted(used_sources_set)) if used_sources_set else "Belum ada"

    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Klip Siap", f"{built_shots} / {total_shots}")
    col_m2.metric("Perlu Dicek", f"{needs_review_count}")
    col_m3.metric("Terkunci", f"{locked_count}")
    col_m4.metric("Sumber Terpakai", sources_str)

    # --- V1: Source Selection Checkboxes ---
    st.write("##### 🌐 Pilihan Sumber Footage")
    col_s1, col_s2, col_s3, col_s4, col_s5 = st.columns(5)

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

    ai_image_keys = config.app.get("openai_image_api_keys", []) or []
    has_ai_image = bool((isinstance(ai_image_keys, list) and any(k.strip() for k in ai_image_keys)) or (isinstance(ai_image_keys, str) and ai_image_keys.strip()))

    cb_pixabay = col_s1.checkbox(
        f"Pixabay ({mask_pixabay})" if has_pixabay else "Pixabay (Belum ada key)",
        value=has_pixabay,
        disabled=not has_pixabay,
        key=f"cb_prov_pixabay_{project_id}",
    )
    cb_coverr = col_s2.checkbox(
        f"Coverr ({mask_coverr})" if has_coverr else "Coverr (Belum ada key)",
        value=has_coverr,
        disabled=not has_coverr,
        key=f"cb_prov_coverr_{project_id}",
    )
    col_s3.checkbox(
        "Pexels (Belum ada key di config)",
        value=False,
        disabled=True,
        key=f"cb_prov_pexels_{project_id}",
    )
    cb_local = col_s4.checkbox(
        "File lokal (siap)",
        value=True,
        disabled=False,
        key=f"cb_prov_local_{project_id}",
    )
    col_s5.checkbox(
        "Gambar AI (Belum ada key OpenAI)",
        value=False,
        disabled=True,
        key=f"cb_prov_ai_{project_id}",
    )

    # Determine provider flag
    target_prov = None
    if cb_pixabay:
        target_prov = "pixabay"
    elif cb_coverr:
        target_prov = "coverr"
    elif cb_local:
        target_prov = "local"

    # --- V1: Single Adaptive Button & Job Monitor ---
    if is_job_running:
        st.button("⏳ Sedang Menyusun Visual...", disabled=True, type="primary", key=f"btn_running_vis_{project_id}")
        st.caption("Pekerjaan sedang berlangsung di latar belakang. Anda dapat memantau progres atau membatalkannya pada panel monitor di atas.")
    else:
        st_val = step_info["status"]
        if st_val == "selesai":
            if st.button("✅ Selesai ✔ — Lanjut ke Render", type="primary", key=f"btn_next_render_{project_id}"):
                st.session_state[f"active_step_{project_id}"] = "render"
                st.rerun()
        elif st_val == "terhenti":
            if st.button("▶️ Lanjutkan", type="primary", key=f"btn_resume_vis_{project_id}"):
                cmd = [sys.executable, "cli_longform.py", "visual", "pipeline", project_id, "--allow-fallback"]
                if target_prov:
                    cmd.extend(["--provider", target_prov])
                jobs.start_job(project_id, "visual_pipeline", cmd)
                st.rerun()
        elif st_val == "perlu_diperbarui":
            if st.button("🔄 Perbarui Klip yang Berubah", type="primary", key=f"btn_update_vis_{project_id}"):
                cmd = [sys.executable, "cli_longform.py", "visual", "pipeline", project_id, "--allow-fallback"]
                if target_prov:
                    cmd.extend(["--provider", target_prov])
                jobs.start_job(project_id, "visual_pipeline", cmd)
                st.rerun()
        else:  # belum
            # W1: Button is active if Script & Voice steps are completed, regardless of total_shots
            st_all = compute_project_step_status(p_dir)
            script_st = st_all["steps"]["script"]["status"]
            voice_st = st_all["steps"]["voice"]["status"]
            script_done = (script_st == "selesai")
            voice_done = (voice_st == "selesai")
            btn_disabled = not (script_done and voice_done)

            if btn_disabled:
                if not script_done and not voice_done:
                    st.warning(f"⚠️ Alokasi visual belum dapat dimulai. Langkah **① Naskah** (status: `{script_st}`) dan **② Suara** (status: `{voice_st}`) harus selesai terlebih dahulu.")
                elif not script_done:
                    st.warning(f"⚠️ Alokasi visual belum dapat dimulai. Selesaikan langkah **① Naskah** terlebih dahulu (status: `{script_st}`).")
                else:
                    st.warning(f"⚠️ Alokasi visual belum dapat dimulai. Selesaikan langkah **② Suara** terlebih dahulu (status: `{voice_st}`).")
            else:
                if total_shots == 0:
                    st.info("💡 Langkah **① Naskah** dan **② Suara** sudah siap. Klik tombol di bawah untuk membagi shot dan mencari footage otomatis.")

            if st.button("🎬 Cari & Susun Visual", type="primary", disabled=btn_disabled, key=f"btn_start_vis_{project_id}"):
                cmd = [sys.executable, "cli_longform.py", "visual", "pipeline", project_id, "--allow-fallback"]
                if target_prov:
                    cmd.extend(["--provider", target_prov])
                jobs.start_job(project_id, "visual_pipeline", cmd)
                st.rerun()

    # --- V2: Galeri per bab / contact sheet interaktif ---
    if total_shots > 0:
        st.write("---")
        st.write("#### 🖼️ Galeri Visual & Contact Sheet Interaktif")

        filter_mode = st.radio(
            "Filter Cepat:",
            ["Semua", "Perlu dicek", "Terkunci", "Placeholder"],
            horizontal=True,
            key=f"vis_filter_{project_id}",
        )

        ch_tabs = st.tabs([f"Bab {ch.chapter_index}: {getattr(ch, 'chapter_title', f'Bab {ch.chapter_index}')[:25]}" for ch in manifest.chapters])
        for ch_idx, ch in enumerate(manifest.chapters):
            with ch_tabs[ch_idx]:
                if filter_mode == "Perlu dicek":
                    ch_shots = [s for s in ch.shots if getattr(s, "needs_review", False)]
                elif filter_mode == "Terkunci":
                    ch_shots = [s for s in ch.shots if getattr(s, "locked", False)]
                elif filter_mode == "Placeholder":
                    ch_shots = [s for s in ch.shots if (getattr(s, "is_fake", False) or getattr(s, "actual_source", "") == "fake")]
                else:
                    ch_shots = ch.shots

                if not ch_shots:
                    st.caption("Tidak ada shot yang cocok dengan filter ini.")
                else:
                    for r_idx in range(0, len(ch_shots), 4):
                        cols = st.columns(4)
                        for c_idx, shot in enumerate(ch_shots[r_idx:r_idx+4]):
                            with cols[c_idx]:
                                thumb_p = get_shot_thumbnail_path(p_dir, shot)
                                if thumb_p and thumb_p.is_file():
                                    st.image(str(thumb_p), use_container_width=True)
                                else:
                                    st.markdown("🖼️ *(Belum ada thumbnail)*")

                                badges = []
                                if getattr(shot, "needs_review", False):
                                    badges.append("⚠️ Perlu dicek")
                                if getattr(shot, "locked", False):
                                    badges.append("🔒 Terkunci")
                                if getattr(shot, "is_fake", False) or (getattr(shot, "actual_source", "") == "fake"):
                                    badges.append("🎨 Placeholder")
                                badge_str = f" {' '.join(badges)}" if badges else ""

                                src = shot.actual_source or shot.requested_source or ("fake" if shot.is_fake else "stock")
                                dur = f"{shot.duration_seconds:.1f}s" if shot.duration_seconds else ""
                                st.caption(f"**#{shot.shot_index}** ({dur}) • `{src}`{badge_str}")

                                narr = get_shot_narration(ch, shot)
                                snippet = (narr[:45] + "...") if len(narr) > 45 else narr
                                if snippet:
                                    st.caption(f'"{snippet}"')

                                is_sel = (st.session_state.get(f"selected_shot_{project_id}") == shot.shot_id)
                                btn_type = "primary" if is_sel else "secondary"
                                btn_lbl = "🔍 Terpilih" if is_sel else "🔍 Detail"
                                if st.button(btn_lbl, key=f"btn_sh_{project_id}_{shot.shot_id}", type=btn_type, use_container_width=True):
                                    st.session_state[f"selected_shot_{project_id}"] = shot.shot_id
                                    st.rerun()

    # --- V3: Detail Panel & Ganti Shot (Visual Replace) ---
    sel_id = st.session_state.get(f"selected_shot_{project_id}")
    if sel_id:
        target_shot = None
        target_chapter = None
        for ch in manifest.chapters:
            for s in ch.shots:
                if s.shot_id == sel_id:
                    target_shot = s
                    target_chapter = ch
                    break
            if target_shot:
                break

        if target_shot and target_chapter:
            st.write("---")
            with st.container(border=True):
                col_dh, col_close = st.columns([4, 1])
                with col_dh:
                    st.subheader(f"🎬 Detail Shot: `{target_shot.shot_id}` (Bab {target_shot.chapter_index}, Shot #{target_shot.shot_index})")
                with col_close:
                    if st.button("✕ Tutup Detail", key=f"btn_close_detail_{project_id}_{target_shot.shot_id}"):
                        st.session_state[f"selected_shot_{project_id}"] = None
                        st.rerun()

                col_left, col_right = st.columns([1, 1])
                with col_left:
                    st.markdown("##### 📺 Preview & Informasi Shot")
                    clip_p = (p_dir / target_shot.rendered_clip) if target_shot.rendered_clip else None
                    asset_p = (p_dir / target_shot.asset_path) if target_shot.asset_path else None
                    with st.spinner("Memuat preview video..."):
                        if clip_p and clip_p.is_file():
                            st.video(str(clip_p))
                        elif asset_p and asset_p.is_file() and asset_p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
                            st.video(str(asset_p))
                        elif asset_p and asset_p.is_file():
                            st.image(str(asset_p), use_container_width=True)
                        else:
                            st.info("Klip belum dibuat. Jalankan penyusunan visual untuk merender klip.")

                    target_narr = get_shot_narration(target_chapter, target_shot)
                    st.markdown(f"**Teks Narasi:**\n> {target_narr or '-'}")

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

                    st.write(f"**Kata Kunci Pencarian:** `{short_q or '-'}`")
                    score_disp = f"{target_shot.relevance_score:.2f}" if target_shot.relevance_score is not None else "-"
                    st.write(f"**Skor Relevansi:** `{score_disp}`")

                    is_locked = getattr(target_shot, "locked", False)
                    lock_lbl = "🔓 Buka Kunci Shot" if is_locked else "🔒 Kunci Shot Ini"
                    if st.button(lock_lbl, key=f"btn_lock_toggle_{project_id}_{target_shot.shot_id}", use_container_width=True):
                        vis_svc = VisualService(p_dir)
                        vis_svc.set_lock(manifest, target_shot.shot_id, not is_locked)
                        save_manifest(manifest, p_dir)
                        st.success(f"Status shot diubah menjadi {'Terkunci' if not is_locked else 'Terbuka'}.")
                        time.sleep(0.3)
                        st.rerun()

                with col_right:
                    st.markdown("##### 🔄 Penggantian Shot (Visual Replace)")
                    tab_cands, tab_srch, tab_up = st.tabs(["🎯 Pilih Kandidat Lain", "🔍 Cari Kata Lain", "📤 Unggah File"])

                    with tab_cands:
                        raw_cands = target_shot.candidates or []
                        cur_asset_file = Path(target_shot.asset_path).name if target_shot.asset_path else ""

                        # Extract active asset base numeric ID if available (X2)
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

                            # Skip if matches current active asset base ID (X2)
                            if active_base_id and base_id == active_base_id:
                                continue
                            if orig_idx == 0 and target_shot.asset_path and not getattr(target_shot, "is_replaced", False):
                                continue
                            if cur_asset_file and base_id in cur_asset_file:
                                continue

                            # Skip duplicates across renditions/resolutions
                            if base_id in seen_ids:
                                continue

                            seen_ids.add(base_id)
                            unique_cands.append((orig_idx, cand))

                        if not unique_cands:
                            st.caption("Tidak ada kandidat alternatif lain yang tersimpan untuk shot ini.")
                        else:
                            st.write(f"Ditemukan **{len(unique_cands)}** kandidat alternatif:")

                            # Optional Candidate Preview Player (X2)
                            active_cp = st.session_state.get(f"preview_cand_url_{project_id}")
                            if active_cp:
                                with st.container(border=True):
                                    st.markdown("##### 🎬 Pratinjau Video Kandidat")
                                    with st.spinner("Memuat pratinjau kandidat..."):
                                        st.video(active_cp)
                                    if st.button("✕ Tutup Pratinjau", key=f"btn_close_cp_{project_id}"):
                                        st.session_state[f"preview_cand_url_{project_id}"] = None
                                        st.rerun()

                            # 3-column candidate grid (X2)
                            for r_i in range(0, len(unique_cands), 3):
                                cand_cols = st.columns(3)
                                for c_col_idx, (orig_idx, cand) in enumerate(unique_cands[r_i:r_i+3]):
                                    with cand_cols[c_col_idx]:
                                        with st.container(border=True):
                                            c_thumb = get_candidate_thumbnail_path(p_dir, cand)
                                            if c_thumb and c_thumb.is_file():
                                                st.image(str(c_thumb), use_container_width=True)
                                            else:
                                                st.markdown("🖼️ *(Thumbnail)*")

                                            score_txt = f"{cand.get('relevance_score', 0):.2f}"
                                            dur_txt = f"{cand.get('duration_seconds', 0):.1f}s"
                                            res_txt = f"{cand.get('width', 0)}x{cand.get('height', 0)}"
                                            prov_txt = cand.get('provider', 'stock').capitalize()
                                            st.caption(f"**{prov_txt}** • `{score_txt}`\n⏱️ {dur_txt} • 📐 {res_txt}")

                                            c_url = cand.get("url") or cand.get("source_url")
                                            btn_col1, btn_col2 = st.columns(2)
                                            with btn_col1:
                                                if c_url and st.button("▶ Preview", key=f"btn_prev_cand_{project_id}_{target_shot.shot_id}_{orig_idx}", use_container_width=True):
                                                    st.session_state[f"preview_cand_url_{project_id}"] = c_url
                                                    st.rerun()
                                            with btn_col2:
                                                if st.button("👉 Pakai ini", key=f"btn_apply_cand_{project_id}_{target_shot.shot_id}_{orig_idx}", type="primary", use_container_width=True):
                                                    with st.spinner("Menerapkan kandidat baru & menyusun klip..."):
                                                        try:
                                                            vis_svc = VisualService(p_dir)
                                                            vis_svc.replace_shot(manifest, target_shot.shot_id, candidate_index=orig_idx+1, rebuild=True)
                                                            target_shot.is_replaced = True
                                                            t_cache = p_dir / "assets" / "visual" / "thumbnails" / f"{target_shot.shot_id}.jpg"
                                                            t_cache.unlink(missing_ok=True)
                                                            save_manifest(manifest, p_dir)
                                                            get_shot_thumbnail_path(p_dir, target_shot)
                                                            st.session_state[f"preview_cand_url_{project_id}"] = None
                                                            st.success(f"Shot berhasil diganti ke kandidat #{orig_idx+1} dan terkunci.")
                                                            time.sleep(0.5)
                                                            st.rerun()
                                                        except Exception as exc:
                                                            st.error(f"Gagal mengganti kandidat: {exc}")

                    with tab_srch:
                        q_input = st.text_input(
                            "Kata Kunci Pencarian Baru:",
                            value=short_q,
                            key=f"txt_srch_{project_id}_{target_shot.shot_id}",
                        )
                        p_opts = ["pixabay", "coverr"] if has_coverr else ["pixabay"]
                        sel_p = st.selectbox("Provider:", p_opts, key=f"sel_p_{project_id}_{target_shot.shot_id}")
                        if st.button("🔍 Cari & Pasang Footage Baru", key=f"btn_srch_apply_{project_id}_{target_shot.shot_id}"):
                            with st.spinner("Mencari footage baru & menyusun klip..."):
                                try:
                                    vis_svc = VisualService(p_dir)
                                    vis_svc.replace_shot(
                                        manifest=manifest,
                                        shot_id=target_shot.shot_id,
                                        prompt=q_input,
                                        provider_name=sel_p,
                                        rebuild=True,
                                    )
                                    t_cache = p_dir / "assets" / "visual" / "thumbnails" / f"{target_shot.shot_id}.jpg"
                                    t_cache.unlink(missing_ok=True)
                                    save_manifest(manifest, p_dir)
                                    get_shot_thumbnail_path(p_dir, target_shot)
                                    st.success("Footage baru berhasil ditemukan, disusun, dan shot terkunci.")
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Gagal mencari footage baru: {exc}")

                    with tab_up:
                        uploaded_f = st.file_uploader(
                            "Unggah video (.mp4, .mov) atau gambar (.jpg, .png):",
                            type=["mp4", "mov", "mkv", "webm", "jpg", "jpeg", "png", "webp"],
                            key=f"up_f_{project_id}_{target_shot.shot_id}",
                        )
                        if uploaded_f:
                            if st.button("📥 Simpan ke Library & Pasang ke Shot", key=f"btn_up_apply_{project_id}_{target_shot.shot_id}"):
                                with st.spinner("Menyimpan ke library lokal & menyusun klip..."):
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
                                        st.success(f"Berkas '{uploaded_f.name}' berhasil ditambahkan ke library lokal dan dipasang ke shot (terkunci).")
                                        time.sleep(0.5)
                                        st.rerun()
                                    except Exception as exc:
                                        st.error(f"Gagal mengunggah berkas: {exc}")

    # --- V4: Cache & Pemeliharaan ---
    with st.expander("⚙️ Pengaturan Lanjutan & Pemeliharaan Cache", expanded=False):
        c_stat = get_cache_status()
        col_c1, col_c2, col_c3 = st.columns(3)
        col_c1.metric("Ukuran Cache Global", f"{c_stat['total_mb']} MB ({c_stat['total_gb']} GB)")
        col_c2.metric("Total Berkas Cache", f"{c_stat['total_files']} berkas")
        col_c3.metric("Berkas Terlindungi (Terkunci)", f"{c_stat['locked_files_count']} berkas")

        st.caption("Cache media global menyimpan footage yang telah diunduh untuk menghindari pengunduhan ulang. Berkas yang dipakai oleh shot berstatus terkunci di seluruh proyek dilindungi secara ketat dari pembersihan.")

        confirm_prune = st.checkbox("Saya yakin ingin membersihkan berkas cache lama yang tidak terkunci", key=f"confirm_prune_{project_id}")
        if st.button("🧹 Bersihkan Cache Lama (Prune)", disabled=not confirm_prune, key=f"btn_prune_cache_{project_id}"):
            with st.spinner("Membersihkan cache..."):
                try:
                    prune_res = prune_cache(older_than_days=14)
                    st.success(f"Pembersihan berhasil! {prune_res['files_removed']} berkas dihapus ({prune_res['mb_freed']} MB dibebaskan). {prune_res['locked_files_skipped']} berkas terlindungi.")
                    time.sleep(1)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Gagal membersihkan cache: {exc}")


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
):
    """Render Step 4: Render Video Final + Subtitle + BGM (Stage 7B.2 R1-R5)."""
    from app.services.longform.render.service import RenderService

    job_state = jobs.get_job_state(project_id)
    is_job_running = is_job_running or bool(job_state and job_state.get("status") == "running")

    st.subheader("④ Render Video Final + Subtitle + BGM")
    st.write(f"**Status Saat Ini:** `{step_info['label']}`")

    st_info = compute_project_step_status(p_dir)
    voice_done = (st_info["steps"]["voice"]["status"] == "selesai")
    visual_done = (st_info["steps"]["visual"]["status"] == "selesai")
    can_render = voice_done and visual_done

    # R1: Prerequisite Check & Messaging
    if not can_render:
        if not voice_done and not visual_done:
            st.warning("⚠️ **Langkah Prasyarat Belum Selesai**: Langkah ② Suara dan ③ Visual harus diselesaikan terlebih dahulu sebelum dapat merender video final.")
        elif not voice_done:
            st.warning("⚠️ **Langkah Prasyarat Belum Selesai**: Langkah ② Suara harus diselesaikan terlebih dahulu.")
        elif not visual_done:
            st.warning(f"⚠️ **Langkah Prasyarat Belum Selesai**: Langkah ③ Visual belum selesai ({st_info['steps']['visual']['completed']}/{st_info['steps']['visual']['total']} klip siap). Selesaikan penyusunan visual terlebih dahulu.")

    # R2: Render Settings Expander
    preset_snap = dict(manifest.preset_snapshot or {})
    sub_style = dict(preset_snap.get("subtitle_style", {}))

    with st.expander("⚙️ Pengaturan Render (Musik Latar & Subtitle)", expanded=False):
        st.markdown("##### 🎵 Musik Latar (BGM)")

        # Available BGM from resource/songs and project assets
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
            "Pilihan Sumber Musik Latar:",
            options=["preset", "none", "upload"],
            format_func=lambda x: {
                "preset": "Pilih dari Folder Musik (resource/songs)",
                "none": "Tanpa Musik (Hanya Narasi)",
                "upload": "Unggah Berkas Musik Sendiri",
            }[x],
            index=["preset", "none", "upload"].index(curr_bgm_choice),
            key=f"bgm_mode_{project_id}",
        )

        selected_bgm_name = preset_snap.get("default_bgm") or (available_bgm[0] if available_bgm else None)
        if bgm_mode == "preset":
            if available_bgm:
                selected_bgm_name = st.selectbox(
                    "Pilih Lagu Musik Latar:",
                    options=available_bgm,
                    index=available_bgm.index(selected_bgm_name) if selected_bgm_name in available_bgm else 0,
                    key=f"sel_bgm_{project_id}",
                )
            else:
                st.caption("Tidak ada berkas musik di `resource/songs`.")
        elif bgm_mode == "upload":
            up_bgm = st.file_uploader("Unggah berkas musik (.mp3, .wav, .m4a):", type=["mp3", "wav", "m4a"], key=f"up_bgm_{project_id}")
            if up_bgm:
                local_bgm_dir.mkdir(parents=True, exist_ok=True)
                target_bgm = local_bgm_dir / up_bgm.name
                target_bgm.write_bytes(up_bgm.getvalue())
                st.success(f"Musik '{up_bgm.name}' berhasil disimpan ke folder proyek.")
                selected_bgm_name = up_bgm.name
            elif local_bgm_files:
                st.info(f"Berkas musik proyek aktif: `{local_bgm_files[0]}`")
        else:
            selected_bgm_name = None

        col_v1, col_v2 = st.columns(2)
        with col_v1:
            bgm_volume = st.slider(
                "Volume Musik Latar:",
                min_value=0.0,
                max_value=1.0,
                value=float(preset_snap.get("bgm_volume", 0.20)),
                step=0.05,
                key=f"sl_bgm_vol_{project_id}",
            )
        with col_v2:
            st.caption("ℹ️ **Sidechain Ducking Otomatis**:")
            st.caption("Volume musik otomatis diturunkan (ducking) saat narator berbicara dan kembali normal saat hening (threshold: 0.03, ratio: 4.0, attack: 200ms, release: 1000ms).")

        st.markdown("---")
        st.markdown("##### 📝 Gaya Subtitle (Burned ASS)")
        col_st1, col_st2 = st.columns(2)
        with col_st1:
            curr_fsize = int(sub_style.get("font_size", 20))
            new_fsize = st.number_input(
                "Ukuran Font Subtitle (pt):",
                min_value=10,
                max_value=120,
                value=min(max(10, curr_fsize), 120),
                step=1,
                key=f"sub_fs_{project_id}",
            )
            st.caption(f"Font: `{sub_style.get('font_name', 'BeVietnamPro-Bold')}` | Warna: `{sub_style.get('primary_color', '&H00FFFFFF')}`")
        with col_st2:
            curr_marginv = int(sub_style.get("margin_v", 40))
            new_marginv = st.number_input(
                "Margin Vertikal Bawah (px):",
                min_value=10,
                max_value=300,
                value=min(max(10, curr_marginv), 300),
                step=5,
                key=f"sub_mv_{project_id}",
            )
            st.caption(f"Outline: `{sub_style.get('outline_color', '&H00000000')}` (Lebar: `{sub_style.get('outline_width', 2)}`)")

        col_save_cfg, col_prev_sub = st.columns([1, 1])
        with col_save_cfg:
            if st.button("💾 Simpan Pengaturan Render", disabled=is_job_running, key=f"btn_save_render_cfg_{project_id}"):
                sub_style["font_size"] = int(new_fsize)
                sub_style["margin_v"] = int(new_marginv)
                preset_snap["subtitle_style"] = sub_style
                preset_snap["bgm_volume"] = bgm_volume
                preset_snap["default_bgm"] = selected_bgm_name
                manifest.preset_snapshot = preset_snap
                save_manifest(manifest, p_dir)
                st.success("Pengaturan render berhasil disimpan. Hash bab diperbarui.")
                time.sleep(0.5)
                st.rerun()

        with col_prev_sub:
            if st.button("🖼️ Pratinjau Subtitle (Frame Preview)", disabled=(not visual_done or is_job_running), key=f"btn_prev_sub_{project_id}"):
                with st.spinner("Merender frame pratinjau subtitle..."):
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
                            st.warning("Tidak ada frame yang dihasilkan pada timestamp tersebut.")
                    except Exception as exc:
                        st.error(f"Gagal membuat pratinjau subtitle: {exc}")

        sub_prev_path = st.session_state.get(f"sub_prev_img_{project_id}")
        if sub_prev_path and Path(sub_prev_path).is_file():
            st.markdown("###### 🖼️ Pratinjau Tampilan Subtitle:")
            st.image(sub_prev_path, use_container_width=True)
            if st.button("✕ Tutup Pratinjau Subtitle", key=f"btn_close_sub_prev_{project_id}"):
                st.session_state.pop(f"sub_prev_img_{project_id}", None)
                st.rerun()

        # Render Ulang Paksa Semua Bab (Stage 7B.2 U)
        st.markdown("---")
        st.markdown("##### 🔄 Render Ulang Paksa Semua Bab")
        confirm_rerender_all = st.checkbox(
            "Saya paham bahwa merender ulang semua bab akan memproses kembali seluruh bab video dari awal.",
            key=f"chk_confirm_rerender_{project_id}",
        )
        if st.button(
            "🔄 Render Ulang Semua Bab",
            disabled=(not confirm_rerender_all or is_job_running),
            key=f"btn_rerender_all_exp_{project_id}",
        ):
            jobs.start_job(project_id, "render_all", ["python", "cli_longform.py", "render", "all", project_id, "--force"])
            time.sleep(0.5)
            st.rerun()

    # R3: Adaptive Action Button
    st_val = step_info["status"]

    col_btn1, col_btn2 = st.columns([2, 1])
    with col_btn1:
        if st_val == "belum":
            btn_label = "🎬 Render Video Final"
        elif st_val == "terhenti":
            btn_label = "▶️ Lanjutkan Render Video"
        elif st_val == "perlu_diperbarui":
            btn_label = "🔄 Render Ulang yang Berubah"
        else:
            btn_label = "✅ Selesai ✔ — Lanjut ke CapCut"

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
                jobs.start_job(project_id, "render_all", ["python", "cli_longform.py", "render", "all", project_id])
                time.sleep(0.5)
                st.rerun()

    with col_btn2:
        if st_val == "selesai":
            if st.button("🔄 Render Ulang (Force)", disabled=is_job_running, key=f"btn_force_render_{project_id}", use_container_width=True):
                jobs.start_job(project_id, "render_all", ["python", "cli_longform.py", "render", "all", project_id, "--force"])
                time.sleep(0.5)
                st.rerun()

    # R5: Results Display & Player
    final_vid = step_info.get("final_video_file") or manifest.final_video_file
    final_vid_path = (p_dir / final_vid) if final_vid else None

    if final_vid_path and final_vid_path.is_file():
        st.write("---")
        st.markdown("##### 📊 Hasil Video Final")

        # Load render_stats.json if present
        stats_file = p_dir / "export" / "render_stats.json"
        stats: Dict[str, Any] = {}
        if stats_file.is_file():
            try:
                stats = json.loads(stats_file.read_text(encoding="utf-8"))
            except Exception:
                pass

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

        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("Durasi Video", dur_fmt)
        col_m2.metric("Ukuran Berkas", f"{vid_size_mb:.1f} MB")
        col_m3.metric("Loudness (EBU R128)", f"{lufs_fmt} • {tp_fmt}")
        col_m4.metric("Jumlah Frame", f"{total_frames} frames ({fps:.0f} fps)")

        # Action Buttons: System player, Folder explorer, Download SRT & YT chapters
        col_a1, col_a2, col_a3, col_a4 = st.columns(4)
        with col_a1:
            if st.button("▶ Buka di Pemutar Sistem", key=f"btn_play_sys_{project_id}", use_container_width=True):
                _safe_open_path(final_vid_path)

        with col_a2:
            if st.button("📂 Buka Folder Export", key=f"btn_open_exp_{project_id}", use_container_width=True):
                _safe_open_path(final_vid_path.parent, is_folder=True)

        srt_path = p_dir / "export" / "subtitles" / "full_timeline.srt"
        with col_a3:
            if srt_path.is_file():
                srt_data = srt_path.read_text(encoding="utf-8", errors="replace")
                st.download_button(
                    "📥 Unduh Subtitle (.srt)",
                    data=srt_data,
                    file_name=f"{project_id}_subtitles.srt",
                    mime="text/plain",
                    use_container_width=True,
                    key=f"dl_srt_{project_id}",
                )
            else:
                st.button("📥 Subtitle Belum Ada", disabled=True, use_container_width=True, key=f"dl_srt_dis_{project_id}")

        yt_path = p_dir / "export" / "youtube_chapters.txt"
        with col_a4:
            if yt_path.is_file():
                yt_data = yt_path.read_text(encoding="utf-8", errors="replace")
                st.download_button(
                    "📥 Unduh Bab YouTube",
                    data=yt_data,
                    file_name=f"{project_id}_youtube_chapters.txt",
                    mime="text/plain",
                    use_container_width=True,
                    key=f"dl_yt_{project_id}",
                )
            else:
                st.button("📥 Bab YouTube Belum Ada", disabled=True, use_container_width=True, key=f"dl_yt_dis_{project_id}")

        # Browser player condition: < 200 MB
        if vid_size_mb < 200.0:
            st.video(str(final_vid_path.resolve()))
        else:
            st.info(f"ℹ️ Ukuran video final ({vid_size_mb:.1f} MB) melebihi batas pratinjau browser (200 MB). Gunakan tombol **'▶ Buka di Pemutar Sistem'** di atas untuk memutar video dengan performa penuh.")


# --- Step 5: CapCut Desktop Export Flow (Stage 7B.2 C1-C4) ---

def render_capcut_step_flow(
    project_id: str,
    p_dir: Path,
    manifest: ProjectManifest,
    step_info: Dict[str, Any],
    is_job_running: bool = False,
):
    """Render Step 5: CapCut Desktop Export & Calibration (Stage 7B.2 C1-C4)."""
    from app.services.longform.capcut.detector import get_capcut_doctor_report
    from app.services.longform.capcut.service import (
        build_calibration_draft,
        resolve_capcut_text_size,
        set_capcut_text_size,
    )

    job_state = jobs.get_job_state(project_id)
    is_job_running = is_job_running or bool(job_state and job_state.get("status") == "running")

    st.subheader("⑤ Ekspor Proyek CapCut Desktop")
    st.write(f"**Status Saat Ini:** `{step_info['label']}`")

    st_info = compute_project_step_status(p_dir)
    voice_done = (st_info["steps"]["voice"]["status"] == "selesai")
    visual_done = (st_info["steps"]["visual"]["status"] == "selesai")

    # C1: CapCut Doctor Diagnostic Panel
    doc_report = get_capcut_doctor_report()
    capcut_running = doc_report.get("is_running", False)
    capcut_installed = doc_report.get("installed", False)
    installed_ver = doc_report.get("installed_version")
    tmpl_ver = doc_report.get("template_version", "8.9.0")
    draft_folder = doc_report.get("draft_folder")

    with st.container(border=True):
        col_cch, col_cc_btn = st.columns([3, 1])
        with col_cch:
            st.markdown("##### 🩺 Cek Status CapCut Desktop")
        with col_cc_btn:
            if st.button("🔄 Cek ulang status CapCut", key=f"btn_recheck_capcut_{project_id}"):
                st.rerun()

        col_d1, col_d2, col_d3 = st.columns(3)
        col_d1.metric("Status CapCut", "✅ Terpasang" if capcut_installed else "❌ Belum Terpasang")
        col_d2.metric("Versi CapCut", f"{installed_ver or 'N/A'}")
        col_d3.metric("Status Aplikasi", "⚠️ Sedang Berjalan" if capcut_running else "✅ Tertutup (Aman)")

        if draft_folder:
            st.caption(f"📁 Folder Draft: `{draft_folder}`")
        else:
            st.caption("📁 Folder Draft: Tidak ditemukan secara otomatis di AppData Local.")

        if capcut_installed and installed_ver != tmpl_ver:
            st.warning(f"⚠️ Versi CapCut terpasang (`{installed_ver}`) berbeda dari template teruji (`{tmpl_ver}`). Kompatibilitas draft sebagian besar tetap terjaga.")

        if capcut_running:
            st.error("⚠️ **CapCut Desktop sedang aktif**. Tutup aplikasi CapCut sebelum mengekspor untuk mencegah korupsi berkas draft!")

    # C2: Text Size Calibration Expander
    current_sz = resolve_capcut_text_size(manifest)
    with st.expander("📏 Kalibrasi Ukuran Teks Subtitle CapCut", expanded=False):
        st.write(f"Ukuran teks subtitle saat ini: **{current_sz}** (Preset: `{manifest.preset_id}`)")
        st.caption("Draft kalibrasi menghasilkan kalimat uji ukuran 5 sampai 10 di CapCut Desktop. Buka draft di CapCut untuk menentukan ukuran yang paling proporsional tanpa terpotong untuk rasio proyek Anda.")

        col_cal1, col_cal2 = st.columns([1, 1])
        with col_cal1:
            if st.button("🛠️ Buat Draft Kalibrasi (MPT_CALIBRATE)", disabled=(capcut_running or not visual_done or is_job_running), key=f"btn_calib_{project_id}"):
                with st.spinner("Membuat draft kalibrasi di folder CapCut..."):
                    try:
                        calib_res = build_calibration_draft(project_id, install=True)
                        st.success(f"Draft kalibrasi '{calib_res['draft_name']}' berhasil dipasang ke CapCut! Buka CapCut untuk melihat perbandingannya.")
                    except Exception as exc:
                        st.error(f"Gagal membuat draft kalibrasi: {exc}")

        with col_cal2:
            new_sz_val = st.number_input(
                "Pilih Ukuran Teks (5 - 10):",
                min_value=5,
                max_value=10,
                value=int(current_sz),
                step=1,
                key=f"num_sz_{project_id}",
            )
            if st.button("💾 Simpan Ukuran Teks", disabled=is_job_running, key=f"btn_save_sz_{project_id}"):
                try:
                    set_capcut_text_size(manifest.preset_id, new_sz_val)
                    manifest.preset_snapshot["capcut_text_size"] = new_sz_val
                    save_manifest(manifest, p_dir)
                    st.success(f"Ukuran teks {new_sz_val} berhasil disimpan untuk preset '{manifest.preset_id}'.")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Gagal menyimpan ukuran teks: {exc}")

    # C4: Prerequisite Verification
    can_export = voice_done and visual_done and not capcut_running
    if not can_export:
        if not voice_done or not visual_done:
            st.warning("⚠️ **Langkah Prasyarat Belum Selesai**: Langkah ② Suara dan ③ Visual harus diselesaikan terlebih dahulu sebelum dapat mengekspor timeline CapCut Desktop.")
        elif capcut_running:
            st.warning("⚠️ **CapCut Sedang Aktif**: Harap tutup aplikasi CapCut Desktop terlebih dahulu sebelum menekan tombol ekspor.")

    # C3: Main Adaptive Export Button
    draft_info = step_info.get("draft_info") or find_latest_capcut_draft(p_dir, project_id)
    is_exported = bool(draft_info)
    btn_label = "🔄 Ekspor Ulang ke CapCut" if is_exported else "🚀 Ekspor ke CapCut (Install)"

    if st.button(
        btn_label,
        disabled=(not can_export or is_job_running),
        type="primary",
        key=f"btn_capcut_main_{project_id}",
    ):
        jobs.start_job(project_id, "capcut_export", ["python", "cli_longform.py", "capcut", "export", project_id, "--install"])
        time.sleep(0.5)
        st.rerun()

    # C3: Results Display
    if draft_info:
        st.write("---")
        st.markdown("##### 📦 Informasi Draft CapCut Terpasang")

        draft_name = draft_info["draft_name"]
        draft_dir_path = Path(draft_info["draft_dir"])

        col_i1, col_i2, col_i3 = st.columns(3)
        col_i1.metric("Nama Draft", draft_name)
        col_i2.metric("Waktu Ekspor", draft_info.get("exported_at") or "Baru saja")

        total_shots = sum(len(ch.shots) for ch in manifest.chapters)
        col_i3.metric("Total Klip Video", f"{total_shots} klip")

        st.caption(f"Lokasi Draft: `{draft_dir_path}`")

        col_b1, _ = st.columns([1, 2])
        with col_b1:
            if st.button("📂 Buka Folder Draft CapCut", key=f"btn_open_draft_{project_id}"):
                _safe_open_path(draft_dir_path, is_folder=True)

        # Export History List
        exp_root = p_dir / "export" / "capcut"
        prev_exports = []
        if exp_root.is_dir():
            for d in sorted(exp_root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if d.is_dir() and d.name.startswith("MPT_"):
                    mtime_str = datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    prev_exports.append({"Draft": d.name, "Waktu Pembuatan": mtime_str, "Path": str(d)})

        if prev_exports:
            with st.expander(f"📜 Riwayat Ekspor CapCut ({len(prev_exports)} draft)", expanded=False):
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
