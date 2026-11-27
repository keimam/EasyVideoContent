# Quick Start: From Project to CapCut

This guide walks you through producing both **Short Videos** and a full **~10-Minute Long Video** end-to-end up to CapCut Desktop export in **EasyFaceless Studio**.

---

## A. Short Video (`Short Video` Menu)

1. Click **Short Video** in the left sidebar.
2. Enter a **Video Subject** (or paste your own script), then choose script language and voice.
3. If no AI API key is set yet, open **Settings** (top-right or **AI & API Providers** in the sidebar).
4. Pick your aspect ratio (`9:16` Shorts/Reels or `16:9` YouTube) and start generation.

---

## B. Full ~10-Minute Long Video Workflow (`Long Video` Menu)

### Step 1 — Create a New Project
1. On the **Long Video** home page (or click **+ New Project**), enter your video topic/title.
2. Set target duration (e.g., `10 min`) and narration language (`id-ID` or `en-US`).
3. *(Optional)* Pick a **Niche**, **Sub-niche**, focus topic, or aspect ratio (`16:9` / `9:16`).
4. Create the project to open the 5-step pipeline: **Script → Voice → Visual → Render → CapCut**.

### Step 2 — Write or Import **Script**
- **AI Script Automation:** Ensure your primary LLM is ready (we recommend configuring **Backup AI providers** under **Settings → General** so daily caps never halt progress), then click **1. Generate Script Outline** and **2. Write All Chapter Scripts**.
- **Import Your Own Script:** Upload a `.md`/`.txt` file or paste `# Chapter 1: Title` text and click **Import Script into Project**.
- Review or edit chapters directly before clicking **Proceed to Voice**.

### Step 3 — Synthesize **Voice**
1. Pick your narrator voice (**Edge TTS** works free without any API key) under **Voice Options, Speed & Preview**.
2. Click **Start Voice Synthesis** (or test first with **Test Voice Sample**). Only new or changed segments are synthesized to WAV with aligned subtitles.
3. Click **Proceed to Visual** once done.

### Step 4 — Prepare & Review **Visual**
1. Expand **Footage sources** to pick your search mode (**Mix best** default or **Priority**), enable **Local files**, or add **Pixabay / Coverr / Smithsonian** keys. Keyless sources (**Wikimedia Commons**, **NASA Library**, **Openverse**, and **Internet Archive**) are enabled out of the box — see [Footage Sources](footage-sources.md).
2. Click **Find & Build Visuals** to segment chapters into shots, search sources, and build clips.
3. Review shots in the **Visual Gallery**:
   - Filter with **Needs check · N** if flagged, or replace clips via **Candidates**, **Search more**, **Upload**, or **AI Video**.
   - If the run ends as **Partially done** (`Visual finished: X of Y shots. Z shot(s) found no matching visual.`), fill remaining gaps via **Search more**, **Local files**, or **🖼️ External Images (Flow, etc.)** before clicking **Proceed to Render**. See [Visual Gallery & Shot Controls](visual-gallery.md).

### Step 5 — **Render** & **CapCut**
1. In **Render**, adjust background music or subtitle options under **Render Settings (Background Music & Subtitles)** if desired, then click **Start Final Video Render**.
2. Once finished, outputs include `export/final_video.mp4`, YouTube timestamps (`export/youtube_chapters.txt`), and asset credits (`export/credits.md`). Click **📂 Open output folder** or **📜 Open credits.md** to copy attributions quickly.
3. *(Optional)* In **CapCut**, make sure **CapCut Desktop** is closed (**Closed · safe**), then click **Export to CapCut (Install)** to write the multi-track timeline directly into CapCut Desktop.

> **One-Click Pipeline (`Run until done`):** After **Script** is ready, click **Run until done** to run **Voice → Visual → Render → CapCut** sequentially in the background.

---

## C. Estimated Production Time for a ~10-Minute Video (CA1 Benchmark)

Measured on a 6-chapter benchmark project (**77 shots**, ~7.51 min final duration, `--no-llm` script import) using test PC specs: **Windows 10 64-bit, Intel Core i3-12100 (4 cores / 8 threads), 7.75 GiB (~8 GB) physical RAM, Python 3.11.9, CapCut Desktop v8.9.0** ([`docs/dev/BENCHMARK.md`](../../dev/BENCHMARK.md)):

| Production Phase | Run 1 — Cold Cache *(fresh search/download)* | Run 2 — Warm Cache *(reusing search/media cache)* | Notes |
|---|---:|---:|---|
| **1. Script** | ~0.03s *(import)* | ~0.05s *(import)* | AI script generation adds LLM latency depending on provider rate limits |
| **2. Voice** | **10.3s** | **11.1s** | Depends on internet connection to TTS service (Edge TTS) |
| **3. Visual** | **~8.5 min** (`507.5s`) | **~6.2 min** (`369.7s`) | Parallel search + download + clip build; strongly depends on internet speed |
| **4. Final Render** | **~2.1 min** (`125.6s`) | **~2.1 min** (`126.2s`) | Local CPU encoding (FFmpeg) |
| **5. CapCut Export** | **3.1s** | **2.8s** | Local draft generation & asset copy |
| **Total (5 Phases)** | **~10.8 min** (`646.6s`) | **~8.5 min** (`509.9s`) | **Real-world wall time depends on your internet speed & upstream source servers** |

---

## D. System Requirements (Calibrated via E4 Benchmark)

Based on measured storage (`~1.75 GB` per 77-shot project + `~2.3 GB` shared visual cache in [Section 5 of `docs/dev/BENCHMARK.md`](../../dev/BENCHMARK.md)):

- **Operating System:** Windows 10 / Windows 11 (64-bit)
- **CPU:** Minimum **4 cores / 4 threads** · Recommended **6–8+ threads** (parallel clip building scales cleanly on 4C/8T)
- **Memory (RAM):** Minimum **8 GB** *(measured CA1 peak Python + FFmpeg ~1.38 GiB cold)* · Recommended **16 GB** when editing alongside CapCut Desktop
- **Free Disk Space (Data Dir):** Minimum **15 GB** · Recommended **20 GB+ SSD** to hold multiple projects and media cache comfortably
- **Internet Connection:** Stable broadband required for online footage search/downloads, Edge TTS, and external AI APIs
