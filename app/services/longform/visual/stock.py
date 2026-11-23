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
import threading
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
    respect_provider_rate_limit,
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

    if not chosen_subject and req:
        for common in ["car", "vehicle", "automobile", "truck", "motorcycle"]:
            if re.search(r"\b" + common + r"\b", clean_q):
                chosen_subject = common
                break

    if not chosen_subject:
        chosen_subject = req[0] if req else ""

    # 2. Extract remaining concrete nouns/keywords
    words = re.findall(r"[a-z0-9]+", clean_q)
    subj_tokens = set(chosen_subject.split()) if chosen_subject else set()
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
    if chosen_subject:
        final_parts = [chosen_subject] + combined[:2]
    else:
        final_parts = combined[:3] or words[:3]
    return " ".join(final_parts)


def _extract_fallback_tags(query: str, source_info: Optional[Dict[str, Any]] = None) -> List[str]:
    """Build candidate tags from API metadata, source URL slug, and query keywords."""
    s_info = source_info or {}
    tags: List[str] = []
    raw_tags = s_info.get("tags")
    if isinstance(raw_tags, list):
        tags.extend(str(t).strip().lower() for t in raw_tags if str(t or "").strip())
    elif isinstance(raw_tags, str) and raw_tags.strip():
        tags.extend(t.strip().lower() for t in raw_tags.split(",") if t.strip())

    # Extract meaningful slug tokens from Pexels/Pixabay/Coverr page URL when tags weren't preserved
    src_page = str(s_info.get("source_page") or s_info.get("source_url") or "")
    if src_page:
        slug_part = urllib.parse.urlparse(src_page).path.strip("/").split("/")[-1]
        for tok in re.findall(r"[A-Za-z]+", slug_part):
            low = tok.lower()
            if len(low) > 2 and low not in STOP_WORDS and low not in ("video", "photo", "watch", "videos") and low not in tags:
                tags.append(low)

    for qk in extract_query_keywords(query):
        if qk not in tags:
            tags.append(qk)
    return tags


_DIGITAL_OFFICE_TRAP_TAGS = {
    "keyboard",
    "typing",
    "laptop",
    "computer",
    "pc",
    "desktop",
    "mouse",
    "smartphone",
    "gaming",
    "coding",
    "tablet",
    "monitor",
    "notebook",
}

_TITLE_BASED_PROVIDERS = {"wikimedia", "openverse", "nasa"}

_GENERIC_ROLE_OR_SCENE_WORDS = {
    "apprentice",
    "man",
    "men",
    "woman",
    "women",
    "boy",
    "boys",
    "girl",
    "girls",
    "person",
    "people",
    "child",
    "children",
    "mother",
    "father",
    "parent",
    "parents",
    "friend",
    "friends",
    "stranger",
    "strangers",
    "worker",
    "workers",
    "village",
    "villages",
    "town",
    "city",
    "cities",
    "street",
    "streets",
    "road",
    "roads",
    "way",
    "ways",
    "home",
    "house",
    "room",
    "door",
    "place",
    "places",
    "area",
    "areas",
    "part",
    "side",
    "end",
    "young",
    "old",
    "elderly",
    "aged",
    "eighteen",
    "night",
    "day",
    "morning",
    "evening",
    "afternoon",
    "winter",
    "time",
    "life",
    "work",
    "story",
    "face",
    "eyes",
    "hand",
    "hands",
}

_NON_VISUAL_ARCHIVE_TRAP_TAGS = {
    "navy",
    "seaman",
    "boatswain",
    "amphibious",
    "cryptologic",
    "dvids",
    "marines",
    "usmc",
    "usaf",
    "usn",
    "battalion",
    "regiment",
    "corps",
    "destroyer",
    "frigate",
    "petty officer",
    "master-at-arms",
    "landsat",
    "usgs",
    "topographic",
    "spectrogram",
}

_GEO_SUFFIX_TERMS = {
    "province",
    "prefecture",
    "county",
    "state",
    "city",
    "town",
    "village",
    "river",
    "lake",
    "sea",
    "ocean",
    "bay",
    "area",
    "mount",
    "mountain",
    "mountains",
    "island",
    "valley",
    "forest",
    "street",
    "avenue",
    "road",
    "highway",
    "bridge",
    "park",
    "peninsula",
    "harbor",
    "district",
    "colony",
    "temple",
    "shrine",
    "castle",
    "palace",
    "tower",
    "station",
    "empire",
    "kingdom",
}

