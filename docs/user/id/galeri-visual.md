# Galeri Visual & Kontrol per Shot

Langkah **Visual** memecah setiap bab menjadi beberapa shot sesuai durasi audio sehingga pergantian gambar terasa dinamis dan relevan.

---

## 1. Status Kartu Shot & Sumber Tampilan

- **Satu Sumber Kebenaran:** Thumbnail pada galeri dan pratinjau di panel Detail sebelah kanan selalu membaca berkas klip final (`rendered_clip`) yang akan masuk ke render akhir.
- **Badge Status:**
  - `🔄 Perlu disusun ulang` (*Needs rebuild*): Anda sudah mengganti aset atau menjalankan *Perbarui kata kunci* / *Cari lagi*, tetapi klip final shot ini belum disusun ulang. Gunakan filter **Perlu diperbarui · N** untuk menemukannya cepat, lalu klik **Susun Klip**.
  - `🧩 Belum disusun` (*Not built*): Aset sumber sudah ada, namun klip final belum pernah dibangun.
  - `🔒 Terkunci` (*Locked*): Shot dikunci agar tidak tertimpa saat Anda menekan **Cari & Susun**.

---

## 2. Mengganti & Mengedit Shot (Panel Detail)

Pilih salah satu kartu shot untuk membuka panel Detail di sebelah kanan, lalu pilih salah satu dari 4 tab:

1. **Kandidat:** Menampilkan daftar kandidat footage hasil pencarian otomatis (sudah disaring dari istilah yang dihindari). Klik **Gunakan** untuk mengganti klip shot ke kandidat lain.
2. **Cari lagi:** Ketik query bahasa Inggris manual Anda (mis. `night street food wok`) untuk langsung mengunduh dan mengganti klip.
3. **Unggah:** Unggah video `.mp4`/`.mov` atau gambar lokal `.jpg`/`.png` dari komputer Anda.
4. **AI Video:** Gunakan penyedia Video AI (mis. WaveSpeed) untuk membuat klip khusus berdasarkan prompt yang bisa Anda edit terlebih dahulu. Perkiraan biaya USD dan batas anggaran selalu dikonfirmasi sebelum pembuatan dimulai.

---

## 3. Memperbarui Kata Kunci AI & Cek Relevansi

- **Perbarui kata kunci dengan AI:** Menghasilkan kata kunci konkret berbahasa Inggris (3–6 kata) plus query alternatif berdasarkan isi bab & jangkar visual proyek (tag manual `[search]` dan shot terkunci tetap dipertahankan).
  Setelah itu klik **Cari & Susun** — progress bar hanya menghitung shot yang berubah dan menampilkan jumlah shot valid/terkunci yang dilewati.
- **Cek relevansi dengan AI:** Toggle opsional (memerlukan model LLM berkemampuan *vision*) untuk memberi label **Relevan**, **Ragu**, atau **Tidak relevan** pada shot terpilih.

---

## 4. Sumber Footage, Pencarian Bertingkat & Atribusi (`credits.md`)

Secara default, studio mencari footage secara bertingkat per shot (**Query Utama/AI → Kata Kunci Lebih Umum → Sumber Berikutnya**) dengan urutan prioritas:
**Pixabay → Coverr → Wikimedia Commons → NASA Library → Openverse (foto)**.

| Sumber | Kunci API | Media & Karakteristik | Aturan Lisensi & Atribusi |
|---|---|---|---|
| **Pixabay** | Perlu key (gratis) | Video HD/4K sinematik, variasi modern lengkap | Pixabay Content License (bebas komersial, atribusi tidak wajib) |
| **Coverr** | Perlu key | Klip video stok sinematik bebas royalti | Coverr License (bebas komersial) |
| **Pexels** | Perlu key (*Pendaftaran key baru ditutup*) | Didukung bagi pemilik API key lama | Pexels License |
| **Wikimedia Commons** | **Gratis (tanpa key)** | Video (`WebM/OGV` dikonversi otomatis ke MP4) & foto dokumenter/sejarah | Hanya Domain Publik, `CC0`, `CC BY`, `CC BY-SA` (`NC`/`ND` ditolak otomatis). Item `CC BY`/`BY-SA` wajib mencantumkan kredit |
| **NASA Image & Video Library** | **Gratis (tanpa key)** | Klip & foto resmi misi antariksa, sains, observasi Bumi | NASA Media (Public Domain / U.S. Gov). Cantumkan kredit `NASA` tanpa menyiratkan endorsement |
| **Openverse** | **Gratis (tanpa key)** | Foto resolusi tinggi dari berbagai koleksi terbuka; dijadikan klip *Ken Burns* | Hanya lisensi komersial (`CC0`, `PDM`, `CC BY`, `CC BY-SA`; `NC`/`ND` ditolak). Item `CC BY`/`BY-SA` wajib atribusi |

