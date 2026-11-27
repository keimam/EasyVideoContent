# Visual Gallery & Shot Controls

The **Visual** step allocates narration chapters into timed shots so visuals remain engaging and matched to your story.

---

## 1. Card Badges & Single Visual Source of Truth

- **Single Source of Truth:** Both gallery thumbnails and the right-hand Detail preview read from the exact final clip (`rendered_clip`) used in chapter rendering.
- **Status Badges:**
  - `🔄 Needs check` / `Needs update`: The shot needs review or its underlying asset changed (e.g., candidate switched or keywords updated) and clips need updating. Filter with **Needs check · N** and run **Update Changed Clips** or **Find & Build Visuals**.
  - `🧩 Not built`: Source asset exists, but the final clip has not been built yet.
  - `🔒 Locked`: Locked shots are protected and skipped during **Find & Build Visuals**.
- **Partially done vs Paused:** If visual preparation finishes completely while some shots found no matching visual, the step status is **Partially done** (`Visual finished: X of Y shots. Z shot(s) found no matching visual.`), listing the missing shots/queries and suggestions with **Proceed to Render** instead of **Resume**.

---

## 2. Replacing or Customizing Shots

Click any shot card to open the Detail panel and use its 4 tabs:

1. **Candidates:** Browse filtered stock candidates (avoiding project `avoid_terms`) and click **Use this** to switch clips.
2. **Search more:** Enter a custom English query (e.g., `night street food wok`) and click **Search Footage** to fetch and replace immediately.
3. **Upload:** Upload a local `.mp4`/`.mov` clip or `.jpg`/`.png` image.
4. **AI Video:** Edit the English prompt and generate a clip using an AI Video provider (e.g. WaveSpeed) with estimated cost confirmation and budget limit.

---

## 3. Updating AI Keywords & Relevance Check

- **Update keywords with AI:** Generates 3–6 word concrete English queries plus fallback alternatives per unlocked shot based on chapter narrative and project visual anchors (preserving `[search]` tags and locked shots).
  When you run **Find & Build Visuals** afterwards, the progress bar tracks only the changed shots starting from `0%` and displays how many valid/locked shots were skipped.
- **Check relevance with AI:** Optional toggle (requires a vision-capable LLM) that labels shots as **Relevant**, **Unsure**, or **Not relevant**.

---

## 4. Footage Sources, Local Files & Attribution (`credits.md`)

- **Full source & licensing guide:** See [Footage Sources, Licensing & Attribution](footage-sources.md) for the complete table covering **Pixabay**, **Coverr**, **Pexels**, **Wikimedia Commons**, **Openverse**, **NASA Library**, **Internet Archive (`Prelinger` & museum collections)**, **Smithsonian Open Access** (optional `api.data.gov` BYOK), **Local files**, and **External Images**, plus **Mix best** vs **Priority** rules.
- **At a glance:** Keyless sources (`Wikimedia`, `NASA`, `Openverse`, `Internet Archive`) run automatically, `credits.md` is generated in `export/credits.md` (open quickly via **📜 Open credits.md**), and `assets/visual/library` can be managed via **📂 Open Local files folder** and **Add media to Local files (multi-file)** → **Save to Local files** (with **Fill empty shots with random local files** disabled by default).

---

## 5. External Images Guide (Google Flow, Zapi) — 3-Tab Layout

When some or all shots are best created with external AI generators (such as **Google Flow**, **Zapi**, **Midjourney**, or **ChatGPT**), scroll below the shot gallery cards on the **Visual** step and expand **🖼️ External Images (Flow, etc.) — Export Prompts & Bulk Import**, organized into **3 tabs**:

