# Hasil Benchmark Produksi Video ~10 Menit (Stage 8C.3, E4)

- **Tanggal uji:** 2026-11-19 14:22:37
- **Versi aplikasi:** EasyFaceless Studio v1.0.0
- **Proyek uji:** `bench_panama_10m` (4 bab, 96 shot, durasi video final **538.2 detik / 8.97 menit**)

---

## 1. Spesifikasi PC Uji

| Komponen | Nilai Terukur |
|---|---|
| **OS** | Windows 10 (AMD64) |
| **CPU** | 12th Gen Intel(R) Core(TM) i3-12100 (4 core / 8 thread) |
| **RAM Fisik** | 7.75 GiB (~8 GB nominal) |
| **Disk Kosong (drive data)** | 12.88 GiB |
| **FFmpeg** | ffmpeg version 2025-11-17-git-e94439e49b-full_build-www.gyan.dev Copyright (c) 2000-2025 the FFmpeg developers |
| **Python** | 3.11.9 |
| **CapCut Desktop** | v8.9.0 (tested) |

---

## 2. Durasi, RAM Puncak & CPU Rata-Rata per Fase

| Fase | Durasi | RAM Puncak (Proses + FFmpeg) | CPU Rata-Rata Sistem | Catatan |
|---|---|---|---|---|
| **1. Naskah (Script)** | 109.5 dtk (1.82 mnt) | 0.0 MB | 4.8% | AI LLM (openrouter: 1314 kata) |
| **2. Suara (Voice)** | 53.0 dtk (0.88 mnt) | 0.0 MB | 3.0% | Edge TTS (538.2 dtk audio) |
| **3. Visual (Fetch + Build)** | 1507.6 dtk (25.13 mnt) | 643.6 MB | 22.7% | 96/96 klip shot siap |
| **4. Render Final** | 153.0 dtk (2.55 mnt) | 683.4 MB | 76.3% | 1080p MP4 + BGM + loudnorm (4 bab) |
| **5. Ekspor CapCut** | 6.3 dtk (0.11 mnt) | 19.7 MB | 13.9% | Draft 96 klip + 169 subtitle |

- **Total waktu eksekusi 5 fase:** **1829.4 detik (30.49 menit)**
- **Puncak RAM proses + FFmpeg tertinggi:** **683.4 MB (0.67 GiB)**

---

## 3. Penggunaan Ruang Disk Proyek

| Folder / Berkas | Ukuran |
|---|---|
| `assets/` (audio WAV + klip footage) | 995.2 MB |
| `export/` (`final_video.mp4`, subtitle, `credits.md`, draft CapCut) | 859.8 MB |
| `render/` & berkas proyek sementara lain | 465.4 MB |
| **Total folder proyek** | **2320.4 MB (2.27 GiB)** |
| **Cache visual global terpakai** | **872.8 MB** |

---

## 4. Kalibrasi Ambang Rekomendasi Sistem (E4)

Berdasarkan pengukuran nyata di atas (proyek ±10 menit, puncak RAM ~683 MB, ukuran folder proyek ~2320 MB + cache & file sementara):

- **RAM:** Minimum **8 GB** (`>= 7.5 GiB` terukur) · Disarankan **16 GB** bila menyunting bersama CapCut Desktop.
- **CPU:** Minimum **4 core / 4 thread** · Disarankan **6–8+ thread** (uji 4 core / 8 thread berjalan lancar).
- **Ruang Disk Kosong (Data Dir):** Minimum **5 GB** (untuk 1–2 proyek & cache) · Disarankan **20 GB** untuk beberapa proyek + cache stok.

> Ulangi benchmark kapan saja lewat `uv run python scripts/dev/benchmark.py`.
