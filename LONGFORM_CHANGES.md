# Log Perubahan File Upstream (Rule A1)

Dokumen ini mencatat setiap perubahan atau penambahan hook pada file asli upstream (`harry0703/MoneyPrinterTurbo`).
Tujuannya adalah menjaga kemudahan sinkronisasi (merge/rebase) dari repositori upstream di masa mendatang.

---

## Ringkasan Modifikasi Berdasarkan Tahap

| Tahap | File Upstream Diubah | Jumlah Baris | Deskripsi Singkat |
|---|---|---|---|
| **Tahap 0** | Tidak Ada | 0 baris | Audit, baseline benchmark, proposal desain |
| **Tahap 1** | `.gitignore` | 1 baris | Menambahkan `!test/longform/**` agar berkas tes unit longform dilacak git (sejalan dengan `!test/services/test_*.py` upstream) |
| **Tahap 2** | Tidak Ada | 0 baris | Script parsing & chunking multi-bab |
| **Tahap 3** | Tidak Ada | 0 baris | Audio generation, alignment, subtitle ASS/SRT |
| **Tahap 4** | Tidak Ada | 0 baris | Visual assets fetch, candidate scoring, contact sheet, review & replace |
| **Tahap 5 & 5.1** | Tidak Ada | 0 baris | Render engine bab, subtitle burner, BGM ducking, concat final, font preflight |
| **Tahap 6A & 6B** | Tidak Ada | 0 baris | Analisis template CapCut 8.9.0, doctor CLI, builder draft portable, multi-track export |
| **Tahap 7A (G5)** | `webui/Main.py` | 6 baris | Entry hook tombol/link "🎬 Long Video" pada top bar header di samping Task Manager & Settings |
| **Tahap 7.9** | Tidak Ada berkas upstream diubah di luar merge | 0 baris baru | Sinkronisasi upstream v1.3.10 (merge clean, hook G5 di Main.py & .gitignore tetap utuh) |
| **Tahap 8A.1** | `webui/Main.py`, `webui/.streamlit/config.toml`, `.streamlit/config.toml` | ~35 baris | Rebrand EasyFaceless Studio: header brand, favicon/title, dark theme tokens, font Plus Jakarta Sans offline, hook sidebar & settings |

---

## Rincian Perubahan per Berkas

### Tahap 1: Fondasi Model Proyek, Preset, Manifest, & Resume
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Hook pada pipa data upstream:** **Nihil**
- **Konfigurasi Git Upstream:**
  - `.gitignore` (baris 38): Menambahkan `!test/longform/**` agar direktori pengujian unit longform baru tidak terblokir oleh aturan `test_*.py` bawaan.
- Seluruh fungsionalitas baru Tahap 1 diisolasi penuh di direktori mandiri:
  - `app/services/longform/` (`__init__.py`, `config.py`, `schema.py`, `hashing.py`, `presets.py`, `store.py`, `planner.py`, `script_import.py`)
  - `cli_longform.py` (CLI mandiri di root direktori)
  - `resource/presets/` (definisi preset bawaan)
  - `docs/longform/` (arsip dokumen dan proposal)
  - `test/longform/` (berkas pengujian unit terisolasi)

### Tahap 2 s/d Tahap 4: Audio, Visual, Relevansi, & CLI Review
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- Seluruh fungsionalitas diisolasi di modul `app/services/longform/` (`audio/`, `visual/`) dan `test/longform/`.

### Tahap 5 & 5.1: Render Bab, Subtitle Burner, BGM Ducking, Concat Final, & Font Preflight
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Aset Font dan Lisensi (R2):**
  - Font default subtitle: `resource/fonts/BeVietnamPro-Bold.ttf`.
  - Sumber: Google Fonts / YellowHaze (Fábio Duarte Martins).
  - Lisensi: **SIL Open Font License 1.1 (OFL-1.1)**, open source bebas royalti untuk penggunaan komersial maupun personal.
  - Font proprietary eksternal seperti "Impact" (Microsoft proprietary) telah dihapus dari seluruh berkas preset (`resource/presets/*.json`).
  - Fitur *Font Preflight Check* ditambahkan di `app/services/longform/render/service.py` dan `cli_longform.py` (`render status`) untuk memperingatkan pengguna secara eksplisit jika font preset tidak ditemukan di direktori `resource/fonts/` atau sistem.
