# Panduan Rilis & Cek Pembaruan Aplikasi (Stage 8C.3, E2 / E7)

Dokumen ini menjelaskan cara menerbitkan rilis baru **EasyFaceless Studio**, membuat berkas `latest.json`, menghitung SHA-256 checksum, serta menguji mekanisme cek pembaruan secara lokal.

---

## 1. Format Berkas `latest.json`

Salin contoh di [`packaging/release/latest.example.json`](../../packaging/release/latest.example.json) ke server statis/GitHub Release (sesuai `UPDATE_MANIFEST_URL` pada [`app/services/branding/constants.py`](../../app/services/branding/constants.py)):

```json
{
  "version": "1.0.1",
  "released": "2026-12-01",
  "notes_id": "Catatan rilis berbahasa Indonesia.",
  "notes_en": "Release notes in English.",
  "download_url": "https://github.com/<org>/<repo>/releases/download/v1.0.1/EasyFacelessStudio-Setup-1.0.1.exe",
  "min_supported": "1.0.0"
}
```

### Penjelasan Field
- **`version`** *(wajib)*: Versi semver rilis terbaru (contoh `"1.0.1"`). Bandingannya selalu terhadap `APP_VERSION` di `constants.py`.
- **`released`**: Tanggal rilis (`YYYY-MM-DD`).
- **`notes_id` / `notes_en`**: Ringkasan perubahan yang ditampilkan pada banner pembaruan sesuai bahasa tampilan aktif.
- **`download_url`**: URL halaman unduhan atau installer yang dibuka di browser default saat pengguna menekan tombol **Unduh**. Tidak ada pengunduhan atau pemasangan otomatis di latar belakang.
- **`min_supported`** *(opsional)*: Versi minimum yang masih didukung format proyek/konfigurasinya.

---

## 2. Perilaku Cek Pembaruan di Aplikasi

- Berjalan **otomatis sekali per 24 jam** saat aplikasi dibuka di thread latar belakang dengan timeout singkat (`3.5` detik).
- Bila komputer sedang **offline** atau manifest gagal diakses, proses berhenti **diam-diam** (hanya dicatat ke log, tanpa pop-up galat mengganggu pengguna).
- Bila ada versi lebih baru: muncul banner berisi versi baru, catatan perubahan, tombol **Unduh**, **Nanti** (tutup di sesi ini), dan **Lewati versi ini** (menyimpan `skipped_version` ke `<data_dir>/.efs_update_state.json`).
- Di **Pengaturan**, pengguna dapat mematikan cek otomatis atau menekan **Cek pembaruan sekarang**.

---

## 3. Menguji Banner Pembaruan Secara Lokal (`EFS_UPDATE_URL`)

Anda dapat menimpa URL manifest dengan berkas `latest.json` lokal menggunakan protokol `file:///` (Windows: gunakan tiga garis miring dan forward slash):

```powershell
$env:EFS_UPDATE_URL = "file:///C:/temp/latest_test.json"
$env:EFS_DATA_DIR = "C:\temp\efs_test_data"
uv run streamlit run webui/Main.py
```

---

## 4. Mempersiapkan Artefak Rilis Windows

1. Perbarui `APP_VERSION` pada `app/services/branding/constants.py` dan tambahkan riwayat perubahan di [`CHANGELOG.md`](../../CHANGELOG.md) dengan judul `## [<versi>]`.
2. Jalankan skrip build installer:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/build/build_installer.ps1
   ```
3. Skrip build otomatis menghasilkan tiga berkas di folder `dist\`:
   - `EasyFacelessStudio-Setup-<versi>.exe` — Installer Inno Setup per-user (tidak menghapus/menimpa data pengguna di `%LOCALAPPDATA%\EasyFaceless Studio`).
   - `EasyFacelessStudio-Setup-<versi>.exe.sha256` — Checksum SHA-256 resmi installer.
   - `RELEASE-NOTES-<versi>.md` — Catatan rilis yang diekstrak dari `CHANGELOG.md`.
