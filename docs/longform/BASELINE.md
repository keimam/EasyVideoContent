# Laporan Baseline Terukur & Proyeksi Arsitektur

Dokumen ini mencatat hasil pengukuran riil (benchmarking) mode pembuatan video bawaan (*upstream*) `MoneyPrinterTurbo` pada video ~60 detik, serta analisis proyeksi untuk video berdurasi 10 menit dan 20 menit bila menggunakan pendekatan bawaan apa adanya.

---

## 1. Lingkungan dan Konfigurasi Pengujian

- **Sistem Operasi**: Windows 11 (64-bit), PowerShell 5.1
- **Profil User**: `C:\Users\IT SUPPORT 2` (mengandung spasi)
- **Runtime**: Python 3.11.9, uv 0.13.7
- **FFmpeg**: FFmpeg 2025-11-17 gyan.dev full build (ada libass, nvenc, amf)
- **Branch**: `stage/0-audit` (berbasis commit `c8dbf96`)
- **Skrip Eksekusi**: `scripts/longform/baseline.ps1`
- **Parameter Video**:
  - Durasi target: ~60 detik
  - Rasio: 16:9 (1920x1080)
  - Naskah: Teks bahasa Inggris 144 kata (niche sejarah & psikologi, tanpa API key LLM)
  - Suara: Edge TTS (`en-US-ChristopherNeural-Male`, rate 1.0, tanpa API key TTS)
  - Subtitle: Aktif (`subtitle_enabled = true`)
  - BGM: Nonaktif (`bgm_type = none`)
  - Materi Visual: File lokal 9 gambar PNG dari `test/resources/1.png` .. `9.png`

---

## 2. Hasil Ukur Nyata (Measured Results) — Video ~60 Detik

Pengukuran dilakukan secara otomatis menggunakan skrip `scripts/longform/baseline.ps1` pada tanggal 5 November 2026.

### 2.1 Ringkasan Metrik Utama

| Metrik | Nilai Terukur | Keterangan |
|:---|:---|:---|
| **Total Wall-Clock Time** | **393,94 detik** (6,57 menit) | Dari pemanggilan skrip hingga selesai |
| **Puncak RAM (Peak Working Set)** | **383,90 MB** | Termasuk proses Python dan child process FFmpeg |
| **MoviePy Render Time** | **226,00 detik** (3,77 menit) | Tahap `video.generate_video` (komposisi TextClip & encode) |
| **Video Combining Time** | **128,00 detik** (2,13 menit) | Tahap `video.combine_videos` (resize, transisi, concat) |
| **Materials Prep Time** | **22,00 detik** | Preprocessing 9 gambar menjadi mp4 zoom (efek Ken Burns CPU) |
| **Audio (Edge TTS) Time** | **2,00 detik** | Sintesis audio 144 kata |
| **Subtitle Generation Time** | **< 1,00 detik** | Pembuatan subtitle SRT dari cue TTS |
| **Ukuran File Video Final** | **4,04 MB** (4.238.469 bytes) | File output MP4 |
| **Durasi Video Final (ffprobe)** | **58,166667 detik** | ~58,17 detik |
| **Resolusi Video** | **1920 x 1080** | 16:9 Full HD |
| **Video Codec & Format** | **H.264** (`yuv420p`) | 30 fps |
| **Audio Codec & Format** | **AAC** | Stereo, 44.100 Hz |
| **Bitrate Rata-rata** | **582,94 kbps** | Video + Audio |
| **Path File Output** | `F:\MPTLong\storage\tasks\5195b3f3-dd51-4105-8edb-514ea6bdc4e5\final-1.mp4` | Terverifikasi utuh |

### 2.2 Verifikasi `ffprobe` Asli

```text
codec_name=h264
width=1920
height=1080
r_frame_rate=30/1
codec_name=aac
r_frame_rate=0/0
duration=58.166667
size=4238469
bit_rate=582941
```

---

## 3. Analisis Profil Kinerja Upstream

Dari data terukur pada video 58,17 detik:
1. **Rasio Waktu Render terhadap Durasi Video**:
   - Total waktu pembuatan: 393,94 detik untuk 58,17 detik video $\rightarrow$ **6,77x durasi video**.
   - Render MoviePy saja: 226 detik $\rightarrow$ **3,88x durasi video** (kecepatan encode hanya ~0,26x *real-time*).
2. **Bottleneck Terbesar**:
   - 57,4% waktu dihabiskan di `generate_video` MoviePy (karena pembuatan objek `TextClip` untuk setiap baris subtitle dan alpha compositing per-frame di CPU).
   - 32,5% waktu dihabiskan di `combine_videos` (karena pemotongan dan penyesuaian ukuran klip MoviePy).
   - 5,6% waktu dihabiskan di `preprocess_video` (MoviePy `resized(lambda t: ...)` merender efek zoom per-frame di CPU Python).
3. **Tahap yang Efisien**:
   - Edge TTS (2 detik) dan penggabungan klip via FFmpeg Concat Demuxer (16 detik) sangat cepat dan efisien.

---

## 4. Proyeksi untuk Video Panjang (10 & 20 Menit) Menggunakan Cara Upstream

> [!WARNING]
> Bagian ini adalah **PROYEKSI / INFERENSI MATEMATIS** berbasis data ukur 58,17 detik di atas. Bagian ini bukan data hasil ukur riil 10/20 menit, karena menjalankan render 20 menit MoviePy secara langsung berisiko fatal pada memori dan waktu.

### 4.1 Asumsi dan Rumus Model Proyeksi

