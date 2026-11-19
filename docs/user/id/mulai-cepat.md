# Mulai Cepat: Dari Proyek hingga CapCut

Panduan ini merangkum langkah cepat menggunakan **EasyFaceless Studio** untuk membuat **Video Pendek** maupun **Video Panjang (10–20 menit)**.

---

## A. Video Pendek (Menu `Video Pendek`)

1. Klik **Video Pendek** di sidebar kiri.
2. Isi **Topik Video** (atau tempel skrip sendiri), lalu pilih bahasa naskah & suara.
3. Bila belum mengatur kunci API, klik ikon/tombol **Pengaturan** di kanan atas atau menu **API & Penyedia AI** di sidebar.
4. Sesuaikan rasio aspek (`9:16` Shorts/Reels atau `16:9` YouTube) dan klik tombol buat video.

---

## B. Video Panjang 10–20 Menit (Menu `Video Panjang`)

### Langkah 1 — Membuat Proyek Baru
1. Di Beranda **Video Panjang**, masukkan **Topik atau judul video** (mis. *"Kisah Kejayaan Kereta Api Nusantara"*).
2. Atur perkiraan **Durasi** (`10–20 menit`) dan **Bahasa narasi** (`id-ID` atau `en-US`).
3. *(Opsional)* Buka **Pengaturan lanjutan** untuk memilih **Niche**, **Sub-niche**, fokus khusus, atau rasio aspek (`16:9` / `9:16`).
4. Klik **Buat Proyek**.

### Langkah 2 — Menulis atau Mengimpor Naskah (`Naskah`)
- **Tulis dengan AI:** Pastikan penyedia LLM sudah siap, klik **Mulai Tulis Naskah AI**. AI akan membuat kerangka dan naskah per bab secara otomatis sesuai bahasa proyek.
- **Impor Markdown/Teks:** Tempel naskah dengan format `# Bab 1: Judul Bab` atau unggah berkas `.md`/`.txt`, lalu klik **Impor Naskah Ini**.
- Anda dapat menyunting teks tiap bab langsung di editor dan menggunakan *"Tulis ulang bab ini"* bila perlu.

### Langkah 3 — Sintesis Suara (`Suara`)
1. Pilih **Penyedia Suara** (bawaan **Edge TTS** tersedia gratis tanpa API key) dan suara narator.
2. Klik **Mulai Sintesis Suara**. Sistem hanya memproses segmen baru/berubah dan langsung mengukur durasi audio beserta timestamp subtitle.

### Langkah 4 — Menyiapkan & Memeriksa Visual (`Visual`)
1. Buka expander **Sumber footage** untuk memilih penyedia stok (**Pixabay**, **Pexels**, **Coverr**, **Lokal**, atau opsional **Gambar AI**).
2. Klik **Cari & Susun**. Sistem akan membagi shot berdasarkan jeda narasi, mencari klip sesuai kata kunci bahasa Inggris, dan membangun klip siap render.
3. Cek galeri shot: kunci shot favorit, ganti kandidat bila kurang pas, atau buat ulang dengan **AI Video**. Lihat panduan lengkap di [Galeri Visual & Shot](galeri-visual.md).

### Langkah 5 — Render & CapCut (`Render` → `CapCut`)
- Pada tab **Render**, pilih musik latar (opsional) lalu klik **Render Semua**. Hasil final tersimpan di folder `export/final_video.mp4` proyek beserta file teks chapter YouTube (`export/youtube_chapters.txt`) dan daftar atribusi sumber (`export/credits.md`).
- Pada kartu proyek yang sudah selesai di Beranda atau tab Render, Anda dapat langsung menekan **Buka folder output** dan **Buka credits.md** (dengan tombol salin).
- *(Opsional)* Pada tab **CapCut**, tutup aplikasi CapCut Desktop terlebih dahulu lalu klik **Ekspor & Pasang ke CapCut** agar timeline multi-track langsung muncul di daftar proyek CapCut Anda.

> **Kebutuhan Sistem (sesuai uji 10 menit):** Windows 10/11 64-bit, minimal CPU 4 thread (disarankan 8+), minimal RAM 8 GB (disarankan 16 GB dengan CapCut), dan ruang disk kosong minimal 5 GB (disarankan 20 GB).

> **Tips Otomatis:** Jika naskah sudah siap, Anda dapat menekan **Jalankan Otomatis** untuk memproses Suara → Visual → Render secara berurutan (berhenti otomatis bila ada shot bertanda *"Perlu dicek"*).
