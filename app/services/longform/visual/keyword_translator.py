"""LLM-powered English keyword translation for stock footage search (Stage 8B.2, F2).

For non-English projects, short stock keywords extracted from narration (X3)
are batch-translated per chapter into concise English search phrases via MPT's
active LLM provider, cached locally, and gracefully fall back to original
keywords when no LLM is configured.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.longform.hashing import hash_string, normalize_text
from app.services.longform.language_guard import normalize_language_code
from app.services.longform.llm_adapter import LLMAdapter, get_llm_readiness
from app.services.longform.visual.cache import get_global_cache_dir


def is_non_english_language(lang_code: Optional[str]) -> bool:
    """Return True if project language is non-English (e.g. 'id', 'id-ID')."""
    return normalize_language_code(lang_code) != "en"


def _cache_file(cache_dir: Optional[Path] = None) -> Path:
    root = Path(cache_dir) if cache_dir else get_global_cache_dir()
    path = root / "query_translations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_translation_cache(cache_dir: Optional[Path] = None) -> Dict[str, Any]:
    path = _cache_file(cache_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_translation_cache(cache: Dict[str, Any], cache_dir: Optional[Path] = None) -> None:
    path = _cache_file(cache_dir)
    try:
        path.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to persist query translation cache: {exc}")


def _cache_key(source_lang: str, raw_query: str) -> str:
    norm_lang = normalize_language_code(source_lang)
    norm_q = normalize_text(raw_query).lower()
    return hash_string(f"f2_en|{norm_lang}|{norm_q}")


def _sanitize_english_query(raw: str, max_words: int = 6) -> str:
    cleaned = re.sub(r"[\"'`\[\]\(\)]+", " ", str(raw or ""))
    cleaned = re.sub(r"[,.;:!?]+", " ", cleaned)
    words = [w for w in cleaned.split() if w]
    if len(words) > max_words:
        words = words[:max_words]
    return " ".join(words).strip()


def _extract_json_dict(raw_text: str) -> Dict[str, str]:
    text = str(raw_text or "").strip()
    if not text:
        return {}
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if v}
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items() if v}
        except Exception:
            pass
    return {}


def _build_batch_prompt(
    items: List[Tuple[str, str]],
    source_lang: str,
    preset_snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    snap = preset_snapshot or {}
    subj_terms = snap.get("required_subject_terms") or snap.get("topic_keywords") or []
    subj_hint = ", ".join(str(x) for x in subj_terms[:5]) if subj_terms else ""
    payload = [{"id": sid, "query": q} for sid, q in items]
    return (
        "Translate each short documentary stock-footage search query from "
        f"language '{source_lang}' into concise 3-6 word English stock search keywords "
        "(suitable for Pixabay/Pexels/Coverr video search).\n"
        + (f"Documentary subject context: {subj_hint}\n" if subj_hint else "")
        + "Rules:\n"
        "- Return ONLY a valid JSON object mapping each shot 'id' to its English keyword phrase.\n"
        "- Keep visual nouns concrete (no punctuation, no sentences, 2-6 English words per item).\n"
        f"Items:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _invoke_translation_llm(prompt: str, llm_adapter: Optional[LLMAdapter] = None) -> Tuple[str, str, str]:
    """Call active LLM cleanly with bounded max_tokens (prevents 402 credit over-reservation)."""
    if llm_adapter is not None:
        resp = llm_adapter.generate(prompt=prompt)
        return resp.content, resp.provider, resp.model

    import time
    from app.config import config
    from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider
    from app.services.llm import (
        _extract_chat_completion_text,
        _normalize_text_response,
        _resolve_provider_field_value,
        _sanitize_error_message,
        _sdk_transport_options,
    )
    from app.services.longform.llm_adapter import LLMRateLimitError, _classify_llm_error
    from app.utils.request_logging import trace_sdk_call

    pid = str(config.app.get("llm_provider", DEFAULT_LLM_PROVIDER_ID) or DEFAULT_LLM_PROVIDER_ID).lower()
    prov_def = get_llm_provider(pid)
    adapter = prov_def.adapter if prov_def else "openai_compatible"
    model_name = prov_def.resolve_model_name(str(config.app.get(prov_def.config_key("model_name"), "") or "")) if prov_def else ""

    if adapter in ("claude_code", "openai_codex", "openai_chatgpt", "kimi_code", "litellm", "qwen"):
        resp = LLMAdapter().generate(prompt=prompt)
        return resp.content, resp.provider, resp.model

    api_key = str(config.app.get(prov_def.config_key("api_key"), "") or "") if prov_def else ""
    base_url = prov_def.resolve_base_url(str(config.app.get(prov_def.config_key("base_url"), "") or "")) if prov_def else ""
    if pid == "ollama":
        api_key = "ollama"
        base_url = base_url or config.get_default_ollama_base_url()

    for attempt in range(3):
        try:
            if adapter == "gemini":
                from google import genai
                from google.genai import types

                http_options = types.HttpOptions(base_url=base_url) if base_url else None
                cfg = types.GenerateContentConfig(temperature=0.2, max_output_tokens=600)
                with genai.Client(api_key=api_key, http_options=http_options) as client:
                    r = trace_sdk_call(
                        pid, base_url, client.models.generate_content, model=model_name, contents=prompt, config=cfg
                    )
                return _normalize_text_response(getattr(r, "text", ""), pid), pid, model_name

            from openai import AzureOpenAI, OpenAI
            from openai.types.chat import ChatCompletion

            if adapter == "azure":
                api_ver = str(config.app.get(prov_def.config_key("api_version"), "2024-02-15-preview") or "2024-02-15-preview")
                client = AzureOpenAI(
                    api_key=api_key, api_version=api_ver, azure_endpoint=base_url, **_sdk_transport_options(35.0, 0)
                )
            elif adapter == "cloudflare_ai_gateway":
                extra = {
                    f.config_suffix: _resolve_provider_field_value(
                        config.app.get(prov_def.config_key(f.config_suffix)), f.default_value
                    )
                    for f in prov_def.extra_fields
                }
                client = OpenAI(
                    api_key=api_key,
                    base_url=f"https://api.cloudflare.com/client/v4/accounts/{extra['account_id']}/ai/v1",
                    default_headers={"cf-aig-gateway-id": extra["gateway_id"]},
                    **_sdk_transport_options(35.0, 0),
                )
            else:
                client = OpenAI(api_key=api_key, base_url=base_url, **_sdk_transport_options(35.0, 0))

            try:
                r = trace_sdk_call(
                    pid,
                    base_url,
                    client.chat.completions.create,
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=600,
                )
                if r and isinstance(r, ChatCompletion):
                    return _extract_chat_completion_text(r, pid), pid, model_name
                raise ValueError(f"[{pid}] invalid response")
            finally:
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
        except Exception as exc:
            msg = _sanitize_error_message(exc)
            classified = _classify_llm_error(msg, model=model_name)
            is_rate = isinstance(classified, LLMRateLimitError) or "429" in msg or "rate limit" in msg.lower()
            if is_rate and attempt < 2:
                time.sleep(min(1.5 * (2 ** attempt), 6.0))
                continue
            raise


def translate_chapter_queries_to_english(
    shots: List[Any],
    preset_snapshot: Optional[Dict[str, Any]] = None,
    project_lang: str = "id-ID",
    *,
    cache_dir: Optional[Path] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Batch-translate a chapter's unlocked shot search queries into English.

    Uses local cache first and issues at most ONE batched LLM request per chapter
    for uncached queries. Falls back gracefully to original queries if LLM is
    not configured or fails.
    """
    if not is_non_english_language(project_lang):
        return {"translated": 0, "cached": 0, "llm_calls": 0, "warning": None}

    norm_lang = normalize_language_code(project_lang)
    cache = _load_translation_cache(cache_dir)

    pending_uncached: List[Tuple[Any, str, str]] = []
    cached_hits = 0

    for shot in shots:
        if getattr(shot, "locked", False):
            continue
        if not force and getattr(shot, "search_query_en", None):
            continue
        raw_q = normalize_text(getattr(shot, "search_query", "") or getattr(shot, "visual_prompt", ""))
        if not raw_q:
            continue
        c_key = _cache_key(norm_lang, raw_q)
        cached_entry = cache.get(c_key) if not force else None
        if isinstance(cached_entry, dict) and cached_entry.get("en"):
            shot.search_query_en = _sanitize_english_query(cached_entry["en"]) or raw_q
            cached_hits += 1
        else:
            pending_uncached.append((shot, raw_q, c_key))

    if not pending_uncached:
        return {"translated": 0, "cached": cached_hits, "llm_calls": 0, "warning": None}

    readiness = get_llm_readiness()
    is_ready = bool(readiness.get("configured")) and not bool(readiness.get("is_invalid"))
    if llm_adapter is None and not is_ready:
        warn = "LLM not configured; using original search keywords without English translation."
        logger.warning(f"F2 translation skipped: {warn}")
        return {"translated": 0, "cached": cached_hits, "llm_calls": 0, "warning": warn}

    prompt = _build_batch_prompt(
        [(s.shot_id, q) for s, q, _ in pending_uncached],
        source_lang=norm_lang,
        preset_snapshot=preset_snapshot,
    )

    try:
        content, used_prov, used_model = _invoke_translation_llm(prompt, llm_adapter=llm_adapter)
        mapping = _extract_json_dict(content)
        translated_count = 0
        for shot, raw_q, c_key in pending_uncached:
            en_raw = mapping.get(shot.shot_id)
            if en_raw:
                en_clean = _sanitize_english_query(en_raw)
                if en_clean:
                    shot.search_query_en = en_clean
                    cache[c_key] = {
                        "lang": norm_lang,
                        "src": raw_q,
                        "en": en_clean,
                        "provider": used_prov,
                        "model": used_model,
                    }
                    translated_count += 1
        if translated_count > 0:
            _save_translation_cache(cache, cache_dir)
        return {
            "translated": translated_count,
            "cached": cached_hits,
            "llm_calls": 1,
            "warning": None if translated_count > 0 else "Empty translation mapping; kept original keywords.",
        }
    except Exception as exc:
        warn = f"Keyword translation fallback to original ({type(exc).__name__}): {exc}"
        logger.warning(warn)
        return {"translated": 0, "cached": cached_hits, "llm_calls": 1, "warning": warn}


