# API & AI Provider Settings

All API keys and active models are managed centrally in the **Settings** dialog (**AI & API Providers** in the sidebar, or the provider switch buttons inside each project step).

---

## 1. Provider Categories

| Category | Role in Studio | Examples | Cost Model |
|---|---|---|---|
| **LLM** | Script writing, summaries, concrete visual keywords, *Visual Context*, scene continuity, vision relevance checks | OpenRouter (`openrouter/free` or paid slugs), Google Gemini (Flash), Groq, DeepSeek, OpenAI, Ollama (local), Qwen, Moonshot | Free & paid options |
| **TTS (Voice)** | Synthesizes WAV narration audio & subtitle timing | **Edge TTS** (free default), OpenAI TTS, Azure Speech, Gemini TTS, ElevenLabs, SiliconFlow | Edge TTS free; others follow provider billing |
| **AI Images** | Optional static AI illustrations with Ken Burns motion | Together AI, DeepInfra, OpenAI Image, or Custom OpenAI-compatible gateways | Paid per provider rate |
| **Stock & Archival Media** | Supplies stock video & archival photography per shot | Pixabay (free key), Coverr (Demo/Pro key), **Wikimedia Commons**, **NASA Library**, **Openverse**, **Internet Archive** (keyless), plus optional **Smithsonian Open Access** (BYOK `api.data.gov`). See [Footage Sources](footage-sources.md) | Free / subject to each source license |
| **AI Video** | Generates bespoke video clips per shot via the **AI Video** tab in shot Detail | **WaveSpeed** (e.g. `Wan 2.2 Ultra Fast`, `Seedance 2.0 Fast`), Luma, Kling, Hailuo, Runway, Volcengine, etc. | **Paid** (shows USD cost estimate & requires budget cap + confirmation) |

---

## 2. Free Models, Gemini Flash & Cost Controls

- **Free Models (`openrouter/free`):** Great for drafting scripts and generating concrete English visual keywords at zero credit cost. Note that free tiers have stricter rate limits and generally do not support image (*vision*) input.
- **Gemini `limit: 0` → Pick a Flash model:** If testing or calling Gemini reports `limit: 0` or says a model is unavailable on the free tier, open **Settings** and select a **Flash** model (such as `gemini-3.5-flash-lite` or `gemini-2.5-flash`).
- **Vision Models (AI Relevance Check):** The optional **Check relevance with AI** toggle requires a vision-capable LLM (such as `gemini-2.5-flash` or `gpt-4o-mini`). Results are cached locally per asset hash.
- **AI Video Guardrails:** Both single-shot and batch AI Video generation always preview estimated USD cost and enforce your maximum budget cap before sending any request.

---

## 3. Common Error Codes (`402`, `404`, `429`)

- **`402` Payment Required / Insufficient Credits:**
  Your provider balance cannot cover the chosen model or requested token budget.
  *Fix:* Top up credits, switch to a free model in **Settings**, or rely on **Backup AI providers**.
- **`404` Model Not Found / Retired:**
  The configured model slug does not exist or its `:free` endpoint was retired.
  *Fix:* Open **Settings**, load the latest model list, and pick an active model slug.
- **`429` Rate Limit Exceeded:**
  Too many requests sent in a short window. Studio retries with backoff and fails over to **Backup AI providers** on daily quota exhaustion. Completed chapters and cached scene batches remain preserved.

---

## 4. OpenAI-Compatible Image Provider Presets

Under **Settings → Material Source Settings** (*OpenAI Compatible*), built-in presets populate **Base URL**, **Model**, and `16:9` size (`1344x768`) while storing **separate API keys per preset (`openai_image_api_keys_by_preset`)** so switching presets never overwrites another preset's key:

| Preset | Base URL | Model Slug | Technical & Licensing Notes (Accessed 2026-11-23) |
|---|---|---|---|
| **Together AI** | `https://api.together.xyz/v1` | `black-forest-labs/FLUX.1-schnell` | Studio adapts OpenAI `size` into `aspect_ratio`, `width`, `height`, `steps`, and `response_format`. Official pricing: [together.ai/pricing](https://together.ai/pricing). Docs: [docs.together.ai](https://docs.together.ai/docs/inference/images/overview). |
| **DeepInfra** | `https://api.deepinfra.com/v1/openai` | `black-forest-labs/FLUX-1-schnell` | Supports OpenAI `size` and `b64_json`. Model page states pricing formula `\$0.0005 × (width / 1024) × (height / 1024) × iters`. Page: [deepinfra.com/black-forest-labs/FLUX-1-schnell](https://deepinfra.com/black-forest-labs/FLUX-1-schnell). |
| **Custom** | *(manual)* | *(manual)* | For local ComfyUI/Automatic1111 gateways or other compatible endpoints. |

- **Nebius Note:** Nebius deprecated and disabled all Text-to-Image models effective April 13, 2026 ([docs.tokenfactory.nebius.com/other-capabilities/deprecation-info](https://docs.tokenfactory.nebius.com/other-capabilities/deprecation-info)), so it is excluded from presets.
- **FLUX.1 Licensing:**
  - `black-forest-labs/FLUX.1-schnell` is released under the **Apache 2.0** license for personal, scientific, and commercial use ([huggingface.co/black-forest-labs/FLUX.1-schnell](https://huggingface.co/black-forest-labs/FLUX.1-schnell)).
  - `black-forest-labs/FLUX.1-dev` *(if configured manually)* uses `flux-1-dev-non-commercial-license` ([huggingface.co/black-forest-labs/FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev)); check terms before commercial deployment.
- **`🧪 Test 1 image` Button:** Click **🧪 Test 1 image** in Settings or Long Video **Footage sources** to preview one generated test image immediately or inspect categorized errors (`401/403` auth, `402`/quota, `404`, `400/422` parameter, or timeout) with API keys redacted.

---

## 5. **Backup AI providers** (Cross-Provider Auto-Failover)

To prevent script generation, AI keywords, *Visual Context*, and scene continuity batches from stopping when your primary AI hits a daily limit or returns `401`/`402`/`404`/`429`:

### Location & Setup in the UI
- Open **Settings → General → Backup AI providers (auto-failover on quota/error)**.
- **Primary provider:** Follows the active selection in **General** settings or LLM Settings. Changes made during an active render apply once the render completes.
- **Backup provider order (tried sequentially):** Pick and order your backups (`llm_failover_chain` in `config/longform.toml`, commonly **`gemini`**, **`groq`**, **`deepseek`**).

### Status Badge Meanings (`AI provider chain status:`)
- **`✅ Ready`:** API key and model are valid and ready for failover.
- **`⚠️ No API key` / `Needs model`:** Skipped automatically without wasting network attempts.
- **`⚠ unusual key format`:** Key prefix/length differs from the provider's typical format — still saved and can be checked cleanly using **🔍 Test all AI providers** if you use custom proxies/keys.
- **`⏸ Temporarily paused ({until})`:** Automatically paused until quota reset (e.g., `00:00 UTC` / **07:00 WIB** for OpenRouter daily cap) or until you update the API key/model after `401`/`402`/`404`.

### `🔍 Test all AI providers` & Choosing Separate Providers
1. **Click `🔍 Test all AI providers`:** Tests your primary provider and every configured backup sequentially, reporting `✅ <Provider> (<model>): OK` or failure reasons with all API keys redacted.
2. **Pair different providers — do not stack multiple free OpenRouter keys:**
   - Per [OpenRouter Limits](https://openrouter.ai/docs/api/reference/limits) *(checked: 2026-11-27)*: *"Spend windows are UTC: a daily budget resets at 00:00 UTC"* — accounts under `$10` purchased credits share global `:free` caps (**50 requests/day**, **20 requests/minute**). Extra keys on the same account do not increase free quota.
   - **Recommended pairing:** Combine **OpenRouter** (`openrouter/free`) with **Google Gemini Flash** (e.g., `gemini-3.5-flash-lite` or `gemini-2.5-flash`), plus **Groq** or **DeepSeek** if available, so each provider has its own independent quota pool.
3. **Official provider rate-limit policies *(checked: 2026-11-26 & 2026-11-27)*:**
   - **Google Gemini:** RPM, TPM, and RPD limits apply per Google Cloud project (not per API key) and reset at midnight Pacific time ([ai.google.dev/gemini-api/docs/rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits)).
   - **Groq:** RPM, RPD (resets midnight UTC), TPM, and TPD limits apply per organization and model ([console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits)).
   - **DeepSeek:** Uses dynamic concurrency rather than a fixed RPM cap, billed from prepaid credits ([api-docs.deepseek.com/quick_start/rate_limit](https://api-docs.deepseek.com/quick_start/rate_limit)).
