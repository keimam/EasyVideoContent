"""Free Keyless Visual Providers with API Etiquette & Caching (Stage 8C.2 D9.2, D9.4).

Implements three zero-key commercial-safe visual providers following BaseVisualProvider:
1. WikimediaVisualProvider (MediaWiki API - video & images; auto-converts WebM/OGV -> MP4)
2. NasaVisualProvider (images-api.nasa.gov - video & images)
3. OpenverseVisualProvider (api.openverse.org anonymous commercial filter - photos -> Ken Burns)
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from loguru import logger

from app.config import config
from app.services.branding.constants import APP_VERSION
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualError,
    canonical_candidate_dedup_key,
    compute_file_content_hash,
    is_commercial_license_allowed,
    record_provider_status_event,
    record_search_io,
)
from app.services.longform.visual.cache import GlobalMediaCache, get_global_cache_dir
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.stock import score_candidate_relevance

EASYFACELESS_USER_AGENT = (
    f"EasyFacelessStudio/{APP_VERSION} "
    "(https://github.com/naseh-ngulik/EasyFacelessStudio; "
    "opensource@easyfaceless.studio)"
)

_STATE_LOCK = threading.Lock()
_PROVIDER_LOCKS: Dict[str, threading.Lock] = {}
_LAST_REQUEST_TS: Dict[str, float] = {}
_PROVIDER_COOLDOWN_UNTIL: Dict[str, float] = {}
_PROVIDER_CONSECUTIVE_ERRORS: Dict[str, int] = {}
_SEARCH_CACHE_TTL_SECONDS = 86400.0  # 24h polite search cache
_TRANSIENT_COOLDOWN_SECONDS = 120.0
_TRANSIENT_FAILURE_THRESHOLD = 2


def get_active_network_circuit_params() -> Tuple[int, int, float]:
    """Return active (timeout_sec, failure_threshold, cooldown_seconds) from longform config (B3)."""
    try:
        from app.services.longform.config import get_network_profile_settings

        prof = get_network_profile_settings()
        return (
            int(prof["timeout"]),
            int(prof["failure_threshold"]),
            float(prof["cooldown_seconds"]),
        )
    except Exception:
        return (6, _TRANSIENT_FAILURE_THRESHOLD, _TRANSIENT_COOLDOWN_SECONDS)


def check_provider_cooldown(provider_key: str) -> float:
    """Return remaining cooldown seconds if provider is paused, else 0.0."""
    pk = str(provider_key or "").lower().strip()
    with _STATE_LOCK:
        until = float(_PROVIDER_COOLDOWN_UNTIL.get(pk, 0.0) or 0.0)
    rem = until - time.time()
    return rem if rem > 0.0 else 0.0


def trip_provider_cooldown(provider_key: str, duration_seconds: float) -> None:
    """Place provider into temporary cooldown for duration_seconds."""
    pk = str(provider_key or "").lower().strip()
    with _STATE_LOCK:
        _PROVIDER_COOLDOWN_UNTIL[pk] = max(
            float(_PROVIDER_COOLDOWN_UNTIL.get(pk, 0.0) or 0.0),
            time.time() + max(1.0, float(duration_seconds)),
        )


def clear_provider_transient_errors(provider_key: str) -> None:
    """Reset consecutive transient error counter after a healthy response."""
    pk = str(provider_key or "").lower().strip()
    with _STATE_LOCK:
        _PROVIDER_CONSECUTIVE_ERRORS[pk] = 0


def note_provider_transient_failure(provider_key: str, detail: str) -> bool:
    """Record a 5xx/timeout failure and open shared circuit breaker if threshold reached.

    Returns True if cooldown was tripped on this call.
    """
    pk = str(provider_key or "").lower().strip()
    _timeout_s, threshold, cooldown_s = get_active_network_circuit_params()
    record_provider_status_event(pk, "timeout", error=detail)
    tripped = False
    with _STATE_LOCK:
        cnt = int(_PROVIDER_CONSECUTIVE_ERRORS.get(pk, 0)) + 1
        _PROVIDER_CONSECUTIVE_ERRORS[pk] = cnt
        if cnt >= threshold:
            _PROVIDER_COOLDOWN_UNTIL[pk] = time.time() + cooldown_s
            tripped = True
            logger.warning(
                f"{pk} temporarily paused for {int(cooldown_s)}s "
                f"after {cnt} consecutive upstream timeout/5xx errors ({detail[:80]})"
            )
    if tripped:
        record_provider_status_event(pk, "cooldown_trip", error=detail)
    return tripped


def reset_provider_circuit_breakers() -> None:
    """Clear per-provider cooldowns and consecutive error counts (useful for tests)."""
    with _STATE_LOCK:
        _PROVIDER_COOLDOWN_UNTIL.clear()
        _PROVIDER_CONSECUTIVE_ERRORS.clear()


def clear_provider_cooldown(provider_key: str) -> None:
    """Clear active cooldown and error streak for a single provider (e.g. on API key change)."""
    pk = str(provider_key or "").lower().strip()
    with _STATE_LOCK:
        _PROVIDER_COOLDOWN_UNTIL.pop(pk, None)
        _PROVIDER_CONSECUTIVE_ERRORS.pop(pk, None)


def _get_provider_lock(provider_key: str) -> threading.Lock:
    with _STATE_LOCK:
        lk = _PROVIDER_LOCKS.get(provider_key)
        if lk is None:
            lk = threading.Lock()
            _PROVIDER_LOCKS[provider_key] = lk
        return lk


def _respect_rate_limit(provider_key: str, min_interval_sec: float = 0.35) -> None:
    """Enforce a thread-safe polite per-provider minimum interval between live network calls (D9.4, V1)."""
    if min_interval_sec <= 0:
        return
    p_lock = _get_provider_lock(provider_key)
    with p_lock:
        now = time.monotonic()
        with _STATE_LOCK:
            last = _LAST_REQUEST_TS.get(provider_key)
        if last is not None:
            elapsed = now - last
            if elapsed < min_interval_sec:
                time.sleep(min_interval_sec - elapsed)
        with _STATE_LOCK:
            _LAST_REQUEST_TS[provider_key] = time.monotonic()


def _strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", " ", str(text))
    clean = html.unescape(clean)
    return re.sub(r"\s+", " ", clean).strip()


def _extract_tags_from_texts(*parts: Optional[str]) -> List[str]:
    tags: List[str] = []
    seen: Set[str] = set()
    for part in parts:
        if not part:
            continue
        for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{1,}", str(part).lower()):
            if tok not in seen:
                seen.add(tok)
                tags.append(tok)
    return tags[:40]


def _get_api_search_cache_dir(cache: GlobalMediaCache) -> Path:
    d = Path(cache.cache_dir) / "api_search"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resilient_http_json_get(
    provider_key: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    min_interval_sec: float = 0.35,
    auth_fail_statuses: Tuple[int, ...] = (),
    on_auth_failure: Optional[Any] = None,
) -> Dict[str, Any]:
    """Shared HTTP JSON GET with rate limiting and 429/5xx/timeout circuit breaker (A2.2, B1, B3)."""
    prof_timeout, _threshold, _cooldown_s = get_active_network_circuit_params()
    eff_timeout = int(timeout) if timeout is not None else int(prof_timeout)

    rem = check_provider_cooldown(provider_key)
    if rem > 0.0:
        raise VisualError(f"{provider_key} cooldown active ({int(rem)}s remaining)")

    last_err = ""
    for attempt in range(1, 3):
        _respect_rate_limit(provider_key, min_interval_sec=min_interval_sec)
        record_search_io(provider_key, cache_hit=False)
        try:
            resp = requests.get(
                url,
                params=params,
                headers=headers,
                proxies=getattr(config, "proxy", None),
                timeout=eff_timeout,
            )
            status = int(getattr(resp, "status_code", 200))
            raw_text = getattr(resp, "text", None)
            body_text = raw_text if isinstance(raw_text, str) else ""
            if auth_fail_statuses and status in auth_fail_statuses:
                last_err = body_text.strip() or f"HTTP {status} unauthorized/rejected"
                auth_outcome = None
                if callable(on_auth_failure):
                    auth_outcome = on_auth_failure(status, body_text)
                if auth_outcome == "rate_limited":
                    raise VisualError(f"{provider_key} rate_limited (HTTP {status})")
                raise VisualError(f"{provider_key} API key rejected (HTTP {status})")
            if status in (429, 403):
                body_low = body_text[:300].lower()
                last_err = f"HTTP {status} rate limit/challenge"
                if "just a moment" in body_low or status == 403 or attempt >= 2:
                    trip_provider_cooldown(provider_key, _cooldown_s)
                    record_provider_status_event(provider_key, "cooldown_trip", error=last_err)
                    break
                wait_s = float((getattr(resp, "headers", {}) or {}).get("Retry-After") or 2.0)
                time.sleep(min(wait_s, 5.0))
                continue
            if status in (500, 502, 503, 504):
                last_err = f"HTTP {status} upstream gateway error"
                note_provider_transient_failure(provider_key, last_err)
                break
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, dict):
                payload = {"items": payload}
            clear_provider_transient_errors(provider_key)
            record_provider_status_event(provider_key, "ok")
            return payload
        except VisualError:
            raise
        except requests.exceptions.Timeout as exc:
            last_err = f"Timeout ({exc})"
            note_provider_transient_failure(provider_key, last_err)
            break
        except Exception as exc:
            last_err = str(exc)
            if "timeout" in last_err.lower() or "504" in last_err or "503" in last_err:
                note_provider_transient_failure(provider_key, last_err)
                break
            if attempt < 2:
                time.sleep(0.3)
            else:
                record_provider_status_event(provider_key, "error", error=last_err)

    raise VisualError(f"{provider_key} API request failed: {last_err}")


def cached_json_get(
    provider_key: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    cache: Optional[GlobalMediaCache] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    min_interval_sec: float = 0.35,
    auth_fail_statuses: Tuple[int, ...] = (),
    on_auth_failure: Optional[Any] = None,
) -> Dict[str, Any]:
    """Perform HTTP GET with User-Agent, rate-limit etiquette, 429/5xx cooldown circuit breaker, and 24h JSON cache."""
    media_cache = cache or GlobalMediaCache()
    cache_dir = _get_api_search_cache_dir(media_cache)
    key_raw = json.dumps({"p": provider_key, "u": url, "q": params or {}}, sort_keys=True)
    cache_file = cache_dir / f"{provider_key}_{hashlib.sha256(key_raw.encode('utf-8')).hexdigest()[:24]}.json"

    if cache_file.is_file():
        try:
            age = time.time() - cache_file.stat().st_mtime
            if age <= _SEARCH_CACHE_TTL_SECONDS:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    record_search_io(provider_key, cache_hit=True)
                    return data
        except Exception:
            pass

    req_headers = {"User-Agent": EASYFACELESS_USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    payload = resilient_http_json_get(
        provider_key,
        url,
        params=params,
        headers=req_headers,
        timeout=timeout,
        min_interval_sec=min_interval_sec,
        auth_fail_statuses=auth_fail_statuses,
        on_auth_failure=on_auth_failure,
    )
    try:
        tmp_c = cache_file.with_suffix(".tmp")
        tmp_c.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_c, cache_file)
    except Exception:
        pass
    return payload


def convert_video_to_h264_mp4(src_path: Path, dest_mp4: Path) -> Path:
    """Transcode WebM/OGV video to clean H.264 yuv420p MP4 using bundled FFmpeg (D9.2)."""
    src = Path(src_path).resolve()
    out = Path(dest_mp4).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out.parent / f"{out.stem}.conv.{uuid.uuid4().hex}.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-t", "25",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-vf", "scale='min(1920,iw)':-2,scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-an",
        str(tmp_out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if res.returncode != 0 or not tmp_out.is_file() or tmp_out.stat().st_size == 0:
        if tmp_out.exists():
            tmp_out.unlink(missing_ok=True)
        raise VisualError(f"FFmpeg WebM/OGV->MP4 conversion failed ({res.returncode}): {res.stderr[-400:]}")
    os.replace(tmp_out, out)
    return out


class _FreeApiProviderBase(BaseVisualProvider):
    """Shared scoring, 720p resolution preference, diagnostics, and cached download logic (D9.2)."""

    def __init__(
        self,
        cache: Optional[GlobalMediaCache] = None,
        credits_manager: Optional[CreditsManager] = None,
    ) -> None:
        self.cache = cache or GlobalMediaCache()
        self.credits_manager = credits_manager

    @property
    def is_fake(self) -> bool:
        return False

    def validate_credentials(self) -> None:
        # Keyless free public API
        return None

    def _score_and_filter_items(
        self,
        raw_items: List[Dict[str, Any]],
        *,
        query: str,
        limit: int,
        excluded_ids: Optional[Set[str]],
        required_subject_terms: Optional[List[str]],
        blocked_terms: Optional[List[str]],
        visual_keywords: Optional[List[str]],
        negative_terms: Optional[List[str]],
        anchor_terms: Optional[List[str]],
        avoid_terms: Optional[List[str]],
        min_relevance_score: Optional[float],
        narration_text: Optional[str] = None,
        strict_ai_relevance: bool = False,
        recommended_visual_sources: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[VisualCandidate], int, List[str]]:
        """Apply resolution preference (>=720p unless no alt), license check, and relevance scoring."""
        excluded = excluded_ids or set()
        rejected_reasons: List[str] = []

        # 720p preference: keep >=1280x720 items if any exist; allow lower-res fallback only if none >=720p
        hd_items = [it for it in raw_items if int(it.get("width") or 0) >= 1280 and int(it.get("height") or 0) >= 720]
        pool = hd_items if hd_items else raw_items

        accepted: List[VisualCandidate] = []
        seen_canon_keys: Set[str] = set()
        for it in pool:
            cid = str(it["candidate_id"])
            if cid in excluded or any(c.candidate_id == cid for c in accepted):
                continue
            cand_stub = {
                "candidate_id": cid,
                "provider": self.name,
                "url": it.get("url"),
                "source_url": it.get("source_url"),
            }
            canon_k = canonical_candidate_dedup_key(cand_stub)
            if canon_k and canon_k in seen_canon_keys:
                continue

            w = int(it.get("width") or 0)
            h = int(it.get("height") or 0)
            if w <= 0 or h <= 0 or (w / max(1, h)) < 1.15:
                rejected_reasons.append(f"non-landscape ({w}x{h})")
                continue

            is_low_res = bool(w < 1280 or h < 720)
            tags = it.get("tags") or []
            is_valid, score, reason = score_candidate_relevance(
                candidate_tags=tags,
                search_query=query,
                visual_keywords=visual_keywords,
                required_subject_terms=required_subject_terms,
                blocked_terms=blocked_terms,
                negative_terms=negative_terms,
                anchor_terms=anchor_terms,
                avoid_terms=avoid_terms,
                min_relevance_score=min_relevance_score,
                is_low_res=is_low_res,
                narration_text=narration_text,
                strict_ai_relevance=strict_ai_relevance,
                source_name=self.name,
                recommended_visual_sources=recommended_visual_sources,
            )
            if not is_valid:
                rejected_reasons.append(reason)
                continue

            accepted.append(
                VisualCandidate(
                    candidate_id=cid,
                    provider=self.name,
                    source_type=it["source_type"],
                    url=it["url"],
                    local_path=None,
                    author=it.get("author"),
                    author_url=it.get("author_url"),
                    source_url=it.get("source_url"),
                    license=it["license"],
                    width=w,
                    height=h,
                    duration_seconds=float(it.get("duration_seconds") or 0.0),
                    source_info=it.get("source_info") or {},
                    tags=tags,
                    relevance_score=score,
                    rendition_name=f"{self.name} ({w}x{h})",
                    is_low_res=is_low_res,
                    title=it.get("title"),
                    thumbnail_url=it.get("thumbnail_url") or it.get("url"),
                )
            )
            if canon_k:
                seen_canon_keys.add(canon_k)

        accepted.sort(key=lambda c: c.relevance_score, reverse=True)
        return accepted[:limit], len(raw_items), rejected_reasons

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        if not candidate.url:
            raise VisualError(f"Candidate {candidate.candidate_id} has no download URL")

        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        parsed_ext = os.path.splitext(urllib.parse.urlparse(candidate.url).path)[1].lower()
        if not self.cache.is_cached(candidate.url):
            _respect_rate_limit(f"dl:{self.name}", 0.25)

        if candidate.source_type == "stock_video" and parsed_ext in (".webm", ".ogv", ".ogg"):
            raw_dl = dest.with_suffix(parsed_ext)
            self.cache.download_or_get(
                candidate.url,
                raw_dl,
                headers={"User-Agent": EASYFACELESS_USER_AGENT},
            )
            final_mp4 = dest.with_suffix(".mp4")
            convert_video_to_h264_mp4(raw_dl, final_mp4)
            if raw_dl != final_mp4:
                raw_dl.unlink(missing_ok=True)
            final_dest = final_mp4
        else:
            self.cache.download_or_get(
                candidate.url,
                dest,
                headers={"User-Agent": EASYFACELESS_USER_AGENT},
            )
            final_dest = dest

        content_hash = compute_file_content_hash(final_dest)
        if self.credits_manager:
            self.credits_manager.record_asset_credit(candidate)

        return VisualAsset(
            asset_id=candidate.candidate_id,
            provider=candidate.provider,
            source_type=candidate.source_type,
            local_path=final_dest,
            content_hash=content_hash,
            author=candidate.author,
            author_url=candidate.author_url,
            source_url=candidate.source_url,
            license=candidate.license,
            width=candidate.width,
            height=candidate.height,
            duration_seconds=candidate.duration_seconds,
            title=candidate.title,
        )


class WikimediaVisualProvider(_FreeApiProviderBase):
    """Wikimedia Commons MediaWiki API provider (video WebM/OGV/MP4 & high-res photos)."""

    API_URL = "https://commons.wikimedia.org/w/api.php"

    @property
    def name(self) -> str:
        return "wikimedia"

    def search_with_diagnostics(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[Set[str]] = None,
        required_subject_terms: Optional[List[str]] = None,
        blocked_terms: Optional[List[str]] = None,
        visual_keywords: Optional[List[str]] = None,
        negative_terms: Optional[List[str]] = None,
        anchor_terms: Optional[List[str]] = None,
        avoid_terms: Optional[List[str]] = None,
        min_relevance_score: Optional[float] = None,
        **kwargs: Any,
    ) -> Tuple[List[VisualCandidate], int, List[str]]:
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": f"{query} filetype:bitmap|video",
            "gsrnamespace": 6,
            "gsrlimit": 25,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata|mediatype",
            "iiurlwidth": 1920,
            "format": "json",
            "origin": "*",
        }
        payload = cached_json_get("wikimedia", self.API_URL, params=params, cache=self.cache)
        pages = (payload.get("query") or {}).get("pages") or {}
        if isinstance(pages, list):
            page_iter = pages
        elif isinstance(pages, dict):
            page_iter = list(pages.values())
        else:
            page_iter = []

        raw_items: List[Dict[str, Any]] = []
        rejected_early: List[str] = []

        for page in page_iter:
            ii_list = page.get("imageinfo") or []
            if not ii_list:
                continue
            ii = ii_list[0]
            mime = str(ii.get("mime") or "").lower()
            mediatype = str(ii.get("mediatype") or "").upper()
            orig_url = str(ii.get("url") or "").strip()
            thumb_url = str(ii.get("thumburl") or "").strip()

            if mime.startswith("video/") or mediatype == "VIDEO":
                source_type = "stock_video"
                media_url = orig_url
            elif mime in ("image/jpeg", "image/jpg", "image/png", "image/webp") or (
                mediatype == "BITMAP" and mime != "image/gif" and mime != "image/tiff"
            ):
                source_type = "stock_image"
                # Prefer iiurlwidth thumbnail for huge BMP/PNG if >=1280, else orig
                tw = int(ii.get("thumbwidth") or 0)
                th = int(ii.get("thumbheight") or 0)
                if thumb_url and tw >= 1280 and th >= 720:
                    media_url = thumb_url
                    ii_w, ii_h = tw, th
                else:
                    media_url = orig_url
                    ii_w = int(ii.get("width") or 0)
                    ii_h = int(ii.get("height") or 0)
            else:
                continue

            if source_type == "stock_video":
                ii_w = int(ii.get("width") or 0)
                ii_h = int(ii.get("height") or 0)

            file_bytes = int(ii.get("size") or 0)
            vid_dur = float(ii.get("duration") or 0.0)
            if source_type == "stock_video" and ((file_bytes > 80 * 1024 * 1024) or (vid_dur > 180.0)):
                rejected_early.append(f"video too large/long ({file_bytes // (1024 * 1024)}MB, {vid_dur:.0f}s)")
                continue
            if source_type == "stock_image" and media_url == orig_url and file_bytes > 40 * 1024 * 1024:
                rejected_early.append(f"image too large ({file_bytes // (1024 * 1024)}MB)")
                continue

            if not media_url:
                continue

            extmeta = ii.get("extmetadata") or {}
            lic_name = _strip_html((extmeta.get("LicenseShortName") or {}).get("value")) or _strip_html(
                (extmeta.get("License") or {}).get("value")
            )
            lic_ok, lic_reason = is_commercial_license_allowed(lic_name)
            if not lic_ok:
                rejected_early.append(lic_reason)
                continue

            page_title = str(page.get("title") or "").removeprefix("File:")
            obj_name = _strip_html((extmeta.get("ObjectName") or {}).get("value")) or page_title
            desc = _strip_html((extmeta.get("ImageDescription") or {}).get("value"))
            artist = _strip_html((extmeta.get("Artist") or {}).get("value")) or "Wikimedia Commons Contributor"
            cats = _strip_html((extmeta.get("Categories") or {}).get("value"))
            page_id = str(page.get("pageid") or hashlib.sha256(media_url.encode("utf-8")).hexdigest()[:12])
            desc_url = str(ii.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/File:{urllib.parse.quote(page_title.replace(' ', '_'))}")

            tags = _extract_tags_from_texts(obj_name, page_title, desc, cats)
            dur = float(ii.get("duration") or target_duration or 5.0) if source_type == "stock_video" else float(target_duration or 5.0)

            raw_items.append({
                "candidate_id": f"wikimedia_{page_id}",
                "source_type": source_type,
                "url": media_url,
                "width": ii_w,
                "height": ii_h,
                "duration_seconds": dur,
                "author": artist,
                "author_url": None,
                "source_url": desc_url,
                "license": lic_name,
                "title": obj_name,
                "thumbnail_url": thumb_url or media_url,
                "tags": tags,
                "source_info": {"mime": mime, "thumb_url": thumb_url, "license_url": _strip_html((extmeta.get("LicenseUrl") or {}).get("value"))},
            })

        accepted, seen_count, rej_score = self._score_and_filter_items(
            raw_items,
            query=query,
            limit=limit,
            excluded_ids=excluded_ids,
            required_subject_terms=required_subject_terms,
            blocked_terms=blocked_terms,
            visual_keywords=visual_keywords,
            negative_terms=negative_terms,
            anchor_terms=anchor_terms,
            avoid_terms=avoid_terms,
            min_relevance_score=min_relevance_score,
            narration_text=kwargs.get("narration_text"),
            strict_ai_relevance=bool(kwargs.get("strict_ai_relevance", False)),
            recommended_visual_sources=kwargs.get("recommended_visual_sources"),
        )
        return accepted, len(page_iter), (rejected_early + rej_score)

    def search_candidates(self, query: str, target_duration: float, limit: int = 3, **kwargs: Any) -> List[VisualCandidate]:
        cands, _, _ = self.search_with_diagnostics(query, target_duration, limit=limit, **kwargs)
        return cands


class NasaVisualProvider(_FreeApiProviderBase):
    """NASA Image and Video Library API provider (images-api.nasa.gov)."""

    SEARCH_URL = "https://images-api.nasa.gov/search"
    ASSET_URL = "https://images-api.nasa.gov/asset/{nasa_id}"

    @property
    def name(self) -> str:
        return "nasa"

    def _resolve_nasa_media(self, nasa_id: str, media_type: str, preview_href: Optional[str]) -> Tuple[Optional[str], str]:
        """Resolve canonical downloadable MP4 (preferred) or high-res JPG from NASA asset manifest."""
        try:
            manifest = cached_json_get(
                "nasa_asset",
                self.ASSET_URL.format(nasa_id=urllib.parse.quote(nasa_id)),
                cache=self.cache,
            )
            items = (manifest.get("collection") or {}).get("items") or []
            hrefs = [str(it.get("href") or "") for it in items if isinstance(it, dict) and it.get("href")]
        except Exception:
            hrefs = []

        if media_type == "video":
            for pattern in ("~orig.mp4", "~large.mp4", "~medium.mp4", ".mp4"):
                for h in hrefs:
                    if h.lower().endswith(pattern):
                        return h, "stock_video"

        for pattern in ("~large.jpg", "~orig.jpg", "~medium.jpg", ".jpg", ".png"):
            for h in hrefs:
                if h.lower().endswith(pattern):
                    return h, "stock_image"

        if preview_href:
            return preview_href, "stock_image"
        return None, "stock_image"

    def search_with_diagnostics(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[Set[str]] = None,
        required_subject_terms: Optional[List[str]] = None,
        blocked_terms: Optional[List[str]] = None,
        visual_keywords: Optional[List[str]] = None,
        negative_terms: Optional[List[str]] = None,
        anchor_terms: Optional[List[str]] = None,
        avoid_terms: Optional[List[str]] = None,
        min_relevance_score: Optional[float] = None,
        **kwargs: Any,
    ) -> Tuple[List[VisualCandidate], int, List[str]]:
        params = {
            "q": query,
            "media_type": "video,image",
            "page_size": 15,
        }
        payload = cached_json_get("nasa", self.SEARCH_URL, params=params, cache=self.cache)
        items = (payload.get("collection") or {}).get("items") or []

        raw_items: List[Dict[str, Any]] = []
        for entry in items[:15]:
            data_list = entry.get("data") or []
            if not data_list:
                continue
            meta = data_list[0]
            nasa_id = str(meta.get("nasa_id") or "").strip()
            media_type = str(meta.get("media_type") or "image").lower()
            if not nasa_id:
                continue

            links = entry.get("links") or []
            preview_href = next(
                (str(lk.get("href")) for lk in links if lk.get("rel") == "preview" and lk.get("href")),
                None,
            )
            if not preview_href:
                continue

            title = str(meta.get("title") or nasa_id).strip()
            desc = str(meta.get("description") or "").strip()
            kw_list = meta.get("keywords") or []
            creator = (
                meta.get("photographer")
                or meta.get("secondary_creator")
                or (f"NASA/{meta['center']}" if meta.get("center") else "NASA")
            )
            tags = _extract_tags_from_texts(title, desc, " ".join(str(k) for k in kw_list))
            raw_items.append({
                "candidate_id": f"nasa_{nasa_id}",
                "source_type": "stock_video" if media_type == "video" else "stock_image",
                "url": preview_href,
                "width": 1920,
                "height": 1080,
                "duration_seconds": float(target_duration or 5.0),
                "author": str(creator),
                "author_url": "https://images.nasa.gov/",
                "source_url": f"https://images.nasa.gov/details/{urllib.parse.quote(nasa_id)}",
                "license": "NASA Media (Public Domain / U.S. Government)",
                "title": title,
                "thumbnail_url": preview_href,
                "tags": tags,
                "source_info": {
                    "nasa_id": nasa_id,
                    "media_type": media_type,
                    "preview_href": preview_href,
                    "center": meta.get("center"),
                },
            })

        accepted, seen_count, rej_reasons = self._score_and_filter_items(
            raw_items,
            query=query,
            limit=limit,
            excluded_ids=excluded_ids,
            required_subject_terms=required_subject_terms,
            blocked_terms=blocked_terms,
            visual_keywords=visual_keywords,
            negative_terms=negative_terms,
            anchor_terms=anchor_terms,
            avoid_terms=avoid_terms,
            min_relevance_score=min_relevance_score,
            narration_text=kwargs.get("narration_text"),
            strict_ai_relevance=bool(kwargs.get("strict_ai_relevance", False)),
            recommended_visual_sources=kwargs.get("recommended_visual_sources"),
        )
        resolved_accepted: List[VisualCandidate] = []
        for cand in accepted:
            s_info = cand.source_info or {}
            media_url, resolved_type = self._resolve_nasa_media(
                str(s_info.get("nasa_id") or ""),
                str(s_info.get("media_type") or "image"),
                s_info.get("preview_href") or cand.url,
            )
            if media_url:
                cand.url = media_url
                cand.source_type = resolved_type
                resolved_accepted.append(cand)
        return resolved_accepted, len(items), rej_reasons

    def search_candidates(self, query: str, target_duration: float, limit: int = 3, **kwargs: Any) -> List[VisualCandidate]:
        cands, _, _ = self.search_with_diagnostics(query, target_duration, limit=limit, **kwargs)
        return cands


class OpenverseVisualProvider(_FreeApiProviderBase):
    """Openverse anonymous API provider (commercial-allowed photos -> Ken Burns clips)."""

    API_URL = "https://api.openverse.org/v1/images/"

    @property
    def name(self) -> str:
        return "openverse"

    def search_with_diagnostics(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[Set[str]] = None,
        required_subject_terms: Optional[List[str]] = None,
        blocked_terms: Optional[List[str]] = None,
        visual_keywords: Optional[List[str]] = None,
        negative_terms: Optional[List[str]] = None,
        anchor_terms: Optional[List[str]] = None,
        avoid_terms: Optional[List[str]] = None,
        min_relevance_score: Optional[float] = None,
        **kwargs: Any,
    ) -> Tuple[List[VisualCandidate], int, List[str]]:
        params = {
            "q": query,
            "license_type": "commercial",
            "aspect_ratio": "wide",
            "page_size": 20,
        }
        payload = cached_json_get("openverse", self.API_URL, params=params, cache=self.cache, min_interval_sec=0.45)
        results = payload.get("results") or []

        raw_items: List[Dict[str, Any]] = []
        rejected_early: List[str] = []

        for r in results:
            lic_code = str(r.get("license") or "").strip().lower()
            lic_ver = str(r.get("license_version") or "").strip()
            lic_label = f"CC {lic_code.upper()} {lic_ver}".strip() if lic_code not in ("cc0", "pdm") else lic_code.upper()
            lic_ok, lic_reason = is_commercial_license_allowed(lic_label)
            if not lic_ok:
                rejected_early.append(lic_reason)
                continue

            img_url = str(r.get("url") or "").strip()
            if not img_url:
                continue

            w = int(r.get("width") or 1280)
            h = int(r.get("height") or 720)
            oid = str(r.get("id") or hashlib.sha256(img_url.encode("utf-8")).hexdigest()[:12])
            title = str(r.get("title") or oid).strip()
            creator = str(r.get("creator") or "Openverse Contributor").strip()
            raw_tags = [str(t.get("name") if isinstance(t, dict) else t).lower() for t in (r.get("tags") or [])]
            tags = list(dict.fromkeys(raw_tags + _extract_tags_from_texts(title)))
            src_url = str(r.get("foreign_landing_url") or r.get("url") or "").strip()

            raw_items.append({
                "candidate_id": f"openverse_{oid}",
                "source_type": "stock_image",
                "url": img_url,
                "width": w,
                "height": h,
                "duration_seconds": float(target_duration or 5.0),
                "author": creator,
                "author_url": r.get("creator_url"),
                "source_url": src_url,
                "license": lic_label,
                "title": title,
                "thumbnail_url": str(r.get("thumbnail") or "").strip() or img_url,
                "tags": tags,
                "source_info": {"source": r.get("source"), "license_url": r.get("license_url")},
            })

        accepted, seen_count, rej_score = self._score_and_filter_items(
            raw_items,
            query=query,
            limit=limit,
            excluded_ids=excluded_ids,
            required_subject_terms=required_subject_terms,
            blocked_terms=blocked_terms,
            visual_keywords=visual_keywords,
            negative_terms=negative_terms,
            anchor_terms=anchor_terms,
            avoid_terms=avoid_terms,
            min_relevance_score=min_relevance_score,
            narration_text=kwargs.get("narration_text"),
            strict_ai_relevance=bool(kwargs.get("strict_ai_relevance", False)),
            recommended_visual_sources=kwargs.get("recommended_visual_sources"),
        )
        return accepted, len(results), (rejected_early + rej_score)

    def search_candidates(self, query: str, target_duration: float, limit: int = 3, **kwargs: Any) -> List[VisualCandidate]:
        cands, _, _ = self.search_with_diagnostics(query, target_duration, limit=limit, **kwargs)
        return cands
