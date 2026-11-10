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
    format_preset_tree,
    get_all_preset_overrides,
    get_preset,
    get_preset_directories,
    get_preset_inheritance_chain,
    has_preset_override,
    list_presets,
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


# --- Project Discovery via Single Status Resolver (C2) ---

def get_project_summaries() -> List[Dict[str, Any]]:
    """Scan projects directory and build status summaries via compute_project_step_status (C2)."""
    root = get_projects_root()
    projects = []
    if not root.is_dir():
        return projects

    for p in sorted(root.iterdir()):
        if p.is_dir() and (p / "manifest.json").is_file():
            try:
                st_info = compute_project_step_status(p)
                projects.append(st_info)
            except Exception as exc:
                logger.warning(f"Failed to inspect project at {p}: {exc}")

    return projects


# --- Job Monitor Widget (C1) ---

@st.fragment
def render_job_monitor_widget(project_id: str):
    """Real-time detached background job monitor widget (C1).

    Auto-refreshes every 2 seconds while a job is actively running.
    Handles 'stopped' states cleanly with resumption guidance.
    """
    state = jobs.get_job_state(project_id)
    if not state:
        return

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
                    st.warning("Permintaan pembatalan telah dikirim.")
                    st.rerun()

        logs = jobs.get_job_logs(project_id, max_lines=30)
        with st.expander("📋 Log Keluaran Proses (30 baris terakhir)", expanded=True):
            if logs:
                st.code("\n".join(logs), language="text")
            else:
                st.caption("Menunggu keluaran log...")

        # Auto-refresh fragment while job is running (skip during pytest)
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            time.sleep(2)
            st.rerun(scope="fragment")

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


# --- Beranda (Home Page) Components (C4.1, C4.2) ---

