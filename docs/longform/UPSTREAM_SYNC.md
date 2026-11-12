# Sinkronisasi Upstream MoneyPrinterTurbo: v1.3.9 -> v1.3.10 (Tahap 7.9)

Dokumen ini mencatat inventaris pra-merge, analisis perubahan upstream, verifikasi titik sentuh longform, strategi penyelesaian konflik, hasil pengujian nyata, dan inventaris penyedia AI untuk branch `stage/7.9-upstream`.

---

## 1. Inventaris Sebelum Merge (U0)

### 1.1 Titik Fork & Versi Target
- **Titik Fork Asal:** `c8dbf96e9dfee43b30f7c3f440175d6bd4de61de` (Tag `v1.3.9` / merge-base).
- **Versi Target:** **`v1.3.10`** (Tag rilis resmi, commit `8b0206be2c7977e3bc3cf25ff99a2adc3c9c65f9`).
- **Alasan Pemilihan `v1.3.10`:**
  - `v1.3.10` adalah rilis resmi stabil terbaru dari upstream.
  - Memenuhi notifikasi pada WebUI: *"Update available: v1.3.10"*.
  - Sesuai prinsip U0: *menggunakan tag rilis terbaru, bukan commit acak di `upstream/main`*.
  - Commit di `upstream/main` setelah `v1.3.10` (`dd09658` s/d `66db0e5`) belum dirilis resmi dan mencakup refactoring video/subtitle yang belum ditandai stabil.

### 1.2 Daftar Commit Baru di Upstream (`c8dbf96..v1.3.10`)
Total 9 commit:
1. `e308df6` - `chore: update Jalapeno Cloud offer copy`
2. `76614ae` - `fix(video): remove jitter from the slow image zoom by scaling with sub-pixel precision`
3. `0501fac` - `chore: add sponsorship information for Whop in README`
4. `22c4671` - `feat(llm): add cheapaimodel provider`
5. `d72bb58` - `fix(material): preserve repeated topics (#1772)`
6. `ace6c8f` - `fix(voice): support script subtags (#1773)`
7. `842525d` - `fix(subtitle): keep similarity() bounded for empty and case-folded input`
8. `bf33010` - `test: make three path assertions hold on Windows`
9. `8b0206b` - `chore: release v1.3.10`

---

## 2. Ringkasan Perubahan Penting Upstream

| Komponen | Perubahan di Upstream v1.3.10 | Dampak pada Longform / MPT |
|---|---|---|
| **Versi & Metadata** | `app/__init__.py`, `pyproject.toml`, `uv.lock` dinaikkan ke `1.3.10` | Menghilangkan banner *"Update available: v1.3.10"* |
| **Penyedia LLM** | Menambahkan `cheapaimodel` ke `app/models/llm_provider.py` | Tambahan 1 provider LLM OpenAI-compatible (bahan Tahap 8B) |
| **Penyedia TTS / Voice** | `app/services/voice.py` mendukung script subtags pada Edge-TTS (mis. `iu-Cans-CA-...`) | Meningkatkan kompatibilitas suara |
| **Material Video** | `app/services/material.py` memperbaiki deduping topik berulang dalam satu naskah | Klip berulang pada pencarian term yang sama tetap teralokasi |
| **Engine Video** | `app/services/video.py` mengadopsi sub-pixel precision affine warp (`_smooth_zoom_clip`) | Menghilangkan getaran/jitter pada zoom foto di video pendek MPT |
| **Subtitle** | `app/services/subtitle.py` membatasi `similarity()` dalam $[0, 1]$ pada case-folding dan empty string | Stabilisasi perbandingan teks subtitle |
| **Pengujian Windows** | Commit `bf33010` memperbaiki penanganan path di Windows untuk `test_material.py` dan `test_utils_ffmpeg_check.py` | Mengurangi kegagalan tes Windows upstream |
| **Config TOML** | `config.example.toml` menambahkan contoh konfigurasi `[cheapaimodel]` | Tidak ada field yang diubah atau dihapus |
| **WebUI & CSS** | `webui/Main.py` dan `webui/styles.css` **NOL perubahan** | Hook G5 tetap murni tanpa modifikasi eksternal |
| **Dependensi Paket** | Tidak ada dependensi third-party baru; Streamlit tetap `1.43.2` | Lingkungan virtual (`uv`) tetap stabil |

---

## 3. Titik Sentuh Longform ke Kode Upstream

