# Hasil Benchmark Produksi Video ~10 Menit & Optimasi Visual (V102-C & V102-C-A1)

- **Tanggal uji:** 2026-11-26
- **Versi aplikasi:** EasyFaceless Studio v1.0.1 (`stage/v102`)
- **Proyek uji:** `bench_panama_10m` (dingin) / `bench_panama_10m_warm` (hangat) — 6 bab, **77 shot**, durasi video final **450.5 detik / 7.51 menit**, `--no-llm` deterministik.
- **Berkas sumber angka:**
  - **Sebelum C:** [`BENCHMARK_baseline_cold.md`](./benchmark/BENCHMARK_baseline_cold.md) · [`visual_metrics_baseline_cold.json`](./benchmark/visual_metrics_baseline_cold.json) · [`baseline_warm_summary.json`](./benchmark/baseline_warm_summary.json) · [`visual_metrics_baseline_warm.json`](./benchmark/visual_metrics_baseline_warm.json)
  - **Sesudah C:** [`BENCHMARK_after_cold.md`](./benchmark/BENCHMARK_after_cold.md) · [`visual_metrics_after_cold.json`](./benchmark/visual_metrics_after_cold.json) · [`after_warm_summary.json`](./benchmark/after_warm_summary.json) · [`visual_metrics_after_warm.json`](./benchmark/visual_metrics_after_warm.json)
  - **Sesudah CA1:** [`BENCHMARK_ca1_cold.md`](./benchmark/BENCHMARK_ca1_cold.md) · [`ca1_cold_summary.json`](./benchmark/ca1_cold_summary.json) · [`visual_metrics_ca1_cold.json`](./benchmark/visual_metrics_ca1_cold.json) · [`ca1_warm_summary.json`](./benchmark/ca1_warm_summary.json) · [`visual_metrics_ca1_warm.json`](./benchmark/visual_metrics_ca1_warm.json)

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

## 2. Perbandingan Sebelum C vs Sesudah C vs Sesudah CA1

### A. Ringkasan 5 Fase End-to-End — Cache Dingin (Cold)

| Fase / Metrik | Sebelum C | Sesudah C | Sesudah CA1 | Delta CA1 vs Sebelum C | Delta CA1 vs Sesudah C |
|---|---:|---:|---:|---:|---:|
| **1. Naskah (Script)** | 0.0 dtk (96.7 MB, 53.1%) | 0.0 dtk (96.8 MB, 28.1%) | 0.03 dtk (97.0 MB, 32.0%) | ~0.0 dtk | ~0.0 dtk |
| **2. Suara (Voice)** | 46.2 dtk (101.7 MB, 9.5%) | 11.9 dtk (102.1 MB, 15.9%) | 10.3 dtk (102.5 MB, 7.7%) | −35.9 dtk | −1.6 dtk |
| **3. Visual (Wall Phase)** | **1195.3 dtk** (1266.0 MB, 33.9%) | **752.1 dtk** (1750.9 MB, 38.5%) | **507.5 dtk** (**1415.2 MB**, 42.7%) | **−687.8 dtk (−57.5%)** | **−244.6 dtk (−32.5%)** |
| **4. Render Final** | 150.9 dtk (798.1 MB, 76.5%) | 136.7 dtk (818.7 MB, 78.3%) | 125.6 dtk (813.4 MB, 74.9%) | −25.3 dtk | −11.1 dtk |
| **5. Ekspor CapCut** | 7.3 dtk (148.0 MB, 17.2%) | 4.2 dtk (250.7 MB, 17.7%) | 3.1 dtk (168.8 MB, 15.8%) | −4.2 dtk | −1.1 dtk |
| **Total Waktu 5 Fase** | **1399.7 dtk** (23.33 mnt) | **904.9 dtk** (15.08 mnt) | **646.6 dtk** (**10.78 mnt**) | **−753.1 dtk (−53.8%)** | **−258.3 dtk (−28.5%)** |
| **Puncak RAM Tertinggi** | **1266.0 MB** (1.24 GiB) | **1750.9 MB** (1.71 GiB) | **1415.2 MB** (**1.38 GiB**) | **+149.2 MB** *(target ≤ +300 MB)* | **−335.7 MB (−19.2%)** |