def render_home_page():
    """Render Beranda: Project Cards & Quick Creation (C4.1, C4.2)."""
    st.header("🎬 Studio Video Panjang")
    st.caption("Buat video dokumenter berdurasi 10–20 menit secara otomatis dengan alur 5 langkah terstruktur.")

    # 1. New Project Quick Form (C4.2)
    with st.expander("➕ Buat Proyek Baru", expanded=st.session_state.get("show_new_proj_expander", False)):
        with st.form("form_create_project"):
            new_title = st.text_input("Judul Proyek (Wajib):", placeholder="Mis. Kisah Legenda Dodge Charger 1969")

            # Presets categorized for user simplicity
            niche_catalog = {
                "Automotive / Otomotif": [
                    ("American Muscle Cars & V8 Heritage", "automotive.muscle_car"),
                    ("Japanese Domestic Market (JDM) Icons", "automotive.jdm"),
                    ("Supercars & Exotic Engineering", "automotive.supercar"),
                    ("Classic Car Restoration & Preservation", "automotive.classic_restoration"),
                ],
                "Documentary / Dokumenter": [
                    ("Dokumenter Umum (Bahasa Indonesia)", "documentary_id"),
                    ("General Documentary (English)", "documentary_en"),
                ],
                "History / Sejarah": [
                    ("Military History & Battlefield Tactics", "history.military"),
                    ("Ancient Civilizations & Lost Empires", "history.ancient"),
                ],
                "True Crime / Misteri": [
                    ("Unsolved Mysteries & Cold Cases", "true_crime.unsolved"),
                ],
                "Technology / Teknologi": [
                    ("Artificial Intelligence & Deep Tech", "technology.ai_frontier"),
                ],
            }

            col_n, col_sub = st.columns(2)
            with col_n:
                selected_niche_cat = st.selectbox("Niche:", list(niche_catalog.keys()), index=0)
            with col_sub:
                sub_pairs = niche_catalog[selected_niche_cat]
                sub_idx = st.selectbox(
                    "Sub-Niche:",
                    range(len(sub_pairs)),
                    format_func=lambda i: sub_pairs[i][0],
                    index=0,
                )
                chosen_preset_id = sub_pairs[sub_idx][1]

            col_dur, col_lang = st.columns(2)
            with col_dur:
                new_duration = st.slider("Target Durasi (menit):", min_value=10, max_value=20, value=12)
            with col_lang:
                preset_def = get_preset(chosen_preset_id)
                default_lang = preset_def.language if preset_def else "en-US"
                lang_options = ["en-US (Bahasa Inggris)", "id-ID (Bahasa Indonesia)"]
                lang_idx = 0 if default_lang == "en-US" else 1
                new_lang = st.selectbox("Bahasa Narasi:", lang_options, index=lang_idx)

            # Hidden / advanced settings in expander (C4.2)
            with st.expander("Pengaturan Lanjutan (Opsional)", expanded=False):
                suggested_id = re.sub(r"[^a-zA-Z0-9_\-]+", "_", new_title.lower()).strip("_")
                custom_id = st.text_input("ID Proyek (Folder):", value=suggested_id, help="Biarkan default jika ingin dibuat otomatis dari judul.")
                new_focus = st.text_input("Sudut Fokus Sub-Niche:", placeholder="Mis. Mesin 426 Hemi dan dominasi NASCAR")
                aspect_ratio = st.selectbox("Rasio Aspek:", ["16:9 (Horizontal YouTube)", "9:16 (Vertikal Shorts/Reels)"], index=0)

            submitted = st.form_submit_button("🚀 Buat Proyek & Mulai", type="primary")
            if submitted:
                eff_id = custom_id.strip() if custom_id.strip() else re.sub(r"[^a-zA-Z0-9_\-]+", "_", new_title.lower()).strip("_")
                if not new_title.strip() or not eff_id:
                    st.error("Judul proyek wajib diisi.")
                elif get_project_dir(eff_id).exists():
                    st.error(f"Proyek dengan ID '{eff_id}' sudah ada. Ubah judul atau ID proyek.")
                else:
                    try:
                        create_project(
                            project_id=eff_id,
                            title=new_title.strip(),
                            preset_id=chosen_preset_id,
                            sub_niche_focus=new_focus.strip() if new_focus else None,
                            aspect_ratio="16:9" if "16:9" in aspect_ratio else "9:16",
                        )
                        st.query_params["project"] = eff_id
                        st.session_state["show_new_proj_expander"] = False
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Gagal membuat proyek: {exc}")

    st.write("---")

    # 2. Project Cards List (C4.1)
    projects = get_project_summaries()
    if not projects:
        st.info("Belum ada proyek video panjang. Silakan klik **➕ Buat Proyek Baru** di atas untuk memulai.")
        return

    st.subheader(f"Daftar Proyek ({len(projects)})")

    step_icons = {
        "selesai": "✅",
        "berjalan": "⏳",
        "terhenti": "⚠️",
        "perlu_diperbarui": "🔄",
        "belum": "⚪",
    }

    for p in projects:
        with st.container(border=True):
            col_info, col_steps, col_action = st.columns([3, 2, 1])

            with col_info:
                st.markdown(f"### {p['title']}")
                niche_text = p['preset_id']
                if p.get('sub_niche_focus'):
                    niche_text += f" › {p['sub_niche_focus']}"
                st.caption(f"📁 {niche_text}  |  ⏱️ ~{p['duration_minutes']} menit  |  <small style='color:gray;'>ID: {p['project_id']}</small>", unsafe_allow_html=True)
                st.markdown(f"👉 **Langkah Berikutnya:** `{p['next_step_name']}`")

            with col_steps:
                st.write("**Status Alur 5 Langkah:**")
                sc = p['steps']['script']
                vc = p['steps']['voice']
                vs = p['steps']['visual']
                rd = p['steps']['render']
                cc = p['steps']['capcut']
                st.write(
                    f"① {step_icons.get(sc['status'], '⚪')} Naskah &nbsp;&nbsp;"
                    f"② {step_icons.get(vc['status'], '⚪')} Suara &nbsp;&nbsp;"
                    f"③ {step_icons.get(vs['status'], '⚪')} Visual &nbsp;&nbsp;"
                    f"④ {step_icons.get(rd['status'], '⚪')} Render &nbsp;&nbsp;"
                    f"⑤ {step_icons.get(cc['status'], '⚪')} CapCut"
                )
                if p.get("preset_has_impactful_diff"):
                    st.caption("⚠️ *Pengaturan niche diperbarui sejak proyek dibuat*")

            with col_action:
                st.write("")
                if st.button("📂 Buka", key=f"btn_open_{p['project_id']}", type="primary"):
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
        meta_sub = f"Preset: `{manifest.preset_id}`"
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
    """Render Step 1: Naskah with single adaptive primary button and settings expander."""
    status = step_info["status"]
    st.subheader("① Naskah Cerita & Pembagian Bab")
    st.write(f"**Status:** `{step_info['label']}`")

    # LLM status & FAKE toggle
    llm_status = check_llm_status()
    use_fake = st.session_state.get(f"fake_toggle_{project_id}", False)

    # 1. Single Adaptive Primary Action Button (C4.3)
    if is_running:
        st.button("⏳ Sedang Menulis Naskah...", disabled=True, type="primary")
    elif status == "selesai":
        if st.button("Selesai ✔ — Lanjut ke Suara", type="primary", key=f"btn_next_voice_{project_id}"):
            st.session_state[f"active_step_{project_id}"] = "voice"
            st.rerun()
    elif status == "terhenti":
        btn_label = "▶️ Lanjutkan Naskah"
        if st.button(btn_label, type="primary", key=f"btn_resume_script_{project_id}"):
            provider_arg = ["--provider", "fake"] if use_fake else []
            if len(manifest.chapters) == 0:
                cmd = [sys.executable, "cli_longform.py", "script", "outline", project_id, "--topic", manifest.title] + provider_arg
                jobs.start_job(project_id, "script_outline", cmd)
            else:
                cmd = [sys.executable, "cli_longform.py", "script", "write", project_id] + provider_arg
                jobs.start_job(project_id, "script_generate", cmd)
            st.rerun()
    elif status == "perlu_diperbarui":
        btn_label = "🔄 Ulangi Naskah (Perlu Diperbarui)"
        if st.button(btn_label, type="primary", key=f"btn_stale_script_{project_id}"):
            provider_arg = ["--provider", "fake"] if use_fake else []
            cmd = [sys.executable, "cli_longform.py", "script", "write", project_id] + provider_arg
            jobs.start_job(project_id, "script_generate", cmd)
            st.rerun()
    else:  # belum
        btn_label = "🚀 Mulai Tulis Naskah"
        btn_disabled = bool(not llm_status["configured"] and not use_fake)
        if st.button(btn_label, type="primary", disabled=btn_disabled, key=f"btn_start_script_{project_id}"):
            provider_arg = ["--provider", "fake"] if use_fake else []
            if len(manifest.chapters) == 0:
                cmd = [sys.executable, "cli_longform.py", "script", "outline", project_id, "--topic", manifest.title] + provider_arg
                jobs.start_job(project_id, "script_outline", cmd)
            else:
                cmd = [sys.executable, "cli_longform.py", "script", "write", project_id] + provider_arg
                jobs.start_job(project_id, "script_generate", cmd)
            st.rerun()
        if btn_disabled:
            st.caption("⚠️ Kunci API LLM belum diatur di config. Aktifkan **Mode Pengujian FAKE** di Pengaturan Lanjutan bila ingin mencoba tanpa API.")

    # 2. Hidden Options in Expander (C4.3)
    with st.expander("⚙️ Pengaturan Lanjutan Naskah & Editor", expanded=False):
        col_cred, col_fk = st.columns([3, 1])
        with col_cred:
            if llm_status["configured"]:
                st.caption(f"🤖 Provider LLM: `{llm_status['provider']}` (Kunci: `{llm_status['masked_key']}`)")
            else:
                st.caption(f"⚠️ {llm_status['message']}")
        with col_fk:
            use_fake_val = st.checkbox("Mode Pengujian FAKE", value=use_fake, key=f"fake_toggle_{project_id}")
            if use_fake_val != use_fake:
                st.session_state[f"fake_toggle_{project_id}"] = use_fake_val
                st.rerun()

        if use_fake_val:
            st.error("⚠️ **MODE PENGUJIAN FAKE AKTIF**: Naskah dibuat secara tiruan tanpa memanggil API LLM nyata.")

        st.write("---")

        # Import Markdown option
        st.write("#### Impor Naskah Markdown Sendiri")
        st.caption("Punya naskah sendiri? Tempel naskah berformat `# Judul` dan `## Bab` di bawah.")
        raw_md = st.text_area("Isi Markdown Naskah:", height=180, key=f"txt_import_md_{project_id}")
        if st.button("Terapkan Naskah Markdown", key=f"btn_apply_md_{project_id}"):
            if not raw_md.strip():
                st.error("Isi naskah markdown tidak boleh kosong.")
            else:
                try:
                    stats = update_project_script(p_dir, raw_md)
                    st.success(f"Naskah berhasil diimpor! {stats['chapters_modified']} bab diperbarui.")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Gagal mengimpor naskah: {exc}")

        # Chapter Editor if chapters exist
        if manifest.chapters:
            st.write("---")
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
                        # Save back into markdown file and update project
                        ch_file = p_dir / "script" / f"chapter_{sel_ch_idx:02d}.md"
                        ch_file.parent.mkdir(parents=True, exist_ok=True)
                        ch_file.write_text(f"## {ch_obj.chapter_title}\n\n{new_ch_text.strip()}\n", encoding="utf-8")
                        st.success(f"Bab {sel_ch_idx} berhasil disimpan.")
                        time.sleep(0.5)
                        st.rerun()

        # Quality Inspection Report
        if manifest.chapters:
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