_COMMON_TITLECASE_NONPROPER = {
    "a", "an", "the", "in", "on", "at", "by", "to", "for", "of", "with", "from",
    "into", "over", "under", "after", "before", "one", "two", "three", "his", "her",
    "their", "she", "he", "they", "it", "if", "then", "when", "while", "there", "these",
    "those", "this", "that", "no", "now", "not", "winter", "summer", "autumn", "spring",
    "snow", "snowstorm", "rain", "wind", "ice", "water", "fire", "river", "ferry",
    "ferryman", "boat", "man", "men", "woman", "women", "people", "person", "village",
    "forest", "night", "day", "morning", "evening", "afternoon", "dark", "cold", "pale",
    "light", "door", "hut", "mother", "father", "old", "young", "apprentice", "breath",
    "smoke", "eyes", "storm", "chapter", "part", "tokyo", "japan", "california",
}

_PERSON_START_VERBS = {
    "was", "were", "is", "are", "lay", "fell", "felt", "slept", "sat", "stood",
    "walked", "ran", "looked", "turned", "spoke", "told", "watched", "knew",
    "heard", "saw", "cried", "whispered", "died", "lived", "stayed", "worked",
}

_GENERIC_NARRATION_NOUNS = {
    "workplace",
    "work",
    "working",
    "office",
    "business",
    "project",
    "people",
    "person",
    "thing",
    "things",
    "time",
    "day",
    "year",
    "years",
    "history",
    "historical",
    "story",
    "part",
    "side",
    "way",
    "place",
    "others",
} | _GENERIC_ROLE_OR_SCENE_WORDS

_NARRATION_VERB_NOISE = {
    "some", "died", "while", "caused", "beneath", "under", "extreme", "many",
    "most", "more", "very", "such", "much", "also", "then", "when", "where",
    "there", "their", "these", "those", "could", "would", "should", "might",
    "built", "made", "used", "took", "went", "came", "kept", "held", "began",
    "started", "became", "remained", "continued", "suffered", "later", "early",
}


def extract_narration_proper_nouns(text: Optional[str]) -> Set[str]:
    """Identify character/person proper nouns so they neither inflate nor penalize relevance scores (A1.1).

    Preserves geographic place names paired with spatial designators (e.g. 'Musashi Province',
    'Bay Area') while excluding capitalized character names (e.g. 'Minokichi', 'Mosaku').
    """
    raw = str(text or "").strip()
    if not raw:
        return set()
    proper: Set[str] = set()
    sentences = re.split(r"(?<=[.!?])\s+|\n+", raw)
    for sent in sentences:
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", sent)
        for idx, tok in enumerate(words):
            if len(tok) < 3 or not tok[0].isupper():
                continue
            low = tok.lower().strip("'-")
            if (
                len(low) < 3
                or low in _COMMON_TITLECASE_NONPROPER
                or low in _GEO_SUFFIX_TERMS
                or low in STOP_WORDS
                or low in GENERIC_WORDS
            ):
                continue
            prev_low = words[idx - 1].lower() if idx > 0 else ""
            next_low = words[idx + 1].lower() if idx + 1 < len(words) else ""
            if next_low in _GEO_SUFFIX_TERMS or prev_low in _GEO_SUFFIX_TERMS:
                continue
            if idx == 0:
                # Sentence-initial capitalized word is a person name when followed by a narrative verb
                if next_low in _PERSON_START_VERBS:
                    proper.add(low)
                continue
            # Mid-sentence capitalized unknown noun not attached to a geographic designator
            proper.add(low)
    return proper


