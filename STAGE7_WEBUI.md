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
