# Laporan Baseline Terukur & Proyeksi Arsitektur

Dokumen ini mencatat hasil pengukuran riil (*benchmarking*) mode pembuatan video bawaan (*upstream*) `MoneyPrinterTurbo` pada video ~60 detik, serta analisis proyeksi untuk video berdurasi 10 menit dan 20 menit bila menggunakan pendekatan bawaan apa adanya.

---

## 1. Lingkungan dan Konfigurasi Pengujian

- **Sistem Operasi**: Windows 11 (64-bit), PowerShell 5.1
- **Profil User**: `C:\Users\IT SUPPORT 2` (mengandung spasi)
- **Runtime**: Python 3.11.9, uv 0.13.7
- **FFmpeg**: FFmpeg 2025-11-17 gyan.dev full build (ada libass, nvenc, amf)
- **Branch**: `stage/1-project-model`
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

Pengukuran dilakukan secara otomatis menggunakan skrip [scripts/longform/baseline.ps1](file:///F:/MPTLong/scripts/longform/baseline.ps1) yang diperbarui dengan **pelacakan rekursif seluruh pohon proses** (parent, child, grandchild).

### 2.1 Metodologi Pengukuran Peak RAM di `baseline.ps1` (Rekursif Seluruh Pohon Proses)
Pengukuran puncak RAM dilakukan dengan memantau seluruh proses di bawah proses Python utama secara rekursif setiap 200 ms:

```powershell
# Dikutip dari scripts/longform/baseline.ps1:
function Get-ProcessTreeStats {
    param([int]$RootPid)
    
    $allProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    if (-not $allProcesses) { 
        return @{ TotalBytes = 0; ProcessNames = @() } 
    }
    
    $pidsToScan = New-Object System.Collections.Generic.Queue[int]
    $pidsToScan.Enqueue($RootPid)
    $collectedPids = New-Object System.Collections.Generic.HashSet[int]
    [void]$collectedPids.Add($RootPid)
    
    $totalWorkingSet = 0
    $processNames = New-Object System.Collections.Generic.HashSet[string]
    
    $childrenMap = @{}
    foreach ($p in $allProcesses) {
        $ppid = [int]$p.ParentProcessId
        if (-not $childrenMap.ContainsKey($ppid)) {
            $childrenMap[$ppid] = New-Object System.Collections.Generic.List[object]
        }
        $childrenMap[$ppid].Add($p)
    }
    
    while ($pidsToScan.Count -gt 0) {
        $curPid = $pidsToScan.Dequeue()
        $matching = $allProcesses | Where-Object { $_.ProcessId -eq $curPid }
        if ($matching) {
            $totalWorkingSet += [int64]$matching.WorkingSetSize
            [void]$processNames.Add($matching.Name)
        }
        if ($childrenMap.ContainsKey($curPid)) {
            foreach ($child in $childrenMap[$curPid]) {
                $cid = [int]$child.ProcessId
                if (-not $collectedPids.Contains($cid)) {
                    [void]$collectedPids.Add($cid)
                    $pidsToScan.Enqueue($cid)
                }
            }
        }
    }
    
    return @{
        TotalBytes = $totalWorkingSet
        ProcessNames = [string[]]$processNames
    }
}
```

> [!IMPORTANT]
> **Koreksi Data RAM Tahap 0**:
> Angka awal pada Tahap 0 (383,90 MB) **keliru/kurang** karena kueri awal hanya membaca direct child 1 level. Dengan pelacakan rekursif penuh seluruh pohon proses pada pengujian ulang, terdeteksi proses MoviePy `imageio-ffmpeg` memanggil binary internal `ffmpeg-win-x86_64-v7.1.exe` dan `conhost.exe` yang aktif bersamaan dengan proses Python dan system FFmpeg.
> **Puncak RAM riil seluruh pohon proses adalah 1.068,62 MB (~1,07 GB)**.

### 2.2 Ringkasan Metrik Terukur (Pengujian Ulang Lengkap)

| Metrik | Nilai Terukur | Keterangan |
|:---|:---|:---|
| **Total Wall-Clock Time** | **410,82 detik** (6,85 menit) | Dari start skrip hingga video terbit |
| **Puncak RAM (Pohon Proses Lengkap)** | **1.068,62 MB** (~1,07 GB) | **Hasil ukur rekursif riil** (Python + MoviePy FFmpeg + System FFmpeg + Conhost) |
| *Puncak RAM Proses Python saja (lama)* | *383,90 MB* | *[KELIRU - hanya proses direct child, tidak mencakup sub-binary MoviePy]* |
| **Daftar Proses yang Terhitung** | `python.exe`, `ffmpeg-win-x86_64-v7.1.exe`, `conhost.exe`, `ffmpeg.exe` | Pohon proses lengkap terdeteksi |
| **MoviePy Render Time** | **226,00 detik** (3,77 menit) | Tahap `video.generate_video` (komposisi TextClip & encode) |
| **Video Combining Time** | **128,00 detik** (2,13 menit) | Tahap `video.combine_videos` (resize, transisi, concat) |
| **Materials Prep Time** | **22,00 detik** | Preprocessing 9 gambar menjadi mp4 zoom (efek Ken Burns CPU) |
| **Audio (Edge TTS) Time** | **2,00 detik** | Sintesis audio 144 kata |
| **Subtitle Generation Time** | **< 1,00 detik** | Pembuatan subtitle SRT dari cue TTS |
| **Ukuran File Video Final** | **4,06 MB** (4.253.999 bytes) | File output MP4 |
| **Durasi Video Final (ffprobe)** | **58,166667 detik** | ~58,17 detik |
| **Resolusi Video** | **1920 x 1080** | 16:9 Full HD |
| **Video Codec & Format** | **H.264** (`yuv420p`) | 30 fps |
| **Audio Codec & Format** | **AAC** | Stereo, 44.100 Hz |
| **Bitrate Rata-rata** | **585,08 kbps** | Video + Audio |
| **Path File Output** | `F:\MPTLong\storage\tasks\395454e7-e59e-4d03-9874-5709b990a8ff\final-1.mp4` | Terverifikasi utuh |

### 2.3 Verifikasi `ffprobe` Asli

```text
codec_name=h264
width=1920
height=1080
r_frame_rate=30/1
codec_name=aac
r_frame_rate=0/0
duration=58.166667
size=4253999
bit_rate=585077
```

---

## 3. Analisis Profil Kinerja Upstream

Dari data terukur pohon proses pada video 58,17 detik:
1. **Rasio Waktu Render terhadap Durasi Video**:
   - Total waktu pembuatan: 410,82 detik untuk 58,17 detik video $\rightarrow$ **7,06x durasi video**.
   - Render MoviePy saja: 226 detik $\rightarrow$ **3,88x durasi video** (kecepatan encode hanya ~0,26x *real-time*).
2. **Beban Memori Riil**:
   - MoviePy pada video 1 menit saja sudah memakan **~1,07 GB RAM** secara agregat karena menjalankan proses Python bersamaan dengan child process decoder dan encoder audio/video `ffmpeg-win-x86_64-v7.1.exe`.
3. **Bottleneck Terbesar**:
   - 55% waktu dihabiskan di `generate_video` MoviePy.
   - 31% waktu dihabiskan di `combine_videos`.

---

## 4. Proyeksi untuk Video Panjang (10 & 20 Menit) Menggunakan Cara Upstream

> [!WARNING]
> Bagian ini adalah **PROYEKSI / INFERENSI MATEMATIS** berbasis data ukur 58,17 detik pohon proses di atas. Bagian ini bukan data hasil ukur riil 10/20 menit.

### 4.1 Asumsi dan Rumus Model Proyeksi

1. **Skala Durasi**:
   - Baseline: $T_0 \approx 1$ menit (58,17 s).
   - Target 10 menit: $T_{10} = 10 \times T_0 \approx 600$ s (naskah ~1.500 kata, ~120 klip visual).
   - Target 20 menit: $T_{20} = 20 \times T_0 \approx 1.200$ s (naskah ~3.000 kata, ~240 klip visual).
2. **Tahap Render Final MoviePy (`generate_video`)**:
   - [DUGAAN]: Diproyeksikan berskala super-linear dengan eksponen 1,15:
     $$t_{render}(T) = 226 \text{ s} \times (T / T_0)^{1,15}$$
3. **Puncak RAM Pohon Proses**:
   - [DUGAAN]: Dengan nilai dasar pohon proses terukur 1.068 MB (~1,07 GB), akumulasi memori untuk 10–20 menit diproyeksikan:
     $$RAM_{tree}(T) \approx 600 \text{ MB} + 468 \text{ MB} \times (T / T_0)^{0,95}$$
     Pada 10 menit: diproyeksikan mencapai **~4,8 GB – 6,0 GB RAM**.
     Pada 20 menit: diproyeksikan melonjak ke **~9,0 GB – 12,0+ GB RAM** (sangat rentan crash kehabisan memori / Out Of Memory pada PC dengan RAM 8–16 GB).
4. **Faktor Risiko Kegagalan Eksekusi (Kualitatif)**:
   - Tanpa chunking per bab dan resume, probabilitas kegagalan pada sesi 10–20 menit sangat tinggi akibat beban memori pohon proses MoviePy, risiko websocket drop TTS, dan crash IO file locking Windows.

### 4.2 Tabel Proyeksi Komparatif

| Parameter / Metrik | Baseline Terukur (~1 Menit) | PROYEKSI 10 Menit (~600 s) | PROYEKSI 20 Menit (~1.200 s) |
|:---|:---|:---|:---|
| **Jumlah Kata Naskah** | 144 kata | ~1.500 kata | ~3.000 kata |
| **Estimasi Jumlah Klip Visual** | 12 klip (loop 9 gambar) | ~120 klip visual | ~240 klip visual |
| **Jumlah Baris Subtitle** | 25 baris | ~250–300 baris | ~500–600 baris |
| **Waktu Audio TTS** | 2,0 s | ~20–30 s *(risiko putus)* | ~45–75 s *(risiko tinggi putus)* |
| **Waktu Prep Material (Ken Burns)** | 22,0 s | ~220 s (3,7 menit) | ~440 s (7,3 menit) |
| **Waktu Video Combining** | 128,0 s | ~1.280 s (21,3 menit) | ~2.560 s (42,7 menit) |
| **Waktu MoviePy Render [DUGAAN]** | 226,0 s | ~3.189 s (53,2 menit) | ~7.076 s (117,9 menit / ~2 jam) |
| **Total Waktu Pembuatan [DUGAAN]** | **410,82 s** (6,85 menit) | **~4.750 s** (~1,3 jam) | **~10.200 s** (~2,8 jam) |
| **Puncak RAM Pohon Proses [DUGAAN]**| **1.068,62 MB** (~1,07 GB) | **~4,8 GB – 6,0 GB** *(sangat berat)*| **~9,0 GB – 12,0+ GB** *(kritis/OOM)* |
| **Ukuran File MP4** | **4,06 MB** | **~42 MB – 48 MB** | **~85 MB – 95 MB** |
| **Stabilitas Eksekusi** | Stabil pada 1 menit | Rawan crash memori & putus TTS | Hampir pasti crash OOM di MoviePy |
| **Fitur Resume jika Crash** | **TIDAK ADA** (ulang dari 0) | **TIDAK ADA** (ulang dari 0) | **TIDAK ADA** (ulang dari 0) |

---

## 5. Kesimpulan Kebutuhan Arsitektur Long Video

Temuan pohon proses di atas mempertegas urgensi meninggalkan MoviePy:
1. **Render Berbasis FFmpeg Native**: Menghapus seluruh beban memori MoviePy (`ffmpeg-win-x86_64-v7.1.exe` + Python frame buffers). [DUGAAN, diukur di Tahap 5]: Diproyeksikan mampu memangkas waktu render menjadi 10–15 menit dan menjaga RAM <500 MB.
2. **Produksi Berbasis Bab & Segmen**: Render parsial per bab mencegah memory leak kumulatif.
3. **Mekanisme Checkpointing & Resume**: Crash pada satu bab tidak merusak bab lain.
4. **TTS Per Paragraf**: Menjaga koneksi stabil dan prosodi narasi alami.