### Tab 1 — `1. Visual Context`
1. **Image style mode:** Choose the visual style for all prompts — **Cinematic realistic** *(default)*, **Illustration**, **Painting**, or **Custom** (AI writes details matching your chosen mode and prevents `Avoid` terms from conflicting with the art style).
2. **Setting & era:** Make sure **Setting & era** explicitly names the historical or modern era (e.g., `19th century London` or `1990s`). If no era is mentioned, a yellow warning reminds you to add one so clothing and buildings stay historically consistent.
3. **Characters, POV & flashbacks (`variant_of`):** Configure characters in **Characters (table: ID, name, complete physical description, aliases, role, variant_of)**. For flashback age variants (such as a childhood version of an adult character), add a variant row referencing the main character's ID in `variant_of`. If your narration uses second-person *"you"*, select **Second-person POV 'you' character**.
4. **Recurring locations:** Keep interior and exterior scenes separate (`_interior` / `_exterior`) in **Recurring locations (split `_interior` / `_exterior`)**, and keep **Color palette & mood** free of specific time-of-day or lighting words.
5. **✨ Generate with AI & Save:** Click **✨ Generate with AI** to auto-populate Visual Context from your title, topic, preset, and script. Duplicate/pronoun aliases and stray time/light words are auto-cleaned on load and save with clear notices before saving via **💾 Save Visual Context**.

### Tab 2 — `2. Export Prompts`
1. **Use AI & target scope:** Keep **Use AI to compose continuous scenes & prompts** checked to maintain scene-to-scene location, time of day, weather, lighting, owned props, and character state across batches, pick **Shots to export** (*Shots without visual*, *Selected shots*, or *All shots*), and click **Prepare prompt files (.txt & .csv)**.
2. **`missing character lock` warning & `🔄 Resume with AI`:**
   - `⚠️ N prompt(s) have validator notices (M missing character lock)` means some shot prompts are missing a character physical lock from Visual Context — select those shots in the scene preview below to assign **Characters in shot**, then click **🔄 Apply preview edits & refresh downloads**.
   - If a batch stops partway (e.g., quota exhausted or timeout), remaining shots fall back gracefully and **🔄 Resume with AI** appears so you can continue only the incomplete batches from cache.
3. **Three download files:** Located cleanly at the top of the Export results:
   - **⬇️ Download Prompts (.txt)** — numbered multi-line prompt list ready to copy or review.
   - **⬇️ Download Prompt Table (.csv UTF-8)** — structured scene and prompt spreadsheet for Excel/Sheets.
   - **⬇️ Prompts for bulk tool (Zapi, etc.) — one line per prompt** (`<project>_prompts_bulk.txt`) — UTF-8 (no BOM) file formatted strictly **1 line = 1 prompt** for bulk queues.

### Tab 3 — `3. Import Images`
1. **Import from local folder (read-only) or upload files:** Enter your downloads folder under **Import from local folder (optional — original files stay untouched)**, keep **Folder sort order** on **Time created (default)** (or switch to *Filename*), and click **📂 Read folder** — or upload `.png`/`.jpg`/`.jpeg`/`.webp`/`.mp4` files directly.
2. **Filename matching & browser `name.webp` / `name (1).webp` pattern:** Matches exact `shot_id` (`ch01_shot001.png`), sequence numbers (`image_1.png`, `02.webp`), and same-base browser duplicates **`name.webp`, `name (1).webp`, `name (2).webp`** (→ shots 1, 2, 3). If `(1)`-style files appear without the unnumbered first file, a yellow warning flags a potential off-by-one shift.
3. **Preview grid & Apply button:** Inspect **Editable mapping preview before apply** (thumbnail, filename, **Target shot**, and **80-char prompt snippet** side by side), then click **✅ Apply external import** above the grid — images receive *Ken Burns* motion (`.mp4` trims to shot duration), lock automatically (`🔒 Locked`), and log to `credits.md`.

### Quick Zapi Workflow
1. In **2. Export Prompts**, run **Prepare prompt files (.txt & .csv)** and download **⬇️ Prompts for bulk tool (Zapi, etc.) — one line per prompt** (`<project>_prompts_bulk.txt`).
2. Paste the lines into your **Zapi** queue *(1 line runs as 1 separate prompt)* and generate images **one by one** into a single downloads folder (e.g. `name.webp`, `name (1).webp`, `name (2).webp`, ...).
3. Open **3. Import Images**, enter that folder path under **Import from local folder**, keep **Time created (default)**, and click **📂 Read folder**.
4. Verify each thumbnail against the 80-character target prompt snippet in the preview grid, then click **✅ Apply external import**.
