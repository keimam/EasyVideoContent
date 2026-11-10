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

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from loguru import logger

from app.services.longform.hashing import compute_audio_group_hash
from app.services.longform.presets import (
    delete_custom_preset,
    format_preset_tree,
    get_all_preset_overrides,
    get_builtin_presets,
    get_custom_presets,
    get_preset,
    get_preset_directories,
    get_preset_inheritance_chain,
    has_preset_override,
    list_presets,
    save_custom_preset,
    suggest_niche_keywords,
)
from app.services.longform.quality_checker import ScriptQualityChecker
from app.services.longform.schema import ItemStatus, ProjectManifest
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

def _render_job_monitor_content(project_id: str, state: Dict[str, Any]):
    status = state.get("status")
    step = state.get("step", "")
    pid = state.get("pid")

    step_display_names = {
        "script_outline": "Penulisan Kerangka Naskah (Outline)",
        "script_generate": "Penulisan Naskah Lengkap",
        "audio_synthesize": "Sintesis Narasi Suara",
        "visual_generate": "Pencarian & Penyiapan Visual",
        "render_final": "Render Video Final",
        "capcut_export": "Ekspor Proyek CapCut",
    }
    step_display = step_display_names.get(step, step)

    if status == "running":
        st.info(f"⏳ **Pekerjaan Sedang Berjalan**: `{step_display}` (PID: {pid})")
        prog = jobs.get_project_progress(project_id, step)
        if prog["total"] > 0:
            st.progress(
                prog["percentage"] / 100.0,
                text=f"{prog['unit_label'].capitalize()} Selesai: {prog['completed']} / {prog['total']} ({prog['percentage']}%)",
            )

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
        logs = jobs.get_job_logs(project_id, max_lines=20)
        if logs:
            with st.expander("📋 Log Terakhir Sebelum Terhenti", expanded=False):
                st.code("\n".join(logs), language="text")

    elif status == "completed":
        st.success(f"✅ **Pekerjaan Selesai**: `{step_display}`")
    elif status == "cancelled":
        st.warning(f"🛑 **Pekerjaan Dibatalkan**: `{step_display}`")
    elif status == "failed":
        st.error(f"❌ **Pekerjaan Gagal**: `{step_display}` (Kode Keluar: {state.get('exit_code', 'unknown')})")
        logs = jobs.get_job_logs(project_id, max_lines=20)
        if logs:
            with st.expander("Detail Error Log", expanded=False):
                st.code("\n".join(logs), language="text")


@st.fragment(run_every=2)
def _active_job_monitor_fragment(project_id: str):
    """Auto-refreshing fragment monitor that polls job state while running (D1)."""
    state = jobs.get_job_state(project_id)
    if not state or state.get("status") != "running":
        # Job transitioned from running to completed / stopped / cancelled / failed.
        # Trigger full app rerun so stepper, buttons, and badges update!
        st.rerun(scope="app")
        return

    _render_job_monitor_content(project_id, state)


