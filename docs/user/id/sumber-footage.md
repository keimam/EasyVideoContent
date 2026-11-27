# Sumber Footage, Lisensi & Atribusi (`credits.md`)

Panduan ini menjelaskan seluruh penyedia video/foto gratis, komersial-bebas, arsip sejarah, dan aset pribadi yang digunakan pada langkah **Visual** di **EasyFaceless Studio**.

---

## 1. Daftar Lengkap Sumber Footage

*(Dicek: 2026-11-26 & 2026-11-27 dari dokumentasi resmi masing-masing penyedia.)*

| Sumber | Perlu Key? | Jenis Media | Lisensi yang Diterima | Kuota / Batas Terverifikasi |
|---|---|---|---|---|
| **Pixabay** | Perlu key *(gratis)* | Video HD/4K | Pixabay Content License *(bebas komersial, tanpa atribusi wajib)* | **100 request per 60 detik** (*"By default, you can make up to 100 requests per 60 seconds."* — [pixabay.com/api/docs](https://pixabay.com/api/docs/), dicek: 2026-11-27); cache disk 24 jam otomatis |
| **Coverr** | Perlu key *(Demo gratis / berbayar)* | Video B-roll | Coverr License *(bebas komersial)* | **50 request per jam** di tier Demo (*"50 free calls per hour No credit card required"* — [coverr.co/developers](https://coverr.co/developers), dicek: 2026-11-27) |
| **Pexels** | Perlu key *(pemilik key lama)* | Video HD/4K | Pexels License | **Catatan UI:** *Pendaftaran key baru Pexels sedang ditutup; pengguna yang sudah punya key tetap didukung.* Angka batas resmi tidak terverifikasi otomatis karena proteksi bot halaman dokumentasi (dicek: 2026-11-27) |
| **Wikimedia Commons** | **Tidak perlu (aktif bawaan)** | Video (`WebM`/`OGV` dikonversi otomatis ke MP4) & Foto | Hanya Domain Publik, `CC0`, `CC BY`, `CC BY-SA` (`NC`/`ND` ditolak otomatis) | Tanpa angka batas numerik tetap yang terverifikasi; dijaga cache 24 jam & jeda sopan |
| **Openverse** | **Tidak perlu (aktif bawaan)** | Gambar / Foto *(animasi Ken Burns)* | Hanya lisensi komersial: `CC0`, `PDM`, `CC BY`, `CC BY-SA` (`NC`/`ND` ditolak) | Dikelola cache 24 jam, jeda sopan, dan *circuit breaker* timeout otomatis |
| **NASA Image & Video Library** | **Tidak perlu (aktif bawaan)** | Video & Foto misi sains/antariksa | Public Domain / Materi Pemerintah AS (cantumkan kredit NASA tanpa menyiratkan dukungan resmi) | Endpoint pencarian media bebas key dengan cache 24 jam |
| **Internet Archive (`Prelinger` & `Cleveland Museum of Art`)** | **Tidak perlu (aktif bawaan)** | Video arsip MP4 *(dipotong langsung lewat HTTP Range tanpa unduh file utuh)* & Gambar museum | Koleksi terkurasi + filter ketat `licenseurl` Domain Publik / `CC0` / `CC BY` / `CC BY-SA` (`Fair Use`/`NC`/`ND` ditolak) | Tanpa angka RPS numerik resmi; wajib header `User-Agent` deskriptif & jeda (*"All automated requests to archive.org must include a descriptive User-Agent header"* — [archive.org/developers/bots.html](https://archive.org/developers/bots.html), dicek: 2026-11-26) |
| **Smithsonian Open Access** | **Opsional BYOK** *(key `api.data.gov` gratis)* | Gambar JPEG resolusi tinggi | Hanya media dengan status resmi `CC0` (`usage.access == "CC0"`; *Limited Use*/berhak cipta ditolak) | **1.000 request per jam** dengan API key gratis `api.data.gov` ([api.data.gov/docs/rate-limits](https://api.data.gov/docs/rate-limits/) via [Catatan Arsip](../../dev/ARCHIVE_SOURCES.md), dicek: 2026-11-26) |
| **Local files** | **Tidak perlu** *(folder proyek)* | Video & Gambar lokal | Hak cipta / izin milik Anda sendiri | Tidak terbatas (bekerja offline) |
| **🖼️ Gambar Eksternal (Flow, dll.)** | Sesuai akun generator eksternal | Gambar & Video buatan eksternal | Mengikuti ketentuan layanan generator yang Anda gunakan | Mengikuti kuota akun Google Flow, Zapi, Midjourney, atau ChatGPT Anda |

> **Urutan bawaan:** `Pixabay → Coverr → Wikimedia → NASA → Openverse → Internet Archive` *(ditambah **Smithsonian** jika API key opsional diisi)*.

---

## 2. Cara Mendapatkan dan Mengisi API Key

Sumber tanpa key (**Wikimedia Commons**, **NASA Library**, **Openverse**, dan **Internet Archive**) sudah langsung aktif sejak pemasangan pertama. Untuk menambah ragam stok modern atau koleksi museum Smithsonian:

1. **Pixabay (Gratis):**
   - Masuk atau buat akun gratis di [pixabay.com/api/docs](https://pixabay.com/api/docs/) untuk menyalin API Key pribadi Anda.
   - Tempel pada kolom kunci Pixabay di **Pengaturan → Pengaturan sumber materi** (atau Wizard langkah **4. Footage**).
2. **Coverr (Gratis Demo 50/jam):**
   - Daftar di [coverr.co/developers](https://coverr.co/developers) untuk mendapat Demo API key (50 request/jam) tanpa kartu kredit.
   - Masukkan kunci Coverr di **Pengaturan → Pengaturan sumber materi** (atau Wizard langkah **4. Footage**).
3. **Smithsonian Open Access (`api.data.gov`, Opsional Gratis):**
   - Daftar gratis di [api.data.gov/signup](https://api.data.gov/signup/) menggunakan email untuk memperoleh kunci pribadi (1.000 req/jam).
   - Pada langkah **Visual → Sumber footage** (atau tab **Pengaturan**), tempel kunci ke kolom **API Key Smithsonian / api.data.gov (opsional)**, tekan **Simpan key Smithsonian**, lalu tekan **Uji 1 request** untuk memastikan status berubah menjadi `✅ siap`.

---

## 3. Mode Pencarian: **Campur terbaik** vs **Prioritas**

Di dalam expander **Sumber footage**, Anda dapat memilih perilaku pencarian lewat **Mode pencarian sumber footage (khusus proyek ini)**:

- **Campur terbaik (skor relevansi tertinggi lintas sumber):** Mengirim pencarian paralel ke semua sumber aktif sekaligus, lalu memilih kandidat dengan skor relevansi tertinggi.
  - **Batas dominasi maks. 60% shot (*Batasi satu sumber maks. 60% shot*):** Setelah jumlah shot cukup banyak, satu sumber tidak boleh memborong lebih dari 60% total shot jika masih ada sumber relevan lain.
  - **Batas berurutan 3 shot (*Hindari sumber yang sama >3 shot berturut-turut*):** Mencegah video terlihat monoton dari satu sumber yang sama beruntun.
  - Bila selisih skor tipis (`≤ 0,02`), studio memprioritaskan sumber yang lebih jarang terpakai.
- **Prioritas (habiskan sumber berurutan sesuai daftar):** Mencoba sumber satu per satu dari urutan paling atas ke bawah hingga menemukan kandidat relevan. Urutan sumber dapat digeser naik/turun memakai tombol **↑** / **↓**.
- **Aturan Local files:** Baik di **Campur terbaik** maupun **Prioritas**, aset dari **Local files** yang lolos batas skor relevansi selalu didahulukan.

---

## 4. Footage Arsip (Abad ke-19 s/d ke-20) & Kewaspadaan Hak Cipta

Sumber **Internet Archive** (terutama *Prelinger Archives* & *Cleveland Museum of Art*) dan **Smithsonian Open Access** sangat ideal untuk topik sejarah, dokumenter peristiwa dunia, atau cerita berlatar **abad ke-19 sampai ke-20**.

Wajib diperhatikan sebelum publikasi ke YouTube atau platform monetisasi lain:
> *"Footage arsip: lisensi dari metadata pengunggah. Selalu cek kredit. Footage publik tetap bisa terkena klaim Content ID pihak lain."*

- Sebagian materi unggahan komunitas di arsip publik menandai lisensi terbuka padahal memuat elemen *Fair Use* atau klaim pihak ketiga (*"Many people believe that all Prelinger Archives films are in the public domain. Some are. Many are not."* — FAQ Prelinger, dicek: 2026-11-26). Studio sudah menolak otomatis item dengan penanda `Fair Use`, `NC`, atau `ND`, namun Anda tetap disarankan meninjau daftar aset di `credits.md` sebelum merilis video komersial.

---

## 5. Letak `credits.md` dan Cara Membukanya

Setiap proyek otomatis menyimpan daftar sumber, judul, pembuat, jenis lisensi (`CC BY`, `CC BY-SA`, `CC0`, Domain Publik, Pixabay, dsb.), dan tautan asli di dalam folder **`export/credits.md`** pada direktori proyek *(serta ikut disalin ke folder keluaran MP4 dan draft CapCut)*.

Cara paling cepat untuk membuka atau menyalin isinya:
1. Di kartu proyek selesai pada Beranda atau pada langkah **Render** di proyek, klik **📜 Buka credits.md**.
2. Di jendela **Atribusi Footage & Musik (`credits.md`)**, gunakan tombol **📋 Salin isi credits.md** atau **⬇️ Unduh credits.md** untuk menempelkan kredit wajib ke deskripsi video Anda.
3. Atau klik **📂 Buka folder output**, lalu masuk ke subfolder `export\` dan buka berkas `credits.md`.