Pemeriksaan menyeluruh seluruh import dari `app.*` pada modul longform:

1. **`app.config.config`**:
   - Digunakan di `llm_adapter.py`, `tts_adapter.py`, `visual/stock.py`, `visual/ai_image.py`, `ui/components.py`, `ui/security.py`.
   - *Status:* **Tidak berubah.** Format dan interface pembacaan config identik.
2. **`app.services.llm._generate_response`**:
   - Digunakan di `llm_adapter.py`.
   - *Status:* **Tidak berubah.** Signature fungsi tetap.
3. **`app.models.llm_provider.get_llm_provider` & `DEFAULT_LLM_PROVIDER_ID`**:
   - Digunakan di `llm_adapter.py`, `ui/security.py`.
   - *Status:* **Tidak berubah.** Penambahan `cheapaimodel` bersifat aditif.
4. **`app.services.voice.create_edge_tts_communicate`**:
   - Digunakan di `tts_adapter.py`.
   - *Status:* **Tidak berubah.** Signature `(text, voice_name, rate_str)` identik.
5. **`app.services.voice.populate_legacy_submaker_with_full_text`**:
   - Digunakan di `tts_adapter.py`.
   - *Status:* **Tidak berubah.**
6. **`app.services.voice.parse_voice_name`, `get_voice_duration`**:
   - Digunakan di `voice_service.py` dan `cli_longform.py`.
   - *Status:* **Tidak berubah.**
7. **`app.services.subtitle.similarity`**:
   - Digunakan di `alignment.py`.
   - *Status:* **Tidak berubah.** Normalisasi case-folding upstream justru meningkatkan ketahanan alignment kata.
8. **`app.services.material.search_videos_pixabay`, `search_videos_pexels`, `search_videos_coverr`, `generate_images_openai`**:
   - Digunakan di `visual/stock.py` dan `visual/ai_image.py`.
   - *Status:* **Tidak berubah.**
9. **`app.models.schema.VideoAspect`**:
   - Digunakan di `visual/stock.py` dan `visual/ai_image.py`.
   - *Status:* **Tidak berubah.**

---

## 4. Resolusi Merge & Pembaruan Dependensi (U1)

### 4.1 Eksekusi Merge
- Perintah: `git merge v1.3.10 -m "merge: sync upstream MoneyPrinterTurbo v1.3.10"`
- Hasil: **Merge made by the 'ort' strategy** (Clean merge, 0 konflik).
- Berkas termodifikasi: 20 files changed, 420 insertions(+), 29 deletions(-).
- Hook G5 di `webui/Main.py` (6 baris) dan aturan `.gitignore` (`!test/longform/*.py`) tetap utuh tanpa disentuh.

### 4.2 Sinkronisasi Dependensi (`uv sync`)
- Output:
  ```
  Resolved 131 packages in 1ms
  Building mpt @ file:///F:/MPTLong
  Built mpt @ file:///F:/MPTLong
  Uninstalled 1 package in 349ms
  Installed 1 package in 303ms
   - mpt==1.3.9 (from file:///F:/MPTLong)
   + mpt==1.3.10 (from file:///F:/MPTLong)
  ```
- Seluruh 131 dependensi tetap kompatibel (Streamlit 1.43.2, moviepy 2.1.2, edge-tts, pydantic, loguru).

### 4.3 Perlindungan & Status Konfigurasi (`config.toml`)
- File `config.toml` berada dalam `.gitignore` dan tidak ditimpa/dihapus oleh proses merge.
- Upstream v1.3.10 menambahkan field opsional `[cheapaimodel]` pada `config.example.toml`. Seluruh konfigurasi pengguna (Pixabay, Coverr, OpenAI, Gemini) tetap utuh dan aktif.

---

## 5. Hasil Pengujian Nyata (U3)

### 5.1 Uji Nyata Long Video: `test_sync_79`
Dibuat dari `test/resources/sample_script.md` dengan alur penuh:
1. **Inisialisasi Proyek:**
   - Perintah: `uv run python cli_longform.py project create --id test_sync_79 --title "Test Upstream Sync 7.9" --preset documentary_en --script test/resources/sample_script.md`
   - Hasil: Sukses (1 Bab, 4 Segmen Visual, 45 Kata, Estimasi 19.3s).
2. **Sintesis Suara (TTS & Alignment):**
   - Perintah: `uv run python cli_longform.py audio synth test_sync_79`
   - Hasil: 1/1 grup selesai, durasi 19.8s, alignment `word_cue`: 1.
