# HANDOVER — EasyFaceless Studio (setelah rilis v1.0.1)

Tanggal: 2026-11-24 · Tempel file ini di awal chat baru. Menggantikan `HANDOVER-EasyFaceless-v1.0.0.md`.

## 1. Proyek
- **Nama:** EasyFaceless Studio — fork MoneyPrinterTurbo (harry0703, inti v1.3.10) dengan fitur **video panjang 10–20 menit** (naskah → suara → visual campuran → render MP4 → draft CapCut), AI opsional, installer Windows.
- **Repo:** GitHub `keimam/EasyVideoContent` · lokal **PC kantor `F:\MPTLong`** · remote `upstream` = MoneyPrinterTurbo. Repo pembaruan publik: `keimam/EasyFacelessStudio-updates` (`latest.json`).
- **Stack:** Python + uv, Streamlit, FFmpeg, Inno Setup 6, launcher C# (`packaging/launcher/LauncherStub.cs`).
- **Kode kita:** `app/services/longform/` (inti; visual di `visual/`), `app/services/branding/`, `cli_longform.py`, `webui/pages/1_🎬_Long_Video.py`.
- **Dokumen utama:** `LONGFORM_CHANGES.md` (hook upstream), `CHANGELOG.md`, `docs/user/{id,en}/`, `docs/dev/{BUILD,RELEASE,BENCHMARK,CLEAN_INSTALL_TEST,PORTING_MACOS}.md`, `THIRD_PARTY_NOTICES.md`. Prompt ronde: `prompts/v101/`.

## 2. Cara kerja
- **Antigravity = agent pembangun; Claude = arsitek + reviewer skeptis** (membaca kode langsung lewat folder `F:\MPTLong` bila terhubung). Pengguna (Pencilgon) menjalankan cek manual di aplikasi terpasang.
- **Format ronde (5 bagian):** prompt siap kirim bernomor versi, Tujuan, Hasil yang harus muncul, Cek manual PowerShell satu perintah per baris, Red flags + kriteria lolos. Label **Lolos / Lolos dengan catatan / Perlu revisi**. Revisi ditulis ulang utuh (v2…); addendum (A1, A2…) diberi label "menambah, tidak menggantikan". Jawab ringkas bahasa Indonesia; langkah demi langkah bila meminta pengguna bertindak.
- **PowerShell biasa (bukan admin)**; `npm.cmd`/`npx.cmd`; baca teks `-Encoding UTF8`; JSON tanpa BOM.
- **Aturan agent yang terbukti penting:** uji di aplikasi TERPASANG; bangun installer SETELAH commit terakhir (hash build di About = HEAD); commit per butir tanpa push; Final Report berisi HEAD, pytest verbatim, tabel kepatuhan, deviasi. **Laporan agent = klaim** — verifikasi dari kode/git (contoh nyata V101: klaim "hook sudah dicatat" ternyata belum; angka thread paralel berubah tanpa dilaporkan).

## 3. Status git
- **Tag `v1.0.1` = `11163bf`** (commit yang dibangun jadi installer). **main** = commit "release: set v1.0.1 download url" di atas `11163bf`, sudah di-push (verifikasi: `git ls-remote --tags origin v1.0.1`).
- Branch `stage/v101` sudah di-fast-forward ke main. Tag sebelumnya: `v1.0.0` = `c6865ba`.

## 4. Gate terakhir (V101-A2, 2026-11-24)
- `uv run python -m pytest -q test\longform` → **470 passed**.
- Build `11163bf-20261124-125333`: PACKAGE VERIFY / SMOKE TEST / PROCESS CHECK PASSED.
- Installer `EasyFacelessStudio-Setup-1.0.1.exe`, SHA256 `96A41F9FB233F66D7330BC392A644EBD6B4B4512200405F8893831AF998D5B5C`.
- Cek manual pengguna (aplikasi terpasang, PC kantor): About build 11163bf; proyek Yuki-onna 21 shot selesai 3m 0s (Pixabay 19 · Wikimedia 2); Local files, jembatan Flow, Uji 1 gambar (401 ramah) lolos; `latest.json` publik = 1.0.1.

