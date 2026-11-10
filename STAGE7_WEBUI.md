# STAGE 7 WEBUI — Catatan & Diagnosa

## Diagnosa Tahap 7A.4 (G1)

### 1. Fakta Status Disk & Proyek Pengguna
- **Pemeriksaan Direktori `storage/presets/`**:
  ```powershell
  PS F:\MPTLong> Get-ChildItem -Path storage/presets
  # (Tidak ada berkas yang keluar / direktori kosong)
  ```
  File preset kustom **tidak tertulis ke disk**. Folder `storage/presets/` kosong.

- **Manifest Proyek Kuliner Pengguna (`project show history_of_street_food_in_japan`)**:
  ```text
  Project Overview: history_of_street_food_in_japan
  =================================================================
    Title:          History of street food in Japan
    Topic:          "History of street food in Japan"
    Status:         pending
    Preset:         umum
    Preset Chain:   documentary_id -> umum
    Sub-Niche Focus:"Ramen, yatai, Street Food"
    Aspect Ratio:   16:9
    Pipeline Ver:   1.0.0
    Schema Ver:     1.0.0
    Est. Duration:  0.0s (0.0 min)

    Alur Langkah & Status (5 Tahap):
      1. Naskah:  [○ BELUM] Belum dibuat
      2. Suara:   [○ BELUM] Belum ada naskah
      3. Visual:  [○ BELUM] Belum dialokasikan
      4. Render:  [○ BELUM] Belum dirender
      5. CapCut:  [○ BELUM] Belum diekspor

    Langkah Berikutnya: Berikutnya: Naskah
  ```
  Pada manifest, `preset_id` tercatat sebagai `"umum"`, dan teks yang dimasukkan pengguna tersimpan di `sub_niche_focus: "Ramen, yatai, Street Food"`.

- **Pemeriksaan `cli_longform.py presets list`**:
  ```text
  Available Presets (8):
  -------------------------------------------------------------------------------------
  automotive (niche, en-US, 140 WPM) - Automotive Culture & Engineering
    ├── automotive.classic_restoration (sub_niche, en-US, 135 WPM) - Classic Car Restoration & Preservation
    ├── automotive.jdm (sub_niche, en-US, 145 WPM) - Japanese Domestic Market (JDM) Icons
    ├── automotive.muscle_car (sub_niche, en-US, 145 WPM) - American Muscle Cars
    └── automotive.supercar (sub_niche, en-US, 140 WPM) - Modern Supercars & Hypercars
  documentary_en (niche, en-US, 140 WPM) - General Documentary (English)
  documentary_id (niche, id-ID, 130 WPM) - Dokumenter Umum (Bahasa Indonesia)
    └── umum (niche, id-ID, 130 WPM) - Umum (General)
  -------------------------------------------------------------------------------------
  ```
  Hanya 8 preset bawaan yang terdaftar. Tidak ada preset kustom `custom.kuliner`.

- **Pemeriksaan `git status`**:
  ```text
  On branch stage/7-webui
  nothing to commit, working tree clean
  ```

---

### 2. Akar Penyebab (*Root Cause*)
1. **Perilaku `st.form` di Streamlit**:
   - Form pembuatan proyek dibungkus oleh `with st.form("form_create_project"):`.
   - Di Streamlit, komponen di dalam `st.form` **tidak memicu rerun** ketika nilai input berubah.
   - Pengecekan `is_new_root = chosen_root not in all_p` dievaluasi saat render awal form (saat `chosen_root = "umum"`, `is_new_root = False`).
   - Akibatnya, blok penampung kata kunci kustom (`if is_new_root or is_new_sub:`) **tidak pernah muncul di layar pengguna**.
2. **Kelemahan `accept_new_options=True` di dalam `st.form`**:
   - `st.selectbox(..., accept_new_options=True)` di Streamlit membutuhkan interaksi pemilihan dinamis di frontend. Ketika berada di dalam form, jika opsi baru tidak terdaftar secara resmi sebelum submit, nilainya kembali (*fallback*) ke opsi awal (`"umum"`).
   - Saat tombol submit diklik, `chosen_root` tetap bernilai `"umum"`, sehingga `is_new_root = False` dan `is_new_sub = False`.
   - Logika pembuatan preset `save_custom_preset` terlewati sepenuhnya, dan proyek langsung dibuat dengan `preset_id = "umum"`.
