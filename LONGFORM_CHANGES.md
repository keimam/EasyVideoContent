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
| **Tahap 8A.2** | `webui/Main.py` | 1 baris | Parameter default_language='id' pada resolusi bahasa antarmuka pengguna |
| **Revisi 8A.2 v2** | Tidak Ada | 0 baris | Audit i18n scanner, custom stepper & metric tile, penyesuaian presisi mockup 1-3, pembersihan emoji |
| **Addendum 8A.2 v2 (V6)** | Tidak Ada | 0 baris | Resolusi tumpang tindih visual step, margin vertikal seragam 16-24px, automated CDP bounding box checks |
| **Addendum 8A.2 v2 (V7)** | Tidak Ada | 0 baris | Pemulihan regresi logika Visual (is_replaced, upload file_path, provider), i18n tuntas seluruh langkah & dialog, AST scanner literal UI, pratinjau suara aman, expander Detail error |
| **Tahap 8B.1** | `webui/Main.py` (B3) | ~31 baris | Penyatuan penyedia AI Long Video; Addendum B3 menambahkan guard `_efs_settings_library_only` + `render_embedded_settings_dialog()` di `webui/Main.py` agar dialog Settings MPT dapat dibuka langsung di halaman aktif Long Video tanpa `switch_page` |
| **Tahap 8B.2** | `webui/Main.py`, `webui/i18n/id.json`, `webui/i18n/en.json` (C6) | ~16 baris | Fitur AI F1–F15 + Addendum C6 (pembersihan string UI "MoneyPrinterTurbo"/"MPT" menjadi "EasyFaceless Studio" / nama fitur, mempertahankan kotak MIT sidebar & dialog Tentang) |
| **Tahap 8C.1** | `webui/Main.py` (diperkecil), `.gitignore` | `webui/Main.py` dipangkas ke 46 baris (`20+`, `26-` vs `v1.3.10`) | Pemindahan hook `Main.py` ke `app/services/branding/hooks.py` (target << 97 baris tercapai), penghapusan duplikat `STAGE7_WEBUI.md`, perapian `screens/final/` & `scripts/dev/`, dokumentasi `README.md` + `docs/user/{id,en}/` + `THIRD_PARTY_NOTICES.md` + `CHANGELOG.md` + `LICENSE` |

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
    * `sidebar.py`: Sidebar terpadu B3 dengan header brand mockup, grup PRODUKSI (Video Panjang, Video Pendek), grup PENGATURAN seragam borderless dan rata kiri menyamai PRODUKSI (Kelola Niche, API & Penyedia AI, Cache & Penyimpanan, tinggi 44 px, padding 12 px, gap ikon 8 px), footer versi, dan tombol berbingkai "Tentang" di bagian bawah.
  - `resource/branding/`: `logo.svg`, `logo.png` (256x256), `favicon.png` (64x64).
  - `resource/fonts/plus-jakarta-sans/` serta `webui/static/fonts/`: Berkas font `PlusJakartaSans[wght].ttf` dan lisensi `OFL.txt` (100% luring, 0 panggilan ke `fonts.googleapis.com`).
  - `docs/branding/mockup/`: Acuan visual UI (1_beranda.png, 2_proyek.png, 3_selesai.png).
  - `docs/branding/screens/`: Tangkapan layar headless verifikasi Edge/Playwright (`main.png`, `longvideo.png`, `sidebar.png`).
  - `scripts/verify_icons_headless.py`: Skrip verifikasi headless otomatis untuk font ikon Material Symbols dan teks UI.
  - `app/services/longform/capcut/service.py`: Awalan ekspor draft baru beralih ke `EFS_` (mis. `EFS_<project_id>_<timestamp>`).
  - `app/services/longform/status.py` & `components.py`: Dukungan pembacaan draft ganda (`EFS_` dan legacy `MPT_`) untuk kompatibilitas riwayat dan deteksi draft terbaru.
  - `test/longform/test_branding.py`: 9 pengujian unit & AppTest memverifikasi konstanta merek, proteksi font Material Symbols, font luring tanpa CDN / static serving, backward compatibility prefix CapCut, AppTest Main.py, AppTest Long Video, alur submit video pendek, dan interaksi klik seluruh item sidebar di kedua halaman.
  - `test/longform/test_ui_jobs.py`: Menghapus *fixed sleep* dan menggantinya dengan polling berbasis kondisi dan batas waktu 20 detik untuk kehandalan tes saat beban CPU tinggi (R4).

### Tahap 8A.2: Halaman Long Video Sesuai Mockup & Bahasa Tampilan ID/EN (L1-L5)
- **File Upstream yang Disentuh:**
  1. `webui/Main.py`:
     - Baris 706: Penambahan parameter `default_language="id"` pada fungsi pemanggil resolusi bahasa antarmuka pengguna (`resolve_ui_language`) agar WebUI secara alami mengutamakan bahasa Indonesia saat nilai konfigurasi bahasa di `config.toml` belum disetel pengguna.
     - *Cara melepas saat merge:* Kembalikan pemanggilan `resolve_ui_language` ke default parameter aslinya.
- **Pembersihan & Konsolidasi Font Statis (L5):**
  - Menghapus duplikasi folder font di `resource/fonts/plus-jakarta-sans/` dan root `static/fonts/`.
  - Menetapkan satu-satunya sumber kebenaran font lokal di `webui/static/fonts/PlusJakartaSans[wght].ttf` dan `webui/static/fonts/OFL.txt` yang dilayani langsung oleh server Streamlit secara luring (0 panggilan CDN eksternal).
- **Modul Lokalisasi Bilingual i18n (`app/services/longform/ui/i18n.py`):**
  - Modul baru independen yang menyediakan kamus terjemahan simetris ID dan EN (>100 kunci) dengan helper `t(key, lang=None, **kwargs)` dan `get_ui_language()`.
  - Integrasi lokalisasi penuh pada modul navigasi sidebar (`sidebar.py`) dan seluruh alur halaman Long Video (`components.py`).
  - Menjaga nama merek (EasyFaceless Studio, MoneyPrinterTurbo, CapCut) tidak diterjemahkan secara paksa.
- **Implementasi Mockup 1 — Beranda Long Video (`components.render_home_page`):**
  - Header bersih dengan judul brand, subtitle ringkas, dan kotak pencarian proyek instan di sisi kanan (`home_search_kw`).
  - Kartu "Mulai video baru" yang terbuka langsung (permanen tanpa perlu membuka expander) dengan baris input topik, slider durasi, pilihan bahasa, dan tombol aksi utama `Buat Proyek`.
  - Expander "Pengaturan lanjutan" untuk pemilihan niche, sub-niche, fokus narasi, rasio aspek, ID folder proyek kustom, dan pembuatan niche baru langsung dari UI.
  - Filter chips interaktif menggunakan `st.pills` ("Semua · N", "Sedang dikerjakan · N", "Selesai · N").
  - Grid kartu proyek 3 kolom responsif dengan wadah thumbnail aspek 16:9, badge durasi mengambang, judul 1 baris dengan pemotongan ellipsis, info sub-niche, visual progress bar 5-segmen berwarna (Selesai, Berjalan, Perlu Diperbarui, Belum), dan tombol "Buka proyek".
