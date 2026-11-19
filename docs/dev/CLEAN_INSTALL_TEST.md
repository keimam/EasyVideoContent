# Panduan Uji Instalasi Bersih Windows (Stage 8C.3, E6)

Dokumen ini memandu pengujian end-to-end paket rilis **EasyFaceless Studio** pada sistem Windows bersih (tanpa Python, `uv`, Git, atau FFmpeg terpasang sebelumnya).

---

## Opsi A — Menggunakan Windows Sandbox (`.wsb`)

> Membutuhkan **Windows 10/11 Pro, Enterprise, atau Education** dengan fitur *Windows Sandbox* aktif. (Di Windows Home atau PC tanpa virtualisasi aktif, gunakan **Opsi B: VM** di bawah.)

1. Pastikan folder `dist\` sudah berisi hasil build terbaru (`EasyFacelessStudio-Setup-1.0.0.exe`).
2. Pastikan path `<HostFolder>` di [`packaging/sandbox/efs-test.wsb`](../../packaging/sandbox/efs-test.wsb) menunjuk ke folder `dist\` Anda (`ReadOnly=true` agar aman).
3. Klik dua kali berkas `packaging/sandbox/efs-test.wsb`. Windows Sandbox terbuka otomatis dan menampilkan folder `efs-dist` di Desktop.

---

## Opsi B — Alternatif VM (VirtualBox / Hyper-V / VMware)

Jika Windows Sandbox tidak tersedia pada mesin Anda:
1. Buat VM Windows 10/11 (64-bit) bersih dengan **RAM minimal 8 GB**, **4 vCPU**, dan **disk kosong minimal 15 GB (disarankan 20 GB+ SSD)** (snapshot awal *before-install*).
2. Pasang atau salin `EasyFacelessStudio-Setup-1.0.0.exe` (opsional verifikasi SHA-256 lewat `Get-FileHash .\EasyFacelessStudio-Setup-1.0.0.exe -Algorithm SHA256` terhadap berkas `.sha256`).
3. Uji dengan kondisi **tanpa** Python/FFmpeg di `PATH` sistem.

---

## Daftar Periksa Pengujian End-to-End

1. **Verifikasi Installer & Instalasi Non-Admin:**
   - Jalankan `EasyFacelessStudio-Setup-1.0.0.exe` → bila SmartScreen muncul (*More info → Run anyway*), pastikan instalasi selesai ke `%LOCALAPPDATA%\Programs\EasyFaceless Studio` tanpa meminta hak Administrator.
2. **Peluncuran Tanpa Jendela Konsol & Wizard Pertama Kali (E1):**
   - Buka lewat Start Menu / ikon Desktop. Pastikan **tidak ada** jendela hitam PowerShell/CMD yang terbuka.
   - Verifikasi **Wizard Pertama Kali** muncul otomatis (Bahasa, Cek Sistem hijau/kuning/merah, OpenRouter, Sumber Footage, Folder CapCut, Selesai).
   - Uji membuat **Proyek Contoh** di langkah akhir wizard.
3. **Alur Produksi Video Panjang & Akses Hasil (E5):**
   - Uji menghasilkan suara (**Edge TTS** gratis tanpa API key), mengambil visual gratis (**Wikimedia/NASA/Openverse** + **Pixabay** bila diisi), lalu **Render Final**.
   - Verifikasi hasil muncul di folder proyek, tombol **Buka folder output**, dan dialog **Buka credits.md** (termasuk tombol **Salin**).
4. **Deteksi & Ekspor CapCut Desktop (E3):**
   - *(Opsional jika CapCut Desktop terpasang di lingkungan uji)* Verifikasi status versi (`CapCut 8.9.0 — teruji ✓` atau peringatan versi belum teruji yang tetap memperbolehkan ekspor), tutup CapCut, lalu uji **Ekspor & Pasang ke CapCut**.
5. **Uji Upgrade Tidak Menghapus Data:**
   - Tutup aplikasi via tombol **Tutup aplikasi** di sidebar, jalankan ulang installer, dan pastikan proyek + `config.toml` di `%LOCALAPPDATA%\EasyFaceless Studio\` tetap utuh.