- **Modul Render Longform:**
  - `app/services/longform/render/` (`chapter_render.py`, `final_concat.py`, `subtitle.py`, `bgm.py`, `service.py`)
  - `test/longform/test_stage5_render.py`

### Tahap 6A: Integrasi CapCut Desktop 8.9.0 & Draft Smoke Builder
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Modul CapCut Baru:**
  - `app/services/longform/capcut/` (`__init__.py`, `detector.py`, `builder.py`, `service.py`)
  - `cli_longform.py` (penambahan subperintah `capcut doctor` dan `capcut smoke`)
  - `docs/longform/capcut_template_8.9.0/` (salinan ter-sanitasi dari template acuan pengguna)
  - `docs/longform/CAPCUT_FORMAT_8.9.0.md` (spesifikasi format terverifikasi CapCut Desktop 8.9.0)
  - `test/longform/test_capcut.py` (pengujian mandiri builder, detector, proses aktif, dan integritas draft pengguna)

### Tahap 6B: Export Proyek Penuh ke CapCut Desktop & Backup Asset Pack
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Pembaruan Modul CapCut:**
  - `app/services/longform/capcut/builder.py`:
    * Kalibrasi ukuran subtitle ke `font_size: 8.0` / `styles[0].size: 8` setara ASS `FontSize 64 @PlayResY 1080` (~5.9% tinggi kanvas).
    * Penempatan posisi subtitle di area bawah layar (`clip.transform: {"x": 0.0, "y": -0.80}`) setara MarginV 80.
    * Styling teks: font BeVietnamPro-Bold, warna putih, outline/stroke hitam tebal (`border_width: 0.15`), bold (`bold_width: 0.20`), drop shadow, serta auto line-wrap pada batas 48 karakter.
    * Pendaftaran seluruh media aset di Media panel bin CapCut melalui `draft_virtual_store.json` (`type: 1`, child_ids) dan `draft_meta_info.json` (`enter_from: 0`, `roughcut_time_range`).
    * Dukungan layout multi-track lengkap: Track 0 (112 shot video urut tanpa gap), Track 1 (208 chunk teks subtitle), Track 2 (4 WAV narasi bab independen dengan offset waktu presisi), Track 3 (1 stem BGM ducked menyeluruh).
  - `app/services/longform/capcut/service.py`:
    * Subperintah `export_full_project()`: validasi durasi timeline vs `final_video.mp4` dalam toleransi 1 frame ($712.667$s vs $712.666$s, selisih $0.001$s).
    * Proteksi benturan nama draft (`get_unique_draft_name`) dengan sufiks `_1`, `_2`.
    * Penyimpanan media: default non-copy (0 MB disalin ke C:, mereferensikan absolut drive proyek) vs opsi `--portable` dengan pengecekan sisa kapasitas disk dan konfirmasi `--yes`.
    * Proteksi proses CapCut berjalan (`is_capcut_running`) sebelum penulisan ke direktori draft.
    * Pembuatan paket aset mandiri (B5) di `export/capcut_assets/` lengkap dengan subfolder bab bernomor, audio stem, subtitle SRT, dan `README.txt` ringkasan proyek komprehensif.
  - `cli_longform.py`: Penambahan perintah `capcut export <id> [--install] [--portable] [--yes] [--name <name>]`.
  - `test/longform/test_capcut.py`: Penambahan 6 pengujian unit terisolasi untuk Stage 6B (kalibrasi teks, bin media, layout multi-track, deteksi tabrakan nama, validasi byte identik, dan struktur paket aset).
  - `test/longform/test_cli_smoke.py`: Penambahan pengujian smoke end-to-end untuk `capcut export`.