- **Implementasi Mockup 2 — Halaman Proyek & Langkah ③ Visual (`components.render_project_page` & `render_visual_step_flow`):**
  - Bilah navigasi atas bersih dengan tombol kembali "← Kembali ke Beranda", judul proyek tanpa emoji ganda, chip metadata (niche, durasi estimasi `≈ X menit`, rasio aspek & bahasa narasi), serta tombol kanan popover `▷ Otomatis sampai selesai` (dengan opsi ekspor CapCut dan batas review visual).
  - Banner monitor pekerjaan latar belakang ditempatkan di posisi atas di atas stepper (Mockup 2).
  - Stepper alur kerja 5 kartu horizontal dengan badge status per langkah.
  - Alur Langkah ③ Visual: tata letak 2 kolom berdampingan (kiri galeri footage per bab dengan filter pills dan 2-kolom kartu shot, kanan panel detail shot persisten yang menampilkan pratinjau video klip, teks narasi utuh, kata kunci, 3 sub-tab penggantian visual [Kandidat Lain, Cari Kata Lain, Unggah], tombol toggle kunci shot, dan checkbox penanda perlu dicek).
- **Implementasi Mockup 3 — Tampilan Selesai (`components.render_project_page`, `render_render_step_flow`, `render_capcut_step_flow`):**
  - Banner sukses warna hijau di bagian atas saat 4 langkah utama / seluruh langkah telah selesai ("Video siap diunggah, draft CapCut sudah terpasang...").
  - Tata letak 2 kartu berdampingan saat proyek selesai dan berada di langkah Render atau CapCut:
    * Sisi Kiri ("Video Final"): Pemutar video HTML5 responsif 16:9, metrik teknis (durasi, ukuran berkas, loudness terintegrasi EBU R128 & true-peak, frame & FPS), tombol pemutar sistem, buka folder ekspor, unduh subtitle (.srt), unduh bab YouTube, serta blok cap waktu bab YouTube yang siap disalin 1-klik (`st.code`).
    * Sisi Kanan ("CapCut"): Kartu diagnostik status CapCut Desktop (terpasang, versi, status aktif), tombol cek ulang status CapCut, kalibrasi ukuran teks subtitle (slider 5–10 dan pembuatan draft kalibrasi), tombol ekspor ulang adaptif, tombol buka folder draft, dan tabel riwayat ekspor jika terdapat lebih dari satu draft.
- **Pengujian & Verifikasi Otomatis (L5):**
  - `test/longform/test_i18n.py`: 4 pengujian memverifikasi simetri kunci kamus ID/EN, substitusi format string, fallback bahasa tak dikenal ke EN, dan proteksi kata nama merek.
  - `test/longform/test_stage8a2_ui.py`: 4 pengujian komprehensif AppTest memvalidasi Beranda 3-kolom kartu proyek & progress bar 5-segmen, tata letak detail proyek & panel penggantian visual 2-kolom, tampilan 2-kolom selesai & cap waktu YouTube, serta pengalihan dinamis bahasa ID ↔ EN.
  - Seluruh 244 pengujian dalam test suite `test/longform` lulus 100% tanpa regresi.
- **Tangkapan Layar Headless (L5):**
  - Pembuatan skrip `scripts/capture_8a2_screens.py` menggunakan Chrome/Edge headless berbasis CDP lokal untuk menangkap seluruh tampilan secara otomatis ke `docs/branding/screens/`:
    * `8a2_beranda.png` & `8a2_beranda_en.png`
    * `8a2_proyek_visual.png` & `8a2_proyek_visual_en.png`
    * `8a2_selesai.png` & `8a2_selesai_en.png`

### Revisi 8A.2 v2: Penyesuaian Presisi ke Mockup & Penguatan i18n (V1-V5)
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Audit & Scanner Kebocoran Kunci i18n (V1):**
  - Mengaudit dan menyinkronkan seluruh kunci i18n yang sebelumnya bocor (`gallery_heading`, `all_chapters_option`, `filter_needs_review`, `filter_locked`, `btn_auto_run_until_done`, `unit_minutes`, `capcut_heading`, `final_video_heading`, dll.) ke kamus `ID_TRANSLATIONS` dan `EN_TRANSLATIONS` di `app/services/longform/ui/i18n.py`.
  - Menambahkan pengujian otomatis AST scanner `test_i18n_codebase_scanner` pada `test/longform/test_i18n.py` yang memindai seluruh penggunaan `t("...")` pada `components.py` dan memvalidasi keberadaannya di kedua kamus bahasa.
  - Menambahkan pengujian `test_i18n_no_raw_keys_in_rendered_ui` yang merender UI Beranda dan Proyek via AppTest lalu memverifikasi bahwa tidak ada kunci i18n bergaris bawah atau pola unit yang bocor ke teks komponen antarmuka.
  - Mendukung pengalihan bahasa dinamis instan melalui query parameter URL `?lang=en` atau `?lang=id` pada `get_ui_language()`.
- **Kartu Stepper & Widget Metrik Kustom (V2):**
  - Implementasi kartu stepper `render_stepper_card_html` di `theme.py` yang menampilkan nomor langkah dalam lingkaran aksen/sukses/netral, judul langkah tebal, teks status berwarna, dan bingkai aksen pada langkah aktif, yang diletakkan di atas tombol interaktif transparan (`opacity: 0`) untuk kompatibilitas penuh dengan AppTest dan interaktivitas klik peramban.
  - Implementasi widget metrik kustom `render_metric_tile` (`.efs-metric-tile`) dengan pembungkusan teks otomatis (`white-space: normal`, `word-break: break-word`), tata letak flex-column, dan tanpa pemotongan tanda titik-titik (`text-overflow: clip`).
  - Menambahkan aturan CSS selektor saudara (`:has(.efs-metric-tile) + [data-testid="stElementContainer"]`) yang menyembunyikan fallback `st.metric` bawaan Streamlit secara bersih di peramban sehingga tidak terjadi duplikasi metrik visual.
  - Pembersihan header langkah: judul pendek tanpa nomor/emoji, satu kalimat deskripsi fungsi, tombol aksi utama di sisi kanan baris judul, dan menghapus status teknis berbentuk blok kode.
- **Penyelarasan Langkah Visual ke Mockup 2 (V3):**
  - 4 metrik kustom horizontal di bawah judul: Klip Siap, Perlu Dicek, Terkunci, dan Sumber Terpakai.
  - Opsi sumber footage (Pexels, Pixabay, Local) dipindahkan ke expander collapsible di bawah header (`> Sumber footage`).
  - Tata letak kolom galeri footage 1.6 : 1.0 (sisi kiri galeri kartu 3 kolom, sisi kanan panel detail shot persisten yang menampilkan pratinjau klip, tombol kunci, ganti footage, dan checkbox perlu dicek).
  - Tab bab dibuat ringkas ("Bab 1", "Bab 2", dst.) dan filter chips menampilkan hitungan jumlah klip per kategori (`Semua (N)`, `Perlu dicek (N)`, `Terkunci (N)`, `Placeholder (N)`).