3. **Mengapa Tes Otomatis 7A.3 Lulus?**:
   - Pada Tahap 7A.3, `test_d8_custom_preset_save_and_retrieve` memanggil fungsi backend `save_custom_preset()` secara langsung di Python tanpa melalui form UI Streamlit.
   - Pada `test_webui_smoke.py`, tes hanya mengisi judul proyek dan memilih opsi yang sudah ada, tanpa menguji alur pengetikan niche baru di dalam form.

---

### 3. Solusi Perbaikan (G2, G3)
1. **Pemisahan dari `st.form` atau Opsi Eksplisit `"+ Niche baru…"`**:
   - Niche dropdown menyertakan opsi eksplisit `"+ Niche baru…"`.
   - Sub-niche dropdown menyertakan opsi `"(Gunakan Niche Utama)"`, sub-niche yang cocok, dan `"+ Sub-niche baru…"`.
   - Form pembuatan proyek menggunakan `st.container(border=True)` interaktif agar saat pengguna memilih `"+ Niche baru…"`, kolom isian nama niche, sub-niche, kata kunci footage, dan filter negatif langsung muncul secara responsif.
2. **Pembuatan Niche + Sub-Niche Sekaligus (G2)**:
   - Jika pengguna membuat niche baru `"Kuliner"` dengan sub-niche `"Street food Jepang"`, sistem otomatis membuat:
     - `storage/presets/custom.kuliner.json` (extends `umum`)
     - `storage/presets/custom.kuliner.street_food_jepang.json` (extends `custom.kuliner`)
     - Kata kunci footage dan filter negatif disimpan di berkas sub-niche.
     - Proyek langsung dikaitkan ke preset sub-niche tersebut, menghasilkan rantai: `documentary_id -> umum -> custom.kuliner -> custom.kuliner.street_food_jepang`.
3. **Penyaringan Dropdown Berdasarkan Bahasa (G3)**:
   - Jika bahasa narasi `id-ID`, sembunyikan `documentary_en` dan preset spesifik non-Indonesia.
   - Sembunyikan `documentary_id` dari dropdown utama (cukup tampil `"Umum"`).
   - Label dropdown menggunakan bahasa Indonesia yang ramah pengguna.
   - Niche kustom diberi label `(buatan sendiri)`.
4. **Pemulihan Proyek Kuliner Lama**:
   - Membuat file preset `custom.kuliner.json` dan `custom.kuliner.street_food_jepang.json` berdasarkan data dari `history_of_street_food_in_japan`.
   - Menghubungkan kembali manifest proyek `history_of_street_food_in_japan` ke preset tersebut.

---

## Diagnosa & Pemulihan Tahap 7A.5 (H1-H4)

### 1. Investigasi Proyek Tokyo (H2)
- **Pertanyaan Diagnostik**: Apakah `history_of_street_food_in_tokyo` diubah oleh skrip pemulihan agen pada 7A.4 atau dipilih pengguna saat pembuatan?
- **Bukti Verbatim dari Jejak Transkrip 7A.4**:
  - *Sebelum pemulihan 7A.4 (Step 8440)*: Manifest asli proyek `history_of_street_food_in_tokyo` diinspeksi:
    ```text
    "project_id": "history_of_street_food_in_tokyo",
    "title": "History of street food in Tokyo",
    "preset_id": "umum",
    "preset_inheritance_chain": ["documentary_id", "umum"],
    "sub_niche_focus": null
    ```
    Terbukti bahwa pengguna membuat proyek ini pada 7A.3 Langkah 1 hanya dengan topik (niche bawaan `"umum"`), tanpa memilih Kuliner atau sub-niche apapun.
  - *Saat eksekusi pemulihan 7A.4 (Step 8492)*: Agen menjalankan skrip pemulihan darurat yang mencakup baris:
    `for pid in ['history_of_street_food_in_japan', 'history_of_street_food_in_tokyo']:`
    yang memaksa kedua proyek menggunakan `custom.kuliner.street_food_jepang`.
  - **Kesimpulan**: Perubahan preset pada proyek Tokyo murni kesalahan skrip pemulihan agen di 7A.4, bukan aksi pengguna.
