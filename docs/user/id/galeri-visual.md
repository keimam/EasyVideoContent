# Galeri Visual & Kontrol per Shot

Langkah **Visual** memecah setiap bab menjadi beberapa shot sesuai durasi audio sehingga pergantian gambar terasa dinamis dan relevan.

---

## 1. Status Kartu Shot & Sumber Tampilan

- **Satu Sumber Kebenaran:** Thumbnail pada galeri dan pratinjau di panel Detail sebelah kanan selalu membaca berkas klip final (`rendered_clip`) yang akan masuk ke render akhir.
- **Badge Status:**
  - `🔄 Perlu dicek` / `Perlu diperbarui`: Anda sudah mengganti aset atau menjalankan *Perbarui kata kunci* / **Cari lagi**, atau shot masih membutuhkan tinjauan. Gunakan filter **Perlu dicek · N** untuk menemukannya cepat, lalu klik **Perbarui Klip yang Berubah** atau **Cari & Susun Visual**.
  - `🧩 Belum disusun`: Aset sumber sudah ada, namun klip final belum pernah dibangun.
  - `🔒 Terkunci`: Shot dikunci agar tidak tertimpa saat Anda menekan **Cari & Susun Visual**.
- **Selesai sebagian vs Terhenti:** Bila proses selesai dengan beberapa shot masih tanpa visual, status tampil sebagai **Selesai sebagian** (`Visual selesai: X dari Y shot. Z shot belum menemukan visual yang cocok.`) lengkap dengan daftar shot/query serta saran perbaikan tanpa tombol **Lanjutkan**.

---

## 2. Mengganti & Mengedit Shot (Panel Detail)

Pilih salah satu kartu shot untuk membuka panel Detail di sebelah kanan, lalu pilih salah satu dari 4 tab:

1. **Kandidat:** Menampilkan daftar kandidat footage hasil pencarian otomatis (sudah disaring dari istilah yang dihindari). Klik **Pakai ini** untuk mengganti klip shot ke kandidat lain.
2. **Cari lagi:** Ketik kueri bahasa Inggris manual Anda (mis. `night street food wok`) lalu tekan **Cari Footage** untuk mengunduh dan mengganti klip langsung.
3. **Unggah:** Unggah video `.mp4`/`.mov` atau gambar lokal `.jpg`/`.png` dari komputer Anda.
4. **AI Video:** Gunakan penyedia Video AI (mis. WaveSpeed) untuk membuat klip khusus berdasarkan prompt yang bisa Anda edit terlebih dahulu. Perkiraan biaya USD dan batas anggaran selalu dikonfirmasi sebelum pembuatan dimulai.

---

## 3. Memperbarui Kata Kunci AI & Cek Relevansi

- **Perbarui kata kunci dengan AI:** Menghasilkan kata kunci konkret berbahasa Inggris (3–6 kata) plus kueri alternatif berdasarkan isi bab & jangkar visual proyek (tag manual `[search]` dan shot terkunci tetap dipertahankan).
  Setelah itu klik **Cari & Susun Visual** — progress bar hanya menghitung shot yang berubah dan menampilkan jumlah shot valid/terkunci yang dilewati.
- **Cek relevansi dengan AI:** Toggle opsional (memerlukan model LLM berkemampuan *vision*) untuk memberi label **Relevan**, **Ragu**, atau **Tidak relevan** pada shot terpilih.

---

## 4. Sumber Footage, Pustaka Lokal & Atribusi (`credits.md`)

- **Panduan lengkap seluruh sumber & lisensi:** Lihat [Sumber Footage, Lisensi & Atribusi](sumber-footage.md) untuk tabel lengkap **Pixabay**, **Coverr**, **Pexels**, **Wikimedia Commons**, **Openverse**, **NASA Library**, **Internet Archive (`Prelinger` & museum)**, **Smithsonian Open Access** (opsional BYOK `api.data.gov`), **Local files**, dan **Gambar Eksternal**, beserta penjelasan mode **Campur terbaik** vs **Prioritas**.
- **Ringkasannya:** Sumber tanpa key (`Wikimedia`, `NASA`, `Openverse`, `Internet Archive`) aktif otomatis, `credits.md` ditulis otomatis di folder `export/credits.md` (buka cepat lewat **📜 Buka credits.md**), dan folder pribadi `assets/visual/library` dapat dibuka lewat **📂 Buka folder Local files** atau diisi via **Tambah media ke Local files (multi-file)** → **Simpan ke Local files** (ingat opsi **Isi shot kosong dengan file lokal acak** default nonaktif agar file tak cocok tidak terpakai acak).

---

## 5. Panduan Gambar Eksternal (Flow, Zapi) — Tata Letak 3 Tab

Jika sebagian atau seluruh shot lebih cocok dibuat memakai generator eksternal (seperti **Google Flow**, **Zapi**, **Midjourney**, atau **ChatGPT**), gulir langkah **Visual** ke bawah daftar kartu shot lalu buka expander **🖼️ Gambar Eksternal (Flow, dll.) — Ekspor Prompt & Impor Massal** yang tersusun dalam **3 tab**:

