# Baseline Benchmark Visual V102-C1 (Sebelum Optimasi)

- **Tanggal pengukuran:** 2026-11-26
- **Commit dasar kode:** `ee8a12e` (`stage/v102`, setelah V102-G-A2, **tanpa** perubahan kode optimasi Visual)
- **Mesin uji:** Windows 10 (AMD64) · 12th Gen Intel(R) Core(TM) i3-12100 (4 core / 8 thread) · RAM 7.75 GiB (~8 GB nominal) · FFmpeg 2025-11-17-git-e94439e49b · Python 3.11.9
- **Proyek uji:** `bench_panama_10m` (6 bab, **77 shot**, durasi video final **450.5 detik / 7.51 menit**, mode `--no-llm` agar deterministik, sumber aktif: Pixabay, Coverr, Wikimedia, NASA, Openverse)

---

## 1. Ringkasan End-to-End (Cold Cache vs Warm Cache)

| Metrik | Cache Dingin (`bench_panama_10m`) | Cache Hangat (`bench_panama_10m_warm`) | Sumber Angka |
|---|---|---|---|
| **Total waktu 5 fase** | **1399.7 dtk** (23.33 mnt) | **725.6 dtk** (12.09 mnt) | `BENCHMARK_baseline_cold.md` / `baseline_warm_summary.json` |
| **1. Naskah (Script)** | 0.0 dtk (RAM 96.7 MB, CPU 53.1%) | 0.09 dtk (RAM 98.5 MB, CPU 33.8%) | idem |
| **2. Suara (Voice)** | 46.2 dtk (RAM 101.7 MB, CPU 9.5%) | 11.01 dtk (RAM 104.3 MB, CPU 19.5%) | idem |
| **3. Visual (Fetch + Build)** | **1195.3 dtk** (RAM **1266.0 MB**, CPU **33.9%**) | **573.27 dtk** (RAM **1376.4 MB**, CPU **44.7%**) | idem |
| **4. Render Final** | 150.9 dtk (RAM 798.1 MB, CPU 76.5%) | 137.86 dtk (RAM 800.5 MB, CPU 79.5%) | idem |
| **5. Ekspor CapCut** | 7.3 dtk (RAM 148.0 MB, CPU 17.2%) | 3.4 dtk (RAM 151.2 MB, CPU 25.6%) | idem |
| **Ukuran Proyek / Cache** | Proyek 1786.0 MB / Cache 2378.1 MB | Proyek & cache berbagi `temp_root` | `BENCHMARK_baseline_cold.md` |

---

## 2. Rincian Fase Visual (`visual_metrics.json`)

> Sumber langsung: [`visual_metrics_baseline_cold.json`](./visual_metrics_baseline_cold.json) dan [`visual_metrics_baseline_warm.json`](./visual_metrics_baseline_warm.json).

| Sub-fase Visual | Cache Dingin (dtk) | % Visual (Dingin) | Cache Hangat (dtk) | % Visual (Hangat) | Catatan Teknis |
|---|---:|---:|---:|---:|---|
| **`search` (`search_wall_seconds`)** | **604.497** | **51.1%** | **278.103** | **49.1%** | Dingin: seluruh sumber; Hangat: Pixabay 248.253 dtk (`0` cache hit), Openverse 40.898 dtk |
| **`build` (`build_seconds` / FFmpeg + overhead)** | **312.453** | **26.4%** | **264.063** | **46.6%** | 77/77 klip sekuensial (`ffmpeg_build_seconds`: 234.73 dtk dingin / 202.40 dtk hangat; sisa ~61.7–77.7 dtk trim/ffprobe/overhead) |
| **`download` (`asset_download_seconds`)** | **260.422** | **22.0%** | **17.656** | **3.1%** | Dingin: 0 hit / 77 miss sekuensial; Hangat: 75 hit / 2 miss |
| **`manifest_save` (`manifest_save_seconds`)** | **6.449** | **0.5%** | **5.662** | **1.0%** | 155× penulisan JSON atomik di `fetch` + `build` |
| **`ai_keywords` + `anchor`** | **1.548** | **0.1%** | **2.595** | **0.5%** | Ekstraksi kata kunci tanpa LLM (`ai_kw_llm_calls=8` cache/regex) |
| **`thumbnail` (`thumbnail_seconds`)** | **0.000** | **0.0%** | **0.000** | **0.0%** | Lazy thumbnail (hanya saat grid UI dibuka, V101-A2.2) |
| **`ai_relevance` (`ai_relevance_seconds`)** | **0.000** | **0.0%** | **0.000** | **0.0%** | `check_ai_relevance=False` sesuai `benchmark.py` |
| **Total `VisualService.run_pipeline` (`total_seconds`)** | **1183.891** | **100%** | **566.735** | **100%** | `allocate`: 0.016 dtk (dingin) / 0.110 dtk (hangat) |

---

## 3. Rincian Sumber Pencarian & Distribusi Baseline

| Sumber | Dingin: HTTP Req / Cache Hit / Detik | Hangat: HTTP Req / Cache Hit / Detik | Distribusi Dingin | Distribusi Hangat |
|---|---|---|---|---|
| **Pixabay** | 400 / 0 / 274.512s | 398 / 0 / 248.253s | **37** (48.1%) | **36** (46.8%) |
| **Wikimedia** | 248 / 81 / 476.976s | 3 / 318 / 14.335s | **18** (23.4%) | **19** (24.7%) |
| **NASA** (`nasa` + `nasa_asset`) | 319 / 99 / 221.223s | 0 / 410 / 10.546s | **18** (23.4%) | **18** (23.4%) |
| **Openverse** | 60 / 26 / 188.670s | 10 / 82 / 40.898s | **4** (5.2%) | **4** (5.2%) |
| **Coverr** | 53 / 0 / 45.938s (403 rate limit) | 1 / 0 / 0.578s (auth_blocked) | **0** | **0** |
| **Total** | — | — | **77** | **77** |
