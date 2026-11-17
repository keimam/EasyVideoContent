"""AI-powered visual search keyword generator per chapter (Stage 8B.2, F8).

Generates concise 3-6 word English stock-footage search queries describing what
appears on screen (subject, location, lighting/atmosphere) rather than slicing raw
words out of narration sentences. Issues one batched LLM call per chapter,
ensures distinct queries when multiple shots share one sentence, respects
explicit [search: ...] tags and preset subject/style, caches results locally,
and falls back cleanly when no LLM is configured.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.longform.hashing import hash_string, normalize_text
from app.services.longform.language_guard import normalize_language_code
from app.services.longform.llm_adapter import (
    LLMAdapter,
    LLMCreditError,
    LLMModelNotFoundError,
    get_llm_readiness,
)
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, Shot
from app.services.longform.visual.cache import get_global_cache_dir
from app.services.longform.visual.keyword_translator import (
    _invoke_translation_llm,
    _sanitize_english_query,
    translate_single_query_to_english,
)
from app.services.longform.visual.query_extractor import extract_short_search_query


def _ai_kw_cache_file(cache_dir: Optional[Path] = None) -> Path:
    root = Path(cache_dir) if cache_dir else get_global_cache_dir()
    path = root / "ai_visual_keywords.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _ai_anchor_cache_file(cache_dir: Optional[Path] = None) -> Path:
    root = Path(cache_dir) if cache_dir else get_global_cache_dir()
    path = root / "ai_visual_anchors.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_json_cache(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json_cache(path: Path, cache: Dict[str, Any], label: str) -> None:
    try:
        path.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to persist {label}: {exc}")


def _load_ai_kw_cache(cache_dir: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json_cache(_ai_kw_cache_file(cache_dir))


def _save_ai_kw_cache(cache: Dict[str, Any], cache_dir: Optional[Path] = None) -> None:
    _save_json_cache(_ai_kw_cache_file(cache_dir), cache, "AI visual keyword cache")


def _ai_kw_cache_key(
    project_lang: str,
    subject_sig: str,
    narration: str,
    part_idx: int,
    part_total: int,
    anchor_sig: str = "",
) -> str:
    norm_lang = normalize_language_code(project_lang)
    norm_narr = normalize_text(narration).lower()
    norm_subj = normalize_text(subject_sig).lower()
    norm_anch = normalize_text(anchor_sig).lower()
    return hash_string(f"f12_aikw|{norm_lang}|{norm_subj}|{norm_anch}|{part_idx}/{part_total}|{norm_narr}")


ABSTRACT_NON_VISUAL_WORDS = {
    "hunger", "unplanned", "unique", "feeling", "feelings", "memory", "memories",
    "emotion", "emotions", "emotional", "concept", "concepts", "idea", "ideas",
    "ritual", "rituals", "nostalgia", "nostalgic", "legacy", "soul", "spirit",
    "truth", "secret", "journey", "meaning", "vibe", "vibes", "satisfaction",
    "satisfied", "magic", "magical", "wonder", "destiny", "dream", "dreams",
    "hope", "sadness", "happiness", "joy", "fear", "silence", "thought",
    "thoughts", "wisdom", "way", "ways", "reason", "story", "stories",
    "experience", "atmosphere", "moment", "moments", "sense", "craving",
    "unexpected", "sudden", "always", "actually", "really", "simply",
    "casual", "simple", "everyday",
}

_TIME_OR_GENERIC_ONLY = {
    "night", "day", "morning", "evening", "afternoon", "dark", "light",
    "city", "urban", "people", "man", "woman", "scene", "view",
}


def _extract_json_any_dict(raw_text: str) -> Dict[str, Any]:
    """Parse JSON object from LLM text while preserving list/nested values (unlike F2 str-only helper)."""
    text = str(raw_text or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    for candidate in (text, (re.search(r"\{[\s\S]*\}", text) or [None])[0]):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return {str(k): v for k, v in parsed.items() if v is not None}
        except Exception:
            continue
    return {}


def _clean_term_list(values: Any) -> List[str]:
    """Normalize a list or comma-separated string into lowercase unique non-empty terms."""
    if isinstance(values, str):
        raw_items = [x.strip() for x in re.split(r"[,;\n]+", values) if x.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw_items = [str(x).strip() for x in values if str(x or "").strip()]
    else:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        norm = normalize_text(item).lower()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _heuristic_anchors_for_project(manifest: ProjectManifest) -> Tuple[List[str], List[str], str]:
    """Deterministic concrete anchor/avoid fallback when LLM is unavailable."""
    snap = manifest.preset_snapshot or {}
    combined_ctx = " ".join(
        [
            str(manifest.topic or ""),
            str(manifest.title or ""),
            str(manifest.preset_id or ""),
            str(manifest.sub_niche_focus or ""),
            str(snap.get("name") or ""),
            " ".join(str(x) for x in (snap.get("required_subject_terms") or [])),
            " ".join(str(x) for x in (snap.get("topic_keywords") or [])),
        ]
    ).lower()

    req_clean = _clean_term_list(snap.get("required_subject_terms") or [])
    topic_clean = _clean_term_list(snap.get("topic_keywords") or [])
    block_clean = _clean_term_list(snap.get("blocked_terms") or [])

    anchors: List[str] = []
    avoids: List[str] = list(block_clean)

    is_food = any(
        k in combined_ctx
        for k in ("kuliner", "food", "street food", "makan", "masak", "cook", "warung", "noodle", "restoran", "jajan")
    )
    is_nature_animal = any(k in combined_ctx for k in ("animal", "wildlife", "hewan", "satwa", "pet", "zoo"))

    if is_food:
        anchors.extend(["food", "street food", "vendor", "cooking", "stall", "market", "night market", "dish", "kitchen"])
        if not is_nature_animal:
            avoids.extend(["animal", "wildlife", "pet", "panda", "dog", "cat", "bird", "cartoon", "garbage", "trash"])
    elif req_clean or topic_clean:
        anchors.extend((req_clean + topic_clean)[:8])
        if not is_nature_animal:
            avoids.extend(["cartoon", "animation", "illustration", "animal", "wildlife"])
    else:
        tokens = [
            w for w in re.findall(r"[a-z0-9]{3,}", combined_ctx)
            if w not in {"umum", "id", "en", "documentary", "naratif", "dokumenter", "the", "and"}
        ]
        anchors.extend(tokens[:6] or ["city street", "people", "workplace", "outdoor"])
        if not is_nature_animal:
            avoids.extend(["animal", "wildlife", "pet", "cartoon", "garbage"])

    anchors = _clean_term_list(req_clean + anchors)[:10]
    avoids = _clean_term_list(avoids)[:10]
    subj_label = str(manifest.topic or manifest.title or snap.get("name") or "documentary").strip()
    summary = f"Concrete real-world visual scenes for {subj_label}: {', '.join(anchors[:5])}."
    return anchors, avoids, summary


def _build_project_anchors_prompt(manifest: ProjectManifest) -> str:
    snap = manifest.preset_snapshot or {}
    preset_name = str(snap.get("name") or manifest.preset_id or "").strip()
    req_terms = ", ".join(str(x) for x in (snap.get("required_subject_terms") or []))
    topic_kws = ", ".join(str(x) for x in (snap.get("topic_keywords") or []))
    chapter_titles = "; ".join(ch.chapter_title for ch in (manifest.chapters or [])[:6] if ch.chapter_title)
    return (
        "You are a stock-footage supervision assistant preparing visual search guardrails.\n"
        f"Project title: {manifest.title}\n"
        f"Project topic: {manifest.topic or manifest.title}\n"
        f"Niche/preset: {preset_name} ({manifest.preset_id})\n"
        + (f"Sub-niche focus: {manifest.sub_niche_focus}\n" if manifest.sub_niche_focus else "")
        + (f"Existing preset subjects: {req_terms}\n" if req_terms else "")
        + (f"Existing topic keywords: {topic_kws}\n" if topic_kws else "")
        + (f"Chapter titles: {chapter_titles}\n" if chapter_titles else "")
        + "\nReturn ONLY strict JSON with these 3 fields:\n"
        "{\n"
        '  "anchor_terms": ["6 to 10 concrete lowercase English nouns/phrases that MUST relate to stock footage tags (e.g. street food, vendor, cooking, stall, market, night market, wok, dish)"],\n'
        '  "avoid_terms": ["5 to 10 concrete lowercase English distractors/false-positive tags to reject (e.g. animal, wildlife, pet, panda, dog, cartoon, animation, garbage, plastic bottle) unless relevant to the topic"],\n'
        '  "visual_summary": "1 concise English sentence describing concrete camera-visible settings, subjects, and lighting for this project"\n'
        "}\n"
        "- No abstract emotion words in anchor_terms; use concrete objects/places searchable on Pixabay/Pexels.\n"
    )


def ensure_project_visual_anchors(
    manifest: ProjectManifest,
    *,
    cache_dir: Optional[Path] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Generate/load cached project visual anchor_terms, avoid_terms, and visual_summary once per project (F12)."""
    if not force and getattr(manifest, "visual_anchor_terms", None):
        return {
            "anchor_terms": list(manifest.visual_anchor_terms),
            "avoid_terms": list(getattr(manifest, "visual_avoid_terms", []) or []),
            "visual_summary": getattr(manifest, "visual_summary", None) or "",
            "source": "manifest",
            "cached": False,
        }

    snap = manifest.preset_snapshot or {}
    sig_raw = "|".join([
        str(manifest.topic or manifest.title or "").lower().strip(),
        str(manifest.preset_id or "").lower().strip(),
        str(manifest.sub_niche_focus or "").lower().strip(),
        ",".join(str(x).lower() for x in (snap.get("required_subject_terms") or [])),
        ",".join(str(x).lower() for x in (snap.get("topic_keywords") or [])),
    ])
    c_key = hash_string(f"f12_anchor|{sig_raw}")
    cache_path = _ai_anchor_cache_file(cache_dir)
    cache = _load_json_cache(cache_path)

    if not force and isinstance(cache.get(c_key), dict) and cache[c_key].get("anchor_terms"):
        entry = cache[c_key]
        manifest.visual_anchor_terms = _clean_term_list(entry.get("anchor_terms"))
        manifest.visual_avoid_terms = _clean_term_list(entry.get("avoid_terms"))
        manifest.visual_summary = str(entry.get("visual_summary") or "").strip() or None
        return {
            "anchor_terms": list(manifest.visual_anchor_terms),
            "avoid_terms": list(manifest.visual_avoid_terms),
            "visual_summary": manifest.visual_summary or "",
            "source": str(entry.get("source") or "cache"),
            "cached": True,
        }

    readiness = get_llm_readiness()
    llm_ready = (llm_adapter is not None) or (
        bool(readiness.get("configured")) and not bool(readiness.get("is_invalid"))
    )

    h_anchors, h_avoids, h_summary = _heuristic_anchors_for_project(manifest)

    if not llm_ready:
        manifest.visual_anchor_terms = h_anchors
        manifest.visual_avoid_terms = h_avoids
        manifest.visual_summary = h_summary
        return {
            "anchor_terms": h_anchors,
            "avoid_terms": h_avoids,
            "visual_summary": h_summary,
            "source": "heuristic",
            "cached": False,
        }

    try:
        prompt = _build_project_anchors_prompt(manifest)
        content, used_prov, used_model = _invoke_translation_llm(
            prompt, llm_adapter=llm_adapter, max_tokens=360
        )
        data = _extract_json_any_dict(content)
        anchors = _clean_term_list(data.get("anchor_terms"))
        avoids = _clean_term_list(data.get("avoid_terms"))
        summary = str(data.get("visual_summary") or "").strip()
        if not anchors:
            anchors = h_anchors
        if not avoids:
            avoids = h_avoids
        if not summary:
            summary = h_summary

        manifest.visual_anchor_terms = anchors[:12]
        manifest.visual_avoid_terms = avoids[:12]
        manifest.visual_summary = summary

        cache[c_key] = {
            "anchor_terms": manifest.visual_anchor_terms,
            "avoid_terms": manifest.visual_avoid_terms,
            "visual_summary": summary,
            "provider": used_prov,
            "model": used_model,
            "source": "ai",
        }
        _save_json_cache(cache_path, cache, "AI visual anchors cache")
        return {
            "anchor_terms": list(manifest.visual_anchor_terms),
            "avoid_terms": list(manifest.visual_avoid_terms),
            "visual_summary": summary,
            "source": "ai",
            "cached": False,
        }
    except (LLMCreditError, LLMModelNotFoundError):
        raise
    except Exception as exc:
        logger.warning(f"F12 anchor generation fallback: {exc}")
        manifest.visual_anchor_terms = h_anchors
        manifest.visual_avoid_terms = h_avoids
        manifest.visual_summary = h_summary
        return {
            "anchor_terms": h_anchors,
            "avoid_terms": h_avoids,
            "visual_summary": h_summary,
            "source": "heuristic_fallback",
            "cached": False,
        }


