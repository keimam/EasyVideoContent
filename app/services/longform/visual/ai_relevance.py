"""AI Vision relevance checker for Long Video shots (Stage 8B.2, F1).

Evaluates fetched footage thumbnails against narration text and preset subject
using MoneyPrinterTurbo's active vision-capable LLM provider. Results are cached
by asset content hash, rate-limited with concurrency bounds and 429 retry, and
flag mismatches as needs_review=True without replacing any asset.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from app.config import config
from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider
from app.services.llm import (
    _extract_chat_completion_text,
    _normalize_text_response,
    _resolve_provider_field_value,
    _sanitize_error_message,
    _sdk_transport_options,
)
from app.services.longform.hashing import hash_string, normalize_text
from app.services.longform.llm_adapter import (
    LLMAdapterError,
    LLMCreditError,
    LLMEmptyResponseError,
    LLMModelNotFoundError,
    LLMRateLimitError,
    MAX_TOKENS_VISION_RELEVANCE,
    _classify_llm_error,
    extract_sdk_completion_or_raise,
    get_llm_readiness,
    invoke_openai_chat_completion,
    resolve_effective_model,
)
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, Shot
from app.services.longform.store import is_stop_requested
from app.services.longform.visual.cache import get_global_cache_dir
from app.services.longform.visual.contact_sheet import _extract_or_load_thumbnail
from app.utils.request_logging import trace_sdk_call

VISION_MODEL_MARKERS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.5",
    "gpt-5",
    "o1",
    "o3",
    "o4-mini",
    "claude-3",
    "claude-4",
    "claude-sonnet-4",
    "claude-opus-4",
    "gemini-",
    "vision",
    "-vl",
    "vl-",
    "pixtral",
    "llava",
    "gemma-3",
    "llama-4",
    "llama-3.2-11b",
    "llama-3.2-90b",
    "grok-2-vision",
    "grok-4",
    "kimi-k2.5",
    "nova-pro",
    "nova-lite",
    "minicpm-v",
    "internvl",
)

NON_VISION_MODEL_MARKERS = (
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-r1",
    "deepseek-v3",
    "gpt-3.5",
    "openrouter/free",
    "qwen-turbo",
    "qwen-plus",
    "qwen-max",
    "moonshot-v1",
    "kimi-for-coding",
    "text-embedding",
    "embedding-",
)

NON_VISION_ADAPTERS = frozenset({"claude_code", "openai_codex", "openai_chatgpt"})


def _model_supports_vision(provider_id: str, adapter: str, model_name: str) -> bool:
    """Heuristically determine whether the configured MPT LLM model supports image inputs."""
    override = str(config.app.get("llm_vision_override", "") or "").strip().lower()
    if override == "vision":
        return True
    if override == "text":
        return False

    if adapter in NON_VISION_ADAPTERS:
        return False

    m = (model_name or "").strip().lower()
    if not m:
        return False

    if any(tok in m for tok in NON_VISION_MODEL_MARKERS):
        # Explicit "-vl" or "vision" overrides base family token when present
        if "-vl" not in m and "vision" not in m:
            return False

    if adapter == "gemini":
        return not any(tok in m for tok in ("embedding", "imagen", "tts"))

    return any(tok in m for tok in VISION_MODEL_MARKERS)


def get_vision_readiness(provider_id: Optional[str] = None) -> Dict[str, Any]:
    """Check whether the active MPT LLM provider/model is ready and supports vision inputs."""
    readiness = get_llm_readiness(provider_id)
    pid = str(readiness.get("provider") or DEFAULT_LLM_PROVIDER_ID).lower()
    prov_def = get_llm_provider(pid)
    adapter = prov_def.adapter if prov_def else "openai_compatible"
    model = str(readiness.get("model") or "")
    is_ready = bool(readiness.get("configured")) and not bool(readiness.get("is_invalid"))

    if not is_ready:
        return {
            **readiness,
            "ready": False,
            "vision_supported": False,
            "vision_reason": "llm_not_ready",
        }

    supported = _model_supports_vision(pid, adapter, model)
    return {
        **readiness,
        "ready": True,
        "adapter": adapter,
        "vision_supported": supported,
        "vision_reason": "ok" if supported else "model_no_vision",
    }


def _cache_dir(custom_dir: Optional[Path] = None) -> Path:
    root = Path(custom_dir) if custom_dir else get_global_cache_dir()
    path = root / "ai_relevance"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _relevance_cache_key(
    asset_hash: str,
    narration: str,
    subject_context: str,
    provider: str,
    model: str,
) -> str:
    payload = "|".join([
        "f1_v1",
        (asset_hash or "").strip(),
        normalize_text(narration).lower(),
        normalize_text(subject_context).lower(),
        (provider or "").strip().lower(),
        (model or "").strip().lower(),
    ])
    return hash_string(payload)


def _read_relevance_cache(cache_key: str, cache_dir: Optional[Path] = None) -> Optional[Dict[str, str]]:
    path = _cache_dir(cache_dir) / f"{cache_key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("relevan") in ("ya", "tidak", "ragu"):
            return {
                "relevan": str(data["relevan"]),
                "alasan": str(data.get("alasan") or ""),
            }
    except Exception:
        pass
    return None


def _write_relevance_cache(
    cache_key: str,
    verdict: Dict[str, str],
    cache_dir: Optional[Path] = None,
) -> None:
    path = _cache_dir(cache_dir) / f"{cache_key}.json"
    try:
        path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to write AI relevance cache: {exc}")


def _limit_words(text: str, max_words: int = 15) -> str:
    words = [w for w in normalize_text(text).split() if w]
    return " ".join(words[:max_words])


def _parse_vision_verdict(raw_text: str) -> Dict[str, str]:
    text = str(raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    data: Optional[Dict[str, Any]] = None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            data = parsed
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    data = parsed
            except Exception:
                data = None

    if not data:
        low = text.lower()
        if any(w in low for w in ("tidak", "not relevant", "irrelevant", "no")):
            verdict = "tidak"
        elif any(w in low for w in ("ragu", "unsure", "uncertain", "maybe")):
            verdict = "ragu"
        else:
            verdict = "ya"
        return {"relevan": verdict, "alasan": _limit_words(text, 15)}

    raw_rel = str(
        data.get("relevan")
        or data.get("relevant")
        or data.get("status")
        or "ragu"
    ).strip().lower()

    if raw_rel in ("ya", "yes", "true", "relevant", "relevan"):
        rel = "ya"
    elif raw_rel in ("tidak", "no", "false", "irrelevant", "not_relevant"):
        rel = "tidak"
    else:
        rel = "ragu"

    reason = str(data.get("alasan") or data.get("reason") or "").strip()
    return {"relevan": rel, "alasan": _limit_words(reason, 15)}


def _shot_narration_text(chapter: Chapter, shot: Shot) -> str:
    if shot.segment_indices and getattr(chapter, "tts_groups", None):
        parts: List[str] = []
        for g in chapter.tts_groups:
            for seg in getattr(g, "segments", []):
                if seg.segment_index in shot.segment_indices and seg.narration_text:
                    parts.append(seg.narration_text.strip())
        if parts:
            return " ".join(parts)
    return shot.search_query or shot.visual_prompt or ""


def _preset_subject_context(manifest: ProjectManifest) -> str:
    snap = manifest.preset_snapshot or {}
    parts: List[str] = []
    if snap.get("name"):
        parts.append(str(snap["name"]))
    req = snap.get("required_subject_terms") or []
    if req:
        parts.append(", ".join(str(x) for x in req[:6]))
    if snap.get("visual_style"):
        parts.append(str(snap["visual_style"]))
    return " | ".join(parts) or manifest.title


def _extract_thumbnail_jpeg_bytes(shot: Shot, project_dir: Path) -> bytes:
    img, _valid = _extract_or_load_thumbnail(shot, project_dir, thumb_w=400, thumb_h=225)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _build_vision_prompt(narration: str, subject_context: str, search_query: str) -> str:
    return (
        "Evaluate whether this single video frame matches the documentary narration sentence and topic.\n"
        f"Topic/Subject: {subject_context}\n"
        f"Search keywords: {search_query}\n"
        f"Narration sentence: {narration}\n\n"
        'Respond ONLY with JSON: {"relevan": "ya" | "tidak" | "ragu", "alasan": "<brief reason in <= 15 words>"}\n'
        'Use "tidak" if clearly off-topic, wrong subject, placeholder/text card, or unrelated; '
        '"ragu" if ambiguous; "ya" if visually appropriate.'
    )


def _call_vision_provider_once(
    image_bytes: bytes,
    prompt_text: str,
    timeout: float = 45.0,
    max_tokens: int = MAX_TOKENS_VISION_RELEVANCE,
) -> str:
    """Invoke active MPT vision LLM with bounded max_tokens and reasoning minimization (F5/F6)."""
    from openai import AzureOpenAI, OpenAI

    llm_provider = str(config.app.get("llm_provider", DEFAULT_LLM_PROVIDER_ID)).lower()
    prov_def = get_llm_provider(llm_provider)
    if prov_def is None:
        raise LLMAdapterError(f"Unsupported LLM provider: {llm_provider}")

    api_key = str(config.app.get(prov_def.config_key("api_key"), "") or "")
    configured_model = str(config.app.get(prov_def.config_key("model_name"), "") or "")
    model_name, _ = resolve_effective_model(llm_provider, configured_model)
    base_url = prov_def.resolve_base_url(str(config.app.get(prov_def.config_key("base_url"), "") or ""))
    adapter = prov_def.adapter

    if llm_provider == "ollama":
        api_key = "ollama"
        base_url = base_url or config.get_default_ollama_base_url()

    b64 = base64.b64encode(image_bytes).decode("ascii")

    if adapter == "gemini":
        from google import genai
        from google.genai import types

        http_options = types.HttpOptions(base_url=base_url) if base_url else None
        gen_cfg = types.GenerateContentConfig(temperature=0.1, max_output_tokens=int(max_tokens))
        parts = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            prompt_text,
        ]
        with genai.Client(api_key=api_key, http_options=http_options) as client:
            resp = trace_sdk_call(
                llm_provider,
                base_url,
                client.models.generate_content,
                model=model_name,
                contents=parts,
                config=gen_cfg,
            )
        try:
            return _normalize_text_response(getattr(resp, "text", ""), llm_provider)
        except ValueError as exc:
            if "empty text content" in str(exc).lower():
                raise LLMEmptyResponseError(
                    str(exc),
                    finish_reason="length",
                    has_reasoning=False,
                    http_ok=True,
                    max_tokens_used=int(max_tokens),
                ) from exc
            raise

    if adapter == "azure":
        api_version = str(config.app.get(prov_def.config_key("api_version"), "2024-02-15-preview") or "2024-02-15-preview")
        client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=base_url,
            **_sdk_transport_options(timeout, 0),
        )
    else:
        if adapter == "cloudflare_ai_gateway":
            extra = {
                f.config_suffix: _resolve_provider_field_value(
                    config.app.get(prov_def.config_key(f.config_suffix)),
                    f.default_value,
                )
                for f in prov_def.extra_fields
            }
            eff_url = f"https://api.cloudflare.com/client/v4/accounts/{extra['account_id']}/ai/v1"
            headers = {"cf-aig-gateway-id": extra["gateway_id"]}
            client = OpenAI(
                api_key=api_key,
                base_url=eff_url,
                default_headers=headers,
                **_sdk_transport_options(timeout, 0),
            )
        else:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                **_sdk_transport_options(timeout, 0),
            )

    try:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
                    },
                ],
            }
        ]
        resp = invoke_openai_chat_completion(
            client.chat.completions.create,
            llm_provider,
            base_url=base_url,
            minimize_reasoning=True,
            model=model_name,
            messages=messages,
            temperature=0.1,
            max_tokens=int(max_tokens),
        )
        return extract_sdk_completion_or_raise(
            resp, llm_provider, max_tokens_used=int(max_tokens)
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _call_vision_with_retry(
    image_bytes: bytes,
    prompt_text: str,
    model_name: str,
    *,
    max_retries: int = 3,
    transport_fn: Optional[Callable[[bytes, str], str]] = None,
) -> str:
    last_err: Optional[Exception] = None
    cur_max_tokens = MAX_TOKENS_VISION_RELEVANCE
    escalated_once = False
    for attempt in range(max_retries):
        try:
            if transport_fn is not None:
                return transport_fn(image_bytes, prompt_text)
            return _call_vision_provider_once(
                image_bytes, prompt_text, max_tokens=cur_max_tokens
            )
        except LLMEmptyResponseError as exc:
            last_err = exc
            if (exc.finish_reason == "length" or exc.has_reasoning) and not escalated_once:
                escalated_once = True
                cur_max_tokens = min(1024, max(cur_max_tokens * 2, 512))
                logger.info(
                    f"Vision call empty ({exc.finish_reason}); retrying once with max_tokens={cur_max_tokens}"
                )
                continue
            raise
        except Exception as exc:
            msg = _sanitize_error_message(exc)
            classified = _classify_llm_error(msg, model=model_name)
            last_err = classified or exc
            if isinstance(classified, (LLMCreditError, LLMModelNotFoundError)):
                raise classified
            is_rate = isinstance(classified, LLMRateLimitError) or "429" in msg or "rate limit" in msg.lower()
            if is_rate and attempt < max_retries - 1:
                delay = min(1.5 * (2 ** attempt), 8.0)
                logger.warning(f"Vision rate limited (attempt {attempt + 1}/{max_retries}); retrying in {delay:.1f}s")
                time.sleep(delay)
                continue
            raise last_err
    raise last_err or LLMAdapterError("Vision evaluation failed")


def _apply_verdict_to_shot(shot: Shot, verdict: Dict[str, str], clear_on_yes: bool = False) -> None:
    rel = verdict["relevan"]
    shot.ai_relevance_status = rel
    shot.ai_relevance_reason = _limit_words(verdict.get("alasan", ""), 15)
    shot.ai_relevance_asset_hash = shot.asset_content_hash
    if rel in ("tidak", "ragu"):
        shot.needs_review = True
    elif clear_on_yes and rel == "ya":
        shot.needs_review = False


def check_shot_relevance(
    shot: Shot,
    chapter: Chapter,
    project_dir: Path | str,
    manifest: ProjectManifest,
    *,
    force_refresh: bool = False,
    clear_on_yes: bool = False,
    cache_dir: Optional[Path] = None,
    transport_fn: Optional[Callable[[bytes, str], str]] = None,
) -> Dict[str, Any]:
    """Evaluate a single shot's visual relevance using cached or active Vision LLM."""
    p_dir = Path(project_dir)
    if not shot.asset_path or not (p_dir / shot.asset_path).is_file():
        raise FileNotFoundError(f"Shot {shot.shot_id} has no asset on disk to inspect.")

    readiness = get_vision_readiness()
    if transport_fn is None and not readiness.get("vision_supported"):
        raise LLMAdapterError(
            "Active model does not support vision inputs; choose a vision model in Settings."
        )

    provider = str(readiness.get("provider") or "")
    model = str(readiness.get("model") or "")
    narration = _shot_narration_text(chapter, shot)
    subject_ctx = _preset_subject_context(manifest)
    asset_hash = shot.asset_content_hash or ""
    c_key = _relevance_cache_key(asset_hash, narration, subject_ctx, provider, model)

    if not force_refresh and asset_hash:
        cached = _read_relevance_cache(c_key, cache_dir=cache_dir)
        if cached is not None:
            _apply_verdict_to_shot(shot, cached, clear_on_yes=clear_on_yes)
            return {"verdict": cached, "cached": True}

    img_bytes = _extract_thumbnail_jpeg_bytes(shot, p_dir)
    prompt_text = _build_vision_prompt(
        narration=narration,
        subject_context=subject_ctx,
        search_query=shot.search_query_en or shot.search_query or shot.visual_prompt or "",
    )
    raw_resp = _call_vision_with_retry(
        img_bytes,
        prompt_text,
        model_name=model,
        transport_fn=transport_fn,
    )
    verdict = _parse_vision_verdict(raw_resp)
    if asset_hash:
        _write_relevance_cache(c_key, verdict, cache_dir=cache_dir)
    _apply_verdict_to_shot(shot, verdict, clear_on_yes=clear_on_yes)
    return {"verdict": verdict, "cached": False}


