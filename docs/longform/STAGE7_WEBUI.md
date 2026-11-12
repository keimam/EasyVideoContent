# Stage 7A WebUI Long Video: Fondasi & Desain Arsitektur

## 1. Audit Struktur webui/Main.py
- **Struktur Halaman**: `webui/Main.py` merupakan aplikasi Streamlit single-page monolitik (~9.500 baris) untuk pembuatan video pendek (Short Video).
- **Inisialisasi**:
  - Mengatur prioritas `sys.path` agar root repository dimuat pertama.
  - Memanggil `st.set_page_config(page_title="MoneyPrinterTurbo", page_icon="🤖", layout="wide", initial_sidebar_state="auto")`.
  - Menginjeksi CSS dari `webui/styles.css` yang menyembunyikan elemen platform Streamlit (Deploy button, watermark) tanpa mengganggu fungsionalitas sidebar.
- **Konfigurasi & API Key**:
  - Konfigurasi dibaca melalui modul `app.config.config` yang memuat file `config.toml`.
  - Kunci API LLM/TTS dibaca dari konfigurasi ini atau environment variable, lalu disimpan ke `st.session_state` pengguna.
  - Upstream `Main.py` tidak menggunakan `st.sidebar`, sehingga sidebar Streamlit sepenuhnya bersih untuk navigasi multipage.

## 2. Keputusan Penambahan Halaman WebUI
- **Pilihan**: **Opsi (a) — Multipage via folder `webui/pages/` (`webui/pages/1_🎬_Long_Video.py`)**.
- **Alasan**:
  1. **Aturan A1 (Isolasi upstream)**: `webui/Main.py` tetap 100% utuh tanpa modifikasi apa pun (0 perubahan file upstream).
  2. **Dukungan Bawaan Streamlit**: Streamlit 1.66 secara otomatis mendeteksi skrip di dalam folder `webui/pages/` dan menyajikan navigasi sidebar secara transparan.
  3. **Fleksibilitas Operasional**: Pengguna dapat mengakses antarmuka Short Video maupun Long Video dalam satu instance server Streamlit, atau menjalankan halaman Long Video secara terisolasi.
  4. **Bebas Konflik**: Masing-masing halaman Streamlit memiliki siklus render independen dan tidak terjadi tabrakan konfigurasi halaman.

## 3. Perintah Menjalankan UI dengan uv di Windows
- **Menjalankan MPT Lengkap (Short Video + Navigasi Long Video di Sidebar)**:
  ```powershell
  uv run streamlit run .\webui\Main.py
  ```
- **Menjalankan Halaman Long Video Langsung**:
  ```powershell
  uv run streamlit run .\webui\pages\1_🎬_Long_Video.py
  ```

## 4. Desain Modul Long Video WebUI
- `app/services/longform/ui/`:
  - `jobs.py`: Background job runner (detached subprocess, PID lock, cancellation via Windows `CTRL_BREAK_EVENT`, log tailing, state persistence di `job_state.json`).
  - `i18n.py`: Kamus teks bahasa Indonesia khusus longform (tidak menyentuh `webui/i18n/`).
  - `security.py`: Masking kunci API (`pk_****abcd`, `sk-****1234`), sanitasi path Windows.
  - `components.py`: Komponen render UI (proyek, naskah, audio, log viewer).
- `webui/pages/1_🎬_Long_Video.py`:
  - Entry point multipage Streamlit yang memuat tab: Proyek, Naskah, Suara & Audio.

## 5. Diagnosa Akar Masalah Tahap 7A.1 (C1: Job Hilang Saat Refresh)

### Pertanyaan Kunci
Apakah proses anak ikut mati saat Streamlit rerun/refresh, atau proses tetap hidup tetapi UI tidak menemukan kembali?

