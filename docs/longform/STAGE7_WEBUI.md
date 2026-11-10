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
