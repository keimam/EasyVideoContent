# Pengaturan API & Penyedia AI

Seluruh konfigurasi API dan model AI dikelola terpusat lewat jendela **Pengaturan** (**API & Penyedia AI** di sidebar atau tombol ganti penyedia di tiap langkah proyek).

---

## 1. Kategori Penyedia

| Kategori | Fungsi di Aplikasi | Contoh Penyedia | Sifat |
|---|---|---|---|
| **LLM (Model Bahasa)** | Menulis naskah, ringkasan, kata kunci visual konkret, *Konteks Visual*, adegan bersambung, serta cek relevansi (*vision*) | OpenRouter (`openrouter/free` atau model berbayar), Google Gemini (Flash), Groq, DeepSeek, OpenAI, Ollama (lokal), Qwen, Moonshot | Ada pilihan gratis & berbayar |
| **TTS (Penyedia Suara)** | Mengubah naskah tiap segmen menjadi audio narasi WAV & sinkronisasi subtitle | **Edge TTS** (default gratis), OpenAI TTS, Azure Speech, Gemini TTS, ElevenLabs, SiliconFlow | Edge TTS gratis; lainnya mengikuti API penyedia |
| **Gambar AI** | Membuat ilustrasi statis bergaya *Ken Burns* (opsional saat visual stok kurang cocok) | Together AI, DeepInfra, OpenAI Image, atau gateway kompatibel Kustom | Berbayar sesuai tarif penyedia |
| **Footage Stok & Arsip** | Menyediakan klip video & foto dokumenter per shot | Pixabay (key gratis), Coverr (key Demo/Pro), **Wikimedia Commons**, **NASA Library**, **Openverse**, **Internet Archive** (tanpa key), plus opsional **Smithsonian Open Access** (BYOK `api.data.gov`). Lihat [Sumber Footage](sumber-footage.md) | Gratis / mengikuti lisensi masing-masing |
| **Video AI** | Menghasilkan klip video pendek per shot lewat tab **AI Video** di panel Detail shot | **WaveSpeed** (mis. `Wan 2.2 Ultra Fast`, `Seedance 2.0 Fast`), Luma, Kling, Hailuo, Runway, Volcengine, dll. | **Berbayar** (selalu menampilkan perkiraan harga USD & konfirmasi sebelum dikirim) |

---

## 2. Model Gratis, Gemini Flash & Kontrol Biaya

- **Model Gratis (`openrouter/free`):** Cocok untuk uji coba naskah dan membuat kata kunci visual AI tanpa saldo kredit. Catatan: model gratis memiliki batas antrean lebih ketat dan umumnya tidak mendukung fitur *vision* (membaca gambar).
- **Gemini `limit: 0` → pilih model Flash:** Bila uji koneksi atau pemanggilan Gemini menampilkan error `limit: 0` / model tidak tersedia di tier gratis, pilih model **Flash** (misalnya `gemini-3.5-flash-lite` atau `gemini-2.5-flash`) di Pengaturan.
- **Model Vision (untuk Cek Relevansi AI):** Fitur opsional **Cek relevansi dengan AI** hanya aktif bila model LLM aktif mendukung masukan gambar (mis. keluarga `gemini-2.5-flash` atau `gpt-4o-mini`). Hasil pemeriksaan di-cache agar tidak memanggil ulang shot yang sama.
- **Video AI:** Sebelum setiap pembuatan tunggal maupun massal, studio selalu menampilkan tabel estimasi biaya USD dan meminta batas anggaran maksimal.

---

## 3. Arti Kode Error Umum (`402`, `404`, `429`)

- **Error `402` (Saldo / Kredit Tidak Cukup):**
  Saldo/kredit pada penyedia LLM atau AI Video Anda tidak mencukupi untuk model atau kuota `max_tokens` yang dipilih.
  *Solusi:* Isi kredit akun penyedia Anda, buka **Pengaturan** untuk memilih model gratis, atau siapkan **Penyedia AI cadangan**.
- **Error `404` (Model Tidak Ditemukan / Dihapus):**
  Slug model yang diisi sudah tidak tersedia di penyedia (mis. versi `:free` lama sudah dipensiunkan).
  *Solusi:* Buka **Pengaturan**, klik muat daftar model atau masukkan slug model aktif terbaru.
- **Error `429` (Rate Limit / Terlalu Banyak Permintaan):**
  Melampaui batas permintaan per menit/hari penyedia API. Studio otomatis mencoba ulang bertahap (*backoff*) dan berpindah ke **Penyedia AI cadangan** jika kuota harian habis. Bab naskah atau batch adegan yang sudah selesai tetap aman tersimpan.

---

## 4. Preset Penyedia Gambar AI (OpenAI Compatible)

Di **Pengaturan → Pengaturan sumber materi** (*OpenAI Compatible*), tersedia preset siap pakai yang mengisi **Base URL**, **Model**, dan ukuran **16:9** (`1344x768`) secara otomatis **dengan API key terpisah per preset (`openai_image_api_keys_by_preset`)** sehingga pergantian preset tidak pernah menimpa key preset lain:

