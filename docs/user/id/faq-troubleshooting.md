# FAQ & Pemecahan Masalah

### 1. Kenapa foto atau video di **Local files** tidak otomatis terpakai?
Folder **Local files** mencocokkan **kata kunci di nama berkas dan nama subfolder** terhadap narasi/query setiap shot, bukan memasukkan berkas secara acak:
- **Penamaan file harus cocok:** Gunakan kata kunci bahasa Inggris yang deskriptif dipisah dengan `_`, `-`, atau `.` (misalnya `plague_doctor_street_night.mp4` atau subfolder `vikings/ship_storm.jpg`).
- **Isi acak bawaan mati:** Opsi **Isi shot kosong dengan file lokal acak** sengaja nonaktif agar klip yang tidak relevan tidak masuk ke semua shot. Aktifkan hanya bila Anda memang ingin memakai file lokal apa adanya saat hasil cocok tidak ada.
- **Cara membuka folder & mengunggah banyak file:** Buka expander **Sumber footage** di langkah **Visual** → tekan **📂 Buka folder Local files**, atau gunakan pemilih **Tambah media ke Local files (multi-file)** lalu klik **Simpan ke Local files** (jika nama berkas sudah ada, pilih *Ganti nama otomatis*, *Timpa file yang ada*, atau *Lewati*).