### B. Ringkasan 5 Fase End-to-End — Cache Hangat (Warm)

| Fase / Metrik | Sebelum C | Sesudah C | Sesudah CA1 | Delta CA1 vs Sebelum C | Delta CA1 vs Sesudah C |
|---|---:|---:|---:|---:|---:|
| **1. Naskah (Script)** | 0.09 dtk (98.5 MB, 33.8%) | 0.05 dtk (98.3 MB, 31.2%) | 0.05 dtk (95.7 MB, 29.2%) | −0.04 dtk | 0.0 dtk |
| **2. Suara (Voice)** | 11.01 dtk (104.3 MB, 19.5%) | 10.8 dtk (115.2 MB, 13.6%) | 11.08 dtk (112.9 MB, 14.4%) | +0.07 dtk | +0.28 dtk |
| **3. Visual (Wall Phase)** | **573.27 dtk** (1376.4 MB, 44.7%) | **553.9 dtk** (1582.5 MB, 45.4%) | **369.71 dtk** (**1376.1 MB**, 51.6%) | **−203.56 dtk (−35.5%)** | **−184.19 dtk (−33.3%)** |
| **4. Render Final** | 137.86 dtk (800.5 MB, 79.5%) | 145.0 dtk (802.8 MB, 79.7%) | 126.22 dtk (801.2 MB, 74.8%) | −11.64 dtk | −18.78 dtk |
| **5. Ekspor CapCut** | 3.4 dtk (151.2 MB, 25.6%) | 3.08 dtk (151.6 MB, 21.3%) | 2.80 dtk (155.0 MB, 17.9%) | −0.60 dtk | −0.28 dtk |
| **Total Waktu 5 Fase** | **725.6 dtk** (12.09 mnt) | **712.8 dtk** (11.88 mnt) | **509.9 dtk** (**8.50 mnt**) | **−215.7 dtk (−29.7%)** | **−202.9 dtk (−28.5%)** |
| **Puncak RAM Tertinggi** | **1376.4 MB** (1.34 GiB) | **1582.5 MB** (1.55 GiB) | **1376.1 MB** (**1.34 GiB**) | **−0.3 MB** | **−206.4 MB (−13.0%)** |

### C. Rincian Sub-Fase Visual (`visual_metrics.json`)

| Sub-fase Visual | Dingin Sebelum C | Dingin Sesudah C | Dingin Sesudah CA1 | Hangat Sebelum C | Hangat Sesudah C | Hangat Sesudah CA1 |
|---|---:|---:|---:|---:|---:|---:|
| **`total_seconds` (Visual)** | **1183.891 dtk** | **747.109 dtk** | **504.422 dtk** | **566.735 dtk** | **550.859 dtk** | **366.797 dtk** |
| `search_wall_seconds` (**CA1.1**) | 604.497 dtk | 382.750 dtk | **180.720 dtk** | 278.103 dtk | 288.093 dtk | **140.687 dtk** |
| `asset_download_seconds` (**C3**) | 260.422 dtk | 119.875 dtk | **110.375 dtk** | 17.656 dtk | 21.516 dtk | **21.062 dtk** |
| `build_seconds` (**C4/C5/CA1.2**) | 312.453 dtk | 232.640 dtk | **201.125 dtk** | 264.063 dtk | 239.047 dtk | **203.765 dtk** |
| `ffmpeg_build_seconds` *(sum worker)* | 234.730 dtk | 387.020 dtk | 333.410 dtk | 202.400 dtk | 398.180 dtk | 336.580 dtk |
| `thumbnail_seconds` | 0.000 dtk | 0.000 dtk | 0.000 dtk | 0.000 dtk | 0.000 dtk | 0.000 dtk |
| `ai_relevance_seconds` | 0.000 dtk | 0.000 dtk | 0.000 dtk | 0.000 dtk | 0.000 dtk | 0.000 dtk |
| `manifest_save_seconds` (**CA1.2**) | 6.449 dtk (155×) | 7.109 dtk (155×) | **1.707 dtk (46×)** | 5.662 dtk (155×) | 11.729 dtk (155×) | **3.328 dtk (46×)** |

