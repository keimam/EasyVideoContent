# Third-Party Notices & Attributions

EasyFaceless Studio dibangun di atas komponen open-source dan layanan pihak ketiga berikut.
Dokumen ini dihasilkan secara otomatis untuk bagian dependensi Python oleh [`scripts/dev/generate_third_party_notices.py`](scripts/dev/generate_third_party_notices.py).

---

## 1. Proyek Upstream

- **MoneyPrinterTurbo** — oleh Harry (`@harry0703`), dilisensikan di bawah **MIT License** ([LICENSE](LICENSE), https://github.com/harry0703/MoneyPrinterTurbo).

---

## 2. Media, Font, & Integrasi Eksternal

### FFmpeg
- **Build dibundel pada paket Windows:** `ffmpeg version 2025-11-17-git-e94439e49b-full_build-www.gyan.dev Copyright (c) 2000-2025 the FFmpeg developers`
- **Sumber build binary:** https://www.gyan.dev/ffmpeg/builds/
- **Tautan source code resmi:** https://github.com/FFmpeg/FFmpeg/commit/e94439e49b (dan arsip resmi https://ffmpeg.org/download.html)
- **Lisensi:** GNU General Public License v3 (GPLv3). Teks lengkap lisensi GPLv3 disertakan pada paket di [`licenses/FFMPEG-GPLv3-LICENSE.txt`](licenses/FFMPEG-GPLv3-LICENSE.txt) beserta rincian build di [`licenses/FFMPEG-BUILD-INFO.txt`](licenses/FFMPEG-BUILD-INFO.txt).
- **Arsitektur pemanggilan:** EasyFaceless Studio memanggil biner eksternal `ffmpeg`/`ffprobe` sebagai subproses terpisah tanpa me-link ke kode Python aplikasi.
- **Catatan opsi LGPL:** Build `win64-lgpl` (mis. `BtbN/FFmpeg-Builds`) mengecualikan `libx264`/`libx265` karena lisensi GPL; saat ini video pendek maupun video panjang masih memakai `-c:v libx264`, sehingga penggantian ke build LGPL membutuhkan perubahan codec default ke `libopenh264` atau encoder hardware (`h264_mf`/`h264_nvenc`).

### Tipografi: Plus Jakarta Sans
- **Berkas:** `webui/static/fonts/PlusJakartaSans[wght].ttf`
- **Lisensi:** **SIL Open Font License 1.1 (OFL-1.1)** — teks lengkap tersedia pada [`webui/static/fonts/OFL.txt`](webui/static/fonts/OFL.txt).

### Sumber Footage Stok (Pixabay, Pexels, Coverr)
- Aset video/gambar yang diunduh oleh pengguna tunduk pada ketentuan lisensi dan syarat layanan masing-masing penyedia:
  - **Pixabay** — https://pixabay.com/service/license-summary/
  - **Pexels** — https://www.pexels.com/license/
  - **Coverr** — https://coverr.co/license
- Pengguna bertanggung jawab memeriksa atribusi, batasan komersial, serta kelayakan merek dagang aset sebelum menerbitkan video.

### Microsoft Edge TTS (`edge-tts`)
- Fitur suara gratis default memanfaatkan layanan Read Aloud Microsoft Edge secara **tidak resmi** melalui pustaka `edge-tts`.
- Layanan tidak memiliki SLA publik; ketersediaan, pembatasan laju (*rate limit*), dan perubahan protokol sepenuhnya berada di bawah kendali Microsoft.

### CapCut Desktop (Ekspor Draft)
- Fitur ekspor draft CapCut bersifat independen dan **tidak berafiliasi dengan atau didukung oleh ByteDance / CapCut**.
- Format berkas draft internal (`draft_content.json` / `draft_meta_info.json`) tidak terdokumentasi secara resmi dan dapat berubah pada versi CapCut berikutnya (teruji pada CapCut Desktop **8.9.0**).

### Penyedia AI Opsional (LLM, TTS, Gambar AI, Video AI)
- Koneksi ke OpenRouter, OpenAI, Anthropic, Google Gemini, DeepSeek, WaveSpeed, SiliconFlow, ElevenLabs, Azure Speech, dan penyedia lainnya menggunakan API key pengguna sendiri dan tunduk pada ketentuan layanan & tarif masing-masing penyedia.

---

## 3. Dependensi Python Terpasang (128 paket)

| Paket | Versi | Lisensi |
|---|---|---|
| `aiohappyeyeballs` | `2.6.1` | PSF-2.0 |
| `aiohttp` | `3.14.5` | Apache-2.0 AND MIT |
| `aiosignal` | `1.4.0` | Apache 2.0 |
| `altair` | `5.5.0` | BSD License |
| `annotated-doc` | `0.0.4` | MIT |
| `annotated-types` | `0.7.0` | MIT License |
| `anyio` | `4.14.2` | MIT |
| `attrs` | `26.1.0` | MIT |
| `av` | `17.0.0` | BSD-3-Clause |
| `azure-cognitiveservices-speech` | `1.41.1` | Other/Proprietary License |
| `boto3` | `1.43.121` | Apache-2.0 |
| `botocore` | `1.43.121` | Apache-2.0 |
| `certifi` | `2026.10.23` | MPL-2.0 |
| `cffi` | `2.1.0` | MIT-0 |
| `charset-normalizer` | `3.4.7` | MIT |
| `click` | `8.1.8` | BSD License |
| `colorama` | `0.4.6` | BSD License |
| `coverage` | `7.15.1` | Apache-2.0 |
| `cryptography` | `50.0.2` | Apache-2.0 OR BSD-3-Clause |
| `ctranslate2` | `4.7.1` | MIT |
| `dashscope` | `1.20.14` | Apache 2.0 |
| `decorator` | `5.2.1` | BSD-2-Clause |
| `distro` | `1.9.0` | Apache License, Version 2.0 |
| `edge-tts` | `7.3.1` | GNU Lesser General Public License v3 (LGPLv3) |
| `fastapi` | `0.136.3` | MIT |
| `faster-whisper` | `1.1.0` | MIT |
| `fastuuid` | `0.14.0` | BSD License |
| `filelock` | `4.2.4` | MIT |
| `flatbuffers` | `25.12.19` | Apache 2.0 |
| `freetype-py` | `2.5.1` | BSD License |
| `frozenlist` | `1.8.0` | Apache-2.0 |
| `fsspec` | `2026.9.0` | BSD-3-Clause |
| `google-auth` | `2.55.2` | Apache 2.0 |
| `google-genai` | `2.11.0` | Apache-2.0 |
| `h11` | `0.16.0` | MIT |
| `h2` | `4.4.1` | MIT |
| `hf-xet` | `1.4.3` | Apache-2.0 |
| `hpack` | `4.2.0` | MIT |
| `httpcore` | `1.0.9` | BSD-3-Clause |
| `httpx` | `0.28.1` | BSD-3-Clause |
| `huggingface_hub` | `1.8.0` | Apache-2.0 |
| `hyperframe` | `6.1.0` | MIT License |
| `idna` | `3.17` | BSD-3-Clause |
| `ImageIO` | `2.37.3` | BSD-2-Clause |
| `imageio-ffmpeg` | `0.6.0` | BSD-2-Clause |
| `importlib_metadata` | `8.5.0` | Apache Software License |
| `iniconfig` | `2.3.0` | MIT |
| `itsdangerous` | `2.2.0` | BSD License |
| `Jinja2` | `3.1.6` | BSD License |
| `jiter` | `0.13.0` | MIT License |
| `jmespath` | `1.1.0` | MIT |
| `jsonschema` | `4.23.0` | MIT |
| `jsonschema-specifications` | `2025.9.1` | MIT |
| `khatt` | `0.4.0` | MIT |
| `litellm` | `1.103.1` | MIT |
| `loguru` | `0.7.3` | MIT License |
| `markdown-it-py` | `4.0.0` | MIT License |
| `MarkupSafe` | `3.0.3` | BSD-3-Clause |
| `mdurl` | `0.1.2` | MIT License |
| `moviepy` | `2.2.1` | MIT License |
| `mpmath` | `1.3.0` | BSD |
| `mpt` | `1.3.10` | MIT |
| `multidict` | `6.7.1` | Apache License 2.0 |
| `narwhals` | `2.18.1` | MIT License |
| `numpy` | `2.4.4` | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `onnxruntime` | `1.24.4` | MIT License |
| `openai` | `2.24.0` | Apache-2.0 |
| `packaging` | `24.2` | Apache Software License; BSD License |
| `pandas` | `2.3.3` | BSD License |
| `pillow` | `13.0.0` | MIT-CMU |
| `pluggy` | `1.6.0` | MIT |
| `proglog` | `0.1.12` | MIT |
| `propcache` | `0.4.1` | Apache-2.0 |
| `protobuf` | `5.29.6` | 3-Clause BSD License |
| `pyarrow` | `23.0.1` | Apache-2.0 |
| `pyasn1` | `0.6.4` | BSD-2-Clause |
| `pyasn1_modules` | `0.4.2` | BSD |
| `pycparser` | `3.0` | BSD-3-Clause |
| `pydantic` | `2.12.5` | MIT |
| `pydantic-settings` | `2.16.2` | MIT |
| `pydantic_core` | `2.41.5` | MIT |
| `pydeck` | `0.9.1` | Apache License 2.0 |
| `pydub` | `0.25.1` | MIT |
| `Pygments` | `2.20.0` | BSD-2-Clause |
| `PyJWT` | `2.15.1` | MIT |
| `pytest` | `9.1.1` | MIT |
| `python-bidi` | `0.6.12` | GNU Library or Lesser General Public License (LGPL) |
| `python-dateutil` | `2.9.0.post0` | Dual License |
| `python-dotenv` | `1.2.2` | BSD-3-Clause |
| `python-multipart` | `0.0.30` | Apache-2.0 |
| `pytz` | `2026.1.post1` | MIT |
| `PyYAML` | `6.0.3` | MIT |
| `redis` | `5.2.0` | MIT |
| `referencing` | `0.37.0` | MIT |
| `regex` | `2026.4.4` | Apache-2.0 AND CNRI-Python |
| `requests` | `2.33.1` | Apache-2.0 |
| `rich` | `14.3.3` | MIT |
| `rpds-py` | `0.30.0` | MIT |
| `ruff` | `0.15.21` | MIT |
| `s3transfer` | `0.19.3` | Apache License 2.0 |
| `setuptools` | `82.0.1` | MIT |
| `shellingham` | `1.5.4` | ISC License |
| `six` | `1.17.0` | MIT |
| `sniffio` | `1.3.1` | MIT OR Apache-2.0 |
| `socksio` | `1.0.0` | MIT License |
| `starlette` | `1.3.1` | BSD-3-Clause |
| `streamlit` | `1.66.1` | Apache-2.0 |
| `streamlit-tour` | `1.1.0` | BSD-3-Clause |
| `sympy` | `1.14.0` | BSD |
| `tabulate` | `0.10.0` | MIT |
| `tenacity` | `9.1.4` | Apache 2.0 |
| `tiktoken` | `0.12.0` | MIT License |
| `tokenizers` | `0.22.2` | Apache Software License |
| `toml` | `0.10.2` | MIT |
| `tqdm` | `4.67.3` | MPL-2.0 AND MIT |
| `typer` | `0.26.5` | MIT |
| `typing-inspection` | `0.4.2` | MIT |
| `typing_extensions` | `4.15.0` | PSF-2.0 |
| `tzdata` | `2025.3` | Apache-2.0 |
| `uharfbuzz` | `0.56.3` | Apache License 2.0 |
| `urllib3` | `2.8.0` | MIT |
| `uvicorn` | `0.32.1` | BSD-3-Clause |
| `watchdog` | `6.0.0` | Apache-2.0 |
| `websocket-client` | `1.9.0` | Apache-2.0 |
| `websockets` | `16.0` | BSD-3-Clause |
| `win32_setctime` | `1.2.0` | MIT license |
| `yarl` | `1.25.1` | Apache-2.0 |
| `zipp` | `3.23.1` | MIT |
