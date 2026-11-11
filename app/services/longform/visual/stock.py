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
    "it", "its", "then", "there", "their"
}

GENERIC_WORDS = {
    "black", "vintage", "smoke", "slow", "motion", "shot", "close", "up",
    "photo", "high", "speed", "classic", "modern", "video", "footage", "clip",
    "cinematic", "realism", "view", "4k", "hd", "background", "abstract",
    "nature", "retro", "white", "red", "blue", "scene", "color", "dark",
    "light", "action", "detail", "movement", "fast", "stream", "room", "table",
    "massive", "chrome", "smoothly", "rumbling", "rural", "sunset"
}

ABSTRACT_WORDS = {
    "aerodynamic", "billowing", "gliding", "breathtaking", "unleashing",
    "timeless", "dramatic", "legendary", "iconic", "mythical", "glory",
    "nostalgic", "atmosphere", "presence", "essence"
}

NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "dual", "single", "triple", "quad", "1960s", "1950s", "1970s", "1980s", "1990s"
}

PRIORITY_NOUNS = {
    "engine", "carburetor", "exhaust", "highway", "burnout", "dragstrip", "road",
    "track", "dashboard", "wheel", "hood", "chassis", "speedometer", "transmission",
    "race", "draftsmen", "engineers", "tunnel", "driver", "tires", "racing"
}


def normalize_tag(tag: str) -> str:
    """Normalize a tag string to its root singular form for automotive and common entities (T3).

    Conversions:
    - 'cars' -> 'car'
    - 'automobiles' -> 'automobile'
    - 'vehicles' -> 'vehicle'
    - 'muscle cars' -> 'muscle car'
    - 'hot rods' -> 'hot rod'
    - 'race cars' -> 'race car'
    - 'sports cars' -> 'sports car'
    - 'engines' -> 'engine'
    - 'carburetors' -> 'carburetor'
    - 'tires' -> 'tire'
    - 'wheels' -> 'wheel'
    - 'roads' -> 'road'
    - 'highways' -> 'highway'
    - 'drivers' -> 'driver'
    """
    t = tag.lower().strip()
    if not t:
        return ""
    replacements = {
        "cars": "car",
        "automobiles": "automobile",
        "vehicles": "vehicle",
        "muscle cars": "muscle car",
        "hot rods": "hot rod",
        "race cars": "race car",
        "sports cars": "sports car",
        "engines": "engine",
        "carburetors": "carburetor",
        "tires": "tire",
        "wheels": "wheel",
        "roads": "road",
        "highways": "highway",
        "drivers": "driver",
        "islands": "island",
        "mountains": "mountain",
        "flowers": "flower",
        "boats": "boat",
        "ships": "ship",
        "turbines": "wind turbine",
        "cities": "city",
    }
    if t in replacements:
        return replacements[t]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        singular = t[:-1]
        for stem in ["car", "automobile", "vehicle", "rod", "engine", "tire", "wheel", "road", "boat", "ship", "island"]:
            if singular.endswith(stem):
                return singular
    return t


_NON_AUTO_CAR_TERMS = {
    "cable car", "cable cars", "rail car", "rail cars", "railway car", "railway cars",
    "train car", "train cars", "dining car", "dining cars", "box car", "box cars",
    "streetcar", "streetcars", "tramcar", "tramcars", "handcar", "handcars",
}


def term_matches_tags(term: str, candidate_tags: List[str]) -> bool:
    """Check if term matches candidate tags using whole-word boundary and tag normalization (S5, T3, X4).

    'car' matches 'muscle car', 'car show', 'race car', 'car', 'cars'.
    'car' does NOT match 'carpet', 'red carpet', 'cartoon', or non-automotive transit terms like 'cable car'.
    """
    term_clean = normalize_tag(term.strip().lower())
    if not term_clean or not candidate_tags:
        return False
    pattern = r"\b" + re.escape(term_clean) + r"(?:s|es)?\b"
    for tag in candidate_tags:
        tag_clean = tag.strip().lower()
        tag_norm = normalize_tag(tag_clean)
        if term_clean == "car" and (tag_clean in _NON_AUTO_CAR_TERMS or tag_norm in _NON_AUTO_CAR_TERMS):
            continue
        if re.search(pattern, tag_clean) or re.search(pattern, tag_norm):
            return True
    return False


