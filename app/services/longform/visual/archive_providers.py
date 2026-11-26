"""Internet Archive & Smithsonian Open Access visual providers (V102-D2).

Curated archive integrations following D1 verification (`docs/dev/ARCHIVE_SOURCES.md`):
- Keyless Internet Archive (`advancedsearch.php` + `/metadata/{id}`) restricted strictly
  to curated allowlist collections (video: `prelinger`; image: `clevelandart`) with
  client-side validation of `licenseurl`, `rights`, and `collection`, plus direct
  FFmpeg HTTP Range input-seeking trim so large full films are never downloaded.
- Short-query tier helper (`build_archive_short_queries`) stripping style adjectives
  when full shot queries yield zero archive hits.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from loguru import logger

from app.services.longform.visual.base import (
    VisualAsset,
    VisualCandidate,
    VisualError,
    compute_file_content_hash,
    is_commercial_license_allowed,
    record_download_io,
    sanitize_source_error,
)
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.free_providers import (
    EASYFACELESS_USER_AGENT,
    _FreeApiProviderBase,
    _extract_tags_from_texts,
    _respect_rate_limit,
    cached_json_get,
)

# ---------------------------------------------------------------------------
# Curated Internet Archive collection allowlist (Architect Decision #1)
# Extending this allowlist requires verifying collection licenseurl consistency
# via an explicit D1 metadata audit first (never add blanket `opensource`).
# ---------------------------------------------------------------------------
IA_VIDEO_COLLECTION_ALLOWLIST: Tuple[str, ...] = (
    "prelinger",  # Prelinger Archives: PD/CC-marked ephemeral & historical films verified in D1
)
IA_IMAGE_COLLECTION_ALLOWLIST: Tuple[str, ...] = (
    "clevelandart",  # Cleveland Museum of Art Open Access: verified CC0 1.0 artworks in D1
)
IA_COLLECTION_REASONS: Dict[str, str] = {
    "prelinger": "Prelinger Archives ephemeral/historical video collection; PD/CC metadata verified in D1.",
    "clevelandart": "Cleveland Museum of Art Open Access image collection; CC0 1.0 licenseurl verified in D1.",
}

# Exact quoted licenseurl values for Elasticsearch queries (avoid `*...*` wildcards which 500 on archive.org)
IA_ALLOWED_LICENSE_URLS: Tuple[str, ...] = (
    "http://creativecommons.org/licenses/publicdomain/",
    "https://creativecommons.org/licenses/publicdomain/",
    "http://creativecommons.org/publicdomain/mark/1.0/",
    "https://creativecommons.org/publicdomain/mark/1.0/",
    "http://creativecommons.org/publicdomain/zero/1.0/",
    "https://creativecommons.org/publicdomain/zero/1.0/",
    "http://creativecommons.org/licenses/by/4.0/",
    "https://creativecommons.org/licenses/by/4.0/",
    "http://creativecommons.org/licenses/by/3.0/",
    "https://creativecommons.org/licenses/by/3.0/",
    "http://creativecommons.org/licenses/by-sa/4.0/",
    "https://creativecommons.org/licenses/by-sa/4.0/",
    "http://creativecommons.org/licenses/by-sa/3.0/",
    "https://creativecommons.org/licenses/by-sa/3.0/",
)

_IA_NC_ND_RE = re.compile(r"(?:[-/]nc\b|[-/]nd\b|\bby-nc|\bby-nd)", re.IGNORECASE)
_IA_ALLOWED_URL_PREFIXES = (
    "creativecommons.org/publicdomain/zero/",
    "creativecommons.org/publicdomain/mark/",
    "creativecommons.org/licenses/publicdomain",
    "creativecommons.org/licenses/by/",
    "creativecommons.org/licenses/by-sa/",
)
_NEGATED_COPYRIGHT_RE = re.compile(
    r"(?:\bno\s+known\s+|\bno\s+|\bfree\s+of\s+(?:known\s+)?|\bwithout\s+|\bnot\s+in\s+|\bout\s+of\s+|\bexpired\s+)copyright",
    re.IGNORECASE,
)
_RIGHTS_REJECT_RE = re.compile(
    r"(?:\bfair\s+use\b|\ball\s+rights\s+reserved\b|\bpermission\b|\bin\s+copyright\b|\bcopyrighted\b|©)",
    re.IGNORECASE,
)
_COPYRIGHT_WORD_RE = re.compile(r"\bcopyright\b", re.IGNORECASE)

_STYLE_ADJECTIVE_NOISE = {
    "illustration", "illustrated", "cinematic", "4k", "8k", "uhd", "hd", "fhd",
    "photorealistic", "realistic", "hyperrealistic", "dramatic", "moody", "epic",
    "atmospheric", "stylized", "aesthetic", "vintage", "retro", "film", "grain",
    "lighting", "lit", "shot", "wide", "close", "closeup", "medium", "aerial",
    "slow", "motion", "timelapse", "scenic", "view", "background", "backdrop",
    "dark", "gloomy", "golden", "hour", "vibrant", "bokeh", "depth", "field",
    "rendered", "painting", "digital", "art", "artwork", "concept", "establishing",
}

_STOP_WORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or",
    "by", "from", "near", "into", "over", "under", "through", "across", "during",
    "around", "is", "are", "was", "were", "be", "that", "this", "these", "those",
}

_GENERIC_FILLER_NOISE = {
    "scene", "moment", "symbolizing", "showing", "depicting", "style", "look",
    "looking", "people", "person", "thing", "object",
}


def build_archive_short_queries(
    query: str,
    anchor_terms: Optional[Iterable[str]] = None,
    *,
    max_variants: int = 2,
) -> List[str]:
    """Build 1-2 word core English fallback queries for archive catalogs (Architect Decision #2).

    Strips cinematic/style/camera adjectives (e.g. ``illustration``, ``cinematic``, ``4k``,
    ``dramatic``, ``moody``) and returns concise fallback search strings without touching
    primary stock/AI query pipelines.
    """
    raw = str(query or "").strip()
    if not raw:
        return []

    tokens = re.findall(r"[A-Za-z][A-Za-z'-]{1,}", raw.lower())
    filtered: List[str] = []
    seen: Set[str] = set()
    for tok in tokens:
        if tok in _STOP_WORDS or tok in _STYLE_ADJECTIVE_NOISE or tok in _GENERIC_FILLER_NOISE:
            continue
        if tok not in seen:
            seen.add(tok)
            filtered.append(tok)

    # Fold in explicit core English anchor tokens if provided and not already present
    for anc in (anchor_terms or []):
        for tok in re.findall(r"[A-Za-z][A-Za-z'-]{1,}", str(anc or "").lower()):
            if tok in _STOP_WORDS or tok in _STYLE_ADJECTIVE_NOISE or tok in _GENERIC_FILLER_NOISE:
                continue
            if tok not in seen:
                seen.add(tok)
                filtered.append(tok)

    if not filtered:
        return []

    variants: List[str] = []
    if len(filtered) >= 2:
        pair = f"{filtered[0]} {filtered[1]}"
        if pair.lower() != raw.lower():
            variants.append(pair)
    single = filtered[0]
    if single.lower() != raw.lower() and single not in variants:
        variants.append(single)
    return variants[: max(1, int(max_variants))]


def _normalize_list_field(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val.strip()] if val.strip() else []
    if isinstance(val, (list, tuple, set)):
        return [str(x).strip() for x in val if str(x or "").strip()]
    return [str(val).strip()] if str(val).strip() else []


def _parse_ia_duration_seconds(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val) if float(val) > 0 else None
    text = str(val).strip()
    if not text:
        return None
    try:
        num = float(text)
        return num if num > 0 else None
    except ValueError:
        pass
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
            if len(nums) == 2:
                return nums[0] * 60.0 + nums[1]
            if len(nums) == 3:
                return nums[0] * 3600.0 + nums[1] * 60.0 + nums[2]
        except ValueError:
            return None
    return None


def normalize_ia_license_label(licenseurl: str) -> str:
    """Convert a verified Creative Commons / Public Domain licenseurl into canonical label."""
    low = str(licenseurl or "").strip().lower()
    if "publicdomain/zero" in low or "/zero/" in low:
        return "CC0 1.0"
    if "publicdomain/mark" in low or "/mark/" in low:
        return "Public Domain Mark 1.0"
    if "licenses/publicdomain" in low:
        return "Public Domain"
    if "licenses/by-sa/" in low:
        ver = "4.0" if "4.0" in low else ("3.0" if "3.0" in low else "")
        return f"CC BY-SA {ver}".strip()
    if "licenses/by/" in low:
        ver = "4.0" if "4.0" in low else ("3.0" if "3.0" in low else "")
        return f"CC BY {ver}".strip()
    return "Public Domain"


def _rights_text_indicates_restriction(rights_values: List[str]) -> Optional[str]:
    """Return rejection reason if rights/copyright metadata indicates restriction or Fair Use."""
    for raw in rights_values:
        txt = str(raw or "").strip()
        if not txt:
            continue
        if _RIGHTS_REJECT_RE.search(txt):
            return f"IA item rights restricted: {txt[:80]}"
        if _COPYRIGHT_WORD_RE.search(txt) and not _NEGATED_COPYRIGHT_RE.search(txt):
            return f"IA item rights claim copyright: {txt[:80]}"
    return None


def validate_ia_item_metadata(
    meta: Dict[str, Any],
    *,
    video_collections: Tuple[str, ...] = IA_VIDEO_COLLECTION_ALLOWLIST,
    image_collections: Tuple[str, ...] = IA_IMAGE_COLLECTION_ALLOWLIST,
) -> Tuple[bool, str, str, str]:
    """Validate collection allowlist, licenseurl, and rights on an IA doc or metadata dict.

    Returns ``(ok, reason, media_kind, license_label)`` where ``media_kind`` is
    ``"video"`` or ``"image"``.
    """
    collections = {c.lower() for c in _normalize_list_field(meta.get("collection"))}
    mediatype = str(meta.get("mediatype") or "").strip().lower()
    in_video = bool(collections & {c.lower() for c in video_collections})
    in_image = bool(collections & {c.lower() for c in image_collections})

    if not in_video and not in_image:
        return False, f"Collection not in IA allowlist: {sorted(collections)}", "", ""

    if mediatype == "movies" and in_video:
        media_kind = "video"
    elif mediatype == "image" and in_image:
        media_kind = "image"
    elif in_video and not in_image:
        media_kind = "video"
    elif in_image and not in_video:
        media_kind = "image"
    else:
        return False, f"Mismatched mediatype={mediatype!r} for collections={sorted(collections)}", "", ""

    lic_urls = _normalize_list_field(meta.get("licenseurl"))
    lic_url = lic_urls[0] if lic_urls else ""
    if not lic_url:
        return False, "Missing IA licenseurl", media_kind, ""

    low_url = lic_url.lower()
    if _IA_NC_ND_RE.search(low_url):
        return False, f"NC/ND IA licenseurl rejected: {lic_url}", media_kind, ""

    stripped = re.sub(r"^https?://", "", low_url)
    if not any(stripped.startswith(prefix) for prefix in _IA_ALLOWED_URL_PREFIXES):
        return False, f"Unrecognized IA licenseurl: {lic_url}", media_kind, ""

    lic_label = normalize_ia_license_label(lic_url)
    lic_ok, lic_reason = is_commercial_license_allowed(lic_label)
    if not lic_ok:
        return False, lic_reason, media_kind, ""

    rights_vals = _normalize_list_field(meta.get("rights")) + _normalize_list_field(meta.get("possible-copyright-status"))
    rights_err = _rights_text_indicates_restriction(rights_vals)
    if rights_err:
        return False, rights_err, media_kind, ""

    return True, "ok", media_kind, lic_label


def compute_ia_segment_window(
    duration_seconds: float,
    target_duration: float,
    slot_index: int = 0,
) -> Tuple[float, float]:
    """Compute a deterministic HTTP Range trim ``(offset_sec, seg_duration_sec)`` within ``[10% .. dur-10%-seg]``.

    Never returns offsets in the opening or closing 10% slate/credits whenever
    the film is long enough to hold the requested window.
    """
    dur = max(1.0, float(duration_seconds or 0.0))
    seg_dur = min(dur, max(2.0, float(target_duration or 5.0) + 1.5))

    if dur <= seg_dur + 0.5:
        return 0.0, round(seg_dur, 2)

    margin = dur * 0.10
    win_start = margin
    win_end = dur - margin - seg_dur
    if win_end <= win_start:
        # Film is slightly longer than seg_dur, not long enough for full 10%+10% padding
        mid_start = max(0.0, (dur - seg_dur) * 0.5)
        return round(mid_start, 2), round(seg_dur, 2)

    # Spread shots smoothly across the safe interior without colliding on slot 0/1/2...
    fractions = (0.0, 0.52, 0.26, 0.78, 0.13, 0.65, 0.39, 0.91)
    idx = int(slot_index) % len(fractions)
    frac = fractions[idx]
    offset = win_start + frac * (win_end - win_start)
    offset = max(win_start, min(win_end, offset))
    return round(offset, 2), round(seg_dur, 2)


def _select_ia_mp4_file(
    files: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Select an MP4 rendition (640x480–1920x1080) and avoid multi-GB ProRes/MPEG-2/MOV."""
    candidates: List[Tuple[Tuple[int, int, int], Dict[str, Any]]] = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        fname = str(f.get("name") or "").strip()
        low_name = fname.lower()
        fmt = str(f.get("format") or "").strip()
        low_fmt = fmt.lower()

        if not low_name.endswith(".mp4"):
            continue
        if any(bad in low_fmt for bad in ("prores", "mpeg2", "mpeg-2", "cinepak", "matroska")):
            continue
        if low_fmt and low_fmt not in ("h.264", "mpeg4", "hires mpeg4", "512kb mpeg4"):
            continue

        try:
            fsize = int(f.get("size") or 0)
        except (TypeError, ValueError):
            fsize = 0
        # Guard against multi-GB mezzanine MP4 files (>1.5 GB)
        if fsize > 1_500_000_000:
            continue

        try:
            w = int(float(f.get("width") or 0))
            h = int(float(f.get("height") or 0))
        except (TypeError, ValueError):
            w, h = 0, 0

        if (w and (w < 640 or w > 1920)) or (h and (h < 480 or h > 1080)):
            continue

        dur = _parse_ia_duration_seconds(f.get("length"))
        if "512kb" in low_fmt:
            tier = 2
        elif h >= 720 or "h.264" in low_fmt or "hires" in low_fmt:
            tier = 0
        else:
            tier = 1

        rank = (tier, abs((h or 480) - 720), fsize)
        candidates.append((
            rank,
            {
                "name": fname,
                "format": fmt or "MPEG4",
                "width": w or 640,
                "height": h or 480,
                "size": fsize,
                "duration_seconds": dur,
            },
        ))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _select_ia_image_file(files: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Select a high-res JPEG image (>=1280px when reported) and skip huge TIFF masters."""
    jpegs: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        fname = str(f.get("name") or "").strip()
        low_name = fname.lower()
        fmt = str(f.get("format") or "").strip().lower()
        if not (low_name.endswith(".jpg") or low_name.endswith(".jpeg")):
            continue
        if low_name.endswith("_thumb.jpg") or "__ia_thumb" in low_name or fmt == "item tile":
            continue

        try:
            fsize = int(f.get("size") or 0)
        except (TypeError, ValueError):
            fsize = 0
        try:
            w = int(float(f.get("width") or 0))
            h = int(float(f.get("height") or 0))
        except (TypeError, ValueError):
            w, h = 0, 0

        if w and w < 1280:
            continue

        is_full_hint = 0 if ("_full." in low_name or w >= 1280) else 1
        rank = (is_full_hint, -(w or fsize))
        jpegs.append((
            rank,
            {
                "name": fname,
                "format": f.get("format") or "JPEG",
                "width": w or 1600,
                "height": h or 1066,
                "size": fsize,
            },
        ))

    if not jpegs:
        return None
    jpegs.sort(key=lambda item: item[0])
    return jpegs[0][1]


class InternetArchiveVisualProvider(_FreeApiProviderBase):
    """Keyless Internet Archive provider using curated collections + FFmpeg HTTP Range trim."""

    SEARCH_URL = "https://archive.org/advancedsearch.php"
    METADATA_URL = "https://archive.org/metadata/{identifier}"
    DOWNLOAD_BASE = "https://archive.org/download"
    DETAILS_BASE = "https://archive.org/details"

    @property
    def name(self) -> str:
        return "internet_archive"

    @classmethod
    def _build_collection_clause(cls) -> str:
        vid_parts = [f"collection:{c}" for c in IA_VIDEO_COLLECTION_ALLOWLIST]
        img_parts = [f"collection:{c}" for c in IA_IMAGE_COLLECTION_ALLOWLIST]
        clauses: List[str] = []
        if vid_parts:
            vid_expr = " OR ".join(vid_parts) if len(vid_parts) > 1 else vid_parts[0]
            clauses.append(f"(({vid_expr}) AND mediatype:movies)")
        if img_parts:
            img_expr = " OR ".join(img_parts) if len(img_parts) > 1 else img_parts[0]
            clauses.append(f"(({img_expr}) AND mediatype:image)")
        return "(" + " OR ".join(clauses) + ")"

    @classmethod
    def _build_license_clause(cls) -> str:
        quoted = [f'licenseurl:"{u}"' for u in IA_ALLOWED_LICENSE_URLS]
        return "(" + " OR ".join(quoted) + ")"

    @classmethod
    def build_search_query(cls, query: str) -> str:
        clean_q = re.sub(r'["()]+', " ", str(query or "")).strip()
        return f"({clean_q}) AND {cls._build_collection_clause()} AND {cls._build_license_clause()}"

    @staticmethod
    def _pick_next_slot_for_item(
        identifier: str,
        excluded_ids: Optional[Set[str]],
        preferred_slot: Optional[int] = None,
    ) -> int:
        used_slots: Set[int] = set()
        prefix = f"ia_{identifier}_seg"
        for cid in (excluded_ids or set()):
            if cid == f"ia_{identifier}":
                used_slots.add(0)
            elif cid.startswith(prefix):
                tail = cid[len(prefix):]
                try:
                    used_slots.add(int(tail))
                except ValueError:
                    pass
        slot = max(0, int(preferred_slot)) if preferred_slot is not None else 0
        for _ in range(16):
            if slot not in used_slots:
                return slot
            slot += 1
        return slot

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
        if not str(query or "").strip():
            return [], 0, []

        params = {
            "q": self.build_search_query(query),
            "fl[]": ["identifier", "title", "description", "subject", "creator", "collection", "mediatype", "licenseurl", "rights"],
            "rows": 15,
            "page": 1,
            "output": "json",
            "sort[]": "downloads desc",
        }
        payload = cached_json_get(
            self.name,
            self.SEARCH_URL,
            params=params,
            cache=self.cache,
            min_interval_sec=0.4,
        )
        docs = ((payload.get("response") or {}).get("docs")) or []

        raw_items: List[Dict[str, Any]] = []
        rejected_early: List[str] = []
        preferred_slot = kwargs.get("shot_index")

        for doc in docs[:15]:
            if not isinstance(doc, dict):
                continue
            identifier = str(doc.get("identifier") or "").strip()
            if not identifier:
                continue

            ok_doc, rej_doc, _, _ = validate_ia_item_metadata(doc)
            if not ok_doc:
                rejected_early.append(f"{identifier}: {rej_doc}")
                continue

            # Fetch item metadata for authoritative rights/collection/licenseurl and file selection
            try:
                meta_url = self.METADATA_URL.format(identifier=urllib.parse.quote(identifier, safe=""))
                meta_payload = cached_json_get(
                    f"{self.name}_meta",
                    meta_url,
                    cache=self.cache,
                    min_interval_sec=0.35,
                )
            except Exception as exc:
                rejected_early.append(f"{identifier}: metadata error: {sanitize_source_error(exc)}")
                continue

            item_meta = meta_payload.get("metadata") if isinstance(meta_payload.get("metadata"), dict) else doc
            # Merge identifier/doc fallbacks in case mocked metadata omits redundant fields
            merged_meta = dict(doc)
            merged_meta.update({k: v for k, v in item_meta.items() if v is not None})

            ok_meta, rej_meta, media_kind, lic_label = validate_ia_item_metadata(merged_meta)
            if not ok_meta:
                rejected_early.append(f"{identifier}: {rej_meta}")
                continue

            files = meta_payload.get("files") if isinstance(meta_payload.get("files"), list) else []
            title_vals = _normalize_list_field(merged_meta.get("title"))
            title = title_vals[0] if title_vals else identifier
            desc_vals = _normalize_list_field(merged_meta.get("description"))
            desc = desc_vals[0] if desc_vals else ""
            creator_vals = _normalize_list_field(merged_meta.get("creator"))
            creator = creator_vals[0] if creator_vals else "Internet Archive Contributor"
            subjects = _normalize_list_field(merged_meta.get("subject"))
            collections = _normalize_list_field(merged_meta.get("collection"))
            lic_url = _normalize_list_field(merged_meta.get("licenseurl"))[0]
            item_page_url = f"{self.DETAILS_BASE}/{urllib.parse.quote(identifier, safe='')}"

            if media_kind == "video":
                chosen = _select_ia_mp4_file(files)
                if not chosen:
                    rejected_early.append(f"{identifier}: no compatible 480p-1080p MP4")
                    continue
                item_dur = chosen.get("duration_seconds") or _parse_ia_duration_seconds(merged_meta.get("runtime")) or max(15.0, float(target_duration or 5.0) + 1.5)
                slot_idx = self._pick_next_slot_for_item(identifier, excluded_ids, preferred_slot=preferred_slot)
                seg_offset, seg_dur = compute_ia_segment_window(item_dur, target_duration, slot_index=slot_idx)
                file_url = f"{self.DOWNLOAD_BASE}/{urllib.parse.quote(identifier, safe='')}/{urllib.parse.quote(chosen['name'], safe='/')}"
                cid = f"ia_{identifier}_seg{slot_idx}"
                source_type = "stock_video"
            else:
                chosen = _select_ia_image_file(files)
                if not chosen:
                    rejected_early.append(f"{identifier}: no suitable JPEG image (>=1280px)")
                    continue
                seg_offset, seg_dur = 0.0, float(target_duration or 5.0)
                file_url = f"{self.DOWNLOAD_BASE}/{urllib.parse.quote(identifier, safe='')}/{urllib.parse.quote(chosen['name'], safe='/')}"
                cid = f"ia_{identifier}"
                source_type = "stock_image"

            tags = _extract_tags_from_texts(title, desc, " ".join(subjects))
            thumb_url = f"https://archive.org/services/img/{urllib.parse.quote(identifier, safe='')}"

            raw_items.append({
                "candidate_id": cid,
                "source_type": source_type,
                "url": file_url,
                "width": chosen["width"],
                "height": chosen["height"],
                "duration_seconds": seg_dur,
                "author": creator,
                "author_url": item_page_url,
                "source_url": item_page_url,
                "license": lic_label,
                "title": title,
                "thumbnail_url": thumb_url,
                "tags": tags,
                "rendition_name": chosen.get("format"),
                "source_info": {
                    "identifier": identifier,
                    "collection": collections,
                    "mediatype": merged_meta.get("mediatype"),
                    "license_url": lic_url,
                    "creator": creator,
                    "item_url": item_page_url,
                    "file_name": chosen["name"],
                    "file_size_bytes": chosen.get("size"),
                    "segment_offset": seg_offset,
                    "segment_duration": seg_dur,
                },
            })

        # Score video & image subsets separately so 480p Prelinger films are not dropped
        # purely because an image item in the same response is >=1280x720.
        vid_raw = [it for it in raw_items if it.get("source_type") == "stock_video"]
        img_raw = [it for it in raw_items if it.get("source_type") != "stock_video"]
        score_kw = dict(
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
        accepted_vid, _, rej_vid = self._score_and_filter_items(vid_raw, **score_kw) if vid_raw else ([], 0, [])
        accepted_img, _, rej_img = self._score_and_filter_items(img_raw, **score_kw) if img_raw else ([], 0, [])
        accepted = sorted(accepted_vid + accepted_img, key=lambda c: c.relevance_score, reverse=True)[:limit]
        return accepted, len(docs), (rejected_early + rej_vid + rej_img)

    def search_candidates(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        **kwargs: Any,
    ) -> List[VisualCandidate]:
        cands, _, _ = self.search_with_diagnostics(query, target_duration, limit=limit, **kwargs)
        return cands

    def _trim_remote_video_segment(
        self,
        stream_url: str,
        dest_path: Path,
        *,
        offset_sec: float,
        duration_sec: float,
        timeout: int = 120,
    ) -> Path:
        """Trim ``[offset_sec .. offset_sec + duration_sec]`` directly via FFmpeg HTTP Range seeking.

        Never falls back to downloading the full remote file on FFmpeg failure.
        """
        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        seg_key = hashlib.sha256(
            f"{stream_url.strip()}|ss={offset_sec:.2f}|t={duration_sec:.2f}".encode("utf-8")
        ).hexdigest()[:24]
        cached_seg = self.cache.cache_dir / f"ia_trim_{seg_key}.mp4"
        cached_seg.parent.mkdir(parents=True, exist_ok=True)

        def _atomic_copy(src_file: Path) -> Path:
            tmp_d = dest.parent / f"{dest.name}.tmp.{uuid.uuid4().hex}"
            try:
                shutil.copyfile(src_file, tmp_d)
                os.replace(tmp_d, dest)
                return dest
            finally:
                if tmp_d.exists():
                    tmp_d.unlink(missing_ok=True)

        if cached_seg.is_file() and cached_seg.stat().st_size > 0:
            record_download_io(cache_hit=True)
            try:
                os.utime(cached_seg, None)
            except Exception:
                pass
            return _atomic_copy(cached_seg)

        record_download_io(cache_hit=False)
        _respect_rate_limit(f"dl:{self.name}", 0.25)

        tmp_cached = self.cache.cache_dir / f"{cached_seg.name}.tmp.{uuid.uuid4().hex}.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "2",
            "-user_agent", EASYFACELESS_USER_AGENT,
            "-ss", f"{max(0.0, float(offset_sec)):.2f}",
            "-i", stream_url,
            "-t", f"{max(1.0, float(duration_sec)):.2f}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-vf", "scale='min(1920,iw)':-2,scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-an",
            str(tmp_cached),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except Exception as exc:
            if tmp_cached.exists():
                tmp_cached.unlink(missing_ok=True)
            raise VisualError(f"IA HTTP segment trim failed ({sanitize_source_error(exc)})") from exc

        if res.returncode != 0 or not tmp_cached.is_file() or tmp_cached.stat().st_size == 0:
            stderr_tail = sanitize_source_error((res.stderr or "")[-300:])
            if tmp_cached.exists():
                tmp_cached.unlink(missing_ok=True)
            raise VisualError(f"IA FFmpeg trim failed ({res.returncode}): {stderr_tail}")

        os.replace(tmp_cached, cached_seg)
        return _atomic_copy(cached_seg)

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        if not candidate.url:
            raise VisualError(f"Candidate {candidate.candidate_id} has no download URL")

        if candidate.source_type == "stock_video":
            s_info = candidate.source_info or {}
            offset = float(s_info.get("segment_offset") or 0.0)
            seg_dur = float(s_info.get("segment_duration") or candidate.duration_seconds or 6.5)
            dest = Path(dest_path).resolve()
            if dest.suffix.lower() != ".mp4":
                dest = dest.with_suffix(".mp4")

            final_path = self._trim_remote_video_segment(
                candidate.url,
                dest,
                offset_sec=offset,
                duration_sec=seg_dur,
            )
            content_hash = compute_file_content_hash(final_path)
            asset = VisualAsset(
                asset_id=candidate.candidate_id,
                provider=self.name,
                source_type="stock_video",
                local_path=final_path,
                content_hash=content_hash,
                author=candidate.author,
                author_url=candidate.author_url,
                source_url=candidate.source_url,
                license=candidate.license,
                width=int(candidate.width or 640),
                height=int(candidate.height or 480),
                duration_seconds=seg_dur,
                title=candidate.title,
            )
            if self.credits_manager is not None:
                self.credits_manager.record_asset_credit(candidate)
            return asset

        return super().fetch_asset(candidate, dest_path)
