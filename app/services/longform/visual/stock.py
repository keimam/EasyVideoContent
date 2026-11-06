"""Stock video provider for longform projects via Pixabay, Pexels, and Coverr (4.1, V1, S1, S2).

Features:
- S1: Relevance scoring against query and preset visual_keywords.
- S1: Filtering by required_subject_terms and blocked_terms.
- S1: Candidate selection based on highest relevance score rather than first hit.
- S2: Selection of smallest rendition with width >= 1920 (e.g. 1080p instead of 4K) to optimize cache and bandwidth.
- Deduplication per provider + asset ID across the project.
- Top-3 candidate ranking per shot.
- Fail-fast credential checks with clear configuration messages covering all providers.
- Default search priority order: Pixabay -> Pexels -> Coverr.
- Downloads cached through GlobalMediaCache (storage/longform_cache/).
- Automatic attribution tracking in export/credits.txt.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from loguru import logger

from app.config import config
from app.models.schema import VideoAspect
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualConfigurationError,
    VisualError,
    compute_file_content_hash,
)
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.credits import CreditsManager
from app.services.material import (
    search_videos_coverr,
    search_videos_pexels,
    search_videos_pixabay,
)

STOP_WORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "up", "about", "into", "over", "after", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "this", "that",
    "shot", "scene", "video", "footage", "clip", "cinematic", "realism", "view"
}


def extract_query_keywords(text: str) -> List[str]:
    """Tokenize query into lowercase keywords, removing stop words (S1)."""
    words = [w.lower().strip() for w in re.findall(r"[A-Za-z0-9_-]+", text or "")]
    return [w for w in words if len(w) > 2 and w not in STOP_WORDS]


def score_candidate_relevance(
    candidate_tags: List[str],
    search_query: str,
    visual_keywords: Optional[List[str]] = None,
    required_subject_terms: Optional[List[str]] = None,
    blocked_terms: Optional[List[str]] = None,
) -> Tuple[bool, float, str]:
    """Score candidate relevance against search query and preset rules (S1).

    Returns:
        (is_valid, relevance_score, reason)
    """
    req_terms = [t.lower().strip() for t in (required_subject_terms or []) if t.strip()]
    block_terms = [t.lower().strip() for t in (blocked_terms or []) if t.strip()]
    cand_tags = [t.lower().strip() for t in (candidate_tags or []) if t.strip()]

    # 1. Blocked terms check
    for bt in block_terms:
        if any(bt in tag for tag in cand_tags):
            return False, 0.0, f"Blocked term matched: {bt}"

    # 2. Required subject terms check
    if req_terms:
        has_req = any(
            any(req == tag or req in tag or tag in req for req in req_terms)
            for tag in cand_tags
        )
        if not has_req:
            return False, 0.0, f"Candidate lacks required subject terms: {req_terms}"

    # 3. Query keywords overlap
    q_words = extract_query_keywords(search_query)
    q_matches = 0
    if q_words:
        for qw in q_words:
            if any(qw in tag or tag in qw for tag in cand_tags):
                q_matches += 1
        q_ratio = q_matches / len(q_words)
    else:
        q_ratio = 0.5

    # 4. Preset visual keywords overlap
    vk_list = visual_keywords or []
    vk_matches = 0
    if vk_list:
        for vk in vk_list:
            vk_words = extract_query_keywords(vk)
            if any(any(vkw in tag or tag in vkw for tag in cand_tags) for vkw in vk_words):
                vk_matches += 1
        vk_ratio = min(vk_matches / len(vk_list), 1.0)
    else:
        vk_ratio = 0.0

    score = 0.7 * q_ratio + 0.3 * vk_ratio
    # If candidate passed required subject check, provide a baseline relevance
    if req_terms:
        score = max(score, 0.25)

    return True, round(score, 3), "Accepted"


def extract_narrower_query(
    search_query: str,
    required_subject_terms: Optional[List[str]] = None,
) -> str:
    """Extract a narrower query (subject term + 1-2 key nouns) for retry escalation (S1)."""
    q_words = extract_query_keywords(search_query)
    req_terms = [t.lower().strip() for t in (required_subject_terms or []) if t.strip()]

    chosen_subject = None
    for rt in req_terms:
        if rt in (search_query or "").lower():
            chosen_subject = rt
            break
    if not chosen_subject and req_terms:
        chosen_subject = req_terms[0]

    other_keywords = [w for w in q_words if not (chosen_subject and chosen_subject in w)]
    narrow_parts = []
    if chosen_subject:
        narrow_parts.append(chosen_subject)
    narrow_parts.extend(other_keywords[:2])
    return " ".join(narrow_parts) if narrow_parts else (chosen_subject or search_query)


def _has_non_empty_key(cfg_key: str) -> bool:
    """Check if config key has at least one non-empty string."""
    val = config.app.get(cfg_key)
    if isinstance(val, (list, tuple)):
        return any(bool(str(k or "").strip()) for k in val)
    return bool(str(val or "").strip())


class StockVisualProvider(BaseVisualProvider):
    """Pixabay, Pexels, and Coverr video adapter implementing BaseVisualProvider (4.1, V1, S1, S2)."""

    def __init__(
        self,
        provider_name: str = "pixabay",
        cache: Optional[GlobalMediaCache] = None,
        credits_manager: Optional[CreditsManager] = None,
    ) -> None:
        self.provider_name = provider_name.lower().strip()
        if self.provider_name not in ("pexels", "pixabay", "coverr", "stock"):
            raise ValueError(f"Unsupported stock provider: {provider_name}")
        self.cache = cache or GlobalMediaCache()
        self.credits_manager = credits_manager

    @property
    def name(self) -> str:
        return self.provider_name

    @property
    def is_fake(self) -> bool:
        return False

    def validate_credentials(self) -> None:
        """Validate API key presence, raising VisualConfigurationError if missing (V1)."""
        has_pixabay = _has_non_empty_key("pixabay_api_keys")
        has_pexels = _has_non_empty_key("pexels_api_keys")
        has_coverr = _has_non_empty_key("coverr_api_keys")

        if self.provider_name == "pixabay" and not has_pixabay:
            raise VisualConfigurationError(
                "Pixabay API key is not configured. Please set 'pixabay_api_keys' "
                f"in config.toml ({config.config_file}) or WebUI Settings before fetching stock visual assets."
            )
        elif self.provider_name == "pexels" and not has_pexels:
            raise VisualConfigurationError(
                "Pexels API key is not configured. Please set 'pexels_api_keys' "
                f"in config.toml ({config.config_file}) or WebUI Settings. "
                "(Note: Pexels has paused new API key issuance; you can use Pixabay or Coverr instead)."
            )
        elif self.provider_name == "coverr" and not has_coverr:
            raise VisualConfigurationError(
                "Coverr API key is not configured. Please set 'coverr_api_keys' "
                f"in config.toml ({config.config_file}) or WebUI Settings before fetching stock visual assets."
            )
        elif self.provider_name == "stock":
            if not (has_pixabay or has_pexels or has_coverr):
                raise VisualConfigurationError(
                    "No stock video API key is configured. Stock providers supported upstream:\n"
                    "  1. Pixabay ('pixabay_api_keys' in config.toml or WebUI Settings)\n"
                    "  2. Pexels ('pexels_api_keys' in config.toml or WebUI Settings - Note: new API key issuance paused)\n"
                    "  3. Coverr ('coverr_api_keys' in config.toml or WebUI Settings, material.py:649)\n"
                    "Please set at least one key, or use '--provider local' (visual library add) or '--provider fake'."
                )

    def _search_pixabay_enhanced(
        self,
        query: str,
        min_duration: int,
    ) -> List[Dict[str, Any]]:
        """Query Pixabay API directly to extract tags and all renditions (S1, S2)."""
        api_keys = config.app.get("pixabay_api_keys")
        api_key = api_keys[0] if isinstance(api_keys, list) and api_keys else api_keys
        if not api_key:
            return []

        params = {
            "q": query,
            "video_type": "all",
            "per_page": 50,
            "key": api_key,
        }
        query_url = f"https://pixabay.com/api/videos/?{urllib.parse.urlencode(params)}"
        try:
            r = requests.get(query_url, proxies=config.proxy, timeout=30)
            if r.status_code != 200:
                logger.warning(f"Pixabay direct query returned status {r.status_code}")
                return []
            data = r.json()
            hits = data.get("hits", [])
            raw_candidates = []

            for v in hits:
                dur = v.get("duration", 0)
                if dur < min_duration:
                    continue

                tags_str = v.get("tags") or ""
                tags = [t.strip().lower() for t in tags_str.split(",") if t.strip()]

                videos_dict = v.get("videos") or {}
                # S2: Filter renditions with width >= 1920 (prefer smallest >= 1920, e.g. 1080p over 4K)
                rendition_matches = []
                all_renditions = []
                for r_name, r_info in videos_dict.items():
                    if not isinstance(r_info, dict):
                        continue
                    w = int(r_info.get("width") or 0)
                    h = int(r_info.get("height") or 0)
                    u = r_info.get("url")
                    sz = int(r_info.get("size") or 0)
                    if not u or w <= 0 or h <= 0:
                        continue
                    # Check landscape aspect
                    if w > h:
                        all_renditions.append((w, h, sz, r_name, u))
                        if w >= 1920 and h >= 1080:
                            rendition_matches.append((w, h, sz, r_name, u))

                if rendition_matches:
                    # Pick smallest rendition with width >= 1920 (e.g. medium 1920x1080 rather than large 3840x2160)
                    rendition_matches.sort(key=lambda x: (x[0], x[2]))
                    chosen_w, chosen_h, chosen_sz, chosen_key, chosen_url = rendition_matches[0]
                elif all_renditions:
                    # Pick largest available if < 1920
                    all_renditions.sort(key=lambda x: x[0], reverse=True)
                    chosen_w, chosen_h, chosen_sz, chosen_key, chosen_url = all_renditions[0]
                else:
                    continue

                raw_candidates.append({
                    "asset_id": str(v.get("id")),
                    "url": chosen_url,
                    "tags": tags,
                    "author": v.get("user"),
                    "author_url": f"https://pixabay.com/users/{v.get('user')}-{v.get('user_id')}/" if v.get("user") else None,
                    "source_url": v.get("pageURL"),
                    "width": chosen_w,
                    "height": chosen_h,
                    "duration": float(dur),
                    "rendition_name": f"{chosen_key} ({chosen_w}x{chosen_h})",
                })

            return raw_candidates
        except Exception as exc:
            logger.warning(f"Pixabay direct search error: {exc}; falling back to standard upstream")
            return []

    def search_candidates(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[Set[str]] = None,
        required_subject_terms: Optional[List[str]] = None,
        blocked_terms: Optional[List[str]] = None,
        visual_keywords: Optional[List[str]] = None,
    ) -> List[VisualCandidate]:
        """Search stock video candidates in 16:9 landscape using priority order: Pixabay -> Pexels -> Coverr.

        Applies relevance scoring (S1), term filtering (S1), and smallest >= 1920 rendition selection (S2).
        """
        self.validate_credentials()
        excluded = excluded_ids or set()
        candidates: List[VisualCandidate] = []
        min_sec = max(int(target_duration), 2)

        has_pixabay = _has_non_empty_key("pixabay_api_keys")
        has_pexels = _has_non_empty_key("pexels_api_keys")
        has_coverr = _has_non_empty_key("coverr_api_keys")

        # 1. Pixabay search
        if (self.provider_name == "pixabay" or (self.provider_name == "stock" and has_pixabay)) and len(candidates) < limit:
            try:
                # Try enhanced direct search first for full tags and smallest >=1920 rendition
                raw_items = self._search_pixabay_enhanced(query, min_sec)
                if raw_items:
                    for raw in raw_items:
                        asset_id = raw["asset_id"]
                        cand_id = f"pixabay_{asset_id}"
                        if cand_id in excluded or any(c.candidate_id == cand_id for c in candidates):
                            continue

                        tags = raw["tags"]
                        # Score candidate relevance
                        is_valid, score, reason = score_candidate_relevance(
                            candidate_tags=tags,
                            search_query=query,
                            visual_keywords=visual_keywords,
                            required_subject_terms=required_subject_terms,
                            blocked_terms=blocked_terms,
                        )
                        if not is_valid:
                            logger.debug(f"Candidate {cand_id} rejected: {reason}")
                            continue

                        candidates.append(
                            VisualCandidate(
                                candidate_id=cand_id,
                                provider="pixabay",
                                source_type="stock_video",
                                url=raw["url"],
                                local_path=None,
                                author=raw["author"],
                                author_url=raw["author_url"],
                                source_url=raw["source_url"],
                                license="Pixabay Content License",
                                width=raw["width"],
                                height=raw["height"],
                                duration_seconds=raw["duration"],
                                source_info={"query": query},
                                tags=tags,
                                relevance_score=score,
                                rendition_name=raw["rendition_name"],
                            )
                        )
                else:
                    # Upstream fallback: app/services/material.py:507
                    results = search_videos_pixabay(
                        search_term=query,
                        minimum_duration=min_sec,
                        video_aspect=VideoAspect.landscape,
                    )
                    for item in results:
                        s_info = getattr(item, "source_info", {}) or {}
                        asset_id = s_info.get("asset_id") or str(abs(hash(item.url)))
                        cand_id = f"pixabay_{asset_id}"
                        if cand_id in excluded or any(c.candidate_id == cand_id for c in candidates):
                            continue

                        creator = s_info.get("creator") or {}
                        rendition = s_info.get("rendition") or {}
                        rend_id = rendition.get("id", "standard")
                        rend_w = int(rendition.get("width") or 1920)
                        rend_h = int(rendition.get("height") or 1080)

                        # Extract tags from query words if upstream didn't preserve
                        tags = extract_query_keywords(query)
                        is_valid, score, _ = score_candidate_relevance(
                            candidate_tags=tags,
                            search_query=query,
                            visual_keywords=visual_keywords,
                            required_subject_terms=required_subject_terms,
                            blocked_terms=blocked_terms,
                        )
                        if not is_valid:
                            continue

                        candidates.append(
                            VisualCandidate(
                                candidate_id=cand_id,
                                provider="pixabay",
                                source_type="stock_video",
                                url=item.url,
                                local_path=None,
                                author=creator.get("name"),
                                author_url=creator.get("url"),
                                source_url=s_info.get("source_page"),
                                license="Pixabay Content License",
                                width=rend_w,
                                height=rend_h,
                                duration_seconds=float(getattr(item, "duration", target_duration) or target_duration),
                                source_info=s_info,
                                tags=tags,
                                relevance_score=score,
                                rendition_name=f"{rend_id} ({rend_w}x{rend_h})",
                            )
                        )
            except Exception as exc:
                logger.error(f"Pixabay stock video search error for query '{query}': {exc}")
                if isinstance(exc, VisualConfigurationError):
                    raise

        # 2. Pexels search
        if (self.provider_name == "pexels" or (self.provider_name == "stock" and has_pexels)) and len(candidates) < limit:
            try:
                results = search_videos_pexels(
                    search_term=query,
                    minimum_duration=min_sec,
                    video_aspect=VideoAspect.landscape,
                )
                for item in results:
                    s_info = getattr(item, "source_info", {}) or {}
                    asset_id = s_info.get("asset_id") or str(abs(hash(item.url)))
                    cand_id = f"pexels_{asset_id}"
                    if cand_id in excluded or any(c.candidate_id == cand_id for c in candidates):
                        continue

                    creator = s_info.get("creator") or {}
                    rendition = s_info.get("rendition") or {}
                    rend_w = int(rendition.get("width") or 1920)
                    rend_h = int(rendition.get("height") or 1080)

                    tags = extract_query_keywords(query)
                    is_valid, score, _ = score_candidate_relevance(
                        candidate_tags=tags,
                        search_query=query,
                        visual_keywords=visual_keywords,
                        required_subject_terms=required_subject_terms,
                        blocked_terms=blocked_terms,
                    )
                    if not is_valid:
                        continue

                    candidates.append(
                        VisualCandidate(
                            candidate_id=cand_id,
                            provider="pexels",
                            source_type="stock_video",
                            url=item.url,
                            local_path=None,
                            author=creator.get("name"),
                            author_url=creator.get("url"),
                            source_url=s_info.get("source_page"),
                            license="Pexels Commercial Free License",
                            width=rend_w,
                            height=rend_h,
                            duration_seconds=float(getattr(item, "duration", target_duration) or target_duration),
                            source_info=s_info,
                            tags=tags,
                            relevance_score=score,
                            rendition_name=f"pexels ({rend_w}x{rend_h})",
                        )
                    )
            except Exception as exc:
                logger.error(f"Pexels stock video search error for query '{query}': {exc}")
                if isinstance(exc, VisualConfigurationError):
                    raise

        # 3. Coverr search
        if (self.provider_name == "coverr" or (self.provider_name == "stock" and has_coverr)) and len(candidates) < limit:
            try:
                results = search_videos_coverr(
                    search_term=query,
                    minimum_duration=min_sec,
                    video_aspect=VideoAspect.landscape,
                )
                for item in results:
                    s_info = getattr(item, "source_info", {}) or {}
                    asset_id = s_info.get("asset_id") or str(abs(hash(item.url)))
                    cand_id = f"coverr_{asset_id}"
                    if cand_id in excluded or any(c.candidate_id == cand_id for c in candidates):
                        continue

                    creator = s_info.get("creator") or {}
                    rendition = s_info.get("rendition") or {}
                    rend_w = int(rendition.get("width") or 1920)
                    rend_h = int(rendition.get("height") or 1080)

                    tags = extract_query_keywords(query)
                    is_valid, score, _ = score_candidate_relevance(
                        candidate_tags=tags,
                        search_query=query,
                        visual_keywords=visual_keywords,
                        required_subject_terms=required_subject_terms,
                        blocked_terms=blocked_terms,
                    )
                    if not is_valid:
                        continue

                    candidates.append(
                        VisualCandidate(
                            candidate_id=cand_id,
                            provider="coverr",
                            source_type="stock_video",
                            url=item.url,
                            local_path=None,
                            author=creator.get("name"),
                            author_url=creator.get("url"),
                            source_url=s_info.get("source_page"),
                            license="Coverr License",
                            width=rend_w,
                            height=rend_h,
                            duration_seconds=float(getattr(item, "duration", target_duration) or target_duration),
                            source_info=s_info,
                            tags=tags,
                            relevance_score=score,
                            rendition_name=f"coverr ({rend_w}x{rend_h})",
                        )
                    )
            except Exception as exc:
                logger.error(f"Coverr stock video search error for query '{query}': {exc}")
                if isinstance(exc, VisualConfigurationError):
                    raise

        # S1: Sort candidates by relevance score descending so highest match is chosen first
        candidates.sort(key=lambda c: c.relevance_score, reverse=True)
        return candidates[:limit]

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        """Download remote stock video using GlobalMediaCache, record credit, compute hash."""
        if not candidate.url:
            raise VisualError(f"Candidate {candidate.candidate_id} has no remote download URL")

        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Download via global cache
        self.cache.download_or_get(candidate.url, dest)

        # Compute content hash
        content_hash = compute_file_content_hash(dest)

        # Record credit
        if self.credits_manager:
            self.credits_manager.record_asset_credit(candidate)

        return VisualAsset(
            asset_id=candidate.candidate_id,
            provider=candidate.provider,
            source_type=candidate.source_type,
            local_path=dest,
            content_hash=content_hash,
            author=candidate.author,
            author_url=candidate.author_url,
            source_url=candidate.source_url,
            license=candidate.license,
            width=candidate.width,
            height=candidate.height,
            duration_seconds=candidate.duration_seconds,
        )