- **Penyelarasan Tampilan Selesai ke Mockup 3 (V4):**
  - Menghilangkan banner ganda: menyembunyikan widget monitor status "Pekerjaan Selesai" saat 4 langkah utama selesai (`hide_completed=core_4_completed`), menyisakan tepat satu banner hijau sukses dari Mockup 3 di bagian atas.
  - Memindahkan kontrol "Pengaturan Niche Proyek" dari expander di atas stepper ke popover tombol `⋯` di bilah atas header.
  - Tampilan 2 kolom berdampingan permanen saat seluruh langkah selesai:
    * Sisi Kiri ("Video final"): Pemutar video HTML5, 4 tile metrik teknis tanpa pemotongan, tombol pemutar sistem & buka folder, cap waktu bab YouTube dengan tombol salin interaktif 1-klik, dan expander "Render lagi" untuk perubahan volume BGM & render ulang paksa.
    * Sisi Kanan ("CapCut"): Header "CapCut" dengan tombol "Cek ulang", 2 tile metrik diagnostik status CapCut & aplikasi aman, kartu draft terbaru, tombol ekspor ulang ke CapCut, tombol buka folder draft CapCut, serta expander kalibrasi ukuran teks subtitle dan riwayat draft.
- **Penyesuaian Durasi Beranda, Pembersihan Total Emoji, & Verifikasi (V5):**
  - Mengubah rentang slider durasi beranda menjadi 10–20 menit dengan nilai default 12 menit dan format string `%d menit`.
  - Menghapus seluruh karakter emoji dari judul, tombol, label, dan chip di seluruh alur antarmuka.
  - Menangkap 6 tangkapan layar headless beresolusi tinggi 1600x1050 (ID & EN: Beranda, Proyek Visual, Selesai) ke `docs/branding/screens/8a2v2_*.png`.
  - Pembuatan skrip profil waktu muat AppTest `Main.py` (`scripts/profile_main_apptest.py`) yang membuktikan waktu inisialisasi hanya 3.22 detik tanpa panggilan jaringan/DNS/subprocess yang lambat.

### Addendum 8A.2 v2: Resolusi Penumpukan Blok & Penyelarasan Jarak Vertikal Konsisten 16–24 px (V6)
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Investigasi Akar Masalah (Root Cause):**
  1. *Jarak Vertikal 0px / Menempel:* Baris 4 metric tile (`.efs-metric-tile`) dirender di dalam `st.columns(4)` (`div[data-testid="stHorizontalBlock"]`). Di bawahnya, expander "Sumber footage" dirender via `st.expander` (`[data-testid="stExpander"]`). Pada layout flexbox kolom Streamlit di dalam `st.container(border=True)`, baik kontainer horizontal baris metrik maupun expander tidak memiliki margin vertikal bawaan. Fallback `st.metric` disembunyikan menggunakan `height: 0; margin: 0;`, sehingga sisi bawah elemen metrik menempel tepat di sisi atas expander (selisih vertikal 0–1 px).
  2. *Lebar Expander Melebihi Barisan Tile:* Komponen expander Streamlit (`details[data-testid="stExpander"]`) memiliki border radius bawaan tema 16 px tanpa deklarasi eksplisit `box-sizing: border-box !important; width: 100% !important;`, sedangkan kartu metrik menggunakan border radius 10 px. Kurva lengkung 16 px dan rendering box-sizing membuat expander terlihat melebar keluar melewati garis batas kartu metrik.
- **Perbaikan Terpusat di Modul Tema Global (`app/services/branding/theme.py`):**
  - Menyelaraskan seluruh komponen kontainer utama (`[data-testid="stExpander"]`, `div[data-testid="stExpander"]`, `details[data-testid="stExpander"]`) dengan `border-radius: 10px !important; box-sizing: border-box !important; width: 100% !important; margin-top: 0 !important; margin-bottom: 1.25rem !important;` (20 px).
  - Menambahkan aturan CSS layout untuk barisan tile metrik kustom `div[data-testid="stHorizontalBlock"]:has(.efs-metric-tile)` dengan `margin-bottom: 1.25rem !important; width: 100% !important; box-sizing: border-box !important;` (20 px).
  - Menambahkan aturan CSS layout untuk barisan stepper kustom `div[data-testid="stHorizontalBlock"]:has(.efs-stepper-card)` dengan `margin-bottom: 1.25rem !important; width: 100% !important; box-sizing: border-box !important;` (20 px).
  - Menambahkan penanganan selektor saudara (`:has(.efs-metric-tile) + [data-testid="stExpander"]`, dll.) untuk mencegah penumpukan margin ganda saat expander langsung mengikuti baris metrik, menjamin jarak vertikal persis 20 px (rentang target 16–24 px).
- **Audit Komponen Kustom Serupa:**
  - Memeriksa seluruh langkah (Naskah, Suara, Visual, Render, CapCut) serta tampilan selesai: seluruh baris metrik, kartu stepper, banner status, dan expander kini memiliki margin vertikal 20 px yang seragam tanpa penumpukan dan lebar 100% box-sizing yang rapi.
- **Pemeriksaan Tabrakan Bounding Box Otomatis di Skrip Tangkapan Layar (`scripts/capture_8a2_screens.py`):**
  - Menambahkan fungsi pengujian otomatis CDP `verify_no_block_overlaps(ws_url, target_url, viewport_width)` yang dijalankan pada resolusi **1280 px** dan **1920 px**.
  - Fungsi ini mengukur koordinat bounding box seluruh blok utama (stepper, job banner, success banner, metric tile row, expander, kartu kolom) dan memverifikasi tidak ada dua blok independen yang bertumpukan (`overlapPx <= 0`).
  - Mengukur jarak vertikal baris metrik ke expander secara presisi via JavaScript di peramban dan mengonfirmasi jarak 20 px (dalam batas 16–24 px).
  - Menghasilkan 6 tangkapan layar beresolusi penuh 1600x1280 terbaru di `docs/branding/screens/8a2v2_*.png`.

### Addendum 8A.2 v2: Pemulihan Regresi Logika, i18n Tuntas, Pratinjau Suara Aman, & Audit Pesan Error (V7)
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Verifikasi Isolasi Non-UI (`git diff stage-8a1-complete --stat -- app/services/longform`):**
  - Hanya 2 file antarmuka yang berubah di `app/services/longform/`: `ui/components.py` dan `ui/i18n.py`. Seluruh file non-UI (service, schema, store, CLI, status, render, capcut) **100% utuh**.
- **Pemulihan Regresi Logika di `app/services/longform/ui/components.py`:**
  1. Menghapus penugasan atribut ilegal `target_shot.is_replaced = True` pada aksi *"Pakai ini"* di Langkah Visual (karena `Shot` di `schema.py` memakai `extra="forbid"` dan tidak memiliki field `is_replaced`), serta mengganti pengecekan ke `target_shot.locked` (yang memang otomatis disetel `True` oleh `VisualService.replace_shot`).
  2. Memulihkan parameter `file_path=asset_info["file_path"]` pada tab *"Unggah"* (`tab_up`) menggantikan regresi 8A.2 (`prompt="", provider_name="local"`).
  3. Memulihkan pemilihan provider dari checkbox di expander *"Sumber footage"* (`target_prov` membaca state `cb_prov_*`).
  4. Membersihkan kunci `st.session_state` checkbox `"Perlu Dicek"` (`chk_rev_{pid}_{shot_id}`) setelah `replace_shot` berhasil agar `needs_review=False` yang baru disimpan tidak tertimpa nilai lama pada rerun berikutnya.