def _sanitize_concrete_query(
    raw_query: Any,
    *,
    anchor_terms: Optional[List[str]] = None,
    visual_keywords: Optional[List[str]] = None,
    fallback_index: int = 0,
) -> str:
    """Enforce 3-6 concrete camera-visible English words, stripping abstract concepts (F12)."""
    cleaned = _sanitize_english_query(str(raw_query or ""), max_words=6)
    tokens = [w for w in cleaned.split() if w.lower() not in ABSTRACT_NON_VISUAL_WORDS]
    meaningful = [w for w in tokens if w.lower() not in _TIME_OR_GENERIC_ONLY]

    anchors = _clean_term_list(anchor_terms or [])
    vis_kws = _clean_term_list(visual_keywords or [])
    pool = anchors + vis_kws

    if len(meaningful) >= 2:
        return _sanitize_english_query(" ".join(tokens[:6]), max_words=6)

    # Abstract-only query (e.g. 'unplanned hunger night' -> only ['night']) -> anchor it to concrete theme!
    seed_words = [w for w in tokens if w.lower() in _TIME_OR_GENERIC_ONLY][:2]
    chosen_phrases: List[str] = []
    if pool:
        start = fallback_index % len(pool)
        rotated = pool[start:] + pool[:start]
        for ph in rotated:
            if ph not in chosen_phrases:
                chosen_phrases.append(ph)
            if len(" ".join(seed_words + chosen_phrases).split()) >= 4:
                break
    combined = " ".join(seed_words + chosen_phrases)
    res = _sanitize_english_query(combined, max_words=6)
    if res:
        return res
    return cleaned or "street market people walking"


