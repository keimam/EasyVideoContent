# Panduan Build Paket & Installer Windows (Stage 8C.2, D1–D7)

Dokumen ini menjelaskan cara membangun paket Windows **EasyFaceless Studio** (`dist/EasyFacelessStudio-Setup-<versi>.exe`) dari repositori sumber yang bersih dengan satu perintah.

---

## 1. Prasyarat Sistem Build

Pastikan alat berikut sudah terpasang di mesin build Windows 10/11 (64-bit):

1. **Python 3.11 + `uv` dengan lingkungan `.venv` terpasang:**
   ```powershell
   uv sync
   ```
   Seluruh dependensi Python (`uv.lock`) akan dibundel langsung ke folder `runtime\python\` tanpa memerlukan internet atau `uv sync` pada komputer pengguna akhir.

2. **FFmpeg & FFprobe (Windows 64-bit):**
   - Pastikan `ffmpeg.exe` dan `ffprobe.exe` tersedia di `PATH` (mis. `C:\ffmpeg\bin\`), atau berikan parameter `-FFmpegDir "C:\ffmpeg\bin"`.
   - Build yang dibundel saat ini memakai **FFmpeg GPLv3** (`gyan.dev`), sehingga lisensi `licenses/FFMPEG-GPLv3-LICENSE.txt` & info build `licenses/FFMPEG-BUILD-INFO.txt` wajib ikut terpaket.

3. **Inno Setup 6 (`ISCC.exe`):**
   Pasang lewat `winget` (tidak membutuhkan hak administrator):
   ```powershell
   winget install JRSoftware.InnoSetup
   ```
   Lokasi standar: `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` atau `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`.

4. **Windows `.NET Framework 4` C# Compiler (`csc.exe`):**
   Tersedia bawaan di setiap Windows 10/11 pada `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe` (digunakan untuk mengompilasi `EasyFaceless Studio.exe` mode GUI/tanpa jendela konsol).

---

## 2. Daftar Periksa Pra-Build

1. Pastikan file [`LICENSE`](../../LICENSE) tidak lagi berisi placeholder `[NAMA PEMILIK]` (skrip build akan mencetak **peringatan** jika placeholder masih ditemukan).
2. Pastikan nilai `APP_NAME`, `APP_VERSION`, dan `APP_OWNER` di [`app/services/branding/constants.py`](../../app/services/branding/constants.py) sudah benar — nilai tersebut menjadi sumber tunggal bagi dialog *Tentang*, metadata executable, dan skrip Inno Setup.

---

## 3. Menjalankan Build Satu Perintah

Jalankan dari akar repositori:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build/build_installer.ps1
```

Opsional (bila `ffmpeg` atau `ISCC.exe` di lokasi khusus):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build/build_installer.ps1 -FFmpegDir "C:\ffmpeg\bin"
```

Tahapan otomatis yang dijalankan:
1. Memeriksa `LICENSE` terhadap `[NAMA PEMILIK]`.
2. Membuat ikon multi-resolusi `resource/branding/app.ico` dari `logo.png`.
3. Menyalin kode bersih ke `build/staging/EasyFaceless Studio/` (tanpa `config.toml`, `storage/`, `_trash`, `.git`, `test/`, `docs/branding/screens/`, `*.backup*`, atau log).
4. Menyalin Python 3.11 relocatable + `site-packages` dari `.venv` ke `runtime/python/` (mengganti `_editable_*.pth` dengan jalur relatif `..\..\..`) serta FFmpeg ke `runtime/ffmpeg/bin/`.
5. Mengompilasi `packaging/launcher/LauncherStub.cs` menjadi `EasyFaceless Studio.exe`.
6. Menguji impor offline Python ter-bundle & menjalankan `scripts/build/verify_package.py`.
7. Mengompilasi `packaging/installer/efs.iss` menggunakan `ISCC.exe` → menghasilkan `dist/EasyFacelessStudio-Setup-<versi>.exe`.

---

## 4. Lokasi Terpasang & Data Pengguna

- **Folder program terpasang (per-user, tanpa admin):** `%LOCALAPPDATA%\Programs\EasyFaceless Studio\`
- **Folder data pengguna (terpisah, bertahan saat upgrade/reinstall):** `%LOCALAPPDATA%\EasyFaceless Studio\`
  - `config.toml` (disalin dari `config.example.toml` saat pertama kali dijalankan jika belum ada)
  - `storage\longform_projects\`, `storage\presets\`, `storage\longform_cache\`, `storage\_trash\`
  - `logs\launcher.log`, `logs\server.log`

Untuk menguji mode terpasang dari source atau memindahkan direktori data sementara, atur environment variable `EFS_DATA_DIR`.

---

## 5. Penandatanganan Kode (SmartScreen Note)

Saat ini installer belum ditandatangani dengan sertifikat Authenticode berbayar, sehingga pengguna dapat melihat peringatan Windows SmartScreen (*More info → Run anyway*). Dokumentasikan rilis dengan SHA-256 checksum hingga sertifikat signing (OV/EV) ditambahkan.
