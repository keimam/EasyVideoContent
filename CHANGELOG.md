# Changelog — EasyFaceless Studio

Seluruh rilisan versi inti EasyFaceless Studio berakar dari satu konstanta `APP_VERSION` pada `app/services/branding/constants.py`.

## [1.0.0] — Tahap 8C.1 (Perapian Repo, Hook Minimal, Dokumentasi & Lisensi)
- **Tahap 8C.1 (C1–C5):**
  - Memindahkan logika inisialisasi halaman, pembungkus dialog Pengaturan, serta parameter kueri dari `webui/Main.py` ke `app/services/branding/hooks.py`, memangkas diff upstream `webui/Main.py` secara signifikan.
  - Merapikan set screenshot akhir di `docs/branding/screens/final/` (ID & EN), memindahkan skrip pengembang ke `scripts/dev/`, menghapus dokumen duplikat `STAGE7_WEBUI.md` di root, melengkapi `.gitignore` (`*.backup*.toml`, `*.log`, `**/_trash/`, folder temp), serta membersihkan path Windows pribadi dari dokumentasi & tes.
  - Menyusun dokumentasi pengguna dwibahasa di `docs/user/id/` dan `docs/user/en/`, `README.md` baru, dialog **Bantuan** dwibahasa di aplikasi, `THIRD_PARTY_NOTICES.md` berbasis generator environment, pembaruan `LICENSE` MIT, serta `CHANGELOG.md`.
- **Addendum C6 & C7:**
  - Membersihkan `"MoneyPrinterTurbo"` dan `"MPT"` dari teks UI, judul halaman, tooltip, dan tombol (termasuk menghapus tautan redundan di atas judul Video Panjang dan akhiran `"(Aktif)"` pada tooltip sidebar), serta menambahkan pemindai C6 otomatis.
  - Meringkas kotak atribusi sidebar menjadi satu baris klik kecil `"v1.0.0"` dan memindahkan atribusi lengkap + isi berkas `LICENSE` + tautan `THIRD_PARTY_NOTICES.md` ke dialog **Tentang / About**.

## Tahap 8B.2 (F1–F15 — Fitur AI Opsional & Penyempurnaan UX Visual/Progres)
- **F1–F5:** Cek relevansi footage berbasis *vision* dengan cache lokal & retry 429; terjemahan kata kunci ke English per bab; **Video AI** per shot & batch *"Perlu dicek"* dengan estimasi tarif, konfirmasi biaya wajib, dan batas anggaran USD; penyempurnaan tes koneksi & default model OpenRouter aman (`openrouter/free`).
- **F6–F11:** Tes koneksi tahan model reasoning (`finish_reason=length` retry); hapus proyek aman ke **Tempat Sampah** (`_trash/`) termasuk saat job berjalan beserta pilihan hapus draft CapCut terpasang; pembuat kata kunci visual konkret AI per shot; guard & perbaikan otomatis judul bab menempel pada narasi; petunjuk tombol vision saat model tidak mendukung gambar.
- **F12–F15:** Jangkar visual topik (`anchor_terms`, `avoid_terms`, `visual_summary`) + eskalasi query stok `<3` kandidat + filter tag tab Kandidat + catatan *"Ditemukan dengan"*; satu sumber kebenaran klip shot (`rendered_clip`) dengan cache thumbnail berbasis hash isi & badge **"Perlu disusun ulang"**; progres akurat terlingkup target unit kerja (memulai dari `0%`, menghitung `dilewati: N (sudah valid/terkunci)`, indeterminate <=95%, mencapai `100%` hanya saat selesai).

## Tahap 8B.1 & Addendum B3 (Penyatuan Penyedia AI)
- Menyatukan LLM (44 penyedia), TTS, Gambar AI, dan Video AI Video Panjang dengan konfigurasi tunggal MPT melalui adapter terpadu tanpa penyimpanan key ganda.
- Penjaga bahasa naskah otomatis (Indonesia/English) dan perbaikan ulang per bab.
- Dialog Pengaturan native dibuka langsung pada halaman Video Panjang aktif tanpa keluar proyek.

## Tahap 8A.1 & 8A.2 (Identitas Studio & Antarmuka Dwibahasa)
- Membangun identitas visual gelap **EasyFaceless Studio**, tipografi offline **Plus Jakarta Sans**, sidebar navigasi seragam (`Video Panjang`, `Video Pendek`, `Kelola Niche`, `API & Penyedia AI`, `Cache & Penyimpanan`), serta tata letak Beranda 3 kolom & langkah kerja proyek dwibahasa (`id` & `en`).

## Tahap 7 & 7.9 (WebUI Video Panjang & Sinkronisasi Upstream v1.3.10)
- Antarmuka multipage `webui/pages/1_🎬_Long_Video.py`, pelari tugas latar belakang aman terhadap *refresh* (`job_state.json`, pembatalan bersih `CTRL_BREAK_EVENT`), editor bab, pratinjau suara, galeri visual 3-kolom, serta penggabungan bersih upstream `v1.3.10`.

## Tahap 0 – 6B (Mesin Pipa Video Panjang & Ekspor CapCut)
- **Tahap 0–1:** Audit lingkungan Windows berpath spasi, model proyek `manifest.json`, sistem preset & sub-niche, serta pelacakan hash inkremental (`hashing.py`).
- **Tahap 2–3:** Penulisan/impor naskah multi-bab, pemeriksaan kualitas narasi, sintesis suara paralel dengan word cue & subtitle ASS/SRT (`voice_service.py`).
- **Tahap 4–5:** Pencarian & scoring visual multi-sumber (`Pixabay`, `Pexels`, `Coverr`, `Local`, `Gambar AI`, `Video AI`), builder klip FFmpeg, peredupan musik latar (BGM ducking), normalisasi loudness, dan render bab/final paralel-aman.
- **Tahap 6A–6B:** Reverse-engineering format CapCut 8.9.0, builder draft portabel multi-trek audio/video/teks selaras, deteksi proses CapCut aktif, serta fitur kalibrasi ukuran teks.
