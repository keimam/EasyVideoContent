# Pengaturan API & Penyedia AI

Seluruh konfigurasi API dan model AI dikelola terpusat lewat jendela **Pengaturan** (**API & Penyedia AI** di sidebar atau tombol ganti penyedia di tiap langkah proyek). Tidak ada penyimpanan kunci terpisah di Video Panjang.

---

## 1. Kategori Penyedia

| Kategori | Fungsi di Aplikasi | Contoh Penyedia | Sifat |
|---|---|---|---|
| **LLM (Model Bahasa)** | Menulis naskah, ringkasan, menerjemahkan & membuat kata kunci visual konkret, jangkar topik, serta cek relevansi (*vision*) | OpenRouter (`openrouter/free` atau model berbayar), OpenAI, Google Gemini, DeepSeek, Groq, Ollama (lokal), Qwen, Moonshot | Ada pilihan gratis & berbayar |
| **TTS (Penyedia Suara)** | Mengubah naskah tiap segmen menjadi audio narasi WAV & sinkronisasi subtitle | **Edge TTS** (default gratis), OpenAI TTS, Azure Speech, Gemini TTS, ElevenLabs, SiliconFlow | Edge TTS gratis; lainnya mengikuti API penyedia |
| **Gambar AI** | Membuat ilustrasi statis bergaya *Ken Burns* (opsional saat visual stok kurang cocok) | OpenAI Image, penyedia kompatibel gambar di tab Materi | Berbayar sesuai tarif penyedia |
| **Footage Stok & Terbuka** | Menyediakan klip video & foto dokumenter per shot | Pixabay (key gratis), Coverr, **Wikimedia Commons** (tanpa key), **NASA Library** (tanpa key), **Openverse** (tanpa key). *Catatan: Pendaftaran key baru Pexels sedang ditutup.* | Gratis / berdasar ketentuan lisensi masing-masing |
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

---

## 4. Preset Penyedia Gambar AI (OpenAI Compatible)

Di jendela **Pengaturan → Sumber Visual / Gambar** (*OpenAI Compatible*), tersedia preset siap pakai yang mengisi **Base URL**, **Model**, dan ukuran **16:9** (`1344x768`) secara otomatis **tanpa menimpa API key Anda**:

| Preset | Base URL | Slug Model | Catatan Teknis & Lisensi (Diakses 2026-11-23) |
|---|---|---|---|
| **Together AI** | `https://api.together.xyz/v1` | `black-forest-labs/FLUX.1-schnell` | Studio otomatis menyesuaikan payload (`aspect_ratio`, `width`, `height`, `steps`, `response_format`). Tarif resmi: [together.ai/pricing](https://together.ai/pricing). Referensi: [docs.together.ai](https://docs.together.ai/docs/inference/images/overview). |
| **DeepInfra** | `https://api.deepinfra.com/v1/openai` | `black-forest-labs/FLUX-1-schnell` | Mendukung parameter OpenAI `size` dan `b64_json`. Tarif di halaman model resminya memakai rumus `\$0.0005 × (width / 1024) × (height / 1024) × iters`. Referensi: [deepinfra.com/black-forest-labs/FLUX-1-schnell](https://deepinfra.com/black-forest-labs/FLUX-1-schnell). |
| **Kustom** | *(isi manual)* | *(isi manual)* | Untuk gateway lokal (ComfyUI / Automatic1111) atau penyedia OpenAI-compatible lain. |

- **Status Nebius Token Factory:** Seluruh model *Text-to-Image* pada Nebius dinonaktifkan mulai 13 April 2026 ([docs.tokenfactory.nebius.com/other-capabilities/deprecation-info](https://docs.tokenfactory.nebius.com/other-capabilities/deprecation-info)), sehingga tidak disertakan sebagai preset.
- **Catatan Lisensi Model FLUX.1:**
  - `black-forest-labs/FLUX.1-schnell`: berlisensi **Apache 2.0**, dapat digunakan untuk penggunaan pribadi, ilmiah, maupun komersial ([huggingface.co/black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell)).
  - `black-forest-labs/FLUX.1-dev` *(jika dipakai manual)*: memakai lisensi **non-komersial** khusus ([huggingface.co/black-forest-labs/FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev)); perhatikan ketentuannya sebelum pemakaian komersial.
- **Tombol Uji 1 Gambar:** Gunakan **Uji 1 gambar** di Pengaturan Gambar atau panel **Sumber Footage** Video Panjang untuk melihat pratinjau gambar langsung atau kategori error yang aman (`401/403` auth, `402`/kuota, `404` model/endpoint, `400/422` parameter, atau timeout jaringan) tanpa pernah menampilkan API key.