3. **Pipeline Visual (Alokasi Shot, Fetch, & Build):**
   - Perintah: `uv run python cli_longform.py visual pipeline test_sync_79`
   - Hasil: 4 shot dialokasikan, 4 asset diunduh via Pixabay, 4 klip dibangun (594 frames, drift `+0f`).
4. **Render Lengkap:**
   - Perintah: `uv run python cli_longform.py render all test_sync_79`
   - Hasil: Sukses dalam 9.18s, durasi akhir 19.82s (drift 22.0ms, batas aman), loudness -14.7 LUFS. Berkas `final_video.mp4` tercipta.
5. **Ekspor & Pemasangan CapCut (`--install`):**
   - Perintah: `uv run python cli_longform.py capcut export test_sync_79 --install --yes`
   - Hasil: Berhasil dipasang ke folder CapCut draft: `C:\Users\IT SUPPORT 2\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft\MPT_test_sync_79_20261112-1131`. Selisih timeline vs final video: 0.022s (dalam toleransi 1 frame).

### 5.2 Uji Nyata Video Pendek Asli MPT
- Dijalankan via pipeline asli MPT (`app.services.task.start`):
  * Subjek: *"The Forest Morning"*
  * Naskah manual: *"The morning sun illuminates the quiet green forest."*
  * Sumber video: Pixabay (`forest, nature`)
  * Tanpa LLM.
- Hasil: Task `fd2b8937-fe9a-4dce-8c23-45606243cb91` selesai dengan sukses dalam 68 detik, menghasilkan video komposit ber-subtitle:
  `F:\MPTLong\storage\tasks\fd2b8937-fe9a-4dce-8c23-45606243cb91\final-1.mp4`. Fitur asli MPT berjalan 100% tanpa gangguan.

### 5.3 Verifikasi WebUI & Versi
- `Main.py` memuat tombol/link navigasi `🎬 Long Video` (via `st.page_link`).
- `test_stage7_g_fixes.py` (8/8 passed) dan `test_stage7_h_fixes.py` (8/8 passed) mengonfirmasi sidebar Navigasi Studio tunggal dan styling bebas redundansi.
- Versi pada brand header: `v1.3.10`.
- Notifikasi *"Update available"* bernilai `None` (tidak ditampilkan) karena versi lokal telah mutakhir.

---

## 6. Verifikasi Pengujian (Baseline vs Sekarang)

### 6.1 `test/longform` (CapCut Ditutup)
- **Hasil:** **227 passed in 159.10s (100% lolos)**.

### 6.2 `test/services` (Upstream Test Suite)
- **Baseline Pra-Merge (v1.3.9):**
  * `5 failed, 3187 passed, 64 skipped, 2 warnings, 12884 subtests passed`
  * 2 kegagalan disebabkan penanganan path Windows pada:
    1. `test_material.py::test_metadata_probe_failure_preserves_existing_cache`
    2. `test_utils_ffmpeg_check.py::test_normalizes_home_path_in_os_environ`
- **Pasca-Merge (v1.3.10):**
  * Berkat commit upstream `bf33010`, kedua pengujian Windows di atas **kini LULUS (Passed)**.
  * Dua modul tes baru upstream (`test_image_motion.py` dan `test_subtitle_similarity.py`) lulus 100% (17 passed).
  * Sisa 3 tes yang belum lewat adalah isu lingkungan Windows yang memang ada di upstream asli:
    1. `test_material_cache_duration.py` (nonfinite float duration)
    2. `test_webui_generation_defaults.py` (disabled widget interaction pada AppTest)
    3. `test_webui_task_history.py` (WinError 1314: privilege symlink Windows)

---

## 7. Inventaris Penyedia AI MPT yang Tersedia (Bahan Tahap 8B)

Berikut adalah daftar lengkap penyedia yang terdaftar di core MPT v1.3.10 yang siap digunakan untuk standardisasi layer penyedia pada Tahap 8B:

