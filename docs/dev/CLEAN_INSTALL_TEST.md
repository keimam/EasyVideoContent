# Panduan Uji Instalasi Bersih Windows (Final R1.6)

Gunakan daftar periksa **12 langkah** ini di PC Windows 10/11 lain yang **belum memiliki Python, `uv`, Git, maupun FFmpeg terpasang** (atau di Windows Sandbox via [`packaging/sandbox/efs-test.wsb`](../../packaging/sandbox/efs-test.wsb)).

## Daftar Periksa Pengujian (<= 15 Langkah)

1. **Unduh installer dari Google Drive:** Buka link rilis → bila tampil halaman peringatan ukuran besar (*"can't scan this file for viruses"*), klik **Tetap download / Download anyway**.
2. **Verifikasi SHA-256:** Di PowerShell folder unduhan, jalankan `Get-FileHash .\EasyFacelessStudio-Setup-1.0.0.exe -Algorithm SHA256` dan cocokkan dengan berkas `.sha256` atau catatan rilis.
3. **Jalankan installer (non-Admin):** Bila SmartScreen muncul, klik **More info → Run anyway**; pasang ke `%LOCALAPPDATA%\Programs\EasyFaceless Studio` tanpa UAC Admin.
4. **Buka aplikasi:** Jalankan lewat Start Menu / Desktop; pastikan **tidak ada jendela konsol CMD/PowerShell** muncul.
5. **Selesaikan Wizard Pertama Kali:** Pilih bahasa, pastikan cek spek sistem tampil (hijau/kuning/merah), isi OpenRouter/API opsional (atau lewati), pastikan Wikimedia/NASA/Openverse aktif bawaan, cek folder & versi CapCut, lalu buat proyek contoh.
6. **Jalankan proyek kecil mode Otomatis:** Buat/gunakan proyek pendek (~1–2 menit) memakai **Edge TTS** (tanpa key) dan sumber visual gratis tanpa key hingga seluruh langkah visual selesai.
7. **Verifikasi Render Final (MP4):** Jalankan **Render Final** dan pastikan berkas video MP4 final muncul serta bisa diputar di folder proyek.
8. **Ekspor ke CapCut Desktop:** Pastikan **CapCut Desktop** tidak aktif (jika terpasang), klik **Ekspor & Pasang ke CapCut**, dan cek draft terbuka normal dengan klip & subtitle.
9. **Cek atribusi & hasil output:** Klik **Buka credits.md** dan **Buka folder output**; pastikan keduanya terbuka dengan daftar atribusi footage lengkap.
10. **Tutup bersih lewat sidebar:** Klik **Tutup aplikasi** dan pastikan server berhenti (ikon/port `:8501` tidak lagi aktif di Task Manager).
11. **Uji Uninstall aman:** Uninstall aplikasi dari *Apps & Features* → pilih **No / Tidak** pada prompt penghapusan folder data → pastikan folder `%LOCALAPPDATA%\EasyFaceless Studio` masih menyimpan proyek Anda.
12. **Instal ulang (Upgrade/Reinstall):** Pasang kembali installer → buka aplikasi → proyek & `config.toml` lama termuat utuh tanpa Wizard berulang, dan dialog **Tentang** menampilkan versi + hash build yang benar.

## Apa yang Harus Dicatat / Difoto Bila Gagal
- **Screenshot penuh** layar error / halaman browser (termasuk baris footer versi/build di dialog **Tentang** atau pesan banner).
- **Nomor langkah** yang gagal (mis. Langkah 6 atau Langkah 8) serta spesifikasi ringkas PC (RAM, CPU, drive kosong, versi CapCut).
- **Kumpulan log wajib** dari folder `%LOCALAPPDATA%\EasyFaceless Studio\logs\` (`launcher.log` dan `server.log`) beserta isi manifest proyek bila gagal di render/CapCut.
