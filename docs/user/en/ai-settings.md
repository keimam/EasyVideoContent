# API & AI Provider Settings

All API keys and active models are configured in one place inside the **Settings** modal (**AI & API Providers** in the sidebar, or the provider settings buttons in each project step).

---

## 1. Provider Categories

| Category | Role in Studio | Examples | Cost Model |
|---|---|---|---|
| **LLM** | Script writing, chapter summaries, concrete English visual keywords, project visual anchors, vision relevance checks | OpenRouter (`openrouter/free` or paid slugs), OpenAI, Google Gemini, DeepSeek, Groq, Ollama (local), Qwen, Moonshot | Free & paid options |
| **TTS (Voice)** | Synthesizes narration WAV audio & subtitle timing | **Edge TTS** (free default), OpenAI TTS, Azure Speech, Gemini TTS, ElevenLabs, SiliconFlow | Edge TTS free; others follow provider billing |
| **AI Images** | Optional static AI illustrations with Ken Burns motion | OpenAI Image & compatible image providers in Material Settings | Paid per provider rate |
| **AI Video** | Generates bespoke video clips per shot via the **AI Video** tab in shot Detail | **WaveSpeed** (e.g. `Wan 2.2 Ultra Fast`, `Seedance 2.0 Fast`), Luma, Kling, Hailuo, Runway, Volcengine, etc. | **Paid** (shows USD cost estimate & requires budget cap + confirmation) |

---

## 2. Free vs Paid Models & Cost Controls

- **Free Models (`openrouter/free`):** Great for drafting scripts and generating concrete English visual keywords at zero credit cost. Keep in mind free tiers have stricter rate limits and usually do not support image (*vision*) input.
- **Vision Models (AI Relevance Check):** The optional **Check relevance with AI** toggle requires a vision-capable LLM (such as `gemini-2.5-flash` or `gpt-4o-mini`). Results are cached locally per asset hash to avoid repeat calls.
- **AI Video Guardrails:** Both single-shot and batch AI Video generation always preview estimated USD cost and enforce your maximum budget cap before sending any request.

---

## 3. Common Error Codes (`402`, `404`, `429`)

- **`402` Payment Required / Insufficient Credits:**
  Your provider balance cannot cover the chosen model or requested token budget.
  *Fix:* Top up credits or open **Settings** and select a free model such as `openrouter/free`.
- **`404` Model Not Found / Retired:**
  The configured model slug does not exist or its `:free` endpoint was retired by the provider.
  *Fix:* Open **Settings** or click **Load model list** in the Script card and select a current model slug.
- **`429` Rate Limit Exceeded:**
  Too many requests sent in a short window. Studio retries with backoff; any chapters already written remain saved on disk and can be resumed via **Resume Remaining Chapters**.