def _build_concrete_query_alternatives(
    raw_value: Any,
    *,
    anchor_terms: Optional[List[str]] = None,
    visual_keywords: Optional[List[str]] = None,
) -> List[str]:
    """Normalize LLM response (list or str) into 2-3 distinct concrete 3-6 word English queries (F12)."""
    if isinstance(raw_value, (list, tuple)):
        raw_candidates = [str(x or "").strip() for x in raw_value if str(x or "").strip()]
    elif isinstance(raw_value, str) and raw_value.strip():
        raw_candidates = [raw_value.strip()]
    else:
        raw_candidates = []

    out: List[str] = []
    seen: set[str] = set()
    for idx, cand in enumerate(raw_candidates):
        q = _sanitize_concrete_query(
            cand,
            anchor_terms=anchor_terms,
            visual_keywords=visual_keywords,
            fallback_index=idx,
        )
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
        if len(out) >= 3:
            break

    anchors = _clean_term_list(anchor_terms or [])
    vis_kws = _clean_term_list(visual_keywords or [])
    pool = [p for p in (anchors + vis_kws) if p]
    if out and len(out) < 2 and pool:
        suffixes = ["wide shot", "closeup cooking", "evening crowd", "stall vendor"]
        for idx in range(len(pool)):
            a1 = pool[idx % len(pool)]
            a2 = pool[(idx + 1) % len(pool)] if len(pool) > 1 else suffixes[idx % len(suffixes)]
            alt_raw = f"{a1} {a2}" if a1 != a2 else f"{a1} {suffixes[idx % len(suffixes)]}"
            alt_q = _sanitize_concrete_query(alt_raw, anchor_terms=anchors, visual_keywords=vis_kws, fallback_index=idx + 1)
            if alt_q and alt_q.lower() not in seen:
                seen.add(alt_q.lower())
                out.append(alt_q)
            if len(out) >= 3:
                break

    return out[:3]


