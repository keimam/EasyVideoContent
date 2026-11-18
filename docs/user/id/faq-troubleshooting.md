# FAQ & Pemecahan Masalah

### 1. Kenapa tombol *"Cek ulang dengan AI"* atau toggle *"Cek relevansi dengan AI"* tidak bisa diklik?
Model LLM aktif Anda tidak mendukung masukan gambar (*vision*). Contoh: beberapa model teks murni gratis seperti `openrouter/free` atau model berbasis teks saja. Klik **Buka Pengaturan** tepat di bawahnya dan pilih model berkemampuan *vision* (seperti Gemini Flash atau GPT-4o mini).

### 2. Kenapa muncul kata `#Bab 1:` menempel di awal narasi?
Pada naskah lama sebelum pembaruan parser heading, baris judul bab tanpa spasi/baris baru bisa ikut terbaca sebagai paragraf. Buka langkah **Naskah** — jika terdeteksi judul bab menempel, banner perbaikan otomatis muncul dengan tombol **Bersihkan Judul Bab dari Narasi** (juga tersedia lewat CLI: `uv run python cli_longform.py project fix-headings <project_id>`).

### 3. Kenapa thumbnail galeri berbeda dengan video atau berstatus *"Perlu disusun ulang"*?
Aset sumber shot sudah diganti/diperbarui (mis. lewat *Perbarui kata kunci dengan AI* atau *Cari lagi*), tetapi klip final shot belum dibangun ulang. Filter dengan chip **Perlu diperbarui · N**, lalu klik **Susun Klip** atau **Cari & Susun**. Thumbnail selalu membaca sumber klip final (`rendered_clip`) dengan cache berbasis hash isi.

### 4. Kenapa progress bar pada pembaruan sebagian tidak mulai dari 99%?
Mulai versi **1.0.0**, proses sebagian di Visual, Suara, dan Render hanya menghitung unit target yang benar-benar berubah (dimulai dari `0/N` = `0%`) dan menampilkan jumlah unit valid/terkunci yang dilewati (`dilewati: X (sudah valid/terkunci)`). Angka hanya menjadi `100%` setelah proses selesai total.

### 5. Bagaimana jika ekspor CapCut gagal karena CapCut sedang aktif?
Tutup aplikasi **CapCut Desktop** sepenuhnya (pastikan ikon baki sistem / Task Manager `CapCut.exe` tidak aktif), lalu ulangi klik **Ekspor & Pasang ke CapCut**.

### 6. `ffmpeg` atau `ffprobe` tidak ditemukan
Pastikan FFmpeg terpasang dan tersedia di `PATH` Windows, atau isi jalur `ffmpeg.exe` secara eksplisit di `config.toml` / tab Pengaturan. Jalankan `uv run python cli_longform.py doctor` (atau `ffmpeg -version`) untuk memverifikasi.
