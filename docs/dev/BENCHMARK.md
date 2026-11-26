# Hasil Benchmark Produksi Video ~10 Menit & Optimasi Visual V102-C

- **Tanggal uji:** 2026-11-26
- **Versi aplikasi:** EasyFaceless Studio v1.0.1 (`stage/v102`)
- **Proyek uji:** `bench_panama_10m` / `bench_panama_10m_warm` (6 bab, **77 shot**, durasi video final **450.5 detik / 7.51 menit**, `--no-llm` deterministik)
- **Berkas sumber angka:**
  - Sebelum (Dingin): [`BENCHMARK_baseline_cold.md`](./benchmark/BENCHMARK_baseline_cold.md) · [`visual_metrics_baseline_cold.json`](./benchmark/visual_metrics_baseline_cold.json)
  - Sebelum (Hangat): [`baseline_warm_summary.json`](./benchmark/baseline_warm_summary.json) · [`visual_metrics_baseline_warm.json`](./benchmark/visual_metrics_baseline_warm.json)
  - Sesudah (Dingin): [`BENCHMARK_after_cold.md`](./benchmark/BENCHMARK_after_cold.md) · [`visual_metrics_after_cold.json`](./benchmark/visual_metrics_after_cold.json)
  - Sesudah (Hangat): [`after_warm_summary.json`](./benchmark/after_warm_summary.json) · [`visual_metrics_after_warm.json`](./benchmark/visual_metrics_after_warm.json)

---

## 1. Spesifikasi PC Uji

| Komponen | Nilai Terukur |
|---|---|
| **OS** | Windows 10 (AMD64) |
| **CPU** | 12th Gen Intel(R) Core(TM) i3-12100 (4 core / 8 thread) |
| **RAM Fisik** | 7.75 GiB (~8 GB nominal) |
| **Disk Kosong (drive data)** | 7.79–9.39 GiB |
| **FFmpeg** | ffmpeg version 2025-11-17-git-e94439e49b-full_build-www.gyan.dev |
| **Python** | 3.11.9 |
| **CapCut Desktop** | v8.9.0 (tested) |

---

## 2. Perbandingan Sebelum vs Sesudah V102-C (Cache Dingin & Cache Hangat)

### A. Ringkasan 5 Fase End-to-End

| Fase / Metrik | Dingin Sebelum | Dingin Sesudah | Hangat Sebelum | Hangat Sesudah |
|---|---:|---:|---:|---:|
| **1. Naskah (Script)** | 0.0 dtk (96.7 MB, 53.1% CPU) | 0.0 dtk (96.8 MB, 28.1% CPU) | 0.09 dtk (98.5 MB, 33.8% CPU) | 0.05 dtk (98.3 MB, 31.2% CPU) |
| **2. Suara (Voice)** | 46.2 dtk (101.7 MB, 9.5% CPU) | 11.9 dtk (102.1 MB, 15.9% CPU) | 11.01 dtk (104.3 MB, 19.5% CPU) | 10.8 dtk (115.2 MB, 13.6% CPU) |
| **3. Visual (Wall Phase)** | **1195.3 dtk** (1266.0 MB, 33.9% CPU) | **752.1 dtk** (1750.9 MB, 38.5% CPU) | **573.27 dtk** (1376.4 MB, 44.7% CPU) | **553.9 dtk** (1582.5 MB, 45.4% CPU) |
| **4. Render Final** | 150.9 dtk (798.1 MB, 76.5% CPU) | 136.7 dtk (818.7 MB, 78.3% CPU) | 137.86 dtk (800.5 MB, 79.5% CPU) | 145.0 dtk (802.8 MB, 79.7% CPU) |
| **5. Ekspor CapCut** | 7.3 dtk (148.0 MB, 17.2% CPU) | 4.2 dtk (250.7 MB, 17.7% CPU) | 3.4 dtk (151.2 MB, 25.6% CPU) | 3.08 dtk (151.6 MB, 21.3% CPU) |
| **Total Waktu 5 Fase** | **1399.7 dtk** (23.33 mnt) | **904.9 dtk** (15.08 mnt) | **725.6 dtk** (12.09 mnt) | **712.8 dtk** (11.88 mnt) |
| **Puncak RAM Tertinggi** | **1266.0 MB** (1.24 GiB) | **1750.9 MB** (1.71 GiB) | **1376.4 MB** (1.34 GiB) | **1582.5 MB** (1.55 GiB) |

### B. Rincian Sub-Fase Visual (`visual_metrics.json`)

| Sub-fase Visual | Dingin Sebelum | Dingin Sesudah | Delta Dingin | Hangat Sebelum | Hangat Sesudah | Delta Hangat |
|---|---:|---:|---:|---:|---:|---:|
| **`total_seconds` (Visual)** | **1183.891 dtk** | **747.109 dtk** | **−436.782 dtk (−36.9%)** | **566.735 dtk** | **550.859 dtk** | **−15.876 dtk (−2.8%)** |
| `search_wall_seconds` | 604.497 dtk | 382.750 dtk | −221.747 dtk | 278.103 dtk | 288.093 dtk | +9.990 dtk *(Openverse live 116.8s vs 40.9s)* |
| `asset_download_seconds` (**C3**) | **260.422 dtk** | **119.875 dtk** | **−140.547 dtk (2.17×)** | 17.656 dtk | 21.516 dtk | +3.860 dtk *(75 vs 76 cache hit)* |
| `build_seconds` (**C4/C5**) | **312.453 dtk** | **232.640 dtk** | **−79.813 dtk (−25.5%)** | **264.063 dtk** | **239.047 dtk** | **−25.016 dtk (−9.5%)** |
| `ffmpeg_build_seconds` *(sum worker)* | 234.730 dtk | 387.020 dtk | *2 worker paralel (`-threads 4`)* | 202.400 dtk | 398.180 dtk | *2 worker paralel (`-threads 4`)* |
| `thumbnail_seconds` | 0.000 dtk | 0.000 dtk | 0.000 dtk (lazy UI) | 0.000 dtk | 0.000 dtk | 0.000 dtk (lazy UI) |
| `ai_relevance_seconds` | 0.000 dtk | 0.000 dtk | 0.000 dtk (`off`) | 0.000 dtk | 0.000 dtk | 0.000 dtk (`off`) |
| `manifest_save_seconds` | 6.449 dtk (155×) | 7.109 dtk (155×) | +0.660 dtk | 5.662 dtk (155×) | 11.729 dtk (155×) | +6.067 dtk |

