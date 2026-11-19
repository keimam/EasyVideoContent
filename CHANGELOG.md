# Changelog — EasyFaceless Studio

Seluruh rilisan versi inti EasyFaceless Studio berakar dari satu konstanta `APP_VERSION` pada `app/services/branding/constants.py`.

## [1.0.0] — Rilis Studio 1.0.0 (Tahap 8C.1 – 8C.3)

### Tahap 8C.3 (Wizard Pertama Kali, Cek Pembaruan & CapCut, Benchmark, Rilis)
- **Wizard Pengaturan Pertama Kali (`E1`):** Muncul otomatis bila folder data belum memiliki penanda selesai (`.efs_setup_complete.json`), dapat dilewati atau dibuka kembali dari **Pengaturan → Jalankan wizard lagi**; terdiri dari 6 langkah (Bahasa tampilan, Cek sistem hijau/kuning/merah, AI OpenRouter dengan tes koneksi & cek kuota, Sumber footage gratis + opsional key, Folder & versi CapCut, serta pembuatan proyek contoh kecil); launcher dan aplikasi menghormati `EFS_DATA_DIR`.
- **Cek Versi Aplikasi (`E2`):** Konstanta tunggal `UPDATE_MANIFEST_URL` (dapat ditimpa env `EFS_UPDATE_URL` termasuk `file:///`), cek otomatis latar belakang maksimal 1× per 24 jam (diam bila offline), banner pembaruan dengan tombol **Unduh**, **Nanti**, dan **Lewati versi ini**, serta kontrol di Pengaturan (**Cek pembaruan otomatis** & **Cek pembaruan sekarang**). Contoh manifest di `packaging/release/latest.example.json` dan panduan di `docs/dev/RELEASE.md`.
- **Deteksi Versi CapCut Desktop (`E3`):** Pembacaan `ProductInfo.xml` via `platform_utils` (plus stub `NotImplementedError` terstruktur untuk macOS), daftar `tested_versions = ["8.9.0"]` di `config/longform.toml`, serta tampilan status `CapCut 8.9.0 — teruji ✓` atau peringatan versi belum teruji / panduan unduh tanpa memblokir ekspor.
- **Benchmark & Kalibrasi Kebutuhan Sistem (`E4`):** Penambahan `scripts/dev/benchmark.py` beserta laporan nyata proyek ~9–10 menit di `docs/dev/BENCHMARK.md` dan penyelarasan ambang spesifikasi di `README.md`, panduan pengguna, dan Wizard.
- **Akses Hasil & Atribusi (`E5`):** Tombol **Buka folder output** dan dialog penampil **Buka credits.md** (termasuk tombol salin & unduh) pada kartu proyek selesai dan tampilan Render.
- **Uji Windows Bersih & Rilis (`E6`–`E7`):** Konfigurasi Windows Sandbox (`packaging/sandbox/efs-test.wsb`) + panduan VM (`docs/dev/CLEAN_INSTALL_TEST.md`), serta pembuatan otomatis berkas `.sha256` dan `RELEASE-NOTES-<versi>.md` di `dist/` oleh `build_installer.ps1`.
- **Addendum `E8` (Keandalan Startup, Tur Terbatas, Disk 15 GB & Smoke Test):** Menyalakan `PYTHONUNBUFFERED=1` agar `logs/server.log` tidak kosong, mengeraskan hook branding & inisialisasi `EFS_DATA_DIR` agar tidak tertelan diam-diam pada data dir kosong, membatasi tur Video Pendek agar hanya tampil pasca-wizard tanpa menumpuk di atas dialog dan memakai string `t()`, menaikkan ambang minimal disk ke `15 GB` (`20 GB+ SSD` disarankan), serta menambahkan `--smoke` pada `verify_package.py` untuk menguji aplikasi terpasang pada `EFS_DATA_DIR` kosong dan berisi saat build installer.

### Tahap 8C.2 (Paket Windows, Sumber Footage Gratis & Perbaikan D8–D9.7)
- **Installer Windows & Runtime Offline (`D0`–`D7`):** Pemilik lisensi (`Naseh Ngulik`), launcher native WinExe tanpa jendela konsol, isolasi folder data pengguna (`%LOCALAPPDATA%\EasyFaceless Studio` / `EFS_DATA_DIR`), abstraksi lintas-platform terpusat di `platform_utils.py`, bundel Python 3.11 + `uv.lock` + FFmpeg + notice GPLv3, serta installer Inno Setup non-admin yang menjaga data saat upgrade.
- **Bebas Placeholder Fake & Validasi Manifest (`D8`, `D8.1`):** Penyedia `fake` dibatasi hanya untuk mode dev/tes (`EFS_DEV=1` / `pytest`), shot gagal ditandai `"Belum ada visual"` secara bersih, serta skema `Shot` nullable dan simpan atomik tervalidasi agar proyek lama maupun berstatus kosong selalu termuat aman.
- **Sumber Footage Gratis Tanpa Key & Penyatuan Pencarian (`D9`–`D9.7`):** Menambahkan Wikimedia Commons, NASA Images Library, dan Openverse Photos aktif bawaan tanpa API key dengan filter lisensi komersial ketat serta pencatatan otomatis ke `export/credits.md`; menyatukan strategi pencarian bertingkat dan relevansi narasi di antara fetch massal, Otomatis, dan *"Cari lagi"*.