def estimate_relevance_check_calls(
    manifest: ProjectManifest,
    *,
    cache_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Count unlocked shots that require fresh vision API calls vs cached shots."""
    readiness = get_vision_readiness()
    provider = str(readiness.get("provider") or "")
    model = str(readiness.get("model") or "")
    subject_ctx = _preset_subject_context(manifest)

    total_eligible = 0
    uncached = 0
    cached_count = 0
    for ch in manifest.chapters:
        for s in ch.shots:
            if s.locked:
                continue
            total_eligible += 1
            if s.asset_content_hash:
                c_key = _relevance_cache_key(
                    s.asset_content_hash,
                    _shot_narration_text(ch, s),
                    subject_ctx,
                    provider,
                    model,
                )
                if _read_relevance_cache(c_key, cache_dir=cache_dir) is not None:
                    cached_count += 1
                    continue
            uncached += 1
    return {
        "total_eligible": total_eligible,
        "estimated_calls": uncached,
        "cached": cached_count,
    }


def check_project_relevance(
    manifest: ProjectManifest,
    project_dir: Path | str,
    *,
    max_concurrency: int = 3,
    force_refresh: bool = False,
    cache_dir: Optional[Path] = None,
    transport_fn: Optional[Callable[[bytes, str], str]] = None,
) -> Dict[str, Any]:
    """Check relevance across all unlocked shots with assets, bounded concurrency, and caching."""
    p_dir = Path(project_dir)
    readiness = get_vision_readiness()
    if transport_fn is None and not readiness.get("vision_supported"):
        return {
            "checked": 0,
            "cached": 0,
            "ya": 0,
            "tidak": 0,
            "ragu": 0,
            "errors": ["Active model does not support images; choose a vision model in Settings."],
        }

    provider = str(readiness.get("provider") or "")
    model = str(readiness.get("model") or "")
    subject_ctx = _preset_subject_context(manifest)

    tasks: List[Tuple[Shot, Chapter, str, str]] = []
    cached_hits = 0
    counts = {"ya": 0, "tidak": 0, "ragu": 0}
    errors: List[str] = []

    for ch in manifest.chapters:
        for shot in ch.shots:
            if shot.locked or not shot.asset_path:
                continue
            asset_file = p_dir / shot.asset_path
            if not asset_file.is_file():
                continue
            narration = _shot_narration_text(ch, shot)
            asset_hash = shot.asset_content_hash or ""
            c_key = _relevance_cache_key(asset_hash, narration, subject_ctx, provider, model)
            if not force_refresh and asset_hash:
                cached = _read_relevance_cache(c_key, cache_dir=cache_dir)
                if cached is not None:
                    _apply_verdict_to_shot(shot, cached)
                    cached_hits += 1
                    counts[cached["relevan"]] += 1
                    continue
            tasks.append((shot, ch, c_key, narration))

    if not tasks:
        return {"checked": 0, "cached": cached_hits, **counts, "errors": errors}

    # Pre-extract JPEG bytes sequentially to avoid ffmpeg contention inside worker threads
    prepared: List[Tuple[Shot, str, str, bytes]] = []
    for shot, _ch, c_key, narration in tasks:
        if is_stop_requested(p_dir):
            break
        try:
            img_bytes = _extract_thumbnail_jpeg_bytes(shot, p_dir)
            prompt_text = _build_vision_prompt(
                narration=narration,
                subject_context=subject_ctx,
                search_query=shot.search_query_en or shot.search_query or shot.visual_prompt or "",
            )
            prepared.append((shot, c_key, prompt_text, img_bytes))
        except Exception as exc:
            errors.append(f"{shot.shot_id}: {exc}")

    checked = 0
    workers = max(1, min(int(max_concurrency), 4))

    def _run_one(item: Tuple[Shot, str, str, bytes]) -> Tuple[Shot, str, Dict[str, str]]:
        sh, ck, pr, jb = item
        raw_resp = _call_vision_with_retry(
            jb,
            pr,
            model_name=model,
            transport_fn=transport_fn,
        )
        return sh, ck, _parse_vision_verdict(raw_resp)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_run_one, item): item[0] for item in prepared}
        for fut in as_completed(future_map):
            sh_ref = future_map[fut]
            try:
                shot, c_key, verdict = fut.result()
                if shot.asset_content_hash:
                    _write_relevance_cache(c_key, verdict, cache_dir=cache_dir)
                _apply_verdict_to_shot(shot, verdict)
                checked += 1
                counts[verdict["relevan"]] += 1
            except Exception as exc:
                errors.append(f"{sh_ref.shot_id}: {exc}")

    return {"checked": checked, "cached": cached_hits, **counts, "errors": errors}