1. **Skala Durasi**:
   - Baseline: $T_0 \approx 1$ menit (58,17 s).
   - Target 10 menit: $T_{10} = 10 \times T_0 \approx 600$ s (naskah ~1.500 kata, ~120 klip visual).
   - Target 20 menit: $T_{20} = 20 \times T_0 \approx 1.200$ s (naskah ~3.000 kata, ~240 klip visual).
2. **Tahap Audio & Material Prep**:
   - Waktu TTS berskala linear: $t_{TTS}(T) = 2 \text{ s} \times (T / T_0)$.
   - Material prep berskala linear terhadap jumlah klip visual: $t_{prep}(T) = 22 \text{ s} \times (T / T_0)$.
3. **Tahap Combining (`combine_videos`)**:
   - Berskala linear terhadap jumlah potongan klip: $t_{comb}(T) = 128 \text{ s} \times (T / T_0)$.
4. **Tahap Render Final MoviePy (`generate_video`)**:
   - Tidak berskala linear sempurna melainkan **super-linear** ($O(T^{1,15})$):
     - Jumlah TextClip bertambah dari ~25 baris menjadi ~250–500 baris.
     - CompositeVideoClip menampung ratusan klip di array Python.
     - Garbage collector Python dan page faults memori meningkatkan overhead per frame secara kumulatif.
     - Rumus: $t_{render}(T) = 226 \text{ s} \times (T / T_0)^{1,15}$.
5. **Puncak RAM**:
   - Baseline menggunakan 384 MB (base Python + libraries ~200 MB, frame buffers ~180 MB).
   - Pada durasi panjang, penumpukan objek klip MoviePy, audio buffers, dan TextClip surfaces menyebabkan akumulasi memori:
     - Rumus: $RAM(T) \approx 200 \text{ MB} + 180 \text{ MB} \times (T / T_0)^{0,95}$.
6. **Probabilitas Kegagalan Eksekusi (Failure Risk)**:
   - Karena upstream berjalan sebagai satu sesi proses tunggal tanpa resume:
     - Edge TTS WebSocket timeout jika satu sesi audio > 5 menit tanpa chunking.
     - Potensi crash OOM atau MoviePy WinError 32 file lock di tengah jalan.
     - Jika crash terjadi di menit ke-18, 100% progres hilang.

### 4.2 Tabel Proyeksi Komparatif

| Parameter / Metrik | Baseline Terukur (~1 Menit) | PROYEKSI 10 Menit (~600 s) | PROYEKSI 20 Menit (~1.200 s) |
|:---|:---|:---|:---|
| **Jumlah Kata Naskah** | 144 kata | ~1.500 kata | ~3.000 kata |
| **Estimasi Jumlah Klip Visual** | 12 klip (loop 9 gambar) | ~120 klip visual | ~240 klip visual |
| **Jumlah Baris Subtitle** | 25 baris | ~250–300 baris | ~500–600 baris |
| **Waktu Audio TTS** | 2,0 s | ~20–30 s *(risiko timeout)* | ~45–75 s *(risiko tinggi putus)* |
| **Waktu Prep Material (Ken Burns)** | 22,0 s | ~220 s (3,7 menit) | ~440 s (7,3 menit) |
| **Waktu Video Combining** | 128,0 s | ~1.280 s (21,3 menit) | ~2.560 s (42,7 menit) |
| **Waktu MoviePy Render** | 226,0 s | ~3.189 s (53,2 menit) | ~7.076 s (117,9 menit / ~2 jam) |
| **Total Waktu Pembuatan** | **393,94 s** (6,6 menit) | **~4.720 s** (~1,3 jam) | **~10.150 s** (~2,8 jam) |
| **Puncak RAM (Peak Working Set)** | **383,90 MB** | **~1,8 GB – 2,5 GB** | **~3,5 GB – 5,0+ GB** *(kritis)* |
| **Ukuran File MP4** | **4,04 MB** | **~42 MB – 48 MB** | **~85 MB – 95 MB** |
| **Risiko Kegagalan Fatal** | Rendah (< 5%) | **Tinggi (~50%)** | **Sangat Tinggi (> 80%)** |
| **Fitur Resume jika Crash** | **TIDAK ADA** (ulang dari 0) | **TIDAK ADA** (ulang dari 0) | **TIDAK ADA** (ulang dari 0) |

---

## 5. Kesimpulan Kebutuhan Arsitektur Long Video

Berdasarkan hasil ukur dan proyeksi di atas, pendekatan upstream apa adanya **mustahil digunakan secara andal untuk video 10–20 menit**. Diperlukan 4 perubahan arsitektur fundamental:

1. **Render Berbasis FFmpeg Native (Menggantikan MoviePy)**:
   - Mengganti perenderan TextClip MoviePy dengan pembakaran subtitle ASS native FFmpeg (`libass`).
   - Mengganti zoom lambda MoviePy dengan filter `zoompan` native FFmpeg.
   - Mengurangi waktu render dari ~2 jam menjadi ~10–15 menit dan memangkas RAM dari >4 GB menjadi <500 MB.
2. **Produksi Berbasis Bab & Segmen (Modular Chunking)**:
   - Memecah video menjadi 4–6 bab mandiri. Tiap bab berdurasi 2–4 menit.
   - Merender per bab menjadi file mp4 parsial, lalu menggabungkan seluruh bab menggunakan FFmpeg Concat Demuxer.
3. **Mekanisme Checkpointing & Resume**:
   - Jika proses berhenti di bab 4, sistem dapat melanjutkan langsung dari bab 4 tanpa merender ulang bab 1, 2, dan 3.
4. **TTS Per Segmen**:
   - Melakukan request TTS per kalimat/segmen dengan caching lokal, menjamin koneksi stabil dan timing sinkron.