### Tahap 6B.1: Kalibrasi Subtitle CapCut, Bottom-Anchor, & Draft Kalibrasi Empiris
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Pembaruan Subtitle & Template Engine:**
  - `app/services/longform/capcut/builder.py`:
    * Implementasi `format_capcut_subtitle`: pemecahan baris tunggal (maksimal 2 baris seimbang per chunk kata ~42 karakter/baris, tanpa `\n` beruntun).
    * Penonaktifan auto-wrap CapCut (`"force_apply_line_max_width": False` mengacu template asli) guna mencegah wrapping ganda (double-wrap) yang memecah teks menjadi 3 baris.
    * Implementasi `calculate_bottom_anchored_y`: jangkar vertikal dari tepi bawah layar, memastikan batas bawah teks selalu konstan pada margin $\approx 7\%$ ($y = -0.8600$ atau setara MarginV 80) baik pada 1 baris maupun 2 baris, sehingga teks mengembang ke atas dan tidak pernah terpotong ke luar frame.
    * Perbaikan integer overflow pada `create_time` di `draft_meta_info.json` (menggunakan unix timestamp detik sesuai format bawaan template alih-alih mikrodetik).
  - `app/services/longform/capcut/service.py`:
    * Penambahan fungsi `build_calibration_draft`: memproduksi draft `MPT_CALIBRATE` dengan 6 segmen berurutan @3.0 detik (size 5, 6, 7, 8, 9, 10) berlabel `[SIZE=N]` di atas footage nyata proyek untuk pengujian empiris langsung oleh pengguna.
    * Penambahan fungsi `resolve_capcut_text_size` dan `set_capcut_text_size`: menyetel field `capcut_text_size` pada preset (default: 7).
  - `app/services/longform/schema.py`: Penambahan field `capcut_text_size: Optional[int] = None` pada model `Preset`.
  - `resource/presets/*.json`: Penambahan `"capcut_text_size": 7` pada preset acuan (`documentary_en.json`, `automotive.muscle_car.json`, `automotive.json`).
  - `cli_longform.py`: Penambahan subperintah `capcut calibrate [id] [--no-install]` dan `capcut set-text-size <preset_id> <size>`.
  - `test/longform/test_capcut.py`: Penambahan 5 pengujian unit baru khusus Stage 6B.1 (C1: pemecahan baris tunggal maks 2 baris; C2: kalkulasi jangkar bawah; C3: pembuatan draft kalibrasi & penyetelan ukuran preset; C4: validasi registrasi media bin dan pencegahan integer overflow; C5: pembacaan ukuran preset pada smoke/export). Total 16 unit tests lolos 100%.

### Tahap 7A.4 Addendum G5: Navigasi Dua Arah MoneyPrinterTurbo ↔ Long Video
- **File Kode Upstream yang Disentuh:** `webui/Main.py` (6 baris penambahan pada `_render_top_bar()`).
- **Lokasi Baris:** `webui/Main.py:1827–1832` di dalam container `top_bar_actions`.
- **Kode yang Ditambahkan:**
  ```python
            try:
                from app.services.longform.ui.nav import render_longform_entry
                render_longform_entry()
            except Exception:
                pass
  ```
- **Alasan:**
  * Halaman utama MoneyPrinterTurbo menyembunyikan header Streamlit bawaan (`header[data-testid="stHeader"] { display: none !important; }` di `webui/styles.css`), sehingga tombol panah/hamburger pembuka sidebar navigasi bawaan Streamlit hilang dari layar pengguna.
  * Penambahan tombol/link `🎬 Long Video` di header `Main.py` (sebelah Task Manager dan Settings) memberikan jalan masuk langsung ke Studio Video Panjang tanpa merusak atau mengubah tata letak maupun perilaku halaman utama.
  * Dibungkus blok `try...except` ketat sehingga `Main.py` tetap berjalan normal 100% tanpa risiko exception sekalipun modul longform belum terinisialisasi.
- **Cara Melepas saat Merge Upstream:**
  * Hapus 6 baris blok `try: from app.services.longform.ui.nav import render_longform_entry ... except Exception: pass` dari fungsi `_render_top_bar()` di `webui/Main.py`.
  * Atau jalankan: `git checkout upstream/main -- webui/Main.py`.