def translate_single_query_to_english(
    query: str,
    preset_snapshot: Optional[Dict[str, Any]] = None,
    project_lang: str = "id-ID",
    *,
    cache_dir: Optional[Path] = None,
    llm_adapter: Optional[LLMAdapter] = None,
) -> Tuple[str, Optional[str]]:
    """Translate one manual search query into English with cache & safe fallback."""
    clean_q = normalize_text(query)
    if not clean_q or not is_non_english_language(project_lang):
        return clean_q, None

    norm_lang = normalize_language_code(project_lang)
    cache = _load_translation_cache(cache_dir)
    c_key = _cache_key(norm_lang, clean_q)
    cached = cache.get(c_key)
    if isinstance(cached, dict) and cached.get("en"):
        return _sanitize_english_query(cached["en"]) or clean_q, None

    readiness = get_llm_readiness()
    is_ready = bool(readiness.get("configured")) and not bool(readiness.get("is_invalid"))
    if llm_adapter is None and not is_ready:
        return clean_q, "LLM not configured; kept original query."

    prompt = _build_batch_prompt(
        [("q1", clean_q)],
        source_lang=norm_lang,
        preset_snapshot=preset_snapshot,
    )
    try:
        content, used_prov, used_model = _invoke_translation_llm(prompt, llm_adapter=llm_adapter)
        mapping = _extract_json_dict(content)
        en_clean = _sanitize_english_query(mapping.get("q1", ""))
        if en_clean:
            cache[c_key] = {
                "lang": norm_lang,
                "src": clean_q,
                "en": en_clean,
                "provider": used_prov,
                "model": used_model,
            }
            _save_translation_cache(cache, cache_dir)
            return en_clean, None
        return clean_q, "Translation returned empty; kept original query."
    except Exception as exc:
        return clean_q, f"Translation fallback ({type(exc).__name__}): {exc}"
