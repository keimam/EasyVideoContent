# Hasil Benchmark Produksi Video ~10 Menit (Stage 8C.3, E4)

- **Tanggal uji:** 2026-11-26 15:03:21
- **Versi aplikasi:** EasyFaceless Studio v1.0.1
- **Proyek uji:** `bench_panama_10m` (6 bab, 77 shot, durasi video final **450.5 detik / 7.51 menit**)

---

## 1. Spesifikasi PC Uji

| Komponen | Nilai Terukur |
|---|---|
| **OS** | Windows 10 (AMD64) |
| **CPU** | 12th Gen Intel(R) Core(TM) i3-12100 (4 core / 8 thread) |
| **RAM Fisik** | 7.75 GiB (~8 GB nominal) |
| **Disk Kosong (drive data)** | 7.79 GiB |
| **FFmpeg** | ffmpeg version 2025-11-17-git-e94439e49b-full_build-www.gyan.dev Copyright (c) 2000-2025 the FFmpeg developers |
| **Python** | 3.11.9 |
| **CapCut Desktop** | v8.9.0 (tested) |

---

## 2. Durasi, RAM Puncak & CPU Rata-Rata per Fase

| Fase | Durasi | RAM Puncak (Proses + FFmpeg) | CPU Rata-Rata Sistem | Catatan |
|---|---|---|---|---|
| **1. Naskah (Script)** | 0.0 dtk (0.00 mnt) | 96.7 MB | 53.1% | Impor naskah tolok ukur (1096 kata, 4 bab) |
| **2. Suara (Voice)** | 46.2 dtk (0.77 mnt) | 101.7 MB | 9.5% | Edge TTS (450.5 dtk audio) |
| **3. Visual (Fetch + Build)** | 1195.3 dtk (19.92 mnt) | 1266.0 MB | 33.9% | 77/77 klip shot siap |
| **4. Render Final** | 150.9 dtk (2.52 mnt) | 798.1 MB | 76.5% | 1080p MP4 + BGM + loudnorm (6 bab) |
| **5. Ekspor CapCut** | 7.3 dtk (0.12 mnt) | 148.0 MB | 17.2% | Draft 77 klip + 138 subtitle |

- **Total waktu eksekusi 5 fase:** **1399.7 detik (23.33 menit)**
- **Puncak RAM proses + FFmpeg tertinggi:** **1266.0 MB (1.24 GiB)**

---

## 3. Penggunaan Ruang Disk Proyek

| Folder / Berkas | Ukuran |
|---|---|
| `assets/` (audio WAV + klip footage + bab sementara) | 776.7 MB |
| `export/` (`final_video.mp4`, subtitle, `credits.md`, draft CapCut) | 644.8 MB |
| `render/` / `output/` / berkas proyek lain | 364.6 MB |
| **Total folder proyek** | **1786.0 MB (1.74 GiB)** |
| **Cache visual global terpakai** | **2378.1 MB** |

---

## 4. Kalibrasi Ambang Rekomendasi Sistem (E4)

Berdasarkan pengukuran nyata di atas (proyek ±10 menit, puncak RAM ~1266 MB, ukuran folder proyek ~1786 MB + cache & file sementara):

- **RAM:** Minimum **8 GB** (`>= 7.5 GiB` terukur) · Disarankan **16 GB** bila menyunting bersama CapCut Desktop.
- **CPU:** Minimum **4 core / 4 thread** · Disarankan **6–8+ thread** (uji 4 core / 8 thread berjalan lancar).
- **Ruang Disk Kosong (Data Dir):** Minimum **15 GB** (1 proyek memakai ~2,3 GB + cache ~0,9 GB + file sementara) · Disarankan **20 GB+ SSD** untuk beberapa proyek + cache stok.

> Ulangi benchmark kapan saja lewat `uv run python scripts/dev/benchmark.py`.