def resolve_source_prior_weight(
    source_name: Optional[str],
    recommended_visual_sources: Optional[Dict[str, Any]],
) -> float:
    """Map preset recommended_visual_sources to a gentle calibrated score adjustment [-0.04, +0.05] (A1.1)."""
    if not source_name or not isinstance(recommended_visual_sources, dict) or not recommended_visual_sources:
        return 0.0
    src = str(source_name).lower().strip()
    clean_map: Dict[str, float] = {}
    for k, v in recommended_visual_sources.items():
        try:
            clean_map[str(k).lower().strip()] = max(0.0, float(v))
        except Exception:
            continue
    if not clean_map:
        return 0.0

    if src in clean_map:
        eff_w = clean_map[src]
    elif src in ("pixabay", "coverr", "pexels", "stock") and "stock" in clean_map:
        eff_w = clean_map["stock"]
    elif src in ("ai_image", "openai") and "ai_image" in clean_map:
        eff_w = clean_map["ai_image"]
    elif src == "local" and "local" in clean_map:
        eff_w = clean_map["local"]
    elif src in _TITLE_BASED_PROVIDERS:
        base_stock = clean_map.get("stock", 0.5)
        eff_w = 0.45 * base_stock
    else:
        return 0.0

    adj = (eff_w - 0.45) * 0.16
    return round(max(-0.04, min(0.05, adj)), 3)


def extract_narration_nouns(
    narration_text: Optional[str],
    *,
    excluded_proper_nouns: Optional[Set[str]] = None,
) -> Tuple[List[str], List[str]]:
    """Extract (concrete_nouns, generic_nouns) from narration text for non-AI candidate ranking (D9.7, A1.1)."""
    prop_set = excluded_proper_nouns if excluded_proper_nouns is not None else extract_narration_proper_nouns(narration_text)
    raw_words = re.findall(r"[a-z]{3,}", (narration_text or "").lower())
    concrete: List[str] = []
    generic: List[str] = []
    seen: set[str] = set()
    for w in raw_words:
        if (
            w in STOP_WORDS
            or w in GENERIC_WORDS
            or w in ABSTRACT_WORDS
            or w in NUMBER_WORDS
            or w in _NARRATION_VERB_NOISE
            or w in prop_set
        ):
            continue
        forms = [w]
        if len(w) > 4 and w.endswith("es"):
            forms.append(w[:-2])
        elif len(w) > 3 and w.endswith("s"):
            forms.append(w[:-1])
        norm = forms[-1]
        if norm in seen or norm in prop_set:
            continue
        seen.add(norm)
        if w in _GENERIC_NARRATION_NOUNS or norm in _GENERIC_NARRATION_NOUNS:
            generic.append(norm)
        else:
            concrete.append(norm)
    return concrete, generic