## 5. Tahap selesai
- **6–8C.3, R1:** lihat handover v1.0.0 (pipeline, WebUI, CapCut, rebrand, AI, installer, wizard, update check).
- **v1.0.0 (2026-11-23):** rilis pertama ke member.
- **V101 (v1.0.1, 2026-11-24):** V0 Local files dinilai dari nama file + isi acak default MATI; V1 mode per proyek Campur terbaik (default proyek baru) / Prioritas (proyek lama), paralel, batas 60% & 3 berturut-turut; V2 tombol folder Local + unggah; V3 jembatan gambar eksternal (ekspor prompt txt/csv, impor massal, terkunci); V4 preset Together/DeepInfra + Uji 1 gambar (Nebius dihapus: text-to-image mereka mati sejak 13 Apr 2026); V5 versi 1.0.1, CapCut 9.7.1.
- **V101-A1:** kalibrasi skor sumber berbasis judul (Wikimedia/Openverse/NASA), dedup kandidat + thumbnail asli, pemulihan JSON kata kunci AI, bug pindah proyek, perapian UI, LONGFORM_CHANGES.
- **V101-A2:** telemetri per fase (`visual_metrics.json`), pemutus sumber error (timeout 6s, jeda 120s setelah 2 gagal), timer "Selesai dalam …", perbaikan Uji 1 gambar.

## 6. Sedang berjalan
- Rilis v1.0.1 sudah terbit (Drive + `latest.json`). **Belum:** uji upgrade v1.0.0 → v1.0.1 di PC rumah (banner muncul, proyek lama utuh), lalu video members-only.
- Belum ada branch/prompt v1.0.2.

## 7. Keputusan desain yang mengikat
- File upstream diubah seminimal mungkin (hook try/except), dicatat di LONGFORM_CHANGES.md → merge upstream mudah.
- Manifest proyek = satu-satunya sumber status; simpan atomik + validasi. Mode pencarian disimpan per proyek.
- Relevansi di atas keragaman: batas 60%/3-berturut hanya berlaku bila sumber lain punya kandidat lolos ambang.
- Local files hanya dipakai bila nama file cocok; isi acak opsional, default MATI.
- Tidak ada key bawaan; preset tidak menimpa key pengguna.
- Sumber yang error dilewati sementara (pemutus), bukan dimatikan permanen.
- Penyedia `fake` hanya di mode tes (`EFS_DEV=1`). Data pengguna `%LOCALAPPDATA%\EasyFaceless Studio`, aplikasi `%LOCALAPPDATA%\Programs\EasyFaceless Studio`; installer non-admin.
- Distribusi: installer di Google Drive (link di video members-only); `latest.json` publik diunggah TERAKHIR (lihat `docs/dev/RELEASE.md`).
- Lisensi MIT; FFmpeg GPLv3; jangan bundel kode AGPL; tanpa otomasi situs pihak ketiga (Flow lewat ekspor/impor).

## 8. Utang & risiko terbuka (→ v1.0.2 kecuali disebut lain)
1. **Coverr** hampir semua request ditolak 403 (45/46 di uji A2) dan tetap dicoba tiap shot (±40 s terbuang) → berhenti setelah 403 pertama; cek kuota key Coverr.
2. **Sumber dilewati tanpa pemberitahuan** (mis. Openverse saat 504) → tampilkan pesan di langkah Visual.
3. **Timeout 6s + jeda setelah 2 gagal** bisa melewati Wikimedia/NASA di koneksi lambat member → uji di PC rumah, pertimbangkan bisa diatur.
4. **Thread paralel 6** (spesifikasi ≤4) — deviasi tidak dilaporkan agent; pantau rate limit.
5. **Satu kolom API key gambar** untuk semua preset → simpan key per preset.
6. **LONGFORM_CHANGES** belum mencatat 3 hook lama Main.py (pemilih bahasa, kontrol studio Settings, tour onboarding) → commit dokumen.
7. **Daftar kata generik/arsip-militer** di `stock.py` disetel dari 2 kasus uji → pantau di niche lain (penalti dilewati bila kata itu ada di query).
8. **Uji 1 gambar dengan key asli** belum pernah dilakukan (hanya 401).
9. Lama: dokumentasi pengguna pendek; installer belum ditandatangani (SmartScreen); Pexels tak terbitkan key baru; OpenRouter gratis 50 request/hari; port macOS belum dibangun.

## 9. Langkah berikut
1. Uji upgrade di PC rumah (v1.0.0 → 1.0.1 dari link Drive), lalu unggah video members-only (link, SHA256, `packaging/release/notes/1.0.1.id.md`).
2. Kumpulkan masukan member beberapa hari.
3. Mulai v1.0.2: branch `stage/v102` dari main; isi = utang 1–6 + backlog lama (sumber arsip NYPL/Library of Congress/Europeana setelah verifikasi API & lisensi; TTS lokal VoiceStudio; FreeVideo bila GPU ≥8 GB).
