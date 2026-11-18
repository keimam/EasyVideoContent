# Tempat Sampah & Penghapusan Aman Proyek

Penghapusan proyek di EasyFaceless Studio menggunakan mekanisme **Tempat Sampah** (`storage/longform_projects/_trash/`) sehingga proyek yang terhapus tidak langsung hilang permanen.

---

## 1. Memindahkan Proyek ke Tempat Sampah

1. Buka Beranda **Video Panjang** (melalui menu `⋯` pada kartu proyek) atau bilah atas halaman proyek, lalu klik **Hapus proyek**.
2. Dialog menampilkan **ukuran folder (MB)** dan jumlah **draft CapCut terpasang** milik proyek tersebut.
3. *(Opsional)* Centang **Hapus juga draft CapCut terpasang** bila Anda juga ingin menghapus folder draft proyek di CapCut Desktop (default **mati**).
4. Ketik **judul proyek** atau **ID proyek**, lalu klik **Pindahkan ke Sampah**.

---

## 2. Menghapus Proyek Saat Pekerjaan Sedang Berjalan

- Semua proyek — termasuk yang sedang menjalankan tugas latar belakang — dapat dihapus.
- Bila ada pekerjaan aktif, dialog menampilkan peringatan bahwa proses aktif akan dihentikan bersih terlebih dahulu sebelum folder dipindahkan ke Tempat Sampah (menjaga `.lock` dan proses anak tetap bersih).
- Jika ada berkas yang masih dikunci oleh aplikasi lain di Windows (mis. pemutar media atau explorer), dialog menampilkan pesan ramah dan daftar jalur berkas terkunci di expander **Detail**.

---

## 3. Memulihkan atau Menghapus Permanen

- Buka filter **Tempat Sampah** di Beranda Video Panjang atau menu **Cache & Penyimpanan**.
- Klik **Pulihkan** untuk mengembalikan proyek ke daftar utama (proyek yang sebelumnya dihentikan berstatus *terhenti* dan dapat dilanjutkan).
- Klik **Hapus permanen** untuk menghapus folder proyek satu per satu, atau **Kosongkan sampah** untuk membebaskan seluruh ruang disk di `_trash/`.

> **Catatan:** Penghapusan proyek ke Tempat Sampah tidak menghapus cache stok global (`storage/longform_cache/`) maupun preset kustom Anda.
