# Usulan Desain Arsitektur Mode Long Video (10–20 Menit)

> [!NOTE]
> Dokumen ini merupakan **USULAN DESAIN (PROPOSAL)** teknis untuk Tahap 1–8. Pada Tahap 0 ini, tidak ada kode aplikasi (`app/` atau `webui/`) yang diubah atau diimplementasikan. Seluruh klaim performa dan integrasi pihak ketiga yang belum diuji secara empiris diberi label **[DUGAAN]**.

---

## 1. Filosofi Desain: Niche Bebas Berbasis Preset

Fork ini dirancang untuk pembuatan video panjang channel YouTube faceless dengan **NICHE BEBAS** (sejarah, psikologi, sains, misteri, bisnis, biografi, cerita horor, teknologi, dll.). Seluruh karakteristik konten — mulai dari bahasa, audiens, gaya bahasa naskah, struktur bab, petunjuk visual, hingga gaya subtitle dan audio — dikendalikan melalui **PRESET** yang fleksibel dan modular, **BUKAN** di-hardcode di kode Python maupun di prompt LLM.

---

## 2. Skema PRESET Longform Video

Preset disimpan dalam format JSON mandiri. Pengguna dapat menambah preset baru semudah meletakkan file `.json` di folder preset tanpa perlu mengubah kode sumber aplikasi.

### 2.1 Lokasi Penyimpanan & Penemuan (*Discovery*)
1. **Preset Bawaan Sistem**: `resource/presets/*.json`
2. **Preset Pengguna**: `storage/presets/*.json` (diprioritaskan jika ada ID yang sama).

### 2.2 Spesifikasi Field Skema Preset

| Field | Tipe | Deskripsi & Contoh |
|:---|:---|:---|
| `id` | `str` (slug) | Identifier unik preset (misal: `history_epic_en`, `psychology_stoic_id`, `mystery_dark_en`). |
| `nama` | `str` | Nama tampilan di UI (misal: "Epic History Documentary (English)"). |
| `versi_skema` | `str` | Versi semver skema preset (misal: `"1.0.0"`). |
| `bahasa` | `str` | Kode bahasa narasi (misal: `"en-US"`, `"id-ID"`). |
| `audiens` | `str` | Deskripsi audiens target (misal: "US YouTube viewers interested in ancient history and deep mysteries"). |
| `gaya_narasi` | `str` | Nada & gaya bahasa naskah (misal: "immersive storytelling, suspenseful, authoritative yet engaging, cinematic pacing"). |
| `struktur_bab` | `list[str]` | Alur baku bab (misal: `["Hook & Introduction", "Historical Context", "The Turning Point", "Climax & Fallout", "Philosophical Takeaway"]`). |
| `aturan_hook` | `str` | Aturan 15 detik pertama (misal: "Langsung ke inti misteri, gunakan pertanyaan provokatif, dilarang menyapa penonton atau perkenalan channel"). |
| `target_kata_per_menit` | `int` | Kecepatan bicara acuan (misal: `145` WPM). |
| `gaya_subtitle` | `dict` | Styling ASS subtitle: font, ukuran, margin bawah, fore_color, outline_color, outline_width, rounded background, format (`sentence_level` default, `word_by_word` opsional). |
| `gaya_visual` | `str` | Arahan visual utama (misal: "cinematic realism, moody chiaroscuro lighting, vintage historical oil painting style"). |
| `template_prompt_gambar` | `dict` | Prefix & suffix prompt AI image (misal prefix: `"cinematic masterpiece, historical photograph, 4k"`). |
| `sumber_visual_disukai` | `dict[str, float]` | Rasio campuran visual (misal: `{"ai_image": 0.6, "stock_video": 0.3, "local": 0.1}`). |
| `voice_default` | `str` | Suara TTS bawaan (misal: `"en-US-ChristopherNeural-Male"`). |
| `bgm_default` | `dict` | Kategori BGM, volume bawaan, dan parameter ducking (misal: category `"ambient_dark"`, volume `0.15`, ducking `-16dB`). |
| `gaya_ken_burns` | `str` | Pola animasi gambar bawaan: `"alternating"`, `"zoom_in"`, `"zoom_out"`, `"pan_left"`, `"pan_right"`. |

