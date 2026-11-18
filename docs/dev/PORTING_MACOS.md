# Rencana & Daftar Periksa Porting macOS (Stage 8C.2, D6)

Dokumen ini menjadi panduan resmi untuk mem-porting **EasyFaceless Studio** ke macOS (Apple Silicon `arm64` dan Intel `x86_64`) setelah rilis Windows 8C.2 stabil.

---

## 1. Abstraksi OS yang Sudah Dipusatkan

Seluruh logika khusus sistem operasi di modul kita telah dipisahkan ke [`app/services/longform/platform_utils.py`](../../app/services/longform/platform_utils.py) dan penentu jalur data ke [`app/services/longform/app_paths.py`](../../app/services/longform/app_paths.py):

| Fungsi di `platform_utils.py` | Implementasi Windows (Aktif) | Status / Catatan macOS (`darwin`) |
|---|---|---|
| `configure_console_utf8()` | `reconfigure(encoding="utf-8")` | Tidak perlu di POSIX |
| `get_hidden_subprocess_flags()` | `CREATE_NO_WINDOW \| CREATE_NEW_PROCESS_GROUP` | `0` (`start_new_session=True` saat Popen) |
| `is_pid_alive(pid)` | Win32 `OpenProcess` + `GetExitCodeProcess` | `os.kill(pid, 0)` |
| `terminate_process_tree(pid)` | `taskkill /PID /T /F` | `os.killpg` + fallback `os.kill` (`SIGTERM`/`SIGKILL`) |
| `get_user_data_dir(app_name)` | `%LOCALAPPDATA%\EasyFaceless Studio` | `~/Library/Application Support/EasyFaceless Studio` |
| `get_user_programs_dir(app_name)` | `%LOCALAPPDATA%\Programs\EasyFaceless Studio` | `/Applications/EasyFaceless Studio.app` |
| `is_capcut_running()` | `tasklist /FI "IMAGENAME eq CapCut.exe"` | `pgrep -f "CapCut.app"` — perlu uji nyata di macOS |
| `get_capcut_product_info_path()` | `%LOCALAPPDATA%\CapCut\Apps\ProductInfo.xml` | `/Applications/CapCut.app/Contents/Info.plist` — perlu verifikasi skema versi CapCut Mac |
| `get_capcut_drafts_dir()` | `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft` | `~/Movies/CapCut/User Data/Projects/com.lveditor.draft` — **wajib verifikasi di Mac** |
| `move_dir_to_trash_reliable()` | Retry backoff + read-only cleanup + copy-fallback | `shutil.move` + fallback copy-then-remove |
| `open_path_in_os(path)` | `os.startfile` / `explorer.exe /select,` | `open` / `open -R` |
| `open_url_in_browser(url)` | `webbrowser.open` / fallback `os.startfile` | `webbrowser.open` / fallback `open` |
| `launch_desktop_app_detached()` | `subprocess.Popen` headless Windows | `subprocess.Popen(..., start_new_session=True)` |

---

## 2. Hal yang Wajib Diverifikasi di Perangkat macOS Fisik

1. **Path & Format Draft CapCut Desktop versi macOS:**
   - Pastikan lokasi folder proyek CapCut Mac: apakah berada di `~/Movies/CapCut/User Data/Projects/com.lveditor.draft` atau di dalam App Sandbox container (`~/Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/...` pada versi Mac App Store vs versi unduhan langsung capcut.com).
   - Verifikasi parsing versi CapCut Mac dari `/Applications/CapCut.app/Contents/Info.plist` (`CFBundleShortVersionString` via `plistlib`), bukan `ProductInfo.xml` XML Windows.
   - Uji kompatibilitas path material media (`/` POSIX path dan perizinan Full Disk Access / TCC saat CapCut Mac membuka draft ekspor).
2. **FFmpeg & Codec Hardware macOS:**
   - Paket Windows memakai `ffmpeg.exe` GPLv3 static + `libx264` (serta opsional `h264_nvenc`/`h264_amf`).
   - Di macOS, verifikasi ketersediaan binary FFmpeg universal (`arm64` + `x86_64`) tanpa dependensi Homebrew eksternal, serta tambahkan/uji dukungan encoder hardware **`h264_videotoolbox`** di `app/services/longform/render/chapter_render.py` (`chapter_render.py` baru mendeteksi `nvenc`/`amf`, sedangkan upstream `video.py` sudah mendukung `h264_videotoolbox`).
3. **Edge TTS, Pillow, & Font Sistem / Bundled Font:**
   - Pastikan font `PlusJakartaSans[wght].ttf` dan font subtitle FFmpeg (`libass`) terdeteksi dengan benar oleh Fontconfig macOS.

---

## 3. Arsitektur Build (`arm64` / `x86_64`) & App Bundle `.app`

1. **Bundle struktur `.app`:**
   - `EasyFaceless Studio.app/Contents/MacOS/EasyFaceless Studio` (launcher shell/Swift/Mach-O yang memicu `Contents/Resources/runtime/python/bin/python3 -m app.services.longform.launcher_runtime` dengan `EFS_INSTALLED=1`).
   - `Contents/Resources/app/`, `webui/`, `resource/`, `licenses/`, `runtime/python/`, `runtime/ffmpeg/bin/ffmpeg`.
   - `Info.plist` dengan `CFBundleIdentifier`, `CFBundleIconFile` (konversi `logo.png` -> `app.icns` via `iconutil`), `LSUIElement` bila diinginkan tanpa dock Python tambahan.
2. **Dukungan Dual-Architecture (`arm64` & `x86_64`):**
   - Pilihan termudah & paling aman ukuran: dua build terpisah (`-arm64.dmg` untuk Apple Silicon M1–M4 dan `-x86_64.dmg` untuk Intel Mac), karena wheel C-extension Python (`numpy`, `av`, `pillow`, `torch` jika ada) ukurannya mengganda bila digabung menjadi thin/universal2 `lipo`.
   - Bila ingin `.dmg` universal2, gunakan wheel `macosx_11_0_universal2` atau gabungkan dylib `.so` lewat `lipo -create` + verifikasi seluruh wheel di `uv.lock`.

---

## 4. Penandatanganan (Apple Developer ID) & Notarisasi

Agar tidak diblokir Gatekeeper (`"EasyFaceless Studio.app" is damaged and can't be opened` / developer tak dikenal):

1. **Keanggotaan Apple Developer Program** (\$99/tahun) → sertifikat **Developer ID Application** (untuk `.app` & semua `.so`/`.dylib`/biner FFmpeg di dalamnya).
2. **Code Signing Rekursif dengan Hardened Runtime & Entitlements:**
   - Python C-extensions dan JIT sering membutuhkan `com.apple.security.cs.allow-unsigned-executable-memory` & `com.apple.security.cs.disable-library-validation`.
   - Tandatangani seluruh binary (`.so`, `.dylib`, `python3`, `ffmpeg`, `ffprobe`, lalu `.app` terluar):
     ```bash
     codesign --force --options runtime --timestamp --entitlements packaging/macos/entitlements.plist --sign "Developer ID Application: Naseh Ngulik (TEAMID)" ...
     ```
3. **Pengemasan `.dmg` & Notarisasi Apple (`notarytool`):**
   ```bash
   xcrun notarytool submit dist/EasyFacelessStudio-1.0.0-arm64.dmg --apple-id ... --team-id ... --wait
   xcrun stapler staple dist/EasyFacelessStudio-1.0.0-arm64.dmg
   ```
   Setelah di-*staple*, pengguna dapat mengunduh & menjalankan secara offline tanpa peringatan Gatekeeper.