### 2. AI berhenti dengan pesan `"kuota harian habis (reset 07:00 WIB)"`
Penulisan naskah AI, ringkasan, kata kunci visual, dan *Konteks Visual* berhenti atau berpindah saat kuota permintaan harian penyedia AI utama Anda habis (pada OpenRouter tier gratis dengan saldo di bawah \$10, batas resminya **50 request/hari** dan **20 request/menit** dan reset pada `00:00 UTC` = **07:00 WIB**; *"Spend windows are UTC: a daily budget resets at 00:00 UTC"* — sumber: [OpenRouter Limits](https://openrouter.ai/docs/api/reference/limits), dicek: 2026-11-27).
- **Solusi terbaik — Aktifkan Penyedia AI cadangan:** Buka **Pengaturan → Umum → Penyedia AI cadangan (otomatis pindah saat kuota/error)**, isi minimal dua penyedia berbeda (mis. OpenRouter + **Google Gemini** atau **Groq**), lalu klik **🔍 Uji semua penyedia AI**. Studio otomatis beralih ke cadangan saat penyedia utama kehabisan kuota harian atau terkena `401`/`402`/`404`/`429`. Lihat detail di [Pengaturan AI & Penyedia API](pengaturan-ai.md).

### 3. Pesan error Gemini `limit: 0` (model tidak tersedia di tier gratis)
Jika muncul pesan bahwa model memiliki `limit: 0` atau tidak tersedia di tier gratis Gemini, model Gemini yang dipilih tidak masuk jatah gratis proyek Google Cloud Anda (atau memerlukan akun berbayar — lihat [Gemini API Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits), dicek: 2026-11-27).
- **Solusi:** Buka **Pengaturan** lalu pilih model **Flash** (misalnya `gemini-3.5-flash-lite` atau `gemini-2.5-flash`) yang mendukung tier gratis.

### 4. Arti peringatan sumber footage (Coverr 403, Openverse timeout, & Profil **Lambat**)
- **`"Coverr dilewati: batas kuota per jam habis. Coba lagi nanti atau gunakan key produksi."`** vs **`"Coverr dilewati: API key ditolak (403). Cek key di API & Penyedia AI."`**:
  - Key Demo gratis Coverr dibatasi **50 request per jam** (*"50 free calls per hour No credit card required"* — [coverr.co/developers](https://coverr.co/developers), dicek: 2026-11-27). Jika muncul **batas kuota per jam habis**, API key Anda valid dan hanya perlu menunggu reset 1 jam (sementara sumber lain seperti Pixabay, Wikimedia, NASA, Openverse, dan Internet Archive tetap berjalan). Hanya pesan **API key ditolak** yang berarti key salah atau tidak valid.
- **Openverse / Wikimedia / NASA dijeda sementara karena timeout:** Jika muncul pesan seperti `"Openverse dijeda …× karena server lambat/error"`, server penyedia tersebut sedang lambat merespons batas waktu standar 6 detik.
  - **Solusi:** Buka **Pengaturan → Umum → Profil koneksi sumber visual**, lalu ubah ke **Lambat (timeout 15 dtk, jeda 60 dtk setelah 3 gagal)**. Pengaturan ini memberi waktu tunggu 15 detik sebelum sumber dianggap lambat dan kembali lebih cepat setelah jeda.

### 5. Arti `"Visual selesai: X dari Y shot. Z shot belum menemukan visual yang cocok."`
Status **Selesai sebagian** berarti proses **Cari & Susun Visual** sudah selesai tuntas (bukan terhenti di tengah jalan), tetapi ada `Z` shot dengan narasi abstrak/sulit dicari yang belum mendapat kecocokan dari seluruh sumber gratis. Karena prosesnya sudah selesai, tombol yang tampil adalah **Lanjut ke Render** (bukan tombol **Lanjutkan**).
- **Langkah untuk mengisi shot yang masih kosong:**
  1. Klik kartu shot yang masih belum berisi visual di **Galeri Visual**.
  2. Gunakan tab **Cari lagi** dengan kata kunci bahasa Inggris konkret (objek visual yang terlihat kamera, misalnya `medieval city alley lantern` alih-alih kata abstrak).
  3. Tambahkan gambar/klip sendiri ke **Local files** (atau unggah di tab **Unggah** shot tersebut).
  4. Gunakan expander **🖼️ Gambar Eksternal (Flow, dll.) — Ekspor Prompt & Impor Massal** dengan cakupan **Shot tanpa visual** untuk membuat gambar lewat Google Flow atau Zapi lalu mengimpornya sekaligus.

### 6. Kenapa API key **Gambar AI** tidak terbawa saat mengganti Together AI ↔ DeepInfra ↔ Kustom?
Setiap preset di **Preset penyedia gambar (OpenAI Compatible)** (**Together AI**, **DeepInfra**, dan **Kustom**) kini menyimpan API key secara **terpisah per preset** di konfigurasi. Mengganti preset tidak pernah menimpa key preset lain. Gunakan tombol **🧪 Uji 1 gambar** untuk mengetes preset aktif beserta key-nya sebelum menjalankan batch.

### 7. Kenapa tombol *"Cek ulang dengan AI"* atau toggle *"Cek relevansi dengan AI"* tidak bisa diklik?
Model LLM aktif Anda tidak mendukung masukan gambar (*vision*), misalnya model teks saja seperti `openrouter/free`. Klik **Buka Pengaturan** tepat di bawahnya lalu pilih model berkemampuan *vision* (seperti Gemini Flash atau GPT-4o mini).

### 8. Kenapa muncul kata `#Bab 1:` menempel di awal narasi?
Pada naskah lama sebelum pembaruan parser heading, baris judul bab tanpa spasi/baris baru bisa ikut terbaca sebagai paragraf. Buka langkah **Naskah** — jika terdeteksi judul bab menempel, banner perbaikan otomatis muncul dengan tombol **Bersihkan Judul Bab dari Narasi** (juga tersedia lewat CLI: `uv run python cli_longform.py project fix-headings <project_id>`).

### 9. Kenapa thumbnail galeri berbeda dengan video atau berstatus *"Perlu disusun ulang"*?
Aset sumber shot sudah diganti/diperbarui (mis. lewat *Perbarui kata kunci dengan AI* atau *Cari lagi*), tetapi klip final shot belum dibangun ulang. Filter dengan chip **Perlu dicek · N** / periksa shot bertanda ulang, lalu klik **Perbarui Klip yang Berubah** atau **Cari & Susun Visual**. Thumbnail membaca klip final (`rendered_clip`) dengan cache berbasis hash isi.

### 10. Kenapa progress bar pada pembaruan sebagian tidak mulai dari 99%?
Proses pembaruan sebagian di Visual, Suara, dan Render hanya menghitung unit target yang benar-benar berubah (mulai dari `0/N` = `0%`) serta menampilkan jumlah unit valid/terkunci yang dilewati. Angka mencapai `100%` hanya setelah pekerjaan selesai sepenuhnya.

### 11. Bagaimana jika ekspor CapCut gagal karena CapCut sedang aktif?
Tutup aplikasi **CapCut Desktop** sepenuhnya (pastikan ikon baki sistem / Task Manager `CapCut.exe` tidak aktif), lalu ulangi klik **Ekspor ke CapCut (Install)** (atau **Ekspor ulang ke CapCut**).

### 12. `ffmpeg` atau `ffprobe` tidak ditemukan
Pastikan FFmpeg terpasang dan tersedia di `PATH` Windows, atau isi jalur `ffmpeg.exe` secara eksplisit di `config.toml` / tab Pengaturan. Jalankan `uv run python cli_longform.py doctor` (atau `ffmpeg -version`) untuk memverifikasi. (Pada versi installer Windows, FFmpeg sudah dibundel otomatis di folder instalasi.)

### 13. Windows Defender SmartScreen menampilkan peringatan *"Windows protected your PC"* saat menjalankan installer 1.0.2
Installer `EasyFacelessStudio-Setup-1.0.2.exe` saat ini **belum ditandatangani dengan sertifikat Authenticode berbayar** (*unsigned*), sehingga Windows SmartScreen dapat menampilkan jendela peringatan pada unduhan baru. Bila Anda mengunduh dari rilis resmi proyek ini:
1. Klik **More info** (atau **Info selengkapnya**).
2. Klik tombol **Run anyway** (atau **Tetap jalankan**).
Installer akan memasang ke `%LOCALAPPDATA%\Programs\EasyFaceless Studio` tanpa memerlukan hak administrator. Data proyek Anda di `%LOCALAPPDATA%\EasyFaceless Studio` tetap aman saat upgrade dari versi sebelumnya.

### 14. Cara mengunduh installer 1.0.2 ukuran besar dari Google Drive & memverifikasi SHA-256
1. **Layar peringatan virus Google Drive:** Untuk berkas installer ~570 MB, Google Drive menampilkan halaman *"Google Drive can't scan this file for viruses"* / *"Google Drive tidak dapat memindai file ini untuk mencari virus"*. Hal ini normal untuk berkas biner berukuran besar — klik **Download anyway** / **Tetap download** untuk melanjutkan unduhan di browser.
2. **Verifikasi keutuhan berkas (SHA-256):** Buka PowerShell di folder unduhan, lalu jalankan:
   ```powershell
   Get-FileHash .\EasyFacelessStudio-Setup-1.0.2.exe -Algorithm SHA256
   ```
   Cocokkan hasil `Hash` dengan nilai pada berkas `.sha256` atau deskripsi video/catatan rilis sebelum memasang.
3. **Pembaruan aplikasi:** Tombol **Unduh Pembaruan** pada banner atau dialog **Tentang** selalu membuka tautan unduhan resmi di browser default (bukan mengunduh diam-diam di latar belakang).