- **Lokalisasi i18n Tuntas (ID & EN):**
  - Melengkapi kamus simetris ID & EN di `app/services/longform/ui/i18n.py` (menghilangkan seluruh ~309 string literal hardcoded di `components.py`, `nav.py`, `sidebar.py`, dan `1_🎬_Long_Video.py`, termasuk dialog dinamis, banner pekerjaan, langkah Naskah, Suara, Visual, Render, dan CapCut, serta pemetaan `next_step` & opsi niche di UI).
- **Pratinjau Suara Aman & Audit Pesan Error:**
  - Tombol *"Buat Video Pratinjau Bab 1"* kini otomatis dinonaktifkan sebelum seluruh audio grup Bab 1 disintesis, disertai keterangan ramah (`voice_preview_prereq_caption`) tanpa menampilkan error jalur file atau perintah CLI teknis.
  - Menambahkan helper `_render_friendly_error(friendly_msg, exc)` sehingga seluruh `st.error` menampilkan pesan ramah pengguna sementara detail teknis/stack ditempatkan di dalam expander *"Detail"* / *"Details"*.
- **Penguatan Otomatis (`test_i18n.py`, `test_stage8a2_ui.py`, `conftest.py`):**
  - Menambahkan AST scanner `test_no_hardcoded_ui_strings_without_t` yang memastikan 0 string literal ID/EN dipakai langsung di widget `st.*` tanpa `t()`.
  - Menambahkan `test_all_steps_no_mixed_language_apptest` (Beranda + 5 langkah dalam ID & EN) dengan daftar kata terlarang lintas bahasa.
  - Menambahkan `test_v7_visual_use_candidate_replaces_asset_and_locks`, `test_v7_visual_lock_toggle_and_upload_wiring`, `test_v7_voice_preview_disabled_before_audio_synth`, dan `test_v7_friendly_error_with_detail_expander`.
- **Tangkapan Layar Verifikasi ID & EN Semua Langkah:** `docs/branding/screens/8a2v3_*.png`.

### Tahap 8B.1: Penyatuan Penyedia AI Long Video dengan MoneyPrinterTurbo (Naskah, Suara, Gambar AI)
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)** — Dialog Settings MPT (`webui/Main.py`) sudah mendukung pembukaan langsung ke tab `"llm"`, `"general"`, dan `"image"` melalui `st.session_state["settings_dialog_open"] = True` dan `st.session_state["settings_dialog_target_tab"]`.
- **Dokumentasi Audit:** `docs/longform/AI_PROVIDERS.md` (tabel lengkap penyedia LLM/TTS/Gambar AI, akar masalah pengecekan Gemini di UI, dan arsitektur satu adapter).
- **LLM / Naskah (`app/services/longform/llm_adapter.py`, `script_generator.py`, `ui/security.py`, `cli_longform.py`):**
  - Menghapus pengecekan manual ganda (`check_has_llm_key`); seluruh 44 penyedia MPT dinilai melalui `get_llm_readiness()` berbasis `configured_llm_providers`, `codex_oauth.account_status`, `chatgpt_oauth.account_status`, `kimi_code_oauth.is_authorized`, CLI, dan Ollama. Validasi prefiks `AIza` hanya berjalan saat penyedia aktif adalah Gemini.
  - Deteksi keluaran string `"Error: ..."` dari `_generate_response()`: retry backoff eksponensial (maks 3x) untuk HTTP 429 (`LLMRateLimitError`) dan kegagalan cepat dengan pesan ramah (`LLMModelNotFoundError`) saat model tidak tersedia/pensiun. `ScriptGenerator` menjaga bab yang sudah selesai agar **Lanjutkan** meneruskan bab tersisa.
  - Penambahan `test_llm_connection()`, `list_llm_models()` (mendukung katalog Codex/ChatGPT/Groq dan endpoint `/models` OpenAI/OpenRouter/Gemini + fallback teks bebas), serta `set_active_llm_model()` yang langsung menulis ke `config.app` MPT.
- **TTS / Suara (`app/services/longform/tts_adapter.py`, `voice_service.py`):**
  - Mendukung pemilihan penyedia TTS terkonfigurasi MPT (`edge` / `azure-tts-v1` selalu tersedia; `azure-tts-v2`, `siliconflow`, `gemini-tts`, `mimo-tts`, `minimax-tts`, `elevenlabs`, `chatterbox`, `kokoro`, `fish_audio`, `voxcpm` bila terkonfigurasi), pengambilan daftar suara per penyedia, ekstraksi word boundary `azure-tts-v2` (`.subs`/`.offset`), dan fallback Whisper (`allow_whisper=True`) untuk penyedia tanpa word timing dengan konversi tetap WAV PCM mono 48 kHz.
  - `VoiceService.set_voice(..., tts_provider=...)` menyimpan `tts_provider` di `preset_snapshot` dan menghitung `compute_audio_group_hash()` sehingga pergantian penyedia hanya menandai grup terdampak `perlu_diperbarui`.
- **Gambar AI / Visual (`app/services/longform/visual/ai_image.py`, `visual/service.py`):**
  - Mendukung kedua backend Gambar AI MPT (`openai_image` dan `codex_image`), mengaktifkan checkbox *"Gambar AI ({provider})"* di Langkah Visual saat terkonfigurasi di MPT, serta menampilkan perkiraan jumlah gambar dan informasi biaya sebelum job berjalan.
- **Tangkapan Layar Verifikasi ID & EN:** `docs/branding/screens/8b1_*.png`.
- **Addendum 8B.1 (B1 — Penyeragaman Pembangun Perintah Job & Pesan Koneksi):**
  - Menambahkan `get_repo_root()`, `get_cli_script_path()`, `build_longform_cli_command()`, `JobLaunchError`, serta validasi executable & skrip CLI sebelum `subprocess.Popen` di `app/services/longform/ui/jobs.py`.
  - Memindahkan seluruh pemanggil `jobs.start_job` di `app/services/longform/ui/components.py` (`script_outline`, `script_generate`, `audio_synthesize`, `visual_pipeline`, `render_all`, `capcut_export`, `pipeline_auto`) ke `jobs.build_longform_cli_command(...)` + `_start_job_safe(...)` agar kegagalan launch menampilkan pesan ramah + expander `Detail`.
  - Memperbaiki teks tes koneksi saat balasan kosong (`llm_connection_ok_plain`) di `i18n.py` dan `components.py` agar tidak berakhiran titik dua kosong.
- **Addendum 8B.1 (B2 — Isolasi Bahasa Narasi Proyek, Deteksi & Retry Bahasa, Normalisasi Paragraf, Tulis Ulang Per Bab):**
  - Menambahkan `app/services/longform/language_guard.py` (`resolve_project_language`, `format_language_directive`, `detect_text_language`, `check_language_compliance`) dan field opsional `ProjectManifest.language`, `Chapter.needs_language_review`, `Chapter.language_warning` di `schema.py`.
  - Memastikan `store.create_project()` menyinkronkan `umum`/`umum.id`/`umum.en` sesuai `language`, menyimpan `manifest.language`, dan menimpa `preset_snapshot["language"]`; serta memperbaiki key `sel_root_niche_{eff_lang_code}` di `components.py` agar state preset tidak tertinggal saat pengguna menukar bahasa narasi.
  - Mempertegas target bahasa narasi proyek di bagian AWAL & AKHIR seluruh template prompt (`resource/longform/prompts/{outline,chapter,revise_length,summary}.md`) termasuk untuk judul bab.
  - Menambahkan validasi bahasa otomatis pasca-panggilan di `ScriptGenerator` dengan retry hingga 2× (`max_lang_retries=2`), penandaan `"Perlu dicek: bahasa"` pada `quality_report.md` & UI, tombol *"Tulis ulang bab ini"* (`script rewrite --chapter <idx>`) untuk memperbaiki bab rusak tanpa menghapus bab lain, serta normalisasi paragraf pendek ke ±40–120 kata secara seragam di `script_import.py::normalize_short_paragraphs()`.