def _find_explicit_search_tag(chapter: Chapter, shot: Shot) -> str:
    """Return explicit [search: ...] tag text from linked chapter segments if any."""
    seg_ids = set(getattr(shot, "segment_indices", []) or [])
    if not seg_ids:
        return ""
    for grp in getattr(chapter, "tts_groups", []) or []:
        for seg in getattr(grp, "segments", []) or []:
            if seg.segment_index in seg_ids and getattr(seg, "visual", None):
                sk = str(seg.visual.search_keywords or "").strip()
                if sk:
                    return sk
    return ""


def infer_keyword_source(chapter: Chapter, shot: Shot) -> str:
    """Infer effective keyword origin ('ai' | 'tag' | 'extraction') for display/logic."""
    explicit = str(getattr(shot, "search_query_source", "") or "").strip().lower()
    if explicit in ("ai", "tag", "extraction"):
        return explicit
    if _find_explicit_search_tag(chapter, shot):
        return "tag"
    return "extraction"


def format_keyword_reason(reason: Optional[str], lang: str = "id") -> str:
    """Translate structured search_query_reason codes into concise localized phrases."""
    raw = str(reason or "").strip()
    if not raw:
        return ""
    is_en = str(lang or "id").lower().startswith("en")
    if raw == "llm_not_ready":
        return "LLM not ready" if is_en else "LLM belum siap"
    if raw == "ai_empty_mapping":
        return "AI returned no query" if is_en else "AI tidak mengembalikan query"
    if raw.startswith("ai_failed:"):
        detail = raw[len("ai_failed:") :].strip() or "error"
        return f"AI failed: {detail}" if is_en else f"AI gagal: {detail}"
    return raw


def _split_clauses_for_parts(sentence: str, part_total: int) -> List[str]:
    """Split a multi-clause narration sentence roughly evenly across N sequential shots."""
    txt = normalize_text(sentence)
    if part_total <= 1 or not txt:
        return [txt] * max(1, part_total)

    # Try splitting on strong clause boundaries first
    raw_clauses = [
        c.strip(" ,;:-")
        for c in re.split(r"[,;]+|\b(?:and|then|while|whereas|dan|serta|lalu|sambil|ketika)\b", txt, flags=re.IGNORECASE)
        if c and len(c.strip()) > 3
    ]
    if len(raw_clauses) >= part_total:
        # Bucket clauses sequentially across parts
        buckets: List[str] = []
        chunk_size = len(raw_clauses) / part_total
        for idx in range(part_total):
            s_i = int(round(idx * chunk_size))
            e_i = int(round((idx + 1) * chunk_size)) if idx < part_total - 1 else len(raw_clauses)
            joined = " ".join(raw_clauses[s_i:max(s_i + 1, e_i)]).strip()
            buckets.append(joined or txt)
        return buckets

    # Fallback: slice word stream proportionally
    words = txt.split()
    if len(words) < part_total * 2:
        return [txt] * part_total
    buckets = []
    step = len(words) / part_total
    for idx in range(part_total):
        s_i = int(round(idx * step))
        e_i = int(round((idx + 1) * step)) if idx < part_total - 1 else len(words)
        buckets.append(" ".join(words[s_i:max(s_i + 1, e_i)]).strip())
    return buckets


def get_shot_narration_slice(
    chapter: Chapter,
    shot: Shot,
    chapter_shots: Optional[List[Shot]] = None,
) -> Tuple[str, int, int, str]:
    """Return (full_narration, part_idx, part_total, clause_focus) for a shot."""
    seg_ids = set(getattr(shot, "segment_indices", []) or [])
    seg_texts: List[str] = []
    for grp in getattr(chapter, "tts_groups", []) or []:
        for seg in getattr(grp, "segments", []) or []:
            if seg.segment_index in seg_ids and seg.narration_text:
                seg_texts.append(seg.narration_text.strip())
    full_narr = normalize_text(" ".join(seg_texts))

    siblings = [
        s for s in (chapter_shots if chapter_shots is not None else getattr(chapter, "shots", []) or [])
        if tuple(sorted(getattr(s, "segment_indices", []) or [])) == tuple(sorted(seg_ids))
    ]
    siblings.sort(key=lambda s: getattr(s, "shot_index", 0))
    part_total = max(1, len(siblings))
    part_idx = 1
    for i, s in enumerate(siblings, start=1):
        if s.shot_id == shot.shot_id:
            part_idx = i
            break

    clauses = _split_clauses_for_parts(full_narr, part_total)
    focus = clauses[part_idx - 1] if 0 <= (part_idx - 1) < len(clauses) else full_narr
    return full_narr, part_idx, part_total, focus


