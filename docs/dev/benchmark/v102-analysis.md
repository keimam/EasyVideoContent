# Analisis Hambatan Visual V102-C2

Berdasarkan pengukuran baseline C1 pada `bench_panama_10m` (6 bab, 77 shot, durasi final 450.5 detik) dari [`v102-baseline.md`](./v102-baseline.md), [`visual_metrics_baseline_cold.json`](./visual_metrics_baseline_cold.json), dan [`visual_metrics_baseline_warm.json`](./visual_metrics_baseline_warm.json):

---

## 1. Tiga Penyumbang Waktu Terbesar (Cold & Warm)

| Peringkat | Sub-fase Visual | Waktu Dingin (Share) | Waktu Hangat (Share) | Penyebab Utama dari Kode & Telemetri | Keputusan |
|---:|---|---:|---:|---|---|
| **1** | **Search** (`search_wall_seconds`) | **604.497 dtk** (**51.1%**) | **278.103 dtk** (**49.1%**) | Loop `fetch_all` memanggil `_search_shot_across_providers` secara berurutan per shot (paralel hanya antar-sumber di dalam 1 shot). Di mode hangat, Pixabay masih memakan `248.253 dtk` (`398` HTTP, `0` cache hit). Urutan pemilihan kandidat antar-shot tetap wajib dipertahankan untuk aturan 60% / 3-berturut. | Pemilihan tetap berurutan (sesuai C3); tidak mengubah aturan skor/relevansi |
| **2** | **Build Klip** (`build_seconds`: FFmpeg + overhead) | **312.453 dtk** (**26.4%**) | **264.063 dtk** (**46.6%**) | `build_all` membangun seluruh **77 klip** 1-per-1 secara sekuensial padahal CPU memiliki 4 core / 8 thread (`avg CPU` sistem saat Visual hanya 33.9%–44.7%). Terdapat pula **77.7 dtk** (dingin) / **61.7 dtk** (hangat) selisih antara `build_seconds` vs `ffmpeg_build_seconds` (overhead `trim_source_video_asset` dengan potensi fallback re-encode `libx264` sebelum `build_video_clip` + `ffprobe -count_frames`). | **Kerjakan C4 & C5**: paralelisasi terbatas `MAX_PARALLEL_BUILDS` + eliminasi fallback re-encode ganda & audit skip/thumbnail |
| **3** | **Download Aset** (`asset_download_seconds`) | **260.422 dtk** (**22.0%**) | **17.656 dtk** (3.1%) | Pada cache dingin, **77 unduhan terpilih** (`0` hit / `77` miss) berjalan 1-per-1 secara sinkron di dalam loop `fetch_all` (`active_provider.fetch_asset`) menunggu jaringan selesai sebelum shot berikutnya mulai mencari/mengunduh. | **Kerjakan C3**: unduh aset terpilih paralel terbatas (`MAX_PARALLEL_DOWNLOADS = 3`) setelah fase pemilihan kandidat berurutan |

---

## 2. Fase Tidak Relevan / Sudah Minimal

| Sub-fase | Dingin | Hangat | Status C2 |
|---|---:|---:|---|
| **`thumbnail_seconds`** | **0.000 dtk** | **0.000 dtk** | Lazy thumbnail (hanya dibuat saat grid UI dibuka) sudah aktif sejak V101-A2.2; verifikasi saja agar tidak ada duplikasi saat grid dibuka (C5) |
| **`manifest_save_seconds`** | 6.449 dtk (0.5%) | 5.662 dtk (1.0%) | Sudah kecil (<1%); cukup jaga aturan **hanya thread utama** yang memanggil `save_manifest` |
| **`ai_keywords` + `anchor`** | 1.548 dtk (0.1%) | 2.595 dtk (0.5%) | Sudah cepat / ter-cache per bab |
| **`ai_relevance_seconds`** | 0.000 dtk | 0.000 dtk | Nonaktif pada tolok ukur standar (`check_ai_relevance=False`) |

---

## 3. Rencana Eksekusi C3–C5 (Hanya untuk Fase Terbukti Besar)
1. **C3 (`download` 260.4 dtk / 22%):** Pisahkan fase *pemilihan kandidat berurutan per shot* (menjaga aturan 60% / 3-berturut dan `used_candidate_ids` tetap deterministik) dari fase *unduh aset paralel terbatas* (`MAX_PARALLEL_DOWNLOADS = 3`, penulisan file atomik `.tmp -> os.replace`, menjaga rate-limit dan pemutus B1/B3 per penyedia, manifest ditulis hanya dari thread utama).
2. **C4 (`build` 312.5 dtk / 26.4% dingin, 46.6% hangat):** Paralelisasi `build_all` menggunakan `ThreadPoolExecutor(max_workers=MAX_PARALLEL_BUILDS)` dengan `MAX_PARALLEL_BUILDS = max(1, min(3, (os.cpu_count() or 4) // 4))`, membagi flag `-threads` FFmpeg per worker (`max(1, (os.cpu_count() or 4) // MAX_PARALLEL_BUILDS)`), mempertahankan `save_manifest` hanya di thread utama, isolasi progres 0→100%, serta ketahanan bila satu klip gagal (`skipped`).
3. **C5 (Kerja ganda pada `build` / `cache` / `thumbnail`):**
   - Hilangkan *double transcode* di `trim_source_video_asset` (`builder.py`) dengan tidak melakukan encode ulang `libx264` kedua sebelum `build_video_clip` (pertahankan hanya `-c copy` cepat jika aman, atau lewati saat copy gagal karena `build_video_clip` langsung melakukan `-ss/-frames:v` presisi sekali jalan).
   - Verifikasi skip `build_shot_clip` saat `shot_hash` valid & file klip utuh (termasuk ketika `build_all` dijalankan paralel, dan tanpa merusak hash akibat mutasi asset in-place).
   - Audit pembuatan thumbnail di UI/cache agar tidak dibuat ulang bila file thumbnail cache & `asset_content_hash` cocok.