- **Tindakan Pemulihan (H2)**:
  - Preset proyek `history_of_street_food_in_tokyo` dikembalikan ke `umum.id` (chain: `documentary_id -> umum.id`).
  - Skrip pemulihan kini memiliki guard ketat (`migrate_umum_presets_and_projects()`): hanya menyentuh preset/proyek yang benar-benar membutuhkan pembaruan, dan setiap perubahan dicatat secara transparan dengan format `[TINDAKAN] Proyek/Preset: nilai_lama -> nilai_baru`.

---

### 2. Pemisahan Preset Umum per Bahasa (H1)
- **Akar Masalah**:
  - Pada 7A.3-7A.4, terdapat berkas preset disk tunggal `resource/presets/umum.json` yang mewarisi `documentary_id` (`id-ID`, ArdiNeural, 130 WPM).
  - Ketika proyek bahasa Inggris (seperti `street_food_in_tokyo_english`) dibuat, kode runtime memodifikasi snapshot proyek, namun perbandingan disk diff (`get_impactful_preset_diffs`) dan `refresh_project_preset` membandingkan proyek dengan `umum.json` di disk, sehingga memunculkan peringatan diff WPM (140 vs 130) dan Voice (ChristopherNeural vs ArdiNeural).
- **Solusi Bersih H1**:
  - Berkas disk tunggal `umum.json` dihapus.
  - Dibuat dua preset disk konkret:
    1. `resource/presets/umum.id.json`:
       - `extends`: `"documentary_id"`
       - `language`: `"id-ID"`
       - `words_per_minute`: 130
       - `default_voice`: `"id-ID-ArdiNeural"`
       - `capcut_text_size`: 8
    2. `resource/presets/umum.en.json`:
       - `extends`: `"documentary_en"`
       - `language`: `"en-US"`
       - `words_per_minute`: 140
       - `default_voice`: `"en-US-ChristopherNeural"`
       - `capcut_text_size`: 8
  - **UI Abstraction**: UI tetap menampilkan SATU label `"Umum (Dokumenter Naratif)"`. Berdasarkan bahasa narasi proyek yang dipilih (`id-ID` atau `en-US`), sistem secara transparan memetakan ke `umum.id` atau `umum.en`.
  - **Hierarki Bersih**:
    - `documentary_en -> umum.en`
    - `documentary_id -> umum.id -> custom.*`
  - **Guard Perbedaan Bahasa**:
    - `refresh_project_preset` menolak pembaruan jika keluarga bahasa proyek (`en` vs `id`) berbeda dari preset target dengan melempar `PresetValidationError`.
    - Di WebUI, tombol "Pakai Pengaturan Terbaru" disembunyikan jika terdapat perbedaan bahasa, menampilkan pesan bahwa perubahan bahasa harus dilakukan secara eksplisit terpisah.

---

### 3. Panah Navigasi Sidebar Halaman Utama (H3)
- **Investigasi DOM Streamlit 1.66.1**:
  - `webui/styles.css` upstream menyembunyikan header dan toolbar dengan aturan:
    `header[data-testid="stHeader"], div[data-testid="stToolbar"] { display: none !important; }`
  - Di Streamlit, tombol panah sidebar (`[data-testid="stExpandSidebarButton"]`) berada di dalam `header[data-testid="stHeader"]`.
- **Solusi Tanpa Mengubah `webui/styles.css`**:
  - Melalui `render_longform_entry()` di modul longform (`app/services/longform/ui/nav.py`), disuntikkan CSS scoped yang:
    - Menampilkan container header (`display: block !important`), namun berlatar belakang transparan dan `pointer-events: none !important` agar tombol toolbar yang tidak diinginkan tidak dapat diklik.
    - Menampilkan tombol toggle sidebar (`[data-testid="stExpandSidebarButton"]` dan `[data-testid="stSidebarCollapseButton"]`) dengan `display: inline-flex !important`, `pointer-events: auto !important`, dan `z-index: 999999 !important`.
    - Mengisi sidebar dengan tautan navigasi studio ke halaman Utama dan halaman Long Video.
  - File `webui/Main.py` tetap mempertahankan isolasi (hanya 6 baris pemanggilan hook try/except).