def extract_query_keywords(text: str) -> List[str]:
    """Tokenize query into lowercase keywords, removing stop and generic words (S1, S5)."""
    words = [w.lower().strip() for w in re.findall(r"[A-Za-z0-9_-]+", text or "")]
    return [
        w for w in words
        if len(w) > 2 and w not in STOP_WORDS and w not in GENERIC_WORDS and w not in ABSTRACT_WORDS and w not in NUMBER_WORDS
    ]


def restructure_stock_query(
    search_query: str,
    required_subject_terms: Optional[List[str]] = None,
    topic_keywords: Optional[List[str]] = None,
) -> str:
    """Restructure a stock search query into 2-3 words with subject in front (S5).

    e.g.:
    - 'classic muscle car rumbling highway' -> 'muscle car highway'
    - 'dual four barrel engine bay carburetor' -> 'car engine carburetor'
    - 'dragstrip burnout tire smoke slow motion' -> 'car dragstrip burnout'
    - 'wind tunnel aerodynamic smoke stream car' -> 'car tunnel wind'
    """
    clean_q = (search_query or "").lower().strip()
    req = [t.lower().strip() for t in (required_subject_terms or []) if t.strip()]

    # 1. Identify primary subject (multi-word first, e.g. 'muscle car')
    chosen_subject = None
    for st in sorted(req, key=len, reverse=True):
        if re.search(r"\b" + re.escape(st) + r"\b", clean_q):
            chosen_subject = st
            break

    if not chosen_subject:
        for common in ["car", "vehicle", "automobile", "truck", "motorcycle"]:
            if re.search(r"\b" + common + r"\b", clean_q):
                chosen_subject = common
                break

    if not chosen_subject:
        chosen_subject = req[0] if req else "car"

    # 2. Extract remaining concrete nouns/keywords
    words = re.findall(r"[a-z0-9]+", clean_q)
    subj_tokens = set(chosen_subject.split())
    p_nouns = []
    others = []
    for w in words:
        if w in subj_tokens or w in STOP_WORDS or w in GENERIC_WORDS or w in ABSTRACT_WORDS or w in NUMBER_WORDS or len(w) <= 2:
            continue
        if w in PRIORITY_NOUNS and w not in p_nouns:
            p_nouns.append(w)
        elif w not in others and w not in p_nouns:
            others.append(w)

    combined = p_nouns + others
    final_parts = [chosen_subject] + combined[:2]
    return " ".join(final_parts)


