"""OpenAI-compatible AI image provider presets, payload adaptation, and 1-image tester (Stage V101 V4)."""

from __future__ import annotations

import base64
import hashlib
import io
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from loguru import logger
from PIL import Image

from app.services.longform.ui.i18n import get_ui_language, t

DEFAULT_IMAGE_TEST_PROMPT = "A serene misty pine mountain valley at dawn, cinematic 16:9 landscape, photorealistic"

IMAGE_PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "together": {
        "key": "together",
        "label_id": "Together AI (FLUX.1-schnell)",
        "label_en": "Together AI (FLUX.1-schnell)",
        "base_url": "https://api.together.xyz/v1",
        "model": "black-forest-labs/FLUX.1-schnell",
        "size": "1344x768",
        "docs_url": "https://docs.together.ai/docs/inference/images/overview",
        "pricing_url": "https://together.ai/pricing",
    },
    "deepinfra": {
        "key": "deepinfra",
        "label_id": "DeepInfra (FLUX-1-schnell)",
        "label_en": "DeepInfra (FLUX-1-schnell)",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "model": "black-forest-labs/FLUX-1-schnell",
        "size": "1344x768",
        "docs_url": "https://deepinfra.com/black-forest-labs/FLUX-1-schnell",
        "pricing_url": "https://deepinfra.com/black-forest-labs/FLUX-1-schnell",
    },
    "custom": {
        "key": "custom",
        "label_id": "Kustom (isi manual)",
        "label_en": "Custom (manual entry)",
        "base_url": "",
        "model": "",
        "size": "1344x768",
        "docs_url": "",
        "pricing_url": "",
    },
}


def redact_api_secrets(text: str, api_keys: Optional[List[str] | str] = None) -> str:
    """Scrub API keys and Bearer tokens from error text before logging or rendering (V4)."""
    out = str(text or "")
    keys_list: List[str] = []
    if isinstance(api_keys, (list, tuple)):
        keys_list = [str(k).strip() for k in api_keys if str(k or "").strip()]
    elif isinstance(api_keys, str) and api_keys.strip():
        keys_list = [k.strip() for k in api_keys.split(",") if k.strip()]

    for k in keys_list:
        if len(k) >= 4:
            out = out.replace(k, "***")
    out = re.sub(r"(?i)bearer\s+[a-z0-9_\-\.]{6,}", "Bearer ***", out)
    out = re.sub(r"(?i)\b(sk|key|tgp|di|tok)-[a-z0-9_\-]{6,}", r"\1-***", out)
    return out


def infer_image_preset_key(base_url: Optional[str]) -> str:
    """Identify preset key from an existing Base URL without mutating custom configs."""
    low = str(base_url or "").strip().lower()
    if "together.xyz" in low:
        return "together"
    if "deepinfra.com" in low:
        return "deepinfra"
    return "custom"


def _parse_size_tuple(image_size: Optional[str]) -> Tuple[int, int]:
    m = re.match(r"^\s*(\d{3,4})\s*[xX×]\s*(\d{3,4})\s*$", str(image_size or ""))
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if 256 <= w <= 2048 and 256 <= h <= 2048:
            return w, h
    return 1344, 768


def adapt_openai_image_payload(
    endpoint: str,
    payload: Dict[str, Any],
    *,
    image_size: Optional[str] = None,
) -> Dict[str, Any]:
    """Adapt standard OpenAI /images/generations payload for Together AI / DeepInfra (V4).

    - Together AI FLUX endpoints reject/ignore OpenAI `size` and expect `aspect_ratio`, `width`,
      `height`, `steps`, `response_format` (`docs.together.ai/reference/post-images-generations`).
    - DeepInfra `/v1/openai/images/generations` accepts OpenAI `size` and `response_format='b64_json'`.
    """
    adapted = dict(payload or {})
    host = (urlparse(str(endpoint or "")).netloc or "").lower()
    raw_size = image_size or adapted.get("size") or "1344x768"

    if "together.xyz" in host:
        w, h = _parse_size_tuple(str(raw_size))
        adapted.pop("size", None)
        adapted["width"] = w
        adapted["height"] = h
        if w > h:
            adapted.setdefault("aspect_ratio", "16:9")
        elif h > w:
            adapted.setdefault("aspect_ratio", "9:16")
        else:
            adapted.setdefault("aspect_ratio", "1:1")
        model_low = str(adapted.get("model") or "").lower()
        adapted.setdefault("steps", 4 if "schnell" in model_low else 20)
        adapted.setdefault("response_format", "b64_json")
        return adapted

    if "deepinfra.com" in host:
        adapted.setdefault("size", str(raw_size))
        adapted.setdefault("response_format", "b64_json")
        return adapted

    return adapted


