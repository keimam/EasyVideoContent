# FAQ & Troubleshooting

### 1. Why is *"Re-check with AI"* or *"Check relevance with AI"* disabled?
Your active LLM does not support image (*vision*) input (for example, several free text-only models such as `openrouter/free`). Click **Open Settings** below the control and pick a vision-capable model (such as Gemini Flash or GPT-4o mini).

### 2. Why did `#Chapter 1:` or `#Bab 1:` appear inside my narration text?
Older imports created before heading-guard hardening could capture glued markdown headings. Open the **Script** step — if glued headings are detected, a banner with **Clean Chapter Headings from Narration** lets you repair them automatically (or run `uv run python cli_longform.py project fix-headings <project_id>`).

### 3. Why does a gallery thumbnail look outdated or show *"Needs rebuild"*?
The shot asset changed (for example via *Update keywords with AI* or *Search again*), while the rendered shot clip has not been rebuilt yet. Filter with **Needs update · N** and run **Build Clips** or **Fetch & Build**. Both gallery thumbnails and Detail previews read from `rendered_clip` using content-hash-keyed cache files.

### 4. Why doesn't partial progress start at 99% anymore?
As of version **1.0.0**, partial runs in Visual, Voice, and Render track only the scheduled target units (`0/N` = `0%`) and report valid/locked skipped items (`skipped: X (already valid/locked)`). Completion reaches `100%` only once the job exits cleanly.

### 5. What should I do if CapCut export refuses because CapCut is open?
Exit **CapCut Desktop** completely (including background tray/processes `CapCut.exe`), then run **Export & Install to CapCut** again.

### 6. `ffmpeg` or `ffprobe` not found
Install FFmpeg and add it to your Windows `PATH`, or configure the explicit path to `ffmpeg.exe` in `config.toml` / Settings. Run `uv run python cli_longform.py doctor` to verify. (When installed via the Windows Setup installer, FFmpeg is already bundled automatically inside the installation directory.)

### 7. Windows Defender SmartScreen shows *"Windows protected your PC"* when opening the installer
The `EasyFacelessStudio-Setup-1.0.0.exe` installer is currently **not digitally signed with a commercial Authenticode certificate** (*unsigned*), so Windows SmartScreen may display a warning prompt on newly downloaded builds. If you obtained the installer from the official project release:
1. Click **More info**.
2. Click **Run anyway**.
Setup installs per-user into `%LOCALAPPDATA%\Programs\EasyFaceless Studio` without requiring administrator privileges.

### 8. How to download large installers from Google Drive & verify SHA-256
1. **Google Drive virus-scan warning page:** Because the installer is ~570 MB, Google Drive displays *"Google Drive can't scan this file for viruses"*. This is normal for large executable files — click **Download anyway** to start the download in your browser.
2. **Verify file integrity (SHA-256):** Open PowerShell in your Downloads folder and run:
   ```powershell
   Get-FileHash .\EasyFacelessStudio-Setup-1.0.0.exe -Algorithm SHA256
   ```
   Compare the printed `Hash` against the `.sha256` file or release notes before installing.
3. **App updates:** Clicking **Download Update** in the update banner or **About** dialog always opens the official release link in your default browser rather than downloading in the background.
