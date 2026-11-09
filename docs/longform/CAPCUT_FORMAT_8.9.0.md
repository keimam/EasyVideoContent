# Analisis Format Draft CapCut Desktop 8.9.0 (Windows)

Dokumen ini mendokumentasikan spesifikasi struktur berkas draft CapCut Desktop versi **8.9.0.3794** (Windows) yang dianalisis langsung dari template pengguna `MPT_TEMPLATE` dan berkas referensi otomasi `TEST_SPIKE_INSTALL_2`.

---

## 1. Lingkungan & Sumber Data

- **Aplikasi:** CapCut Desktop Windows 64-bit
- **Versi Terpasang:** `8.9.0` (Build `8.9.0.3794`, terverifikasi dari `ProductInfo.xml`)
- **Direktori Draft Pengguna:** `C:/Users/<USER>/AppData/Local/CapCut/User Data/Projects/com.lveditor.draft/`
- **Template Acuan:** `MPT_TEMPLATE` (dibuat langsung oleh pengguna di CapCut 8.9.0, memuat 1 video, 1 audio, 1 teks "TEMPLATE")
- **Template Referensi Otomasi:** `TEST_SPIKE_INSTALL_2` (dibuat oleh pipeline otomasi pengguna sebelumnya)
- **Salinan Bersih (Sanitized):** Disimpan di `docs/longform/capcut_template_8.9.0/` (data pribadi pengguna dan path spesifik telah disamarkan).

---

## 2. Struktur Berkas Draft (File Layout)

Sebuah folder draft CapCut (misalnya `MPT_SMOKE_uji_nokey`) terdiri atas 22 berkas utama:

```
com.lveditor.draft/<DRAFT_NAME>/
├── draft_meta_info.json         # Registrasi metadata draft untuk UI CapCut
├── draft_content.json           # Timeline utama (tracks, segments, materials)
├── draft_content.json.bak       # Salinan backup timeline
├── draft_cover.jpg              # Thumbnail cover draft
├── draft_agency_config.json     # Konfigurasi agency (default empty/minimal)
├── draft_biz_config.json        # Konfigurasi bisnis (0 byte / empty)
├── draft_settings               # Format binary kecil untuk settings
├── draft_virtual_store.json     # Store virtual asset
├── key_value.json               # Konfigurasi pasangan key-value internal
├── performance_opt_info.json    # Profiling optimasi
├── timeline_layout.json         # Layout UI timeline (posisi panel)
├── attachment_editing.json      # Konfigurasi edit attachment
├── attachment_pc_common.json    # Attachment PC
├── common_attachment/           # Direktori metadata AI/plugin/script attachment
│   ├── attachment_action_scene.json
│   ├── attachment_gen_ai_info.json
│   ├── attachment_pc_timeline.json
│   ├── attachment_plugin_draft.json
│   └── attachment_script_video.json
├── Timelines/                   # Multi-timeline container
│   ├── project.json             # Pemetaan container timeline ke main_timeline_id
│   ├── project.json.bak
│   └── <TIMELINE_ID>/           # Subdirektori bernama sesuai ID timeline (UUID)
│       ├── draft_content.json   # Salinan identik dari root draft_content.json
│       ├── draft_content.json.bak
│       ├── draft_cover.jpg
│       ├── attachment_editing.json
│       ├── attachment_pc_common.json
│       ├── common_attachment/...
│       └── attachment/patch/
│           ├── mini_draft.json  # Denormalized cache segment (dibuat/diperbarui CapCut)
│           └── patch.json
└── media/                       # Folder lokal portabel untuk aset klip, audio, font
```

---

## 3. Spesifikasi Terverifikasi (Verified Schema)

