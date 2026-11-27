# FAQ & Troubleshooting

### 1. Why aren't files in **Local files** automatically filling every shot?
**Local files** matches **descriptive keywords from filenames and subfolder names** against each shot's narration and search query — it does not drop random clips everywhere:
- **Filenames must match:** Name files with descriptive English keywords separated by underscores, hyphens, or dots (for example `plague_doctor_street_night.mp4` or subfolder `vikings/ship_storm.jpg`).
- **Random fill is off by default:** **Fill empty shots with random local files** stays disabled so unrelated files are never injected automatically. Enable it only if you explicitly want remaining unmatched shots filled/recycled from your library.
- **How to open the folder & multi-upload:** Expand **Footage sources** on the **Visual** step → click **📂 Open Local files folder**, or select files in **Add media to Local files (multi-file)** and click **Save to Local files** (existing filenames prompt cleanly for *Auto-rename*, *Overwrite*, or *Skip*).

### 2. AI stops with `"daily quota exhausted (resets 07:00 WIB)"` / `"kuota harian habis"`
AI script writing, summaries, keywords, or *Visual Context* paused or failed because your primary AI hit its daily limit (on OpenRouter `:free` models with under \$10 purchased credits, limits are **50 requests/day** and **20 requests/minute**, resetting at `00:00 UTC` = **07:00 WIB**; *"Spend windows are UTC: a daily budget resets at 00:00 UTC"* — [OpenRouter Limits](https://openrouter.ai/docs/api/reference/limits), checked: 2026-11-27).
- **Recommended fix — Configure Backup AI providers:** Open **Settings → General → Backup AI providers (auto-failover on quota/error)**, enter keys for genuinely separate providers (such as OpenRouter + **Google Gemini** or **Groq**), and click **🔍 Test all AI providers**. Studio automatically switches to your backup list whenever the primary provider hits daily caps or returns `401`/`402`/`404`/`429`. See [API & AI Provider Settings](ai-settings.md).

### 3. Gemini `limit: 0` error (model not available on Free Tier)
If an error reports `limit: 0` or says the model is unavailable on the Gemini Free Tier, that model is not included in your Google Cloud project's free quota (or requires billing — see [Gemini API Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits), checked: 2026-11-27).
- **Fix:** Open **Settings** and select a **Flash** model (such as `gemini-3.5-flash-lite` or `gemini-2.5-flash`) available on the free tier.

### 4. Visual source warning meanings (Coverr 403, Openverse timeouts, & **Slow** connection profile)
- **`"Coverr skipped: hourly query quota exhausted. Try again later or use a production key."`** vs **`"Coverr skipped: API key rejected (403). Check your key in AI & API Providers."`**:
  - Coverr's free Demo API key allows **50 calls per hour** (*"50 free calls per hour No credit card required"* — [coverr.co/developers](https://coverr.co/developers), checked: 2026-11-27). **Hourly query quota exhausted** means your key is valid and simply reached its hourly cap (other sources such as Pixabay, Wikimedia, NASA, Openverse, and Internet Archive keep running). Only **API key rejected** indicates an invalid key.
- **Openverse / Wikimedia / NASA paused due to timeouts:** Messages such as `"Openverse paused …× due to slow/failing server"` indicate the source exceeded the default 6-second timeout.
  - **Fix:** Open **Settings → General → Visual source connection profile** and switch to **Slow (15s timeout, pause 60s after 3 failures)** so slower servers are given 15 seconds and shorter cooldowns.

### 5. What does `"Visual finished: X of Y shots. Z shot(s) found no matching visual."` mean?
**Partially done** means **Find & Build Visuals** completed its full run cleanly (it was *not* interrupted mid-job), while `Z` shots with abstract narration found no suitable match across all free sources. Because the job finished, **Proceed to Render** is shown instead of **Resume**.
- **How to fill the remaining missing shots:**
  1. Click any unmatched shot in the **Visual Gallery**.
  2. Use the **Search more** tab with concrete camera-visible English nouns (e.g. `medieval city alley lantern` instead of abstract concepts).
  3. Add your own assets to **Local files** or upload directly via the **Upload** tab.
  4. Expand **🖼️ External Images (Flow, etc.) — Export Prompts & Bulk Import**, set scope to **Shots without visual**, generate images in Google Flow or Zapi, and bulk import them.

### 6. Why doesn't my **AI Image** API key carry over when switching Together AI ↔ DeepInfra ↔ Custom?
Under **Image provider preset (OpenAI Compatible)**, **Together AI**, **DeepInfra**, and **Custom** now store separate API keys per preset so switching presets never overwrites another provider's key. Click **🧪 Test 1 image** to verify your active preset and key cleanly.

### 7. Why is *"Re-check with AI"* or *"Check relevance with AI"* disabled?
Your active LLM does not support image (*vision*) input (for example, text-only models such as `openrouter/free`). Click **Open Settings** below the control and pick a vision-capable model (such as Gemini Flash or GPT-4o mini).

### 8. Why did `#Chapter 1:` or `#Bab 1:` appear inside my narration text?
Older imports created before heading-guard hardening could capture glued markdown headings. Open the **Script** step — if glued headings are detected, a banner with **Clean Chapter Headings from Narration** lets you repair them automatically (or run `uv run python cli_longform.py project fix-headings <project_id>`).

### 9. Why does a gallery thumbnail look outdated or need rebuilding?
The shot asset changed (for example via *Update keywords with AI* or *Search again*), while the rendered shot clip has not been rebuilt yet. Check **Needs check · N** or flagged shots, then click **Update Changed Clips** or **Find & Build Visuals**. Both gallery thumbnails and Detail previews read from `rendered_clip` using content-hash-keyed cache files.

### 10. Why doesn't partial progress start at 99% anymore?
Partial runs in Visual, Voice, and Render track only the scheduled target units (`0/N` = `0%`) and report valid/locked skipped items. Completion reaches `100%` only once the job exits cleanly.

### 11. What should I do if CapCut export refuses because CapCut is open?
Exit **CapCut Desktop** completely (including background tray/processes `CapCut.exe`), then click **Export to CapCut (Install)** or **Re-export to CapCut** again.

### 12. `ffmpeg` or `ffprobe` not found
Install FFmpeg and add it to your Windows `PATH`, or configure the explicit path to `ffmpeg.exe` in `config.toml` / Settings. Run `uv run python cli_longform.py doctor` to verify. (When installed via the Windows Setup installer, FFmpeg is already bundled automatically inside the installation directory.)

### 13. Windows Defender SmartScreen shows *"Windows protected your PC"* when opening the 1.0.2 installer
The `EasyFacelessStudio-Setup-1.0.2.exe` installer is currently **not digitally signed with a commercial Authenticode certificate** (*unsigned*), so Windows SmartScreen may display a warning prompt on newly downloaded builds. If you obtained the installer from the official project release:
1. Click **More info**.
2. Click **Run anyway**.
Setup installs per-user into `%LOCALAPPDATA%\Programs\EasyFaceless Studio` without requiring administrator privileges and preserves existing user projects during upgrades.

### 14. How to download large 1.0.2 installers from Google Drive & verify SHA-256
1. **Google Drive virus-scan warning page:** Because the installer is ~570 MB, Google Drive displays *"Google Drive can't scan this file for viruses"*. This is normal for large executable files — click **Download anyway** to start the download in your browser.
2. **Verify file integrity (SHA-256):** Open PowerShell in your Downloads folder and run:
   ```powershell
   Get-FileHash .\EasyFacelessStudio-Setup-1.0.2.exe -Algorithm SHA256
   ```
   Compare the printed `Hash` against the `.sha256` file or release notes before installing.
3. **App updates:** Clicking **Download Update** in the update banner or **About** dialog always opens the official release link in your default browser rather than downloading in the background.