### Tahap 7B.1: WebUI Langkah ③ Visual, Contact Sheet Interaktif, & Visual Replace
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Pembaruan Komponen Long Video & WebUI:**
  - `cli_longform.py`:
    * Penambahan subperintah `visual pipeline <project_id> [--provider <name>] [--allow-fallback]`: mengeksekusi `visual fetch` lalu `visual build` secara berurutan dalam satu job background tanpa jeda manual.
  - `app/services/longform/visual/local.py`:
    * Penambahan method `add_file(file_content: bytes, filename: str)` pada `LocalLibraryManager` untuk menerima berkas video/gambar dari uploader UI, menyimpannya langsung ke `assets/visual/library/`, dan mengembalikan metadata lengkap (`content_hash`, `size_bytes`, `asset_type`).
  - `app/services/longform/ui/jobs.py`:
    * Dukungan pengecekan status selesai untuk langkah visual (`visual_pipeline`, `visual_build`, `visual_fetch`).
    * Perhitungan progres visual multi-fase di `get_project_progress`: Fase 1 (0%–50%) "Mencari footage", Fase 2 (50%–100%) "Menyusun klip".
  - `app/services/longform/status.py`:
    * Penyesuaian `compute_project_step_status` agar mengenali langkah aktif visual background (`visual_pipeline`, `visual_build`, `visual_fetch`) sebagai status `"berjalan"`.
  - `app/services/longform/ui/components.py`:
    * Implementasi lengkap `render_visual_step_flow()` menggantikan placeholder sebelumnya.
    * **V1 Ringkasan & Tombol Utama:** Metrik klip siap, perlu dicek, terkunci, sumber terpakai; checkbox pilihan sumber footage (Pixabay, Coverr, Pexels, File lokal, Gambar AI) dengan kunci tersamar (`mask_api_key`) dan alasan penonaktifan; tombol adaptif tunggal (`belum`, `terhenti`, `perlu_diperbarui`, `selesai`); integrasi monitor job dengan label fase.
    * **V2 Galeri Visual / Contact Sheet Interaktif:** Tab per bab, grid thumbnail responsif 4 kolom menggunakan gambar kecil (.jpg dari cache, bukan video penuh) dengan waktu buka super cepat (< 5 ms untuk 112 shot); filter cepat (Semua, Perlu dicek, Terkunci, Placeholder); nomor shot, durasi, potongan teks narasi, sumber, dan badge status; tombol pemilihan shot untuk membuka panel detail.
    * **V3 Panel Detail & Visual Replace:** Preview video potongan klip; teks narasi kalimat lengkap; kata kunci pencarian; skor relevansi; tombol toggle kunci/buka kunci shot; 3 sub-tab penggantian:
      1) "Pilih kandidat lain": daftar kandidat tersimpan dengan skor & tombol "Pakai ini".
      2) "Cari dengan kata lain": input query kustom + pemilihan provider + cari & terapkan.
      3) "Unggah file sendiri": file uploader media video/gambar + simpan ke library lokal + pasang ke shot.
      Setiap aksi penggantian otomatis mengunci shot (`locked = True`), merender ulang hanya 1 klip terkait, dan memperbarui cache thumbnail.
    * **V4 Cache & Pemeliharaan:** Expander pengaturan lanjutan menampilkan ukuran cache global, total berkas, dan jumlah berkas terlindungi; tombol "Bersihkan Cache Lama" (prune) dengan konfirmasi, melindungi seluruh aset yang dipakai proyek aktif.
  - `app/services/longform/store.py`:
    * Verifikasi dan penegasan eksplisit kata kunci `"preset_recovery"` pada fungsi `migrate_umum_presets_and_projects()`.
  - `test/longform/test_stage7b_visual_ui.py`:
    * Pengujian komprehensif 7 test cases mencakup seluruh status WebUI Langkah ③, benchmark performa galeri 112 shot uji_nokey (< 5s), isolasi rebuild 1 shot pada kandidat baru, uploader file lokal ke library, penanda stop marker, proteksi cache, dan kata kunci preset_recovery.

### Tahap 7.9: Sinkronisasi Pembaruan Upstream MoneyPrinterTurbo v1.3.10
- **File Kode Upstream yang Disentuh di Luar Merge:** **Nihil (0 berkas kode)**
- **Hasil Merge Upstream:**
  - Merge clean via 'ort' strategy menggabungkan tag rilis resmi upstream `v1.3.10` (commit `8b0206b`) ke branch `stage/7.9-upstream`.
  - Berkas upstream yang diperbarui secara otomatis:
    * `app/__init__.py`: versi dinaikkan ke `1.3.10`.
    * `app/models/llm_provider.py`: penambahan provider `cheapaimodel`.
    * `app/services/material.py`: preservasi topik berulang pada pencarian term yang sama.
    * `app/services/subtitle.py`: normalisasi bounded similarity $[0, 1]$ pada case-folding/string kosong.
    * `app/services/video.py`: interpolasi sub-pixel precision affine warp (`_smooth_zoom_clip`) untuk menghilangkan getaran zoom gambar.
    * `app/services/voice.py`: dukungan script subtag pada Edge TTS (mis. `iu-Cans-CA-*`).
    * `pyproject.toml` & `uv.lock`: versi paket `mpt` menjadi `1.3.10`.
    * `docs/release-notes-v1.3.10.md` dan test suite upstream baru (`test_subtitle_similarity.py`, `test_image_motion.py`).
