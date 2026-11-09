# Log Perubahan File Upstream (Rule A1)

Dokumen ini mencatat setiap perubahan atau penambahan hook pada file asli upstream (`harry0703/MoneyPrinterTurbo`).
Tujuannya adalah menjaga kemudahan sinkronisasi (merge/rebase) dari repositori upstream di masa mendatang.

---

## Ringkasan Modifikasi Berdasarkan Tahap

| Tahap | File Upstream Diubah | Jumlah Baris | Deskripsi Singkat |
|---|---|---|---|
| **Tahap 0** | Tidak Ada | 0 baris | Audit, baseline benchmark, proposal desain |
| **Tahap 1** | `.gitignore` | 1 baris | Menambahkan `!test/longform/**` agar berkas tes unit longform dilacak git (sejalan dengan `!test/services/test_*.py` upstream) |
| **Tahap 2** | Tidak Ada | 0 baris | Script parsing & chunking multi-bab |
| **Tahap 3** | Tidak Ada | 0 baris | Audio generation, alignment, subtitle ASS/SRT |
| **Tahap 4** | Tidak Ada | 0 baris | Visual assets fetch, candidate scoring, contact sheet, review & replace |
| **Tahap 5 & 5.1** | Tidak Ada | 0 baris | Render engine bab, subtitle burner, BGM ducking, concat final, font preflight |
| **Tahap 6A** | Tidak Ada | 0 baris | Analisis template CapCut 8.9.0, doctor CLI, builder draft portable, smoke test |

---

## Rincian Perubahan per Berkas

### Tahap 1: Fondasi Model Proyek, Preset, Manifest, & Resume
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Hook pada pipa data upstream:** **Nihil**
- **Konfigurasi Git Upstream:**
  - `.gitignore` (baris 38): Menambahkan `!test/longform/**` agar direktori pengujian unit longform baru tidak terblokir oleh aturan `test_*.py` bawaan.
- Seluruh fungsionalitas baru Tahap 1 diisolasi penuh di direktori mandiri:
  - `app/services/longform/` (`__init__.py`, `config.py`, `schema.py`, `hashing.py`, `presets.py`, `store.py`, `planner.py`, `script_import.py`)
  - `cli_longform.py` (CLI mandiri di root direktori)
  - `resource/presets/` (definisi preset bawaan)
  - `docs/longform/` (arsip dokumen dan proposal)
  - `test/longform/` (berkas pengujian unit terisolasi)

### Tahap 2 s/d Tahap 4: Audio, Visual, Relevansi, & CLI Review
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- Seluruh fungsionalitas diisolasi di modul `app/services/longform/` (`audio/`, `visual/`) dan `test/longform/`.

### Tahap 5 & 5.1: Render Bab, Subtitle Burner, BGM Ducking, Concat Final, & Font Preflight
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Aset Font dan Lisensi (R2):**
  - Font default subtitle: `resource/fonts/BeVietnamPro-Bold.ttf`.
  - Sumber: Google Fonts / YellowHaze (Fábio Duarte Martins).
  - Lisensi: **SIL Open Font License 1.1 (OFL-1.1)**, open source bebas royalti untuk penggunaan komersial maupun personal.
  - Font proprietary eksternal seperti "Impact" (Microsoft proprietary) telah dihapus dari seluruh berkas preset (`resource/presets/*.json`).
  - Fitur *Font Preflight Check* ditambahkan di `app/services/longform/render/service.py` dan `cli_longform.py` (`render status`) untuk memperingatkan pengguna secara eksplisit jika font preset tidak ditemukan di direktori `resource/fonts/` atau sistem.
- **Modul Render Longform:**
  - `app/services/longform/render/` (`chapter_render.py`, `final_concat.py`, `subtitle.py`, `bgm.py`, `service.py`)
  - `test/longform/test_stage5_render.py`

### Tahap 6A: Integrasi CapCut Desktop 8.9.0 & Draft Smoke Builder
- **File Kode Upstream yang Disentuh:** **Nihil (0 berkas kode)**
- **Modul CapCut Baru:**
  - `app/services/longform/capcut/` (`__init__.py`, `detector.py`, `builder.py`, `service.py`)
  - `cli_longform.py` (penambahan subperintah `capcut doctor` dan `capcut smoke`)
  - `docs/longform/capcut_template_8.9.0/` (salinan ter-sanitasi dari template acuan pengguna)
  - `docs/longform/CAPCUT_FORMAT_8.9.0.md` (spesifikasi format terverifikasi CapCut Desktop 8.9.0)
  - `test/longform/test_capcut.py` (pengujian mandiri builder, detector, proses aktif, dan integritas draft pengguna)
