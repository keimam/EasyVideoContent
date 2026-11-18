# Pengaturan API & Penyedia AI

Seluruh konfigurasi API dan model AI dikelola terpusat lewat jendela **Pengaturan** (**API & Penyedia AI** di sidebar atau tombol ganti penyedia di tiap langkah proyek). Tidak ada penyimpanan kunci terpisah di Video Panjang.

---

## 1. Kategori Penyedia

| Kategori | Fungsi di Aplikasi | Contoh Penyedia | Sifat |
|---|---|---|---|
| **LLM (Model Bahasa)** | Menulis naskah, ringkasan, menerjemahkan & membuat kata kunci visual konkret, jangkar topik, serta cek relevansi (*vision*) | OpenRouter (`openrouter/free` atau model berbayar), OpenAI, Google Gemini, DeepSeek, Groq, Ollama (lokal), Qwen, Moonshot | Ada pilihan gratis & berbayar |
| **TTS (Penyedia Suara)** | Mengubah naskah tiap segmen menjadi audio narasi WAV & sinkronisasi subtitle | **Edge TTS** (default gratis), OpenAI TTS, Azure Speech, Gemini TTS, ElevenLabs, SiliconFlow | Edge TTS gratis; lainnya mengikuti API penyedia |
| **Gambar AI** | Membuat ilustrasi statis bergaya *Ken Burns* (opsional saat visual stok kurang cocok) | OpenAI Image, penyedia kompatibel gambar di tab Materi | Berbayar sesuai tarif penyedia |
| **Video AI** | Menghasilkan klip video pendek per shot lewat tab **AI Video** di panel Detail shot | **WaveSpeed** (mis. `Wan 2.2 Ultra Fast`, `Seedance 2.0 Fast`), Luma, Kling, Hailuo, Runway, Volcengine, dll. | **Berbayar** (selalu menampilkan perkiraan harga USD & konfirmasi sebelum dikirim) |

---

## 2. Model Gratis vs Berbayar (OpenRouter & Tips Hemat)

- **Model Gratis (`openrouter/free`):** Cocok untuk uji coba naskah dan membuat kata kunci visual AI tanpa saldo kredit. Catatan: model gratis dapat memiliki antrean (*rate limit*) lebih ketat dan tidak selalu mendukung fitur *vision* (membaca gambar).
- **Model Vision (untuk Cek Relevansi AI):** Fitur opsional **Cek relevansi dengan AI** hanya aktif bila model LLM aktif mendukung masukan gambar (mis. keluarga `gemini-2.5-flash` atau `gpt-4o-mini`). Hasil pemeriksaan di-cache agar tidak memanggil ulang shot yang sama.
- **Video AI:** Sebelum setiap pembuatan tunggal maupun massal, studio selalu menampilkan tabel estimasi biaya USD dan meminta batas anggaran maksimal. Tidak ada permintaan berbayar yang dikirim otomatis tanpa klik konfirmasi.

---

## 3. Arti Kode Error Umum (`402`, `404`, `429`)

- **Error `402` (Payment Required / Kredit Tidak Cukup):**
  Saldo/kredit pada penyedia LLM atau AI Video Anda tidak mencukupi untuk model atau kuota `max_tokens` yang dipilih.
  *Solusi:* Isi kredit akun penyedia Anda atau buka **Pengaturan** lalu pilih model gratis (mis. `openrouter/free` pada OpenRouter).
- **Error `404` (Model Tidak Ditemukan / Dihapus):**
  Slug model yang diisi sudah tidak tersedia di penyedia (mis. versi `:free` lama sudah dipensiunkan).
  *Solusi:* Buka **Pengaturan**, klik **Muat daftar model** pada kartu Naskah atau masukkan slug model aktif terbaru.
- **Error `429` (Rate Limit / Terlalu Banyak Permintaan):**
  Anda melampaui batas permintaan per menit/hari dari penyedia API. Studio otomatis melakukan coba ulang bertahap (*backoff*). Pada penulisan naskah bab, bab yang sudah selesai tetap aman tersimpan dan bisa dilanjutkan dengan tombol **Lanjutkan Bab Tersisa**.
