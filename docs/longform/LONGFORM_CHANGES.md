# Longform Minimal Upstream Hooks (`LONGFORM_CHANGES.md`)

All EasyFaceless Studio longform functionality lives under `app/services/longform/`, `app/services/branding/`, and longform-specific tooling so upstream files remain easy to sync. Only strictly minimal, `try/except`-guarded hooks exist in upstream files:

## Upstream Hook Inventory

### 1. `app/services/material.py` (`generate_images_openai`)
- **Hook:** Wraps `payload` with `app.services.longform.visual.image_presets.adapt_openai_image_payload(endpoint, payload, image_size=image_size)` inside a bare `try/except` fallback (Stage V101 V4).
- **Reason:** Together AI FLUX endpoints (`api.together.xyz/v1`) expect `width`, `height`, `aspect_ratio`, `steps`, and `response_format` instead of OpenAI's `size` parameter; DeepInfra benefits from explicit `response_format="b64_json"`. Keeping this in a guarded hook ensures zero behavioral change for standard OpenAI endpoints and safe fallback if longform modules are removed.

### 2. `webui/Main.py` (`elif provider == "openai_image":` settings dialog)
- **Hook:** Calls `app.services.longform.visual.image_presets.render_openai_image_preset_controls(...)` inside a guarded `try/except` block (Stage V101 V4).
- **Reason:** Surfaces quick **Together AI (FLUX.1-schnell)** / **DeepInfra (FLUX-1-schnell)** / **Custom** preset controls and the **Test 1 image** diagnostic directly in the image provider panel without altering core upstream settings widgets or translation bundles.