- **Integritas Modifikasi Fork:**
  * Hook G5 pada `webui/Main.py` (6 baris di `_render_top_bar()`) tetap utuh tanpa modifikasi atau penimpaan.
  * Pengecualian `.gitignore` (`!test/longform/*.py`) tetap dipertahankan.
  * File konfigurasi pengguna `config.toml` tetap aman dan tidak tersentuh.
- **Hasil Verifikasi Pasca-Merge:**
  * Seluruh rangkaian 227 pengujian di `test/longform` lulus 100%.
  * Banner *"Update available: v1.3.10"* pada antarmuka WebUI hilang secara alami karena versi aplikasi telah mutakhir (`v1.3.10`).

### Tahap 8A.1: Rebrand "EasyFaceless Studio" (Identitas, Tema Global, Font Offline, & Sidebar)
- **File Upstream yang Disentuh:**
  1. `webui/Main.py`:
     - Baris 79–115: Konfigurasi `st.set_page_config` judul halaman `"EasyFaceless Studio — Video Pendek"`, icon favicon PNG lokal, dialog *About* dengan atribusi penuh ke MoneyPrinterTurbo (MIT). Injeksi tema CSS branding secara aman dengan blok `try...except`.
     - Baris 1773–1782: Hook pada `_render_brand()` memanggil `render_brand_header()` dari `app.services.branding.theme` (judul brand, logo, badge versi `"v1.0 · inti v1.3.10"`, dan link update `"Pembaruan inti MoneyPrinterTurbo tersedia"`). Jika modul branding dilepas, fallback otomatis ke tampilan upstream asli.
     - Baris 9483–9490: Handler `open_settings` pada query parameter di `_render_application()` untuk navigasi mulus dari sidebar Video Panjang ke tab pengaturan API & Cache.
     - *Cara melepas saat merge:* Hapus blok try/except `branding` dan kembalikan pemanggilan `_render_brand` ke template HTML lama.
  2. `webui/.streamlit/config.toml` & `.streamlit/config.toml`:
     - Penambahan bagian `[server] enableStaticServing = true` untuk penyajian aset font lokal statis (R2 revisi R1).
     - Penambahan bagian `[theme]` tema dasar gelap (`base="dark"`, `primaryColor="#8FA6FF"`, `backgroundColor="#0D1015"`, `secondaryBackgroundColor="#11151C"`, `textColor="#E8ECF2"`).
