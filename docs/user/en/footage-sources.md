# Footage Sources, Licensing & Attribution (`credits.md`)

This guide covers every built-in free stock source, keyed video library, historical archive provider, and local asset workflow used in **EasyFaceless Studio**.

---

## 1. Complete Footage Sources Table

*(Checked: 2026-11-26 & 2026-11-27 against official provider documentation.)*

| Source | API Key Required? | Media Types | Accepted Licenses | Verified Rate / Quota Limits |
|---|---|---|---|---|
| **Pixabay** | Key needed *(free)* | HD/4K Stock Video | Pixabay Content License *(free commercial use, attribution optional)* | **100 requests per 60 seconds** (*"By default, you can make up to 100 requests per 60 seconds."* — [pixabay.com/api/docs](https://pixabay.com/api/docs/), checked: 2026-11-27); automatic 24h disk cache |
| **Coverr** | Key needed *(free Demo / paid)* | Stock B-roll Video | Coverr License *(commercial-free)* | **50 requests per hour** on free Demo tier (*"50 free calls per hour No credit card required"* — [coverr.co/developers](https://coverr.co/developers), checked: 2026-11-27) |
| **Pexels** | Legacy key needed | HD/4K Stock Video | Pexels License | **UI Note:** *New Pexels API key registration is currently closed; existing key holders remain supported.* Official docs page rate limit unverified via automated GET (bot protection, checked: 2026-11-27) |
| **Wikimedia Commons** | **No key needed (enabled by default)** | Video (`WebM`/`OGV` auto-converted to MP4) & Photos | Public Domain, `CC0`, `CC BY`, `CC BY-SA` only (`NC`/`ND` rejected automatically) | No fixed numeric cap verified; protected by 24h search cache & polite rate spacing |
| **Openverse** | **No key needed (enabled by default)** | Photos *(Ken Burns motion)* | Commercial licenses only: `CC0`, `PDM`, `CC BY`, `CC BY-SA` (`NC`/`ND` rejected) | Managed via 24h search cache, polite rate spacing, and automatic timeout cooldown |
| **NASA Image & Video Library** | **No key needed (enabled by default)** | Space, science, Earth video & photos | Public Domain / U.S. Gov Media (credit NASA without implying endorsement) | Unauthenticated search endpoint protected by 24h disk cache |
| **Internet Archive (`Prelinger` & `Cleveland Museum of Art`)** | **No key needed (enabled by default)** | Archival MP4 video *(stream-trimmed via HTTP Range without full download)* & museum images | Curated collections + strict `licenseurl` check for Public Domain / `CC0` / `CC BY` / `CC BY-SA` (`Fair Use`/`NC`/`ND` rejected) | No published numeric RPS cap; requires descriptive `User-Agent` & delays (*"All automated requests to archive.org must include a descriptive User-Agent header"* — [archive.org/developers/bots.html](https://archive.org/developers/bots.html), checked: 2026-11-26) |
| **Smithsonian Open Access** | **Optional BYOK** *(free `api.data.gov` key)* | High-resolution museum JPEG photos | Strictly `CC0` media only (`usage.access == "CC0"`; *Limited Use* items rejected) | **1,000 requests per hour** with registered free `api.data.gov` key ([api.data.gov/docs/rate-limits](https://api.data.gov/docs/rate-limits/) per [Archive Research](../../dev/ARCHIVE_SOURCES.md), checked: 2026-11-26) |
| **Local files** | **No key needed** *(project folder)* | Local Video & Images | Your own ownership / rights | Unlimited (works offline) |
| **🖼️ External Images (Flow, etc.)** | Follows external generator account | External AI Images & Clips | Subject to the external generator you choose | Follows your Google Flow, Zapi, Midjourney, or ChatGPT account limits |

> **Default order:** `Pixabay → Coverr → Wikimedia → NASA → Openverse → Internet Archive` *(plus **Smithsonian** whenever an optional key is configured)*.

---

## 2. Obtaining & Configuring API Keys

Keyless sources (**Wikimedia Commons**, **NASA Library**, **Openverse**, and **Internet Archive**) work immediately after setup. To unlock additional stock libraries or Smithsonian photography:

1. **Pixabay (Free):**
   - Create a free account at [pixabay.com/api/docs](https://pixabay.com/api/docs/) and copy your API key.
   - Paste it under **Settings → Material Source Settings** (or Wizard step **4. Footage**).
2. **Coverr (Free Demo 50/hour):**
   - Sign up at [coverr.co/developers](https://coverr.co/developers) for a free Demo API key (50 calls/hour, no credit card required).
   - Paste it under **Settings → Material Source Settings** (or Wizard step **4. Footage**).
3. **Smithsonian Open Access (`api.data.gov`, Optional Free BYOK):**
   - Sign up free at [api.data.gov/signup](https://api.data.gov/signup/) via email to get a personal key (1,000 req/hour).
   - In the project **Visual → Footage sources** expander (or **Settings**), paste the key into **Smithsonian / api.data.gov API Key (optional)**, click **Save Smithsonian key**, then click **Test 1 request** to confirm `✅ ready`.

---

## 3. Search Modes: **Mix best** vs **Priority**

Inside **Footage sources**, pick how the engine queries active sources via **Footage search mode (this project)**:

- **Mix best (highest relevance score across active sources):** Searches all active sources in parallel and picks the highest-scoring candidate while maintaining diversity:
  - **60% single-source share cap (*Cap any single source at 60% of shots*):** Prevents any single provider from dominating more than 60% of shots when relevant alternatives exist.
  - **>3 consecutive shot guard (*Avoid same source for >3 consecutive shots*):** Prevents long streaks of clips from the same source back-to-back.
  - Near-ties within `≤ 0.02` favor whichever provider has been used fewer times in the project.
- **Priority (exhaust sources sequentially in list order):** Searches providers strictly top-to-bottom until an acceptable candidate is found. Reorder providers using the **↑** / **↓** buttons.
- **Local files priority:** In both modes, matching files in **Local files** that pass the relevance threshold always win first.

---

## 4. Archival Footage (19th–20th Century) & Copyright Vigilance

**Internet Archive** (*Prelinger Archives* & *Cleveland Museum of Art*) and **Smithsonian Open Access** work best for historical topics, documentaries, and **19th-century through 20th-century** eras.

Keep this UI reminder in mind before publishing:
> *"Archival footage: licenses reflect uploader metadata. Always check credits. Public domain and Creative Commons archival footage can still trigger third-party Content ID claims."*

- Some community uploads carry permissive tags alongside *Fair Use* notices or third-party claims (*"Many people believe that all Prelinger Archives films are in the public domain. Some are. Many are not."* — Prelinger FAQ, checked: 2026-11-26). While the client rejects `Fair Use`, `NC`, and `ND` records automatically, always review `credits.md` before monetizing public archival clips.

---

## 5. Where `credits.md` Lives & How to Open It

Every project generates `export/credits.md` inside its project directory *(also copied into final MP4 outputs and CapCut drafts)* listing asset titles, creators, licenses (`CC BY`, `CC BY-SA`, `CC0`, Public Domain, Pixabay, etc.), and source links.

Quickest ways to view and copy attribution lines:
1. Click **📜 Open credits.md** on the completed project card on the Home page or in the **Render** step.
2. In the **Footage & Music Attributions (`credits.md`)** dialog, use **📋 Copy credits.md** or **⬇️ Download credits.md** to paste required credits into your video description.
3. Or click **📂 Open output folder** and inspect `export\credits.md` directly.
