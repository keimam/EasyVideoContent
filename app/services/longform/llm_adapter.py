"""Unified LLM Adapter for Longform Video Generation.

Routes all LLM requests through a single adapter (2.1), supporting:
- Upstream MPT LLM providers configured in config.toml (calling upstream_llm._generate_response)
- Deterministic 'fake' provider for zero-cost offline development and automated unit testing
- File-based atomic caching per prompt in the project directory
- Timeout and bounded exponential retry with sanitized error messages (no leaked API keys)
- Call duration and token tracking (when provided by upstream)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.config import config
from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider

OPENROUTER_SAFE_DEFAULT_MODEL = "openrouter/free"
MAX_TOKENS_TEST_CONNECTION = 256
MAX_TOKENS_VISION_RELEVANCE = 256

# Cached OpenRouter /models pricing classification & metadata keyed by lowercase model id
_MODEL_PRICING_CACHE: Dict[str, str] = {}
_OPENROUTER_MODEL_META_CACHE: Dict[str, Dict[str, Any]] = {}

# Regex to sanitize sensitive keys from error messages (named params + raw OpenRouter/OpenAI/Groq/Gemini prefixes)
_SENSITIVE_KEY_RE = re.compile(
    r"((?:api[_-]?key|access[_-]?token|secret|password|bearer\s+)[=:\s]+)([a-zA-Z0-9_\-\.]{8,})",
    re.IGNORECASE,
)
_RAW_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(sk-(?:or-|proj-|ant-)?[a-zA-Z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{12,}|gsk_[0-9A-Za-z_\-]{12,})"
)


def sanitize_error(msg: str) -> str:
    """Mask potential API keys, secrets, user IDs, or embedded raw credentials in error messages."""
    masked = _SENSITIVE_KEY_RE.sub(r"\1***REDACTED***", str(msg))
    masked = _RAW_TOKEN_RE.sub("***REDACTED***", masked)
    masked = re.sub(
        r"""(['"]?user[_-]?id['"]?\s*[:=]\s*)(['"]?)[^'",}\s]+\2""",
        r"\1\2***REDACTED***\2",
        masked,
        flags=re.IGNORECASE,
    )
    return masked


def _short_tz_label(dt_local: datetime) -> str:
    raw_tz = str(dt_local.tzname() or "").strip()
    low = raw_tz.lower()
    if "se asia" in low or raw_tz in ("WIB", "ICT", "+07"):
        return "WIB"
    if "central indonesia" in low or raw_tz == "WITA":
        return "WITA"
    if "eastern indonesia" in low or raw_tz == "WIT":
        return "WIT"
    return raw_tz or "Lokal"


def format_concise_llm_error(exc_or_msg: Any, provider_id: str = "") -> str:
    """Summarize raw LLM provider errors (especially OpenRouter 429 JSON with user_id/URLs) into concise user-safe text (GA1.2)."""
    raw = sanitize_error(str(exc_or_msg or ""))
    pid = (provider_id or "").strip().lower()
    prov_def = get_llm_provider(pid) if pid else None
    label = (prov_def.default_label if prov_def else None) or (pid.capitalize() if pid else "AI")
    low = raw.lower()
    if not pid and ("openrouter" in low or "free-models-per-day" in low):
        label = "OpenRouter"

    if (
        isinstance(exc_or_msg, LLMDailyQuotaError)
        or (isinstance(exc_or_msg, LLMRateLimitError) and getattr(exc_or_msg, "is_daily", False))
        or "free-models-per-day" in low
        or "requests per day" in low
        or "daily quota" in low
    ):
        try:
            dt_local = datetime.fromtimestamp(_next_utc_midnight_ts(), tz=timezone.utc).astimezone()
            reset_str = f"{dt_local.strftime('%H:%M')} {_short_tz_label(dt_local)}"
        except Exception:
            reset_str = "00:00 UTC"
        return f"{label}: kuota harian habis (reset {reset_str})"

    if isinstance(exc_or_msg, LLMCreditError) or "402" in low or "insufficient credit" in low:
        return f"{label}: saldo/kredit habis (402)"
    if isinstance(exc_or_msg, LLMAuthError) or "401" in low or "403" in low or "unauthorized" in low or "invalid api key" in low:
        return f"{label}: API key ditolak server (401/403)"
    if isinstance(exc_or_msg, LLMModelNotFoundError) or "404" in low or "no endpoints found" in low:
        return f"{label}: model tidak ditemukan (404)"

    # Strip long URLs & raw dict error wrappers for cleaner logs/warnings
    cleaned = re.sub(r"https?://\S+", "", raw)
    cleaned = re.sub(r"\{\s*['\"]error['\"][\s\S]*$", "", cleaned).strip()
    if not cleaned:
        cleaned = raw.splitlines()[0]
    cleaned = " ".join(cleaned.split())
    return cleaned[:160]


def resolve_effective_model(provider_id: str, configured_model: Optional[str]) -> Tuple[str, bool]:
    """Resolve model name for Long Video, substituting a working OpenRouter free model when empty."""
    pid = (provider_id or "").strip().lower()
    raw = (configured_model or "").strip()
    if pid == "openrouter" and not raw:
        return OPENROUTER_SAFE_DEFAULT_MODEL, True
    prov_def = get_llm_provider(pid)
    if prov_def:
        resolved = prov_def.resolve_model_name(raw)
        if pid == "openrouter" and resolved == "minimax/minimax-m3:free" and not raw:
            return OPENROUTER_SAFE_DEFAULT_MODEL, True
        return resolved, False
    return raw, False


def resolve_model_billing_tier(provider_id: str, model_id: Optional[str]) -> str:
    """Classify model as 'free', 'paid', 'subscription', or 'unknown' when provider info exists."""
    pid = (provider_id or "").strip().lower()
    mid = (model_id or "").strip().lower()
    if pid == "ollama":
        return "free"
    if pid in ("openai_codex", "openai_chatgpt", "claude_code", "kimi_code"):
        return "subscription"
    if pid == "openrouter":
        if mid in _MODEL_PRICING_CACHE:
            return _MODEL_PRICING_CACHE[mid]
        if mid == "openrouter/free" or mid.endswith(":free"):
            return "free"
        if mid:
            return "paid"
        return "unknown"
    if pid in ("openai", "anthropic", "deepseek", "moonshot", "qwen", "azure", "grok", "minimax"):
        return "paid" if mid else "unknown"
    return "unknown"


def compute_script_max_tokens(kind: str, target_words: int = 0) -> int:
    """Compute bounded reasonable max_tokens for each Long Video LLM call type (F5)."""
    k = (kind or "default").strip().lower()
    if k in ("test", "test_connection", "ping"):
        return MAX_TOKENS_TEST_CONNECTION
    if k in ("vision", "ai_relevance", "relevance"):
        return MAX_TOKENS_VISION_RELEVANCE
    if k == "summary":
        return 320
    if k == "outline":
        return min(1600, max(700, int(max(target_words, 0) * 0.25) + 800))
    if k in ("chapter", "revise_length", "revise", "rewrite"):
        return min(4096, max(800, int(max(target_words, 0) * 2.5) + 400))
    return 2048


@dataclass
class LLMResponse:
    """Structured response from LLMAdapter."""

    content: str
    provider: str
    model: str
    duration_seconds: float
    tokens: Optional[Dict[str, int]] = None
    cache_hit: bool = False
    max_tokens_requested: Optional[int] = None
    failover_used: bool = False
    providers_tried: Optional[List[str]] = None
    provider_errors: Optional[Dict[str, str]] = None
    attempts_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LLMAdapterError(Exception):
    """Raised when an LLM call fails after retries."""
    pass


class LLMConfigurationError(LLMAdapterError):
    """Raised when an LLM provider lacks required configuration (e.g. missing API key)."""
    pass


class LLMAuthError(LLMAdapterError):
    """Raised on HTTP 401 / invalid or revoked API key (never retried)."""
    pass


