# Panduan Rilis & Cek Pembaruan Aplikasi (Stage 8C.3, E2 / E7 / R1)

Dokumen ini menjelaskan alur rilis resmi **EasyFaceless Studio**, pembuatan `latest.json`, verifikasi SHA-256, serta pengujian pembaruan di aplikasi.

---

## 1. URL Produksi (`UPDATE_MANIFEST_URL`) & Format `latest.json`

Pada [`app/services/branding/constants.py`](../../app/services/branding/constants.py), `UPDATE_MANIFEST_URL` terhubung ke manifest publik resmi:

```text
https://raw.githubusercontent.com/keimam/EasyFacelessStudio-updates/main/latest.json
```

Bila manifest di repo publik belum terbit atau belum diperbarui (HTTP 404), startup check gagal secara senyap dan tombol **Cek pembaruan sekarang** menampilkan pesan ramah **"Informasi versi belum tersedia"** sementara aplikasi tetap berjalan normal.

Contoh dan berkas rilis:
- [`packaging/release/latest.example.json`](../../packaging/release/latest.example.json) — contoh format untuk versi berikutnya.
- [`packaging/release/latest.json`](../../packaging/release/latest.json) — manifest rilis aktif yang dihasilkan oleh `scripts/build/make_latest_json.py`.

---

## 2. Langkah Rilis Berurutan (Wajib Terurut!)

> [!IMPORTANT]
> **Unggah installer ke Google Drive DULU, unggah `latest.json` TERAKHIR.** Jangan pernah memperbarui `latest.json` sebelum link Google Drive benar-benar siap dan dapat diakses, agar member tidak mendapat link yang belum ada atau rusak.

1. **Pastikan kode & dokumen sudah di-commit bersih:** `git status -s` harus kosong (`build_installer.ps1` menolak working tree kotor kecuali memakai `-AllowDirty` untuk uji lokal).
2. **Bangun installer dari commit terakhir:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/build/build_installer.ps1
   ```
   Verifikasi hasil di folder `dist\`:
   - `EasyFacelessStudio-Setup-<versi>.exe` (installer non-admin, tanpa peringatan `UPDATE_MANIFEST_URL` kosong)
   - `EasyFacelessStudio-Setup-<versi>.exe.sha256`
   - `RELEASE-NOTES-<versi>.md` (Bahasa Indonesia di awal untuk deskripsi video members-only + English)
   Pastikan hash commit di footer build & `RELEASE-NOTES` sama persis dengan `git rev-parse --short HEAD`.
3. **Buat tag git rilis:**
   ```powershell
   git tag -a v<versi> -m "Release EasyFaceless Studio v<versi>"
   ```
4. **Siapkan catatan rilis ramah member untuk banner pembaruan (`packaging/release/notes/`):**
   - Tulis `packaging/release/notes/<versi>.id.md` dan `packaging/release/notes/<versi>.en.md` (1–3 kalimat ringkas dengan bahasa pengguna, sejalan antara `id` dan `en`, **tanpa** kode tahap/butir internal seperti *Tahap*, `R1.1`, `E12`, atau `D9`).
5. **Unggah installer ke Google Drive (DULU):**
   - Unggah `dist\EasyFacelessStudio-Setup-<versi>.exe` dan `.sha256` ke folder Google Drive khusus rilis.
   - Atur berbagi akses ke **Siapa saja yang memiliki link → Pelihat (*Anyone with the link: Viewer*)**.
   - Salin tautan Google Drive dan uji buka dari jendela Incognito.
6. **Buat & validasi `latest.json` memakai link Google Drive:**
   ```powershell
   uv run python scripts/build/make_latest_json.py --download-url "<LINK_GOOGLE_DRIVE>"
   ```
   Skrip ini membaca [`packaging/release/notes/<versi>.id.md`](../../packaging/release/notes/1.0.0.id.md) & `.en.md` serta `APP_VERSION` dari `constants.py`, menolak pola internal bila ada, lalu menghasilkan [`packaging/release/latest.json`](../../packaging/release/latest.json) dengan encoding UTF-8 tanpa BOM.
7. **Unggah `latest.json` ke repo publik `keimam/EasyFacelessStudio-updates` (TERAKHIR):**
   - Push `latest.json` hasil langkah 6 ke branch `main` di repo `https://github.com/keimam/EasyFacelessStudio-updates`.
8. **Verifikasi langsung di aplikasi:**
   - Buka aplikasi terpasang → menu **Tentang / About** → klik **Cek pembaruan sekarang**. Pastikan hasil menampilkan informasi versi/pembaruan tanpa error jaringan atau 404.

---

## 3. Menguji Banner Pembaruan Secara Lokal (`EFS_UPDATE_URL`)

Gunakan protokol `file:///` (Windows: tiga garis miring + forward slash) untuk menimpa `UPDATE_MANIFEST_URL` tanpa menyentuh server publik:

```powershell
$env:EFS_UPDATE_URL = "file:///C:/temp/latest_test.json"
$env:EFS_DATA_DIR = "C:\temp\efs_test_data"
uv run streamlit run webui/Main.py
```