- **Revisi 8A.1 (R1 & R2): Proteksi Ikon Material Symbols, Penyesuaian Mockup, & Satu Pintu Per Fungsi:**
  - *Perbaikan Ikon Streamlit (R1):* Font Plus Jakarta Sans dibatasi secara ketat hanya pada elemen teks UI (body, markdown, label, input, button text). Elemen ikon Streamlit (`[data-testid="stIconMaterial"]`, `[class*="material-symbols"]`, `.material-symbols-rounded`, tombol ciut sidebar, pembungkus icon) dilindungi secara eksplisit dengan `font-family: 'Material Symbols Rounded', ... !important`, sehingga glyph ikon tidak lagi tampil sebagai teks ligature mentah ("movie", "bolt", "category", dsb.).
  - *Penyajian Font Statis & Reduksi CSS (R1):* Menggantikan embedding base64 TTF berulang di setiap rerun Streamlit dengan `enableStaticServing = true` di path `/app/static/fonts/PlusJakartaSans[wght].ttf`. Ukuran payload CSS berkurang drastis dari ~239 KB menjadi ~4.4 KB (>98% pemangkasan).
  - *Penyelarasan Sidebar ke Mockup (R1 & R2):* Seluruh item menu (PRODUKSI dan PENGATURAN) tampil seragam sebagai menu item polos tanpa bingkai border. Item sidebar aktif menggunakan latar `#1F2633` dan ikon aksen `#8FA6FF` **tanpa garis tepi aksen kiri**. Header sidebar logo 36x36 dengan teks bertumpuk `EasyFaceless` (bold) + `STUDIO` (small caps abu-abu). Footer sidebar menggunakan kartu halus dengan versi dan atribusi MIT.
  - *Perbaikan Kelola Niche (R2):* Menghubungkan fungsi nyata `format_preset_tree()`, `list_presets()`, `get_builtin_presets()`, dan `get_custom_presets()` dari `app.services.longform.presets` (serta menyediakan alias `render_preset_tree = format_preset_tree`). Dialog menampilkan pohon hirarki ASCII, metrik ringkasan, inspeksi detail, dan tombol langsung ke pengelola niche di Long Video tanpa `ImportError`.
  - *Pembersihan Kode Lama Navigasi Studio (R2):* Menghapus total blok fallback `st.subheader("🧭 Navigasi Studio")` dari `app/services/longform/ui/nav.py`.
  - *Satu Pintu Per Fungsi (R2):*
    * Menghapus tautan redundant "🎬 Long Video" (hook G5) dari `_render_top_bar()` di `webui/Main.py`, memangkas diff file upstream.
    * Memindahkan pemanggilan `render_brand_sidebar(current_page="main")` ke blok inisialisasi awal di samping `inject_brand_theme()`.
    * Menyembunyikan tombol redundant "Settings" di bilah atas melalui CSS terisolasi dari modul branding (`div.st-key-open_settings_dialog_button { display: none !important; }`), sementara logika Python upstream tetap 100% utuh tanpa perubahan. Item sidebar "API & Penyedia AI" menjadi pintu tunggal untuk membuka dialog pengaturan yang sama persis (seluruh tab).
- **Modul Mandiri Terisolasi:**
  - `app/services/branding/`:
    * `constants.py`: Identitas merek (`APP_NAME = "EasyFaceless Studio"`, `APP_VERSION = "1.0.0"`, `CORE_VERSION = "1.3.10"`, `MIT_ATTRIBUTION = "Dibangun di atas MoneyPrinterTurbo (MIT)"`, palet warna HEX token B2).
    * `theme.py`: Pengendali CSS tema global offline, proteksi font ikon Material Symbols, token CSS global, penyembunyian tombol redundant bilah atas via CSS, dan helper gaya 8A.2.
    * `sidebar.py`: Sidebar terpadu B3 dengan header brand mockup, grup PRODUKSI (Video Panjang, Video Pendek), grup PENGATURAN seragam borderless (Kelola Niche, API & Penyedia AI, Cache & Penyimpanan), footer versi, dan dialog "Tentang".
  - `resource/branding/`: `logo.svg`, `logo.png` (256x256), `favicon.png` (64x64).
  - `resource/fonts/plus-jakarta-sans/` serta `webui/static/fonts/`: Berkas font `PlusJakartaSans[wght].ttf` dan lisensi `OFL.txt` (100% luring, 0 panggilan ke `fonts.googleapis.com`).
  - `docs/branding/mockup/`: Acuan visual UI (1_beranda.png, 2_proyek.png, 3_selesai.png).
  - `docs/branding/screens/`: Tangkapan layar headless verifikasi Edge/Playwright (`main.png`, `longvideo.png`, `sidebar.png`).
  - `scripts/verify_icons_headless.py`: Skrip verifikasi headless otomatis untuk font ikon Material Symbols dan teks UI.
  - `app/services/longform/capcut/service.py`: Awalan ekspor draft baru beralih ke `EFS_` (mis. `EFS_<project_id>_<timestamp>`).
  - `app/services/longform/status.py` & `components.py`: Dukungan pembacaan draft ganda (`EFS_` dan legacy `MPT_`) untuk kompatibilitas riwayat dan deteksi draft terbaru.
  - `test/longform/test_branding.py`: 9 pengujian unit & AppTest memverifikasi konstanta merek, proteksi font Material Symbols, font luring tanpa CDN / static serving, backward compatibility prefix CapCut, AppTest Main.py, AppTest Long Video, alur submit video pendek, dan interaksi klik seluruh item sidebar di kedua halaman.