def render_job_monitor_widget(project_id: str):
    """Real-time background job monitor widget (D1)."""
    state = jobs.get_job_state(project_id)
    if not state:
        return

    status = state.get("status")
    if status == "running":
        _active_job_monitor_fragment(project_id)
    else:
        _render_job_monitor_content(project_id, state)


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
    """Render Beranda: Compact 2-column cards grid & quick creation (D5, D7, D8)."""
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

    # 2. New Project Form ala MoneyPrinterTurbo asli (shown when toggled) (D7, D8)
    if st.session_state.get("show_new_proj_form"):
        with st.container(border=True):
            st.subheader("✨ Buat Proyek Video Baru")
            with st.form("form_create_project"):
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
                    new_lang = st.selectbox("Bahasa Narasi:", ["id-ID (Bahasa Indonesia)", "en-US (Bahasa Inggris)"], index=0)

                # D7: Niche, Sub-niche, Fokus inside expander "Pengaturan lanjutan"
                with st.expander("⚙️ Pengaturan Lanjutan (Niche, Sub-Niche, Rasio)", expanded=False):
                    all_p = list_presets()

                    # Root niches: default to "umum" (D7)
                    root_niche_ids = ["umum"] + sorted([
                        pid for pid, p in all_p.items()
                        if pid != "umum" and (not p.extends or p.kind == "niche")
                    ])
                    root_labels = {pid: all_p[pid].name if pid in all_p else pid for pid in root_niche_ids}

                    col_n, col_sub = st.columns(2)
                    with col_n:
                        chosen_root = st.selectbox(
                            "Niche Utama:",
                            options=root_niche_ids,
                            format_func=lambda pid: root_labels.get(pid, pid),
                            index=0,
                            accept_new_options=True,
                            help="Pilih niche yang tersedia atau ketik nama niche baru (D8).",
                            key="sel_root_niche",
                        )

                    is_new_root = chosen_root not in all_p

                    with col_sub:
                        sub_candidates = [
                            pid for pid, p in all_p.items()
                            if p.extends == chosen_root
                        ]
                        sub_options = ["(Gunakan Niche Utama)"] + sub_candidates
                        sub_labels = {pid: all_p[pid].name for pid in sub_candidates}
                        sub_labels["(Gunakan Niche Utama)"] = "(Gunakan Niche Utama)"

                        chosen_sub = st.selectbox(
                            "Sub-Niche (Opsional):",
                            options=sub_options,
                            format_func=lambda pid: sub_labels.get(pid, pid),
                            index=0,
                            accept_new_options=True,
                            help="Pilih sub-niche atau ketik nama sub-niche baru (D8).",
                            key="sel_sub_niche",
                        )

                    is_new_sub = (chosen_sub != "(Gunakan Niche Utama)" and chosen_sub not in all_p)

                    custom_footage_kw = ""
                    custom_avoid_kw = ""
                    if is_new_root or is_new_sub:
                        target_new_label = chosen_sub if is_new_sub else chosen_root
                        st.info(f"✨ Menyiapkan niche kustom baru: **{target_new_label}**")
                        col_kw1, col_kw2 = st.columns(2)
                        with col_kw1:
                            custom_footage_kw = st.text_input(
                                "Kata Kunci Footage / Visual (pisahkan koma):",
                                placeholder="Mis. v8 engine, muscle car drag strip, burnout",
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
                    submitted = st.form_submit_button("🚀 Buat Proyek", type="primary")

                if submitted:
                    if not new_topic.strip():
                        st.error("Topik / Judul video wajib diisi.")
                    else:
                        eff_id = custom_id.strip() if custom_id.strip() else re.sub(r"[^a-zA-Z0-9_\-]+", "_", new_topic.lower()).strip("_")
                        if not eff_id:
                            eff_id = f"project_{int(time.time())}"

                        if get_project_dir(eff_id).exists():
                            st.error(f"Proyek dengan ID '{eff_id}' sudah ada. Ubah topik atau ID proyek.")
                        else:
                            try:
                                eff_lang_code = "en-US" if "en-US" in new_lang else "id-ID"
                                target_preset_id = chosen_root

                                if is_new_sub or is_new_root:
                                    target_label = chosen_sub if is_new_sub else chosen_root
                                    clean_slug = re.sub(r"[^a-zA-Z0-9_]+", "_", target_label.lower()).strip("_")
                                    parent_ext = chosen_root if is_new_sub else "umum"

                                    custom_pid = f"custom.{clean_slug}" if not is_new_sub else f"custom.{parent_ext}.{clean_slug}"

                                    suggested = suggest_niche_keywords(target_label, new_topic, use_llm=check_has_llm_key())
                                    if custom_footage_kw.strip():
                                        extra_vis = [k.strip() for k in custom_footage_kw.split(",") if k.strip()]
                                        suggested["visual_keywords"] = list(dict.fromkeys(suggested.get("visual_keywords", []) + extra_vis))
                                    if custom_avoid_kw.strip():
                                        extra_neg = [k.strip() for k in custom_avoid_kw.split(",") if k.strip()]
                                        suggested["negative_terms"] = list(dict.fromkeys(suggested.get("negative_terms", []) + extra_neg))

                                    new_preset_data = {
                                        "id": custom_pid,
                                        "name": target_label,
                                        "schema_version": "1.0.0",
                                        "kind": "sub_niche" if is_new_sub else "niche",
                                        "extends": parent_ext,
                                        "language": eff_lang_code,
                                        "+topic_keywords": suggested.get("topic_keywords", []),
                                        "+visual_keywords": suggested.get("visual_keywords", []),
                                        "+required_subject_terms": suggested.get("required_subject_terms", []),
                                        "+negative_terms": suggested.get("negative_terms", []),
                                        "+blocked_terms": suggested.get("blocked_terms", []),
                                        "capcut_text_size": 8,
                                    }
                                    save_custom_preset(new_preset_data, allow_overwrite=True)
                                    target_preset_id = custom_pid
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

    # Preset Impactful Difference Banner (C3)
    if st_info.get("preset_has_impactful_diff"):
        diff_count = len(st_info["preset_diff_details"])
        col_w, col_wb = st.columns([4, 1])
        with col_w:
            st.warning(f"⚠️ **Pengaturan Niche Diperbarui**: Terdapat {diff_count} perubahan pengaturan niche sejak proyek dibuat.")
            with st.expander("Lihat Rincian Perubahan Pengaturan", expanded=False):
                for df in st_info["preset_diff_details"]:
                    st.write(f"- **{df['field']}**: {df['description']}")
                    st.caption(f"  Proyek: `{df['snapshot_val']}`  vs  Preset Baru: `{df['current_val']}`")
        with col_wb:
            st.write("")
            if st.button("Pakai Pengaturan Terbaru", key=f"btn_refresh_preset_{project_id}"):
                refresh_project_preset(p_dir)
                st.success("Pengaturan proyek diperbarui ke versi terbaru.")
                time.sleep(0.5)
                st.rerun()

    # Always Visible Top Job Monitor (C1, C4.3)
    render_job_monitor_widget(project_id)

    st.write("---")

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
            render_script_step_flow(project_id, p_dir, manifest, active_info, is_job_running)
        elif active_step == "voice":
            render_voice_step_flow(project_id, p_dir, manifest, active_info, is_job_running)
        elif active_step == "visual":
            render_visual_step_flow(project_id, p_dir, manifest, active_info)
        elif active_step == "render":
            render_render_step_flow(project_id, p_dir, manifest, active_info)
        elif active_step == "capcut":
            render_capcut_step_flow(project_id, p_dir, manifest, active_info)


# --- Step 1: Naskah Flow (C4.3) ---

def render_script_step_flow(project_id: str, p_dir: Path, manifest: ProjectManifest, step_info: Dict[str, Any], is_running: bool):
    """Render Step 1: Naskah with two clear side-by-side cards (D3)."""
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

            if st.button("📥 Impor Naskah Ini", type="primary", key=f"btn_submit_import_{project_id}", disabled=is_running):
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

            ai_disabled = is_running or (not llm_ok and not use_fake)
            ai_btn_label = "⏳ Sedang Menulis..." if is_running else ("✨ Mulai Tulis Naskah AI" if len(manifest.chapters) == 0 else "✨ Buat Ulang Naskah AI")
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

def render_voice_step_flow(project_id: str, p_dir: Path, manifest: ProjectManifest, step_info: Dict[str, Any], is_running: bool):
    """Render Step 2: Suara with single adaptive primary button and voice options expander."""
    status = step_info["status"]
    st.subheader("② Suara & Sintesis Narasi Audio")
    st.write(f"**Status:** `{step_info['label']}`")

    voice_svc = VoiceService(p_dir)

    # 1. Single Adaptive Primary Action Button (C4.3)
    if is_running:
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
        btn_disabled = bool(len(manifest.chapters) == 0)
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


# --- Step 3: Visual Flow (C4.3) ---

def render_visual_step_flow(project_id: str, p_dir: Path, manifest: ProjectManifest, step_info: Dict[str, Any]):
    """Render Step 3: Visual (C4.3)."""
    st.subheader("③ Alokasi & Penyiapan Klip Visual")
    st.write(f"**Status Saat Ini:** `{step_info['label']}`")
    st.info(f"Proyek memiliki total **{step_info['total']}** shot terencana, dengan **{step_info['completed']}** klip visual valid.")

    st.button("Langkah Visual (Tersedia di Tahap 7B)", disabled=True, type="primary", key=f"btn_vis_disabled_{project_id}")
    st.caption("Pemilihan visual otomatis, pencarian stock video lokal/web, dan substitusi visual manual tersedia di Tahap 7B.")


# --- Step 4: Render Flow (C4.3) ---

def render_render_step_flow(project_id: str, p_dir: Path, manifest: ProjectManifest, step_info: Dict[str, Any]):
    """Render Step 4: Render Video Final (C4.3)."""
    st.subheader("④ Render Video Final + Subtitle + BGM")
    st.write(f"**Status Saat Ini:** `{step_info['label']}`")

    final_vid = step_info.get("final_video_file")
    if final_vid and (p_dir / final_vid).is_file():
        st.success(f"Video final siap: `{final_vid}`")
        st.video(str(p_dir / final_vid))

    st.button("Langkah Render (Tersedia di Tahap 7B)", disabled=True, type="primary", key=f"btn_rnd_disabled_{project_id}")
    st.caption("Penyusunan transisi bab, pembakaran subtitle ASS gaya documentary, ducking musik latar, dan rendering akhir tersedia di Tahap 7B.")


# --- Step 5: CapCut Flow (C4.3) ---

def render_capcut_step_flow(project_id: str, p_dir: Path, manifest: ProjectManifest, step_info: Dict[str, Any]):
    """Render Step 5: CapCut Export (C4.3)."""
    st.subheader("⑤ Ekspor Proyek CapCut Desktop")
    st.write(f"**Status Saat Ini:** `{step_info['label']}`")

    draft_info = step_info.get("draft_info")
    if draft_info:
        st.success(f"Draft CapCut Terdeteksi: **{draft_info['draft_name']}** (diekspor {draft_info['exported_at']})")
        st.caption(f"Lokasi: `{draft_info['draft_dir']}`")

    st.button("Langkah Ekspor CapCut (Tersedia di Tahap 7B)", disabled=True, type="primary", key=f"btn_cc_disabled_{project_id}")
    st.caption("Ekspor timeline CapCut draft_content.json otomatis ke folder draft pengguna tersedia di Tahap 7B.")


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