def migrate_openai_image_api_keys_by_preset(
    app_settings: Dict[str, Any],
    *,
    save_fn: Optional[Any] = None,
    runtime_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """One-time migration moving a single legacy openai_image_api_keys entry into per-preset dict (B5)."""
    existing_map = app_settings.get("openai_image_api_keys_by_preset")
    if isinstance(existing_map, dict):
        cleaned = {
            str(k).strip(): str(v).strip()
            for k, v in existing_map.items()
            if str(k or "").strip() and str(v or "").strip()
        }
        return cleaned

    migrated: Dict[str, str] = {}
    cur_key = _extract_first_api_key(app_settings.get("openai_image_api_keys"))
    if cur_key:
        target_preset = infer_image_preset_key(app_settings.get("openai_image_base_url"))
        migrated[target_preset] = cur_key

    app_settings["openai_image_api_keys_by_preset"] = dict(migrated)
    if runtime_cfg is not None:
        runtime_cfg["openai_image_api_keys_by_preset"] = dict(migrated)
    if callable(save_fn):
        save_fn("openai_image_api_keys_by_preset", dict(migrated))
    return dict(migrated)


def apply_image_provider_preset(
    preset_key: str,
    app_settings: Dict[str, Any],
    *,
    save_fn: Optional[Any] = None,
    runtime_cfg: Optional[Dict[str, Any]] = None,
    active_api_key: Optional[str] = None,
    previous_preset_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply preset Base URL/Model/Size while storing active key under old preset and loading new preset key (B5)."""
    if preset_key not in IMAGE_PROVIDER_PRESETS:
        raise ValueError(f"Unknown image preset: {preset_key}")
    preset = IMAGE_PROVIDER_PRESETS[preset_key]

    by_preset = migrate_openai_image_api_keys_by_preset(
        app_settings,
        save_fn=save_fn,
        runtime_cfg=runtime_cfg,
    )
    old_preset = (
        str(previous_preset_key).strip()
        if previous_preset_key and str(previous_preset_key).strip() in IMAGE_PROVIDER_PRESETS
        else infer_image_preset_key(app_settings.get("openai_image_base_url"))
    )
    cur_active_key = (
        _extract_first_api_key(active_api_key)
        if active_api_key is not None
        else _extract_first_api_key(app_settings.get("openai_image_api_keys"))
    )
    if cur_active_key:
        by_preset[old_preset] = cur_active_key
    elif active_api_key is not None and not cur_active_key and old_preset == preset_key:
        by_preset.pop(old_preset, None)

    loaded_key = str(by_preset.get(preset_key) or "").strip()
    new_keys_list = [loaded_key] if loaded_key else []

    app_settings["openai_image_base_url"] = preset["base_url"]
    app_settings["openai_image_model"] = preset["model"]
    app_settings["openai_image_size"] = preset["size"]
    app_settings["openai_image_api_keys_by_preset"] = dict(by_preset)
    app_settings["openai_image_api_keys"] = list(new_keys_list)

    if runtime_cfg is not None:
        runtime_cfg["openai_image_base_url"] = preset["base_url"]
        runtime_cfg["openai_image_model"] = preset["model"]
        runtime_cfg["openai_image_size"] = preset["size"]
        runtime_cfg["openai_image_api_keys_by_preset"] = dict(by_preset)
        runtime_cfg["openai_image_api_keys"] = list(new_keys_list)

    if callable(save_fn):
        save_fn("openai_image_base_url", preset["base_url"])
        save_fn("openai_image_model", preset["model"])
        save_fn("openai_image_size", preset["size"])
        save_fn("openai_image_api_keys_by_preset", dict(by_preset))
        save_fn("openai_image_api_keys", list(new_keys_list))

    res = dict(preset)
    res["loaded_api_key"] = loaded_key
    return res


def categorize_image_test_error(
    status_code: Optional[int],
    body_text: str = "",
    *,
    api_key: str = "",
    lang: Optional[str] = None,
) -> Tuple[str, str]:
    """Classify HTTP/API errors cleanly into auth/quota/not_found/invalid_param/network without leaking keys."""
    eff_lang = (lang or get_ui_language()).lower()
    safe_body = redact_api_secrets(body_text, api_key)
    low = safe_body.lower()

    if status_code in (401, 403):
        return "auth", t("img_test_err_auth", lang=eff_lang, code=status_code)
    if status_code == 402 or any(k in low for k in ("insufficient", "quota", "credit", "payment required", "balance")):
        return "quota", t("img_test_err_quota", lang=eff_lang, code=status_code or 402)
    if status_code == 404:
        return "not_found", t("img_test_err_not_found", lang=eff_lang)
    if status_code in (400, 422):
        # Extract offending parameter if returned by JSON API
        param_match = re.search(r'"param"\s*:\s*"([A-Za-z0-9_.\-]+)"', safe_body)
        if not param_match:
            param_match = re.search(r"(?:invalid|unknown|unsupported|unexpected)\s+(?:parameter|field)\s*[:\'\"]*\s*([A-Za-z0-9_.\-]+)", low)
        param_name = param_match.group(1) if param_match else "-"
        return "invalid_param", t("img_test_err_invalid_param", lang=eff_lang, code=status_code, param=param_name, detail=safe_body[:180])
    if status_code == 429:
        return "rate_limit", t("img_test_err_rate_limit", lang=eff_lang)
    return "http_error", t("img_test_err_generic", lang=eff_lang, code=status_code or "-", detail=safe_body[:180])


def _build_images_endpoint(base_url: str) -> str:
    clean = str(base_url or "").strip().rstrip("/")
    if not clean:
        clean = "https://api.openai.com/v1"
    if clean.endswith("/images/generations"):
        return clean
    if re.search(r"/v\d+(?:/openai)?$", clean):
        return f"{clean}/images/generations"
    return f"{clean}/v1/images/generations"


def test_single_openai_image(
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    image_size: Optional[str] = None,
    prompt: str = DEFAULT_IMAGE_TEST_PROMPT,
    timeout: int = 30,
    lang: Optional[str] = None,
) -> Dict[str, Any]:
    """Send 1 test request to active OpenAI-compatible image provider and return preview bytes or categorized error."""
    from app.config import config

    eff_lang = (lang or get_ui_language()).lower()
    raw_base = (
        str(base_url).strip()
        if str(base_url or "").strip()
        else str(config.app.get("openai_image_base_url", "") or "").strip()
    )
    raw_model = (
        str(model).strip()
        if str(model or "").strip()
        else str(config.app.get("openai_image_model", "") or "").strip()
    )
    raw_size = (
        str(image_size).strip()
        if str(image_size or "").strip()
        else (str(config.app.get("openai_image_size", "") or "").strip() or "1344x768")
    )

    if str(api_key or "").strip():
        raw_key = _extract_first_api_key(api_key)
    else:
        raw_key = _extract_first_api_key(config.app.get("openai_image_api_keys"))

    if not str(raw_base).strip():
        return {"ok": False, "category": "config", "message": t("img_test_err_missing_url", lang=eff_lang)}
    if not str(raw_model).strip():
        return {"ok": False, "category": "config", "message": t("img_test_err_missing_model", lang=eff_lang)}

    endpoint = _build_images_endpoint(str(raw_base))
    payload = adapt_openai_image_payload(
        endpoint,
        {
            "model": str(raw_model).strip(),
            "prompt": str(prompt or DEFAULT_IMAGE_TEST_PROMPT).strip(),
            "n": 1,
            "size": str(raw_size).strip() or "1344x768",
        },
        image_size=str(raw_size).strip() or "1344x768",
    )

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if raw_key:
        headers["Authorization"] = f"Bearer {raw_key}"

    try:
        resp = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            proxies=getattr(config, "proxy", None),
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.exceptions.Timeout:
        return {"ok": False, "category": "network", "message": t("img_test_err_network", lang=eff_lang, detail="Timeout")}
    except Exception as exc:
        safe_exc = redact_api_secrets(f"{type(exc).__name__}: {exc}", raw_key)
        return {"ok": False, "category": "network", "message": t("img_test_err_network", lang=eff_lang, detail=safe_exc[:160])}

    status = int(getattr(resp, "status_code", 0) or 0)
    if status >= 400:
        cat, msg = categorize_image_test_error(status, getattr(resp, "text", "") or "", api_key=raw_key, lang=eff_lang)
        logger.warning(f"Image test failed ({cat}, HTTP {status}) on {endpoint}: {msg}")
        return {"ok": False, "category": cat, "status_code": status, "message": msg}

    try:
        body = resp.json()
    except Exception:
        return {"ok": False, "category": "invalid_response", "message": t("img_test_err_bad_json", lang=eff_lang)}

    data_items = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data_items, list) or not data_items or not isinstance(data_items[0], dict):
        return {"ok": False, "category": "invalid_response", "message": t("img_test_err_bad_json", lang=eff_lang)}

    entry = data_items[0]
    img_bytes: Optional[bytes] = None
    if entry.get("b64_json"):
        img_bytes = base64.b64decode(entry["b64_json"])
    elif entry.get("url"):
        dl = requests.get(str(entry["url"]), proxies=getattr(config, "proxy", None), timeout=timeout)
        dl.raise_for_status()
        img_bytes = dl.content

    if not img_bytes:
        return {"ok": False, "category": "invalid_response", "message": t("img_test_err_bad_json", lang=eff_lang)}

    with Image.open(io.BytesIO(img_bytes)) as im:
        im.load()
        w, h = im.size
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="PNG")
        png_bytes = buf.getvalue()

    return {
        "ok": True,
        "image_bytes": png_bytes,
        "width": w,
        "height": h,
        "model": str(raw_model).strip(),
        "endpoint": endpoint,
    }


def _extract_first_api_key(raw_keys: Any) -> str:
    """Return the first non-empty API key from list/tuple/comma-or-newline string."""
    if isinstance(raw_keys, (list, tuple)):
        tokens = [str(k).strip() for k in raw_keys if str(k or "").strip()]
    else:
        tokens = [tok.strip() for tok in re.split(r"[,;\r\n]+", str(raw_keys or "")) if tok.strip()]
    return tokens[0] if tokens else ""


def resolve_active_openai_image_form_values(
    *,
    session_state: Optional[Any] = None,
    app_settings: Optional[Dict[str, Any]] = None,
    runtime_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """Resolve active openai_image fields from form session_state first, falling back to saved config (never '')."""
    ss = session_state if isinstance(session_state, dict) or hasattr(session_state, "get") else {}
    app_s = app_settings if isinstance(app_settings, dict) else {}
    rt_s = runtime_cfg if isinstance(runtime_cfg, dict) else {}

    def _pick_text(widget_key: str, cfg_key: str) -> Optional[str]:
        for src_val in (ss.get(widget_key) if ss else None, app_s.get(cfg_key), rt_s.get(cfg_key)):
            cleaned = str(src_val or "").strip()
            if cleaned:
                return cleaned
        return None

    def _pick_key() -> Optional[str]:
        for src_val in (
            ss.get("openai_image_api_keys_input") if ss else None,
            app_s.get("openai_image_api_keys"),
            rt_s.get("openai_image_api_keys"),
        ):
            tok = _extract_first_api_key(src_val)
            if tok:
                return tok
        return None

    return {
        "base_url": _pick_text("openai_image_base_url_input", "openai_image_base_url"),
        "api_key": _pick_key(),
        "model": _pick_text("openai_image_model_input", "openai_image_model"),
        "image_size": _pick_text("openai_image_size_input", "openai_image_size"),
    }


def _openai_image_config_signature(
    preset_key: str,
    vals: Dict[str, Optional[str]],
) -> str:
    raw = "|".join([
        str(preset_key or ""),
        str(vals.get("base_url") or ""),
        str(vals.get("api_key") or ""),
        str(vals.get("model") or ""),
        str(vals.get("image_size") or ""),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def render_openai_image_preset_controls(
    *,
    st_module: Any,
    app_settings: Dict[str, Any],
    save_settings_fn: Optional[Any] = None,
    set_runtime_cfg_fn: Optional[Any] = None,
) -> None:
    """Render preset dropdown and 1-image test UI inside upstream openai_image settings (V4, A2.4)."""
    from app.config import config as _cfg
    from app.config.config import save_config as _save_cfg

    lang = get_ui_language()
    migrate_openai_image_api_keys_by_preset(app_settings, runtime_cfg=_cfg.app)
    active_vals = resolve_active_openai_image_form_values(
        session_state=st_module.session_state,
        app_settings=app_settings,
        runtime_cfg=_cfg.app,
    )
    inferred = infer_image_preset_key(active_vals.get("base_url"))
    preset_keys = ["custom", "together", "deepinfra"]

    # Keep current preset's non-empty active key synced in by_preset map without losing other presets
    if active_vals.get("api_key"):
        cur_map = dict(
            app_settings.get("openai_image_api_keys_by_preset")
            or _cfg.app.get("openai_image_api_keys_by_preset")
            or {}
        )
        if cur_map.get(inferred) != active_vals["api_key"]:
            cur_map[inferred] = str(active_vals["api_key"])
            app_settings["openai_image_api_keys_by_preset"] = cur_map
            _cfg.app["openai_image_api_keys_by_preset"] = dict(cur_map)

    def _fmt(pk: str) -> str:
        meta = IMAGE_PROVIDER_PRESETS[pk]
        return meta["label_id"] if lang.startswith("id") else meta["label_en"]

    sel_preset = st_module.selectbox(
        t("img_preset_select_label"),
        options=preset_keys,
        index=preset_keys.index(inferred) if inferred in preset_keys else 0,
        format_func=_fmt,
        help=t("img_preset_select_help"),
        key="openai_image_preset_selector",
    )

    cur_sig = _openai_image_config_signature(sel_preset, active_vals)
    prev_sig = st_module.session_state.get("_openai_img_test_sig")
    if prev_sig is not None and prev_sig != cur_sig:
        st_module.session_state.pop("_openai_img_test_result", None)
    st_module.session_state["_openai_img_test_sig"] = cur_sig

    col_p1, col_p2 = st_module.columns(2)
    with col_p1:
        if st_module.button(
            t("btn_apply_image_preset"),
            key="btn_apply_openai_image_preset",
            disabled=(sel_preset == "custom" and inferred == "custom"),
            use_container_width=True,
        ):
            def _save_key_val(k: str, v: Any) -> None:
                _cfg.app[k] = v
                if callable(set_runtime_cfg_fn):
                    set_runtime_cfg_fn("app", k, v)

            applied = apply_image_provider_preset(
                sel_preset,
                app_settings,
                save_fn=_save_key_val,
                runtime_cfg=_cfg.app,
                active_api_key=active_vals.get("api_key") or "",
                previous_preset_key=inferred,
            )
            _save_cfg()
            loaded_key = str(applied.get("loaded_api_key") or "")
            st_module.session_state["openai_image_base_url_input"] = applied["base_url"]
            st_module.session_state["openai_image_model_input"] = applied["model"]
            st_module.session_state["openai_image_size_input"] = applied["size"]
            st_module.session_state["openai_image_api_keys_input"] = loaded_key
            st_module.session_state.pop("_openai_img_test_result", None)
            st_module.session_state["_openai_img_test_sig"] = _openai_image_config_signature(
                sel_preset,
                {
                    "base_url": applied["base_url"] or None,
                    "api_key": loaded_key or None,
                    "model": applied["model"] or None,
                    "image_size": applied["size"] or None,
                },
            )
            st_module.success(t("img_preset_applied_ok", name=_fmt(sel_preset)))
            st_module.rerun()

    with col_p2:
        if st_module.button(
            t("btn_test_single_image"),
            key="btn_test_single_image_settings",
            use_container_width=True,
        ):
            test_vals = resolve_active_openai_image_form_values(
                session_state=st_module.session_state,
                app_settings=app_settings,
                runtime_cfg=_cfg.app,
            )
            with st_module.spinner(t("spinner_testing_single_image")):
                res = test_single_openai_image(
                    base_url=test_vals.get("base_url"),
                    api_key=test_vals.get("api_key"),
                    model=test_vals.get("model"),
                    image_size=test_vals.get("image_size"),
                )
            st_module.session_state["_openai_img_test_result"] = res

    last_res = st_module.session_state.get("_openai_img_test_result")
    if isinstance(last_res, dict):
        if last_res.get("ok"):
            st_module.success(t("img_test_ok", w=last_res["width"], h=last_res["height"], model=last_res["model"]))
            st_module.image(last_res["image_bytes"], width=360)
        elif last_res.get("message"):
            st_module.error(last_res["message"])
