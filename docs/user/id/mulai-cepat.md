# Mulai Cepat: Dari Proyek hingga CapCut

Panduan ini merangkum langkah cepat menggunakan **EasyFaceless Studio** untuk membuat **Video Pendek** maupun satu **Video Panjang (~10 menit)** secara utuh dari nol hingga siap diedit di CapCut Desktop.

---

## A. Video Pendek (Menu `Video Pendek`)

1. Klik **Video Pendek** di sidebar kiri.
2. Isi **Topik Video** (atau tempel skrip sendiri), lalu pilih bahasa naskah & suara.
3. Bila belum mengatur kunci API, klik **Pengaturan** di kanan atas atau menu **API & Penyedia AI** di sidebar.
4. Sesuaikan rasio aspek (`9:16` Shorts/Reels atau `16:9` YouTube) dan klik tombol buat video.

---

## B. Alur Lengkap Video Panjang ±10 Menit (Menu `Video Panjang`)

### Langkah 1 — Membuat Proyek Baru
1. Di Beranda **Video Panjang** (atau klik **+ Proyek Baru**), masukkan topik/judul video Anda.
2. Atur perkiraan durasi (mis. `10 menit`) dan bahasa narasi (`id-ID` atau `en-US`).
3. *(Opsional)* Atur **Niche**, **Sub-niche**, fokus khusus, atau rasio aspek (`16:9` / `9:16`).
4. Buat proyek untuk masuk ke alur 5 langkah: **Naskah → Suara → Visual → Render → CapCut**.

### Langkah 2 — Menulis atau Mengimpor **Naskah**
- **Otomatisasi Naskah AI:** Pastikan penyedia LLM sudah siap (disarankan menyiapkan **Penyedia AI cadangan** di **Pengaturan → Umum** agar tidak terhenti bila kuota harian habis), lalu klik **1. Buat Outline Naskah** dan **2. Tulis Seluruh Bab Naskah**.
- **Impor Naskah Sendiri:** Unggah berkas `.md`/`.txt` atau tempel naskah berformat `# Bab 1: Judul Bab`, lalu klik **Impor Naskah ke Proyek**.
- Anda dapat menyunting teks tiap bab secara langsung sebelum menekan **Lanjut ke Suara**.

### Langkah 3 — Sintesis **Suara**
1. Pilih karakter suara (bawaan **Edge TTS** langsung aktif gratis tanpa API key) pada **Pilihan Suara, Kecepatan & Pratinjau**.
2. Klik **Mulai Sintesis Audio** (bisa dicoba dulu lewat **Tes Sampel Suara**). Sistem hanya menyintesis segmen baru/berubah menjadi audio WAV serta menyelaraskan subtitle otomatis.
3. Setelah selesai, klik **Lanjut ke Visual**.

### Langkah 4 — Menyiapkan & Memeriksa **Visual**
1. Buka expander **Sumber footage** untuk memilih mode pencarian (**Campur terbaik** bawaan atau **Prioritas**), mengaktifkan sumber lokal (**Local files**), atau menambah kunci **Pixabay / Coverr / Smithsonian**. Sumber gratis tanpa key (**Wikimedia Commons**, **NASA Library**, **Openverse**, dan **Internet Archive**) sudah aktif otomatis — lihat [Sumber Footage](sumber-footage.md).
2. Klik **Cari & Susun Visual** untuk memecah narasi menjadi shot, mencari footage, dan membangun klip siap render.
3. Periksa kartu shot di **Galeri Visual**:
   - Gunakan filter **Perlu dicek · N** bila ada shot bertanda **Perlu dicek**, atau ganti/unggah aset lewat tab **Kandidat**, **Cari lagi**, **Unggah**, atau **AI Video**.
   - Bila status berakhir sebagai **Selesai sebagian** (`Visual selesai: X dari Y shot. Z shot belum menemukan visual yang cocok.`), lengkapi sisa shot via **Cari lagi**, **Local files**, atau expander **🖼️ Gambar Eksternal (Flow, dll.)** sebelum menekan **Lanjut ke Render**. Lihat [Galeri Visual & Kontrol per Shot](galeri-visual.md).

