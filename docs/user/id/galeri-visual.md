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
