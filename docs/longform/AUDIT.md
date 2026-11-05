# Audit Arsitektur Upstream MoneyPrinterTurbo

Dokumen ini merupakan hasil audit arsitektur menyeluruh terhadap codebase upstream `MoneyPrinterTurbo` (HEAD `c8dbf96`), dievaluasi untuk kebutuhan pengembangan mode **Long Video** (10–20 menit, ~1.500–3.000 kata, 4–6 bab).

---

## 1. Peta Alur `_run_pipeline` (`app/services/task.py`)

Alur eksekusi utama berada pada fungsi `_run_pipeline` di [app/services/task.py:1549-1981](file:///F:/MPTLong/app/services/task.py#L1549-L1981).

### 1.1 Tahapan Eksekusi, Fungsi, Input/Output, dan Penyimpanan

| No | Tahap (`generation_progress.stage`) | Fungsi Pemanggil | Input Utama | Output Utama | Lokasi File / State | Progress |
|:---|:---|:---|:---|:---|:---|:---|
| 0 | `preparing` | `video.validate_subtitle_colors`, preflight provider checks | `params: VideoParams` | Validasi kredensial & FFmpeg | `sm.state.update_task` | 5% |
| 1 | `script` | `generate_script(task_id, params)` -> `llm.generate_script` | `video_subject`, `paragraph_number`, prompts | `video_script: str` | In-memory / State | 10% |
| 2 | `terms` | `generate_terms(task_id, params, video_script)` -> `llm.generate_terms` | `video_subject`, `video_script` | `video_terms: list[str]` | `storage/tasks/<id>/script_data.json` | 20% |
| 3 | `audio` | `generate_audio(task_id, params, video_script)` -> `voice.tts` | `video_script`, `voice_name`, `voice_rate` | `audio_file`, `audio_duration`, `sub_maker` | `storage/tasks/<id>/audio.mp3` | 30% |
| 4 | `subtitle` | `generate_subtitle(task_id, params, video_script, sub_maker, audio_file)` | `sub_maker`, `video_script`, `audio_file` | `subtitle_path: str` | `storage/tasks/<id>/subtitle.srt` | 40% |
| 5 | `materials` | `get_video_materials` -> `download_videos` / `preprocess_video` | `video_terms`, `audio_duration`, `video_materials` | `downloaded_videos: list[str]` | `storage/cache/vid-*.mp4` atau `storage/local_videos/` | 50% |
| 6 | `rendering` | `generate_final_videos` -> `video.combine_videos` + `video.generate_video` | `downloaded_videos`, `audio_file`, `subtitle_path` | `combined-*.mp4`, `final-*.mp4` | `storage/tasks/<id>/combined-1.mp4`, `final-1.mp4` | 50% - 100% |
| 7 | (opsional) | `_schedule_cross_post` | `final-*.mp4`, metadata | Upload status | Background thread pool | 100% |

### 1.2 Mekanisme Pencatatan Progress dan State

1. **State Manager (`app/services/state.py`)**:
   - `sm.state.update_task(task_id, state=..., progress=..., **kwargs)` menyimpan state di memory (`sm._tasks`) atau Redis jika diaktifkan.
   - Status nilai task: `const.TASK_STATE_PROCESSING` (progress 5%–99%), `const.TASK_STATE_COMPLETE` (100%), atau `const.TASK_STATE_FAILED`.
   - File state lokal juga ditulis ke file disk via `task_artifacts.write_script_data`.
2. **Generation Progress Scope (`app/services/generation_progress.py`)**:
   - `generation_progress.task_scope(task_id)` mengikat thread local context.
   - `generation_progress.stage("script" | "terms" | "audio" | "subtitle" | "materials" | "rendering")` memancarkan telemetry status real-time untuk WebUI polling / SSE.
3. **Heartbeat Thread**:
   - Selama proses encoding FFmpeg dan rendering MoviePy yang memakan waktu lama, `_stage_heartbeat` ([app/services/video.py:517-542](file:///F:/MPTLong/app/services/video.py#L517-L542)) mencetak log heartbeat tiap 30 detik agar task tidak dianggap macet (*stalled*).

---

## 2. Audit Kelayakan Modul untuk Long Video (10–20 Menit)

Berikut evaluasi mendalam per modul upstream untuk kebutuhan video 10–20 menit:

### 2.1 Modul LLM (`app/services/llm.py`)

- **Fungsi yang BISA Dipakai Ulang**:
  - `_generate_response` ([app/services/llm.py:315](file:///F:/MPTLong/app/services/llm.py#L315)): Adapter inti untuk berbagai model AI (Moonshot/Kimi, OpenAI, Gemini, Claude, DeepSeek, Qwen, Ollama). Memiliki penanganan retry, sanitasi error, dan timeout yang sangat baik.
  - `_extract_embedded_json` ([app/services/llm.py:1026](file:///F:/MPTLong/app/services/llm.py#L1026)): Parsing JSON terisolasi dari output markdown code fence.
- **Fungsi yang TIDAK Cocok untuk Long Video**:
  - `generate_script` ([app/services/llm.py:911](file:///F:/MPTLong/app/services/llm.py#L911)):
    - **Alasan Berbasis Kode**: Menghasilkan naskah sekali jalan (*one-shot prompt*) tanpa pembagian bab atau segmen.
    - **Batasan Skema**: `paragraph_number` diatur batas maksimal `le=10` pada skema Pydantic ([app/models/schema.py:167, 224](file:///F:/MPTLong/app/models/schema.py#L167)) serta konstanta `MAX_SCRIPT_PARAGRAPH_NUMBER = 10` di [app/services/llm.py:24](file:///F:/MPTLong/app/services/llm.py#L24) dan fungsi validasi `_normalize_script_paragraph_number` di [app/services/llm.py:860](file:///F:/MPTLong/app/services/llm.py#L860).
    - Menghasilkan 1.500–3.000 kata dalam satu prompt LLM menyebabkan halusinasi, hilangnya struktur narasi YouTube, pengulangan, dan tidak adanya metadata petunjuk visual per segmen (*visual cues*).
  - `generate_terms` ([app/services/llm.py:1040](file:///F:/MPTLong/app/services/llm.py#L1040)):
    - **Alasan Berbasis Kode**: Pada [app/services/task.py:395](file:///F:/MPTLong/app/services/task.py#L395), jumlah kata kunci pencarian dikunci statis: `amount=8 if params.match_materials_to_script else 5`.
    - Untuk video 15 menit (yang membutuhkan ~150–200 footage/gambar), 5 atau 8 keyword akan menyebabkan visual berulang secara monoton dan tidak relevan dengan narasi detail tiap bab.

### 2.2 Modul Voice & TTS (`app/services/voice.py`)

- **Fungsi yang BISA Dipakai Ulang**:
  - `azure_tts_v1` ([app/services/voice.py:1579](file:///F:/MPTLong/app/services/voice.py#L1579)): Wrapper Edge-TTS dengan ekstraksi cue subtitle (`edge_tts.SubMaker`).
  - Provider TTS lainnya: `azure_tts_v2`, `elevenlabs_tts`, `minimax_tts`, `fish_audio_tts`, `kokoro_tts`, `voxcpm_tts`.
  - Fungsi utilitas audio: `get_audio_duration`, `apply_audio_volume`.
- **Fungsi yang TIDAK Cocok untuk Long Video**:
  - Pola pemanggilan `voice.tts` sekali jalan ([app/services/task.py:608-619](file:///F:/MPTLong/app/services/task.py#L608-L619)):
    - Menyuapkan seluruh teks naskah (1.500–3.000 kata) ke `azure_tts_v1` sekaligus.
    - **Batasan Edge-TTS**: WebSocket Edge-TTS rentan terhadap timeout koneksi jika durasi sintesis audio melebihi beberapa menit atau payload karakter terlalu besar (~10.000 byte).
    - Jika koneksi terputus di tengah, sintesis gagal total dan harus diulang dari awal (tanpa checkpointing).
    - Tidak ada timing per segmen: sulit menyinkronkan durasi footage/gambar per segmen dengan kalimat narasi spesifik.

### 2.3 Modul Subtitle (`app/services/subtitle.py` & `app/services/voice.py`)

- **Fungsi yang BISA Dipakai Ulang**:
  - `voice.create_subtitle` ([app/services/voice.py:3768](file:///F:/MPTLong/app/services/voice.py#L3768)): Konversi cue TTS ke format SRT dengan penyesuaian timestamp.
  - Parser SRT bawaan: `subtitle.file_to_subtitles`.
- **Fungsi yang TIDAK Cocok untuk Long Video**:
  - Mekanisme pembuatan klip subtitle MoviePy (`TextClip` per entri SRT) di [app/services/video.py:1661-1904](file:///F:/MPTLong/app/services/video.py#L1661-L1904):
    - Untuk video 15 menit, file SRT memiliki sekitar 300–600 baris subtitle (atau ribuan jika word-by-word).
    - Upstream membuat satu instance `TextClip` (Pillow rasterization) untuk setiap entri lalu membungkus semuanya ke dalam `CompositeVideoClip([video_clip, *text_clips])` ([app/services/video.py:1903](file:///F:/MPTLong/app/services/video.py#L1903)).
    - Ini adalah **penyebab utama konsumsi memori masif dan lambatnya render MoviePy**. Ribuan klip Python dievaluasi di memori pada tiap frame video.
    - **Solusi Long Video**: Menggunakan format subtitle **ASS (Advanced SubStation Alpha)** yang di-burn langsung oleh filter native FFmpeg (`-vf "ass=subtitles.ass"` via `libass`), menghilangkan ribuan TextClip Python.

### 2.4 Modul Material (`app/services/material.py`)

- **Fungsi yang BISA Dipakai Ulang**:
  - `save_video` ([app/services/material.py:1257](file:///F:/MPTLong/app/services/material.py#L1257)): Download video dengan validasi file dan caching berbasis MD5: `f"vid-{md5(url)}.mp4"`.
  - Integrasi provider stok video: Pexels, Pixabay, Coverr.
  - Download paralel: `_download_materials_in_parallel` ([app/services/material.py:2265](file:///F:/MPTLong/app/services/material.py#L2265)).
- **Fungsi yang TIDAK Cocok untuk Long Video**:
  - `download_videos` ([app/services/material.py:2361](file:///F:/MPTLong/app/services/material.py#L2361)) dan `_download_videos_by_script_order`:
    - Mengumpulkan materi video acak atau berurutan secara global berdasarkan 5–8 kata kunci untuk memenuhi total `audio_duration`.
    - Tidak memiliki konsep *segment visual matching* (misalnya: segmen 1: gambar AI Ken Burns, segmen 2: footage stok Pexels, segmen 3: file video lokal user).
    - Caching di material.py tidak melacak hash prompt visual per segmen naskah.

### 2.5 Modul Video (`app/services/video.py`)

- **Fungsi yang BISA Dipakai Ulang**:
  - `concat_video_clips_with_ffmpeg` ([app/services/video.py:626](file:///F:/MPTLong/app/services/video.py#L626)):
    - Sangat bagus! Menggunakan FFmpeg Concat Demuxer (`-f concat -safe 0 -i manifest.txt`) dengan filter BT.709, penanganan `-nostdin` (commit `c8dbf96`), dan escaping path Windows yang aman ([app/services/video.py:493](file:///F:/MPTLong/app/services/video.py#L493)).
  - Resolusi dimensi aspek rasio: `aspect.to_resolution()`.
- **Fungsi yang TIDAK Cocok untuk Long Video**:
  - `combine_videos` ([app/services/video.py:976](file:///F:/MPTLong/app/services/video.py#L976)):
    - Melakukan pemotongan dan penyesuaian rasio setiap klip di Python/MoviePy (`_fit_clip_to_canvas`), lalu looping materi jika durasi video kurang dari audio ([app/services/video.py:1248](file:///F:/MPTLong/app/services/video.py#L1248)).
    - Tidak mengenal hierarki bab/segmen: seluruh klip digabungkan menjadi satu file raksasa `combined-1.mp4`.
  - `generate_video` ([app/services/video.py:1605](file:///F:/MPTLong/app/services/video.py#L1605)):
    - Render video final berbasis MoviePy (`_write_videofile_with_codec_fallback`).
    - Merender seluruh timeline 10–20 menit dalam satu sesi MoviePy tunggal. Jika terjadi crash di menit ke-18, seluruh render 18 menit hilang dan harus dimulai dari nol.
  - `render_image_zoom_video` ([app/services/video.py:1974](file:///F:/MPTLong/app/services/video.py#L1974)):
    - Menggunakan fungsi lambda MoviePy: `clip.resized(lambda t: 1 + (clip_duration * 0.03) * (t / clip.duration))` ([app/services/video.py:1991](file:///F:/MPTLong/app/services/video.py#L1991)).
    - Tiap frame gambar di-resize ulang via Pillow di CPU Python per-frame pada 30 fps! Ini sangat lambat (membutuhkan ~2,5 detik proses untuk 5 detik video gambar).
    - FFmpeg memiliki filter native `zoompan` yang mampu merender efek Ken Burns (zoom-in, zoom-out, pan-left, pan-right) puluhan kali lebih cepat tanpa overhead Python GIL.

---

## 3. Daftar Penyedia (Providers) yang Ada di Repo

### 3.1 LLM Providers (`app/models/llm_provider.py`)

| Provider ID | Nama Tampilan | Adapter Utama | Config Keys Utama |
|:---|:---|:---|:---|
| `moonshot` (default) | Kimi / Moonshot AI | `openai_compatible` | `moonshot_api_key`, `moonshot_model_name`, `moonshot_base_url` |
| `openai` | OpenAI | `openai_compatible` | `openai_api_key`, `openai_model_name`, `openai_base_url` |
| `openai_codex` | OpenAI Codex | `openai_compatible` (OAuth) | Menggunakan session token di `storage/auth/` |
| `openai_chatgpt` | OpenAI ChatGPT | `openai_compatible` (OAuth) | Menggunakan PKCE OAuth browser lokal |
| `anthropic` | Anthropic Claude | `openai_compatible` / proxy | `anthropic_api_key`, `anthropic_model_name` |
| `gemini` | Google Gemini | `gemini` (google-genai SDK) | `gemini_api_key`, `gemini_model_name` |
| `deepseek` | DeepSeek | `openai_compatible` | `deepseek_api_key`, `deepseek_model_name` |
| `qwen` | Alibaba Cloud Qwen | `qwen` (dashscope SDK) | `qwen_api_key`, `qwen_model_name` |
| `azure` | Microsoft Azure OpenAI | `azure` (AzureOpenAI SDK) | `azure_api_key`, `azure_model_name`, `azure_base_url`, `azure_api_version` |
| `volcengine` | ByteDance VolcEngine Ark | `openai_compatible` | `volcengine_api_key`, `volcengine_model_name` |
| `ollama` | Ollama (Lokal) | `openai_compatible` | `ollama_model_name`, `ollama_base_url` |
| `claude_code` | Claude Code CLI | `claude_code` (subproc CLI) | Memanggil binary CLI `claude` |
| Lainnya | Grok, MiniMax, MiMo, OpenRouter, ModelScope, dll. | `openai_compatible` | Sesuai prefix provider di config |

### 3.2 TTS Providers (`app/services/voice.py`)

| Provider ID | Fungsi Pemanggil | Format Voice Name | Kebutuhan Kredensial / Config |
|:---|:---|:---|:---|
| `edge` (default) | `azure_tts_v1` | `zh-CN-XiaoxiaoNeural-Female`, `en-US-ChristopherNeural-Male`, dll. | Gratis, tanpa API Key |
| `azure_v2` | `azure_tts_v2` | `azure:<voice>` atau suffix `-V2` | `azure_speech_key`, `azure_speech_region` |
| `siliconflow` | `siliconflow_tts` | `siliconflow:<model>:<voice>` | `siliconflow_api_key` |
| `gemini` | `gemini_tts` | `gemini:<voice>` | `gemini_api_key` |
| `elevenlabs` | `elevenlabs_tts` | `elevenlabs:<voice_id>:<name>` | `elevenlabs_api_key` |
| `minimax` | `minimax_tts` | `minimax:<voice_id>` | `minimax_api_key`, `minimax_group_id` |
| `mimo` | `mimo_tts` | `mimo:<voice>` | `mimo_api_key` |
| `fish_audio` | `fish_audio_tts` | `fish_audio:<ref_id>` | `fish_audio_api_key` |
| `voxcpm` | `voxcpm_tts` | `voxcpm:<voice_id>` | `voxcpm_api_key` |
| `kokoro` | `kokoro_tts` | `kokoro:<voice>` | Lokal ONNX / model files |
| `chatterbox` | `chatterbox_tts` | `chatterbox:<voice>` | Lokal Chatterbox engine |

### 3.3 AI Image Providers (`app/services/material.py`)

| Provider ID | Fungsi Pemanggil | Kebutuhan Config |
|:---|:---|:---|
| `openai_image` | `generate_images_openai` | `openai_image_base_url`, `openai_image_model`, `openai_image_api_keys` |
| `codex_image` | `generate_images_codex` | OAuth credential via `codex_oauth` |

### 3.4 Video Providers (Stock & AI Video) (`app/services/material.py`)

| Provider ID | Kategori | Fungsi Pemanggil | Kebutuhan Config |
|:---|:---|:---|:---|
| `pexels` | Stock Video | `search_videos_pexels` | `pexels_api_keys` |
| `pixabay` | Stock Video | `search_videos_pixabay` | `pixabay_api_keys` |
| `coverr` | Stock Video | `search_videos_coverr` | `coverr_api_keys` |
| `local` | File Lokal | `preprocess_video` | File di `storage/local_videos/` atau path lokal |
| `wavespeed` | AI Video | `generate_videos_wavespeed` | `wavespeed_api_key` |
| `volcengine_seedance` | AI Video | `_download_videos_seedance_on_demand` | `seedance_api_key` |
| `ofox` | AI Video | `_download_videos_ofox_on_demand` | `ofox_api_key` |
| `metaso_minimax` | AI Video | `_download_videos_metaso_minimax_on_demand` | `metaso_api_key` |
| `muapi` | AI Video | `_download_videos_muapi_on_demand` | `muapi_api_key` |
| `infistar` | AI Video | `_download_videos_infistar_on_demand` | `infistar_api_key` |
| `jalapeno` | AI Video | Jalapeno endpoint | `jalapeno_api_key` |
| `loomloom` | AI Video | `loomloom.py` | `loomloom_api_key` |

---

## 4. Lokasi File Temp dan Keamanan Path Berspasi

Pada sistem pengujian, direktori profil user adalah `C:\Users\IT SUPPORT 2` (mengandung spasi). Berikut audit lokasi file temp dan sanitasi path:

| Komponen | Lokasi File Temp | Mekanisme Pembuatan | Keamanan Spasi Windows | Catatan Evaluasi |
|:---|:---|:---|:---|:---|
| **MoviePy Audio/Video** | `storage/tasks/<id>/` (atau output dir) | `tempfile.mkstemp(..., dir=output_dir)` | **AMAN** | Di-handle via `_get_temp_audio_dir(output_dir)` untuk mencegah cross-drive lock ([app/services/video.py:1630](file:///F:/MPTLong/app/services/video.py#L1630)). |
| **Edge TTS Audio** | Direktori task (`voice_file` dir) | `tempfile.mkstemp(prefix=".edge-tts-", dir=dir)` | **AMAN** | Menggunakan file descriptor OS aman; tidak melewati shell string parsing. |
| **Material Cache** | `storage/cache/` | `save_video` -> `storage/cache/vid-<md5>.mp4` | **AMAN** | Menggunakan path Python standar. |
| **FFmpeg Concat List** | Direktori output task | `tempfile.NamedTemporaryFile(..., dir=output_dir)` | **AMAN** | `_format_ffmpeg_concat_path` ([app/services/video.py:493](file:///F:/MPTLong/app/services/video.py#L493)) mengganti backslash `\` menjadi slash `/` dan meng-escape tanda kutip satu `'`. |
| **FFmpeg Filter Subtitle** | Filter `-vf subtitles=...` atau `ass=...` | *Belum ada di upstream* (MoviePy dipakai) | **KRITIKAL** | **Peringatan untuk Long Video**: Bila menggunakan filter `-vf "ass='path'"` di Windows, FFmpeg menganggap karakter `:` (drive letter seperti `C:`) dan `\` sebagai delimeter filter. Harus di-escape khusus (`C\:/path/with space`). |
| **Subprocess Execution** | Seluruh pemanggilan `subprocess.run` | Menggunakan list argumen `[exe, arg1, arg2]` | **AMAN** | Python otomatis mengutip argumen yang mengandung spasi saat membangun command line Windows (`CreateProcessW`). |

---

## 5. Titik Hook Minimal Upstream (Aturan A1)

Untuk memenuhi aturan **A1** (menjaga kode upstream tetap mudah di-merge), seluruh modul baru long video diisolasi ke dalam `app/services/longform/`.

Berikut titik integrasi minimal (*hook points*) yang direncanakan:

1. **`app/models/schema.py`**:
   - Menambahkan field opsional `mode: Literal["short", "long"] = "short"` dan `longform_params: Optional[LongVideoParams] = None` ke dalam `VideoParams`.
2. **`app/services/task.py`**:
   - Pada baris pertama `_run_pipeline` ([app/services/task.py:1562](file:///F:/MPTLong/app/services/task.py#L1562)):
     ```python
     if getattr(params, "video_mode", "short") == "long":
         from app.services.longform.pipeline import run_longform_pipeline
         return run_longform_pipeline(task_id, params, stop_at=stop_at)
     ```
   - Dengan satu hook sederhana ini, alur video pendek bawaan tidak terganggu sama sekali (100% backward compatible, mematuhi aturan A2).
3. **`cli.py`**:
   - Menambahkan opsi `--mode {short,long}` dan opsi manifest long video.
4. **`LONGFORM_CHANGES.md`**:
   - Setiap modifikasi pada file upstream dicatat rapi (file, nomor baris, dan alasan).