> **Ringkasan dampak:** Unduhan aset dingin dengan `MAX_PARALLEL_DOWNLOADS = 3` menjadi **2.17× lebih cepat** (`260.42s → 119.88s`), dan build klip paralel dengan `MAX_PARALLEL_BUILDS = max(1, min(3, cpu_count // 4))` (`2` worker × `-threads 4` pada i3-12100 8-thread) memangkas waktu wall build dari **312.45s → 232.64s** (dingin) dan **264.06s → 239.05s** (hangat) dengan kenaikan RAM puncak yang tetap jauh di bawah batas 8 GB (`1.71 GiB`).

---

## 3. Perbandingan `source_distribution` dan `candidate_id` (C6)

| Run | Pixabay | Wikimedia | NASA | Openverse | Coverr | Total Shot | Sumber |
|---|---:|---:|---:|---:|---:|---:|---|
| **Baseline Dingin** | 37 (48.1%) | 18 (23.4%) | 18 (23.4%) | 4 (5.2%) | 0 | 77 | [`visual_metrics_baseline_cold.json`](./benchmark/visual_metrics_baseline_cold.json) |
| **Baseline Hangat** | 36 (46.8%) | 19 (24.7%) | 18 (23.4%) | 4 (5.2%) | 0 | 77 | [`visual_metrics_baseline_warm.json`](./benchmark/visual_metrics_baseline_warm.json) |
| **Sesudah Dingin** | 36 (46.8%) | 19 (24.7%) | 17 (22.1%) | 5 (6.5%) | 0 | 77 | [`visual_metrics_after_cold.json`](./benchmark/visual_metrics_after_cold.json) |
| **Sesudah Hangat** | 36 (46.8%) | 18 (23.4%) | 17 (22.1%) | 6 (7.8%) | 0 | 77 | [`visual_metrics_after_warm.json`](./benchmark/visual_metrics_after_warm.json) |

**Penjelasan perbedaan `candidate_id`:**
- Dari **77 shot**, **66/77 shot (85.7%)** memilih `(provider, candidate_id)` **identik** antara *Baseline Dingin* (`baseline_cold_selections.json`) dan *Sesudah Dingin* (`after_cold_selections.json`), dan **76/77 (98.7%)** identik antara *Sesudah Dingin* dan *Sesudah Hangat* (`after_warm_selections.json`).
- Sebelas perbedaan pada uji langsung berasal murni dari kondisi server eksternal saat pengujian (bukan perubahan logika pemilihan/relevansi):
  1. Pada *Baseline Dingin*, Wikimedia mengalami **4 timeout** (`timeout=4`) sedangkan pada *Sesudah Dingin* Wikimedia mencatat **0 timeout**, sehingga `ch01_shot009` dan `ch04_shot007` memperoleh kandidat Wikimedia (`wikimedia_14346505` dan `wikimedia_2932139`) tepat pada pencarian paralel sumber, dan dedup berurutan `used_candidate_ids` menggeser 8 ID Pixabay berikutnya satu posisi (`pixabay_103581 → 109651 → 112471 → 251487`, dan `269098 → 269099 → 71264 → 71267`).
  2. Pada `ch05_shot009` (*Sesudah Dingin*) dan `ch02_shot008` (*Sesudah Hangat*), Openverse lebih sedikit mengalami timeout (8 timeout di baseline dingin → 6 di sesudah dingin → 0 timeout di sesudah hangat), sehingga Openverse lolos dan terpilih (`openverse_09d1220d...` dan `openverse_dfb6beab...`).
- Urutan pencarian & aturan pemilihan (`priority` / `mix_best` 60% / 3-berturut) tetap sekuensial 100% deterministik.

---

## 4. Kalibrasi Ambang Rekomendasi Sistem & Ruang Disk (E4)

- **Ukuran folder proyek (`BENCHMARK_after_cold.md`):** `assets/` **766.7 MB** · `export/` **639.9 MB** · `render/` + lain **363.2 MB** · Total proyek **1769.9 MB (1.73 GiB)** · Cache visual global **2365.2 MB**.
- **RAM:** Minimum **8 GB** (`>= 7.5 GiB` terukur; puncak dengan build paralel 2 worker terukur **1.71 GiB**) · Disarankan **16 GB** bila menyunting bersama CapCut Desktop.
- **CPU:** Minimum **4 core / 4 thread** · Disarankan **6–8+ thread** (rumus paralel `MAX_PARALLEL_BUILDS = max(1, min(3, cpu_count // 4))`, membagi `-threads` per FFmpeg).
- **Ruang Disk Kosong (Data Dir):** Minimum **15 GB** · Disarankan **20 GB+ SSD**.

> Ulangi benchmark kapan saja lewat `uv run python scripts/dev/benchmark.py`.
