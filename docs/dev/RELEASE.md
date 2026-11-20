# Panduan Rilis & Cek Pembaruan Aplikasi (Stage 8C.3, E2 / E7)

Dokumen ini menjelaskan cara menerbitkan rilis baru **EasyFaceless Studio**, membuat berkas `latest.json`, menghitung SHA-256 checksum, serta menguji mekanisme cek pembaruan secara lokal.

---

## 1. Konfigurasi URL Produksi (`UPDATE_MANIFEST_URL`) & Format `latest.json`

Pada [`app/services/branding/constants.py`](../../app/services/branding/constants.py), `UPDATE_MANIFEST_URL` dibiarkan **kosong (`""`) sebagai placeholder** agar rilis dev/fork tidak memanggil URL pihak ketiga dan tidak memunculkan error 404 saat cek otomatis:

- Selama `UPDATE_MANIFEST_URL` kosong (dan `EFS_UPDATE_URL` tidak disetel), **cek pembaruan otomatis dinonaktifkan secara diam-diam**. Skrip [`build_installer.ps1`](../../scripts/build/build_installer.ps1) juga menampilkan **PERINGATAN** saat membangun installer jika `UPDATE_MANIFEST_URL` masih kosong.
- Sebelum menerbitkan installer produksi resmi, isi `UPDATE_MANIFEST_URL` dengan URL HTTPS berkas `latest.json` milik Anda sendiri (mis. GitHub Releases / CDN statis).

Salin contoh di [`packaging/release/latest.example.json`](../../packaging/release/latest.example.json) ke server statis/GitHub Release sesuai `UPDATE_MANIFEST_URL`:

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

- Satu fungsi sumber kebenaran URL: `update_checker.get_update_manifest_url()` (`EFS_UPDATE_URL` bila disetel, selain itu `UPDATE_MANIFEST_URL`). Setiap pengecekan dicatat ke `logs/server.log` dengan `url`, `local_version`, `remote_version`, dan `decision`.
- Berjalan **sinkron singkat saat startup** (`<= 2.0` detik, maks. 1× per 24 jam) bila URL terkonfigurasi sehingga banner langsung muncul tanpa perlu klik tambahan.
- Bila ada versi lebih baru:
  - Banner tampil di bagian atas semua halaman (**Video Panjang**, detail proyek, **Video Pendek**, serta dialog **Bantuan**) dengan tombol **Unduh Pembaruan**, **Nanti** (sembunyikan sesi ini), dan **Lewati versi ini** (permanen per versi).
  - Penanda kecil **`Pembaruan tersedia (v<versi>)`** tampil di sidebar tepat di bawah `v1.0.0` dan membuka dialog catatan rilis.
- Di **Pengaturan → Umum** dan **Tentang**, pengguna dapat menyalakan/mematikan cek otomatis serta menekan **Cek pembaruan sekarang** (menampilkan hasil tersedia, sudah terbaru, atau pesan gagal ramah + expander detail teknis).

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
