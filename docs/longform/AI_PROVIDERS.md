# Audit & Migrasi Penyedia AI Long Video ke Lapisan MoneyPrinterTurbo (Tahap 8B.1)

Dokumen ini mencatat hasil audit menyeluruh atas cara **MoneyPrinterTurbo (MPT) v1.3.10** mengelola penyedia AI (**LLM / Naskah**, **TTS / Suara**, **Gambar AI / Visual**), membandingkannya dengan implementasi **Long Video (`app/services/longform/`)** saat ini, menjelaskan penyebab bug pemeriksaan Gemini pada *"Tulis dengan AI"*, serta menetapkan arsitektur satu-adapter per jenis layanan tanpa duplikasi kunci API.

> **Batasan Ruang Lingkup (8B.1 vs 8B.2):**
> - **Termasuk di 8B.1:** Naskah (LLM), Suara (TTS), Gambar AI (OpenAI Images & Codex Image), serta satu pintu pengaturan melalui dialog Settings MPT (`webui/Main.py`).
> - **Di luar ruang lingkup 8B.1 (ditunda ke 8B.2):** Cek relevansi footage berbasis AI, terjemahan kata kunci ke bahasa Inggris, dan *"Buat dengan AI Video"* per shot (WaveSpeed).

---

## 1. Tabel Audit Penyedia AI (MPT vs Long Video)

### A. LLM / Pembuatan Naskah

| Aspek | MoneyPrinterTurbo (Upstream) | Long Video Saat Ini (Sebelum 8B.1) | Kesenjangan & Rencana Migrasi (8B.1) |
|---|---|---|---|
| **Daftar Penyedia** | `app/models/llm_provider.py` (`LLM_PROVIDER_REGISTRY` — 44 penyedia: OpenAI, OpenRouter, Gemini, DeepSeek, Ollama, Moonshot, Azure, Groq, Anthropic/Claude, Codex, ChatGPT OAuth, Claude Code CLI, Kimi Code, Gemini CLI, Qwen, dll.) | `app/services/longform/llm_adapter.py` sudah memanggil `app.services.llm._generate_response(...)`, namun UI memeriksa kesiapan secara manual | Gunakan satu sumber kebenaran dari registry MPT & helper kesiapan MPT untuk semua 44 penyedia |
| **Cek Kesiapan (Readiness)** | `webui/settings_providers.py::configured_llm_providers(settings, oauth_status)` + status OAuth (`codex_oauth`, `chatgpt_oauth`, `kimi_code_oauth`) + CLI (`claude_code`, `gemini_cli`, `ollama` tanpa key) | 1. `app/services/longform/ui/components.py::check_has_llm_key()` membaca `f"{prov}_api_key"` langsung & memaksa `AIza` untuk Gemini<br>2. `app/services/longform/ui/security.py::check_llm_status()` mengulang pengecekan manual `requires_api_key` | Hapus logika ganda `check_has_llm_key()`; delegasikan kesiapan sepenuhnya ke `llm_adapter.get_llm_readiness()` berbasis `configured_llm_providers` + status OAuth/CLI |
| **Tes Koneksi** | `app/services/llm.py::test_connection() -> tuple[bool, str, float]` (menggunakan `_generate_response` dengan `request_timeout` pendek & pesan error ter-sanitasi) | Belum ada tombol tes koneksi di kartu *"Tulis dengan AI"* | Tambahkan tombol *"Tes koneksi"* di kartu *"Tulis dengan AI"* yang memanggil adapter (`upstream_llm.test_connection()`) |
| **Daftar Model** | `app/services/codex_models.get_catalog()`, `app/services/chatgpt_models.get_catalog()`, `webui/Main.py::get_groq_model_ids()` (`GET {base_url}/models`); penyedia lain memakai input teks bebas | Tidak ada tampilan/pilihan model di UI Long Video; memakai `config.app[f"{prov}_model_name"]` diam-diam | Tampilkan penyedia & model aktif MPT di kartu AI; dukung katalog MPT (`codex`, `chatgpt`, `groq`) serta tombol *"Muat daftar model"* via endpoint `/models` untuk penyedia yang mendukung (`openai`, `openrouter`, `gemini`, gateway OpenAI-compatible) dengan fallback input teks bebas |
| **Penanganan Error & Retry** | `app/services/llm.py::_generate_response()` menangkap semua `Exception` dan mengembalikan string `"Error: <pesan>"` (termasuk 429 rate-limit atau 404 model retired) alih-alih melempar exception | `LLMAdapter.generate()` memeriksa retry untuk `Exception`, tetapi **tidak memeriksa kembalian string `"Error: ..."`** dari `_generate_response()` sehingga error 429/404 lolos ke parser JSON | Deteksi return `"Error: ..."` di `LLMAdapter.generate()`; pisahkan `LLMRateLimitError` (retry backoff eksponensial maks 3x + pesan ramah) dan `LLMModelNotFoundError` (*"Model X tidak tersedia, pilih model lain"* + tombol ke Settings); pertahankan progres bab yang sudah selesai agar **Lanjutkan** meneruskan bab yang belum jadi |
| **Pengaturan Key & Dialog** | `config.toml` (`[app]`) + dialog Settings `webui/Main.py` (tab `"llm"`, dipicu lewat `session_state["settings_dialog_open"] = True`, `settings_dialog_target_tab = "llm"`, `st.switch_page("Main.py")`) | Pesan peringatan statis meminta mengisi `config.toml` manual | Tambahkan tombol *"Ganti penyedia/model"* yang langsung membuka dialog Settings MPT tab `"llm"` (tanpa mengubah kode upstream `Main.py`) |

