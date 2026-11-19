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
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from loguru import logger

from app.config import config
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualError,
    compute_file_content_hash,
    is_commercial_license_allowed,
)
from app.services.longform.visual.cache import GlobalMediaCache, get_global_cache_dir
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.stock import score_candidate_relevance

EASYFACELESS_USER_AGENT = (
    "EasyFacelessStudio/1.0.0 "
    "(https://github.com/naseh-ngulik/EasyFacelessStudio; "
    "opensource@easyfaceless.studio)"
)

_LAST_REQUEST_TS: Dict[str, float] = {}
_PROVIDER_COOLDOWN_UNTIL: Dict[str, float] = {}
_SEARCH_CACHE_TTL_SECONDS = 86400.0  # 24h polite search cache


def _respect_rate_limit(provider_key: str, min_interval_sec: float = 0.35) -> None:
    """Enforce a polite per-provider minimum interval between live network calls (D9.4)."""
    if min_interval_sec <= 0:
        return
    now = time.monotonic()
    last = _LAST_REQUEST_TS.get(provider_key)
    if last is not None:
        elapsed = now - last
        if elapsed < min_interval_sec:
            time.sleep(min_interval_sec - elapsed)
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


def cached_json_get(
    provider_key: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    cache: Optional[GlobalMediaCache] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    min_interval_sec: float = 0.35,
) -> Dict[str, Any]:
    """Perform HTTP GET with User-Agent, rate-limit etiquette, 429 Retry-After, and 24h JSON cache."""
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
                    return data
        except Exception:
            pass

    req_headers = {"User-Agent": EASYFACELESS_USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    last_err = ""
    cooldown_until = _PROVIDER_COOLDOWN_UNTIL.get(provider_key, 0.0)
    if cooldown_until > time.time():
        raise VisualError(f"{provider_key} rate-limited cooldown active ({int(cooldown_until - time.time())}s remaining)")

    for attempt in range(1, 3):
        _respect_rate_limit(provider_key, min_interval_sec=min_interval_sec)
        try:
            resp = requests.get(
                url,
                params=params,
                headers=req_headers,
                proxies=getattr(config, "proxy", None),
                timeout=timeout,
            )
            if resp.status_code in (429, 403):
                body_low = (resp.text or "")[:300].lower()
                last_err = f"HTTP {resp.status_code} rate limit/challenge"
                if "just a moment" in body_low or resp.status_code == 403 or attempt >= 2:
                    _PROVIDER_COOLDOWN_UNTIL[provider_key] = time.time() + 60.0
                    break
                wait_s = float(resp.headers.get("Retry-After") or 2.0)
                time.sleep(min(wait_s, 5.0))
                continue
            resp.raise_for_status()
            payload = resp.json()
            if not isinstance(payload, dict):
                payload = {"items": payload}
            try:
                tmp_c = cache_file.with_suffix(".tmp")
                tmp_c.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp_c, cache_file)
            except Exception:
                pass
            return payload
        except Exception as exc:
            last_err = str(exc)
            if attempt < 2:
                time.sleep(1.0)

    raise VisualError(f"{provider_key} API request failed: {last_err}")


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
    ) -> Tuple[List[VisualCandidate], int, List[str]]:
        """Apply resolution preference (>=720p unless no alt), license check, and relevance scoring."""
        excluded = excluded_ids or set()
        rejected_reasons: List[str] = []

        # 720p preference: keep >=1280x720 items if any exist; allow lower-res fallback only if none >=720p
        hd_items = [it for it in raw_items if int(it.get("width") or 0) >= 1280 and int(it.get("height") or 0) >= 720]
        pool = hd_items if hd_items else raw_items

        accepted: List[VisualCandidate] = []
        for it in pool:
            cid = str(it["candidate_id"])
            if cid in excluded or any(c.candidate_id == cid for c in accepted):
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
                )
            )

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
                "tags": tags,
                "source_info": {"mime": mime, "license_url": _strip_html((extmeta.get("LicenseUrl") or {}).get("value"))},
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
        )
        return accepted, len(results), (rejected_early + rej_score)

    def search_candidates(self, query: str, target_duration: float, limit: int = 3, **kwargs: Any) -> List[VisualCandidate]:
        cands, _, _ = self.search_with_diagnostics(query, target_duration, limit=limit, **kwargs)
        return cands