def score_candidate_relevance(
    candidate_tags: List[str],
    search_query: str,
    visual_keywords: Optional[List[str]] = None,
    required_subject_terms: Optional[List[str]] = None,
    blocked_terms: Optional[List[str]] = None,
    negative_terms: Optional[List[str]] = None,
    min_relevance_score: Optional[float] = None,
    is_low_res: bool = False,
) -> Tuple[bool, float, str]:
    """Score candidate relevance against search query and preset rules with strict filters (S1, S5, S6, T3).

    Strict filters (T3):
    1. Blocked terms (whole-word boundary): if matched, REJECT immediately.
    2. Required subject terms: candidate MUST have:
       - Subject in the first 5 tags (Pixabay orders by relevance), OR
       - At least 2 distinct required subject terms across all tags.
       If neither condition is met, REJECT immediately.
    3. Negative terms penalty & ambiguous query words:
       - Matched negative terms incur heavy penalty (-0.15 per term, up to -0.45).
       - Ambiguous query words matching negative terms (e.g. 'bay' from 'engine bay')
         are excluded from query overlap credit if negative terms are present in candidate tags.
    4. Minimum score threshold (default 0.35):
       - If score after all penalties is below min_relevance_score, return False.

    Returns:
        (is_valid, relevance_score, reason)
    """
    req_terms = [t.lower().strip() for t in (required_subject_terms or []) if t.strip()]
    block_terms = [t.lower().strip() for t in (blocked_terms or []) if t.strip()]
    neg_terms = [t.lower().strip() for t in (negative_terms or []) if t.strip()]
    cand_tags = [t.lower().strip() for t in (candidate_tags or []) if t.strip()]
    min_thresh = min_relevance_score if min_relevance_score is not None else 0.35

    # 1. Blocked terms hard check (whole-word boundary, S5)
    for bt in block_terms:
        if term_matches_tags(bt, cand_tags):
            return False, 0.0, f"Blocked term matched: {bt}"

    # 2. Required subject terms strict check (T3)
    if req_terms:
        first_5_tags = cand_tags[:5]
        subj_in_first_5 = any(term_matches_tags(req, first_5_tags) for req in req_terms)
        matched_distinct_subjects = {req for req in req_terms if term_matches_tags(req, cand_tags)}
        has_sufficient_subjects = subj_in_first_5 or (len(matched_distinct_subjects) >= 2)

        if not has_sufficient_subjects:
            return (
                False,
                0.0,
                f"Candidate lacks required subject terms: subject not in first 5 tags and fewer than 2 distinct subject terms ({len(matched_distinct_subjects)} found)",
            )

    # 3. Subject score (if passed, 1.0 if req_terms present else 0.5)
    subject_score = 1.0 if req_terms else 0.5

    # 4. Check negative terms present in candidate tags (T3)
    matched_neg_terms = [nt for nt in neg_terms if term_matches_tags(nt, cand_tags)]

    # 5. Query keywords overlap (ignoring stopwords and generic words, S5)
    # T3: If candidate contains negative terms, ambiguous query words matching negative terms
    # (e.g. 'bay' in 'engine bay') must not give query overlap credit.
    q_words = extract_query_keywords(search_query)
    effective_q_words = []
    for qw in q_words:
        if matched_neg_terms and (qw in neg_terms or any(term_matches_tags(qw, [nt]) for nt in neg_terms)):
            continue
        effective_q_words.append(qw)

    if effective_q_words:
        q_matches = sum(1 for qw in effective_q_words if term_matches_tags(qw, cand_tags))
        q_ratio = q_matches / len(effective_q_words)
    else:
        q_ratio = 0.5 if not matched_neg_terms else 0.0

    # 6. Preset visual keywords overlap
    vk_list = visual_keywords or []
    if vk_list:
        vk_matches = 0
        for vk in vk_list:
            vk_words = extract_query_keywords(vk)
            if any(term_matches_tags(vkw, cand_tags) for vkw in vk_words):
                vk_matches += 1
        vk_ratio = min(vk_matches / len(vk_list), 1.0)
    else:
        vk_ratio = 0.0

    score = 0.50 * subject_score + 0.35 * q_ratio + 0.15 * vk_ratio

    # T3: Negative terms heavy penalty (-0.15 per matched negative term, max -0.45)
    if matched_neg_terms:
        neg_penalty = min(len(matched_neg_terms) * 0.15, 0.45)
        score = max(0.0, score - neg_penalty)

    # S6: Low resolution / extreme aspect ratio penalty
    if is_low_res:
        score = max(0.05, score - 0.20)

    score = round(score, 3)

    # T3: Minimum threshold enforcement
    if score < min_thresh:
        return False, score, f"Score {score:.3f} below minimum threshold {min_thresh:.2f}"

    return True, score, "Accepted"