### A. Format & Satuan Waktu (Time Unit)
- **Status:** **Terverifikasi 100%**.
- **Satuan:** **Mikrodetik (microseconds / $\mu\text{s}$)**. $1\text{ detik} = 1.000.000\ \mu\text{s}$.
- Seluruh field durasi dan rentang waktu (`duration`, `target_timerange.start`, `target_timerange.duration`, `source_timerange.start`, `source_timerange.duration`, `tm_duration`) bertipe **integer dalam mikrodetik**.
- Contoh: Durasi $18,888\text{ s} \rightarrow 18.888.000\ \mu\text{s}$.

### B. Versi Skema
- **Key:** `version`: `360000` (integer)
- **Key:** `new_version`: `"175.0.0"` (string)
- **Key:** `fps`: `30.0` (float)
- **Key:** `canvas_config`: `{"ratio": "original", "width": 1920, "height": 1080, "background": null}`

### C. Struktur Tracks & Segments (`draft_content.json`)
Setiap track dalam `tracks` memiliki `type`: `"video"`, `"audio"`, atau `"text"`, dan memuat array `segments`:
- Segment Video:
  - `id`: UUID unik (uppercase).
  - `material_id`: UUID merujuk ke entri di `materials.videos`.
  - `target_timerange`: `{"start": <offset_mikrodetik>, "duration": <durasi_mikrodetik>}`.
  - `source_timerange`: `{"start": 0, "duration": <durasi_mikrodetik>}`.
  - `extra_material_refs`: Menghubungkan segment ke material bantu (`speeds`, `canvases`, `placeholder_infos`, `sound_channel_mappings`, `material_colors`, `vocal_separations`).
  - `speed`: `1.0`, `volume`: `1.0`.
- Segment Audio:
  - `id`: UUID unik.
  - `material_id`: UUID merujuk ke entri di `materials.audios`.
  - `target_timerange`: `{"start": <offset>, "duration": <durasi>}`.
  - `source_timerange`: `{"start": 0, "duration": <durasi>}`.
  - `extra_material_refs`: Menghubungkan ke `speeds`, `placeholder_infos`, `beats`, `sound_channel_mappings`, `vocal_separations`.
- Segment Teks (Subtitle):
  - `id`: UUID unik.
  - `material_id`: UUID merujuk ke entri di `materials.texts`.
  - `target_timerange`: `{"start": <start_offset>, "duration": <durasi>}`.
  - `source_timerange`: `null`.
  - `extra_material_refs`: Opsional (`[]` kosong pada `TEST_SPIKE_INSTALL_2`, atau ref animasi stiker).

### D. Struktur Material Registry (`draft_content.json -> materials`)
- `materials.videos`:
  - `id`: UUID material.
  - `path`: Path absolut berkas video/gambar dengan forward slashes (`C:/.../media/clip.mp4`).
  - `duration`: Durasi berkas sumber dalam mikrodetik.
  - `width`: 1920, `height`: 1080.
  - `local_material_id`: UUID penghubung ke `draft_meta_info.json`.
  - `type`: `"video"` (atau `"photo"` untuk gambar statis).
- `materials.audios`:
  - `id`: UUID material.
  - `path`: Path absolut berkas audio (`C:/.../media/audio.wav`).
  - `duration`: Durasi berkas audio dalam mikrodetik.
  - `local_material_id`: UUID penghubung ke `draft_meta_info.json`.
  - `type`: `"extract_music"`.
- `materials.texts`:
  - `id`: UUID material.
  - `type`: `"text"`.
  - `content`: String JSON serial (JSON-encoded string) berisi teks dan gayanya:
    ```json
    "content": "{\"text\":\"Teks Subtitle\",\"styles\":[{\"fill\":{\"content\":{\"render_type\":\"solid\",\"solid\":{\"color\":[1,1,1]}}},\"font\":{\"path\":\"C:/.../font.ttf\",\"id\":\"\"},\"size\":15,\"range\":[0,13]}]}"
    ```
  - `font_size`: `15.0` s/d `24.0` (skala CapCut teks timeline).
  - `alignment`: `1` (center).