| Preset | Base URL | Slug Model | Catatan Teknis & Lisensi (Diakses 2026-11-23) |
|---|---|---|---|
| **Together AI** | `https://api.together.xyz/v1` | `black-forest-labs/FLUX.1-schnell` | Studio otomatis menyesuaikan payload (`aspect_ratio`, `width`, `height`, `steps`, `response_format`). Tarif resmi: [together.ai/pricing](https://together.ai/pricing). Referensi: [docs.together.ai](https://docs.together.ai/docs/inference/images/overview). |
| **DeepInfra** | `https://api.deepinfra.com/v1/openai` | `black-forest-labs/FLUX-1-schnell` | Mendukung parameter OpenAI `size` dan `b64_json`. Tarif di halaman model resminya memakai rumus `\$0.0005 × (width / 1024) × (height / 1024) × iters`. Referensi: [deepinfra.com/black-forest-labs/FLUX-1-schnell](https://deepinfra.com/black-forest-labs/FLUX-1-schnell). |
| **Kustom** | *(isi manual)* | *(isi manual)* | Untuk gateway lokal (ComfyUI / Automatic1111) atau penyedia OpenAI-compatible lain. |

- **Status Nebius Token Factory:** Seluruh model *Text-to-Image* pada Nebius dinonaktifkan mulai 13 April 2026 ([docs.tokenfactory.nebius.com/other-capabilities/deprecation-info](https://docs.tokenfactory.nebius.com/other-capabilities/deprecation-info)), sehingga tidak disertakan sebagai preset.
- **Catatan Lisensi Model FLUX.1:**
  - `black-forest-labs/FLUX.1-schnell`: berlisensi **Apache 2.0**, dapat digunakan untuk penggunaan pribadi, ilmiah, maupun komersial ([huggingface.co/black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell)).
  - `black-forest-labs/FLUX.1-dev` *(jika dipakai manual)*: memakai lisensi **non-komersial** khusus ([huggingface.co/black-forest-labs/FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev)); perhatikan ketentuannya sebelum pemakaian komersial.
- **Tombol `🧪 Uji 1 gambar`:** Gunakan **🧪 Uji 1 gambar** di Pengaturan atau panel **Sumber footage** Video Panjang untuk mengetes preset aktif beserta kategori error aman (`401/403`, `402`/kuota, `404`, `400/422`, atau timeout) tanpa pernah menampilkan API key.

---

## 5. **Penyedia AI cadangan** (Auto-Failover Lintas Penyedia)

Agar penulisan naskah, kata kunci AI, *Konteks Visual*, dan penyusunan adegan tetap jalan meski penyedia utama kehabisan kuota harian atau error `401`/`402`/`404`/`429`:

### Letak & Pengaturan di Antarmuka
- Buka **Pengaturan → Umum → Penyedia AI cadangan (otomatis pindah saat kuota/error)** *(dalam bahasa Inggris: **Settings → General → Backup AI providers**)*.
- **Penyedia utama:** Mengikuti pilihan aktif di tab **Umum** (klik tombol gunakan penyedia) atau tab pengaturan LLM. Perubahan saat render berjalan diterapkan setelah render selesai.
- **Urutan penyedia cadangan (dicoba berurutan):** Pilih dan atur urutan cadangan (disimpan di `config/longform.toml` → `llm_failover_chain`, default urutan umum: **`gemini`**, **`groq`**, **`deepseek`**).

### Arti Status Rantai Penyedia AI
Di bawah daftar urutan tampil **Status rantai penyedia AI:** per penyedia:
- **`✅ Siap`:** API key & model valid dan siap dipakai cadangan.
- **`⚠️ Belum ada key` / `Perlu model`:** Dilewati otomatis tanpa membuang percobaan permintaan jaringan.
- **`⚠ format key tidak biasa`:** Awalan/panjang key berbeda dari pola lazim penyedia tersebut — tetap dapat disimpan dan diuji dengan **🔍 Uji semua penyedia AI** bila Anda memakai key/proxy kustom.
- **`⏸ Dijeda sementara ({until})`:** Penyedia dijeda otomatis sampai waktu reset (mis. `00:00 UTC` / **07:00 WIB** untuk limit harian OpenRouter) atau sampai API key/model diganti pada error `401`/`402`/`404`.

### Tombol `🔍 Uji semua penyedia AI` & Saran Kombinasi Penyedia Berbeda
1. **Klik `🔍 Uji semua penyedia AI`:** Menguji penyedia utama beserta seluruh daftar cadangan secara berurutan, lalu menampilkan status `✅ <Penyedia> (<model>): OK` atau pesan alasan gagal tanpa pernah membocorkan API key.
2. **Gunakan penyedia yang berbeda — bukan menumpuk key OpenRouter gratisan:**
   - Mengacu pada dokumentasi resmi [OpenRouter Limits](https://openrouter.ai/docs/api/reference/limits) *(dicek: 2026-11-27)*: *"Spend windows are UTC: a daily budget resets at 00:00 UTC"* — akun dengan total pembelian kredit `< $10` dibatasi **50 request/hari** dan **20 request/menit** untuk model `:free`. Membuat beberapa key dari satu akun tidak menambah kuota harian gratis.
   - **Kombinasi paling efektif untuk pemula:** Jadikan **OpenRouter** (`openrouter/free`) dan **Google Gemini Flash** (mis. `gemini-3.5-flash-lite` atau `gemini-2.5-flash`) sebagai pasangan utama + cadangan, lalu tambahkan **Groq** atau **DeepSeek** sebagai cadangan berikutnya.
3. **Referensi kebijakan batas resmi penyedia *(dicek: 2026-11-26 & 2026-11-27)*:**
   - **Google Gemini:** Batas RPM, TPM, dan RPD berlaku per proyek Google Cloud (bukan per API key) dan reset tengah malam waktu Pasifik ([ai.google.dev/gemini-api/docs/rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits)).
   - **Groq:** Batas RPM, RPD (reset tengah malam UTC), TPM, dan TPD berlaku per organisasi dan model ([console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits)).
   - **DeepSeek:** Menggunakan kontrol konkurensi dinamis berbasis saldo prabayar ([api-docs.deepseek.com/quick_start/rate_limit](https://api-docs.deepseek.com/quick_start/rate_limit)).
