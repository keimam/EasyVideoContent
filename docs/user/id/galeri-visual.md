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