- **Addendum 8B.1 (B3 — Dialog Settings MPT di Halaman Aktif, Ikon `st.dialog`, & Subjudul Sadar-Status):**
  - Hook minimal di `webui/Main.py` (`_efs_settings_library_only` membungkus bootstrap halaman agar dapat diimpor sebagai pustaka + helper `render_embedded_settings_dialog()`); `webui/pages/1_🎬_Long_Video.py`, `app/services/branding/sidebar.py`, dan `components._open_mpt_settings_dialog()` membuka dialog Settings MPT langsung di halaman Long Video tanpa `st.switch_page("Main.py")` sehingga proyek & langkah aktif tidak berpindah.
  - Memperbaiki aturan pengecualian ikon Material Symbols di `app/services/branding/theme.py` (`span[role="img"][aria-label$=" icon"]`, `span[style*="Material Symbols"]`, dan aturan di dalam `st.dialog` / `stSubheader`) sehingga ikon `:material/psychology:`, `:material/movie:`, `:material/image:`, dan `:material/video_library:` pada tab Umum tampil sebagai glif, bukan teks mentah.
  - Menambahkan `_get_step_subtitle()` di `components.py` dan kunci `*_subtitle_{belum,berjalan,terhenti,perlu_diperbarui,selesai}` simetris ID/EN di `i18n.py` untuk semua 5 langkah serta memperjelas label `prov_ai_no_mpt_key`.
  - **Cara Melepas Hook `webui/Main.py` saat Merge Upstream:** Hapus blok `if not st.session_state.get("_efs_settings_library_only", False):` (kembalikan indentasi baris `st.set_page_config`–`render_brand_sidebar` dan `_render_application()`) serta hapus fungsi `render_embedded_settings_dialog()`.

### Tahap 8B.2: Fitur AI Opsional (Relevansi Vision, Terjemah Kata Kunci, AI Video per Shot) & Perapian UI
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode baru)** — Seluruh fitur memakai adapter/konfigurasi MPT yang sudah ada melalui modul terisolasi di `app/services/longform/visual/`.
- **F1 (`app/services/longform/visual/ai_relevance.py`):** Toggle *"Cek relevansi dengan AI"* (default mati) di expander *Sumber footage*, deteksi kapabilitas vision model aktif, ekstraksi frame thumbnail + narasi + subjek preset, cache per hash aset, batas konkurensi, retry 429, estimasi jumlah panggilan, penandaan `needs_review=True` tanpa mengganti aset, serta tombol *"Cek ulang dengan AI"* di panel detail shot.
- **F2 (`app/services/longform/visual/keyword_translator.py`):** Terjemahan kata kunci pendek ke English secara batch per bab untuk proyek non-English (hanya alokasi baru / cari ulang, menjaga `shot_hash` lama tetap utuh), cache global, fallback aman dengan peringatan ringan bila LLM belum siap, serta tampilan kata kunci asli + English dan praisi *"Cari lagi"* di panel detail shot.
- **F3 (`app/services/longform/visual/ai_video.py`, `config/longform.toml`, `app/services/longform/config.py`):** Tab *"AI Video"* per shot (prioritas WaveSpeed, dukungan penyedia AI video MPT lain), pembulatan durasi ke dukungan penyedia `>= durasi shot`, tabel harga yang dapat diedit pengguna di `config/longform.toml`, dialog konfirmasi biaya wajib sebelum memanggil job runner (tahan F5 & Batal, kunci otomatis & rebuild 1 klip saja + perbarui thumbnail), serta aksi massal *"Buat AI Video untuk shot Perlu dicek"* dengan batas anggaran `$`.
- **F4 (`app/services/branding/theme.py`, `app/services/longform/ui/i18n.py`):** Menyembunyikan ikon tautan jangkar heading (`🔗` / `[data-testid="stHeaderActionElements"]`) dan meringkas label checkbox Gambar AI belum diatur dengan tooltip lengkap (`help`).
- **Tangkapan Layar Verifikasi ID & EN:** `docs/branding/screens/8b2_ai_video_tab_{id,en}.png`, `docs/branding/screens/8b2_ai_video_confirm_{id,en}.png`.
- **Addendum 8B.2 (F5 — Isolasi `config.toml`, Batas `max_tokens` per Jenis Panggilan, Penanganan 402/404 tanpa Retry, Fallback Model Aman OpenRouter, Lencana Gratis/Berbayar, & Sumber Tabel Harga AI Video):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)** — `app/models/llm_provider.py` (`minimax/minimax-m3:free`) tidak diubah sesuai aturan.
  - Menambahkan fixture `autouse=True` `isolate_host_config_toml` di `test/longform/conftest.py` agar seluruh tes maupun AppTest tidak dapat mengubah `config.toml` pengguna.
  - Membatasi parameter `max_tokens` di lapisan pemanggil longform (`llm_adapter.py`, `script_generator.py`, `ai_relevance.py`, `keyword_translator.py`) agar tidak pernah dikirim kosong (yang menyebabkan OpenRouter me-reservasi batas `65535` token).
  - Menambahkan `LLMCreditError` (HTTP `402`) dan mendeteksi galat `"unavailable for free"` / `"paid version is available now"` (`404` -> `LLMModelNotFoundError`) dengan **0 retry otomatis**, pesan ramah pengguna + saran `openrouter/free`, tombol *"Buka Pengaturan"*, serta expander *"Detail"*.
  - Saat `llm_provider == "openrouter"` dan kolom model kosong, lapisan longform memakai fallback aman `"openrouter/free"` (`resolve_effective_model()`); menambahkan lencana `(Gratis)` / `(Berbayar)` pada kartu *"Tulis dengan AI"* dan toggle *"Cek relevansi dengan AI"*.
  - Memperbarui tabel harga `[ai_video_pricing]` (`config/longform.toml`, `app/services/longform/config.py`, `app/services/longform/visual/ai_video.py`): menambahkan `wavespeed-ai/wan-2.2/t2v-480p-ultra-fast` (`$0.010/dtk`), `wavespeed-ai/wan-2.2/t2v-5b-720p` (`$0.060/dtk`), mengoreksi `bytedance/seedance-2.0-fast*` (`$0.100/dtk`), menambahkan `url` sumber resmi + `checked_at = "2026-11-17"` di setiap baris model, dan menampilkan penafian *"perkiraan — cek tarif penyedia"* beserta tautan sumber di dekat perkiraan biaya.
