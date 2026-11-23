# HANDOVER — EasyFaceless Studio (setelah rilis v1.0.0)

Tanggal: 2026-11-23 · Tempel file ini di awal chat baru.

## 1. Proyek
- **Nama:** EasyFaceless Studio — fork MoneyPrinterTurbo (harry0703, inti v1.3.10) dengan fitur **video panjang 10–20 menit** (naskah → suara → visual campuran → render MP4 → draft CapCut), AI opsional, installer Windows.
- **Repo:** GitHub `keimam/EasyVideoContent` · lokal **PC kantor `F:\MPTLong`** · remote `upstream` = MoneyPrinterTurbo.
- **Stack:** Python + uv, Streamlit (WebUI), FFmpeg, Inno Setup 6 (installer), launcher C# (`packaging/launcher/LauncherStub.cs`).
- **Kode kita:** `app/services/longform/` (inti video panjang), `app/services/branding/` (merek, sidebar, i18n, wizard, update), `cli_longform.py`, `webui/pages/1_🎬_Long_Video.py`.
- **Dokumen utama:** `LONGFORM_CHANGES.md` (bagian HOOK UPSTREAM), `CHANGELOG.md`, `README.md`, `docs/user/{id,en}/`, `docs/dev/{BUILD,RELEASE,BENCHMARK,CLEAN_INSTALL_TEST,PORTING_MACOS}.md`, `THIRD_PARTY_NOTICES.md`.

## 2. Cara kerja
- **Antigravity = agent pembangun; Claude = arsitek + reviewer skeptis.** Pengguna (Pencilgon) menjalankan cek manual di Windows.
- **Format tiap ronde (5 bagian):** (1) prompt siap kirim bernomor versi, (2) Tujuan, (3) Hasil yang harus muncul, (4) Cek manual PowerShell satu perintah per baris, (5) Red flags + kriteria lolos. Label: **Lolos / Lolos dengan catatan / Perlu revisi**. Revisi ditulis ulang utuh (v2, v3…); addendum diberi label "menambah, tidak menggantikan". Jawab ringkas bahasa Indonesia; jelaskan langkah demi langkah bila meminta pengguna melakukan sesuatu.
- **PowerShell:** pakai PowerShell **biasa (bukan admin)** untuk aplikasi/pytest; `npm.cmd`/`npx.cmd`; baca teks `-Encoding UTF8`; tulis JSON tanpa BOM.
- **Aturan agent yang terbukti penting:** uji dengan **aplikasi terpasang** (bukan hanya source); **bangun ulang installer SETELAH commit terakhir** (hash build di About = HEAD); commit di akhir tanpa push; Final Report berisi HEAD, pytest verbatim, tabel kepatuhan, deviasi.

## 3. Status git
- **main = `33112bf`** ("release: set v1.0.0 download url"), sudah di-push.
- **Tag `v1.0.0` = `c6865ba`** (commit yang dibangun jadi installer).
- Tag tahap: `stage-6-complete` … `stage-8c1-complete`, `stage-8c2-complete`, `stage-8c3-complete`.
- Branch `stage/r1-release` sudah di-merge (fast-forward). Belum ada branch V101.

## 4. Gate terakhir (R1.7, 2026-11-20)
- `uv run python -m pytest -q test\longform` → **439 passed**.
- Build: `[PACKAGE VERIFY PASSED]`, `[SMOKE TEST PASSED]`, `[PROCESS CHECK PASSED]`.
- Tes upstream: 2 gagal **sudah ada sebelum 8C.1** (symlink WinError 1314 butuh Developer Mode; color picker karena config lokal) — bukan regresi.