### 2.3 Validasi & Snapshot Preset pada Manifest
- **Validasi**: Diimplementasikan menggunakan model Pydantic (`LongformPreset`) dan JSON Schema saat aplikasi memuat file preset.
- **Snapshot Immutability**: Saat sebuah proyek long video dibuat, `project_manifest.json` **wajib menyimpan `preset_id` DAN snapshot penuh objek preset** yang digunakan saat itu. Tujuannya: bila pengguna kemudian mengedit file preset di masa mendatang, proyek-proyek lama yang belum selesai atau yang ingin di-render ulang tidak akan terpengaruh atau rusak.

---

## 3. Struktur Proyek & Satu Sumber Kebenaran (*Single Source of Truth*)

### 3.1 Struktur Direktori Proyek

Semua proyek long video disimpan terstruktur di bawah `storage/longform_projects/<project_id>/`:

```text
storage/longform_projects/<project_id>/
├── project_manifest.json          # SATU-SATUNYA sumber kebenaran (manifest + state proyek)
├── assets/
│   ├── audio/
│   │   ├── chapter_01/
│   │   │   ├── seg_001.mp3        # Audio TTS per segmen
│   │   │   └── chapter_01.mp3     # Audio gabungan bab
│   │   └── ...
│   ├── visual/
│   │   ├── raw/                   # File gambar AI mentah, footage stok, aset lokal
│   │   └── processed/             # Klip MP4 segmen (hasil Ken Burns / scaling)
│   ├── subtitles/
│   │   ├── chapter_01.ass         # Subtitle ASS per bab untuk burn FFmpeg
│   │   └── full_timeline.ass      # Subtitle utuh seluruh video (tanpa burn)
│   └── bgm/
│       ├── raw_bgm.mp3            # File background music
│       └── ducked_bgm.mp3         # BGM hasil ducking & loudness normalization
├── chapters/
│   ├── chapter_01.mp4             # Render utuh Bab 1 (visual + voice + ASS subtitle)
│   ├── chapter_02.mp4             # Render utuh Bab 2
│   └── ...
└── export/
    ├── final_video.mp4            # Video final (concat seluruh bab + BGM)
    ├── youtube_chapters.txt       # Timestamp bab siap salin untuk deskripsi YouTube
    ├── subtitles/
    │   ├── full_timeline.srt      # Subtitle SRT utuh untuk upload YouTube
    │   └── full_timeline.ass      # Subtitle ASS utuh untuk player eksternal
    └── capcut_project/            # Project CapCut Desktop siap edit
        ├── draft_content.json     # [DUGAAN] Timeline multi-track CapCut
        └── draft_meta_info.json   # [DUGAAN] Metadata project CapCut
```

### 3.2 Satu Sumber Kebenaran & Penulisan Atomik
- Tidak ada file status terpisah (`project_state.json` dihapus dari usulan awal). **`project_manifest.json` adalah satu-satunya sumber kebenaran** (*single source of truth*) yang memuat spesifikasi naskah, metadata, hash cache, dan status pengerjaan setiap bab dan segmen (`pending`, `rendering`, `completed`, `failed`).
- **Mekanisme Penulisan Atomik (*Atomic Write*)**:
  Untuk mencegah kerusakan data manifest bila komputer mati mendadak atau terjadi crash proses:
  1. Manifest ditulis ke file sementara terlebih dahulu: `project_manifest.json.tmp.<uuid>`
  2. File sementara di-flush ke disk (`file.flush(); os.fsync(file.fileno())`).
  3. Dilakukan operasi atomik `os.replace("project_manifest.json.tmp.<uuid>", "project_manifest.json")`.
  Operasi `os.replace` dijamin atomik oleh sistem operasi (baik NTFS di Windows maupun POSIX di Linux).