class LLMRateLimitError(LLMAdapterError):
    """Raised when the active LLM provider returns HTTP 429 / rate limit exhaustion."""

    def __init__(
        self,
        message: str,
        *,
        is_daily: bool = False,
        retry_after_seconds: Optional[float] = None,
        reset_hint: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.is_daily = bool(is_daily)
        self.retry_after_seconds = retry_after_seconds
        self.reset_hint = reset_hint


class LLMDailyQuotaError(LLMRateLimitError):
    """Raised when provider daily free request quota (429 per-day) is exhausted (never retried)."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: Optional[float] = None,
        reset_hint: Optional[str] = None,
    ) -> None:
        super().__init__(
            message,
            is_daily=True,
            retry_after_seconds=retry_after_seconds,
            reset_hint=reset_hint,
        )


class LLMCreditError(LLMAdapterError):
    """Raised on HTTP 402 / insufficient provider credit (never automatically retried)."""
    pass


class LLMModelNotFoundError(LLMAdapterError):
    """Raised when the configured LLM model is not found, deprecated, or retired."""

    def __init__(self, message: str, model: str = "") -> None:
        super().__init__(message)
        self.model = model


class LLMEmptyResponseError(LLMAdapterError):
    """Raised when HTTP succeeds (200) yet no visible text remains after reasoning/finish checks."""

    def __init__(
        self,
        message: str,
        *,
        finish_reason: str = "",
        has_reasoning: bool = False,
        http_ok: bool = True,
        max_tokens_used: int = 0,
    ) -> None:
        super().__init__(message)
        self.finish_reason = (finish_reason or "").strip().lower()
        self.has_reasoning = bool(has_reasoning)
        self.http_ok = bool(http_ok)
        self.max_tokens_used = int(max_tokens_used or 0)


def _supports_kwarg(fn: Any, kwarg_name: str) -> bool:
    """Return True if callable accepts kwarg_name or **kwargs (safe for narrow test mocks)."""
    import inspect

    try:
        sig = inspect.signature(fn)
    except Exception:
        return True
    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD or param.name == kwarg_name:
            return True
    return False


def build_reasoning_control_kwargs(
    provider_id: str,
    create_fn: Any = None,
    *,
    minimize_reasoning: bool = True,
) -> Dict[str, Any]:
    """Build OpenRouter reasoning-minimization extra_body kwargs when appropriate (F6)."""
    if not minimize_reasoning:
        return {}
    pid = (provider_id or "").strip().lower()
    if pid != "openrouter":
        return {}
    if create_fn is not None and not _supports_kwarg(create_fn, "extra_body"):
        return {}
    return {"extra_body": {"reasoning": {"enabled": False}}}


def invoke_openai_chat_completion(
    create_fn: Any,
    provider_id: str,
    *,
    base_url: str = "",
    minimize_reasoning: bool = True,
    **call_kwargs: Any,
) -> Any:
    """Invoke OpenAI-compatible chat.completions.create with OpenRouter reasoning control & safe 400 fallback."""
    from app.utils.request_logging import trace_sdk_call

    extra_kwargs = build_reasoning_control_kwargs(
        provider_id, create_fn, minimize_reasoning=minimize_reasoning
    )
    try:
        return trace_sdk_call(
            provider_id,
            base_url,
            create_fn,
            **call_kwargs,
            **extra_kwargs,
        )
    except Exception as exc:
        if extra_kwargs:
            low = str(exc).lower()
            if "reasoning" in low or "extra_body" in low or "unrecognized" in low or "unknown field" in low:
                # Some reasoning models reject enabled=False yet accept effort="low" before full removal
                try:
                    return trace_sdk_call(
                        provider_id,
                        base_url,
                        create_fn,
                        **call_kwargs,
                        extra_body={"reasoning": {"effort": "low"}},
                    )
                except Exception as exc2:
                    low2 = str(exc2).lower()
                    if "reasoning" in low2 or "extra_body" in low2 or "unrecognized" in low2 or "unknown field" in low2:
                        return trace_sdk_call(
                            provider_id,
                            base_url,
                            create_fn,
                            **call_kwargs,
                        )
                    raise
        raise


def extract_sdk_completion_or_raise(
    response: Any,
    provider_id: str,
    *,
    max_tokens_used: int = 0,
) -> str:
    """Extract normalized completion text or raise LLMEmptyResponseError with finish/reasoning metadata."""
    from openai.types.chat import ChatCompletion
    from app.services.llm import _extract_chat_completion_text, _normalize_text_response

    if not response:
        raise ValueError(f"[{provider_id}] returned an invalid response")
    choices = getattr(response, "choices", None)
    if not choices:
        if isinstance(response, ChatCompletion):
            raise ValueError(f"[{provider_id}] returned empty choices")
        raise ValueError(f"[{provider_id}] returned an invalid response")

    first_choice = choices[0]
    finish_reason = str(getattr(first_choice, "finish_reason", "") or "").strip().lower()
    message = getattr(first_choice, "message", None)
    if message is None:
        raise ValueError(f"[{provider_id}] returned empty message")

    raw_content = getattr(message, "content", None)
    msg_extra = getattr(message, "model_extra", None) or {}
    has_reasoning_field = bool(
        getattr(message, "reasoning", None)
        or getattr(message, "reasoning_content", None)
        or msg_extra.get("reasoning")
        or msg_extra.get("reasoning_content")
    )
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage is not None else None
    reasoning_tokens = int(getattr(details, "reasoning_tokens", 0) or 0) if details is not None else 0
    has_think_tags = isinstance(raw_content, str) and "<think" in raw_content.lower()
    has_reasoning = bool(has_reasoning_field or reasoning_tokens > 0 or has_think_tags)

    try:
        if isinstance(response, ChatCompletion):
            return _extract_chat_completion_text(response, provider_id)
        return _normalize_text_response(raw_content, provider_id)
    except ValueError as exc:
        err_str = str(exc)
        if "empty text content" in err_str.lower():
            meta_suffix = f"finish_reason={finish_reason or 'unknown'}"
            if has_reasoning:
                meta_suffix += f", reasoning_tokens={reasoning_tokens or 'present'}"
            raise LLMEmptyResponseError(
                f"{err_str} ({meta_suffix})",
                finish_reason=finish_reason,
                has_reasoning=has_reasoning,
                http_ok=True,
                max_tokens_used=max_tokens_used,
            ) from exc
        raise


def _mask_secret(raw_key: str) -> str:
    """Mask an API key showing only the last 4 characters."""
    s = (raw_key or "").strip()
    if len(s) <= 6:
        return "****"
    return f"****{s[-4:]}"


def _extract_response_headers(exc: Any) -> Dict[str, str]:
    """Extract lowercase response header map from SDK exceptions when available."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None) if resp is not None else None
    if not headers:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in dict(headers).items()}
    except Exception:
        return {}