### Temuan & Bukti Verbatim
Berdasarkan investigasi langsung pada proyek `dodge_charger_stage2`:
1. **File status `job_state.json`**:
   ```json
   {
     "project_id": "dodge_charger_stage2",
     "step": "audio_synthesize",
     "status": "running",
     "pid": 17324,
     "exit_code": null,
     "started_at": "2026-11-10T10:00:13.864660",
     "log_file": "F:\\MPTLong\\storage\\longform_projects\\dodge_charger_stage2\\logs\\job_20261110_100013_audio_synthesize.log"
   }
   ```
2. **Isi log proses anak `job_20261110_100013_audio_synthesize.log` (baris akhir)**:
   ```text
   Audio Synthesis Completed Successfully for Project: dodge_charger_stage2
   -----------------------------------------------------------------
     Status:                  14/14 groups completed
     Total Audio Duration:    259.3s (4.3 min)
     Alignment Breakdown:     {'word_cue': 14, 'whisper': 0, 'proportional': 0}
   -----------------------------------------------------------------
   Run 'python cli_longform.py audio preview dodge_charger_stage2 --chapter 1' to verify synchronization in VLC.
   ```
3. **Status Proses OS Windows**:
   - PID 17324 selesai secara normal (exit code 0) pada pukul 10:01:41.
   - **KESIMPULAN**: Proses anak **TIDAK MATI** saat browser di-refresh! Proses terus berjalan di latar belakang Windows hingga ke-14 grup selesai disintesis secara sempurna.

### Mengapa UI Menampilkan Job "Hilang" & Audio "⚠ 1/14"?
1. **Navigasi Ter-reset ke Tab Beranda**: Di Tahap 7A v1, UI diorganisir dalam tab Streamlit terpisah (`tab_proj`, `tab_script`, `tab_voice`). Saat pengguna me-refresh browser (F5), seluruh state session Streamlit ter-reset (`selected_longform_project = None`) dan antarmuka otomatis kembali ke Tab 1 (Manajemen Proyek). Komponen pemantau job (`render_job_monitor_widget`) hanya ditaruh di Tab 2 dan Tab 3, sehingga di Tab 1 panel pemantau job sama sekali tidak muncul.
2. **Snapshot Saat Refresh Terjadi**: Pengguna me-refresh browser sesaat setelah sintesis dimulai (sekitar 10:00:20), saat baru 1 grup audio yang sempat selesai ditulis ke disk. Tabel proyek membaca `manifest.json` pada detik itu dan mencetak `⚠ 1/14`.
3. **Ketiadaan Query Parameter**: Tidak ada persistensi URL query param (`?project=<id>`), sehingga refresh browser selalu melempar pengguna keluar dari konteks proyek.
4. **Status Job Tidak Diperbarui di Halaman Ringkasan**: Fungsi daftar proyek di Beranda tidak memanggil siklus rekonsiliasi status job (`get_job_state()`), sehingga jika proses anak selesai atau terhenti saat pengguna di Beranda, state file tetap tertinggal dalam status `"running"`.
5. **Flags Pembuatan Proses di Windows**: Di Windows, untuk independensi penuh dari console lifecycle parent Streamlit, subprocess harus menggunakan `creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP` dengan `close_fds=True`.

### Solusi Desain Tahap 7A.1 (C1, C2, C4)
1. **Pemisahan Beranda vs Halaman Proyek berbasis Query Param (`?project=<id>`)**:
   - Refresh browser tidak lagi melempar pengguna ke halaman luar; pengguna tetap berada di dalam halaman proyek yang aktif.
2. **Job Monitor Global di Halaman Proyek**:
   - Panel job selalu tampil di bagian atas halaman proyek selama job aktif / baru saja terhenti / selesai.
3. **Deteksi Status "Terhenti" & Tombol "Lanjutkan"**:
   - Jika proses mati tanpa menyelesaikan seluruh unit target, status transisi ke `"stopped"` ("Terhenti di X dari Y") dengan tombol utama "Lanjutkan".
4. **Pembersihan Lock Basi Otomatis**:
   - Jika file `.lock` ada namun PID sudah mati, lock dibersihkan secara otomatis tanpa memblokir tombol aksi pengguna.