- **Addendum 8B.2 (F6 — Perbaikan Tes Koneksi & Token Reasoning pada Model Gratis):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
  - Memperbarui `MAX_TOKENS_TEST_CONNECTION = 256` dan `MAX_TOKENS_VISION_RELEVANCE = 256` (`llm_adapter.py`), serta batas minimal batch/tunggal `keyword_translator.py` ke `320` agar token reasoning model gratis tidak menghabiskan kuota respons sebelum teks konten muncul.
  - Menambahkan `invoke_openai_chat_completion(..., minimize_reasoning=True)` untuk mengirim `extra_body={"reasoning": {"enabled": False}}` khusus pada penyedia OpenRouter (dengan fallback otomatis bila gateway menolak argumen tambahan), serta `extract_sdk_completion_or_raise()` dan `LLMEmptyResponseError` untuk mendeteksi `finish_reason="length"` / `reasoning_tokens` > 0.
  - Menambahkan eskalasi 1× otomatis saat respons kosong dengan `finish_reason="length"` atau `has_reasoning` (`256 → 512` pada tes koneksi dan vision relevance; `320 → 640` / `2×` maks `3072` pada terjemah kata kunci). Bila HTTP tetap `200 OK` namun teks tetap kosong permanen setelah retry, tes koneksi mengembalikan penanda `[EMPTY_AFTER_HTTP_OK]` agar UI menampilkan peringatan ramah *"Terhubung, tetapi model tidak mengembalikan teks — coba lagi atau pilih model lain."* alih-alih *"Tes koneksi gagal"*.
  - Untuk adapter non-OpenAI-compatible/non-Gemini (mis. OAuth/CLI), `test_llm_connection()` langsung memanggil `app.services.llm.test_connection()`.
- **Addendum 8B.2 (F7, F8, F9 — Hapus Proyek & Tempat Sampah, Kata Kunci Visual AI per Shot, & Pilihan Penyedia AI Video):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
  - **F8 (`app/services/longform/visual/ai_keywords.py`, `schema.py`, `shot.py`, `visual/service.py`, `cli_longform.py`):** Menambahkan field opsional `Shot.search_query_source` (`"ai" | "extraction" | "tag"`), satu panggilan batch LLM per bab untuk menghasilkan query visual English 3–6 kata yang menggambarkan objek/suasana nyata di layar (membedakan tiap sub-shot dari kalimat yang sama lewat pemecahan klausa + pencegah duplikat), menghormati tag `[search]`, cache lokal, fallback ke ekstraktor saat LLM belum siap, tombol UI *"Perbarui kata kunci dengan AI"* (cakupan seluruh bab / bab ini + perkiraan panggilan, menandai `ItemStatus.stale` hanya pada shot belum terkunci), tampilan *"Kata kunci pencarian (EN)"* + *"Asal: ..."* + ringkasan bahasa asli di panel detail, serta subperintah CLI `visual ai-keywords`.
  - **F9 (`app/services/longform/visual/ai_video.py`, `ui/components.py`):** Menambahkan `list_supported_ai_video_providers()` untuk menampilkan seluruh 8 penyedia AI Video MPT saat belum ada penyedia yang dikonfigurasi + tombol *"Pengaturan Video AI"* menuju tab `"video"`, serta menjaga dropdown penyedia + model dinamis mengikuti seluruh penyedia terkonfigurasi MPT (prioritas WaveSpeed pertama) dan estimasi biayanya.
  - **F7 (`app/services/longform/trash.py`, `presets.py`, `ui/components.py`, `cli_longform.py`):** Menambahkan fitur hapus proyek aman yang memindahkan folder proyek ke `storage/longform_projects/_trash/<id>_<timestamp>/` dari menu `"⋯"` pada kartu beranda dan bilah atas halaman proyek, dialog konfirmasi wajib ketik judul/ID + ukuran folder (MB) + jumlah draft CapCut (`EFS_`/`MPT_`) dengan checkbox *"Hapus juga draft CapCut terpasang"* default MATI, penolakan saat job berjalan atau proyek terkunci, tampilan **"Tempat Sampah"** (filter beranda & pintasan di *Cache & Pemeliharaan*) dengan *"Pulihkan"*, *"Hapus permanen"*, *"Kosongkan sampah"* + total MB dibebaskan, penjagaan `delete_custom_preset` agar tidak menghitung proyek di `_trash/`, serta subperintah CLI `project delete`, `project restore`, `trash list`, dan `trash empty`.
  - **Tangkapan Layar Verifikasi ID & EN:** `docs/branding/screens/8b2_ai_video_dropdown_{id,en}.png`, `docs/branding/screens/8b2_delete_dialog_{id,en}.png`, `docs/branding/screens/8b2_trash_{id,en}.png`.
- **Addendum 8B.2 (F10 & F11 — Keandalan Hapus Proyek + Job Berjalan, Guard Judul Bab, Label Kata Kunci Jujur, & Petunjuk Vision):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)** — Seluruh perbaikan berada di modul Long Video terisolasi (`trash.py`, `script_import.py`, `script_generator.py`, `schema.py`, `visual/ai_keywords.py`, `ui/components.py`, `ui/i18n.py`, `cli_longform.py`).
  - **F10 (`app/services/longform/trash.py`, `ui/components.py`, `ui/i18n.py`):**
    - Memperbaiki kebuntuan modal *"Hapus Proyek"* di Streamlit `1.54` (ketik ID lalu klik `"Pindahkan ke Sampah"` tanpa Enter/blur sebelumnya terserap tombol `disabled`) dengan membungkus input + tombol submit `st.form` yang selalu dapat diklik dan memvalidasi kecocokan ID/judul di handler submit.
    - Mengizinkan penghapusan **semua** proyek termasuk yang memiliki job aktif: menampilkan peringatan non-memblokir (`dlg_delete_running_warn`), menulis marker berhenti (`STOP_REQUESTED`), menunggu keluar hingga ~20 detik dengan indikator kemajuan, menghentikan paksa proses anak bila masih hidup, melepas `.lock`, menandai `job_state.json` `"stopped"`, lalu memindahkan folder ke `_trash/`. Saat dipulihkan (*"Pulihkan"*), proyek berstatus `"terhenti"` dan dapat dilanjutkan.
    - Menambahkan pemindahan Windows yang andal (`gc.collect()`, penghapusan atribut readonly, coba ulang backoff saat `PermissionError`, cadangan copy-lalu-hapus dengan rollback atomik memulihkan berkas asal bila berkas masih terkunci, serta pengecualian `ProjectTrashError(locked_files=...)` yang menampilkan pesan ramah + daftar jalur berkas terkunci di expander *Detail*).
  - **F11 (`app/services/longform/script_import.py`, `script_generator.py`, `schema.py`, `visual/ai_keywords.py`, `ui/components.py`, `cli_longform.py`):**
    - Memperketat parser Markdown & generator naskah (`strip_glued_chapter_heading()`, `_extract_heading_and_body()`) agar variasi heading seperti `#Bab N:`, `##Bab N:`, `Chapter N:`, `Bab N -`, `[ch:N]`, dan heading menempel tanpa spasi/baris baru tidak pernah masuk ke teks narasi, TTS, subtitle, maupun ekstraksi kata kunci.
    - Menambahkan utilitas perbaikan proyek terdampak (`detect_leaked_chapter_headings()`, `fix_project_headings()`, CLI `project fix-headings <id>`, serta banner + tombol *"Bersihkan Judul Bab dari Narasi"* pada langkah **Naskah**) yang membersihkan narasi & kata kunci ekstraksi belum terkunci sekaligus menandai grup audio terkait `ItemStatus.stale` (`"perlu_diperbarui"`).
    - Menambahkan `Shot.search_query_reason` dan `format_keyword_reason()` agar label kata kunci jujur menampilkan `(EN)` hanya ketika query berbahasa Inggris (`search_query_en` atau proyek EN) dan `({proj_lang})` ketika masih memakai ekstraksi non-EN, menyertakan alasan pada *Asal* (`LLM belum siap`, `AI gagal: ...`), serta mempertahankan peringatan fallback kata kunci AI (`ai_kw_last_notice_*`) melintasi `st.rerun()`.
    - Menambahkan tooltip `help` + keterangan singkat + tombol **"Buka Pengaturan"** di bawah tombol nonaktif *"Cek ulang dengan AI"* bila model LLM aktif tidak mendukung pembacaan gambar (*vision*).
  - **Tangkapan Layar Verifikasi ID & EN:** `docs/branding/screens/8b2_delete_running_warn_{id,en}.png`, `docs/branding/screens/8b2_f10_trash_{id,en}.png`, `docs/branding/screens/8b2_f11_shot_detail_{id,en}.png`, `docs/branding/screens/8b2_f11_script_fix_{id,en}.png`.