def estimate_ai_keyword_calls(
    manifest: ProjectManifest,
    *,
    chapter_index: Optional[int] = None,
    force: bool = True,
    cache_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Estimate unlocked shots and LLM batch calls needed for F8/F12 keyword generation."""
    from app.services.longform.language_guard import resolve_project_language

    proj_lang = resolve_project_language(manifest)
    snap = manifest.preset_snapshot or {}
    subj_sig = "|".join(str(x) for x in (snap.get("required_subject_terms") or snap.get("topic_keywords") or [])[:4])
    anchor_sig = "|".join(_clean_term_list(getattr(manifest, "visual_anchor_terms", []) or [])[:5])
    cache = _load_ai_kw_cache(cache_dir)

    target_chapters = [
        ch for ch in manifest.chapters
        if chapter_index is None or ch.chapter_index == chapter_index
    ]

    unlocked_shots = 0
    tag_shots = 0
    uncached_shots = 0
    chapters_with_calls = 0

    for ch in target_chapters:
        ch_has_uncached = False
        for sh in ch.shots:
            if getattr(sh, "locked", False):
                continue
            unlocked_shots += 1
            if infer_keyword_source(ch, sh) == "tag":
                tag_shots += 1
                continue
            if not force and getattr(sh, "search_query_source", None) == "ai" and getattr(sh, "search_query_en", None):
                continue
            narr, part_idx, part_total, _ = get_shot_narration_slice(ch, sh, ch.shots)
            if not narr:
                continue
            c_key = _ai_kw_cache_key(proj_lang, subj_sig, narr, part_idx, part_total, anchor_sig=anchor_sig)
            if not force and isinstance(cache.get(c_key), dict) and cache[c_key].get("en"):
                continue
            uncached_shots += 1
            ch_has_uncached = True
        if ch_has_uncached:
            chapters_with_calls += 1

    return {
        "unlocked_shots": unlocked_shots,
        "tag_shots": tag_shots,
        "uncached_shots": uncached_shots,
        "estimated_llm_calls": chapters_with_calls,
    }


def _build_visual_keywords_batch_prompt(
    items: List[Dict[str, Any]],
    *,
    source_lang: str,
    preset_snapshot: Optional[Dict[str, Any]] = None,
    project_topic: Optional[str] = None,
    chapter_title: Optional[str] = None,
    sub_niche_focus: Optional[str] = None,
    visual_summary: Optional[str] = None,
    anchor_terms: Optional[List[str]] = None,
    avoid_terms: Optional[List[str]] = None,
) -> str:
    snap = preset_snapshot or {}
    preset_name = str(snap.get("name") or snap.get("id") or "").strip()
    subj_terms = snap.get("required_subject_terms") or snap.get("topic_keywords") or []
    vis_terms = snap.get("visual_keywords") or []
    subj_hint = ", ".join(str(x) for x in subj_terms[:5]) if subj_terms else ""
    style_hint = ", ".join(str(x) for x in vis_terms[:5]) if vis_terms else ""
    anchor_hint = ", ".join(_clean_term_list(anchor_terms or [])[:10])
    avoid_hint = ", ".join(_clean_term_list(avoid_terms or [])[:10])

    return (
        "You are a documentary video director creating stock footage search queries (Pixabay/Pexels/Coverr).\n"
        f"Narration language: {source_lang}\n"
        + (f"Project topic: {project_topic}\n" if project_topic else "")
        + (f"Chapter title: {chapter_title}\n" if chapter_title else "")
        + (f"Niche: {preset_name}\n" if preset_name else "")
        + (f"Sub-niche focus: {sub_niche_focus}\n" if sub_niche_focus else "")
        + (f"Project visual summary: {visual_summary}\n" if visual_summary else "")
        + (f"Anchor visual subjects (MUST ground queries on-theme): {anchor_hint}\n" if anchor_hint else "")
        + (f"Forbidden subjects/tags to avoid: {avoid_hint}\n" if avoid_hint else "")
        + (f"Core visual subject: {subj_hint}\n" if subj_hint else "")
        + (f"Visual style hints: {style_hint}\n" if style_hint else "")
        + "\nRules:\n"
        "- For EACH shot, output 2 to 3 DISTINCT concrete English stock-footage queries (each 3 to 6 words ONLY) as a JSON array in priority order.\n"
        "- EVERY query MUST describe PHYSICAL OBJECTS, PLACES, PEOPLE, LIGHTING, OR ACTIONS directly visible to a camera (e.g. 'night street food stall crowd', 'vendor cooking noodles wok steam').\n"
        "- STRICTLY FORBIDDEN: abstract concepts, feelings, emotions, or non-camera adjectives (NEVER use words like hunger, unplanned, unique, feeling, memory, nostalgia, ritual, satisfaction, soul, legacy, story, magic, experience, casual).\n"
        "- If a narration line is abstract or poetic (e.g. 'malam membangunkan rasa lapar yang tak direncanakan'), translate it into concrete on-theme visual scenes matching the project topic and anchor subjects (e.g. ['night street food stall crowd', 'vendor grilling skewers charcoal smoke', 'hungry diners eating street food']).\n"
        "- NEVER search bare generic objects that clash with context (e.g. for 'warung tenda bangku plastik', search 'street food stall dining tables' or 'outdoor night food vendor', NOT camping tent or empty plastic bottle).\n"
        "- CRITICAL: Items sharing the same narration sentence (`part` 1/N, 2/N, ...) MUST receive COMPLETELY DIFFERENT queries covering sequential concrete moments.\n"
        "- Return ONLY a strict JSON object `{shot_id: [\"primary 3-6 words\", \"alt 3-6 words\", \"alt2 3-6 words\"]}` with no commentary.\n"
        f"Shots:\n{json.dumps(items, ensure_ascii=False)}"
    )


def _differentiate_duplicate_same_sentence_queries(
    shots: List[Shot],
    chapter: Chapter,
    preset_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    """Guarantee shots derived from the same narration sentence do not share an identical English query."""
    groups: Dict[Tuple[int, ...], List[Shot]] = {}
    for sh in shots:
        key = tuple(sorted(getattr(sh, "segment_indices", []) or []))
        if key:
            groups.setdefault(key, []).append(sh)

    vis_kws = [str(x).strip() for x in (preset_snapshot or {}).get("visual_keywords", []) if str(x).strip()]
    suffix_modifiers = ["wide view", "close up", "street view", "night glow"]

    for _seg_key, grp_shots in groups.items():
        if len(grp_shots) <= 1:
            continue
        grp_shots.sort(key=lambda s: getattr(s, "shot_index", 0))
        seen: set[str] = set()
        for idx, sh in enumerate(grp_shots):
            cur = normalize_text(getattr(sh, "search_query_en", None) or sh.search_query or "").lower()
            if cur and cur not in seen:
                seen.add(cur)
                continue
            # Re-derive using clause slice or visual preset distinction
            narr, _, _, focus = get_shot_narration_slice(chapter, sh, grp_shots)
            clause_q = extract_short_search_query(focus, visual_keywords=vis_kws)
            alt = _sanitize_english_query(clause_q, max_words=6)
            if not alt or alt.lower() in seen:
                base_words = [w for w in (cur or alt or "cinematic scene").split() if w][:4]
                mod = suffix_modifiers[idx % len(suffix_modifiers)]
                alt = _sanitize_english_query(" ".join(base_words + mod.split()), max_words=6)
            sh.search_query_en = alt
            if not sh.search_query:
                sh.search_query = alt
            existing_alts = [q for q in (getattr(sh, "search_queries_en", None) or []) if q and q.lower() != alt.lower()]
            sh.search_queries_en = [alt] + existing_alts[:2]
            seen.add(alt.lower())


def generate_chapter_visual_keywords_ai(
    shots: List[Shot],
    chapter: Chapter,
    *,
    preset_snapshot: Optional[Dict[str, Any]] = None,
    project_lang: str = "id-ID",
    project_topic: Optional[str] = None,
    sub_niche_focus: Optional[str] = None,
    visual_summary: Optional[str] = None,
    anchor_terms: Optional[List[str]] = None,
    avoid_terms: Optional[List[str]] = None,
    cache_dir: Optional[Path] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Generate 2-3 concise 3-6 word concrete English visual queries per shot via 1 batch LLM call (F8/F12)."""
    norm_lang = normalize_language_code(project_lang)
    snap = preset_snapshot or {}
    subj_terms = snap.get("required_subject_terms") or snap.get("topic_keywords") or []
    vis_terms = snap.get("visual_keywords") or []
    clean_anchors = _clean_term_list(anchor_terms or subj_terms or [])
    clean_avoids = _clean_term_list(avoid_terms or [])
    subj_sig = "|".join(str(x) for x in subj_terms[:4])
    anchor_sig = "|".join(clean_anchors[:5])
    cache = _load_ai_kw_cache(cache_dir)

    updated = 0
    skipped_locked = 0
    tag_preserved = 0
    cached_hits = 0
    llm_calls = 0
    warning: Optional[str] = None
    examples: List[Dict[str, str]] = []

    readiness = get_llm_readiness()
    llm_ready = (llm_adapter is not None) or (
        bool(readiness.get("configured")) and not bool(readiness.get("is_invalid"))
    )

    pending: List[Tuple[Shot, str, str, int, int, str]] = []

    for shot in shots:
        if getattr(shot, "locked", False):
            skipped_locked += 1
            continue

        # 1. Respect explicit [search: ...] tag if present
        tag_text = _find_explicit_search_tag(chapter, shot)
        if tag_text or str(getattr(shot, "search_query_source", "") or "").lower() == "tag":
            raw_tag = tag_text or shot.search_query or shot.visual_prompt
            shot.search_query = raw_tag
            shot.search_query_source = "tag"
            if llm_ready and raw_tag:
                en_tag, warn_t = translate_single_query_to_english(
                    raw_tag,
                    preset_snapshot=snap,
                    project_lang=project_lang,
                    cache_dir=cache_dir,
                    llm_adapter=llm_adapter,
                )
                shot.search_query_en = en_tag or raw_tag
                shot.search_queries_en = [shot.search_query_en]
                if warn_t and not warning:
                    warning = warn_t
            else:
                shot.search_query_en = shot.search_query_en or raw_tag
                if shot.search_query_en and not getattr(shot, "search_queries_en", None):
                    shot.search_queries_en = [shot.search_query_en]
            tag_preserved += 1
            continue

        if not force and getattr(shot, "search_query_source", None) == "ai" and getattr(shot, "search_query_en", None):
            continue

        full_narr, part_idx, part_total, focus_clause = get_shot_narration_slice(chapter, shot, shots)
        src_text = full_narr or shot.visual_prompt or shot.search_query or ""
        if not src_text:
            continue

        c_key = _ai_kw_cache_key(norm_lang, subj_sig, src_text, part_idx, part_total, anchor_sig=anchor_sig)
        cached_entry = cache.get(c_key) if not force else None
        if isinstance(cached_entry, dict) and (cached_entry.get("alts") or cached_entry.get("en")):
            raw_cached = cached_entry.get("alts") or cached_entry.get("en")
            alts = _build_concrete_query_alternatives(
                raw_cached,
                anchor_terms=clean_anchors,
                visual_keywords=vis_terms,
            )
            if alts:
                shot.search_query_en = alts[0]
                shot.search_queries_en = alts
                shot.search_query_source = "ai"
                shot.search_query_reason = None
                if not shot.search_query:
                    shot.search_query = alts[0]
                cached_hits += 1
                updated += 1
                examples.append({
                    "shot_id": shot.shot_id,
                    "narration": full_narr or src_text,
                    "focus": focus_clause,
                    "query": alts[0],
                    "alternatives": ", ".join(alts[1:]),
                    "source": "ai",
                })
                continue

        pending.append((shot, full_narr or src_text, c_key, part_idx, part_total, focus_clause))

    if not pending:
        return {
            "updated": updated,
            "skipped_locked": skipped_locked,
            "tag_preserved": tag_preserved,
            "cached": cached_hits,
            "llm_calls": 0,
            "fallback_used": False,
            "warning": warning,
            "examples": examples,
        }

    # 2. Fallback when LLM is NOT ready
    if not llm_ready:
        warn_msg = "LLM belum siap — memakai ekstraksi lokal sebagai cadangan."
        logger.warning(f"F8 AI keywords fallback: {warn_msg}")
        for shot, src_text, _c_key, _p_i, _p_t, focus_clause in pending:
            ext_q = extract_short_search_query(
                focus_clause or src_text,
                visual_keywords=vis_terms,
                topic_keywords=snap.get("topic_keywords"),
                required_subject_terms=snap.get("required_subject_terms"),
            )
            shot.search_query = ext_q
            shot.search_query_source = "extraction"
            shot.search_query_reason = "llm_not_ready"
            if norm_lang != "en":
                # Keep non-English extracted query clearly marked; only set search_query_en if project is English
                if not getattr(shot, "search_query_en", None):
                    shot.search_query_en = None
            else:
                shot.search_query_en = ext_q
            updated += 1
            examples.append({
                "shot_id": shot.shot_id,
                "narration": src_text,
                "focus": focus_clause,
                "query": ext_q,
                "source": "extraction",
            })
        _differentiate_duplicate_same_sentence_queries([p[0] for p in pending], chapter, snap)
        return {
            "updated": updated,
            "skipped_locked": skipped_locked,
            "tag_preserved": tag_preserved,
            "cached": cached_hits,
            "llm_calls": 0,
            "fallback_used": True,
            "warning": warn_msg,
            "examples": examples,
        }

    # 3. Issue ONE batched LLM call for this chapter (bounded max_tokens + reasoning minimization)
    payload_items: List[Dict[str, Any]] = []
    for shot, src_text, _c_key, part_idx, part_total, focus_clause in pending:
        item: Dict[str, Any] = {
            "id": shot.shot_id,
            "narration": src_text,
        }
        if part_total > 1:
            item["part"] = f"{part_idx}/{part_total}"
            item["focus_clause"] = focus_clause
        payload_items.append(item)

    prompt = _build_visual_keywords_batch_prompt(
        payload_items,
        source_lang=norm_lang,
        preset_snapshot=snap,
        project_topic=project_topic,
        chapter_title=getattr(chapter, "chapter_title", None),
        sub_niche_focus=sub_niche_focus,
        visual_summary=visual_summary,
        anchor_terms=clean_anchors,
        avoid_terms=clean_avoids,
    )
    batch_max_tokens = min(2400, max(520, 95 * len(pending) + 260))

    try:
        content, used_prov, used_model = _invoke_translation_llm(
            prompt, llm_adapter=llm_adapter, max_tokens=batch_max_tokens
        )
        llm_calls = 1
        mapping = _extract_json_any_dict(content)
        for shot, src_text, c_key, _part_idx, _part_total, focus_clause in pending:
            raw_q = mapping.get(shot.shot_id)
            alts = _build_concrete_query_alternatives(
                raw_q,
                anchor_terms=clean_anchors,
                visual_keywords=vis_terms,
            )
            if alts:
                q_clean = alts[0]
                shot.search_query_en = q_clean
                shot.search_queries_en = alts
                shot.search_query_source = "ai"
                shot.search_query_reason = None
                if not shot.search_query:
                    shot.search_query = q_clean
                cache[c_key] = {
                    "lang": norm_lang,
                    "en": q_clean,
                    "alts": alts,
                    "provider": used_prov,
                    "model": used_model,
                }
                updated += 1
                examples.append({
                    "shot_id": shot.shot_id,
                    "narration": src_text,
                    "focus": focus_clause,
                    "query": q_clean,
                    "alternatives": ", ".join(alts[1:]),
                    "source": "ai",
                })
            else:
                ext_q = extract_short_search_query(focus_clause or src_text, visual_keywords=vis_terms)
                shot.search_query = shot.search_query or ext_q
                shot.search_query_source = "extraction"
                shot.search_query_reason = "ai_empty_mapping"
                if norm_lang == "en" and not shot.search_query_en:
                    shot.search_query_en = ext_q
                updated += 1

        _differentiate_duplicate_same_sentence_queries(shots, chapter, snap)
        # Sync differentiated queries back into cache/examples
        for shot, _src_text, c_key, _p_i, _p_t, _fc in pending:
            if shot.search_query_source == "ai" and shot.search_query_en and c_key in cache:
                cache[c_key]["en"] = shot.search_query_en
                if getattr(shot, "search_queries_en", None):
                    cache[c_key]["alts"] = list(shot.search_queries_en)
        for ex in examples:
            sh_obj = next((p[0] for p in pending if p[0].shot_id == ex["shot_id"]), None)
            if sh_obj and sh_obj.search_query_en:
                ex["query"] = sh_obj.search_query_en
        _save_ai_kw_cache(cache, cache_dir)
        return {
            "updated": updated,
            "skipped_locked": skipped_locked,
            "tag_preserved": tag_preserved,
            "cached": cached_hits,
            "llm_calls": llm_calls,
            "fallback_used": False,
            "warning": warning,
            "examples": examples,
        }
    except (LLMCreditError, LLMModelNotFoundError):
        raise
    except Exception as exc:
        short_err = str(exc).strip().splitlines()[0][:120]
        warn_msg = f"AI gagal ({type(exc).__name__}): {short_err}"
        logger.warning(warn_msg)
        for shot, src_text, _c_key, _p_i, _p_t, focus_clause in pending:
            ext_q = extract_short_search_query(
                focus_clause or src_text,
                visual_keywords=vis_terms,
                topic_keywords=snap.get("topic_keywords"),
                required_subject_terms=snap.get("required_subject_terms"),
            )
            shot.search_query = shot.search_query or ext_q
            shot.search_query_source = "extraction"
            shot.search_query_reason = f"ai_failed:{type(exc).__name__}: {short_err}"
            if norm_lang == "en" and not shot.search_query_en:
                shot.search_query_en = ext_q
            updated += 1
        return {
            "updated": updated,
            "skipped_locked": skipped_locked,
            "tag_preserved": tag_preserved,
            "cached": cached_hits,
            "llm_calls": 1,
            "fallback_used": True,
            "warning": warn_msg,
            "examples": examples,
        }


def update_project_visual_keywords_ai(
    manifest: ProjectManifest,
    *,
    chapter_index: Optional[int] = None,
    force: bool = True,
    mark_stale: bool = True,
    cache_dir: Optional[Path] = None,
    llm_adapter: Optional[LLMAdapter] = None,
) -> Dict[str, Any]:
    """Update visual search keywords with AI across unlocked shots of target chapter(s).

    When ``mark_stale=True``, unlocked shots whose keyword changed (or that already had
    an asset) are marked ``ItemStatus.stale`` ('perlu diperbarui') so 'Cari & Susun'
    re-fetches ONLY those shots while leaving locked shots completely untouched.
    """
    from app.services.longform.language_guard import resolve_project_language

    proj_lang = resolve_project_language(manifest)
    snap = manifest.preset_snapshot or {}

    # F12: Generate/load cached anchor & avoid terms once per project before updating keywords
    anchor_res = ensure_project_visual_anchors(
        manifest,
        cache_dir=cache_dir,
        llm_adapter=llm_adapter,
        force=False,
    )
    proj_anchors = list(getattr(manifest, "visual_anchor_terms", []) or anchor_res.get("anchor_terms") or [])
    proj_avoids = list(getattr(manifest, "visual_avoid_terms", []) or anchor_res.get("avoid_terms") or [])
    proj_vis_summary = getattr(manifest, "visual_summary", None) or anchor_res.get("visual_summary")

    total_updated = 0
    total_locked = 0
    total_tags = 0
    total_cached = 0
    total_llm_calls = 0 if anchor_res.get("source") != "ai" or anchor_res.get("cached") else 1
    stale_marked = 0
    fallback_used = False
    warnings: List[str] = []
    all_examples: List[Dict[str, str]] = []

    for ch in manifest.chapters:
        if chapter_index is not None and ch.chapter_index != chapter_index:
            continue
        unlocked = [s for s in ch.shots if not getattr(s, "locked", False)]
        locked_cnt = len(ch.shots) - len(unlocked)
        total_locked += locked_cnt
        if not unlocked:
            continue

        before_state = {
            s.shot_id: (
                getattr(s, "search_query_en", None),
                list(getattr(s, "search_queries_en", []) or []),
                getattr(s, "search_query_source", None),
            )
            for s in unlocked
        }

        res = generate_chapter_visual_keywords_ai(
            unlocked,
            ch,
            preset_snapshot=snap,
            project_lang=proj_lang,
            project_topic=manifest.topic or manifest.title,
            sub_niche_focus=getattr(manifest, "sub_niche_focus", None),
            visual_summary=proj_vis_summary,
            anchor_terms=proj_anchors,
            avoid_terms=proj_avoids,
            cache_dir=cache_dir,
            llm_adapter=llm_adapter,
            force=force,
        )
        total_updated += int(res.get("updated", 0))
        total_tags += int(res.get("tag_preserved", 0))
        total_cached += int(res.get("cached", 0))
        total_llm_calls += int(res.get("llm_calls", 0))
        fallback_used = fallback_used or bool(res.get("fallback_used"))
        if res.get("warning"):
            warnings.append(str(res["warning"]))
        all_examples.extend(res.get("examples") or [])

        if mark_stale:
            for s in unlocked:
                prev_en, prev_alts, prev_src = before_state[s.shot_id]
                now_en = getattr(s, "search_query_en", None)
                now_alts = list(getattr(s, "search_queries_en", []) or [])
                now_src = getattr(s, "search_query_source", None)
                changed = (now_en != prev_en) or (now_alts != prev_alts) or (now_src != prev_src)
                has_existing_media = bool(s.asset_path or s.rendered_clip)
                if changed:
                    # F13: Clear stale search candidates/tags whenever keyword changed so old results (e.g. animals) never linger
                    s.candidates = []
                    s.tags = []
                    s.relevance_score = None
                    s.effective_query = None
                    s.ai_relevance_status = None
                    s.ai_relevance_reason = None
                    if has_existing_media:
                        s.status = ItemStatus.stale
                        stale_marked += 1

    return {
        "updated": total_updated,
        "skipped_locked": total_locked,
        "tag_preserved": total_tags,
        "cached": total_cached,
        "llm_calls": total_llm_calls,
        "stale_marked": stale_marked,
        "fallback_used": fallback_used,
        "warnings": warnings,
        "examples": all_examples,
        "anchor_terms": proj_anchors,
        "avoid_terms": proj_avoids,
        "visual_summary": proj_vis_summary,
    }
