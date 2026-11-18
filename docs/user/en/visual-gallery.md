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
