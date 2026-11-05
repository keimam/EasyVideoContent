# Laporan Baseline Terukur & Proyeksi Arsitektur

Dokumen ini mencatat hasil pengukuran riil (*benchmarking*) mode pembuatan video bawaan (*upstream*) `MoneyPrinterTurbo` pada video ~60 detik, serta analisis proyeksi untuk video berdurasi 10 menit dan 20 menit bila menggunakan pendekatan bawaan apa adanya.

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
  - Naskah: Teks bahasa Inggris 144 kata (contoh naskah uji, tanpa API key LLM)
  - Suara: Edge TTS (`en-US-ChristopherNeural-Male`, rate 1.0, tanpa API key TTS)
  - Subtitle: Aktif (`subtitle_enabled = true`)
  - BGM: Nonaktif (`bgm_type = none`)
  - Materi Visual: File lokal 9 gambar PNG dari `test/resources/1.png` .. `9.png`

---

## 2. Hasil Ukur Nyata (Measured Results) — Video ~60 Detik

Pengukuran dilakukan secara otomatis menggunakan skrip [scripts/longform/baseline.ps1](file:///F:/MPTLong/scripts/longform/baseline.ps1) pada tanggal 5 November 2026.

### 2.1 Metodologi Pengukuran Peak RAM di `baseline.ps1`
Pengukuran puncak RAM dilakukan dengan melakukan polling berkala setiap 150 milidetik terhadap proses utama Python dan menjumlahkannya dengan seluruh proses anak (*child processes*, seperti `ffmpeg.exe`):

```powershell
# Dikutip dari scripts/longform/baseline.ps1 baris 81-105:
$PeakRamBytes = 0
$ParentPid = $proc.Id

while (-not $proc.HasExited) {
    try {
        $proc.Refresh()
        $currentRam = $proc.WorkingSet64
        if ($currentRam -gt $PeakRamBytes) {
            $PeakRamBytes = $currentRam
        }
        
        # Also check child processes (ffmpeg, etc.)
        $childProcs = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentPid" -ErrorAction SilentlyContinue
        if ($childProcs) {
            $childRam = ($childProcs | Measure-Object -Property WorkingSetSize -Sum).Sum
            $totalRam = $currentRam + $childRam
            if ($totalRam -gt $PeakRamBytes) {
                $PeakRamBytes = $totalRam
            }
        }
    } catch {
        # ignore transient query errors
    }
    Start-Sleep -Milliseconds 150
}
```
*Pembuktian*: Pada Windows, pemanggilan `Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentPid"` mengembalikan objek proses anak yang dipicu oleh Python (seperti subprocess FFmpeg saat pra-render klip). Properti `WorkingSetSize` dari proses anak dijumlahkan dengan `WorkingSet64` dari proses Python utama untuk mencatat total Working Set riil sistem selama proses berjalan.

### 2.2 Ringkasan Metrik Terukur

| Metrik | Nilai Terukur | Keterangan |
|:---|:---|:---|
| **Total Wall-Clock Time** | **393,94 detik** (6,57 menit) | Dari pemanggilan skrip hingga selesai |
| **Puncak RAM (Peak Working Set)** | **383,90 MB** | Terukur gabungan Python + child process FFmpeg |
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

### 2.3 Verifikasi `ffprobe` Asli

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
   - Waktu TTS diasumsikan berskala linear: $t_{TTS}(T) = 2 \text{ s} \times (T / T_0)$.
   - Material prep berskala linear terhadap jumlah klip visual: $t_{prep}(T) = 22 \text{ s} \times (T / T_0)$.
3. **Tahap Combining (`combine_videos`)**:
   - Berskala linear terhadap jumlah potongan klip: $t_{comb}(T) = 128 \text{ s} \times (T / T_0)$.
4. **Tahap Render Final MoviePy (`generate_video`)**:
   - [DUGAAN]: Diproyeksikan berskala super-linear dengan eksponen 1,15:
     $$t_{render}(T) = 226 \text{ s} \times (T / T_0)^{1,15}$$
     *Alasan Pemilihan Eksponen 1,15*: MoviePy menampung seluruh objek `TextClip` subtitle (~250 klip untuk 10 menit, ~500 klip untuk 20 menit) di dalam list `CompositeVideoClip([video_clip, *text_clips])`. Saat rendering berlangsung, evaluasi frame dan composite mask di Python mengalami degradasi performa non-linear akibat overhead penelusuran list klip aktif per frame, overhead siklus Garbage Collector Python untuk membersihkan buffer array sementara, serta fragmentasi alokasi heap memori pada runtime berdurasi panjang.
5. **Puncak RAM**:
   - [DUGAAN]: Diproyeksikan menggunakan rumus:
     $$RAM(T) \approx 200 \text{ MB} + 180 \text{ MB} \times (T / T_0)^{0,95}$$
     *Alasan Pemilihan Rumus*: Nilai dasar 200 MB merupakan footprint statis Python 3.11 dan modul-modul yang di-import (MoviePy, Pillow, NumPy, Torch/ONNX dependencies). Nilai 180 MB adalah konsumsi dinamis frame buffer dan clip references pada baseline 1 menit. Eksponen 0,95 dipilih karena meskipun frame video lama dilepaskan, referensi objek `TextClip`, cache mask alpha, dan internal state MoviePy audio/video reader tetap terakumulasi secara bertahap seiring bertambahnya ratusan klip dalam satu sesi proses.
6. **Faktor Risiko Kegagalan Eksekusi (Kualitatif)**:
   - Upstream menjalankan proses sebagai satu sesi tunggal tanpa mekanisme resume:
     - **Putusnya Koneksi TTS**: Menyuapkan 1.500–3.000 kata dalam satu request WebSocket Edge-TTS tunggal berisiko terputus akibat network timeout atau batas koneksi pihak penyedia.
     - **File Locking di Windows**: Kerentanan `WinError 32` (file sedang digunakan oleh proses lain) pada file audio/video sementara MoviePy meningkat drastis saat menangani ratusan file dalam durasi 1–2 jam.
     - **Crash Tanpa Checkpoint**: Bila terjadi kegagalan pada menit ke-18 (misalnya out-of-memory atau error encoding), seluruh 18 menit pekerjaan hilang dan harus dimulai ulang dari nol.

### 4.2 Tabel Proyeksi Komparatif

| Parameter / Metrik | Baseline Terukur (~1 Menit) | PROYEKSI 10 Menit (~600 s) | PROYEKSI 20 Menit (~1.200 s) |
|:---|:---|:---|:---|
| **Jumlah Kata Naskah** | 144 kata | ~1.500 kata | ~3.000 kata |
| **Estimasi Jumlah Klip Visual** | 12 klip (loop 9 gambar) | ~120 klip visual | ~240 klip visual |
| **Jumlah Baris Subtitle** | 25 baris | ~250–300 baris | ~500–600 baris |
| **Waktu Audio TTS** | 2,0 s | ~20–30 s *(risiko putus koneksi)* | ~45–75 s *(risiko tinggi putus)* |
| **Waktu Prep Material (Ken Burns)** | 22,0 s | ~220 s (3,7 menit) | ~440 s (7,3 menit) |
| **Waktu Video Combining** | 128,0 s | ~1.280 s (21,3 menit) | ~2.560 s (42,7 menit) |
| **Waktu MoviePy Render [DUGAAN]** | 226,0 s | ~3.189 s (53,2 menit) | ~7.076 s (117,9 menit / ~2 jam) |
| **Total Waktu Pembuatan [DUGAAN]** | **393,94 s** (6,6 menit) | **~4.720 s** (~1,3 jam) | **~10.150 s** (~2,8 jam) |
| **Puncak RAM [DUGAAN]** | **383,90 MB** | **~1,8 GB – 2,5 GB** | **~3,5 GB – 5,0+ GB** *(kritis)* |
| **Ukuran File MP4** | **4,04 MB** | **~42 MB – 48 MB** | **~85 MB – 95 MB** |
| **Stabilitas Eksekusi** | Stabil pada pengujian | Rawan putus TTS & memori bengkak | Sangat rawan crash OOM di MoviePy |
| **Fitur Resume jika Crash** | **TIDAK ADA** (ulang dari 0) | **TIDAK ADA** (ulang dari 0) | **TIDAK ADA** (ulang dari 0) |

---

## 5. Kesimpulan Kebutuhan Arsitektur Long Video

Berdasarkan hasil ukur dan proyeksi di atas, pendekatan upstream apa adanya **tidak memadai untuk video 10–20 menit**. Diperlukan 4 perubahan arsitektur fundamental:

1. **Render Berbasis FFmpeg Native (Menggantikan MoviePy)**:
   - Mengganti perenderan TextClip MoviePy dengan pembakaran subtitle ASS native FFmpeg (`libass`).
   - Mengganti zoom lambda MoviePy dengan filter `zoompan` native FFmpeg.
   - [DUGAAN, diukur di Tahap 5]: Diproyeksikan mampu memangkas waktu render menjadi 10–15 menit dan menjaga RAM <500 MB.
2. **Produksi Berbasis Bab & Segmen (Modular Chunking)**:
   - Memecah video menjadi 4–6 bab mandiri. Tiap bab berdurasi 2–4 menit.
   - Merender per bab menjadi file mp4 parsial, lalu menggabungkan seluruh bab menggunakan FFmpeg Concat Demuxer.
3. **Mekanisme Checkpointing & Resume**:
   - Jika proses berhenti di bab 4, sistem dapat melanjutkan langsung dari bab 4 tanpa merender ulang bab 1, 2, dan 3.
4. **TTS Per Segmen / Per Paragraf**:
   - Melakukan request TTS secara terkelompok atau per segmen dengan caching lokal, menjamin koneksi stabil dan timing sinkron.