def score_candidate_relevance_detailed(
    candidate_tags: List[str],
    search_query: str,
    visual_keywords: Optional[List[str]] = None,
    required_subject_terms: Optional[List[str]] = None,
    blocked_terms: Optional[List[str]] = None,
    negative_terms: Optional[List[str]] = None,
    anchor_terms: Optional[List[str]] = None,
    avoid_terms: Optional[List[str]] = None,
    min_relevance_score: Optional[float] = None,
    is_low_res: bool = False,
    narration_text: Optional[str] = None,
    strict_ai_relevance: bool = False,
    source_name: Optional[str] = None,
    is_title_based: Optional[bool] = None,
    proper_nouns: Optional[Set[str]] = None,
    recommended_visual_sources: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Score candidate relevance and return full component breakdown for A1.1 transparency."""
    req_terms = [t.lower().strip() for t in (required_subject_terms or []) if str(t or "").strip()]
    block_terms = [t.lower().strip() for t in (blocked_terms or []) if str(t or "").strip()]
    neg_terms = [t.lower().strip() for t in (negative_terms or []) if str(t or "").strip()]
    anchor_list = [t.lower().strip() for t in (anchor_terms or []) if str(t or "").strip()]
    avoid_list = [t.lower().strip() for t in (avoid_terms or []) if str(t or "").strip()]
    cand_tags = [t.lower().strip() for t in (candidate_tags or []) if str(t or "").strip()]
    min_thresh = min_relevance_score if min_relevance_score is not None else 0.35
    src_norm = str(source_name or "").lower().strip()
    title_based = bool(is_title_based) if is_title_based is not None else (src_norm in _TITLE_BASED_PROVIDERS)

    def _reject(sc: float, reason_txt: str, comps: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "is_valid": False,
            "score": round(float(sc), 3),
            "reason": reason_txt,
            "components": comps or {},
        }

    # 1. Blocked terms & project avoid_terms hard check (whole-word boundary, S5 / F12)
    for bt in block_terms:
        if term_matches_tags(bt, cand_tags):
            return _reject(0.0, f"Blocked term matched: {bt}")
    for av in avoid_list:
        if term_matches_tags(av, cand_tags):
            return _reject(0.0, f"Avoid term matched: {av}")

    # 2. Required subject terms strict check (T3)
    if req_terms:
        first_5_tags = cand_tags[:5]
        subj_in_first_5 = any(term_matches_tags(req, first_5_tags) for req in req_terms)
        matched_distinct_subjects = {req for req in req_terms if term_matches_tags(req, cand_tags)}
        has_sufficient_subjects = subj_in_first_5 or (len(matched_distinct_subjects) >= 2)
        if not has_sufficient_subjects:
            return _reject(
                0.0,
                f"Candidate lacks required subject terms: subject not in first 5 tags and fewer than 2 distinct subject terms ({len(matched_distinct_subjects)} found)",
            )

    # 2b. Project anchor_terms check (F12 / F13)
    anchor_matched = True
    distinct_anchor_hits = 0
    matched_anchors: List[str] = []
    if anchor_list:
        top_anchor_hit = any(term_matches_tags(anc, cand_tags[:8]) for anc in anchor_list)
        matched_anchors = [anc for anc in anchor_list if term_matches_tags(anc, cand_tags)]
        distinct_anchor_hits = len(matched_anchors)
        anchor_matched = top_anchor_hit or (distinct_anchor_hits >= 2)

    # 3. Subject score (calibrated so single broad anchor on noisy title-based captions cannot max out)
    if req_terms:
        subject_score = 1.0
    elif anchor_list and anchor_matched:
        if title_based:
            subject_score = 0.80 if distinct_anchor_hits >= 2 else 0.58
        else:
            subject_score = 1.0
    else:
        subject_score = 0.5

    # 4. Negative terms present in candidate tags (T3)
    matched_neg_terms = [nt for nt in neg_terms if term_matches_tags(nt, cand_tags)]

    # Extract proper nouns so character names neither inflate nor penalize scores (A1.1b)
    detected_proper = (
        extract_narration_proper_nouns(narration_text)
        | extract_narration_proper_nouns(search_query)
        | {str(p or "").lower().strip() for p in (proper_nouns or set()) if str(p or "").strip()}
    )

    # 5. Query keywords overlap (ignoring stopwords, generic words, negative collisions & proper nouns)
    q_words_raw = extract_query_keywords(search_query)
    q_words = [qw for qw in q_words_raw if qw not in detected_proper]
    effective_q_words: List[str] = []
    for qw in q_words:
        if matched_neg_terms and (qw in neg_terms or any(term_matches_tags(qw, [nt]) for nt in neg_terms)):
            continue
        effective_q_words.append(qw)

    q_matched_words: List[str] = []
    if effective_q_words:
        q_matched_words = [qw for qw in effective_q_words if term_matches_tags(qw, cand_tags)]
        q_ratio = len(q_matched_words) / len(effective_q_words)
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

    subj_comp = 0.50 * subject_score
    q_comp = 0.35 * q_ratio
    vk_comp = 0.15 * vk_ratio
    score = subj_comp + q_comp + vk_comp

    # Penalties accumulator
    penalties = 0.0
    if matched_neg_terms:
        neg_pen = min(len(matched_neg_terms) * 0.15, 0.45)
        score = max(0.0, score - neg_pen)
        penalties -= neg_pen

    if is_low_res:
        before_s = score
        score = max(0.05, score - 0.20)
        penalties += (score - before_s)

    # Narration nouns & trap checks (D9.7 + A1.1)
    conc_narration_nouns, gen_narration_nouns = extract_narration_nouns(
        narration_text, excluded_proper_nouns=detected_proper
    )
    asked_tokens = set(re.findall(r"[a-z]+", f"{search_query or ''} {narration_text or ''}".lower()))
    conc_hits = [n for n in conc_narration_nouns if term_matches_tags(n, cand_tags)]
    gen_hits = [n for n in gen_narration_nouns if term_matches_tags(n, cand_tags)]

    narration_bonus = 0.0
    if conc_narration_nouns or gen_narration_nouns:
        head_tags = " ".join(cand_tags[:8])
        trap_hits = [
            tp for tp in _DIGITAL_OFFICE_TRAP_TAGS
            if tp not in asked_tokens and re.search(rf"\b{re.escape(tp)}\b", head_tags)
        ]
        if trap_hits and not conc_hits:
            penalties -= 0.20
            score = max(0.0, score - 0.20)
        elif conc_hits:
            narration_bonus = min(len(conc_hits) * 0.07, 0.21)
            score = min(1.0, score + narration_bonus)
        elif gen_hits:
            narration_bonus = min(len(gen_hits) * 0.02, 0.04)
            score = min(1.0, score + narration_bonus)

    # A1.1a: Title/description sources must NOT score high on a single generic/role word
    concrete_q_matches = {w for w in q_matched_words if w not in _GENERIC_ROLE_OR_SCENE_WORDS}
    concrete_narr_hits = {n for n in conc_hits if n not in _GENERIC_ROLE_OR_SCENE_WORDS}
    all_concrete_hits = concrete_q_matches | concrete_narr_hits
    title_cap_applied: Optional[str] = None
    if title_based:
        has_two_concrete = len(all_concrete_hits) >= 2
        has_narr_plus_anchor = (len(concrete_narr_hits) >= 1 and bool(anchor_list and anchor_matched))
        if not (has_two_concrete or has_narr_plus_anchor):
            if len(all_concrete_hits) == 0:
                score = min(score, 0.30)
                title_cap_applied = "single_generic_only_cap_0.30"
            else:
                score = min(score, 0.44)
                title_cap_applied = "insufficient_concrete_title_cap_0.44"

    # Non-visual military / satellite archive trap check on title-based providers (applied after cap so penalties are never swallowed)
    archive_trap_hits: List[str] = []
    if title_based:
        archive_trap_hits = [
            tp for tp in _NON_VISUAL_ARCHIVE_TRAP_TAGS
            if tp not in asked_tokens and term_matches_tags(tp, cand_tags)
        ]
        if archive_trap_hits:
            arch_pen = 0.25 if any(tp in ("navy", "seaman", "boatswain", "amphibious", "cryptologic", "dvids", "marines", "usmc") for tp in archive_trap_hits) else 0.18
            score = max(0.0, score - arch_pen)
            penalties -= arch_pen

    # A1.1c: Preset recommended_visual_sources prior calibration (never zeroes or disables)
    source_prior = resolve_source_prior_weight(src_norm, recommended_visual_sources)
    if source_prior != 0.0:
        score = max(0.0, min(1.0, score + source_prior))

    score = round(score, 3)

    components = {
        "source_name": src_norm or "default",
        "title_based": title_based,
        "subject": round(subj_comp, 3),
        "anchor": round(subj_comp if (not req_terms and anchor_list and anchor_matched) else 0.0, 3),
        "matched_anchors": matched_anchors,
        "query_overlap": round(q_comp, 3),
        "query_matches": q_matched_words,
        "concrete_matches": sorted(all_concrete_hits),
        "visual_keyword": round(vk_comp, 3),
        "narration_bonus": round(narration_bonus, 3),
        "penalties": round(penalties, 3),
        "source_prior": source_prior,
        "proper_nouns_ignored": sorted(detected_proper),
        "archive_trap_hits": archive_trap_hits,
        "title_cap_applied": title_cap_applied,
    }

    # F12: Anchor penalty & rejection when zero anchor terms match candidate tags
    if anchor_list and not anchor_matched:
        score = round(min(score * 0.35, max(0.0, min_thresh - 0.10)), 3)
        return _reject(score, "No anchor_terms matched candidate tags", components)

    # T3 + D9.7: Minimum threshold enforcement
    effective_min = max(float(min_thresh), 0.42) if strict_ai_relevance else float(min_thresh)
    if score < effective_min:
        return _reject(score, f"Score {score:.3f} below minimum threshold {effective_min:.2f}", components)

    return {
        "is_valid": True,
        "score": score,
        "reason": "Accepted",
        "components": components,
    }


def score_candidate_relevance(
    candidate_tags: List[str],
    search_query: str,
    visual_keywords: Optional[List[str]] = None,
    required_subject_terms: Optional[List[str]] = None,
    blocked_terms: Optional[List[str]] = None,
    negative_terms: Optional[List[str]] = None,
    anchor_terms: Optional[List[str]] = None,
    avoid_terms: Optional[List[str]] = None,
    min_relevance_score: Optional[float] = None,
    is_low_res: bool = False,
    narration_text: Optional[str] = None,
    strict_ai_relevance: bool = False,
    source_name: Optional[str] = None,
    is_title_based: Optional[bool] = None,
    proper_nouns: Optional[Set[str]] = None,
    recommended_visual_sources: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, float, str]:
    """Score candidate relevance against search query, preset rules, and project anchor/avoid terms (S1, S5, T3, F12, A1.1)."""
    res = score_candidate_relevance_detailed(
        candidate_tags=candidate_tags,
        search_query=search_query,
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
        source_name=source_name,
        is_title_based=is_title_based,
        proper_nouns=proper_nouns,
        recommended_visual_sources=recommended_visual_sources,
    )
    return bool(res["is_valid"]), float(res["score"]), str(res["reason"])


def extract_narrower_query(
    search_query: str,
    required_subject_terms: Optional[List[str]] = None,
) -> str:
    """Extract a narrower query (subject term + 1-2 key nouns) for retry escalation (S1, S5)."""
    return restructure_stock_query(search_query, required_subject_terms)


def build_short_core_queries(
    search_query: str,
    anchor_terms: Optional[List[str]] = None,
) -> List[str]:
    """Shorten a 4-6 word query into 2-3 core visual words anchored to project topic (F13).

    Example:
    - 'night street food stall crowd' -> ['street food night', 'food stall crowd', 'street food']
    - 'tent food stall plastic table' -> ['food stall table', 'food stall']
    """
    clean_q = re.sub(r"[^a-z0-9\s]+", " ", (search_query or "").lower()).strip()
    anchors = [a.lower().strip() for a in (anchor_terms or []) if str(a or "").strip()]
    words = [w for w in clean_q.split() if len(w) > 2 and w not in STOP_WORDS]
    anchor_tokens = {tok for anc in anchors for tok in anc.split()}

    anchor_words = [w for w in words if w in anchor_tokens]
    other_words = [w for w in words if w not in anchor_tokens and w not in GENERIC_WORDS]
    generic_tail = [w for w in words if w in GENERIC_WORDS and w not in anchor_tokens]

    ordered = anchor_words + other_words + generic_tail
    out: List[str] = []
    seen: set[str] = set()

    def _add(parts: List[str]) -> None:
        q_str = " ".join(parts).strip()
        if q_str and q_str not in seen:
            seen.add(q_str)
            out.append(q_str)

    # 3-word core combination preserving matching multi-word anchor phrase if present
    matched_phrase = next((anc for anc in anchors if " " in anc and anc in clean_q), None)
    if matched_phrase:
        rest = [w for w in ordered if w not in matched_phrase.split()]
        if rest:
            _add([matched_phrase, rest[0]])
        _add([matched_phrase])

    if len(ordered) >= 3:
        _add(ordered[:3])
    if len(ordered) >= 2:
        _add(ordered[:2])
    # Fallback combining top anchor + primary non-anchor word
    if anchors and other_words:
        _add([anchors[0], other_words[0]])

    return [q for q in out if q.lower() != clean_q]


def build_generalized_visual_queries(
    query: str,
    anchor_terms: Optional[List[str]] = None,
    topic_keywords: Optional[List[str]] = None,
    visual_keywords: Optional[List[str]] = None,
) -> List[str]:
    """Build broader Tier-2/Tier-3 noun & visual concept queries when concrete query hits 0 (D9.1)."""
    clean_q = re.sub(r"[^\w\s-]", " ", (query or "").lower()).strip()
    tokens = [t for t in clean_q.split() if t and len(t) >= 3 and t not in STOP_WORDS]

    non_generic = [t for t in tokens if t not in GENERIC_WORDS and t not in ABSTRACT_WORDS]
    nouns = [t for t in non_generic if t in PRIORITY_NOUNS] + [t for t in non_generic if t not in PRIORITY_NOUNS]

    out: List[str] = []
    seen: set[str] = {clean_q}

    def _add(val: str) -> None:
        v = re.sub(r"\s+", " ", str(val or "").strip().lower())
        if v and v not in seen and len(v) >= 3:
            seen.add(v)
            out.append(v)

    # Pairs of core nouns, then single strongest nouns
    if len(nouns) >= 2:
        _add(f"{nouns[0]} {nouns[1]}")
        _add(f"{nouns[-2]} {nouns[-1]}")
    for n in nouns[:4]:
        _add(n)

    # Anchor + primary noun combinations, then standalone anchors/visual_keywords/topic_keywords
    anchors = [str(a).strip().lower() for a in (anchor_terms or []) if str(a or "").strip()]
    if anchors and nouns:
        for anc in anchors[:2]:
            if nouns[0] not in anc:
                _add(f"{anc} {nouns[0]}")
    for anc in anchors[:4]:
        _add(anc)
    for vk in (visual_keywords or [])[:3]:
        _add(str(vk))
    for tk in (topic_keywords or [])[:3]:
        _add(str(tk))

    return out


def filter_candidates_by_anchors_and_avoids(
    candidates: List[Dict[str, Any]],
    *,
    anchor_terms: Optional[List[str]] = None,
    avoid_terms: Optional[List[str]] = None,
    blocked_terms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Filter candidate dicts (e.g. tab Kandidat) rejecting any avoid_terms or 0-anchor-match entries (F13)."""
    avoids = [str(x).lower().strip() for x in (avoid_terms or []) if str(x or "").strip()]
    blocks = [str(x).lower().strip() for x in (blocked_terms or []) if str(x or "").strip()]
    anchors = [str(x).lower().strip() for x in (anchor_terms or []) if str(x or "").strip()]
    if not avoids and not blocks and not anchors:
        return list(candidates)

    kept: List[Dict[str, Any]] = []
    for cand in candidates:
        c_tags = [str(x).lower().strip() for x in (cand.get("tags") or []) if str(x or "").strip()]
        if any(term_matches_tags(bt, c_tags) for bt in blocks):
            continue
        if any(term_matches_tags(av, c_tags) for av in avoids):
            continue
        if anchors and c_tags:
            top_hit = any(term_matches_tags(anc, c_tags[:8]) for anc in anchors)
            multi_hit = sum(1 for anc in anchors if term_matches_tags(anc, c_tags)) >= 2
            if not (top_hit or multi_hit):
                continue
        kept.append(cand)
    return kept


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
        self._diag_local = threading.local()
        self._last_raw_count: int = 0
        self._last_rejected_reasons: List[str] = []

    def search_with_diagnostics(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        **kwargs: Any,
    ) -> Tuple[List[VisualCandidate], int, List[str]]:
        """Run search_candidates and return (accepted_candidates, raw_hit_count, rejected_reasons) (D9.1)."""
        self._diag_local.raw_count = 0
        self._diag_local.rejected_reasons = []
        self._last_raw_count = 0
        self._last_rejected_reasons = []
        cands = self.search_candidates(query=query, target_duration=target_duration, limit=limit, **kwargs)
        raw_cnt = int(getattr(self._diag_local, "raw_count", self._last_raw_count))
        rej_list = list(getattr(self._diag_local, "rejected_reasons", self._last_rejected_reasons))
        return cands, raw_cnt, rej_list

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
            respect_provider_rate_limit("pixabay", 0.25)
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
                        t_url = r_info.get("thumbnail") or ""
                        if w >= 1920 and is_near_16_9:
                            tier_a.append((w, h, sz, r_name, u, False, t_url))
                        elif w >= 1920 and not is_extreme:
                            tier_b.append((w, h, sz, r_name, u, False, t_url))
                        else:
                            tier_c.append((w, h, sz, r_name, u, True, t_url))

                if tier_a:
                    # Pick smallest rendition with width >= 1920 (e.g. medium 1920x1080 rather than large 3840x2160)
                    tier_a.sort(key=lambda x: (x[0], x[2]))
                    chosen_w, chosen_h, chosen_sz, chosen_key, chosen_url, is_low_res, chosen_thumb = tier_a[0]
                elif tier_b:
                    tier_b.sort(key=lambda x: (x[0], x[2]))
                    chosen_w, chosen_h, chosen_sz, chosen_key, chosen_url, is_low_res, chosen_thumb = tier_b[0]
                elif tier_c:
                    # Pick highest resolution among tier_c (< 1920 or extreme ratio)
                    tier_c.sort(key=lambda x: x[0], reverse=True)
                    chosen_w, chosen_h, chosen_sz, chosen_key, chosen_url, is_low_res, chosen_thumb = tier_c[0]
                else:
                    continue

                if not chosen_thumb:
                    for fb_k in ("small", "medium", "tiny", "large"):
                        fb_r = vids.get(fb_k)
                        if isinstance(fb_r, dict) and fb_r.get("thumbnail"):
                            chosen_thumb = fb_r["thumbnail"]
                            break

                raw_candidates.append({
                    "asset_id": str(v.get("id")),
                    "url": chosen_url,
                    "thumbnail_url": chosen_thumb or None,
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
        anchor_terms: Optional[List[str]] = None,
        avoid_terms: Optional[List[str]] = None,
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
        raw_total_count = 0
        rejected_reasons_log: List[str] = []
        min_sec = max(int(target_duration), 2)
        narration_txt = kwargs.get("narration_text")
        strict_ai = bool(kwargs.get("strict_ai_relevance", False))
        rec_sources = kwargs.get("recommended_visual_sources")

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
                    raw_total_count += len(raw_items)
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
                            anchor_terms=anchor_terms,
                            avoid_terms=avoid_terms,
                            min_relevance_score=min_relevance_score,
                            is_low_res=raw.get("is_low_res", False),
                            narration_text=narration_txt,
                            strict_ai_relevance=strict_ai,
                            source_name="pixabay",
                            recommended_visual_sources=rec_sources,
                        )
                        if not is_valid:
                            rejected_reasons_log.append(reason)
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
                                thumbnail_url=raw.get("thumbnail_url"),
                            )
                        )
                else:
                    # Upstream fallback: app/services/material.py:507
                    results = search_videos_pixabay(
                        search_term=query,
                        minimum_duration=min_sec,
                        video_aspect=VideoAspect.landscape,
                    )
                    raw_total_count += len(results or [])
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

                        # Extract tags from source metadata / query words if upstream didn't preserve
                        tags = _extract_fallback_tags(query, s_info)
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
                            narration_text=narration_txt,
                            strict_ai_relevance=strict_ai,
                            source_name="pixabay",
                            recommended_visual_sources=rec_sources,
                        )
                        if not is_valid:
                            rejected_reasons_log.append(reason)
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

                    tags = _extract_fallback_tags(query, s_info)
                    is_valid, score, _ = score_candidate_relevance(
                        candidate_tags=tags,
                        search_query=query,
                        visual_keywords=visual_keywords,
                        required_subject_terms=required_subject_terms,
                        blocked_terms=blocked_terms,
                        negative_terms=negative_terms,
                        anchor_terms=anchor_terms,
                        avoid_terms=avoid_terms,
                        min_relevance_score=min_relevance_score,
                        narration_text=narration_txt,
                        strict_ai_relevance=strict_ai,
                        source_name="pexels",
                        recommended_visual_sources=rec_sources,
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

                    tags = _extract_fallback_tags(query, s_info)
                    is_valid, score, _ = score_candidate_relevance(
                        candidate_tags=tags,
                        search_query=query,
                        visual_keywords=visual_keywords,
                        required_subject_terms=required_subject_terms,
                        blocked_terms=blocked_terms,
                        negative_terms=negative_terms,
                        anchor_terms=anchor_terms,
                        avoid_terms=avoid_terms,
                        min_relevance_score=min_relevance_score,
                        narration_text=narration_txt,
                        strict_ai_relevance=strict_ai,
                        source_name="coverr",
                        recommended_visual_sources=rec_sources,
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
        self._diag_local.raw_count = raw_total_count
        self._diag_local.rejected_reasons = rejected_reasons_log
        self._last_raw_count = raw_total_count
        self._last_rejected_reasons = rejected_reasons_log
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
