"""Streamlit UI components for Long Video WebUI (B3, B4, B5, B6).

Thin wrapper around longform services and CLI jobs.
Single source of truth remains ProjectManifest and disk files.
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
from app.services.longform.planner import compute_plan
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


# --- Project Discovery & Status ---

def get_project_summaries() -> List[Dict[str, Any]]:
    """Scan projects directory and build status summaries across all pipeline stages (B3)."""
    root = get_projects_root()
    projects = []
    if not root.is_dir():
        return projects

    for p in sorted(root.iterdir()):
        if p.is_dir() and (p / "manifest.json").is_file():
            try:
                manifest = load_manifest(p)
                p_id = manifest.project_id

                # 1. Script status
                num_chapters = len(manifest.chapters)
                chapters_with_text = sum(
                    1 for ch in manifest.chapters
                    if any(g.paragraph_text and g.paragraph_text.strip() for g in ch.tts_groups)
                    or (p / "script" / f"chapter_{ch.chapter_index:02d}.md").is_file()
                )
                if num_chapters > 0 and chapters_with_text == num_chapters:
                    script_st = "✅ Selesai"
                elif chapters_with_text > 0:
                    script_st = "⚠️ Draf"
                else:
                    script_st = "⚪ Belum"

                # 2. Audio status
                total_groups = sum(len(ch.tts_groups) for ch in manifest.chapters)
                completed_groups = 0
                for ch in manifest.chapters:
                    for g in ch.tts_groups:
                        if g.status == ItemStatus.completed and g.audio and g.audio.file_path:
                            af = p / g.audio.file_path
                            if af.is_file() and af.stat().st_size > 44:
                                completed_groups += 1

                if total_groups > 0 and completed_groups == total_groups:
                    audio_st = "✅ Selesai"
                elif completed_groups > 0:
                    audio_st = f"⚠️ {completed_groups}/{total_groups}"
                else:
                    audio_st = "⚪ Belum"

                # 3. Visual status
                total_segs = sum(len(g.segments) for ch in manifest.chapters for g in ch.tts_groups)
                completed_segs = 0
                for ch in manifest.chapters:
                    for g in ch.tts_groups:
                        for s in g.segments:
                            if s.visual and s.visual.rendered_clip and (p / s.visual.rendered_clip).is_file():
                                completed_segs += 1

                if total_segs > 0 and completed_segs == total_segs:
                    visual_st = "✅ Selesai"
                elif completed_segs > 0:
                    visual_st = f"⚠️ {completed_segs}/{total_segs}"
                else:
                    visual_st = "⚪ Belum"

                # 4. Render status
                if manifest.final_video_file and (p / manifest.final_video_file).is_file():
                    render_st = "✅ Selesai"
                else:
                    render_st = "⚪ Belum"

                # 5. CapCut status
                capcut_draft_found = any(p.glob("capcut_export*")) or (p / "draft_content.json").is_file()
                capcut_st = "✅ Selesai" if capcut_draft_found else "⚪ Belum"

                # Preset change detection (B1)
                preset_changed = False
                try:
                    curr_preset = get_preset(manifest.preset_id)
                    preset_changed = (manifest.preset_snapshot != curr_preset.model_dump())
                except Exception:
                    pass

                projects.append({
                    "id": p_id,
                    "title": manifest.title,
                    "preset_id": manifest.preset_id,
                    "overall_status": manifest.status.value,
                    "script_status": script_st,
                    "audio_status": audio_st,
                    "visual_status": visual_st,
                    "render_status": render_st,
                    "capcut_status": capcut_st,
                    "preset_changed": preset_changed,
                    "manifest": manifest,
                    "dir": p,
                })
            except Exception as exc:
                logger.warning(f"Failed to inspect project at {p}: {exc}")

    return projects


# --- Job Monitor Widget ---

@st.fragment
def render_job_monitor_widget(project_id: str):
    """Real-time detached background job monitor widget (B2).

    Auto-refreshes every 2 seconds only while a job is actively running.
    """
    state = jobs.get_job_state(project_id)
    if not state:
        return

    status = state.get("status")
    step = state.get("step", "")
    pid = state.get("pid")

    if status == "running":
        st.info(f"⏳ **Job Sedang Berjalan**: `{step}` (PID: {pid})")
        prog = jobs.get_project_progress(project_id, step)
        if prog["total"] > 0:
            st.progress(prog["percentage"] / 100.0, text=f"{prog['unit_label'].capitalize()} Selesai: {prog['completed']} / {prog['total']} ({prog['percentage']}%)")

        col_cancel, col_space = st.columns([1, 4])
        with col_cancel:
            if st.button("🛑 Batalkan Job", key=f"btn_cancel_{project_id}"):
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

    elif status == "completed":
        st.success(f"✅ **Job Selesai**: `{step}` (Exit Code: {state.get('exit_code', 0)})")
    elif status == "cancelled":
        st.warning(f"🛑 **Job Dibatalkan**: `{step}`")
    elif status == "failed":
        st.error(f"❌ **Job Gagal**: `{step}` (Exit Code: {state.get('exit_code', 'unknown')})")
        logs = jobs.get_job_logs(project_id, max_lines=20)
        if logs:
            with st.expander("Detail Error Log", expanded=False):
                st.code("\n".join(logs), language="text")


# --- Projects Tab Components (B3) ---

def render_projects_tab(selected_project_id: Optional[str]) -> Optional[str]:
    """Render Tab 1: Project Management (B3)."""
    st.subheader(t("projects_list_title"))
    projects = get_project_summaries()

    if not projects:
        st.info("Belum ada proyek Long Video. Silakan buat proyek baru di bawah.")
    else:
        # Render project table
        table_rows = []
        for p in projects:
            alert = " ⚠️ [Preset Berubah]" if p["preset_changed"] else ""
            table_rows.append({
                "ID Proyek": p["id"] + alert,
                "Judul": p["title"],
                "Preset": p["preset_id"],
                "Naskah": p["script_status"],
                "Audio": p["audio_status"],
                "Visual": p["visual_status"],
                "Render": p["render_status"],
                "CapCut": p["capcut_status"],
            })
        st.dataframe(table_rows, use_container_width=True)

    st.divider()

    # Active Project Details & Selection
    proj_ids = [p["id"] for p in projects]
    default_idx = 0
    if selected_project_id in proj_ids:
        default_idx = proj_ids.index(selected_project_id)

    col_sel, col_blank = st.columns([2, 1])
    with col_sel:
        active_pid = st.selectbox(
            "Pilih Proyek Aktif untuk Dikelola:",
            proj_ids if proj_ids else ["(Tidak ada proyek)"],
            index=default_idx if proj_ids else 0,
            key="active_project_selector",
        )

    if active_pid and active_pid != "(Tidak ada proyek)":
        active_p = next((p for p in projects if p["id"] == active_pid), None)
        if active_p:
            m = active_p["manifest"]
            p_dir = active_p["dir"]

            # Preset change warning banner (B1)
            if active_p["preset_changed"]:
                st.warning(t("preset_changed_warning"))
                if st.button(t("btn_refresh_preset"), key=f"btn_refresh_{active_pid}"):
                    refresh_project_preset(p_dir)
                    st.success(t("refresh_preset_success"))
                    time.sleep(0.5)
                    st.rerun()

            # Overview details
            with st.expander(f"ℹ️ Ringkasan Detail Proyek: {m.project_id}", expanded=True):
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    st.write(f"**Judul**: {m.title}")
                    st.write(f"**Status Proyek**: `{m.status.value}`")
                    st.write(f"**Rasio Aspek**: {m.aspect_ratio}")
                with col_b:
                    st.write(f"**Preset Utama**: `{m.preset_id}`")
                    chain_str = " -> ".join(m.preset_inheritance_chain) if m.preset_inheritance_chain else m.preset_id
                    st.write(f"**Hierarki Preset**: `{chain_str}`")
                    st.write(f"**Fokus Sub-Niche**: {m.sub_niche_focus or '(tidak ditentukan)'}")
                with col_c:
                    st.write(f"**Jumlah Bab**: {len(m.chapters)}")
                    total_groups = sum(len(ch.tts_groups) for ch in m.chapters)
                    st.write(f"**Total Grup TTS**: {total_groups}")
                    st.write(f"**Folder**: `{p_dir.name}`")

    st.divider()

    # Create New Project Section (B3 cascading niche -> sub-niche)
    st.subheader(t("create_project_title"))
    with st.form("form_create_project"):
        new_title = st.text_input(t("project_title_label"), placeholder=t("project_title_placeholder"))

        # Auto slugify title to id
        suggested_id = re.sub(r"[^a-zA-Z0-9_\-]+", "_", new_title.lower()).strip("_")
        new_id = st.text_input(t("project_id_label"), value=suggested_id, help=t("project_id_help"))

        # Presets hierarchy
        all_presets = list_presets()
        root_niches = [pid for pid, p in all_presets.items() if not p.extends or p.kind == "niche"]
        
        col_niche, col_sub = st.columns(2)
        with col_niche:
            selected_niche = st.selectbox(t("niche_label"), root_niches, index=0)

        # Children of selected niche
        children = [pid for pid, p in all_presets.items() if p.extends == selected_niche]
        sub_options = [f"(Root: {selected_niche})"] + children
        with col_sub:
            selected_sub = st.selectbox(t("sub_niche_label"), sub_options, index=0)

        effective_preset_id = selected_niche if selected_sub.startswith("(Root:") else selected_sub

        col_foc, col_dur, col_lang = st.columns([2, 1, 1])
        with col_foc:
            new_focus = st.text_input(t("focus_label"), placeholder=t("focus_placeholder"))
        with col_dur:
            new_duration = st.slider(t("duration_label"), min_value=10, max_value=20, value=12)
        with col_lang:
            preset_obj = all_presets.get(effective_preset_id)
            preset_lang = preset_obj.language if preset_obj else "en-US"
            new_lang = st.selectbox(t("language_label"), ["en-US", "id-ID", "es-ES", "zh-CN"], index=0 if preset_lang == "en-US" else 1)

        submitted = st.form_submit_button(t("btn_create_project"))
        if submitted:
            if not new_title.strip() or not new_id.strip():
                st.error("Judul dan ID Proyek tidak boleh kosong.")
            elif get_project_dir(new_id).exists():
                st.error(f"Proyek dengan ID '{new_id}' sudah ada. Gunakan ID lain.")
            else:
                try:
                    create_project(
                        project_id=new_id.strip(),
                        title=new_title.strip(),
                        preset_id=effective_preset_id,
                        sub_niche_focus=new_focus.strip() if new_focus else None,
                    )
                    st.success(t("project_created_success", id=new_id))
                    time.sleep(0.5)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Gagal membuat proyek: {exc}")

    return active_pid if active_pid != "(Tidak ada proyek)" else None


# --- Script Tab Components (B4) ---

def render_script_tab(project_id: Optional[str]):
    """Render Tab 2: Script and Chapter Management (B4)."""
    if not project_id:
        st.warning("Silakan pilih atau buat proyek terlebih dahulu pada tab Manajemen Proyek.")
        return

    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir():
        st.error(f"Direktori proyek '{project_id}' tidak ditemukan.")
        return

    manifest = load_manifest(p_dir)
    preset = get_preset(manifest.preset_id)

    # Job monitor widget for active script operations
    render_job_monitor_widget(project_id)

    # LLM Credential check and FAKE toggle (A4, B4, B6)
    llm_status = check_llm_status()
    st.subheader(t("script_mode_label"))

    col_key_info, col_fake = st.columns([2, 1])
    with col_key_info:
        if llm_status["configured"]:
            st.success(f"🤖 **Provider LLM Aktif**: `{llm_status['provider']}` (Kunci: `{llm_status['masked_key']}`)")
        else:
            st.error(f"⚠️ {llm_status['message']}")
            st.caption("Konfigurasikan kunci API di `config.toml` atau gunakan toggle FAKE di samping untuk pengujian.")

    with col_fake:
        use_fake = st.checkbox(t("fake_toggle_label"), value=False, key=f"fake_toggle_{project_id}")

    if use_fake:
        st.error(t("fake_banner"))

    # Mode Selector
    mode = st.radio("Metode Pembuatan Naskah:", [t("mode_llm"), t("mode_import")], horizontal=True)

    if mode == t("mode_llm"):
        # Mode A: LLM outline + generate
        col_out, col_gen = st.columns(2)
        with col_out:
            st.write("### Langkah 1: Struktur Outline")
            st.write("Menghasilkan daftar kandidat judul dan ringkasan per bab.")
            btn_outline_disabled = bool(not llm_status["configured"] and not use_fake)
            if st.button(t("btn_generate_outline"), disabled=btn_outline_disabled, key=f"btn_out_{project_id}"):
                provider_arg = ["--provider", "fake"] if use_fake else []
                topic = manifest.title or "Video Topic"
                cmd = [sys.executable, "cli_longform.py", "script", "outline", project_id, "--topic", topic] + provider_arg
                jobs.start_job(project_id, "script_outline", cmd)
                st.rerun()

        with col_gen:
            st.write("### Langkah 2: Tulis Bab Naskah")
            st.write("Menulis seluruh bab narasi lengkap dengan tag visual dan verifikasi fakta.")
            btn_gen_disabled = bool(not llm_status["configured"] and not use_fake)
            if st.button(t("btn_generate_script"), disabled=btn_gen_disabled, key=f"btn_gen_{project_id}"):
                provider_arg = ["--provider", "fake"] if use_fake else []
                cmd = [sys.executable, "cli_longform.py", "script", "write", project_id] + provider_arg
                jobs.start_job(project_id, "script_generate", cmd)
                st.rerun()

    else:
        # Mode B: Import Markdown Script
        st.write("### Impor Naskah Markdown")
        st.caption("Format naskah menggunakan `# Judul`, `## Bab`, dan tag `[visual]`, `[motion]`, `[source]`, `[search]`, `[verify]`.")
        raw_md = st.text_area(t("script_import_label"), height=250, placeholder=t("script_import_placeholder"))
        if st.button(t("btn_import_script"), key=f"btn_import_{project_id}"):
            if not raw_md.strip():
                st.error("Isi naskah markdown tidak boleh kosong.")
            else:
                try:
                    stats = update_project_script(p_dir, raw_md)
                    st.success(f"Naskah berhasil diimpor! Bab dimodifikasi: {stats['chapters_modified']}, Grup TTS: {stats['groups_added'] + stats['groups_modified']}")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Gagal mengimpor naskah: {exc}")

    st.divider()

    # Chapter Editor (B4)
    if manifest.chapters:
        st.subheader(t("chapter_editor_title"))
        ch_indices = [ch.chapter_index for ch in manifest.chapters]
        selected_ch_idx = st.selectbox(
            t("select_chapter_label"),
            ch_indices,
            format_func=lambda idx: f"Bab {idx}: {next((c.chapter_title for c in manifest.chapters if c.chapter_index == idx), '')}",
            key=f"ch_editor_sel_{project_id}",
        )

        selected_ch = next((c for c in manifest.chapters if c.chapter_index == selected_ch_idx), None)
        if selected_ch:
            # Reconstruct chapter markdown or show existing
            ch_text_lines = []
            for g in selected_ch.tts_groups:
                seg_texts = []
                for s in g.segments:
                    tags = []
                    if s.visual and s.visual.prompt:
                        tags.append(f"[visual: {s.visual.prompt}]")
                    if s.visual and s.visual.motion:
                        tags.append(f"[motion: {s.visual.motion.value}]")
                    if s.visual and s.visual.search_keywords:
                        tags.append(f"[search: {s.visual.search_keywords}]")
                    if s.verify:
                        tags.append(f"[verify: {s.verify}]")
                    tag_str = " ".join(tags)
                    seg_texts.append(f"{s.narration_text} {tag_str}".strip())
                ch_text_lines.append(" ".join(seg_texts))

            existing_ch_content = "\n\n".join(ch_text_lines)
            edited_text = st.text_area(f"Konten Bab {selected_ch_idx}", value=existing_ch_content, height=220, key=f"ta_ch_{selected_ch_idx}")

            if st.button(t("btn_save_chapter"), key=f"btn_save_ch_{selected_ch_idx}"):
                # Build combined script and call update_project_script
                all_ch_texts = []
                all_ch_texts.append(f"# {manifest.title}\n")
                for c in manifest.chapters:
                    all_ch_texts.append(f"## Chapter {c.chapter_index}: {c.chapter_title}\n")
                    if c.chapter_index == selected_ch_idx:
                        all_ch_texts.append(edited_text.strip() + "\n")
                    else:
                        # existing chapter text
                        c_paras = []
                        for g in c.tts_groups:
                            c_paras.append(" ".join(s.narration_text for s in g.segments))
                        all_ch_texts.append("\n\n".join(c_paras) + "\n")

                full_script_md = "\n\n".join(all_ch_texts)
                try:
                    stats = update_project_script(p_dir, full_script_md)
                    st.success(f"Perubahan Bab {selected_ch_idx} berhasil disimpan!")
                    st.warning(f"Unit yang menjadi STALE: {stats['groups_modified']} grup audio, {stats['segments_modified']} segmen visual.")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Gagal memperbarui bab: {exc}")

    # Quality Report Display (B4)
    st.divider()
    st.subheader(t("quality_report_title"))
    try:
        checker = ScriptQualityChecker(p_dir)
        report = checker.inspect()
        
        col_q1, col_q2, col_q3 = st.columns(3)
        with col_q1:
            if report.get("is_fake"):
                st.error("🏷️ **Status**: PROVIDER FAKE (Uji Coba)")
            else:
                st.success("🏷️ **Status**: Produksi (Real LLM)")
            st.metric("Total Kata Naskah", report.get("duration_info", {}).get("total_words", 0))

        with col_q2:
            st.metric("Estimasi Durasi", f"{report.get('duration_info', {}).get('estimated_duration_seconds', 0)/60.0:.1f} Menit")
            st.metric("Frasa Klise", len(report.get("cliche_matches", [])))

        with col_q3:
            st.metric("Tag [verify]", len(report.get("verify_claims", [])))
            st.metric("Pengulangan N-Gram", len(report.get("all_ngrams", [])))

        if report.get("cliche_matches") or report.get("hook_violations"):
            with st.expander("🔍 Temuan Detail Kualitas Naskah", expanded=False):
                if report.get("cliche_matches"):
                    st.write("**Frasa Klise Terdeteksi**:")
                    for cl in report["cliche_matches"]:
                        st.write(f"- `{cl.get('matched_word')}` pada Bab {cl.get('chapter_index')}")
                if report.get("hook_violations"):
                    st.write("**Pelanggaran Aturan Hook**:")
                    for hv in report["hook_violations"]:
                        st.write(f"- {hv}")

    except Exception as exc:
        st.caption(f"Laporan kualitas belum tersedia: {exc}")


# --- Voice & Audio Tab Components (B5) ---

def render_voice_tab(project_id: Optional[str]):
    """Render Tab 3: Voice and Audio Management (B5)."""
    if not project_id:
        st.warning("Silakan pilih atau buat proyek terlebih dahulu pada tab Manajemen Proyek.")
        return

    p_dir = get_project_dir(project_id)
    if not p_dir.is_dir():
        st.error(f"Direktori proyek '{project_id}' tidak ditemukan.")
        return

    manifest = load_manifest(p_dir)
    voice_svc = VoiceService(p_dir)

    # Background Job Monitor for voice operations
    render_job_monitor_widget(project_id)

    st.subheader(t("voice_select_label"))

    # Filter voices
    col_filter, col_rate = st.columns([2, 1])
    with col_filter:
        lang_filter = st.selectbox(t("voice_language_filter"), ["en-US", "id-ID", "Semua"], index=0)
        filter_val = None if lang_filter == "Semua" else lang_filter
        voices = list_available_voices(filter_val)
        if not voices:
            voices = ["en-US-ChristopherNeural-Male", "en-US-GuyNeural-Male", "id-ID-ArdiNeural-Male"]

    current_voice = manifest.preset_snapshot.get("default_voice", voices[0])
    current_rate = float(manifest.preset_snapshot.get("voice_rate", 1.0))

    with col_rate:
        voice_rate = st.slider(t("voice_rate_label"), min_value=0.8, max_value=1.5, value=current_rate, step=0.05)

    voice_idx = voices.index(current_voice) if current_voice in voices else 0
    selected_voice = st.selectbox("Daftar Suara:", voices, index=voice_idx, key=f"voice_sel_{project_id}")

    col_test, col_apply = st.columns(2)
    with col_test:
        if st.button(t("btn_test_voice"), key=f"btn_test_v_{project_id}"):
            with st.spinner("Mensintesis sampel suara..."):
                try:
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        adapter = TTSAdapter(
                            provider="edge",
                            voice_name=selected_voice,
                            voice_rate=voice_rate,
                            output_dir=Path(tmp_dir),
                        )
                        from app.services.longform.schema import TtsGroup
                        dummy_group = TtsGroup(group_index=1, paragraph_text="This is a narrator sample for the longform documentary.")
                        res = adapter.synthesize_group(dummy_group, chapter_index=1, force_refresh=True)
                        wav_data = res.wav_file.read_bytes()
                        st.audio(wav_data, format="audio/wav")
                        st.success("Sampel suara berhasil disintesis.")
                except Exception as exc:
                    st.error(f"Gagal memutar sampel suara: {exc}")

    with col_apply:
        # Calculate how many groups need resynthesis
        clean_v = selected_voice.strip()
        total_groups = sum(len(ch.tts_groups) for ch in manifest.chapters)
        stale_count = 0
        for ch in manifest.chapters:
            for g in ch.tts_groups:
                new_hash = compute_audio_group_hash(
                    paragraph_text=g.get_effective_paragraph_text(),
                    tts_provider=g.audio.tts_provider or "edge",
                    tts_model=g.audio.tts_model or "",
                    voice_name=clean_v,
                    voice_rate=float(voice_rate),
                    text_norm_version=g.audio.text_norm_version or "v1",
                    audio_format=g.audio.audio_format or "wav_48k_pcm",
                )
                target_wav = p_dir / (g.audio.file_path or f"assets/audio/groups/ch{ch.chapter_index}_g{g.group_index}.wav")
                if not (g.audio.audio_hash == new_hash and target_wav.is_file() and target_wav.stat().st_size > 44):
                    stale_count += 1

        if st.button(t("btn_set_voice"), key=f"btn_apply_v_{project_id}"):
            voice_svc.set_voice(selected_voice, voice_rate)
            st.success(f"Suara berhasil diterapkan ke proyek! {stale_count} grup akan disintesis ulang.")
            time.sleep(0.5)
            st.rerun()

    st.caption(t("voice_resynth_notice", stale_count=stale_count, total_count=total_groups))

    st.divider()

    # Synthesize Audio Section (B5)
    st.subheader("Sintesis Narasi Audio")
    col_synth_btn, col_synth_space = st.columns([2, 3])
    with col_synth_btn:
        active_job = jobs.get_job_state(project_id)
        is_running = bool(active_job and active_job.get("status") == "running")
        if st.button(t("btn_synthesize_audio"), disabled=is_running, key=f"btn_synth_{project_id}"):
            cmd = [sys.executable, "cli_longform.py", "audio", "synth", project_id]
            jobs.start_job(project_id, "audio_synthesize", cmd)
            st.rerun()

    # Audio Groups Status Table
    st.write(f"### {t('audio_status_title')}")
    table_rows = []
    for ch in manifest.chapters:
        for g in ch.tts_groups:
            target_path = g.audio.file_path if (g.audio and g.audio.file_path) else f"assets/audio/groups/ch{ch.chapter_index}_g{g.group_index}.wav"
            af = p_dir / target_path
            is_done = (af.is_file() and af.stat().st_size > 44 and g.status == ItemStatus.completed)
            table_rows.append({
                "Bab": ch.chapter_index,
                "Grup": g.group_index,
                "Karakter": len(g.paragraph_text),
                "Status": "✅ Selesai" if is_done else ("⚠️ Stale" if g.status == ItemStatus.stale else "⏳ Menunggu"),
                "Durasi (s)": round(g.audio.duration_seconds or 0.0, 2) if g.audio else 0.0,
                "Metode Align": (g.audio.alignment_method or "-") if g.audio else "-",
                "Match Ratio": (g.audio.word_match_ratio or 1.0) if g.audio else 1.0,
            })

    if table_rows:
        st.dataframe(table_rows, use_container_width=True, height=260)
    else:
        st.info("Naskah belum memiliki bab atau grup TTS. Silakan buat naskah terlebih dahulu.")

    st.divider()

    # Audio Preview Section (B5)
    st.subheader(t("audio_preview_title"))
    if manifest.chapters:
        ch_options = [c.chapter_index for c in manifest.chapters]
        preview_ch = st.selectbox("Pilih Bab untuk Preview:", ch_options, key=f"prev_ch_{project_id}")

        if st.button(t("btn_generate_preview"), key=f"btn_prev_{project_id}"):
            with st.spinner("Menghasilkan video preview MP4 dengan subtitle..."):
                try:
                    wav_p, srt_p, mp4_p = voice_svc.generate_preview(preview_ch)
                    if mp4_p.is_file():
                        st.session_state[f"preview_mp4_{project_id}"] = str(mp4_p)
                        st.success("Preview berhasil dibuat!")
                    else:
                        st.error("File preview MP4 tidak terbentuk.")
                except Exception as exc:
                    st.error(f"Gagal menghasilkan preview: {exc}")

        cached_preview = st.session_state.get(f"preview_mp4_{project_id}")
        if cached_preview and Path(cached_preview).is_file():
            st.video(cached_preview)