### 3.3 Skema Manifest (`project_manifest.json`)

Berikut contoh struktur manifest (dengan contoh naskah sejarah sebagai ilustrasi):

```json
{
  "pipeline_version": "1.0.0",
  "project_id": "proj-uuid-12345",
  "title": "Ancient Wisdom and Modern Psychology (Contoh)",
  "aspect_ratio": "16:9",
  "preset_id": "history_epic_en",
  "preset_snapshot": { ... },
  "encoder_settings": {
    "video_encoder": "libx264",
    "video_profile": "high",
    "video_level": "4.2",
    "pix_fmt": "yuv420p",
    "resolution": [1920, 1080],
    "fps": 30.0,
    "audio_codec": "aac",
    "audio_sample_rate": 44100,
    "audio_channels": 2
  },
  "chapters": [
    {
      "chapter_index": 1,
      "chapter_title": "The Stoic Mirror",
      "status": "completed",
      "chapter_hash": "a1b2c3d4...",
      "output_file": "chapters/chapter_01.mp4",
      "segments": [
        {
          "segment_index": 1,
          "narration_text": "Throughout ancient history, humans searched for meaning.",
          "visual": {
            "type": "ai_image",
            "prompt": "Ancient Roman philosopher sitting in study, cinematic, 4k",
            "motion": "zoom_in",
            "source_content_hash": "f4c7e2b1...",
            "source_file": "assets/visual/raw/img_01_01.png",
            "rendered_clip": "assets/visual/processed/clip_01_01.mp4",
            "visual_hash": "d8a1c9e3..."
          },
          "audio": {
            "tts_provider": "edge",
            "voice_name": "en-US-ChristopherNeural-Male",
            "voice_rate": 1.0,
            "duration": 5.42,
            "file_path": "assets/audio/chapter_01/seg_001.mp3",
            "audio_hash": "b7e2d9f4..."
          }
        }
      ]
    }
  ]
}
```

---

## 4. Strategi Render Berbasis FFmpeg Native

Seluruh alur render MoviePy ditinggalkan untuk mode long video, digantikan oleh pipeline FFmpeg native yang sangat cepat, ringan memori, dan bebas dari Python GIL bottleneck.

### 4.1 Efek Ken Burns Native FFmpeg & Catatan Jebakan `zoompan`
- Mengganti fungsi MoviePy CPU `clip.resized(lambda t: ...)` dengan filter FFmpeg native `zoompan`:
  ```bash
  ffmpeg -loop 1 -i image.png -vf "zoompan=z='min(zoom+0.0015,1.2)':d=150:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30" -t 5 -c:v libx264 -pix_fmt yuv420p output.mp4
  ```
- **Jebakan Umum Filter `zoompan`**:
  1. *Sinkronisasi Durasi*: Penggunaan `-loop 1` dengan `d=<frames>` rentan menghasilkan frame berlebih atau hang bila parameter `-t` tidak diselaraskan persis dengan `d / fps`. Solusi: `d` selalu dihitung persis $d = \text{round}(\text{duration} \times \text{fps})$.
  2. *Micro-jittering / Getaran Gambar*: Parameter posisi `x` dan `y` yang bernilai pecahan secara default dibulatkan ke integer per frame, memicu getaran gambar (*jitter*). Solusi: gunakan ekspresi pergerakan halus (*smooth trajectory*) atau render pada resolusi internal lebih tinggi sebelum di-downscale ke 1080p.
  3. [DUGAAN, diukur di Tahap 5]: Kecepatan render `zoompan` FFmpeg diproyeksikan jauh lebih cepat dibanding MoviePy (klaim awal "<0,3 detik" dicatat sebagai dugaan dan akan diuji secara empiris dengan benchmarking komparatif pada Tahap 5).

