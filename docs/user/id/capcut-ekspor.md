# Ekspor Timeline ke CapCut Desktop

EasyFaceless Studio dapat menghasilkan draft **CapCut Desktop** portabel lengkap dengan trek video per shot, audio narasi per bab, musik latar, dan subtitle agar Anda bisa menyunting ulang video langsung di CapCut.

---

## 1. Versi Teruji & Peringatan Penting

- **Versi CapCut Desktop teruji:** **8.9.0** (Windows).
- **Wajib tutup CapCut sebelum ekspor (`--install` / tombol *Ekspor & Pasang ke CapCut*):**
  CapCut menyimpan indeks proyek di `root_meta_info.json`. Menulis draft saat aplikasi CapCut Desktop terbuka dapat menyebabkan korupsi indeks atau draft tidak terbaca. Studio otomatis mendeteksi proses `CapCut.exe` dan menolak pemasangan hingga CapCut ditutup penuh.

---

## 2. Alur Ekspor Standar

1. Selesaikan tahap **Render** (minimal audio & klip visual setiap bab sudah lengkap).
2. Buka tab **CapCut** di proyek Anda.
3. Tutup aplikasi **CapCut Desktop** sepenuhnya.
4. Klik **Ekspor & Pasang ke CapCut**. Studio menulis draft baru ke folder proyek lokal CapCut (`%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\EFS_<project_id>_...`).
5. Buka CapCut Desktop kembali — proyek muncul paling atas di halaman utama CapCut.

---

## 3. Kalibrasi Ukuran Teks Subtitle

Perbedaan resolusi `16:9` vs `9:16` serta jenis font memengaruhi proporsi ukuran teks di CapCut:
1. Di bagian **Kalibrasi Ukuran Teks** pada tab CapCut, klik **Buat Draft Kalibrasi**.
2. Buka draft kalibrasi tersebut di CapCut Desktop — berisi segmen uji ukuran teks dari **5 sampai 10**.
3. Pilih angka yang terlihat paling proporsional tanpa terpotong di kiri/kanan layar, lalu simpan di Studio melalui tombol **Simpan Ukuran Teks**. Nilai tersebut dipakai pada ekspor berikutnya untuk preset yang sama.
