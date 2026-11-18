# Quick Start: From Project to CapCut

This guide covers the fastest way to produce both **Short Videos** and **10–20 Minute Long Videos** in **EasyFaceless Studio**.

---

## A. Short Video (`Short Video` Menu)

1. Click **Short Video** in the left sidebar.
2. Enter a **Video Subject** (or paste your own script), then choose script language and voice.
3. If no AI API key is set yet, open **Settings** (top-right or **AI & API Providers** in the sidebar).
4. Choose aspect ratio (`9:16` Shorts/Reels or `16:9` YouTube) and start generation.

---

## B. Long Video 10–20 Minutes (`Long Video` Menu)

### Step 1 — Create a Project
1. On the **Long Video** home page, enter a **Topic or video title**.
2. Set target **Duration** (`10–20 min`) and **Narration language** (`id-ID` or `en-US`).
3. *(Optional)* Expand **Advanced settings** to choose a **Niche**, **Sub-niche**, focus topic, or aspect ratio.
4. Click **Create Project**.

### Step 2 — Write or Import Script (`Script`)
- **Write with AI:** Once your LLM provider is ready, click **Start Writing AI Script** to generate a multi-chapter outline and narration in the project language.
- **Import Markdown/Text:** Paste Markdown chapters (`# Chapter 1: Title`) or upload `.md`/`.txt`, then click **Import Script**.
- Edit any chapter in the per-chapter editor and use *"Rewrite this chapter"* when needed.

### Step 3 — Voice Synthesis (`Voice`)
1. Choose your **Voice Provider** (**Edge TTS** works free out-of-the-box without an API key) and narrator voice.
2. Click **Start Voice Synthesis**. Only pending or changed segments are processed, producing 48kHz WAV audio and word-level subtitle timings.

### Step 4 — Visual Matching & Clip Assembly (`Visual`)
1. Expand **Footage sources** to pick your stock provider (**Pixabay**, **Pexels**, **Coverr**, **Local**, or optional **AI Images**).
2. Click **Fetch & Build** to split narration into timed shots, fetch English stock clips, and build normalized clips.
3. Inspect shots in the 3-column gallery: lock keepers, switch candidates, or generate bespoke clips via **AI Video**. See [Visual Gallery & Shot Controls](visual-gallery.md).

### Step 5 — Render & CapCut (`Render` → `CapCut`)
- In **Render**, pick optional background music and click **Render All** to output `output/final_video.mp4` plus YouTube chapter timestamps.
- *(Optional)* In **CapCut**, close **CapCut Desktop** completely first, then click **Export & Install to CapCut** to install the multi-track timeline directly into CapCut.

> **Auto-Run Tip:** Once the script is ready, click **Auto-Run** to execute Voice → Visual → Render sequentially (pausing automatically if any shot is flagged *Needs check*).