### 4.2 Subtitle ASS via `libass` & Subtitle Tanpa Burn
- Subtitle di-format ke file ASS (*Advanced SubStation Alpha*) per bab.
- Format ASS memungkinkan styling kaya (font, margin, outline, background box) dan di-burn ke video bab menggunakan filter native FFmpeg: `-vf "ass='path/to/subtitles.ass'"`
- **Subtitle Tanpa Burn (*Clean Master*)**: Selain di-burn ke MP4, sistem wajib menyimpan file subtitle utuh (`full_timeline.srt` dan `full_timeline.ass`) di folder `export/subtitles/`. File ini digunakan untuk import track subtitle di CapCut dan upload manual caption YouTube.

### 4.3 Audio Ducking & Normalisasi Loudness
- **Audio Ducking**: Menggunakan filter FFmpeg `sidechaincompress` pada trek BGM yang dikendalikan oleh trek narasi suara. Saat ada suara narasi, BGM diturunkan otomatis sebesar -16 dB. Saat jeda antar-kalimat, BGM naik kembali secara halus (*smooth release*).
- **Normalisasi Loudness**: Target standar siaran YouTube adalah **-14 LUFS** dengan true peak max -1 dBTP, dieksekusi via filter FFmpeg `loudnorm=I=-14:TP=-1:LRA=11`.

### 4.4 Konsistensi Mutlak Encoder Antar-Bab (*Zero-Glitch Concat*)
Salah satu risiko utama concat stream copy (`ffmpeg -f concat -c copy`) adalah diskontinuitas bila bab-bab video memiliki konfigurasi encoder yang berbeda.
- **Aturan Konsistensi Encoder**:
  1. Encoder dipilih **SEKALI** saat proyek dibuat (misal `h264_nvenc` jika hardware acceleration aktif, atau `libx264` default).
  2. Parameter berikut **DIKUNCI IDENTIK** di seluruh bab:
     - Video codec (`h264`), profile (`high`), level (`4.2`), pixel format (`yuv420p`), resolusi (`1920x1080`), fps (`30.0`), timebase (`1/15360` atau standar FFmpeg).
     - Audio codec (`aac`), sample rate (`44100`), channels (`2 stereo`).
  3. **Penanganan Fallback**: Bila hardware encoder gagal di tengah pengerjaan (misal crash driver GPU di Bab 3), proyek **BERHENTI dengan pesan jelas** atau merender ulang **SEMUA** bab menggunakan software encoder fallback (`libx264`). Sistem **DILARANG KERAS mencampur** bab NVENC dengan bab libx264 dalam satu proyek.
  4. **Validasi Pra-Concat**: Sebelum perintah concat dijalankan, sistem menjalankan inspeksi `ffprobe` otomatis ke seluruh file `chapter_*.mp4` untuk memvalidasi kesamaan codec, profile, dimensi, dan audio sample rate. Jika ada ketidakcocokan, proses dibatalkan sebelum concat menghasilkan file korup.

---

## 5. Strategi Sintesis Suara (TTS): Evaluasi Opsi & Keputusan

Untuk menghasilkan 1.500–3.000 kata narasi, dievaluasi dua pendekatan arsitektur:

| Kriteria Evaluasi | Opsi 1: TTS Per Kalimat / Segmen Tunggal | Opsi 2: TTS Per Paragraf / Kelompok (3–6 Kalimat) |
|:---|:---|:---|
| **Mekanisme** | 1 request TTS untuk setiap kalimat segmen (~10–25 kata). | 1 request TTS untuk 1 paragraf, lalu file audio dipotong per segmen menggunakan word boundary / cue timing. |
| **Frekuensi Request** | Sangat tinggi (150–250 request per video 15 menit). | Sedang (25–40 request per video 15 menit). |
| **Risiko Rate Limit** | Tinggi pada provider berbayar / kuota per menit. | Rendah / aman dari rate limiting. |
| **Kontinuitas Intonasi** | Intonasi cenderung datar/terputus antar-kalimat karena TTS tidak membaca konteks kalimat berikutnya. | **Sangat Baik**: Intonasi, jeda napas, dan prosodi alami antarkalimat terjaga utuh. |
| **Granularitas Cache & Retry**| Sangat presisi (edit 1 kalimat hanya render 1 kalimat). | Presisi tingkat paragraf (edit 1 kalimat render 1 paragraf). |
| **Risiko Desinkronisasi** | Nol risiko potong (audio sudah terisolasi). | Butuh parser pemotong audio berbasis word-cue timestamp. |

### Keputusan Arsitektur TTS
Dipilih **Opsi 2 (TTS Per Paragraf / Kelompok Kalimat)** sebagai strategi utama, dengan **Fallback Otomatis ke Opsi 1**:
- *Alasan*: Untuk video panjang edukasi/narasi YouTube, kelancaran intonasi dan prosodi bicara antar-kalimat adalah faktor terpenting bagi retensi audiens. Opsi 2 juga memangkas jumlah request ke penyedia TTS hingga 80%.
- *Fallback Mechanism*: Bila proses pemotongan segmen Opsi 2 menemui kegagalan alignment cue (misalnya cue tidak tersedia dari provider tertentu), sistem secara otomatis beralih ke Opsi 1 untuk segmen tersebut.

---

## 6. Strategi Cache & Smart Resume

### 6.1 Formula Hash Cache yang Presisi
Setiap komponen diverifikasi menggunakan SHA-256 berbasis konten, bukan sekadar path:
1. **Audio Segment Hash**:
   $$\text{Hash}_{\text{audio}} = \text{sha256}(\text{normalized\_text} + \text{tts\_provider} + \text{tts\_model} + \text{voice\_name} + \text{voice\_rate} + \text{text\_norm\_version})$$
2. **Visual Segment Hash**:
   $$\text{Hash}_{\text{visual}} = \text{sha256}(\text{source\_file\_content\_hash} + \text{motion\_type} + \text{duration} + \text{target\_w} + \text{target\_h} + \text{target\_fps})$$
   *Catatan*: Menggunakan hash isi file sumber gambar/video (`source_file_content_hash`), bukan path, sehingga penggantian file dengan nama yang sama tetap terdeteksi.
3. **Chapter Render Hash**:
   $$\text{Hash}_{\text{chapter}} = \text{sha256}(\sum \text{segment\_hashes} + \text{encoder\_settings} + \text{subtitle\_style\_hash} + \text{preset\_id} + \text{preset\_version} + \text{pipeline\_version})$$
   *Catatan*: Variabel `pipeline_version` (misal `"1.0.0"`) disematkan agar jika di masa depan terdapat perbaikan logika render pada kode aplikasi, cache lama dapat dibatalkan secara sengaja.

### 6.2 Alur Smart Resume
- Saat eksekusi dijalankan ulang setelah crash:
  1. Baca `project_manifest.json`.
  2. Untuk setiap bab, cek apakah file fisik `chapters/chapter_XX.mp4` ada dan valid.
  3. Hitung ulang `chapter_hash`. Jika hash cocok $\rightarrow$ **LEWATI (SKIP)** bab tersebut.
  4. Bila pengguna mengedit naskah Bab 3, hanya Bab 3 yang dirender ulang. Bab 1, 2, 4, 5, 6 tetap utuh, dan proses langsung meloncat ke tahap Concat Final (memakan waktu beberapa detik via `-c copy`).

---

## 7. Abstraksi Sumber Gambar AI (*Image Provider Interface*)