- **Alasan "Belum ada visual":** Bila seluruh query dan sumber gagal, shot ditandai `"Belum ada visual"` beserta ringkasan alasannya di panel Detail (mis. jumlah kandidat & alasan penolakan relevansi/lisensi).
- **Berkas `credits.md`:** Otomatis dibuat di `export/credits.md` dan ikut disalin ke folder hasil render MP4 & draft CapCut berisi judul, pembuat, lisensi, dan tautan sumber. Shot yang memakai `CC BY` / `CC BY-SA` menampilkan tanda **Perlu atribusi** di panel Detail.


---

## 5. Pustaka Media Lokal (`Local files`)

Anda bisa menyiapkan koleksi gambar atau klip video milik sendiri (arsip dokumenter, aset merek, atau rekaman Pribadi) di folder `assets/visual/library` proyek:

1. **Cara membuka & mengunggah:** Buka panel **Sumber Footage** di halaman **Visual**, lihat jumlah file saat ini, lalu tekan **Buka folder Local files** untuk membuka folder langsung di Windows Explorer, atau pakai pengunggah multi-file yang tersedia (bila nama file sudah ada, studio akan menanyakan **Ganti nama otomatis**, **Timpa**, atau **Lewati** agar file lama tidak tertimpa diam-diam).
2. **Aturan penamaan agar akurat:** Studio membaca kata kunci dari nama file (pisahkan kata dengan garis bawah `_`, tanda hubung `-`, atau titik `.`) serta nama subfolder di dalamnya — contohnya `old_lighthouse_night_fog.jpg` atau subfolder `vikings/ship_storm.jpg`.
3. **Prioritas & opsi pengisian acak:** Jika centang **Local files** aktif dan ada file lokal yang lolos skor relevansi, file tersebut selalu didahulukan baik di mode **Campur terbaik** maupun **Prioritas**. Opsi **Isi shot kosong dengan file lokal acak** nonaktif secara bawaan agar file yang tidak cocok tidak mengisi semua shot otomatis.
4. **Format yang didukung:** Video (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`) dan gambar (`.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`).

---

## 6. Jembatan Gambar Eksternal (Google Flow / Midjourney / ChatGPT)

Jika sebagian shot lebih cocok dibuat memakai generator eksternal seperti Google Flow, Midjourney, atau ChatGPT, gunakan panel **Gambar Eksternal** di halaman **Visual**:

1. **Ekspor prompt gambar:** Pilih cakupan (*Shot tanpa visual*, *Shot terpilih*, atau *Semua shot*), klik **Siapkan file prompt**, lalu unduh berkas `.txt` (berawalan `[shot_id]`) atau `.csv` UTF-8 (`shot_id`, durasi, cuplikan narasi, prompt bahasa Inggris, rasio `16:9`, dan gaya visual proyek).
2. **Buat gambar/klip di alat eksternal:** Hasilkan visual Anda lalu beri nama berkas sesuai `shot_id` (misalnya `ch01_shot001.png`) atau gunakan angka urutan sesuai urutan ekspor terakhir (`image_1.png`, `02.webp`, dst.).
3. **Impor gambar massal:** Unggah sekumpulan `.png`, `.jpg`, `.jpeg`, `.webp`, atau `.mp4`. Studio menampilkan pratinjau pemetaan yang bisa Anda ubah manual per file dan memperingatkan jika ada file yang belum cocok sebelum Anda menekan **Terapkan impor eksternal**.
4. **Otomatis disusun & dikunci:** Gambar otomatis diberi animasi *Ken Burns* (dan video `.mp4` dipotong/disesuaikan ke durasi shot), ditandai **terkunci** (`Locked`) agar tidak tertimpa saat menekan *Cari & Susun*, *Otomatis*, maupun *Perbarui kata kunci dengan AI*, serta dicatat di `credits.md` sebagai `"Gambar dibuat pengguna dengan alat eksternal"`.