def extract_narrower_query(
    search_query: str,
    required_subject_terms: Optional[List[str]] = None,
) -> str:
    """Extract a narrower query (subject term + 1-2 key nouns) for retry escalation (S1, S5)."""
    return restructure_stock_query(search_query, required_subject_terms)


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
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query Pixabay API directly to extract tags and all renditions (S1, S2, S5, S6)."""
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
        if category:
            params["category"] = category

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
                tier_a = []  # >= 1920 and near 16:9
                tier_b = []  # >= 1920 other ratio
                tier_c = []  # < 1920 or extreme ratio

                for r_name, r_info in videos_dict.items():
                    if not isinstance(r_info, dict):
                        continue
                    w = int(r_info.get("width") or 0)
                    h = int(r_info.get("height") or 0)
                    u = r_info.get("url")
                    sz = int(r_info.get("size") or 0)
                    if not u or w <= 0 or h <= 0:
                        continue
                    if w > h:
                        ratio = w / h
                        is_near_16_9 = 1.60 <= ratio <= 1.95
                        is_extreme = ratio < 1.33 or ratio > 2.20
                        if w >= 1920 and is_near_16_9:
                            tier_a.append((w, h, sz, r_name, u, False))
                        elif w >= 1920 and not is_extreme:
                            tier_b.append((w, h, sz, r_name, u, False))
                        else:
                            tier_c.append((w, h, sz, r_name, u, True))

                if tier_a:
                    # Pick smallest rendition with width >= 1920 (e.g. medium 1920x1080 rather than large 3840x2160)
                    tier_a.sort(key=lambda x: (x[0], x[2]))
                    chosen_w, chosen_h, chosen_sz, chosen_key, chosen_url, is_low_res = tier_a[0]
                elif tier_b:
                    tier_b.sort(key=lambda x: (x[0], x[2]))
                    chosen_w, chosen_h, chosen_sz, chosen_key, chosen_url, is_low_res = tier_b[0]
                elif tier_c:
                    # Pick highest resolution among tier_c (< 1920 or extreme ratio)
                    tier_c.sort(key=lambda x: x[0], reverse=True)
                    chosen_w, chosen_h, chosen_sz, chosen_key, chosen_url, is_low_res = tier_c[0]
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
                    "is_low_res": is_low_res,
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
        negative_terms: Optional[List[str]] = None,
        min_relevance_score: Optional[float] = None,
        category: Optional[str] = None,
        **kwargs: Any,
    ) -> List[VisualCandidate]:
        """Search stock video candidates in 16:9 landscape using priority order: Pixabay -> Pexels -> Coverr.

        Applies relevance scoring (S1), hard term filtering (S5), category filtering (S5),
        and rendition optimization (S2, S6).
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
                # Try enhanced direct search with category first
                raw_items = self._search_pixabay_enhanced(query, min_sec, category=category)
                if not raw_items and category:
                    # Retry without category in case category was too narrow
                    raw_items = self._search_pixabay_enhanced(query, min_sec, category=None)

                if raw_items:
                    for raw in raw_items:
                        asset_id = raw["asset_id"]
                        cand_id = f"pixabay_{asset_id}"
                        if cand_id in excluded or any(c.candidate_id == cand_id for c in candidates):
                            continue

                        tags = raw["tags"]
                        # Score candidate relevance with hard filters
                        is_valid, score, reason = score_candidate_relevance(
                            candidate_tags=tags,
                            search_query=query,
                            visual_keywords=visual_keywords,
                            required_subject_terms=required_subject_terms,
                            blocked_terms=blocked_terms,
                            negative_terms=negative_terms,
                            min_relevance_score=min_relevance_score,
                            is_low_res=raw.get("is_low_res", False),
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
                                is_low_res=raw.get("is_low_res", False),
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
                            negative_terms=negative_terms,
                            min_relevance_score=min_relevance_score,
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
                        negative_terms=negative_terms,
                        min_relevance_score=min_relevance_score,
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
                        negative_terms=negative_terms,
                        min_relevance_score=min_relevance_score,
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
