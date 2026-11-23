# Visual Gallery & Shot Controls

The **Visual** step allocates narration chapters into timed shots so visuals remain engaging and matched to your story.

---

## 1. Card Badges & Single Visual Source of Truth

- **Single Source of Truth:** Both gallery thumbnails and the right-hand Detail preview read from the exact final clip (`rendered_clip`) used in chapter rendering (falling back to `asset_path` with a *Not built* label if no clip exists yet).
- **Status Badges:**
  - `🔄 Needs rebuild`: The underlying shot asset changed (e.g., candidate switched or keywords updated), while the rendered clip is outdated. Filter with **Needs update · N** and run **Build Clips**.
  - `🧩 Not built`: Source asset exists, but the final clip has not been built yet.
  - `🔒 Locked`: Locked shots are protected and skipped during **Fetch & Build**.

---

## 2. Replacing or Customizing Shots

Click any shot card to open the Detail panel and use its 4 tabs:

1. **Candidates:** Browse filtered stock candidates (avoiding project `avoid_terms`) and click **Use** to switch clips.
2. **Search again:** Enter a custom English query (e.g., `night street food wok`) to fetch and replace immediately.
3. **Upload:** Upload a local `.mp4`/`.mov` clip or `.jpg`/`.png` image.
4. **AI Video:** Edit the English prompt and generate a clip using an AI Video provider (e.g. WaveSpeed) with estimated cost confirmation and budget limit.

---

## 3. Updating AI Keywords & Relevance Check

- **Update keywords with AI:** Generates 3–6 word concrete English queries plus fallback alternatives per unlocked shot based on chapter narrative and project visual anchors (preserving `[search]` tags and locked shots).
  When you run **Fetch & Build** afterwards, the progress bar tracks only the changed shots starting from `0%` and displays how many valid/locked shots were skipped.
- **Check relevance with AI:** Optional toggle (requires a vision-capable LLM) that labels shots as **Relevant**, **Unsure**, or **Not relevant**.

---

## 4. Footage Sources, Tiered Search & Attribution (`credits.md`)

By default, the studio searches each shot in escalating tiers (**Primary/AI Queries → Broader Visual Noun/Concept Queries → Next Source**) using this priority order:
**Pixabay → Coverr → Wikimedia Commons → NASA Library → Openverse (photos)**.

| Source | API Key | Media & Strengths | License & Attribution Rules |
|---|---|---|---|
| **Pixabay** | Free key needed | Broad cinematic HD/4K stock footage | Pixabay Content License (commercial-free, attribution optional) |
| **Coverr** | Key needed | Curated royalty-free lifestyle/scene B-roll | Coverr License |
| **Pexels** | Key needed (*New registrations closed*) | Supported for existing key holders only | Pexels License |
| **Wikimedia Commons** | **Free (no key)** | Documentary & historical photos + videos (`WebM/OGV` auto-converted to H.264 MP4) | Strict commercial filter: Public Domain, `CC0`, `CC BY`, `CC BY-SA` only (`NC`/`ND` rejected). `CC BY`/`BY-SA` requires attribution |
| **NASA Image & Video Library** | **Free (no key)** | Authentic space, science, Earth, and aviation footage | NASA Media (Public Domain / U.S. Gov). Credit NASA without implying endorsement |
| **Openverse** | **Free (no key)** | Curated open-licensed photography animated with smooth Ken Burns motion | Commercial filter (`CC0`, `PDM`, `CC BY`, `CC BY-SA` only; `NC`/`ND` rejected). `CC BY`/`BY-SA` requires attribution |

- **Missing Visual Diagnostics:** When no source returns an acceptable match, the shot is marked `"No visual yet"` with a concise summary and expandable search attempt log in the Detail panel.
- **Automatic `credits.md`:** Always written to `export/credits.md` and copied into final MP4 outputs and CapCut draft folders with creator, title, license, and source links. Shots using `CC BY` / `CC BY-SA` display an **Attribution required** notice in the Detail panel.


---

## 5. Local Media Library (`Local files`)

You can include your own footage or images (archival material, brand assets, or custom clips) inside your project's `assets/visual/library` folder:

1. **Opening the folder & uploading:** Expand **Footage Sources** on the **Visual** step to check the current file count, click **Open Local files folder** to jump straight to the directory, or use the multi-file uploader (existing filenames trigger an explicit **Auto-rename**, **Overwrite**, or **Skip** prompt so files are never silently overwritten).
2. **Naming guidelines for relevance matching:** The studio extracts lowercase tags from filename separators (`_`, `-`, `.`) and subfolder names — for example `old_lighthouse_night_fog.jpg` or `vikings/ship_storm.jpg`.
3. **Priority & random fallback option:** Whenever **Local files** is checked and a file passes the relevance threshold, it takes priority in both **Mix best** and **Priority** search modes. The **Fill empty shots with random local files** option stays disabled by default so unrelated files are never injected automatically.
4. **Supported formats:** Video (`.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`) and image (`.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`).

---

## 6. External Image Bridge (Google Flow / Midjourney / ChatGPT)

When you prefer generating specific shots with an external AI tool such as Google Flow, Midjourney, or ChatGPT, open **External Image Bridge** on the **Visual** step:

1. **Export image prompts:** Choose your target scope (*Shots without visual*, *Selected shots*, or *All shots*), prepare the files, and download `.txt` (prefixed with `[shot_id]`) or UTF-8 `.csv` (`shot_id`, duration, narration snippet, English prompt, `16:9` aspect ratio, and project visual style).
2. **Create your visuals externally:** Name outputs cleanly with the matching `shot_id` (e.g., `ch01_shot001.png`) or by trailing sequence number matching your last export order (`image_1.png`, `02.webp`, etc.).
3. **Bulk import:** Upload `.png`, `.jpg`, `.jpeg`, `.webp`, or `.mp4` files. An editable mapping table shows auto-matches and flags unmatched files clearly before you click **Apply external import**.
4. **Built & protected:** Imported images receive smooth *Ken Burns* motion (and `.mp4` clips trim to shot duration), become **Locked** automatically so they survive *Fetch & Build*, *Auto*, and *Update keywords with AI*, and are logged in `credits.md` as user-created external assets.