Upstream saat ini hanya memiliki dua provider gambar AI: `openai_image` (DALL-E / OpenAI-compatible API) dan `codex_image` (ChatGPT subscription).
Untuk mempersiapkan penambahan provider gambar AI modern pada Tahap 4 (Flux, Midjourney API proxy, Stable Diffusion / ComfyUI lokal), dirancang antarmuka modular (*interface*) tanpa mengubah kode upstream:

```python
# Usulan antarmuka modular (diimplementasikan di Tahap 4):
class BaseImageProvider(ABC):
    @abstractmethod
    def generate(
        self,
        prompt: str,
        size: str = "1920x1080",
        seed: int | None = None,
        timeout_seconds: float = 60.0
    ) -> Path:
        """Menghasilkan gambar dari prompt teks dan mengembalikan path file lokal."""
        pass
```

---

## 8. Pendekatan Export Project CapCut (Desktop)

Selain menghasilkan MP4 final, sistem mengekspor folder proyek CapCut Desktop yang dapat langsung dibuka dan diedit oleh pengguna.

### 8.1 Status Verifikasi vs Dugaan/Hipotesis CapCut Desktop

| Komponen / Klaim | Status | Detail Teknis & Mitigasi |
|:---|:---:|:---|
| **Target Versi CapCut Pengguna** | **TERCATAT** | Versi CapCut Desktop Windows pengguna saat ini adalah **8.9.0**. |
| **Lokasi Folder Draft Windows** | **[DUGAAN, diverifikasi di Tahap 6]** | `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\<project_name>\`. Perlu diverifikasi pada instalasi lokal versi 8.9.0 di Tahap 6. |
| **Skema JSON Track & Material (`draft_content.json`)** | **[DUGAAN]** | Format JSON multi-track (video, audio, text subtitle) berbasis skema CapCut v3/v4. Pada versi 8.9.0, skema field mungkin telah mengalami perubahan minor atau penambahan metadata wajib. |
| **Enkripsi Draft di Versi Baru** | **[DUGAAN / RISIKO]** | Pada versi baru (termasuk potensi versi 8.x), CapCut mungkin menerapkan proteksi atau validasi signature internal pada draft baru. Diuji secara langsung di Tahap 6. |
| **Import Alternatif (FCPXML/EDL)** | **[DUGAAN]** | Kemampuan CapCut Desktop mengimpor langsung format FCPXML/EDL belum diverifikasi untuk versi 8.9.0 (umumnya FCPXML didukung oleh NLE profesional seperti Premiere/DaVinci). Status dicatat sebagai dugaan. |

---

## 9. Keputusan Desain yang Telah Disepakati

Berdasarkan tinjauan dan arahan pengguna pada Evaluasi Tahap 0:
1. **Gaya Subtitle**:
   - Diatur per preset.
   - Default untuk long video = **kalimat/frasa penuh (maksimal 2 baris)** di bagian bawah layar demi kenyamanan membaca konten edukasi/dokumenter.
   - Mode per-kata (*word-by-word highlight*) tetap tersedia sebagai opsi konfigurasi di dalam preset.
2. **Preset Ken Burns (Animasi Gambar)**:
   - Default bergantian secara otomatis (*alternating*: zoom-in $\rightarrow$ pan-left $\rightarrow$ zoom-out $\rightarrow$ pan-right).
   - Disediakan field opsional pada level segmen naskah bila pengguna ingin mengunci tipe animasi tertentu (`zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `static`).
3. **Batas Durasi Proyek**:
   - Diatur via `config.toml` dengan default: `longform_min_seconds = 600` (10 menit) dan `longform_max_seconds = 1200` (20 menit).
   - Nilai durasi melebihi 1.200 detik diperbolehkan dengan menampilkan pesan peringatan (*warning*), bukan pembatasan keras (*hard block*).
4. **Target Versi CapCut Desktop**:
   - Ditargetkan untuk **CapCut Desktop 8.9.0 (Windows)**, dengan verifikasi empiris struktur draft yang akan dilakukan pada Tahap 6.
