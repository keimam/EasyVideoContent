"""Longform Coverr API search client with process-level 401/403 short-circuit and shared resilience (V102 B1)."""

from __future__ import annotations

import hashlib
import re
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

from app.config import config
from app.services.longform.visual.base import VisualError
from app.services.longform.visual.free_providers import resilient_http_json_get

COVERR_SEARCH_URL = "https://api.coverr.co/videos"

_COVERR_AUTH_LOCK = threading.Lock()
_COVERR_AUTH_STATE: Dict[str, Any] = {
    "blocked": False,
    "key_hash": "",
    "status_code": None,
    "error": None,
}


class CoverrAuthBlockedError(VisualError):
    """Raised when Coverr rejects requests with HTTP 401/403 until the key changes or app restarts (B1)."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _extract_coverr_api_key() -> str:
    raw = config.app.get("coverr_api_keys")
    if isinstance(raw, (list, tuple)):
        return next((str(k).strip() for k in raw if str(k or "").strip()), "")
    text = str(raw or "").strip()
    if "," in text:
        return next((tok.strip() for tok in text.split(",") if tok.strip()), "")
    return text


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]


def redact_coverr_secret(text: str, api_key: str = "") -> str:
    out = str(text or "")
    if api_key and len(api_key) >= 4:
        out = out.replace(api_key, "***")
    out = re.sub(r"(?i)bearer\s+[a-z0-9_\-\.]{6,}", "Bearer ***", out)
    return out


def reset_coverr_auth_block() -> None:
    """Clear process-level Coverr auth_blocked state (for tests or key reset)."""
    with _COVERR_AUTH_LOCK:
        _COVERR_AUTH_STATE["blocked"] = False
        _COVERR_AUTH_STATE["key_hash"] = ""
        _COVERR_AUTH_STATE["status_code"] = None
        _COVERR_AUTH_STATE["error"] = None


def get_coverr_auth_status() -> Dict[str, Any]:
    """Return current Coverr process-level auth status, auto-clearing if API key changed."""
    key_hash = _hash_key(_extract_coverr_api_key())
    with _COVERR_AUTH_LOCK:
        if _COVERR_AUTH_STATE["blocked"] and _COVERR_AUTH_STATE["key_hash"] != key_hash:
            _COVERR_AUTH_STATE["blocked"] = False
            _COVERR_AUTH_STATE["key_hash"] = ""
            _COVERR_AUTH_STATE["status_code"] = None
            _COVERR_AUTH_STATE["error"] = None
        return {
            "auth_blocked": bool(_COVERR_AUTH_STATE["blocked"]),
            "status_code": _COVERR_AUTH_STATE["status_code"],
            "error": _COVERR_AUTH_STATE["error"],
        }


def is_coverr_auth_blocked() -> bool:
    return bool(get_coverr_auth_status()["auth_blocked"])


def _safe_public_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _creator_info(value: Any) -> Optional[Dict[str, str]]:
    if isinstance(value, str) and value.strip():
        return {"name": value.strip()}
    if not isinstance(value, dict):
        return None
    creator: Dict[str, str] = {}
    if value.get("id") is not None:
        creator["id"] = str(value.get("id"))
    name = value.get("name") or value.get("username")
    if name:
        creator["name"] = str(name)
    page = _safe_public_url(value.get("url") or value.get("profile_url") or value.get("profile_page"))
    if page:
        creator["profile_page"] = page
    return creator or None


def _matches_landscape(width: Any, height: Any, *, is_vertical: Any = None) -> bool:
    try:
        nw = int(float(width or 0))
        nh = int(float(height or 0))
    except (OverflowError, TypeError, ValueError):
        nw, nh = 0, 0
    if nw > 0 and nh > 0:
        return nw > nh
    if isinstance(is_vertical, bool):
        return not is_vertical
    return False


def search_coverr_videos(
    search_term: str,
    minimum_duration: int,
    *,
    video_aspect: str = "landscape",
    page_size: int = 20,
    timeout: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Query Coverr /videos directly without fetching mp4_download during search (B1).

    On HTTP 401/403, marks Coverr as ``auth_blocked`` for the remainder of the process
    (until the Coverr API key changes) and raises ``CoverrAuthBlockedError`` without issuing
    further HTTP requests.
    """
    api_key = _extract_coverr_api_key()
    if not api_key:
        return []

    key_hash = _hash_key(api_key)
    auth_st = get_coverr_auth_status()
    if auth_st["auth_blocked"]:
        code = auth_st.get("status_code") or 403
        raise CoverrAuthBlockedError(
            f"Coverr auth_blocked (HTTP {code})",
            status_code=code,
        )

    aspect_low = str(video_aspect or "landscape").lower().strip()
    params: Dict[str, Any] = {
        "query": search_term,
        "page_size": int(page_size),
        "urls": "true",
        "sort": "popular",
    }
    if "portrait" in aspect_low or "9:16" in aspect_low:
        params["filter"] = "is_vertical:true"
    elif "square" not in aspect_low and "1:1" not in aspect_low:
        params["filter"] = "is_vertical:false"

    def _on_auth_fail(status_code: int, detail: str) -> None:
        safe_detail = redact_coverr_secret(detail, api_key)
        with _COVERR_AUTH_LOCK:
            _COVERR_AUTH_STATE["blocked"] = True
            _COVERR_AUTH_STATE["key_hash"] = key_hash
            _COVERR_AUTH_STATE["status_code"] = int(status_code)
            _COVERR_AUTH_STATE["error"] = f"HTTP {status_code}"
        logger.warning(
            f"Coverr auth_blocked after HTTP {status_code}; skipping Coverr for remainder of run ({safe_detail[:80]})"
        )

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        payload = resilient_http_json_get(
            "coverr",
            COVERR_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=timeout,
            min_interval_sec=0.25,
            auth_fail_statuses=(401, 403),
            on_auth_failure=_on_auth_fail,
        )
    except VisualError as exc:
        if _COVERR_AUTH_STATE.get("blocked") and _COVERR_AUTH_STATE.get("key_hash") == key_hash:
            code = _COVERR_AUTH_STATE.get("status_code")
            raise CoverrAuthBlockedError(redact_coverr_secret(str(exc), api_key), status_code=code) from exc
        raise VisualError(redact_coverr_secret(str(exc), api_key)) from exc

    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        logger.warning("Coverr video search returned an unsupported payload shape")
        return []

    results: List[Dict[str, Any]] = []
    for v in hits:
        if not isinstance(v, dict):
            continue
        try:
            duration = int(float(v.get("duration") or 0))
        except (OverflowError, TypeError, ValueError):
            continue
        if duration < int(minimum_duration):
            continue

        video_id = v.get("id")
        urls = v.get("urls")
        if not isinstance(urls, dict):
            continue
        mp4_download_url = urls.get("mp4_download")
        if not video_id or not isinstance(mp4_download_url, str) or not mp4_download_url:
            continue

        if "square" not in aspect_low and "1:1" not in aspect_low:
            if not _matches_landscape(
                v.get("max_width"),
                v.get("max_height"),
                is_vertical=v.get("is_vertical"),
            ):
                continue

        rend_w = int(v.get("max_width") or 1920)
        rend_h = int(v.get("max_height") or 1080)
        source_page = _safe_public_url(v.get("canonical_url") or v.get("url"))
        creator = _creator_info(v.get("creator") or v.get("author"))
        source_info = {
            "provider": "coverr",
            "search_term": search_term,
            "asset_id": str(video_id),
            "source_page": source_page,
            "creator": creator,
            "rendition": {
                "id": "mp4_download",
                "width": v.get("max_width"),
                "height": v.get("max_height"),
            },
        }
        thumb_url = _safe_public_url(v.get("thumbnail") or v.get("poster"))
        title_str = str(v.get("title") or v.get("description") or "").strip() or None
        raw_tags = v.get("tags") if isinstance(v.get("tags"), list) else []

        results.append({
            "asset_id": str(video_id),
            "url": mp4_download_url,
            "duration": float(duration),
            "source_page": source_page,
            "creator": creator,
            "width": rend_w,
            "height": rend_h,
            "source_info": source_info,
            "title": title_str,
            "tags": [str(tg).strip().lower() for tg in raw_tags if str(tg or "").strip()],
            "thumbnail_url": thumb_url,
        })

    return results
