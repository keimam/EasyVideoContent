"""Indonesian localization dictionary for Long Video WebUI (B6).

Maintained within the longform module to keep upstream webui/i18n/ 100% untouched.
"""

from __future__ import annotations

I18N = {
    # App Header
    "page_title": "Video Panjang (Longform)",
    "page_subtitle": "Pembuatan dan produksi video panjang berdurasi 10–20 menit secara terstruktur",
    
    # Navigation Tabs
    "tab_projects": "📁 Manajemen Proyek",
    "tab_script": "📝 Naskah & Bab",
    "tab_voice": "🎙️ Suara & Audio",
    
    # Projects Page (B3)
    "projects_list_title": "Daftar Proyek Long Video",
    "create_project_title": "Buat Proyek Baru",
    "project_title_label": "Judul Proyek",
    "project_title_placeholder": "Contoh: Sejarah Muscle Car Amerika 1960-an",
    "project_id_label": "ID Proyek (Unik)",
    "project_id_help": "Pengenal folder proyek, hanya huruf, angka, underscore, atau strip",
    "niche_label": "Kategori Niche Utama",
    "sub_niche_label": "Sub-Niche",
    "focus_label": "Fokus Sub-Niche (Opsional)",
    "focus_placeholder": "Contoh: Fokus pada Pontiac GTO dan Ford Mustang",
    "duration_label": "Target Durasi (Menit)",
    "language_label": "Bahasa Naskah",
    "btn_create_project": "🚀 Buat Proyek",
    "project_created_success": "Proyek '{id}' berhasil dibuat!",
    
    # Project Status
    "status_completed": "✅ Selesai",
    "status_stale": "⚠️ Stale (Perlu Re-sintesis)",
    "status_draft": "📝 Draf",
    "status_running": "⏳ Sedang Berjalan",
    "status_none": "⚪ Belum Dimulai",
    "preset_changed_warning": "⚠️ Preset berubah — jalankan refresh-preset?",
    "btn_refresh_preset": "🔄 Refresh Preset Proyek",
    "refresh_preset_success": "Snapshot preset proyek berhasil diperbarui!",

    # Script Page (B4)
    "script_mode_label": "Pilih Metode Naskah",
    "mode_llm": "🤖 Mode A: Otomatisasi AI (LLM)",
    "mode_import": "📄 Mode B: Impor Markdown",
    "llm_key_warning": "Kunci API LLM belum dikonfigurasi. Silakan atur di konfigurasi MPT / config.toml.",
    "fake_toggle_label": "Gunakan provider FAKE (hanya untuk pengujian / tanpa biaya)",
    "fake_banner": "PERINGATAN: Provider FAKE aktif. Naskah yang dihasilkan adalah teks tiruan deterministik untuk keperluan uji pipa data.",
    "btn_generate_outline": "1. Buat Outline Naskah",
    "btn_generate_script": "2. Tulis Seluruh Bab Naskah",
    "script_import_label": "Tempel Naskah Markdown",
    "script_import_placeholder": "Tempel naskah bertag [visual] [motion] [source] [search] [verify] di sini...",
    "btn_import_script": "📥 Impor Naskah ke Proyek",
    "chapter_editor_title": "Editor Naskah per Bab",
    "select_chapter_label": "Pilih Bab untuk Diedit",
    "btn_save_chapter": "💾 Simpan Perubahan Bab",
    "chapter_saved_stale_warning": "Bab disimpan. Dependensi audio/visual downstream menjadi STALE dan perlu disintesis ulang.",
    "quality_report_title": "📊 Laporan Kualitas Naskah",
    "quality_hook": "Hook Bab",
    "quality_cliche": "Frasa Klise Terdeteksi",
    "quality_ngram": "Pengulangan N-gram",
    "quality_verify": "Tag [verify] Belum Terverifikasi",
    
    # Voice Page (B5)
    "voice_select_label": "Pilih Suara Narator",
    "voice_language_filter": "Filter Bahasa Suara",
    "voice_rate_label": "Kecepatan Bicara (Rate)",
    "btn_test_voice": "🔊 Tes Sampel Suara",
    "btn_set_voice": "💾 Terapkan Suara ke Proyek",
    "voice_resynth_notice": "Mengganti suara akan menyebabkan {stale_count} dari {total_count} grup audio disintesis ulang.",
    "btn_synthesize_audio": "🎙️ Sintesis Seluruh Audio Bab",
    "audio_status_title": "Tabel Status Audio per Grup TTS",
    "audio_preview_title": "Preview Audio & Subtitle",
    "btn_generate_preview": "🎬 Buat Preview Audio (MP4 + Burn Subtitle)",
    
    # Job Monitor (B2)
    "job_monitor_title": "Status Background Job",
    "job_status_running": "Job sedang berjalan...",
    "job_status_completed": "Job selesai dengan sukses.",
    "job_status_failed": "Job gagal.",
    "job_status_cancelled": "Job telah dibatalkan.",
    "btn_cancel_job": "🛑 Batalkan Job",
    "log_output_title": "Log Keluaran Proses (30 baris terakhir)",
}


def t(key: str, **kwargs) -> str:
    """Retrieve localized Indonesian text with optional format parameters."""
    text = I18N.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
