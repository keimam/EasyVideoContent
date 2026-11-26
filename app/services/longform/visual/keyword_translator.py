"""LLM-powered English keyword translation for stock footage search (Stage 8B.2, F2).

For non-English projects, short stock keywords extracted from narration (X3)
are batch-translated per chapter into concise English search phrases via MPT's
active LLM provider, cached locally, and gracefully fall back to original
keywords when no LLM is configured.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from app.services.longform.hashing import hash_string, normalize_text
from app.services.longform.language_guard import normalize_language_code
from app.services.longform.llm_adapter import LLMAdapter, get_llm_readiness
from app.services.longform.visual.cache import get_global_cache_dir

_LAST_LLM_CALL_META = threading.local()


def get_last_translation_llm_meta() -> Dict[str, Any]:
    """Return metadata (failover_used, providers_tried, provider_errors, attempts_summary) from latest _invoke_translation_llm call on this thread."""
    meta = getattr(_LAST_LLM_CALL_META, "data", None)
    return dict(meta) if isinstance(meta, dict) else {}


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


def _invoke_translation_llm(
    prompt: str,
    llm_adapter: Optional[LLMAdapter] = None,
    max_tokens: int = 400,
    *,
    expect_json: bool = False,
    json_validator: Optional[Callable[[str], Any]] = None,
    primary_provider: Optional[str] = None,
    failover_chain: Optional[Sequence[str]] = None,
) -> Tuple[str, str, str]:
    """Call active LLM (with configured failover chain & minimal-reasoning JSON retry) with bounded max_tokens."""
    from app.services.longform.llm_adapter import call_llm_with_failover

    resp = call_llm_with_failover(
        prompt=prompt,
        primary_provider=primary_provider,
        failover_chain=failover_chain,
        llm_adapter=llm_adapter,
        force_refresh=True,
        max_tokens=max_tokens,
        minimize_reasoning=True,
        expect_json=expect_json,
        json_validator=json_validator,
    )
    _LAST_LLM_CALL_META.data = {
        "provider": resp.provider,
        "model": resp.model,
        "failover_used": bool(getattr(resp, "failover_used", False)),
        "providers_tried": list(getattr(resp, "providers_tried", None) or [resp.provider]),
        "provider_errors": dict(getattr(resp, "provider_errors", None) or {}),
        "attempts_summary": str(getattr(resp, "attempts_summary", "") or ""),
    }
    return resp.content, resp.provider, resp.model


def call_translation_llm_json(
    invoke_fn: Callable[..., Tuple[str, str, str]],
    prompt: str,
    *,
    llm_adapter: Optional[LLMAdapter] = None,
    max_tokens: int = 400,
    json_validator: Optional[Callable[[str], Any]] = None,
    primary_provider: Optional[str] = None,
    failover_chain: Optional[Sequence[str]] = None,
) -> Tuple[str, str, str]:
    """Call `_invoke_translation_llm` with JSON retry/failover kwargs when unpatched, preserving 3-arg mock compatibility."""
    if invoke_fn is _invoke_translation_llm:
        return invoke_fn(
            prompt,
            llm_adapter=llm_adapter,
            max_tokens=max_tokens,
            expect_json=True,
            json_validator=json_validator,
            primary_provider=primary_provider,
            failover_chain=failover_chain,
        )
    return invoke_fn(prompt, llm_adapter=llm_adapter, max_tokens=max_tokens)


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

    batch_max_tokens = min(1600, max(320, 45 * len(pending_uncached) + 200))
    try:
        content, used_prov, used_model = call_translation_llm_json(
            _invoke_translation_llm,
            prompt,
            llm_adapter=llm_adapter,
            max_tokens=batch_max_tokens,
            json_validator=lambda txt: bool(_extract_json_dict(txt)),
        )
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
        content, used_prov, used_model = call_translation_llm_json(
            _invoke_translation_llm,
            prompt,
            llm_adapter=llm_adapter,
            max_tokens=320,
            json_validator=lambda txt: bool(_extract_json_dict(txt)),
        )
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
