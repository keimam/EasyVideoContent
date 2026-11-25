"""Scene grouping & continuity tracker per shot batch for consistent external prompts (V102 F2, F3)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.services.longform.hashing import hash_string, normalize_text
from app.services.longform.llm_adapter import (
    LLMAdapter,
    LLMCreditError,
    LLMModelNotFoundError,
    get_llm_readiness,
)
from app.services.longform.schema import (
    Chapter,
    ProjectManifest,
    SceneContinuity,
    Shot,
    VisualBible,
)
from app.services.longform.visual.ai_keywords import get_shot_narration_slice
from app.services.longform.visual.cache import get_global_cache_dir
from app.services.longform.visual.keyword_translator import _invoke_translation_llm

SCENE_BATCH_SIZE = 15
_SEARCH_TAG_RE = re.compile(r"\[search\s*:[^\]]*\]", re.IGNORECASE)
_VALID_TIMES = {"dawn", "day", "dusk", "night"}


def _scene_cache_file(cache_dir: Optional[Path] = None) -> Path:
    root = Path(cache_dir) if cache_dir else get_global_cache_dir()
    path = root / "external_scene_prompts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_scene_cache(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_scene_cache(path: Path, cache: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to persist external scene cache: {exc}")


def _clean_narration_for_scene(raw_text: str) -> str:
    txt = _SEARCH_TAG_RE.sub("", str(raw_text or ""))
    return normalize_text(txt)


def _shot_narration_text(ch: Chapter, sh: Shot) -> str:
    full_narr, _p_idx, _p_tot, focus = get_shot_narration_slice(ch, sh, ch.shots)
    text = focus or full_narr
    if not text:
        text = str(sh.visual_prompt or sh.search_query or "").strip()
    return _clean_narration_for_scene(text)


def _match_characters_by_aliases(text: str, bible: Optional[VisualBible]) -> List[str]:
    """Deterministically match bible character IDs using word boundaries (most specific aliases first)."""
    if bible is None or not bible.characters:
        return []
    low_text = f" {normalize_text(text).lower()} "
    if not low_text.strip():
        return []

    is_flashback_ctx = any(
        tok in low_text
        for tok in ("years old", "when you were", "when she was", "when he was", "childhood", "flashback", "memory", "younger")
    )

    matched_ids: List[str] = []
    # Sort characters so flashback-specific entries are evaluated cleanly when flashback markers exist
    ordered_chars = list(bible.characters)
    if is_flashback_ctx:
        ordered_chars.sort(
            key=lambda c: (0 if ("flashback" in c.role.lower() or "child" in c.id.lower() or "young" in c.id.lower()) else 1)
        )

    for ch in ordered_chars:
        is_fb_char = "flashback" in ch.role.lower() or any("flashback" in a.lower() for a in ch.aliases)
        if is_fb_char and not is_flashback_ctx:
            continue
        # Skip adult version of same base character if a flashback counterpart already matched
        if is_flashback_ctx and not is_fb_char and any(mid.startswith(ch.id) or ch.id.startswith(mid.split("_child")[0]) for mid in matched_ids):
            continue

        terms = [ch.name] + [
            re.sub(r"\([^)]*\)", "", a).strip()
            for a in (ch.aliases or [])
        ]
        terms = [t for t in terms if t]
        # Prefer longer phrase matches first
        terms.sort(key=len, reverse=True)
        hit = False
        for term in terms:
            pat = r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])"
            if re.search(pat, low_text):
                hit = True
                break
        if hit and ch.id not in matched_ids:
            matched_ids.append(ch.id)
    return matched_ids


def _extract_batch_shots_json(raw_text: str) -> List[Dict[str, Any]]:
    """Parse batched scene/prompt JSON response, tolerating code fences, <think>, and dict/list wrappers (F2)."""
    text = str(raw_text or "").strip()
    if not text:
        return []
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()

    candidates: List[str] = [text]
    m_obj = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if m_obj:
        candidates.append(m_obj.group(1))

    for cand in candidates:
        for attempt in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
            try:
                parsed = json.loads(attempt)
            except Exception:
                continue
            if isinstance(parsed, list):
                return [x for x in parsed if isinstance(x, dict)]
            if isinstance(parsed, dict):
                for key in ("shots", "items", "results", "data"):
                    inner = parsed.get(key)
                    if isinstance(inner, list):
                        return [x for x in inner if isinstance(x, dict)]
                    if isinstance(inner, dict):
                        out: List[Dict[str, Any]] = []
                        for sid, val in inner.items():
                            if isinstance(val, dict):
                                out.append({"shot_id": sid, **val})
                        return out
                # Dict directly keyed by shot_id
                direct: List[Dict[str, Any]] = []
                for sid, val in parsed.items():
                    if isinstance(val, dict) and "shot" in sid.lower():
                        direct.append({"shot_id": sid, **val})
                if direct:
                    return direct
    return []


def _norm_time_of_day(val: Any) -> str:
    low = str(val or "").strip().lower()
    if low in _VALID_TIMES:
        return low
    for t_opt in ("dawn", "dusk", "night", "day"):
        if t_opt in low:
            return t_opt
    return low


def _apply_batch_continuity(
    batch_shots: List[Tuple[Chapter, Shot, str]],
    llm_items: List[Dict[str, Any]],
    bible: Optional[VisualBible],
    prev_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Map LLM batch outputs back onto Shot objects and enforce continuity state across shots & batch edges."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for idx, item in enumerate(llm_items):
        sid = str(item.get("shot_id") or item.get("id") or "").strip()
        if sid:
            by_id[sid.lower()] = item
        elif idx < len(batch_shots):
            by_id[batch_shots[idx][1].shot_id.lower()] = item

    valid_char_ids = {c.id for c in (bible.characters if bible else [])}
    valid_loc_ids = {loc.id for loc in (bible.locations if bible else [])}

    cur_scene_id = str(prev_state.get("scene_id") or "").strip()
    cur_loc = prev_state.get("location_id")
    cur_time = str(prev_state.get("time_of_day") or "")
    cur_weather = str(prev_state.get("weather") or "")
    cur_lighting = str(prev_state.get("lighting") or "")
    cur_props = list(prev_state.get("props") or [])
    cur_cstate = str(prev_state.get("character_state") or "")

    for ch, sh, narr in batch_shots:
        raw = by_id.get(sh.shot_id.lower()) or {}
        raw_sid = str(raw.get("scene_id") or "").strip()
        if not raw_sid:
            raw_sid = cur_scene_id or f"ch{ch.chapter_index:02d}_scene01"

        same_scene = bool(cur_scene_id and raw_sid == cur_scene_id)

        # Location resolution
        raw_loc_present = "location_id" in raw
        raw_loc = str(raw.get("location_id") or "").strip() or None
        if raw_loc and valid_loc_ids and raw_loc not in valid_loc_ids:
            # Match by lowercase name if model emitted location name instead of id
            matched_loc = next((loc.id for loc in (bible.locations if bible else []) if loc.name.lower() == raw_loc.lower()), None)
            raw_loc = matched_loc
        if same_scene and not raw_loc_present:
            eff_loc = cur_loc
        elif same_scene and raw_loc is None and not raw.get("location_changed"):
            eff_loc = raw_loc if raw_loc_present and raw.get("clear_location") else (raw_loc or cur_loc)
        else:
            eff_loc = raw_loc

        # Time / weather / lighting: stay inherited inside same scene, do NOT auto-carry old scene env across scene transitions
        raw_time = _norm_time_of_day(raw.get("time_of_day")) if "time_of_day" in raw else ""
        eff_time = raw_time or (cur_time if same_scene else "")

        raw_weather = str(raw.get("weather") or "").strip() if "weather" in raw else ""
        eff_weather = raw_weather if "weather" in raw and (raw_weather or not same_scene) else (cur_weather if same_scene else raw_weather)

        raw_lighting = str(raw.get("lighting") or "").strip() if "lighting" in raw else ""
        eff_lighting = raw_lighting if "lighting" in raw and (raw_lighting or not same_scene) else (cur_lighting if same_scene else raw_lighting)

        # Props continuity: carried across shots until explicitly removed/cleared
        if "props" in raw and isinstance(raw.get("props"), list):
            eff_props = [str(p).strip() for p in raw["props"] if str(p or "").strip()]
        elif "props" in raw and isinstance(raw.get("props"), str):
            eff_props = [x.strip() for x in re.split(r"[,;\n]+", raw["props"]) if x.strip()]
        else:
            eff_props = list(cur_props)

        for added in (raw.get("props_added") or []):
            s_add = str(added or "").strip()
            if s_add and s_add.lower() not in {x.lower() for x in eff_props}:
                eff_props.append(s_add)
        for removed in (raw.get("props_removed") or []):
            r_low = str(removed or "").strip().lower()
            if r_low:
                eff_props = [p for p in eff_props if r_low not in p.lower()]

        # Character state continuity
        if "character_state" in raw:
            cs_val = raw.get("character_state")
            if isinstance(cs_val, list):
                eff_cstate = ", ".join(str(x).strip() for x in cs_val if str(x or "").strip())
            else:
                eff_cstate = str(cs_val or "").strip()
        else:
            eff_cstate = cur_cstate

        # Characters in shot
        raw_chars = raw.get("characters_in_shot")
        if isinstance(raw_chars, list):
            eff_chars: List[str] = []
            for item in raw_chars:
                tok = str(item or "").strip()
                if not tok:
                    continue
                if tok in valid_char_ids:
                    cid = tok
                elif bible:
                    cid = next((c.id for c in bible.characters if c.name.lower() == tok.lower()), "")
                else:
                    cid = ""
                if cid and cid not in eff_chars:
                    eff_chars.append(cid)
        else:
            eff_chars = _match_characters_by_aliases(narr, bible)

        # Scene English description
        scene_en = _clean_narration_for_scene(raw.get("scene_en") or "")
        if not scene_en:
            scene_en = narr or _clean_narration_for_scene(sh.visual_prompt or sh.search_query or "")

        sh.scene_id = raw_sid
        sh.scene_en = scene_en
        sh.characters_in_shot = eff_chars
        sh.location_id = eff_loc
        sh.continuity = SceneContinuity(
            location_id=eff_loc,
            time_of_day=eff_time,
            weather=eff_weather,
            lighting=eff_lighting,
            props=eff_props,
            character_state=eff_cstate,
        )

        cur_scene_id = raw_sid
        cur_loc = eff_loc
        cur_time = eff_time
        cur_weather = eff_weather
        cur_lighting = eff_lighting
        cur_props = list(eff_props)
        cur_cstate = eff_cstate

    return {
        "scene_id": cur_scene_id,
        "location_id": cur_loc,
        "time_of_day": cur_time,
        "weather": cur_weather,
        "lighting": cur_lighting,
        "props": list(cur_props),
        "character_state": cur_cstate,
    }


def _build_scene_batch_prompt(
    manifest: ProjectManifest,
    chapter: Chapter,
    batch: List[Tuple[int, Shot, str]],
    chapter_shots: List[Tuple[Shot, str]],
    prev_state: Dict[str, Any],
) -> str:
    bible = manifest.visual_bible
    world = bible.world if bible else str(manifest.topic or manifest.title or "")
    ch_ov = None
    if bible and bible.chapter_overrides:
        ch_ov = next((ov for ov in bible.chapter_overrides if ov.chapter_index == chapter.chapter_index), None)

    chars_payload = [
        {"id": c.id, "name": c.name, "aliases": c.aliases, "role": c.role, "description": c.description}
        for c in (bible.characters if bible else [])
    ]
    locs_payload = [
        {"id": loc.id, "name": loc.name, "description": loc.description}
        for loc in (bible.locations if bible else [])
    ]

    shots_payload: List[Dict[str, Any]] = []
    for global_idx, sh, narr in batch:
        prev_narr = chapter_shots[global_idx - 1][1] if global_idx > 0 else ""
        next_narr = chapter_shots[global_idx + 1][1] if (global_idx + 1) < len(chapter_shots) else ""
        shots_payload.append(
            {
                "shot_id": sh.shot_id,
                "narration": narr,
                "prev_narration": prev_narr[:180],
                "next_narration": next_narr[:180],
            }
        )

    return (
        "You are a visual continuity supervisor breaking narration shots into continuous visual scenes for an image generator.\n"
        f"Project world: {world}\n"
        + (f"Chapter setting override: world={ch_ov.world}, era={ch_ov.era}\n" if ch_ov else "")
        + f"Chapter {chapter.chapter_index}: {chapter.chapter_title}\n"
        + f"Available characters (JSON): {json.dumps(chars_payload, ensure_ascii=False)}\n"
        + f"Available locations (JSON): {json.dumps(locs_payload, ensure_ascii=False)}\n"
        + f"Final scene state carried from previous batch: {json.dumps(prev_state or {}, ensure_ascii=False)}\n"
        + f"Current shot batch (JSON): {json.dumps(shots_payload, ensure_ascii=False)}\n\n"
        "Continuity & prompt rules (strict):\n"
        "1. Keep consecutive shots in the SAME `scene_id` (inheriting location_id, time_of_day, weather, lighting, props, character_state) unless the narration moves to a new location, changes time of day (e.g. night -> dawn), or enters/exits a flashback.\n"
        "2. Inherit carried `props` and `character_state` from the previous shot/batch across shots until the narration explicitly states an item was dropped, discarded, buried, put away permanently, or the character's condition changes.\n"
        "3. Populate `characters_in_shot` ONLY with exact `id` values from Available characters (if Available characters is empty, always return `[]`). When a shot is a flashback to a younger age, pick the flashback character `id` instead of the present-age `id`.\n"
        "4. Write `scene_en` as ONE concrete visual English sentence derived from that shot's `narration` showing action, setting, and framing.\n"
        "   - Inside `scene_en`, refer to recurring characters by their exact `name` (or clear concrete subject); avoid leaving generic standalone words like 'child', 'boy', 'girl', 'you', 'she', 'he', 'woman', 'man', 'person' untethered when a locked character is present.\n"
        "5. `time_of_day` must be one of `\"dawn\"`, `\"day\"`, `\"dusk\"`, `\"night\"`, or `\"\"`.\n"
        "\nReturn ONLY strict JSON:\n"
        '{"shots": [{"shot_id": "...", "scene_id": "ch01_scene01", "location_id": "", "time_of_day": "", "weather": "", "lighting": "", "props": [], "character_state": "", "characters_in_shot": [], "scene_en": "..."}]}\n'
    )


def _apply_fallback_chapter_continuity(chapter: Chapter, target_set: set[str], bible: Optional[VisualBible]) -> None:
    """Deterministic fallback without LLM: 1 chapter = 1 scene, word-level alias match, empty location/continuity (F2, F3)."""
    scene_id = f"ch{chapter.chapter_index:02d}_scene01"
    for sh in chapter.shots or []:
        if sh.shot_id not in target_set:
            continue
        narr = _shot_narration_text(chapter, sh)
        if not sh.scene_id:
            sh.scene_id = scene_id
        sh.scene_en = sh.scene_en or narr or _clean_narration_for_scene(sh.search_query_en or sh.search_query or "")
        if not sh.characters_in_shot:
            sh.characters_in_shot = _match_characters_by_aliases(narr, bible)
        if sh.location_id is None:
            sh.location_id = None
        if sh.continuity is None:
            sh.continuity = SceneContinuity()


def assign_scenes_and_prompts_for_shots(
    manifest: ProjectManifest,
    *,
    target_shot_ids: Optional[Sequence[str]] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    cache_dir: Optional[Path] = None,
    force: bool = False,
    allow_llm: bool = True,
) -> Dict[str, Any]:
    """Assign continuous scene_id, characters_in_shot, location_id, continuity, and scene_en per shot (F2, F3)."""
    all_shots = [sh for ch in (manifest.chapters or []) for sh in (ch.shots or [])]
    if not all_shots:
        return {"source": "empty", "llm_calls": 0, "cache_hits": 0, "shot_count": 0}

    target_set = (
        {str(s).strip() for s in target_shot_ids if str(s).strip()}
        if target_shot_ids is not None
        else {s.shot_id for s in all_shots}
    )

    bible = manifest.visual_bible
    bible_sig = hash_string(json.dumps(bible.model_dump(mode="json") if bible else {}, sort_keys=True))

    readiness = get_llm_readiness()
    llm_ready = allow_llm and (
        (llm_adapter is not None)
        or (bool(readiness.get("configured")) and not bool(readiness.get("is_invalid")))
    )

    if not llm_ready:
        for ch in manifest.chapters or []:
            _apply_fallback_chapter_continuity(ch, target_set, bible)
        return {"source": "fallback", "llm_calls": 0, "cache_hits": 0, "shot_count": len(target_set)}

    cache_path = _scene_cache_file(cache_dir)
    cache = _load_scene_cache(cache_path)
    llm_calls = 0
    cache_hits = 0
    cache_dirty = False
    used_source = "ai"

    for ch in manifest.chapters or []:
        ch_shots_sorted = sorted(ch.shots or [], key=lambda s: (s.shot_index, s.shot_id))
        ch_narrs: List[Tuple[Shot, str]] = [(sh, _shot_narration_text(ch, sh)) for sh in ch_shots_sorted]
        if not ch_narrs:
            continue

        # Even if user exports a subset, walk full chapter sequentially in batches of 15 so continuity never breaks
        needs_compute = force or any(
            (sh.shot_id in target_set) and (not sh.scene_en or not sh.scene_id)
            for sh, _ in ch_narrs
        )
        if not needs_compute:
            continue

        prev_state: Dict[str, Any] = {}
        for start in range(0, len(ch_narrs), SCENE_BATCH_SIZE):
            slice_items = ch_narrs[start:start + SCENE_BATCH_SIZE]
            batch_indexed = [(start + idx, sh, narr) for idx, (sh, narr) in enumerate(slice_items)]
            batch_tuples = [(ch, sh, narr) for sh, narr in slice_items]

            sig_parts = [
                f"{sh.shot_id}:{sh.shot_hash or ''}:{hash_string(narr)}"
                for sh, narr in slice_items
            ]
            prev_sig = hash_string(json.dumps(prev_state, sort_keys=True))
            batch_key = hash_string(f"v102_f2_batch|ch{ch.chapter_index}|{bible_sig}|{prev_sig}|{'|'.join(sig_parts)}")

            llm_items: Optional[List[Dict[str, Any]]] = None
            if not force and isinstance(cache.get(batch_key), dict) and isinstance(cache[batch_key].get("items"), list):
                llm_items = cache[batch_key]["items"]
                cache_hits += 1
            else:
                prompt = _build_scene_batch_prompt(manifest, ch, batch_indexed, ch_narrs, prev_state)
                parsed_items: List[Dict[str, Any]] = []
                last_exc: Optional[Exception] = None
                for attempt in range(2):
                    try:
                        llm_calls += 1
                        raw_txt, _prov, _mdl = _invoke_translation_llm(
                            prompt,
                            llm_adapter=llm_adapter,
                            max_tokens=2048,
                        )
                        parsed_items = _extract_batch_shots_json(raw_txt)
                        if parsed_items:
                            break
                        last_exc = ValueError("Empty scene batch JSON from LLM")
                    except (LLMCreditError, LLMModelNotFoundError):
                        raise
                    except Exception as exc:
                        last_exc = exc
                        logger.warning(f"Scene batch ch{ch.chapter_index} attempt {attempt + 1} failed: {exc}")

                if parsed_items:
                    llm_items = parsed_items
                    cache[batch_key] = {"items": parsed_items}
                    cache_dirty = True
                else:
                    logger.warning(f"Scene batch ch{ch.chapter_index} falling back deterministically: {last_exc}")
                    used_source = "fallback"
                    _apply_fallback_chapter_continuity(ch, {sh.shot_id for sh, _ in slice_items}, bible)
                    prev_state = {}
                    continue

            prev_state = _apply_batch_continuity(batch_tuples, llm_items, bible, prev_state)

    if cache_dirty:
        _save_scene_cache(cache_path, cache)

    return {
        "source": used_source,
        "llm_calls": llm_calls,
        "cache_hits": cache_hits,
        "shot_count": len(target_set),
    }