### Tab 1 — `1. Konteks Visual`
1. **Mode gaya gambar:** Pilih gaya visual utama proyek — bawaan **Realistis sinematik**, atau **Ilustrasi**, **Lukisan**, maupun **Kustom** (AI menyesuaikan detail dalam gaya pilihan Anda serta mencegah kata `Hindari` bertabrakan dengan gaya gambar).
2. **Latar & zaman (*Setting*):** Pastikan kolom **Latar & zaman** menyebut periode/era secara jelas (mis. `19th century London` atau `1990s Jakarta`). Jika zaman belum disebut, muncul peringatan kuning agar kostum & bangunan tidak bercampur era.
3. **Tokoh, POV & varian kilas balik (`variant_of`):** Definisikan tokoh di tabel **Tokoh (table: ID, nama, deskripsi fisik lengkap, alias, peran, variant_of)**. Jika tokoh memiliki adegan kilas balik (mis. versi anak atau lanjut usia), buat baris varian dengan mengisi kolom `variant_of` ke ID tokoh utamanya. Bila narasi memakai kata ganti orang kedua (*kamu/you*), pilih **Tokoh sudut pandang 'you/kamu'**.
4. **Lokasi berulang:** Pisahkan lokasi yang sama menjadi `_interior` dan `_exterior` pada tabel **Lokasi berulang (pisahkan `_interior` / `_exterior`)**, dan biarkan kolom **Warna & suasana** bebas dari kata waktu/pencahayaan spesifik.
5. **✨ Buat dengan AI & Simpan:** Klik **✨ Buat dengan AI** agar studio membaca judul, topik, preset, dan naskah untuk mengisi seluruh Konteks Visual secara otomatis. Saat dimuat/disimpan, studio membersihkan otomatis alias kata ganti/ganda & kata waktu yang terselip, lalu menyimpannya saat Anda menekan **💾 Simpan Konteks Visual**.

### Tab 2 — `2. Ekspor Prompt`
1. **Gunakan AI & cakupan ekspor:** Centang **Gunakan AI untuk menyusun adegan & prompt bersambung** (bila ingin kesinambungan lokasi, waktu, cuaca, cahaya, benda, dan kondisi tokoh otomatis per adegan), pilih **Cakupan shot yang diekspor** (*Shot tanpa visual*, *Shot terpilih*, atau *Semua shot*), lalu klik **Siapkan file prompt (.txt & .csv)**.
2. **Arti peringatan `missing character lock` & tombol `🔄 Lanjutkan dengan AI`:**
   - Pesan `⚠️ N prompt memiliki catatan validator (M prompt belum mengunci tokoh)` (*missing character lock*) menandakan ada prompt shot yang belum menyertakan blok pengunci deskripsi tokoh dari Konteks Visual — buka shot tersebut di tabel ringkas per adegan di bawahnya untuk memilih **Tokoh di shot** lalu klik **🔄 Perbarui pratinjau & file unduhan**.
   - Jika sebagian batch AI berhenti di tengah (mis. kuota AI habis atau timeout), shot terdampak jatuh aman ke jalur non-AI dan tombol **🔄 Lanjutkan dengan AI** muncul untuk melanjutkan hanya batch yang tersisa dari cache.
3. **Tiga file unduhan siap pakai:** Di bagian atas daftar hasil tersedia 3 tombol unduh:
   - **⬇️ Unduh Prompt (.txt)** — daftar bernomor rapi per shot untuk ditempel manual atau dibaca langsung.
   - **⬇️ Unduh Tabel Prompt (.csv UTF-8)** — tabel lengkap beserta kolom metadata adegan untuk Excel/Google Sheets.
   - **⬇️ Prompt untuk bulk tool (Zapi, dll.) — satu baris per prompt** (`<project>_prompts_bulk.txt`) — format UTF-8 tanpa BOM berisi tepat **1 baris = 1 prompt** khusus antrean bulk.

### Tab 3 — `3. Impor Gambar`
1. **Impor dari folder lokal (read-only) atau unggah file:** Masukkan path folder hasil unduhan pada **Impor dari folder lokal (opsional — file asli tidak diubah)**, biarkan **Urutkan file folder** pada **Waktu dibuat (bawaan)** (atau ganti ke *Nama file*), lalu klik **📂 Baca folder** — atau unggah manual `.png`/`.jpg`/`.jpeg`/`.webp`/`.mp4`.
2. **Pencocokan pola nama & file `(1)` browser:** Studio mencocokkan otomatis pola `shot_id` (`ch01_shot001.png`), nomor urut ekspor (`image_1.png`, `02.webp`), serta urutan duplikat browser bernama dasar sama **`nama.webp`, `nama (1).webp`, `nama (2).webp`** (→ shot 1, 2, 3). Jika berkas berakhiran `(1)` ada tetapi file pertama tanpa angka hilang, studio menampilkan peringatan kuning agar urutan tidak bergeser satu shot.
3. **Pratinjau & tombol Terapkan:** Di bagian bawah tab tampil **Pratinjau pemetaan sebelum diterapkan** (thumbnail kecil, nama berkas, dropdown **Shot tujuan**, dan **80 karakter awal prompt shot tujuan** dalam satu baris). Klik **✅ Terapkan impor eksternal** di atas grid — aset langsung diberi animasi *Ken Burns* (atau `.mp4` dipotong sesuai durasi), dikunci (`🔒 Terkunci`), dan dicatat ke `credits.md`.

### Alur Singkat Bulk Generate dengan Zapi
1. Pada tab **2. Ekspor Prompt**, klik **Siapkan file prompt (.txt & .csv)** lalu unduh **⬇️ Prompt untuk bulk tool (Zapi, dll.) — satu baris per prompt** (`<project>_prompts_bulk.txt`).
2. Tempel seluruh baris berkas tersebut ke antrean **Zapi** (*1 baris diproses menjadi 1 prompt terpisah*), lalu jalankan pembuatan **satu per satu** hingga semua hasil terunduh ke satu folder (mis. `nama.webp`, `nama (1).webp`, `nama (2).webp`, ...).
3. Buka tab **3. Impor Gambar**, isi path folder unduhan di **Impor dari folder lokal**, biarkan urutan **Waktu dibuat (bawaan)**, lalu klik **📂 Baca folder**.
4. Cocokkan thumbnail terhadap 80 karakter prompt di pratinjau pemetaan, lalu tekan **✅ Terapkan impor eksternal**.
