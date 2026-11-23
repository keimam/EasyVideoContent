# API & AI Provider Settings

All API keys and active models are configured in one place inside the **Settings** modal (**AI & API Providers** in the sidebar, or the provider settings buttons in each project step).

---

## 1. Provider Categories

| Category | Role in Studio | Examples | Cost Model |
|---|---|---|---|
| **LLM** | Script writing, chapter summaries, concrete English visual keywords, project visual anchors, vision relevance checks | OpenRouter (`openrouter/free` or paid slugs), OpenAI, Google Gemini, DeepSeek, Groq, Ollama (local), Qwen, Moonshot | Free & paid options |
| **TTS (Voice)** | Synthesizes narration WAV audio & subtitle timing | **Edge TTS** (free default), OpenAI TTS, Azure Speech, Gemini TTS, ElevenLabs, SiliconFlow | Edge TTS free; others follow provider billing |
| **AI Images** | Optional static AI illustrations with Ken Burns motion | OpenAI Image & compatible image providers in Material Settings | Paid per provider rate |
| **Stock & Open Media** | Supplies stock video & documentary photography per shot | Pixabay (free key), Coverr, **Wikimedia Commons** (no key), **NASA Library** (no key), **Openverse** (no key). *Note: New Pexels API key registration is closed.* | Free / subject to each source license |
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

---

## 4. OpenAI-Compatible Image Provider Presets

Under **Settings → Image Generation** (*OpenAI Compatible*), built-in presets populate **Base URL**, **Model**, and `16:9` size (`1344x768`) **without overwriting your existing API key**:

| Preset | Base URL | Model Slug | Technical & Licensing Notes (Accessed 2026-11-23) |
|---|---|---|---|
| **Together AI** | `https://api.together.xyz/v1` | `black-forest-labs/FLUX.1-schnell` | Studio adapts OpenAI `size` into `aspect_ratio`, `width`, `height`, `steps`, and `response_format`. Official pricing: [together.ai/pricing](https://together.ai/pricing). Docs: [docs.together.ai](https://docs.together.ai/docs/inference/images/overview). |
| **DeepInfra** | `https://api.deepinfra.com/v1/openai` | `black-forest-labs/FLUX-1-schnell` | Supports OpenAI `size` and `b64_json`. Model page states pricing formula `\$0.0005 × (width / 1024) × (height / 1024) × iters`. Page: [deepinfra.com/black-forest-labs/FLUX-1-schnell](https://deepinfra.com/black-forest-labs/FLUX-1-schnell). |
| **Custom** | *(manual)* | *(manual)* | For local ComfyUI/SD gateways or other compatible endpoints. |

- **Nebius Note:** Nebius deprecated and disabled all Text-to-Image models effective April 13, 2026 ([docs.tokenfactory.nebius.com/other-capabilities/deprecation-info](https://docs.tokenfactory.nebius.com/other-capabilities/deprecation-info)), so it is excluded from presets.
- **FLUX.1 Licensing:**
  - `black-forest-labs/FLUX.1-schnell` is released under the **Apache 2.0** license for personal, scientific, and commercial use ([huggingface.co/black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell)).
  - `black-forest-labs/FLUX.1-dev` *(if configured manually)* uses `flux-1-dev-non-commercial-license` ([huggingface.co/black-forest-labs/FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev)); check terms before commercial deployment.
- **Test 1 Image Button:** Click **Test 1 image** in Image Settings or Long Video **Footage Sources** to preview a generated test image immediately or inspect categorized errors (`401/403` auth, `402`/quota, `404` model/endpoint, `400/422` parameter, or timeout/network) with API keys redacted.
