# Panduan Penggunaan Tahap 1: Model Proyek, Preset, Manifest, & Resume

Dokumen ini menjelaskan penggunaan modul dan CLI `cli_longform.py` yang dibangun pada **Tahap 1** untuk EasyVideoContent (MoneyPrinterTurbo Longform Fork).

Seluruh kode Tahap 1 berada di `app/services/longform/` dan `cli_longform.py` pada root direktori, dengan **nol perubahan (0 baris modifikasi)** pada file upstream.

---

## 1. Arsitektur & Model Data

### Hierarki Data (K3)
Proyek video panjang disusun dengan struktur bertingkat:
```
ProjectManifest (proyek)
  └── Chapter (bab, unit render parsial & resume)
        └── TtsGroup (paragraf narasi, unit sintesis TTS)
              └── Segment (kalimat narasi, unit visual & efek gerak Ken Burns)
```

- **TtsGroup (Paragraf)**: Menjadi unit sintesis suara Edge-TTS agar intonasi narasi tetap alami dan kohesif antar-kalimat. Hash audio dihitung di tingkat grup paragraf ini.
- **Segment (Kalimat)**: Menjadi unit visual (gambar AI / stock video / file lokal) dan durasi visual. Offset waktu start/end dalam audio grup diisi saat tahap perataan waktu (Tahap 3).
- **Chapter (Bab)**: Menjadi batas modular render parsial FFmpeg. Hash bab adalah gabungan berurutan SHA-256 dari seluruh hash audio grup dan segmen visual di dalamnya, setelan encoder, gaya subtitle, dan snapshot preset (K1, K2).
- **ProjectManifest**: Menyimpan seluruh konfigurasi proyek, snapshot preset kanonik, dan metadata output.

### Struktur Direktori Proyek
Setiap proyek disimpan di `storage/longform_projects/<project_id>/` dengan struktur direktori standar:
```
storage/longform_projects/<project_id>/
├── manifest.json                  # Single source of truth (penulisan atomik + fsync)
├── .lock                          # Kunci advisory proses tunggal (PID + timestamp)
├── assets/
│   ├── visual/
│   │   ├── raw/                   # File gambar/video sumber lokal hasil staging (K7)
│   │   └── clips/                 # Potongan video per-segmen ter-render
│   ├── audio/
│   │   └── groups/                # File audio narasi per TtsGroup (paragraf)
│   └── chapters/                  # Render MP4 per bab
└── output/
    └── final_video.mp4            # Hasil akhir video utuh (concat FFmpeg)
```

---

## 2. Fitur Keandalan (Reliability)

1. **Penulisan Atomik & Tahan Kegagalan (K5)**:
   - Penulisan `manifest.json` selalu dilakukan ke file temp acak (`manifest.json.tmp.<uuid>`), di-`flush` dan di-`fsync` ke disk, kemudian diganti secara atomik dengan `os.replace`.
   - Mengatasi potensi `PermissionError` sementara pada Windows (misal saat dipindai antivirus) dengan retry eksponensial otomatis (5 kali percobaan).
2. **Project Lock dengan Pemulihan Kunci Basi (K5)**:
   - File `.lock` mencegah dua proses mengerjakan proyek yang sama secara bersamaan.
   - Jika proses crash mendadak meninggalkan `.lock`, proses berikutnya memeriksa keaktifan PID menggunakan Win32 API (`OpenProcess` & `GetExitCodeProcess`). Bila proses lama sudah mati, lock basi diambil alih secara otomatis dengan pesan log peringatan.
3. **Staging File Visual Lokal (K7)**:
   - File visual eksternal disalin ke `assets/visual/raw/` dengan penamaan berbasis hash konten. Seluruh path di dalam manifest divalidasi dan diwajibkan berupa path relatif ke folder proyek.
4. **Perhitungan Rencana Eksekusi (Planner & WorkUnit)**:
   - Sistem membandingkan manifest dengan kondisi nyata di disk (keberadaan file dan ukuran > 0 byte) serta kecocokan hash kanonik.
   - Status unit kerja: `pending` (file belum ada / 0 byte), `stale` (isi naskah/snapshot berubah sehingga hash berbeda), atau `completed`.

---

## 3. Panduan Penggunaan CLI (`cli_longform.py`)

### 3.1 Manajemen Preset
Melihat daftar preset yang tersedia (termasuk deteksi apakah preset bawaan `builtin` atau kustom `custom` di `storage/presets/`):
```powershell
uv run python cli_longform.py presets list
```

Memvalidasi sintaks dan skema file preset JSON:
```powershell
uv run python cli_longform.py presets validate resource/presets/documentary_en.json
```

### 3.2 Pembuatan Proyek Baru
Membuat proyek baru dari file naskah Markdown:
```powershell
uv run python cli_longform.py project create `
  --title "The Mystery of Roanoke" `
  --preset documentary_en `
  --script docs/longform/examples/sample_script.md `
  --id roanoke_project
```
Output akan menampilkan rincian jumlah bab, TTS group, segmen visual, estimasi durasi, dan peringatan durasi (jika naskah < 10 menit atau > 20 menit).

### 3.3 Melihat Ringkasan Proyek
```powershell
uv run python cli_longform.py project show roanoke_project
```

### 3.4 Memeriksa Rencana Kerja (Planning & Status)
Menampilkan status tiap unit kerja (`tts_group_audio`, `visual_segment`, `chapter_render`, `final_concat`):
```powershell
uv run python cli_longform.py project plan roanoke_project
```

### 3.5 Simulasi Eksekusi & Uji Resume (`dev-simulate`)
Menjalankan simulasi transisi status pipa data tanpa memanggil Edge-TTS, AI Image, atau FFmpeg nyata:
```powershell
# Menjalankan simulasi penuh
uv run python cli_longform.py project dev-simulate roanoke_project

# Menjalankan simulasi dengan jeda waktu buatan (detik)
uv run python cli_longform.py project dev-simulate roanoke_project --delay 0.2

# Menguji kemampuan RESUME dengan menyuntikkan kegagalan pada unit tertentu:
uv run python cli_longform.py project dev-simulate roanoke_project --fail-at ch2_g1_audio
```
Setelah kegagalan terjadi, periksa `project plan roanoke_project` untuk melihat bab 1 tetap `completed` dan bab 2 `pending`. Jalankan kembali tanpa `--fail-at` untuk melanjutkan (resume) hingga selesai.