## 5. Tahap selesai
- **6–7.9:** pipeline long video, WebUI, job runner, CapCut 8.9.0, preset/niche, sinkron upstream v1.3.10.
- **8A:** rebrand EasyFaceless + UI Streamlit, i18n ID/EN.
- **8B:** penyedia AI (OpenRouter dll.), kata kunci/relevansi AI, AI video, Tempat Sampah.
- **8C.1:** hook upstream minimal (diff Main.py ≈52 baris), dokumentasi ID/EN, LICENSE + THIRD_PARTY_NOTICES, versi 1.0.0; C6–C8 (nama MPT dari UI, dialog Tentang, Bantuan dalam aplikasi, error AI jelas, hemat kuota, model reasoning).
- **8C.2:** installer Inno Setup, launcher tanpa konsol, folder data terpisah, platform_utils, lisensi FFmpeg GPL; D8–D9.7 (tanpa placeholder fake, manifest atomik, sumber gratis Wikimedia/NASA/Openverse, credits.md, installer non-admin).
- **8C.3:** wizard pertama kali, cek versi + banner, deteksi versi CapCut, benchmark, E8–E12 (crash sub-halaman, bahasa berkedip, server lama saat upgrade, hash build).
- **R1/R1.7:** URL produksi, `make_latest_json.py`, catatan rilis member, pembersihan proses build.
- **Rilis v1.0.0 (2026-11-23):** uji PC bersih lolos (PC rumah, CapCut 9.7.1); installer di folder Google Drive; `latest.json` di repo publik `keimam/EasyFacelessStudio-updates`; aplikasi menampilkan "latest version (v1.0.0)".

## 6. Sedang berjalan
- Pengguna sedang membagikan v1.0.0 ke member (video members-only: link Drive, catatan rilis ID, SHA256 `8FF58E7FD980B598A0F743520085086CA6CFCFCF879E2E8D86A95B3325948C57`).
- **V101 belum dimulai.** Prompt siap kirim: `prompts/v101/V101-v1.md` (lampiran handover ini).

## 7. Keputusan desain yang mengikat
- Perubahan file upstream seminimal mungkin (hook try/except), dicatat di LONGFORM_CHANGES.md → merge upstream mudah.
- Manifest proyek = satu-satunya sumber status; simpan atomik + validasi sebelum menulis.
- Penyedia `fake` hanya aktif di mode tes/dev (`EFS_DEV=1`).
- Data pengguna di `%LOCALAPPDATA%\EasyFaceless Studio`, aplikasi di `%LOCALAPPDATA%\Programs\EasyFaceless Studio`; mode source tetap memakai lokasi repo. Uji pakai `EFS_DATA_DIR` dan `EFS_UPDATE_URL`.
- Installer tanpa hak admin; aplikasi tidak pernah berjalan elevated; file watcher Streamlit dimatikan di mode terpasang.
- Distribusi: installer di Google Drive (link di video members-only); `latest.json` publik di `raw.githubusercontent.com/keimam/EasyFacelessStudio-updates/main/latest.json`. Urutan rilis: unggah installer dulu, `latest.json` terakhir.
- Lisensi MIT (Copyright Harry + Naseh Ngulik); FFmpeg GPLv3 dicantumkan; jangan membundel kode AGPL (mis. VoiceStudio) — integrasi hanya lewat API lokal terpisah.
- Tanpa otomasi situs pihak ketiga (mis. Google Flow) — pakai jembatan ekspor prompt / impor massal.

## 8. Utang & risiko terbuka
1. Pixabay mendominasi footage (pencarian berhenti di sumber pertama) → **V101**.
2. Local files tidak terdokumentasi; folder `<proyek>\assets\visual\library` tidak mudah ditemukan → **V101**.
3. Langkah Visual lambat (±25 dari 30 menit untuk video ±9 menit, benchmark i3/8 GB) → setelah V101.
4. Dokumentasi pengguna masih pendek → bertahap dari masukan member.
5. Installer belum ditandatangani (SmartScreen) → nanti.
6. Pexels tidak menerbitkan key baru; OpenRouter gratis 50 request/hari (1.000 setelah top-up $10).
7. Port macOS belum dibangun (persiapan di `PORTING_MACOS.md`).

## 9. Langkah berikut
1. Di PC kantor: `git checkout -b stage/v101`, lalu kirim `prompts/v101/V101-v1.md` ke agent.
2. Nilai laporan V101, jalankan cek manual, rilis v1.0.1 dengan alur RELEASE.md.
3. Backlog setelahnya: v1.0.2 sumber arsip sejarah (NYPL, Library of Congress, Europeana — verifikasi API & lisensi); TTS lokal VoiceStudio (setelah uji manual kualitas ID & kecepatan CPU); FreeVideo (tunggu GPU ≥8 GB + cek lisensi MiniMax H3); Muapi (hanya bila harga menarik).
