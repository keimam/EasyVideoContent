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
    _extract_json_dict,
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


def _load_ai_kw_cache(cache_dir: Optional[Path] = None) -> Dict[str, Any]:
    path = _ai_kw_cache_file(cache_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_ai_kw_cache(cache: Dict[str, Any], cache_dir: Optional[Path] = None) -> None:
    path = _ai_kw_cache_file(cache_dir)
    try:
        path.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to persist AI visual keyword cache: {exc}")


def _ai_kw_cache_key(
    project_lang: str,
    subject_sig: str,
    narration: str,
    part_idx: int,
    part_total: int,
) -> str:
    norm_lang = normalize_language_code(project_lang)
    norm_narr = normalize_text(narration).lower()
    norm_subj = normalize_text(subject_sig).lower()
    return hash_string(f"f8_aikw|{norm_lang}|{norm_subj}|{part_idx}/{part_total}|{norm_narr}")


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
    """Estimate unlocked shots and LLM batch calls needed for F8 keyword generation."""
    from app.services.longform.language_guard import resolve_project_language

    proj_lang = resolve_project_language(manifest)
    snap = manifest.preset_snapshot or {}
    subj_sig = "|".join(str(x) for x in (snap.get("required_subject_terms") or snap.get("topic_keywords") or [])[:4])
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
            c_key = _ai_kw_cache_key(proj_lang, subj_sig, narr, part_idx, part_total)
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
) -> str:
    snap = preset_snapshot or {}
    preset_name = str(snap.get("name") or snap.get("id") or "").strip()
    subj_terms = snap.get("required_subject_terms") or snap.get("topic_keywords") or []
    vis_terms = snap.get("visual_keywords") or []
    subj_hint = ", ".join(str(x) for x in subj_terms[:5]) if subj_terms else ""
    style_hint = ", ".join(str(x) for x in vis_terms[:5]) if vis_terms else ""

    return (
        "You are a documentary video director creating stock footage search queries (Pixabay/Pexels/Coverr).\n"
        f"Narration language: {source_lang}\n"
        + (f"Project topic/niche: {preset_name}\n" if preset_name else "")
        + (f"Core visual subject: {subj_hint}\n" if subj_hint else "")
        + (f"Visual style hints: {style_hint}\n" if style_hint else "")
        + "\nRules:\n"
        "- For EACH item, output 1 concise visual search query in ENGLISH (3 to 6 words ONLY).\n"
        "- Describe WHAT IS VISIBLY SHOWN ON SCREEN (concrete subject, location, lighting/time-of-day, action), "
        "NEVER a word-for-word fragment from the narration sentence.\n"
        "- Example: 'Di tengah bisingnya klakson kota dan cahaya lampu neon...' -> "
        "Part 1/2: 'night city traffic neon lights', Part 2/2: 'street food charcoal grill garlic'.\n"
        "- CRITICAL: Items sharing the same narration sentence (`part` 1/N, 2/N, ...) MUST receive COMPLETELY DIFFERENT "
        "queries covering sequential visual moments in `focus_clause`.\n"
        "- No punctuation, no quotes, no commentary. Return ONLY a strict JSON object `{shot_id: \"3-6 english words\"}`.\n"
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
            seen.add(alt.lower())


def generate_chapter_visual_keywords_ai(
    shots: List[Shot],
    chapter: Chapter,
    *,
    preset_snapshot: Optional[Dict[str, Any]] = None,
    project_lang: str = "id-ID",
    cache_dir: Optional[Path] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Generate concise 3-6 word English visual queries for unlocked chapter shots via 1 batch LLM call."""
    norm_lang = normalize_language_code(project_lang)
    snap = preset_snapshot or {}
    subj_terms = snap.get("required_subject_terms") or snap.get("topic_keywords") or []
    vis_terms = snap.get("visual_keywords") or []
    subj_sig = "|".join(str(x) for x in subj_terms[:4])
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
                if warn_t and not warning:
                    warning = warn_t
            else:
                shot.search_query_en = shot.search_query_en or raw_tag
            tag_preserved += 1
            continue

        if not force and getattr(shot, "search_query_source", None) == "ai" and getattr(shot, "search_query_en", None):
            continue

        full_narr, part_idx, part_total, focus_clause = get_shot_narration_slice(chapter, shot, shots)
        src_text = full_narr or shot.visual_prompt or shot.search_query or ""
        if not src_text:
            continue

        c_key = _ai_kw_cache_key(norm_lang, subj_sig, src_text, part_idx, part_total)
        cached_entry = cache.get(c_key) if not force else None
        if isinstance(cached_entry, dict) and cached_entry.get("en"):
            q_clean = _sanitize_english_query(cached_entry["en"], max_words=6)
            if q_clean:
                shot.search_query_en = q_clean
                shot.search_query_source = "ai"
                shot.search_query_reason = None
                if not shot.search_query:
                    shot.search_query = q_clean
                cached_hits += 1
                updated += 1
                examples.append({
                    "shot_id": shot.shot_id,
                    "narration": full_narr or src_text,
                    "focus": focus_clause,
                    "query": q_clean,
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
    )
    batch_max_tokens = min(1800, max(380, 55 * len(pending) + 220))

    try:
        content, used_prov, used_model = _invoke_translation_llm(
            prompt, llm_adapter=llm_adapter, max_tokens=batch_max_tokens
        )
        llm_calls = 1
        mapping = _extract_json_dict(content)
        for shot, src_text, c_key, _part_idx, _part_total, focus_clause in pending:
            raw_q = mapping.get(shot.shot_id)
            q_clean = _sanitize_english_query(raw_q, max_words=6) if raw_q else ""
            if q_clean:
                shot.search_query_en = q_clean
                shot.search_query_source = "ai"
                shot.search_query_reason = None
                if not shot.search_query:
                    shot.search_query = q_clean
                cache[c_key] = {
                    "lang": norm_lang,
                    "en": q_clean,
                    "provider": used_prov,
                    "model": used_model,
                }
                updated += 1
                examples.append({
                    "shot_id": shot.shot_id,
                    "narration": src_text,
                    "focus": focus_clause,
                    "query": q_clean,
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

    total_updated = 0
    total_locked = 0
    total_tags = 0
    total_cached = 0
    total_llm_calls = 0
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
            s.shot_id: (getattr(s, "search_query_en", None), getattr(s, "search_query_source", None))
            for s in unlocked
        }

        res = generate_chapter_visual_keywords_ai(
            unlocked,
            ch,
            preset_snapshot=snap,
            project_lang=proj_lang,
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
                prev_en, prev_src = before_state[s.shot_id]
                now_en = getattr(s, "search_query_en", None)
                now_src = getattr(s, "search_query_source", None)
                changed = (now_en != prev_en) or (now_src != prev_src)
                has_existing_media = bool(s.asset_path or s.rendered_clip)
                if changed and has_existing_media:
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
    }