- **Addendum 8B.2 (F12 — Kata Kunci Visual Konkret, Jangkar Topik `anchor_terms`/`avoid_terms`, & Multi-Query Alternatif):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)** — Seluruh perubahan berada di `schema.py`, `visual/ai_keywords.py`, `visual/stock.py`, `visual/service.py`, `ui/components.py`, dan `ui/i18n.py`.
  - **Jangkar Topik & Ringkasan Visual Proyek (`schema.py`, `ai_keywords.py`, `components.py`):** Menambahkan `ProjectManifest.visual_anchor_terms`, `visual_avoid_terms`, dan `visual_summary`; helper `ensure_project_visual_anchors()` menghasilkan dan me-cache `anchor_terms`/`avoid_terms` sekali per proyek (`ai_visual_anchors.json`) beserta fallback heuristik offline, serta menampilkan/mengeditnya melalui expander *"Pengaturan Lanjutan Proyek (Jangkar Visual)"* (*"Simpan Pengaturan"* & *"Buat ulang dengan AI"*).
  - **Prompt Konkret + Guard Kata Abstrak + 2–3 Alternatif (`ai_keywords.py`, `schema.py`):** Menambahkan `Shot.search_queries_en`, daftar `ABSTRACT_NON_VISUAL_WORDS`, dan `_build_concrete_query_alternatives()`; prompt batch F8/F12 menerima topik proyek, judul bab, sub-niche, ringkasan visual, serta `anchor_terms`/`avoid_terms` dan memaksa 2–3 query konkret yang terlihat kamera per shot.
  - **Filter Relevansi Stok + Fallback Berurutan (`visual/stock.py`, `visual/service.py`):** `score_candidate_relevance()` dan `StockVisualProvider.search_candidates()` menolak kandidat yang mengandung `avoid_terms` atau tidak mencocokkan satu pun `anchor_terms`, serta mengekstrak token slug URL pada fallback Pexels/Coverr/Pixabay; `VisualService.fetch_all()` mencoba query utama lalu query alternatif berurutan pada `Shot.search_queries_en` secara otomatis bila query sebelumnya tidak menghasilkan kandidat lolos filter.
- **Addendum 8B.2 (F13 — Paksa Fetch Ulang Saat Kata Kunci Berubah, Eskalasi Query Pendek & Anchor `< 3` Kandidat, Filter Tab Kandidat, & Tampilan Query Terpilih):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)** — Pembaruan hanya pada `visual/ai_keywords.py`, `visual/stock.py`, `visual/service.py`, `ui/components.py`, dan `ui/i18n.py`.
  - **Invalidasi Aset & Kandidat Basi (`ai_keywords.py`, `service.py`):** Saat kata kunci shot berubah pada `update_project_visual_keywords_ai()`, `candidates`, `tags`, `effective_query`, dan status vision lama langsung dibersihkan serta aset ditandai `stale`; `_is_shot_asset_valid_for_provider()` juga mendeteksi ketidaksesuaian `effective_query` vs kata kunci terkini agar fetch wajib mengambil ulang (sementara shot `locked=True` tetap terlindungi). Thumbnail lama dihapus saat penggantian aset.
  - **Eskalasi Query Stok `< 3` Kandidat (`stock.py`, `service.py`):** Menambahkan `build_short_core_queries()` (2–3 kata inti); `VisualService.fetch_all()` mengakumulasi kandidat lolos filter melintasi urutan: query utama → query alternatif → query dipersingkat (2–3 kata inti) → `anchor_terms` proyek sampai tercapai 3 kandidat relevan, lalu mencatat query penentu kandidat teratas ke `Shot.effective_query`.
  - **Filter Tab Kandidat & Panel Detail (`stock.py`, `components.py`, `i18n.py`):** Menambahkan `filter_candidates_by_anchors_and_avoids()` untuk menyaring kandidat bertag `avoid_terms` / nol `anchor_terms` pada tab **Kandidat**, serta menampilkan **"Ditemukan dengan: `...`"** (*"Found with: `...`"*) di panel Detail shot.
- **Addendum 8B.2 (F14 — Sumber Kebenaran Tunggal Klip Shot, Cache Thumbnail Berbasis Hash, Badge "Perlu disusun ulang" & Filter "Perlu diperbarui · N"):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)** — Perubahan terisolasi di `visual/base.py`, `visual/service.py`, `visual/ai_video.py`, `ui/components.py`, dan `ui/i18n.py`.
  - **Sumber Kebenaran & Cache Hash (`visual/base.py`, `ui/components.py`):** Menambahkan `resolve_shot_visual_source()` dan `invalidate_shot_thumbnail_cache()`; thumbnail galeri dan pratinjau panel Detail kini selalu membaca berkas yang sama (`rendered_clip` sebagai sumber utama, atau `asset_path` bila klip belum disusun), dengan cache thumbnail diberi kunci hash isi (`<shot_id>_<hash16>.jpg`) dan berkas lama/non-hash dihapus otomatis setiap aset/klip berubah.
  - **Status, Badge, & Filter (`visual/service.py`, `visual/ai_video.py`, `ui/components.py`, `ui/i18n.py`):** Aset yang berubah tanpa build ulang menandai `status=stale` (`perlu_diperbarui`) dan memunculkan badge **"Perlu disusun ulang"** di kartu galeri maupun peringatan di panel Detail (serta badge **"Belum disusun"** bila `rendered_clip` belum ada), ditambah chip filter galeri **"Perlu diperbarui · N"**.
- **Addendum 8B.2 (F15 — Progres Akurat untuk Proses Sebagian di Visual, Suara, Render, & Auto):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)** — Perubahan hanya pada `store.py`, `visual/service.py`, `voice_service.py`, `render/service.py`, `render/chapter_render.py`, `ui/jobs.py`, `ui/components.py`, `ui/i18n.py`, dan `cli_longform.py`.
  - **Penghitungan Target Terlingkup (`visual/service.py`, `voice_service.py`, `render/service.py`, `render/chapter_render.py`):** Sebelum memproses, setiap layanan mempartisi unit kerja vs unit valid/terkunci yang dilewati, lalu melaporkan kemajuan `0/N` (`0.0%`) hingga `N/N` (`<=99.0%` saat berjalan; fase tanpa unit terukur memakai `is_indeterminate=True` <=95%; mencapai `100.0%` hanya setelah selesai).
  - **Monitor Pekerjaan & i18n (`store.py`, `ui/jobs.py`, `ui/components.py`, `ui/i18n.py`):** `update_job_phase()` menyimpan `phase_key`/`phase_args`/`skipped`/`partial` secara atomik di `job_state.json`; `jobs.start_job()` memulai dari `0.0%` (*indeterminate*) agar tidak pernah melompat ke `99%`; widget monitor menampilkan `dilewati: N (sudah valid/terkunci)` / `skipped: N (already valid/locked)`.