---

### B. TTS / Sintesis Suara

| Aspek | MoneyPrinterTurbo (Upstream) | Long Video Saat Ini (Sebelum 8B.1) | Kesenjangan & Rencana Migrasi (8B.1) |
|---|---|---|---|
| **Daftar Penyedia** | `webui/Main.py` (11 server TTS): `azure-tts-v1` (Edge TTS), `azure-tts-v2`, `siliconflow`, `gemini-tts`, `mimo-tts`, `minimax-tts`, `elevenlabs`, `chatterbox`, `kokoro`, `fish_audio`, `voxcpm` | `app/services/longform/tts_adapter.py::TTSAdapter` memvalidasi hanya `"edge"`, `"openai"`, `"gemini"`, dan UI langkah Suara hardcoded memanggil `list_available_voices()` (Edge saja) dan `TTSAdapter(provider="edge")` untuk sampel | Dukung pemilihan penyedia TTS dari daftar penyedia MPT yang siap (Edge / `azure-tts-v1` selalu tersedia; lainnya muncul jika kredensial/konfigurasinya terisi), plus pemetaan kompatibel mundur `"edge"` <-> `"azure-tts-v1"` |
| **Daftar Suara per Penyedia** | `app/services/voice.py`: `get_all_azure_voices()`, `get_siliconflow_voices()`, `get_gemini_voices()`, `get_mimo_voices()`, `get_minimax_voices()`, `get_elevenlabs_voices(api_key)`, `get_chatterbox_voices()`, `get_kokoro_voices()`, `get_fish_audio_voices()`, `get_voxcpm_voices()` | Hanya `get_all_azure_voices()` yang dipanggil oleh `voice_service.list_available_voices()` | Tambahkan query suara per penyedia di `voice_service` yang mendelegasikan ke fungsi `app.services.voice` sesuai penyedia terpilih |
| **Word Timing & Sinkronisasi Subtitle** | Hanya `azure_tts_v1` (mengisi `sub_maker.cues`) dan `azure_tts_v2` (word boundary ke `sub_maker.subs` & `sub_maker.offset` dalam satuan 100ns) yang menyediakan timing kata asli. Penyedia lain memanggil `populate_legacy_submaker_with_full_text()` yang hanya memperkirakan durasi kalimat secara proporsional | `tts_adapter.py` hanya membaca `sub_maker.cues` dan memanggil `align_group_segments(..., allow_whisper=False)` — akibatnya word boundary `azure-tts-v2` terabaikan dan penyedia tanpa word timing tidak pernah memakai Whisper (`alignment.py::align_with_whisper`) | 1. Ekstrak word cues dari `sub_maker.cues` **dan** `sub_maker.subs`/`offset` (`azure-tts-v2`).<br>2. Saat penyedia tidak menghasilkan word timing asli, jalankan fallback **Whisper** (`allow_whisper=True`) dan tampilkan catatan di UI bahwa proses lebih lama.<br>3. Tetap konversi semua keluaran audio ke WAV PCM mono 48 kHz (`audio_util.convert_to_wav_48k_pcm`). |
| **Hash & Status Stale** | — | `hashing.py::compute_audio_group_hash()` sudah memasukkan `tts_provider` dan `voice_name`; namun `VoiceService.set_voice()` sebelumnya tidak menerima perubahan `tts_provider` dan memakai `g.audio.tts_provider or "edge"` | Perbarui `VoiceService.set_voice(..., tts_provider=...)` dan simpan `tts_provider` di `preset_snapshot` sehingga pergantian penyedia hanya menandai grup terdampak sebagai `perlu_diperbarui` (dan tidak membuang yang cocok hash-nya) |