### E. Metadata Registrasi (`draft_meta_info.json`)
File ini dibaca oleh panel beranda CapCut untuk memuat daftar draft:
- `draft_id`: UUID unik draft.
- `draft_name`: Nama tampilan proyek draft.
- `draft_fold_path`: Path absolut folder draft (`C:/.../MPT_SMOKE_uji_nokey`).
- `draft_root_path`: Path direktori root draft (`C:/.../com.lveditor.draft`).
- `tm_duration`: Total durasi timeline dalam mikrodetik (wajib sama persis dengan `duration` di `draft_content.json`).
- `draft_materials[0]['value']`: Daftar item media yang diimpor, masing-masing dengan:
  - `id`: Menyesuaikan `local_material_id` di `draft_content.json`.
  - `file_Path`: Path absolut berkas media.
  - `duration`: Durasi dalam mikrodetik.
  - `metetype`: `"video"`, `"photo"`, atau `"music"`.
  - `type`: `0`.

### F. Aturan Konsistensi Antar-Berkas (Mandatory Consistency)
1. `Timelines/project.json['main_timeline_id']` == `draft_content.json['id']`.
2. Nama subdirektori `Timelines/<ID>/` == `draft_content.json['id']`.
3. `Timelines/<ID>/draft_content.json` identik byte-for-byte dengan root `draft_content.json`.
4. `draft_meta_info.json['tm_duration']` == `draft_content.json['duration']`.
5. Nilai `local_material_id` pada setiap video/audio di `draft_content.json` harus terdapat di `draft_meta_info.json -> draft_materials[0]['value']`.

---

## 4. Perbandingan Template: `MPT_TEMPLATE` vs `TEST_SPIKE_INSTALL_2`

| Fitur / Parameter | `MPT_TEMPLATE` (CapCut 8.9.0 Native) | `TEST_SPIKE_INSTALL_2` (Automated POC) | Catatan Desain MPT |
|---|---|---|---|
| **Jumlah File** | 22 berkas utama | 22 berkas utama | Struktur berkas 100% konsisten. |
| **Versi Schema** | `version`: 360000, `new_version`: "175.0.0" | `version`: 360000, `new_version`: "175.0.0" | Identik pada CapCut 8.9.0. |
| **Tracks Video** | 1 track video (1 klip MP4) | 1 track video (2 foto PNG) | Builder mendukung video MP4 hasil render Tahap 4. |
| **Tracks Audio** | 1 track audio (`extract_music`) | 2 track audio (VO + BGM) | Builder mendukung pemotongan audio bab yang sinkron. |
| **Tracks Text** | 1 track text (`extra_material_refs` terisi) | 1 track text (`extra_material_refs: []`) | Membuktikan refs animasi stiker opsional. |
| **Folder Media** | Path menunjuk ke `Downloads/` user | Path menunjuk ke `Downloads/.../sample-data` | Builder MPT menyalin seluruh aset ke `<draft>/media/` agar portabel dan terisolasi. |

---

## 5. Dugaan vs Risiko Versi

1. **Bagian yang Terverifikasi Pasti (Empirically Proven):**
   - Penulisan JSON unencrypted, nilai mikrodetik, struktur 3 lapis track (video, text, audio), ID mapping, registrasi `draft_meta_info.json`.
2. **Dugaan (Hypothesized / Tolerant):**
   - Berkas `mini_draft.json` di bawah `Timelines/<ID>/attachment/patch/` merupakan cache denormalized internal CapCut. Jika diisi dengan salinan template yang disesuaikan atau diperbarui oleh CapCut saat dibuka, CapCut membaca `draft_content.json` sebagai sumber kebenaran (source of truth).
3. **Risiko Versi (Version Risk):**
   - CapCut Desktop dapat memperbarui skema pada versi mayor (misalnya 9.x).
   - Oleh karena itu, modul builder menyediakan perintah `capcut doctor` untuk mendeteksi versi CapCut yang terpasang dan menampilkan peringatan jika versi tidak sama dengan `8.9.0`.
