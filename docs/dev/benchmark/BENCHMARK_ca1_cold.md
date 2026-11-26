# Hasil Benchmark Produksi Video ~10 Menit (Stage 8C.3, E4)

- **Tanggal uji:** 2026-11-26 16:56:38
- **Versi aplikasi:** EasyFaceless Studio v1.0.1
- **Proyek uji:** `bench_panama_10m` (6 bab, 77 shot, durasi video final **450.5 detik / 7.51 menit**)

---

## 1. Spesifikasi PC Uji

| Komponen | Nilai Terukur |
|---|---|
| **OS** | Windows 10 (AMD64) |
| **CPU** | 12th Gen Intel(R) Core(TM) i3-12100 (4 core / 8 thread) |
| **RAM Fisik** | 7.75 GiB (~8 GB nominal) |
| **Disk Kosong (drive data)** | 9.03 GiB |
| **FFmpeg** | ffmpeg version 2025-11-17-git-e94439e49b-full_build-www.gyan.dev Copyright (c) 2000-2025 the FFmpeg developers |
| **Python** | 3.11.9 |
| **CapCut Desktop** | v8.9.0 (tested) |

---

## 2. Durasi, RAM Puncak & CPU Rata-Rata per Fase

| Fase | Durasi | RAM Puncak (Proses + FFmpeg) | CPU Rata-Rata Sistem | Catatan |
|---|---|---|---|---|
| **1. Naskah (Script)** | 0.0 dtk (0.00 mnt) | 97.0 MB | 32.0% | Impor naskah tolok ukur (1096 kata, 4 bab) |
| **2. Suara (Voice)** | 10.3 dtk (0.17 mnt) | 102.5 MB | 7.7% | Edge TTS (450.5 dtk audio) |
| **3. Visual (Fetch + Build)** | 507.5 dtk (8.46 mnt) | 1415.2 MB | 42.7% | 77/77 klip shot siap |
| **4. Render Final** | 125.6 dtk (2.09 mnt) | 813.4 MB | 74.9% | 1080p MP4 + BGM + loudnorm (6 bab) |
| **5. Ekspor CapCut** | 3.1 dtk (0.05 mnt) | 168.8 MB | 15.8% | Draft 77 klip + 138 subtitle |

- **Total waktu eksekusi 5 fase:** **646.6 detik (10.78 menit)**
- **Puncak RAM proses + FFmpeg tertinggi:** **1415.2 MB (1.38 GiB)**

---

## 3. Penggunaan Ruang Disk Proyek

| Folder / Berkas | Ukuran |
|---|---|
| `assets/` (audio WAV + klip footage + bab sementara) | 756.5 MB |
| `export/` (`final_video.mp4`, subtitle, `credits.md`, draft CapCut) | 627.3 MB |
| `render/` / `output/` / berkas proyek lain | 365.4 MB |
| **Total folder proyek** | **1749.2 MB (1.71 GiB)** |
| **Cache visual global terpakai** | **2281.5 MB** |

---

## 4. Kalibrasi Ambang Rekomendasi Sistem (E4)

Berdasarkan pengukuran nyata di atas (proyek ±10 menit, puncak RAM ~1415 MB, ukuran folder proyek ~1749 MB + cache & file sementara):

- **RAM:** Minimum **8 GB** (`>= 7.5 GiB` terukur) · Disarankan **16 GB** bila menyunting bersama CapCut Desktop.
- **CPU:** Minimum **4 core / 4 thread** · Disarankan **6–8+ thread** (uji 4 core / 8 thread berjalan lancar).
- **Ruang Disk Kosong (Data Dir):** Minimum **15 GB** (1 proyek memakai ~2,3 GB + cache ~0,9 GB + file sementara) · Disarankan **20 GB+ SSD** untuk beberapa proyek + cache stok.

> Ulangi benchmark kapan saja lewat `uv run python scripts/dev/benchmark.py`.