def _parse_rate_limit_details(
    error_msg: str,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[bool, Optional[float], Optional[str]]:
    """Differentiate daily free quota vs per-minute 429 and compute wait duration."""
    lower = (error_msg or "").lower()
    hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}

    retry_after: Optional[float] = None
    raw_ra = hdrs.get("retry-after") or ""
    if raw_ra:
        try:
            retry_after = max(0.0, float(raw_ra))
        except ValueError:
            pass

    reset_ts: Optional[float] = None
    raw_reset = hdrs.get("x-ratelimit-reset") or hdrs.get("x-ratelimit-reset-requests") or ""
    if raw_reset:
        try:
            val = float(raw_reset)
            if val > 1e12:
                val /= 1000.0
            if val > 1e9:
                reset_ts = val
                wait_from_reset = max(0.0, val - time.time())
                if retry_after is None:
                    retry_after = wait_from_reset
            elif retry_after is None:
                retry_after = max(0.0, val)
        except ValueError:
            pass

    is_daily = any(
        tok in lower
        for tok in (
            "free-models-per-day",
            "free models per day",
            "per-day",
            "per day",
            "daily limit",
            "daily quota",
            "requests per day",
            "exceeded your current quota",
        )
    )
    if not is_daily:
        is_rpm = any(tok in lower for tok in ("per-min", "per min", "requests per minute", "rpm"))
        # If header shows remaining==0 and reset is >30 min away without RPM mention, treat as daily
        rem_hdr = hdrs.get("x-ratelimit-remaining")
        if not is_rpm and rem_hdr == "0" and retry_after is not None and retry_after > 1800:
            is_daily = True

    reset_hint: Optional[str] = None
    if reset_ts is not None:
        from datetime import datetime, timezone

        reset_hint = datetime.fromtimestamp(reset_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    elif raw_reset:
        reset_hint = raw_reset

    return is_daily, retry_after, reset_hint


def _classify_llm_error(
    error_msg: str,
    model: str = "",
    headers: Optional[Dict[str, str]] = None,
) -> Optional[LLMAdapterError]:
    """Classify upstream error text into auth, credit, daily/per-min 429, or model-not-found errors."""
    lower = (error_msg or "").lower()
    if any(
        tok in lower
        for tok in (
            "error code: 401",
            "http 401",
            "'code': 401",
            '"code": 401',
            " 401 ",
            "invalid_api_key",
            "invalid api key",
            "incorrect api key",
            "user not found",
            "unauthorized",
            "authentication failed",
        )
    ):
        return LLMAuthError(error_msg)

    if any(
        tok in lower
        for tok in (
            "error code: 402",
            "http 402",
            "'code': 402",
            '"code": 402',
            " 402 ",
            "requires more credits",
            "can only afford",
            "insufficient credits",
            "insufficient balance",
            "not enough credits",
            "out of credits",
        )
    ):
        return LLMCreditError(error_msg)

    if any(
        tok in lower
        for tok in (
            "429",
            "rate limit",
            "rate_limit",
            "too many requests",
            "requests per minute",
            "quota exceeded",
            "throttl",
            "free-models-per-day",
        )
    ):
        is_daily, retry_after, reset_hint = _parse_rate_limit_details(error_msg, headers=headers)
        if is_daily:
            return LLMDailyQuotaError(
                error_msg,
                retry_after_seconds=retry_after,
                reset_hint=reset_hint,
            )
        return LLMRateLimitError(
            error_msg,
            is_daily=False,
            retry_after_seconds=retry_after,
            reset_hint=reset_hint,
        )

    model_not_found = (
        "model_not_found" in lower
        or "does not exist" in lower
        or "no such model" in lower
        or "unknown model" in lower
        or "invalid model" in lower
        or "not a valid model" in lower
        or "model is retired" in lower
        or "has been retired" in lower
        or "unavailable for free" in lower
        or "paid version is available now" in lower
        or ("not found" in lower and ("model" in lower or "404" in lower))
        or ("unavailable" in lower and ("model" in lower or "404" in lower))
    )
    if model_not_found:
        return LLMModelNotFoundError(error_msg, model=model)
    return None


class LLMAdapter:
    """Adapter mediating LLM calls with caching, retries, and fake provider support."""

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        cache_dir: Optional[str | Path] = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        fake_delay: float = 0.0,
    ) -> None:
        self.provider = (provider or config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID).lower().strip()
        self.fake_delay = float(fake_delay)
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else None
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.retry_delay = max(0.1, retry_delay)

        # Resolve model name cleanly (safe openrouter/free fallback when OpenRouter field is empty)
        self.used_safe_default = False
        self.last_request_max_tokens: Optional[int] = None
        if model:
            self.model = model.strip()
        elif self.provider == "fake":
            self.model = "deterministic-fake-v1"
        else:
            prov_def = get_llm_provider(self.provider)
            raw_model = config.app.get(prov_def.config_key("model_name"), "") if prov_def else ""
            self.model, self.used_safe_default = resolve_effective_model(self.provider, raw_model)

        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_fake(self) -> bool:
        """Return True if this adapter is running against the local fake test mock."""
        return self.provider == "fake"

    def validate_configuration(self) -> None:
        """Verify that provider has required credentials before execution."""
        if self.is_fake:
            return

        readiness = get_llm_readiness(self.provider)
        if readiness.get("configured"):
            return

        prov_def = get_llm_provider(self.provider)
        if prov_def:
            if prov_def.requires_api_key:
                key_name = prov_def.config_key("api_key")
                key_val = (config.app.get(key_name) or "").strip()
                if not key_val:
                    url_hint = f" ({prov_def.api_key_url})" if prov_def.api_key_url else ""
                    raise LLMConfigurationError(
                        f"LLM Provider '{self.provider}' is selected, but '{key_name}' is not configured or empty.\n"
                        f"To configure this provider:\n"
                        f"  1. Open WebUI -> 'Basic Settings' -> 'LLM Settings' (or edit config.toml)\n"
                        f"  2. Select Provider '{prov_def.default_label}' and enter your API Key{url_hint}\n"
                        f"  3. Save settings, OR pass '--provider fake' if you wish to run in offline testing/simulation mode."
                    )
            raise LLMConfigurationError(
                readiness.get("message")
                or f"LLM Provider '{self.provider}' is not ready. Check MPT Settings or pass '--provider fake'."
            )
        else:
            key_name = f"{self.provider}_api_key"
            key_val = (config.app.get(key_name) or "").strip()
            if not key_val:
                raise LLMConfigurationError(
                    f"LLM Provider '{self.provider}' is selected, but '{key_name}' is not configured or empty.\n"
                    f"Please configure '{key_name}' in config.toml or pass '--provider fake' for offline testing."
                )

    def _compute_cache_key(self, prompt: str) -> str:
        """Compute sha256 hash representing prompt and call configuration."""
        normalized_prompt = prompt.strip()
        data = f"{self.provider}:{self.model}:{normalized_prompt}".encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def _read_cache(self, cache_key: str) -> Optional[LLMResponse]:
        """Read cached response from disk if present."""
        if not self.cache_dir:
            return None
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.is_file():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return LLMResponse(
                    content=data["content"],
                    provider=data.get("provider", self.provider),
                    model=data.get("model", self.model),
                    duration_seconds=0.0,
                    tokens=data.get("tokens"),
                    cache_hit=True,
                )
            except Exception as exc:
                logger.warning(f"Failed to read LLM cache '{cache_file}': {exc}")
        return None

    def _write_cache(self, cache_key: str, response: LLMResponse) -> None:
        """Atomically write response to cache."""
        if not self.cache_dir:
            return
        cache_file = self.cache_dir / f"{cache_key}.json"
        tmp_file = self.cache_dir / f"{cache_key}.json.tmp.{uuid.uuid4().hex}"
        try:
            payload = json.dumps(
                {
                    "content": response.content,
                    "provider": response.provider,
                    "model": response.model,
                    "tokens": response.tokens,
                    "created_at": time.time(),
                },
                indent=2,
            ).encode("utf-8")
            with open(tmp_file, "wb") as fp:
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_file, cache_file)
        except Exception as exc:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            logger.warning(f"Failed to write LLM cache '{cache_file}': {exc}")

    def _call_bounded_sdk(
        self,
        *,
        prompt: str,
        resolved_model: str,
        max_tokens: int,
        runtime_cfg: Dict[str, Any],
        minimize_reasoning: bool = False,
    ) -> str:
        """Execute LLM call with explicit max_tokens and reasoning minimization for short calls (F5/F6)."""
        from app.services.llm import (
            _normalize_text_response,
            _resolve_provider_field_value,
            _sdk_transport_options,
        )
        from app.utils.request_logging import trace_sdk_call

        prov_def = get_llm_provider(self.provider)
        if not prov_def:
            raise LLMAdapterError(f"Unsupported LLM provider: {self.provider}")

        adapter = prov_def.adapter
        api_key = str(runtime_cfg.get(prov_def.config_key("api_key"), "") or "")
        base_url = prov_def.resolve_base_url(str(runtime_cfg.get(prov_def.config_key("base_url"), "") or ""))
        if self.provider == "ollama":
            api_key = "ollama"
            base_url = base_url or config.get_default_ollama_base_url()

        if adapter == "gemini":
            from google import genai
            from google.genai import types

            http_options = types.HttpOptions(base_url=base_url) if base_url else None
            gen_cfg = types.GenerateContentConfig(temperature=0.5, max_output_tokens=int(max_tokens))
            with genai.Client(api_key=api_key, http_options=http_options) as client:
                resp = trace_sdk_call(
                    self.provider,
                    base_url,
                    client.models.generate_content,
                    model=resolved_model,
                    contents=prompt,
                    config=gen_cfg,
                )
            try:
                return _normalize_text_response(getattr(resp, "text", ""), self.provider)
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

        from openai import AzureOpenAI, OpenAI

        sdk_client = None
        try:
            if adapter == "azure":
                api_ver = str(runtime_cfg.get(prov_def.config_key("api_version"), "2024-02-15-preview") or "2024-02-15-preview")
                sdk_client = AzureOpenAI(
                    api_key=api_key,
                    api_version=api_ver,
                    azure_endpoint=base_url,
                    **_sdk_transport_options(self.timeout, 0),
                )
            elif adapter == "cloudflare_ai_gateway":
                extra = {
                    f.config_suffix: _resolve_provider_field_value(
                        runtime_cfg.get(prov_def.config_key(f.config_suffix)), f.default_value
                    )
                    for f in prov_def.extra_fields
                }
                sdk_client = OpenAI(
                    api_key=api_key,
                    base_url=f"https://api.cloudflare.com/client/v4/accounts/{extra['account_id']}/ai/v1",
                    default_headers={"cf-aig-gateway-id": extra["gateway_id"]},
                    **_sdk_transport_options(self.timeout, 0),
                )
            else:
                sdk_client = OpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    **_sdk_transport_options(self.timeout, 0),
                )

            resp = invoke_openai_chat_completion(
                sdk_client.chat.completions.create,
                self.provider,
                base_url=base_url,
                minimize_reasoning=minimize_reasoning,
                model=resolved_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=int(max_tokens),
            )
            return extract_sdk_completion_or_raise(
                resp, self.provider, max_tokens_used=int(max_tokens)
            )
        finally:
            close = getattr(sdk_client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def generate(
        self,
        prompt: str,
        fake_kind: Optional[str] = None,  # "outline" | "chapter" | "revise_length" | "summary"
        fake_kwargs: Optional[Dict[str, Any]] = None,
        force_refresh: bool = False,
        max_tokens: Optional[int] = None,
        minimize_reasoning: Optional[bool] = None,
    ) -> LLMResponse:
        """Execute prompt against configured provider or fake mock, checking cache first."""
        # Fail fast if real provider credentials are missing
        self.validate_configuration()

        target_words = int((fake_kwargs or {}).get("target_words", 0) or 0)
        eff_max_tokens = int(max_tokens) if max_tokens is not None else compute_script_max_tokens(
            fake_kind or "default", target_words=target_words
        )
        self.last_request_max_tokens = eff_max_tokens

        cache_key = self._compute_cache_key(prompt)

        # 1. Check cache
        if not force_refresh:
            cached = self._read_cache(cache_key)
            if cached is not None:
                cached.max_tokens_requested = eff_max_tokens
                return cached

        # 2. Fake provider execution
        if self.provider == "fake":
            start_time = time.perf_counter()
            content = self._generate_fake_response(prompt, fake_kind, fake_kwargs)
            duration = time.perf_counter() - start_time
            resp = LLMResponse(
                content=content,
                provider="fake",
                model=self.model or "deterministic-fake-v1",
                duration_seconds=round(duration, 4),
                tokens=None,
                cache_hit=False,
                max_tokens_requested=eff_max_tokens,
            )
            self._write_cache(cache_key, resp)
            return resp

        from app.services import llm as upstream_llm

        last_error = ""
        resolved_model = self.model
        prov_def = get_llm_provider(self.provider)
        if not resolved_model:
            raw_model = config.app.get(prov_def.config_key("model_name"), "") if prov_def else ""
            resolved_model, _ = resolve_effective_model(self.provider, raw_model)

        # Build isolated config snapshot honoring explicit adapter provider/model overrides
        runtime_cfg = dict(config.app)
        runtime_cfg["llm_provider"] = self.provider
        if resolved_model and prov_def:
            runtime_cfg[prov_def.config_key("model_name")] = resolved_model

        gen_fn = getattr(upstream_llm, "_generate_response", None)
        is_mocked_upstream = (
            gen_fn is None
            or getattr(gen_fn, "__module__", "") != "app.services.llm"
            or getattr(gen_fn, "__name__", "") != "_generate_response"
        )
        adapter_type = prov_def.adapter if prov_def else "openai_compatible"
        can_call_bounded_sdk = (
            not is_mocked_upstream
            and adapter_type in ("openai", "openai_compatible", "gemini", "azure", "cloudflare_ai_gateway")
        )

        last_empty_error: Optional[LLMEmptyResponseError] = None
        cur_tokens = eff_max_tokens
        eff_minimize = (
            bool(minimize_reasoning)
            if minimize_reasoning is not None
            else (self.provider == "openrouter" or eff_max_tokens <= 512)
        )
        seen_empty_configs: set[Tuple[int, bool]] = set()
        for attempt in range(1, self.max_retries + 1):
            start_time = time.perf_counter()
            self.last_request_max_tokens = cur_tokens
            try:
                if can_call_bounded_sdk:
                    try:
                        raw_text = self._call_bounded_sdk(
                            prompt=prompt,
                            resolved_model=resolved_model,
                            max_tokens=cur_tokens,
                            runtime_cfg=runtime_cfg,
                            minimize_reasoning=eff_minimize,
                        )
                    except LLMEmptyResponseError as empty_exc:
                        seen_empty_configs.add((cur_tokens, eff_minimize))
                        if empty_exc.finish_reason == "length" or empty_exc.has_reasoning:
                            next_minimize = True
                            escalated = min(6144, max(cur_tokens * 2, 512))
                            if (escalated, next_minimize) not in seen_empty_configs:
                                logger.info(
                                    f"LLM returned empty text (finish_reason={empty_exc.finish_reason or 'unknown'}) "
                                    f"at max_tokens={cur_tokens}, minimize_reasoning={eff_minimize}; "
                                    f"retrying with max_tokens={escalated}, minimize_reasoning={next_minimize}"
                                )
                                cur_tokens = escalated
                                eff_minimize = next_minimize
                                self.last_request_max_tokens = cur_tokens
                                raw_text = self._call_bounded_sdk(
                                    prompt=prompt,
                                    resolved_model=resolved_model,
                                    max_tokens=cur_tokens,
                                    runtime_cfg=runtime_cfg,
                                    minimize_reasoning=eff_minimize,
                                )
                            else:
                                raise
                        else:
                            raise
                else:
                    raw_text = upstream_llm._generate_response(
                        prompt=prompt,
                        app_config=runtime_cfg,
                        request_timeout=self.timeout,
                        max_retries=0,  # Let adapter manage bounded backoff
                    )
                duration = time.perf_counter() - start_time
                text_out = (raw_text or "").strip()

                # upstream _generate_response catches exceptions and returns "Error: <msg>"
                if not text_out or text_out.startswith("Error:"):
                    clean_err = sanitize_error(text_out or "Empty LLM response")
                    classified = _classify_llm_error(clean_err, model=resolved_model)
                    if isinstance(classified, (LLMAuthError, LLMModelNotFoundError, LLMCreditError, LLMDailyQuotaError)):
                        raise classified
                    last_error = clean_err
                    logger.warning(
                        f"LLM call attempt {attempt}/{self.max_retries} returned error ({duration:.2f}s): {last_error}"
                    )
                    if attempt < self.max_retries:
                        wait_s = self.retry_delay * (2 ** (attempt - 1))
                        if isinstance(classified, LLMRateLimitError) and classified.retry_after_seconds is not None:
                            wait_s = min(max(float(classified.retry_after_seconds), wait_s), 65.0)
                        time.sleep(wait_s)
                        continue
                    if isinstance(classified, LLMRateLimitError):
                        raise LLMRateLimitError(
                            f"LLM provider '{self.provider}' rate limit (429) persisted after {self.max_retries} attempts: {last_error}",
                            is_daily=classified.is_daily,
                            retry_after_seconds=classified.retry_after_seconds,
                            reset_hint=classified.reset_hint,
                        )
                    raise LLMAdapterError(
                        f"LLM request to provider '{self.provider}' failed after {self.max_retries} attempts. "
                        f"Last error: {last_error}"
                    )

                resp = LLMResponse(
                    content=text_out,
                    provider=self.provider,
                    model=resolved_model,
                    duration_seconds=round(duration, 3),
                    tokens=None,
                    cache_hit=False,
                    max_tokens_requested=cur_tokens,
                )
                self._write_cache(cache_key, resp)
                return resp
            except LLMEmptyResponseError as empty_exc:
                duration = time.perf_counter() - start_time
                seen_empty_configs.add((cur_tokens, eff_minimize))
                last_empty_error = empty_exc
                last_error = sanitize_error(str(empty_exc))
                logger.warning(
                    f"LLM call attempt {attempt}/{self.max_retries} returned empty text ({duration:.2f}s): {last_error}"
                )
                if attempt < self.max_retries:
                    reason_like = bool(empty_exc.has_reasoning or empty_exc.finish_reason == "length")
                    next_minimize = True if (reason_like or self.provider == "openrouter") else eff_minimize
                    next_tokens = (
                        min(6144, max(cur_tokens * 2, 512))
                        if reason_like
                        else cur_tokens
                    )
                    if (next_tokens, next_minimize) in seen_empty_configs:
                        raise
                    cur_tokens = next_tokens
                    eff_minimize = next_minimize
                    time.sleep(self.retry_delay * (2 ** (attempt - 1)))
                    continue
                raise
            except (LLMAuthError, LLMModelNotFoundError, LLMCreditError, LLMDailyQuotaError):
                raise
            except LLMRateLimitError as rl_exc:
                if rl_exc.is_daily:
                    raise
                last_error = sanitize_error(str(rl_exc))
                if attempt < self.max_retries:
                    wait_s = self.retry_delay * (2 ** (attempt - 1))
                    if rl_exc.retry_after_seconds is not None:
                        wait_s = min(max(float(rl_exc.retry_after_seconds), wait_s), 65.0)
                    time.sleep(wait_s)
                    continue
                raise
            except Exception as exc:
                duration = time.perf_counter() - start_time
                last_error = sanitize_error(str(exc))
                resp_headers = _extract_response_headers(exc)
                classified = _classify_llm_error(last_error, model=resolved_model, headers=resp_headers)
                if isinstance(classified, (LLMAuthError, LLMModelNotFoundError, LLMCreditError, LLMDailyQuotaError)):
                    raise classified
                logger.warning(
                    f"LLM call attempt {attempt}/{self.max_retries} failed ({duration:.2f}s): {last_error}"
                )
                if attempt < self.max_retries:
                    wait_s = self.retry_delay * (2 ** (attempt - 1))
                    if isinstance(classified, LLMRateLimitError) and classified.retry_after_seconds is not None:
                        wait_s = min(max(float(classified.retry_after_seconds), wait_s), 65.0)
                    time.sleep(wait_s)
                elif isinstance(classified, LLMRateLimitError):
                    raise LLMRateLimitError(
                        f"LLM provider '{self.provider}' rate limit (429) persisted after {self.max_retries} attempts: {last_error}",
                        is_daily=classified.is_daily,
                        retry_after_seconds=classified.retry_after_seconds,
                        reset_hint=classified.reset_hint,
                    )

        if last_empty_error is not None:
            raise last_empty_error
        raise LLMAdapterError(
            f"LLM request to provider '{self.provider}' failed after {self.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    def _generate_fake_response(
        self,
        prompt: str,
        fake_kind: Optional[str] = None,
        fake_kwargs: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate high-quality deterministic fake responses for offline testing."""
        kwargs = fake_kwargs or {}

        # Allow tests to inject errors or invalid JSON directly
        if kwargs.get("fake_error"):
            raise ValueError(kwargs["fake_error"])
        if kwargs.get("fake_invalid_json"):
            return "INVALID JSON {{{ missing closing bracket"
        if kwargs.get("fake_raw_content"):
            return kwargs["fake_raw_content"]

        if self.fake_delay > 0:
            time.sleep(self.fake_delay)

        kind = fake_kind
        if not kind:
            # Infer from prompt
            if "narrative_summary" in prompt or "editorial assistant for documentary video productions" in prompt:
                kind = "summary"
            elif "Required JSON Output Structure:" in prompt or "candidate_titles" in prompt:
                kind = "outline"
            elif "Required Action:" in prompt or "Current Chapter Status:" in prompt:
                kind = "revise_length"
            else:
                kind = "chapter"

        if kind == "summary":
            ch_title = kwargs.get("chapter_title", "Chapter Title")
            ch_index = kwargs.get("chapter_index", 1)
            topic = kwargs.get("topic", "the subject")
            summary_dict = {
                "narrative_summary": (
                    f"Chapter {ch_index} ({ch_title}) established the core narrative developments and historical context. "
                    f"Key mechanical milestones were reached under competitive conditions in the evolution of {topic}. "
                    "The momentum set the stage for subsequent technical and competitive challenges."
                ),
                "key_facts": [
                    f"Milestone engineering benchmark established in Chapter {ch_index}",
                    f"Primary competition dynamics and records set for {topic} in Chapter {ch_index}",
                ],
            }
            return json.dumps(summary_dict, indent=2)

        elif kind == "outline":
            topic = kwargs.get("topic", "The 1969 Dodge Charger Legend")
            target_min = float(kwargs.get("target_duration_minutes", 12.0))
            wpm = int(kwargs.get("words_per_minute", 140))
            total_words = int(target_min * wpm)

            # Generate 4 chapters
            words_per_ch = total_words // 4
            chapters_data = [
                {
                    "chapter_index": 1,
                    "title": f"The Genesis of {topic}",
                    "purpose": "Introduce the cultural moment and engineering rivalry that birthed the machine.",
                    "key_points": [
                        "Detroit streets in the late 1960s",
                        "The raw competition between the Big Three",
                    ],
                    "target_words": words_per_ch,
                },
                {
                    "chapter_index": 2,
                    "title": "Engineering the Beast: Iron and Aerodynamics",
                    "purpose": "Examine the technical development, powerplants, and racing homologation.",
                    "key_points": [
                        "NASCAR aero development in wind tunnels",
                        "The legendary 426 Hemi engine development",
                    ],
                    "target_words": words_per_ch,
                },
                {
                    "chapter_index": 3,
                    "title": "On the Track and Dragstrip Dominance",
                    "purpose": "Document track triumphs, quarter-mile dominance, and legendary driver rivalries.",
                    "key_points": [
                        "Quarter-mile acceleration and tire smoke",
                        "Breaking speed records at Talladega",
                    ],
                    "target_words": words_per_ch,
                },
                {
                    "chapter_index": 4,
                    "title": "The Enduring Icon: Hollywood and Collector Lore",
                    "purpose": "Analyze how Hollywood film appearances and auctions cemented legendary status.",
                    "key_points": [
                        "Dukes of Hazzard and Bullitt screen presence",
                        "Modern six-figure auction values",
                    ],
                    "target_words": total_words - (words_per_ch * 3),
                },
            ]

            outline_dict = {
                "topic": topic,
                "target_duration_minutes": target_min,
                "words_per_minute": wpm,
                "total_target_words": total_words,
                "candidate_titles": [
                    f"The Untold Story of {topic}",
                    f"How the {topic} Conquered the Asphalt",
                    f"The Muscle Car King: {topic} Explained",
                ],
                "opening_hook": (
                    f"In 1969, an unholy thunder cracked across the backstreets of Detroit as {topic} "
                    "unleashed four hundred and twenty-five horses of pure, unadulterated iron."
                ),
                "chapters": chapters_data,
            }
            return json.dumps(outline_dict, indent=2)

        elif kind == "revise_length":
            # Length revision fake generator
            target_words = int(kwargs.get("target_words", 400))
            ch_title = kwargs.get("chapter_title", "Revised Chapter")
            return self._build_fake_chapter_markdown(
                title=ch_title,
                target_words=target_words,
                is_revision=True,
            )

        else:
            # Chapter generator
            ch_title = kwargs.get("chapter_title", "Chapter Title")
            ch_index = kwargs.get("chapter_index", 1)
            target_words = int(kwargs.get("target_words", 420))
            return self._build_fake_chapter_markdown(
                title=ch_title,
                target_words=target_words,
                chapter_index=ch_index,
            )

    def _build_fake_chapter_markdown(
        self,
        title: str,
        target_words: int,
        chapter_index: int = 1,
        is_revision: bool = False,
    ) -> str:
        """Synthesize realistic markdown chapter text with exact tag patterns matching target words."""
        # Clean title of any markdown markers
        clean_title = title.replace("#", "").strip()

        base_sentences = [
            (
                f"The asphalt trembled under the immense vibration of Detroit steel as the engine roared to life.",
                "black classic muscle car rumbling on a rural highway at sunset",
                "zoom_in",
                "stock",
                "classic muscle car rumbling highway",
                None,
            ),
            (
                f"Under the sculpted hood lay a dual-carbureted monster producing four hundred and twenty-five gross horsepower.",
                "closeup shot of massive chrome air cleaner and dual four-barrel carburetors",
                "pan_left",
                "local",
                "dual four barrel engine bay carburetor",
                "426 Hemi factory rated at 425 gross horsepower at 5000 RPM",
            ),
            (
                f"Engineers worked through grueling night shifts in clandestine Michigan workshops to beat their crosstown rivals.",
                "vintage black and white photo of automotive draftsmen over drafting tables",
                "zoom_out",
                "stock",
                "1960s automotive drafting room engineers",
                None,
            ),
            (
                f"When the green flag dropped, the rear tires broke traction in a violent cloud of burning white smoke.",
                "slow motion tire smoke billowing from wide rear drag radials at dragstrip",
                "zoom_in",
                "stock",
                "dragstrip burnout tire smoke slow motion",
                "Quarter mile completed in under 13.8 seconds on bias-ply tires",
            ),
            (
                f"Every curve of the sheet metal was refined to achieve maximum high-speed aerodynamic stability.",
                "wind tunnel smoke stream gliding smoothly over vintage fastback roofline",
                "pan_right",
                "ai_image",
                "wind tunnel aerodynamic smoke stream car",
                None,
            ),
            (
                f"By the end of the racing season, the machine had claimed a permanent place in automotive history.",
                "victory lane celebration with vintage trophy and cheering racing pit crew",
                "static",
                "stock",
                "1960s victory lane trophy celebration racing",
                "Produced in limited quantities of under 500 homologation units",
            ),
        ]

        # Assemble paragraphs
        # Target roughly ~18-22 words per sentence
        total_generated_words = 0
        paragraphs = []
        current_para = []

        # We repeat or trim sentences to get close to target_words
        cycle_idx = 0
        while total_generated_words < target_words:
            sent_tpl = base_sentences[cycle_idx % len(base_sentences)]
            cycle_idx += 1

            text, visual, motion, source, search, verify = sent_tpl
            tag_str = f"[visual: {visual}] [motion: {motion}] [source: {source}] [search: {search}]"
            if verify:
                tag_str += f" [verify: {verify}]"

            sentence_line = f"{text} {tag_str}"
            current_para.append(sentence_line)
            total_generated_words += len(text.split())

            # Group every 3 sentences into a paragraph
            if len(current_para) >= 3:
                paragraphs.append(" ".join(current_para))
                current_para = []

        if current_para:
            paragraphs.append(" ".join(current_para))

        joined_paragraphs = "\n\n".join(paragraphs)
        return f"## {clean_title}\n\n{joined_paragraphs}\n"


# ---------------------------------------------------------------------------
# Unified MPT LLM Readiness, Connection Testing, & Model Discovery (Tahap 8B.1)
# ---------------------------------------------------------------------------


def _get_oauth_status_map() -> Dict[str, bool]:
    """Read MPT subscription OAuth login states cleanly without side effects."""
    status: Dict[str, bool] = {
        "openai_codex": False,
        "openai_chatgpt": False,
        "kimi_code": False,
    }
    try:
        from app.services import codex_oauth

        status["openai_codex"] = bool(codex_oauth.account_status())
    except Exception:
        pass
    try:
        from app.services import chatgpt_oauth

        status["openai_chatgpt"] = bool(chatgpt_oauth.account_status())
    except Exception:
        pass
    try:
        from app.services import kimi_code_oauth

        status["kimi_code"] = bool(kimi_code_oauth.is_authorized())
    except Exception:
        pass
    return status


def get_llm_readiness(provider_id: Optional[str] = None) -> Dict[str, Any]:
    """Return unified MPT LLM readiness & credential summary across all 44 providers.

    Delegates configured provider detection to ``webui.settings_providers.configured_llm_providers``
    and respects OAuth/CLI/local (Ollama) providers with zero duplicate key storage.
    """
    active_id = (
        provider_id or config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID
    ).lower().strip()
    prov_def = get_llm_provider(active_id)
    oauth_status = _get_oauth_status_map()

    configured_tuple: Tuple[str, ...] = ()
    try:
        from webui.settings_providers import configured_llm_providers

        configured_tuple = configured_llm_providers(config.app, oauth_status)
    except Exception:
        configured_tuple = ()

    if not prov_def:
        raw_key = str(config.app.get(f"{active_id}_api_key") or "").strip()
        raw_model = str(config.app.get(f"{active_id}_model_name") or "").strip()
        is_ready = bool(raw_key)
        return {
            "provider": active_id,
            "provider_label": active_id,
            "model": raw_model,
            "adapter": "openai",
            "configured": is_ready,
            "is_invalid": False,
            "masked_key": _mask_secret(raw_key) if raw_key else "",
            "message": "" if is_ready else f"Kredensial penyedia '{active_id}' belum diatur.",
            "configured_providers": configured_tuple,
            "requires_api_key": True,
        }

    raw_model = str(config.app.get(prov_def.config_key("model_name")) or "").strip()
    resolved_model, used_safe_default = resolve_effective_model(active_id, raw_model)
    billing_tier = resolve_model_billing_tier(active_id, resolved_model)
    provider_label = prov_def.default_label or active_id
    adapter = prov_def.adapter

    # Resolve readiness based on MPT provider kind
    if prov_def.requires_api_key:
        key_name = prov_def.config_key("api_key")
        raw_key = str(config.app.get(key_name) or "").strip()
        if not raw_key:
            return {
                "provider": active_id,
                "provider_label": provider_label,
                "model": resolved_model,
                "used_safe_default": used_safe_default,
                "billing_tier": billing_tier,
                "adapter": adapter,
                "configured": False,
                "is_invalid": False,
                "masked_key": "",
                "message": f"Key '{key_name}' belum diisi di Pengaturan AI MPT.",
                "configured_providers": configured_tuple,
                "requires_api_key": True,
            }

        # Gemini API key format check ONLY when Gemini is the active provider
        if ("gemini" in active_id or "google" in active_id) and not raw_key.startswith("AIza"):
            return {
                "provider": active_id,
                "provider_label": provider_label,
                "model": resolved_model,
                "used_safe_default": used_safe_default,
                "billing_tier": billing_tier,
                "adapter": adapter,
                "configured": False,
                "is_invalid": True,
                "masked_key": _mask_secret(raw_key),
                "message": "Key Gemini tampaknya tidak valid (seharusnya dimulai dengan 'AIza'). Periksa kembali di Settings -> Pengaturan AI (LLM).",
                "configured_providers": configured_tuple,
                "requires_api_key": True,
            }

        extra_missing = [
            f.config_suffix
            for f in prov_def.extra_fields
            if f.required and not str(config.app.get(prov_def.config_key(f.config_suffix)) or "").strip()
        ]
        if extra_missing:
            return {
                "provider": active_id,
                "provider_label": provider_label,
                "model": resolved_model,
                "used_safe_default": used_safe_default,
                "billing_tier": billing_tier,
                "adapter": adapter,
                "configured": False,
                "is_invalid": False,
                "masked_key": _mask_secret(raw_key),
                "message": f"Konfigurasi tambahan belum lengkap ({', '.join(extra_missing)}).",
                "configured_providers": configured_tuple,
                "requires_api_key": True,
            }

        return {
            "provider": active_id,
            "provider_label": provider_label,
            "model": resolved_model,
            "used_safe_default": used_safe_default,
            "billing_tier": billing_tier,
            "adapter": adapter,
            "configured": True,
            "is_invalid": False,
            "masked_key": _mask_secret(raw_key),
            "message": "Siap",
            "configured_providers": configured_tuple,
            "requires_api_key": True,
        }

    # Non-API-key providers: OAuth subscriptions, local Ollama, CLI agents
    if adapter in ("openai_codex", "openai_chatgpt", "kimi_code"):
        connected = bool(oauth_status.get(adapter)) or (active_id in configured_tuple)
        return {
            "provider": active_id,
            "provider_label": provider_label,
            "model": resolved_model,
            "used_safe_default": used_safe_default,
            "billing_tier": billing_tier,
            "adapter": adapter,
            "configured": connected,
            "is_invalid": False,
            "masked_key": "OAuth" if connected else "",
            "message": "Terhubung (OAuth)" if connected else f"Akun OAuth {provider_label} belum login.",
            "configured_providers": configured_tuple,
            "requires_api_key": False,
        }

    if adapter in ("claude_code", "gemini_cli"):
        default_bin = "claude" if adapter == "claude_code" else "gemini"
        cli_cfg = str(config.app.get(prov_def.config_key("cli_path")) or "").strip() or default_bin
        found = bool(shutil.which(cli_cfg) or os.path.isfile(cli_cfg))
        return {
            "provider": active_id,
            "provider_label": provider_label,
            "model": resolved_model or "cli-default",
            "used_safe_default": used_safe_default,
            "billing_tier": billing_tier,
            "adapter": adapter,
            "configured": found,
            "is_invalid": False,
            "masked_key": "CLI" if found else "",
            "message": f"CLI {default_bin} siap" if found else f"CLI '{cli_cfg}' tidak ditemukan di PATH.",
            "configured_providers": configured_tuple,
            "requires_api_key": False,
        }

    # Ollama or other keyless local server
    base_url = prov_def.resolve_base_url(str(config.app.get(prov_def.config_key("base_url")) or ""))
    has_local = bool(base_url or active_id == "ollama") and bool(resolved_model or raw_model)
    return {
        "provider": active_id,
        "provider_label": provider_label,
        "model": resolved_model,
        "used_safe_default": used_safe_default,
        "billing_tier": billing_tier,
        "adapter": adapter,
        "configured": has_local or (active_id in configured_tuple),
        "is_invalid": False,
        "masked_key": "Local",
        "message": "Siap (Lokal)" if (has_local or active_id in configured_tuple) else f"Model '{active_id}' belum diatur.",
        "configured_providers": configured_tuple,
        "requires_api_key": False,
    }


def test_llm_connection() -> Tuple[bool, str, float]:
    """Verify LLM connectivity aligned with MPT's prompt while preventing 402 & reasoning truncation (F5/F6)."""
    from app.services import llm as upstream_llm

    tc_fn = getattr(upstream_llm, "test_connection", None)
    is_mocked_tc = tc_fn is not None and (
        getattr(tc_fn, "__module__", "") != "app.services.llm"
        or getattr(tc_fn, "__name__", "") != "test_connection"
    )
    active_id = (config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID).lower().strip()
    prov_def = get_llm_provider(active_id)
    adapter_type = prov_def.adapter if prov_def else "openai_compatible"
    use_upstream_directly = is_mocked_tc or (
        adapter_type not in ("openai", "openai_compatible", "gemini", "azure", "cloudflare_ai_gateway")
    )

    if use_upstream_directly and tc_fn is not None:
        try:
            ok, msg, latency = tc_fn()
            return bool(ok), sanitize_error(msg or ""), float(latency or 0.0)
        except Exception as exc:
            return False, sanitize_error(str(exc)), 0.0

    t0 = time.perf_counter()
    conn_timeout = float(getattr(upstream_llm, "LLM_CONNECTION_TEST_TIMEOUT_SECONDS", 30.0) or 30.0)
    try:
        adapter = LLMAdapter(max_retries=1, timeout=conn_timeout)
        resp = adapter.generate(
            prompt="Reply with exactly: OK",
            fake_kind="test_connection",
            force_refresh=True,
            max_tokens=MAX_TOKENS_TEST_CONNECTION,
            minimize_reasoning=True,
        )
        clear_provider_pause(active_id)
        return True, sanitize_error((resp.content or "").strip()), float(time.perf_counter() - t0)
    except LLMEmptyResponseError as exc:
        return (
            False,
            f"[EMPTY_AFTER_HTTP_OK] {sanitize_error(str(exc))}",
            float(time.perf_counter() - t0),
        )
    except Exception as exc:
        return False, sanitize_error(str(exc)), float(time.perf_counter() - t0)


def supports_model_listing(provider_id: Optional[str] = None) -> bool:
    """Return True if the provider supports dynamic model catalog or ``/models`` listing."""
    active_id = (
        provider_id or config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID
    ).lower().strip()
    prov_def = get_llm_provider(active_id)
    if not prov_def:
        return False
    if prov_def.adapter in ("openai_codex", "openai_chatgpt", "openai"):
        return True
    if active_id in ("gemini", "google", "groq", "openrouter"):
        return True
    return False


def list_llm_models(
    provider_id: Optional[str] = None,
    force_refresh: bool = False,
    timeout: float = 10.0,
) -> Tuple[List[str], Optional[str]]:
    """Fetch available models for the active MPT LLM provider.

    Reuses MPT catalogs for Codex, ChatGPT OAuth, and Groq, and queries ``/models``
    for OpenAI, OpenRouter, Gemini, and OpenAI-compatible gateways. Never raises;
    returns ``(models, error_message)`` so callers can fall back to free-text input.
    """
    import requests

    active_id = (
        provider_id or config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID
    ).lower().strip()
    prov_def = get_llm_provider(active_id)
    if not prov_def:
        return [], f"Provider '{active_id}' does not support model listing."

    try:
        if prov_def.adapter == "openai_codex":
            from app.services import codex_models

            catalog = codex_models.get_catalog(force_refresh=force_refresh)
            models = [m for m in (getattr(catalog, "models", None) or []) if isinstance(m, str) and m.strip()]
            return models, None

        if prov_def.adapter == "openai_chatgpt":
            from app.services import chatgpt_models

            catalog = chatgpt_models.get_catalog(force_refresh=force_refresh)
            models = [m for m in (getattr(catalog, "models", None) or []) if isinstance(m, str) and m.strip()]
            return models, None

        if active_id == "gemini":
            api_key = str(config.app.get(prov_def.config_key("api_key")) or "").strip()
            if not api_key:
                return [], "Gemini API key is not configured."
            base_url = (
                prov_def.resolve_base_url(str(config.app.get(prov_def.config_key("base_url")) or ""))
                or "https://generativelanguage.googleapis.com"
            ).rstrip("/")
            url = f"{base_url}/v1beta/models"
            resp = requests.get(url, params={"key": api_key}, timeout=timeout)
            resp.raise_for_status()
            data = resp.json().get("models", [])
            models = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                methods = item.get("supportedGenerationMethods") or []
                if methods and "generateContent" not in methods:
                    continue
                name = str(item.get("name") or "").removeprefix("models/").strip()
                if name:
                    models.append(name)
            return sorted(set(models)), None

        # OpenAI / OpenRouter / Groq / DeepSeek / OpenAI-compatible gateway
        if prov_def.adapter == "openai" or active_id in ("groq", "openrouter"):
            api_key = str(config.app.get(prov_def.config_key("api_key")) or "").strip()
            if prov_def.requires_api_key and not api_key:
                return [], f"API key for '{active_id}' is not configured."
            base_url = prov_def.resolve_base_url(str(config.app.get(prov_def.config_key("base_url")) or ""))
            if active_id == "ollama" and not base_url:
                base_url = config.get_default_ollama_base_url()
            if not base_url:
                return [], f"Base URL for '{active_id}' is not configured."

            if active_id == "groq":
                try:
                    from webui.Main import get_groq_model_ids

                    groq_models = get_groq_model_ids(api_key, base_url)
                    if groq_models:
                        return groq_models, None
                except Exception:
                    pass

            models_url = f"{base_url.rstrip('/')}/models"
            headers: Dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            resp = requests.get(models_url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", []) if isinstance(payload, dict) else []
            models = []
            for item in data:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                mid = item["id"].strip()
                if not mid:
                    continue
                models.append(mid)
                pricing = item.get("pricing")
                pricing_kind = None
                if active_id == "openrouter" and isinstance(pricing, dict):
                    try:
                        p_in = float(pricing.get("prompt", 0) or 0)
                        p_out = float(pricing.get("completion", 0) or 0)
                        pricing_kind = "free" if (p_in == 0.0 and p_out == 0.0) else "paid"
                        _MODEL_PRICING_CACHE[mid.lower()] = pricing_kind
                    except (TypeError, ValueError):
                        pass
                if active_id == "openrouter":
                    supp_params = item.get("supported_parameters")
                    supp_list = [str(p) for p in supp_params] if isinstance(supp_params, list) else []
                    arch = item.get("architecture") or {}
                    out_mods = arch.get("output_modalities") if isinstance(arch, dict) else []
                    out_list = [str(m).lower() for m in out_mods] if isinstance(out_mods, list) else ["text"]
                    _OPENROUTER_MODEL_META_CACHE[mid.lower()] = {
                        "id": mid,
                        "pricing_kind": pricing_kind or ("free" if ":free" in mid.lower() or mid.lower() == OPENROUTER_SAFE_DEFAULT_MODEL else "unknown"),
                        "supported_parameters": supp_list,
                        "output_modalities": out_list,
                        "is_reasoning": is_openrouter_reasoning_model(mid, supp_list),
                        "context_length": item.get("context_length"),
                    }
            return sorted(set(models)), None

        return [], f"Provider '{active_id}' uses manual model configuration."
    except Exception as exc:
        return [], sanitize_error(str(exc))


_REASONING_SLUG_RE = re.compile(
    r"(deepseek[-_/]?r1|\bqwq\b|thinking|reasoner|[-_/]reasoning\b|[-/]r1\b|\bo1\b|\bo3\b|\bo4-mini\b|gpt-oss)",
    re.IGNORECASE,
)


def is_openrouter_reasoning_model(
    model_id: str,
    supported_parameters: Optional[Sequence[str]] = None,
) -> bool:
    """Return True if an OpenRouter model is reasoning-heavy / reasoning-first (C8.3)."""
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    if _REASONING_SLUG_RE.search(mid):
        return True
    if supported_parameters:
        params_low = {str(p).strip().lower() for p in supported_parameters}
        if "reasoning_effort" in params_low or (
            ("reasoning" in params_low or "include_reasoning" in params_low)
            and "max_tokens" not in params_low
        ):
            return True
    meta = _OPENROUTER_MODEL_META_CACHE.get(mid)
    if meta and "is_reasoning" in meta:
        return bool(meta["is_reasoning"])
    return False


def get_openrouter_model_metadata(model_id: str) -> Dict[str, Any]:
    """Return cached OpenRouter model metadata (is_free, is_reasoning, context_length)."""
    mid = (model_id or "").strip().lower()
    cached = dict(_OPENROUTER_MODEL_META_CACHE.get(mid, {}))
    if "is_reasoning" not in cached:
        cached["is_reasoning"] = is_openrouter_reasoning_model(mid)
    if "is_random_router" not in cached:
        cached["is_random_router"] = (mid == OPENROUTER_SAFE_DEFAULT_MODEL)
    return cached


def list_openrouter_free_models(
    force_refresh: bool = False,
    timeout: float = 10.0,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Fetch OpenRouter text/chat `:free` models annotated with reasoning flags cleanly (C8.3)."""
    if force_refresh or not _OPENROUTER_MODEL_META_CACHE:
        models, err = list_llm_models(provider_id="openrouter", force_refresh=force_refresh, timeout=timeout)
        if err and not _OPENROUTER_MODEL_META_CACHE:
            return [], err
    else:
        models = sorted({v["id"] for v in _OPENROUTER_MODEL_META_CACHE.values() if v.get("id")})

    free_entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for mid in models:
        low = mid.lower()
        meta = _OPENROUTER_MODEL_META_CACHE.get(low, {})
        out_mods = [str(m).lower() for m in (meta.get("output_modalities") or ["text"])]
        if "audio" in out_mods or "image" in out_mods or "lyria" in low:
            continue
        is_free = (low == OPENROUTER_SAFE_DEFAULT_MODEL) or (":free" in low) or (meta.get("pricing_kind") == "free")
        if not is_free or low in seen:
            continue
        seen.add(low)
        is_random = low == OPENROUTER_SAFE_DEFAULT_MODEL
        is_reasoning = is_openrouter_reasoning_model(mid, meta.get("supported_parameters"))
        free_entries.append({
            "id": mid,
            "is_reasoning": is_reasoning,
            "is_random_router": is_random,
            "context_length": meta.get("context_length"),
        })

    # Prefer explicit non-reasoning `:free` models first, then `openrouter/free`, then reasoning `:free`
    free_entries.sort(
        key=lambda e: (
            2 if e["is_reasoning"] else (1 if e["is_random_router"] else 0),
            e["id"].lower(),
        )
    )
    return free_entries, None


def fetch_openrouter_key_quota(
    api_key: Optional[str] = None,
    timeout: float = 10.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Query `GET https://openrouter.ai/api/v1/key` safely without logging the API key (C8.2)."""
    import requests

    eff_key = (api_key or str(config.app.get("openrouter_api_key") or "")).strip()
    if not eff_key:
        return None, "OpenRouter API key is not configured."

    try:
        resp = requests.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {eff_key}"},
            timeout=timeout,
        )
        if resp.status_code == 401:
            return None, "HTTP 401 Unauthorized — invalid OpenRouter API key."
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return None, "Invalid response from OpenRouter /key endpoint."

        free_daily = data.get("free_model_daily_requests")
        limit_val = data.get("limit")
        limit_remaining = data.get("limit_remaining")
        usage_val = data.get("usage")
        usage_daily = data.get("usage_daily")
        is_free_tier = bool(data.get("is_free_tier", False))

        used_daily: Optional[int] = None
        limit_daily: Optional[int] = None
        remaining_daily: Optional[int] = None

        if isinstance(free_daily, dict):
            used_daily = free_daily.get("used")
            limit_daily = free_daily.get("limit")
            remaining_daily = free_daily.get("remaining")
        elif isinstance(free_daily, (int, float)):
            used_daily = int(free_daily)

        if remaining_daily is None and isinstance(limit_daily, (int, float)) and isinstance(used_daily, (int, float)):
            remaining_daily = max(0, int(limit_daily) - int(used_daily))

        return {
            "label": str(data.get("label") or ""),
            "is_free_tier": is_free_tier,
            "limit": limit_val,
            "limit_remaining": limit_remaining,
            "usage": usage_val,
            "usage_daily": usage_daily,
            "free_daily_used": used_daily,
            "free_daily_limit": limit_daily,
            "free_daily_remaining": remaining_daily,
            "rate_limit": data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else None,
            "raw": data,
        }, None
    except Exception as exc:
        return None, sanitize_error(str(exc))


def set_active_llm_model(model_name: str, provider_id: Optional[str] = None) -> str:
    """Persist selected model directly into MPT config.app without duplicate storage."""
    active_id = (
        provider_id or config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID
    ).lower().strip()
    prov_def = get_llm_provider(active_id)
    key = prov_def.config_key("model_name") if prov_def else f"{active_id}_model_name"
    cleaned = (model_name or "").strip()
    config.app[key] = cleaned
    try:
        config.save_config()
    except Exception as exc:
        logger.warning(f"Failed to save config after updating {key}: {exc}")
    return cleaned


# ---------------------------------------------------------------------------
# Failover Chain Configuration & Readiness Inspection (V102 G1)
# ---------------------------------------------------------------------------


def build_provider_failover_chain(
    primary_provider: Optional[str] = None,
    failover_chain: Optional[Sequence[str]] = None,
) -> List[str]:
    """Return ordered deduplicated `[primary, *backups]` chain without altering single-provider default (G1)."""
    from app.services.longform.config import get_llm_failover_chain, normalize_llm_failover_chain

    active_id = (
        primary_provider or config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID
    ).lower().strip()
    raw_backups = (
        list(normalize_llm_failover_chain(failover_chain))
        if failover_chain is not None
        else get_llm_failover_chain()
    )
    ordered: List[str] = []
    seen: set[str] = set()
    for pid in [active_id, *raw_backups]:
        clean_pid = str(pid or "").strip().lower()
        if not clean_pid or clean_pid in seen:
            continue
        seen.add(clean_pid)
        ordered.append(clean_pid)
    return ordered


def _provider_key_fingerprint(provider_id: str) -> str:
    pid = (provider_id or "").strip().lower()
    prov_def = get_llm_provider(pid)
    key_name = prov_def.config_key("api_key") if prov_def else f"{pid}_api_key"
    raw_key = str(config.app.get(key_name) or "").strip()
    if not raw_key:
        return ""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]


_PAUSE_LOCK = threading.Lock()
_MEMORY_PROVIDER_PAUSES: Dict[str, Dict[str, Any]] = {}
_PAUSE_STATE_FILE = Path("storage") / "longform_cache" / "llm_provider_pauses.json"


def _pause_file_path() -> Path:
    env_override = os.environ.get("EFS_LLM_PAUSE_STATE_PATH", "").strip()
    if env_override:
        return Path(env_override)
    return _PAUSE_STATE_FILE


def _load_pause_map_unlocked() -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = dict(_MEMORY_PROVIDER_PAUSES)
    p = _pause_file_path()
    try:
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, dict):
                        merged.setdefault(k, v)
    except Exception:
        pass
    return merged


def _save_pause_map_unlocked(data: Dict[str, Dict[str, Any]]) -> None:
    p = _pause_file_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.tmp.{uuid.uuid4().hex}")
        with open(tmp, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(data, ensure_ascii=False, indent=2))
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, p)
    except Exception as exc:
        logger.debug(f"Could not persist provider pause state: {exc}")