> **Profil Peak RAM CA1.2 (Python vs Child FFmpeg):**
> - **CA1 Dingin (Visual):** Total puncak `1415.2 MB` (**Python RSS `152.8 MB`**, **FFmpeg child `1281.7 MB`**) — turun `−335.7 MB` dari Sesudah C (`1750.9 MB`) dan memenuhi target `≤ baseline + 300 MB` (`≤ 1566.0 MB`) setelah pembatasan `-x264-params rc-lookahead=12`, pelepasan `Future`/`pending_downloads` secara langsung di loop `as_completed`, dan `gc.collect()` antar-fase.
> - **CA1 Hangat (Visual):** Total puncak `1376.1 MB` (**Python RSS `139.2 MB`**, **FFmpeg child `1256.8 MB`**) — setara dengan Baseline Hangat (`1376.4 MB`) meskipun berjalan dengan prefetch + download paralel + build paralel 2 worker.

---

## 3. Jumlah Request & Timeout per Sumber (`search_per_source` + `source_status`)

| Sumber | Metrik | Dingin Sebelum C | Dingin Sesudah C | Dingin Sesudah CA1 | Hangat Sesudah CA1 |
|---|---|---:|---:|---:|---:|
| **Pixabay** | `tier / http / hit` | 395 / 400 / 0 | 393 / 398 / 0 | **382** / 421 / 0 | 404 / 521 / 0 |
| | `timeout / status` | 0 / `ok` | 0 / `ok` | **0 / `ok`** | **0 / `ok`** |
| **Wikimedia** | `tier / http / hit` | 329 / 248 / 81 | 321 / 247 / 74 | 326 / 250 / 76 | 331 / **1** / **330** |
| | `timeout / status` | **4** / `timeout` | 0 / `ok` | **0 / `ok`** | **0 / `ok`** |
| **NASA** | `tier / http / hit` | 339 / 249 / 90 | 331 / 249 / 82 | 337 / 247 / 90 | 343 / **2** / **341** |
| | `timeout / status` | 0 / `ok` | 0 / `ok` | **0 / `ok`** | **0 / `ok`** |
| **Openverse** | `tier / http / hit` | 194 / 60 / 26 | 220 / 106 / 48 | 267 / 192 / 75 | 273 / **3** / **270** |
| | `timeout / status` | **8** / `cooldown=5` | **6** / `cooldown=1` | **0 / `ok`** | **0 / `ok`** |
| **Coverr** | `tier / http / hit` | 140 / 53 / 0 | 78 / 1 / 0 | 77 / **3** / 0 | 53 / **53** / 0 |
| | `timeout / status` | 0 / `rate_limited` | 0 / `auth_blocked` | 0 / `auth_blocked` *(HTTP 403)* | 0 / `rate_limited` *(cd=3)* |

- **CA1 dingin mencatat 0 timeout di seluruh sumber** (sebelumnya Openverse `8×` & Wikimedia `4×` di Sebelum C, dan Openverse `6×` di Sesudah C).
- Karena `Coverr` langsung memicu circuit breaker (`auth_blocked` setelah 3 HTTP di dingin; `rate_limited` + cooldown setelah 53 HTTP di hangat), seluruh shot tersisa mengirim **0** HTTP request ke Coverr.
- Pada cache hangat CA1, tingkat hit cache pencarian mencapai **99.7% Wikimedia (`330/331`)**, **99.4% NASA (`341/343`)**, dan **98.9% Openverse (`270/273`)**.