### 7.1 Penyedia LLM (44 Provider Terdaftar di `LLM_PROVIDER_REGISTRY`)
1. **moonshot** (default: `kimi-k3`)
2. **kimi_code** (default: `kimi-for-coding`)
3. **openai** (default: `gpt-5.5`)
4. **openai_codex**
5. **openai_chatgpt**
6. **anthropic** (default: `claude-sonnet-5`)
7. **gemini** (default: `gemini-3.1-pro-preview`)
8. **muse_spark** (default: `muse-spark-1.3`)
9. **deepseek** (default: `deepseek-v4-pro`)
10. **qwen** (default: `qwen-max`)
11. **azure** (default: `gpt-35-turbo`)
12. **volcengine** (default: `doubao-seed-2-1-turbo-260628`)
13. **grok** (default: `grok-4.3`)
14. **minimax** (default: `MiniMax-M3`)
15. **mimo** (default: `mimo-v2.5-pro`)
16. **iflytek** (default: `spark-x2.5`)
17. **jalapeno** (default: `DeepSeek-V4.1-Flash`)
18. **infistar** (default: `gpt-6-luna`)
19. **shengsuanyun** (default: `deepseek/deepseek-v4-flash`)
20. **apimart** (default: `gpt-5.6-terra`)
21. **cloudflare** (default: `openai/gpt-4.1-mini`)
22. **modelscope** (default: `ZhipuAI/GLM-5.2`)
23. **aihubmix** (default: `gpt-5.4-mini`)
24. **aimlapi** (default: `openai/gpt-5-5`)
25. **evolink** (default: `gpt-5.5`)
26. **openrouter** (default: `minimax/minimax-m3:free`)
27. **api_route** (default: `gpt-5.4-mini`)
28. **fluxionai** (default: `gpt-5.5`)
29. **cheaperinference** (default: `gpt-5.4-mini`)
30. **requesty** (default: `openai/gpt-5.4-mini`)
31. **futureinfra** (default: `openai/gpt-4o-mini`)
32. **yapi** (default: `deepseek/deepseek-v4-flash`)
33. **opper** (default: `gpt-5.4-mini`)
34. **onomeo** (default: `auto`)
35. **empiriolabs** (default: `qwen3-8-flash`)
36. **runcrate** (default: `deepseek-ai/DeepSeek-V4.1-Flash`)
37. **cloudgpu** (default: `deepseek-v4-flash`)
38. **cheapaimodel** (BARU di v1.3.10; default: `deepseek-v4-1-flash`)
39. **ollama** (lokal)
40. **claude_code**
41. **oneapi**
42. **litellm** (default: `openai/gpt-4o-mini`)
43. **groq** (default: `openai/gpt-oss-120b`)
44. **pollinations** (default: `openai-fast`)

### 7.2 Penyedia TTS / Audio
1. **edge_tts** (Microsoft Edge Online TTS - zero-cost, multi-language, multi-voice, script-subtag support)
2. **azure_tts_v1** & **azure_tts_v2** (Azure Cognitive Speech Services)
3. **elevenlabs_tts** (ElevenLabs High-Fidelity Voice Synthesis)
4. **gemini_tts** (Google Gemini Voice API)
5. **minimax_tts** (MiniMax Audio API)
6. **mimo_tts** (Mimo Speech)
7. **siliconflow_tts** (SiliconFlow Voice Services)
8. **chatterbox_tts** (Chatterbox Engine)
9. **kokoro_tts** (Kokoro Local & Lightweight TTS)
10. **fish_audio_tts** (Fish Audio TTS)
11. **voxcpm_tts** (VoxCPM Voice Engine)
12. **_openai_compatible_tts** (OpenAI Audio Speech-compatible TTS endpoints)

### 7.3 Penyedia Material Video & Gambar
1. **Stock Video:**
   - Pixabay Video API (`search_videos_pixabay`)
   - Pexels Video API (`search_videos_pexels`)
   - Coverr Video API (`search_videos_coverr`)
2. **AI Image Generation:**
   - OpenAI DALL-E / Image (`generate_images_openai`)
   - Codex Image Context (`generate_images_codex`)
3. **AI Video Generation / On-Demand Pipelines:**
   - VolcEngine Seedance (`_download_videos_seedance_on_demand`)
   - Metaso MiniMax (`_download_videos_metaso_minimax_on_demand`)
   - Ofox (`_download_videos_ofox_on_demand`)
   - MuAPI (`_download_videos_muapi_on_demand`)
   - InfiStar Video (`_download_videos_infistar_on_demand`)
   - Jalapeno Video
   - TwelveLabs (`twelvelabs.py`)
   - LoomLoom (`loomloom.py`)
   - WaveSpeed (`_download_videos_wavespeed_on_demand`)