### Langkah 5 — **Render** & **CapCut**
1. Di langkah **Render**, atur musik latar/subtitle pada **Pengaturan Render (Musik Latar & Subtitle)** bila perlu, lalu klik **Mulai Render Video Final**.
2. Setelah render selesai, proyek menghasilkan `export/final_video.mp4`, daftar chapter YouTube (`export/youtube_chapters.txt`), dan atribusi sumber (`export/credits.md`). Anda bisa langsung menekan **📂 Buka folder output** atau **📜 Buka credits.md** (untuk menyalin kredit ke deskripsi video).
3. *(Opsional)* Di langkah **CapCut**, pastikan **CapCut Desktop** sedang tertutup (**Tertutup · aman**), lalu klik **Ekspor ke CapCut (Install)** agar timeline multi-track langsung terpasang di CapCut Desktop Anda.

> **Mode Praktis (`Otomatis sampai selesai`):** Setelah langkah **Naskah** selesai, Anda dapat menekan **Otomatis sampai selesai** untuk menjalankan **Suara → Visual → Render → CapCut** berurutan otomatis dalam satu antrean latar belakang.

---

## C. Perkiraan Waktu Produksi Video ±10 Menit (Hasil Benchmark CA1)

Angka berikut diukur nyata menggunakan proyek uji 6 bab (**77 shot**, durasi final ~7,51 menit, impor naskah `--no-llm`) pada PC uji **Windows 10 64-bit, Intel Core i3-12100 (4 core / 8 thread), RAM 7,75 GiB (~8 GB), Python 3.11.9, CapCut Desktop v8.9.0** ([`docs/dev/BENCHMARK.md`](../../dev/BENCHMARK.md)):

| Tahap Produksi | Run 1 — Cache Dingin *(belum ada cache)* | Run 2 — Cache Hangat *(aset/pencarian sudah di-cache)* | Catatan |
|---|---:|---:|---|
| **1. Naskah** | ~0,03 dtk *(impor)* | ~0,05 dtk *(impor)* | Penulisan dengan AI menambah waktu sesuai kecepatan & batas rate limit LLM Anda |
| **2. Suara** | **10,3 dtk** | **11,1 dtk** | Bergantung koneksi ke layanan TTS (Edge TTS) |
| **3. Visual** | **~8,5 menit** (`507,5 dtk`) | **~6,2 menit** (`369,7 dtk`) | Pencarian + unduhan + pembangunan klip; sangat bergantung koneksi internet |
| **4. Render Final** | **~2,1 menit** (`125,6 dtk`) | **~2,1 menit** (`126,2 dtk`) | Lokal CPU (FFmpeg) |
| **5. Ekspor CapCut** | **3,1 dtk** | **2,8 dtk** | Penulisan draft & salin aset lokal |
| **Total 5 Fase** | **~10,8 menit** (`646,6 dtk`) | **~8,5 menit** (`509,9 dtk`) | **Waktu riil sangat bergantung kecepatan internet & server sumber footage** |

---

## D. Persyaratan Sistem (Terkalibrasi Benchmark E4)

Mengacu pada [Bagian 5 `docs/dev/BENCHMARK.md`](../../dev/BENCHMARK.md) (ukuran terukur: ~1,75 GB per proyek 77 shot + ~2,3 GB cache visual global):

- **Sistem Operasi:** Windows 10 / Windows 11 (64-bit)
- **CPU:** Minimal **4 core / 4 thread** · Disarankan **6–8+ thread** (proses penyusunan klip paralel berjalan lancar di 4C/8T)
- **Memori (RAM):** Minimal **8 GB** *(puncak pemakaian Python + FFmpeg terukur ~1,38 GiB pada uji CA1 dingin)* · Disarankan **16 GB** bila menyunting bersama CapCut Desktop
- **Ruang Disk Kosong (Folder Data):** Minimal **15 GB** · Disarankan **20 GB+ SSD** agar nyaman menampung beberapa proyek beserta cache visual
- **Koneksi Internet:** Stabil untuk pencarian/unduhan stok footage, sintesis Edge TTS, dan AI eksternal