---

## 4. Perbandingan `source_distribution` dan `candidate_id`

| Run | Pixabay | Wikimedia | NASA | Openverse | Coverr | Total Shot | Sumber |
|---|---:|---:|---:|---:|---:|---:|---|
| **Sebelum C (Dingin)** | 37 (48.1%) | 18 (23.4%) | 18 (23.4%) | 4 (5.2%) | 0 | 77 | [`visual_metrics_baseline_cold.json`](./benchmark/visual_metrics_baseline_cold.json) |
| **Sebelum C (Hangat)** | 36 (46.8%) | 19 (24.7%) | 18 (23.4%) | 4 (5.2%) | 0 | 77 | [`visual_metrics_baseline_warm.json`](./benchmark/visual_metrics_baseline_warm.json) |
| **Sesudah C (Dingin)** | 36 (46.8%) | 19 (24.7%) | 17 (22.1%) | 5 (6.5%) | 0 | 77 | [`visual_metrics_after_cold.json`](./benchmark/visual_metrics_after_cold.json) |
| **Sesudah C (Hangat)** | 36 (46.8%) | 18 (23.4%) | 17 (22.1%) | 6 (7.8%) | 0 | 77 | [`visual_metrics_after_warm.json`](./benchmark/visual_metrics_after_warm.json) |
| **Sesudah CA1 (Dingin)** | 37 (48.1%) | 16 (20.8%) | 16 (20.8%) | 8 (10.4%) | 0 | 77 | [`visual_metrics_ca1_cold.json`](./benchmark/visual_metrics_ca1_cold.json) |
| **Sesudah CA1 (Hangat)** | 34 (44.2%) | 18 (23.4%) | 17 (22.1%) | 8 (10.4%) | 0 | 77 | [`visual_metrics_ca1_warm.json`](./benchmark/visual_metrics_ca1_warm.json) |

- Perbandingan per-shot `after_cold` vs `ca1_cold` menunjukkan **55/77 (71.4%)** kecocokan persis, dan `ca1_cold` vs `ca1_warm` sebesar **65/77 (84.4%)**: perbedaan di benchmark langsung berasal dari **Openverse tidak lagi mengalami timeout (`0` vs `6–8`)** sehingga kandidat Openverse terpilih di lebih banyak shot (`8` vs `4–5`) dan rotasi skor server Pixabay pada kueri langsung yang menggeser dedup sekuensial `used_candidate_ids` satu posisi pada shot berikutnya.
- Kesetaraan deterministik **100%** ketika input penyedia tetap dibuktikan oleh uji terisolasi `test_ca1_prefetch_fake_selection_identical_to_sequential` (`pref_picked == seq_picked`).

---

## 5. Kalibrasi Ambang Rekomendasi Sistem & Ruang Disk (E4)

- **Ukuran folder proyek (`BENCHMARK_ca1_cold.md`):** `assets/` **756.5 MB** · `export/` **627.3 MB** · `render/` + lain **365.4 MB** · Total proyek **1749.2 MB (1.71 GiB)** · Cache visual global **2281.5 MB** (hangat: proyek **1771.3 MB**, cache **2404.4 MB**).
- **RAM:** Minimum **8 GB** (`>= 7.5 GiB` terukur; puncak CA1 **1.38 GiB** dingin / **1.34 GiB** hangat) · Disarankan **16 GB** bila menyunting bersama CapCut Desktop.
- **CPU:** Minimum **4 core / 4 thread** · Disarankan **6–8+ thread** (rumus paralel `MAX_PARALLEL_BUILDS = max(1, min(3, cpu_count // 4))`).
- **Ruang Disk Kosong (Data Dir):** Minimum **15 GB** · Disarankan **20 GB+ SSD**.

> Ulangi benchmark kapan saja lewat `uv run python scripts/dev/benchmark.py`.