### Tahap 8C.1 (Perapian Repo, Hook Minimal, Dokumentasi & Lisensi)
- **Tahap 8C.1 (C1–C5):**
  - Memindahkan logika inisialisasi halaman, pembungkus dialog Pengaturan, serta parameter kueri dari `webui/Main.py` ke `app/services/branding/hooks.py`, memangkas diff upstream `webui/Main.py` secara signifikan.
  - Merapikan set screenshot akhir di `docs/branding/screens/final/` (ID & EN), memindahkan skrip pengembang ke `scripts/dev/`, menghapus dokumen duplikat `STAGE7_WEBUI.md` di root, melengkapi `.gitignore` (`*.backup*.toml`, `*.log`, `**/_trash/`, folder temp), serta membersihkan path Windows pribadi dari dokumentasi & tes.
  - Menyusun dokumentasi pengguna dwibahasa di `docs/user/id/` dan `docs/user/en/`, `README.md` baru, dialog **Bantuan** dwibahasa di aplikasi, `THIRD_PARTY_NOTICES.md` berbasis generator environment, pembaruan `LICENSE` MIT, serta `CHANGELOG.md`.
- **Addendum C6 & C7:**
  - Membersihkan `"MoneyPrinterTurbo"` dan `"MPT"` dari teks UI, judul halaman, tooltip, dan tombol (termasuk menghapus tautan redundan di atas judul Video Panjang dan akhiran `"(Aktif)"` pada tooltip sidebar), serta menambahkan pemindai C6 otomatis.
  - Meringkas kotak atribusi sidebar menjadi satu baris klik kecil `"v1.0.0"` dan memindahkan atribusi lengkap + isi berkas `LICENSE` + tautan `THIRD_PARTY_NOTICES.md` ke dialog **Tentang / About**.

## Tahap 8B.2 (F1–F15 — Fitur AI Opsional & Penyempurnaan UX Visual/Progres)
- **F1–F5:** Cek relevansi footage berbasis *vision* dengan cache lokal & retry 429; terjemahan kata kunci ke English per bab; **Video AI** per shot & batch *"Perlu dicek"* dengan estimasi tarif, konfirmasi biaya wajib, dan batas anggaran USD; penyempurnaan tes koneksi & default model OpenRouter aman (`openrouter/free`).
- **F6–F11:** Tes koneksi tahan model reasoning (`finish_reason=length` retry); hapus proyek aman ke **Tempat Sampah** (`_trash/`) termasuk saat job berjalan beserta pilihan hapus draft CapCut terpasang; pembuat kata kunci visual konkret AI per shot; guard & perbaikan otomatis judul bab menempel pada narasi; petunjuk tombol vision saat model tidak mendukung gambar.
- **F12–F15:** Jangkar visual topik (`anchor_terms`, `avoid_terms`, `visual_summary`) + eskalasi query stok `<3` kandidat + filter tag tab Kandidat + catatan *"Ditemukan dengan"*; satu sumber kebenaran klip shot (`rendered_clip`) dengan cache thumbnail berbasis hash isi & badge **"Perlu disusun ulang"**; progres akurat terlingkup target unit kerja (memulai dari `0%`, menghitung `dilewati: N (sudah valid/terkunci)`, indeterminate <=95%, mencapai `100%` hanya saat selesai).

## Tahap 8B.1 & Addendum B3 (Penyatuan Penyedia AI)
- Menyatukan LLM (44 penyedia), TTS, Gambar AI, dan Video AI Video Panjang dengan konfigurasi tunggal MPT melalui adapter terpadu tanpa penyimpanan key ganda.
- Penjaga bahasa naskah otomatis (Indonesia/English) dan perbaikan ulang per bab.
- Dialog Pengaturan native dibuka langsung pada halaman Video Panjang aktif tanpa keluar proyek.

## Tahap 8A.1 & 8A.2 (Identitas Studio & Antarmuka Dwibahasa)
- Membangun identitas visual gelap **EasyFaceless Studio**, tipografi offline **Plus Jakarta Sans**, sidebar navigasi seragam (`Video Panjang`, `Video Pendek`, `Kelola Niche`, `API & Penyedia AI`, `Cache & Penyimpanan`), serta tata letak Beranda 3 kolom & langkah kerja proyek dwibahasa (`id` & `en`).

## Tahap 7 & 7.9 (WebUI Video Panjang & Sinkronisasi Upstream v1.3.10)
- Antarmuka multipage `webui/pages/1_🎬_Long_Video.py`, pelari tugas latar belakang aman terhadap *refresh* (`job_state.json`, pembatalan bersih `CTRL_BREAK_EVENT`), editor bab, pratinjau suara, galeri visual 3-kolom, serta penggabungan bersih upstream `v1.3.10`.

## Tahap 0 – 6B (Mesin Pipa Video Panjang & Ekspor CapCut)
- **Tahap 0–1:** Audit lingkungan Windows berpath spasi, model proyek `manifest.json`, sistem preset & sub-niche, serta pelacakan hash inkremental (`hashing.py`).
- **Tahap 2–3:** Penulisan/impor naskah multi-bab, pemeriksaan kualitas narasi, sintesis suara paralel dengan word cue & subtitle ASS/SRT (`voice_service.py`).
- **Tahap 4–5:** Pencarian & scoring visual multi-sumber (`Pixabay`, `Pexels`, `Coverr`, `Local`, `Gambar AI`, `Video AI`), builder klip FFmpeg, peredupan musik latar (BGM ducking), normalisasi loudness, dan render bab/final paralel-aman.
- **Tahap 6A–6B:** Reverse-engineering format CapCut 8.9.0, builder draft portabel multi-trek audio/video/teks selaras, deteksi proses CapCut aktif, serta fitur kalibrasi ukuran teks.