---

### C. Gambar AI / Visual

| Aspek | MoneyPrinterTurbo (Upstream) | Long Video Saat Ini (Sebelum 8B.1) | Kesenjangan & Rencana Migrasi (8B.1) |
|---|---|---|---|
| **Penyedia Aktif** | `app/services/material.py`:<br>1. `openai_image`: `is_openai_image_enabled()` + `generate_images_openai(...)` (endpoint OpenAI `/images/generations` kompatibel)<br>2. `codex_image`: login `codex_oauth` + `codex_image_model_name` + `generate_images_codex(...)` | `app/services/longform/visual/ai_image.py::AIImageProvider` hanya membaca `openai_image_base_url`, `openai_image_model`, `openai_image_api_keys` dan hanya memanggil `generate_images_openai` | Dukung kedua backend gambar AI MPT (`openai_image` dan `codex_image`), dengan deteksi kesiapan otomatis dari konfigurasi & status OAuth MPT |
| **UI Langkah Visual** | `webui/Main.py` membaca `configured_material_providers(settings, groups, codex_connected)` dan `material.is_openai_image_enabled()` | `app/services/longform/ui/components.py:1605` mengunci checkbox secara statis: `col_s5.checkbox(t("prov_ai_no_key"), value=False, disabled=True)` (*"Gambar AI (Belum ada key OpenAI)"*) | Cek kesiapan penyedia Gambar AI dari adapter; aktifkan checkbox bila siap dengan label nama penyedia aktif (mis. *"Gambar AI (OpenAI Images)"* atau *"Gambar AI (Codex Image)"*), serta tampilkan perkiraan jumlah gambar & informasi biaya sebelum job dimulai |

---

## 2. Akar Masalah: Mengapa "Tulis dengan AI" Masih Mengecek Gemini Secara Khusus

Berdasarkan inspeksi langsung pada kode sumber di branch baseline:

1. **`app/services/longform/ui/components.py` (`check_has_llm_key()`, baris 134–146):**
   - Membaca langsung `prov = (config.app.get("llm_provider") or "openai")` dan memeriksa `config.app.get(f"{prov}_api_key")`.
   - Tidak memanggil `get_llm_provider(prov)` maupun `configured_llm_providers(...)` dari MPT. Penyedia berbasis OAuth (`openai_codex`, `openai_chatgpt`), CLI (`claude_code`, `gemini_cli`), atau lokal (`ollama`) tidak memiliki `<prov>_api_key`, sehingga selalu dianggap tidak terkonfigurasi.
   - Memiliki pengecekan keras:
     ```python
     if ("gemini" in prov or "google" in prov) and not key.startswith("AIza"):
         return False
     ```
2. **`app/services/longform/ui/security.py` (`check_llm_status()`, baris 31–93):**
   - Mengulang secara manual logika validasi provider dari registry dan juga menyematkan pemeriksaan prefiks `AIza` untuk Gemini secara terpisah dari lapisan MPT.
3. **Dampaknya:** UI Long Video memiliki dua logika pengecekan sendiri yang terpisah dari MPT Settings (`webui/Main.py` & `webui/settings_providers.py`). Dengan memusatkan kesiapan LLM di `LLMAdapter` menggunakan `configured_llm_providers` + status OAuth/CLI MPT (dan menjaga validasi format `AIza` **hanya** saat penyedia aktif adalah Gemini), seluruh 44 penyedia MPT langsung didukung secara seragam tanpa penyimpanan key ganda.