- **Addendum C6 (Pembersihan Nama MoneyPrinterTurbo/MPT dari String UI & Penyelarasan Tooltip Sidebar):**
  - **File Upstream yang Disentuh (Rule A1):** `webui/Main.py` (~7 baris pada fallback `_main_page_title`, `_render_brand`, dan `_about_content`), `webui/i18n/id.json` (3 baris: `Settings Preset Import Failed`, `Key Restore Failed`, `chatgpt.oauth.pending`), dan `webui/i18n/en.json` (12 baris prosa UI penyedia/cadangan/OAuth — seluruh URL rujukan eksternal tetap dipertahankan).
  - **Modul Longform & Branding (`nav.py`, `components.py`, `sidebar.py`, `constants.py`, `theme.py`, `i18n.py`):** Menghapus tautan berulang *"← MoneyPrinterTurbo (Video Pendek)"* di atas judul halaman Video Panjang (`show_top_return=False`, membiarkan teks fallback `nav_back_mpt` menjadi *"← Video Pendek"* / *"← Short Video"*), membersihkan *"MoneyPrinterTurbo"* dan *"MPT"* dari teks/tooltip/error `i18n.py`, `UPSTREAM_UPDATE_PREFIX`, dan `theme.py`, serta menghapus `"(Aktif)"` / `"(Active)"` dari tooltip sidebar (`nav_long_video_help`, `nav_short_video_help`). Kotak atribusi MIT sidebar dan dialog **Tentang** dipertahankan sesuai aturan masing-masing tahap.
  - **Pemindai Terotomasi & Tangkapan Layar (`test/longform/test_branding.py`):** Menambahkan `test_c6_no_moneyprinter_or_mpt_in_ui_strings` + verifikasi screenshot ID & EN `docs/branding/screens/c6_{long,short}_video_{id,en}.png`.
- **Addendum C7 (Peringkasan Kotak Versi Sidebar & Pemindahan Atribusi ke Dialog Tentang):**
  - **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode baru)**.
  - **Sidebar & Dialog Tentang (`app/services/branding/sidebar.py`, `app/services/longform/ui/i18n.py`):** Mengganti kotak versi/atribusi MIT di sidebar menjadi satu baris klik kecil rata kiri redup `"v1.0.0"` (`efs_btn_version_*`) tepat di atas tombol `"Tentang"` / `"About"` yang membuka dialog Tentang; memindahkan detail versi aplikasi (`v1.0.0`), versi inti MPT (`v1.3.10`), kalimat `"Dibangun di atas MoneyPrinterTurbo oleh harry0703 (MIT License)"` + tautan repo asli, tautan ke `THIRD_PARTY_NOTICES.md`, dan teks lengkap berkas `LICENSE` di dalam expander `"Lihat lisensi"` / `"View license"` (dibaca langsung dari `LICENSE`) ke `_render_about_dialog_body()`; seluruh teks melalui `t()` (ID & EN) serta memperketat pengecualian pemindai C6 agar atribusi hanya berada di dialog Tentang & berkas legal.

---

## HOOK UPSTREAM

Daftar lengkap perubahan yang masih tersisa pada berkas asli upstream `MoneyPrinterTurbo v1.3.10`, alasan masing-masing, serta langkah melepas sementara saat menjalankan *merge/rebase* upstream:

| Berkas Upstream | Baris Saat Ini | Ukuran Diff (`vs v1.3.10`) | Alasan Hook | Cara Melepas Saat Merge Upstream |
|---|---|---|---|---|
| `webui/Main.py` | 79–83 | ~21 baris | `try/except` memanggil `app.services.branding.hooks.init_main_page()` untuk `set_page_config` judul/ikon Studio, injeksi CSS tema gelap, dan sidebar terpadu (skip otomatis saat mode `_efs_settings_library_only`). | Kembalikan blok `st.set_page_config(...)` & `styles.css` upstream saat resolve conflict, lalu sisipkan kembali blok 5 baris `try/except init_main_page(...)`. |
| `webui/Main.py` | 671 | +1 baris | `default_language="id"` pada `utils.resolve_ui_language(...)` agar bahasa bawaan Studio adalah Indonesia. | Tambahkan kembali argumen `default_language="id"` di panggilan `resolve_ui_language`. |
| `webui/Main.py` | 1742–1771 | ~12 baris | `try/except` memanggil `render_brand_header()` untuk header merek *EasyFaceless Studio* + penyesuaian fallback C6 `"EasyFaceless Studio"`. | Pertahankan `_render_brand()` upstream, lalu sisipkan blok 6 baris `try/except render_brand_header(...) return` di awal fungsi. |
| `webui/Main.py` | 9496–9500 | ~5 baris | `try/except` memanggil `hooks.run_main_or_settings(...)` untuk menangani query `?open_settings=` dan mengeksekusi dialog Settings secara aman saat diimpor oleh halaman Video Panjang. | Gantikan panggilan tunggal `_render_application()` di akhir berkas dengan blok `try/except run_main_or_settings(...)` sepanjang 5 baris. |
| `webui/i18n/id.json` | 343, 351, 460 | 6 baris (`3+`, `3-`) | Addendum C6: membersihkan `"MoneyPrinterTurbo"` menjadi `"EasyFaceless Studio"` pada pesan error preset/cadangan kunci dan OAuth ChatGPT. | Terima versi upstream bila perlu lalu jalankan ulang penggantian 3 key tersebut (diverifikasi oleh `test_c6_no_moneyprinter_or_mpt_in_ui_strings`). |
| `webui/i18n/en.json` | 268..737 (12 key) | 24 baris (`12+`, `12-`) | Addendum C6: membersihkan `"MoneyPrinterTurbo"`/`"MPT"` pada prosa tips penyedia & pesan error UI (URL rujukan tetap utuh). | Terima versi upstream lalu bersihkan teks UI non-URL (otomatis terdeteksi oleh `test_c6_no_moneyprinter_or_mpt_in_ui_strings`). |
| `.gitignore` | 38 (dst.) | +7 baris | Memastikan `test/longform/*.py`, `*.backup*.toml`, `*.log`, `**/_trash/`, dan folder uji sementara terkelola dengan benar. | Pertahankan tambahan aturan abaikan di akhir `.gitignore`. |

> **Ringkasan angka `webui/Main.py`:** Turun dari baseline **`97` baris (`67+`, `30-`)** vs `stage-7.9-complete` menjadi **`46` baris (`20+`, `26-`)** vs tag `v1.3.10` (dan `52` baris `20+`, `32-` vs `stage-7.9-complete`). Seluruh file `app/services/*.py`, `app/models/`, `app/config/`, dan `cli.py` memiliki **0 perubahan upstream**.