def _next_utc_midnight_ts(now_ts: Optional[float] = None) -> float:
    now = float(time.time() if now_ts is None else now_ts)
    return float(((int(now // 86400) + 1) * 86400))


def _format_pause_until_label(paused_until_ts: Optional[float]) -> str:
    if not paused_until_ts:
        return "Sementara dijeda"
    try:
        dt_local = datetime.fromtimestamp(float(paused_until_ts), tz=timezone.utc).astimezone()
        tz_abbr = _short_tz_label(dt_local)
        return f"Pause s/d {dt_local.strftime('%H:%M')} {tz_abbr}".strip()
    except Exception:
        return "Sementara dijeda"


def mark_provider_paused(
    provider_id: str,
    *,
    reason: str,
    paused_until_ts: Optional[float] = None,
    error_detail: str = "",
) -> Dict[str, Any]:
    """Record temporary pause state for a provider (daily quota -> UTC midnight, auth/credit/404 -> 1h or until config changes)."""
    pid = (provider_id or "").strip().lower()
    if not pid:
        return {}
    now = time.time()
    if paused_until_ts is None:
        if reason == "daily_quota":
            paused_until_ts = _next_utc_midnight_ts(now)
        else:
            paused_until_ts = now + 3600.0
    prov_def = get_llm_provider(pid)
    raw_model = str(config.app.get(prov_def.config_key("model_name"), "") or "") if prov_def else ""
    eff_model, _ = resolve_effective_model(pid, raw_model)
    entry = {
        "provider": pid,
        "reason": str(reason or "paused"),
        "paused_until": float(paused_until_ts),
        "key_fp": _provider_key_fingerprint(pid),
        "model": eff_model,
        "error": format_concise_llm_error(str(error_detail or ""), pid)[:180],
        "recorded_at": now,
    }
    with _PAUSE_LOCK:
        cur = _load_pause_map_unlocked()
        cur[pid] = entry
        _MEMORY_PROVIDER_PAUSES[pid] = entry
        _save_pause_map_unlocked(cur)
    return entry


def clear_provider_pause(provider_id: Optional[str] = None) -> None:
    """Clear pause state for one provider or all providers."""
    with _PAUSE_LOCK:
        cur = _load_pause_map_unlocked()
        if provider_id is None:
            _MEMORY_PROVIDER_PAUSES.clear()
            cur.clear()
        else:
            pid = (provider_id or "").strip().lower()
            _MEMORY_PROVIDER_PAUSES.pop(pid, None)
            cur.pop(pid, None)
        _save_pause_map_unlocked(cur)


def get_provider_pause_state(provider_id: str) -> Optional[Dict[str, Any]]:
    """Return active pause entry for provider, auto-clearing if expired or if key/model changed."""
    pid = (provider_id or "").strip().lower()
    if not pid:
        return None
    with _PAUSE_LOCK:
        cur = _load_pause_map_unlocked()
        entry = cur.get(pid)
        if not entry:
            return None
        now = time.time()
        until = float(entry.get("paused_until") or 0.0)
        reason = str(entry.get("reason") or "")
        should_clear = until <= now
        if not should_clear and entry.get("key_fp") != _provider_key_fingerprint(pid):
            should_clear = True
        if not should_clear and reason == "model_not_found":
            prov_def = get_llm_provider(pid)
            raw_model = str(config.app.get(prov_def.config_key("model_name"), "") or "") if prov_def else ""
            eff_model, _ = resolve_effective_model(pid, raw_model)
            if eff_model != str(entry.get("model") or ""):
                should_clear = True
        if should_clear:
            _MEMORY_PROVIDER_PAUSES.pop(pid, None)
            cur.pop(pid, None)
            _save_pause_map_unlocked(cur)
            return None
        _MEMORY_PROVIDER_PAUSES[pid] = entry
        return dict(entry)


def get_provider_chain_status(
    chain: Optional[Sequence[str]] = None,
    *,
    primary_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Inspect configured credentials, resolved default/custom model, and pause status for each provider (G1/G2)."""
    targets = (
        list(chain)
        if chain is not None
        else build_provider_failover_chain(primary_provider=primary_provider)
    )
    results: List[Dict[str, Any]] = []
    for raw_pid in targets:
        pid = str(raw_pid or "").strip().lower()
        if not pid:
            continue
        prov_def = get_llm_provider(pid)
        readiness = get_llm_readiness(pid)
        model = str(readiness.get("model") or "").strip()
        requires_model = bool(getattr(prov_def, "requires_model_name", True)) if prov_def else True

        if not bool(readiness.get("configured")):
            status = "invalid_key" if bool(readiness.get("is_invalid")) else "no_key"
            logger.debug(f"Failover provider '{pid}' skipped ({status}); no secret logged")
            results.append({
                "provider": pid,
                "label": readiness.get("provider_label") or pid,
                "model": model,
                "status": status,
                "ready": False,
                "paused_until": None,
                "pause_label": "",
                "message": sanitize_error(str(readiness.get("message") or "")),
            })
            continue

        if requires_model and not model:
            logger.debug(f"Failover provider '{pid}' skipped (needs_model)")
            results.append({
                "provider": pid,
                "label": readiness.get("provider_label") or pid,
                "model": "",
                "status": "needs_model",
                "ready": False,
                "paused_until": None,
                "pause_label": "",
                "message": "Perlu model",
            })
            continue

        pause = get_provider_pause_state(pid)
        if pause is not None:
            p_reason = str(pause.get("reason") or "paused")
            status_code = (
                "paused_quota"
                if p_reason in ("daily_quota", "credit_exhausted")
                else ("paused_auth" if p_reason == "auth_rejected" else "paused")
            )
            until_ts = float(pause.get("paused_until") or 0.0) or None
            pause_label = _format_pause_until_label(until_ts)
            results.append({
                "provider": pid,
                "label": readiness.get("provider_label") or pid,
                "model": model,
                "status": status_code,
                "ready": False,
                "paused_until": until_ts,
                "pause_label": pause_label,
                "message": sanitize_error(str(pause.get("error") or pause_label)),
            })
            continue

        results.append({
            "provider": pid,
            "label": readiness.get("provider_label") or pid,
            "model": model,
            "status": "ready",
            "ready": True,
            "paused_until": None,
            "pause_label": "",
            "message": "Siap",
        })
    return results


def has_any_ready_llm_provider(primary_provider: Optional[str] = None) -> bool:
    """Return True if at least one provider in the configured primary+failover chain is ready."""
    return any(
        bool(item.get("ready"))
        for item in get_provider_chain_status(primary_provider=primary_provider)
    )


def is_primary_or_failover_ready(
    readiness: Dict[str, Any],
    readiness_fn: Any = None,
) -> bool:
    """Check primary readiness cleanly, consulting backup providers only when failover is configured and not mocked."""
    if bool(readiness.get("configured")) and not bool(readiness.get("is_invalid")):
        return True
    if readiness_fn is not None and getattr(readiness_fn, "__module__", "") != __name__:
        return False
    from app.services.longform.config import get_llm_failover_chain

    if not get_llm_failover_chain():
        return False
    return has_any_ready_llm_provider()


def _classify_failover_exception(exc: Exception, *, model: str = "") -> Tuple[str, Optional[str], Exception]:
    """Classify an LLM failure into (short_tag, pause_reason_or_none, normalized_exc)."""
    clean_msg = sanitize_error(str(exc))
    if isinstance(exc, LLMDailyQuotaError) or (isinstance(exc, LLMRateLimitError) and exc.is_daily):
        return "quota", "daily_quota", exc
    if isinstance(exc, LLMCreditError):
        return "credit", "credit_exhausted", exc
    if isinstance(exc, LLMAuthError):
        return "auth", "auth_rejected", exc
    if isinstance(exc, LLMModelNotFoundError):
        return "model_not_found", "model_not_found", exc
    if isinstance(exc, LLMRateLimitError):
        return "rate_limit", None, exc
    if isinstance(exc, LLMEmptyResponseError):
        return "empty", None, exc

    classified = _classify_llm_error(clean_msg, model=model)
    if isinstance(classified, LLMDailyQuotaError):
        return "quota", "daily_quota", classified
    if isinstance(classified, LLMCreditError):
        return "credit", "credit_exhausted", classified
    if isinstance(classified, LLMAuthError):
        return "auth", "auth_rejected", classified
    if isinstance(classified, LLMModelNotFoundError):
        return "model_not_found", "model_not_found", classified
    if isinstance(classified, LLMRateLimitError):
        return "rate_limit", None, classified
    if "empty" in clean_msg.lower() or "json" in clean_msg.lower():
        return "invalid_json" if "json" in clean_msg.lower() else "empty", None, exc
    return "error", None, exc


def _format_failover_attempts_summary(
    chain: Sequence[str],
    provider_errors: Dict[str, str],
    succeeded_provider: Optional[str] = None,
) -> str:
    parts: List[str] = []
    for pid in chain:
        if pid == succeeded_provider:
            parts.append(f"{pid}: ok")
        elif pid in provider_errors:
            parts.append(f"{pid}: {sanitize_error(provider_errors[pid])}")
    return "; ".join(parts)


def call_llm_with_failover(
    prompt: str,
    *,
    primary_provider: Optional[str] = None,
    failover_chain: Optional[Sequence[str]] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    fake_kind: Optional[str] = None,
    fake_kwargs: Optional[Dict[str, Any]] = None,
    force_refresh: bool = False,
    max_tokens: Optional[int] = None,
    minimize_reasoning: Optional[bool] = None,
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
    expect_json: bool = False,
    json_validator: Optional[Callable[[str], Any]] = None,
) -> LLMResponse:
    """Invoke configured primary LLM and seamlessly fail over across backup providers (G2/G3).

    - Skips providers without credentials, without a required model, or currently paused.
    - Immediately pauses & fails over on daily quota (429 free-models-per-day), credit exhausted (402),
      auth rejection (401/403), or missing model (404), and fails over when per-minute 429 retries
      or minimal-reasoning JSON retries still fail.
    - When `expect_json=True`, retries ONCE on the same provider with minimal reasoning & JSON-only
      instruction before moving to the next backup provider.
    """
    if llm_adapter is not None and getattr(llm_adapter, "provider", "") == "fake":
        resp = llm_adapter.generate(
            prompt=prompt,
            fake_kind=fake_kind,
            fake_kwargs=fake_kwargs,
            force_refresh=force_refresh,
            max_tokens=max_tokens,
            minimize_reasoning=minimize_reasoning,
        )
        resp.providers_tried = ["fake"]
        resp.provider_errors = {}
        resp.attempts_summary = "fake: ok"
        return resp

    inferred_primary = primary_provider
    if inferred_primary is None and llm_adapter is not None:
        inferred_primary = getattr(llm_adapter, "provider", None)

    chain = build_provider_failover_chain(
        primary_provider=inferred_primary,
        failover_chain=failover_chain,
    )
    statuses = {item["provider"]: item for item in get_provider_chain_status(chain)}
    provider_errors: Dict[str, str] = {}
    providers_tried: List[str] = []
    last_terminal_exc: Optional[Exception] = None

    eff_timeout = float(timeout) if timeout is not None else float(getattr(llm_adapter, "timeout", 90.0) or 90.0)
    eff_retries = (
        int(max_retries)
        if max_retries is not None
        else int(getattr(llm_adapter, "max_retries", 2 if len(chain) > 1 else 3) or 2)
    )
    eff_retry_delay = float(getattr(llm_adapter, "retry_delay", 1.5) or 1.5)
    custom_mock_adapter = (
        llm_adapter is not None
        and type(llm_adapter).generate is not LLMAdapter.generate
    )

    def _invoke_once(
        pid: str,
        model_id: str,
        prompt_text: str,
        min_reason: Optional[bool],
        toks: Optional[int],
    ) -> LLMResponse:
        use_injected = (
            llm_adapter is not None
            and len(providers_tried) == 1
            and (custom_mock_adapter or getattr(llm_adapter, "provider", pid) == pid)
        )
        if use_injected:
            try:
                return llm_adapter.generate(
                    prompt=prompt_text,
                    fake_kind=fake_kind,
                    fake_kwargs=fake_kwargs,
                    force_refresh=force_refresh,
                    max_tokens=toks,
                    minimize_reasoning=min_reason,
                )
            except TypeError:
                try:
                    return llm_adapter.generate(prompt=prompt_text, max_tokens=toks)
                except TypeError:
                    return llm_adapter.generate(prompt=prompt_text)

        adapter = LLMAdapter(
            provider=pid,
            model=model_id,
            timeout=eff_timeout,
            max_retries=eff_retries,
            retry_delay=eff_retry_delay,
            cache_dir=getattr(llm_adapter, "cache_dir", None) if llm_adapter is not None else None,
        )
        return adapter.generate(
            prompt=prompt_text,
            fake_kind=fake_kind,
            fake_kwargs=fake_kwargs,
            force_refresh=force_refresh,
            max_tokens=toks,
            minimize_reasoning=min_reason,
        )

    def _validate_json_response(resp: LLMResponse) -> bool:
        if not expect_json and json_validator is None:
            return True
        if json_validator is not None:
            return bool(json_validator(resp.content))
        from app.services.longform.script_generator import extract_json_payload
        payload = extract_json_payload(resp.content or "")
        if not payload:
            return False
        try:
            parsed = json.loads(payload)
            return isinstance(parsed, (dict, list)) and bool(parsed)
        except Exception:
            return False

    for pid in chain:
        st = statuses.get(pid, {})
        st_code = str(st.get("status") or "no_key")
        allow_mocked_bypass = custom_mock_adapter and pid == chain[0]
        if not st.get("ready") and not allow_mocked_bypass:
            provider_errors[pid] = str(st.get("pause_label") or st_code)
            logger.debug(f"LLM failover skip provider '{pid}': {provider_errors[pid]}")
            continue

        providers_tried.append(pid)
        model_id = str(st.get("model") or "")
        try:
            try:
                resp = _invoke_once(pid, model_id, prompt, minimize_reasoning, max_tokens)
                needs_json_retry = not _validate_json_response(resp)
            except LLMEmptyResponseError:
                if not (expect_json or json_validator is not None):
                    raise
                needs_json_retry = True
                resp = None  # type: ignore[assignment]

            if needs_json_retry:
                # G3: Retry ONCE on the SAME provider with minimal reasoning + strict JSON reminder
                json_retry_prompt = (
                    f"{prompt.rstrip()}\n\n"
                    "Return JSON only matching the requested schema. No prose, no markdown explanation, no reasoning text."
                )
                retry_tokens = max(int(max_tokens or 1024), 1024)
                logger.info(
                    f"LLM provider '{pid}' returned empty/non-JSON payload; retrying once with minimize_reasoning=True"
                )
                resp = _invoke_once(pid, model_id, json_retry_prompt, True, retry_tokens)
                if not _validate_json_response(resp):
                    raise LLMEmptyResponseError(
                        f"Provider '{pid}' returned invalid/empty JSON after minimal-reasoning retry",
                        finish_reason="invalid_json",
                        has_reasoning=False,
                        http_ok=True,
                        max_tokens_used=retry_tokens,
                    )

            resp.failover_used = len(providers_tried) > 1 or bool(provider_errors)
            resp.providers_tried = list(providers_tried)
            resp.provider_errors = dict(provider_errors)
            resp.attempts_summary = _format_failover_attempts_summary(
                chain, provider_errors, succeeded_provider=pid
            )
            if resp.failover_used:
                logger.info(
                    f"LLM failover succeeded using '{pid}' ({resp.model}); prior outcomes: "
                    + sanitize_error(json.dumps(provider_errors, ensure_ascii=False))
                )
            return resp
        except Exception as exc:
            err_tag, pause_reason, norm_exc = _classify_failover_exception(exc, model=model_id)
            provider_errors[pid] = err_tag
            last_terminal_exc = norm_exc
            if pause_reason:
                mark_provider_paused(pid, reason=pause_reason, error_detail=str(norm_exc))
            concise_err = format_concise_llm_error(norm_exc, pid)
            logger.warning(f"LLM provider '{pid}' failed ({err_tag}): {concise_err}")

    summary = _format_failover_attempts_summary(chain, provider_errors, succeeded_provider=None)
    if last_terminal_exc is not None:
        try:
            setattr(last_terminal_exc, "providers_tried", list(providers_tried))
            setattr(last_terminal_exc, "provider_errors", dict(provider_errors))
            setattr(last_terminal_exc, "attempts_summary", summary)
        except Exception:
            pass
        if len(chain) > 1 and summary:
            concise_last = format_concise_llm_error(last_terminal_exc, providers_tried[-1] if providers_tried else "")
            msg = sanitize_error(f"{concise_last} | Provider dicoba: {summary}")
            new_exc = type(last_terminal_exc)(msg) if type(last_terminal_exc) is not LLMRateLimitError else LLMRateLimitError(
                msg,
                is_daily=getattr(last_terminal_exc, "is_daily", False),
                retry_after_seconds=getattr(last_terminal_exc, "retry_after_seconds", None),
                reset_hint=getattr(last_terminal_exc, "reset_hint", ""),
            )
            try:
                setattr(new_exc, "providers_tried", list(providers_tried))
                setattr(new_exc, "provider_errors", dict(provider_errors))
                setattr(new_exc, "attempts_summary", summary)
            except Exception:
                pass
            raise new_exc from last_terminal_exc
        if isinstance(last_terminal_exc, (LLMDailyQuotaError, LLMCreditError, LLMAuthError, LLMModelNotFoundError, LLMRateLimitError)):
            raise last_terminal_exc
        raise LLMAdapterError(format_concise_llm_error(last_terminal_exc)) from last_terminal_exc

    paused_quota_pid = next(
        (pid for pid in chain if statuses.get(pid, {}).get("status") == "paused_quota"),
        None,
    )
    if paused_quota_pid:
        concise_q = format_concise_llm_error("free-models-per-day", paused_quota_pid)
        full_q = f"{concise_q} | Provider dicoba: {summary}" if (len(chain) > 1 and summary) else concise_q
        err_q = LLMDailyQuotaError(sanitize_error(full_q))
        setattr(err_q, "providers_tried", list(providers_tried))
        setattr(err_q, "provider_errors", dict(provider_errors))
        setattr(err_q, "attempts_summary", summary)
        raise err_q

    err = LLMAdapterError(sanitize_error(f"Semua penyedia AI dilewati/dijeda: {summary or 'tidak ada key siap'}"))
    setattr(err, "providers_tried", list(providers_tried))
    setattr(err, "provider_errors", dict(provider_errors))
    setattr(err, "attempts_summary", summary)
    raise err


def test_llm_provider(provider_id: str, timeout: float = 15.0) -> Tuple[bool, str, float]:
    """Run lightweight connection ping against a specific provider ID (clears pause on success)."""
    pid = (provider_id or config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID).lower().strip()
    active = str(config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID).lower().strip()
    if pid == active:
        return test_llm_connection()

    prov_def = get_llm_provider(pid)
    readiness = get_llm_readiness(pid)
    if not readiness.get("configured"):
        return False, sanitize_error(str(readiness.get("message") or "API key belum diatur")), 0.0
    model_name = str(readiness.get("model") or "").strip()
    if prov_def and prov_def.requires_model_name and not model_name:
        return False, "Model belum diisi", 0.0

    t0 = time.perf_counter()
    try:
        adapter = LLMAdapter(provider=pid, model=model_name, max_retries=1, timeout=float(timeout))
        resp = adapter.generate(
            prompt="Reply with exactly: OK",
            fake_kind="test_connection",
            force_refresh=True,
            max_tokens=MAX_TOKENS_TEST_CONNECTION,
            minimize_reasoning=True,
        )
        clear_provider_pause(pid)
        return True, sanitize_error((resp.content or "").strip()), float(time.perf_counter() - t0)
    except Exception as exc:
        return False, sanitize_error(str(exc)), float(time.perf_counter() - t0)


def test_all_llm_providers(chain: Optional[Sequence[str]] = None, timeout: float = 12.0) -> List[Dict[str, Any]]:
    """Sequentially verify primary + configured failover providers with safe status messages (G4)."""
    targets = list(chain) if chain is not None else build_provider_failover_chain()
    results: List[Dict[str, Any]] = []
    for raw_pid in targets:
        pid = str(raw_pid or "").strip().lower()
        if not pid:
            continue
        readiness = get_llm_readiness(pid)
        prov_def = get_llm_provider(pid)
        model_name = str(readiness.get("model") or "").strip()
        if not readiness.get("configured"):
            results.append({
                "provider": pid,
                "label": readiness.get("provider_label") or pid,
                "model": model_name,
                "ok": False,
                "status": "invalid_key" if readiness.get("is_invalid") else "no_key",
                "latency": 0.0,
                "message": "API key belum diatur" if not readiness.get("is_invalid") else "API key tidak valid",
            })
            continue
        if prov_def and prov_def.requires_model_name and not model_name:
            results.append({
                "provider": pid,
                "label": readiness.get("provider_label") or pid,
                "model": "",
                "ok": False,
                "status": "needs_model",
                "latency": 0.0,
                "message": "Perlu model",
            })
            continue
        ok, msg, latency = test_llm_provider(pid, timeout=timeout)
        results.append({
            "provider": pid,
            "label": readiness.get("provider_label") or pid,
            "model": model_name,
            "ok": bool(ok),
            "status": "ok" if ok else "failed",
            "latency": round(float(latency or 0.0), 2),
            "message": sanitize_error(msg or ""),
        })
    return results


