"""Scene grouping & continuity tracker per shot batch for consistent external prompts (V102 F2, F3)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.services.longform.hashing import hash_string, normalize_text
from app.services.longform.llm_adapter import (
    LLMAdapter,
    LLMCreditError,
    LLMDailyQuotaError,
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
from app.services.longform.visual.image_presets import redact_api_secrets
from app.services.longform.visual.keyword_translator import _invoke_translation_llm

SCENE_BATCH_SIZE = 15
_SEARCH_TAG_RE = re.compile(r"\[search\s*:[^\]]*\]", re.IGNORECASE)
_VALID_TIMES = {"dawn", "day", "dusk", "night"}


class ExternalPromptGenerationError(RuntimeError):
    """Structured batch failure allowing UI to explain reason and resume from cached batches (FA1.1)."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        completed_batches: int = 0,
        total_batches: int = 0,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.completed_batches = completed_batches
        self.total_batches = total_batches


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


_FLASHBACK_CUE_RE = re.compile(
    r"\b(?:when\s+(?:you|she|he|they)\s+were\s+(?:young|little|small|a\s+(?:child|boy|girl)|[a-z]+|\d+)"
    r"|at\s+age\s+\d+|years?\s+old|year[\-\s]old|as\s+a\s+(?:child|boy|girl)"
    r"|childhood|flashback|younger\s+self|years?\s+(?:earlier|ago)|in\s+(?:her|his|your)\s+youth)\b",
    re.IGNORECASE,
)


def _resolve_flashback_variant_for_text(
    narr_text: str,
    char_id: str,
    bible: Optional[VisualBible],
) -> str:
    """When narration is a childhood/age flashback and a `variant_of == char_id` exists, promote that variant ID (FA2.6)."""
    if bible is None or not bible.characters or not _FLASHBACK_CUE_RE.search(str(narr_text or "")):
        return char_id
    variants = [c for c in bible.characters if getattr(c, "variant_of", None) == char_id]
    if not variants:
        return char_id
    low = str(narr_text or "").lower()
    word_digits = {"five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12"}
    age_tokens: set[str] = set(re.findall(r"\b\d{1,2}\b", low))
    for w, d in word_digits.items():
        if re.search(rf"\b{w}\b", low):
            age_tokens.add(d)
            age_tokens.add(w)
    if age_tokens:
        for v in variants:
            blob = f"{v.id} {v.name} {v.description} {' '.join(v.aliases or [])}".lower()
            if any(re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", blob) for tok in age_tokens):
                return v.id
    return variants[0].id


def _match_characters_by_aliases(text: str, bible: Optional[VisualBible]) -> List[str]:
    """Deterministically match bible character IDs using word boundaries (most specific aliases first)."""
    if bible is None or not bible.characters:
        return []
    low_text = f" {normalize_text(text).lower()} "
    if not low_text.strip():
        return []

    is_flashback_ctx = bool(_FLASHBACK_CUE_RE.search(low_text)) or any(
        tok in low_text
        for tok in ("years old", "when you were", "when she was", "when he was", "childhood", "flashback", "memory", "younger")
    )

    ordered_chars = list(bible.characters)
    if is_flashback_ctx:
        ordered_chars.sort(
            key=lambda c: (0 if (c.variant_of or "flashback" in c.role.lower() or "child" in c.id.lower() or "young" in c.id.lower()) else 1)
        )

    matched_ids: List[str] = []
    # Map base_id -> flashback variant IDs so flashbacks cleanly pick variant_of entries (FA1.3)
    flashback_variants = {
        c.variant_of: c.id
        for c in bible.characters
        if getattr(c, "variant_of", None)
    }

    pov_id = str(getattr(bible, "pov_character_id", None) or "").strip()
    if pov_id and re.search(r"(?<![a-z0-9])(?:you|your|yourself)(?![a-z0-9])", low_text):
        eff_pov = _resolve_flashback_variant_for_text(low_text, pov_id, bible) if is_flashback_ctx else pov_id
        if any(c.id == eff_pov for c in bible.characters) and eff_pov not in matched_ids:
            matched_ids.append(eff_pov)

    for ch in ordered_chars:
        is_fb_char = bool(ch.variant_of) or "flashback" in ch.role.lower() or any("flashback" in a.lower() for a in ch.aliases)
        if is_fb_char and not is_flashback_ctx:
            continue
        # Skip adult version of same base character if a flashback counterpart already matched
        if is_flashback_ctx and not is_fb_char and any(
            (mid == flashback_variants.get(ch.id)) or mid.startswith(ch.id) or ch.id.startswith(mid.split("_child")[0])
            for mid in matched_ids
        ):
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
    *,
    bible_sig: Optional[str] = None,
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
                if cid:
                    cid = _resolve_flashback_variant_for_text(narr, cid, bible)
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
        sh.scene_source = "ai"
        sh.scene_bible_hash = bible_sig
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

    pov_id = str(getattr(bible, "pov_character_id", None) or "").strip() if bible else ""
    chars_payload = [
        {"id": c.id, "name": c.name, "aliases": c.aliases, "role": c.role, "variant_of": c.variant_of or "", "description": c.description}
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

    eff_world_ctx = (
        f"{ch_ov.world} ({ch_ov.era})" if (ch_ov and str(ch_ov.world or "").strip()) else world
    )
    return (
        "You are a visual continuity supervisor breaking narration shots into continuous visual scenes for an image generator.\n"
        f"Active world/setting: {eff_world_ctx}\n"
        + (f"Second-person POV character ('you'/'your'): {pov_id}\n" if pov_id else "")
        + f"Chapter {chapter.chapter_index}: {chapter.chapter_title}\n"
        + f"Available characters (JSON): {json.dumps(chars_payload, ensure_ascii=False)}\n"
        + f"Available locations (JSON): {json.dumps(locs_payload, ensure_ascii=False)}\n"
        + f"Final scene state carried from previous batch: {json.dumps(prev_state or {}, ensure_ascii=False)}\n"
        + f"Current shot batch (JSON): {json.dumps(shots_payload, ensure_ascii=False)}\n\n"
        "Continuity & prompt rules (strict):\n"
        "1. Keep consecutive shots in the SAME `scene_id` (inheriting location_id, time_of_day, weather, lighting, props, character_state) unless the narration moves to a new location (e.g. exterior -> interior), changes time of day (e.g. night -> dawn), or enters/exits a flashback.\n"
        "2. Inherit carried `props` and `character_state` from the previous shot/batch across shots until the narration explicitly states an item was dropped, discarded, buried, put away permanently, or the character's condition changes.\n"
        "   - Record important objects WITH their owner in `props` (e.g. `\"Harriot's brass compass\"`, `\"Elena's carved wooden doll\"`). NEVER invent transferring a prop to a different character in `scene_en` or `props` unless narration states a handoff.\n"
        "3. Resolve pronouns (`she`, `he`, `her`, `him`, `they`, `you`, `your`) PER SHOT from surrounding narrative context and populate `characters_in_shot` ONLY with exact `id` values from Available characters (if Available characters is empty, always return `[]`). When a shot is a flashback to a younger age, pick the `variant_of`/flashback character `id` instead of the present-age `id`.\n"
        "4. Match `location_id` strictly to whether the action is indoors (`…_interior`) or outdoors (`…_exterior`) using exact IDs from Available locations.\n"
        "5. Write `scene_en` as ONE concrete visual English sentence derived from that shot's `narration` showing action, setting, and framing.\n"
        "   - Inside `scene_en`, refer to recurring characters by their exact `name` (or clear concrete subject); avoid leaving generic standalone words like 'child', 'boy', 'girl', 'you', 'she', 'he', 'woman', 'man', 'person' untethered when a locked character is present.\n"
        "6. `time_of_day` must be one of `\"dawn\"`, `\"day\"`, `\"dusk\"`, `\"night\"`, or `\"\"`.\n"
        "\nReturn ONLY strict JSON:\n"
        '{"shots": [{"shot_id": "...", "scene_id": "ch01_scene01", "location_id": "", "time_of_day": "", "weather": "", "lighting": "", "props": [], "character_state": "", "characters_in_shot": [], "scene_en": "..."}]}\n'
    )


def _apply_fallback_chapter_continuity(chapter: Chapter, target_set: set[str], bible: Optional[VisualBible]) -> None:
    """Deterministic fallback without LLM: 1 chapter = 1 scene, word-level alias match, empty location/continuity (F2, F3, FA2.1)."""
    scene_id = f"ch{chapter.chapter_index:02d}_scene01"
    for sh in chapter.shots or []:
        if sh.shot_id not in target_set:
            continue
        narr = _shot_narration_text(chapter, sh)
        if not sh.scene_id:
            sh.scene_id = scene_id
        sh.scene_en = sh.scene_en or narr or _clean_narration_for_scene(sh.search_query_en or sh.search_query or "")
        if getattr(sh, "scene_source", None) != "manual":
            sh.scene_source = "fallback"
            sh.scene_bible_hash = None
        if not sh.characters_in_shot:
            sh.characters_in_shot = _match_characters_by_aliases(narr, bible)
        if sh.location_id is None:
            sh.location_id = None
        if sh.continuity is None:
            sh.continuity = SceneContinuity()


def _classify_batch_failure(exc: Exception) -> str:
    if isinstance(exc, (LLMCreditError, LLMDailyQuotaError)):
        return "quota"
    low = str(exc or "").lower()
    if any(tok in low for tok in ("quota", "credit", "402", "429", "rate limit", "free-models-per-day", "insufficient", "limit exceeded")):
        return "quota"
    if any(tok in low for tok in ("timeout", "timed out", "read timed out")):
        return "timeout"
    if any(tok in low for tok in ("json", "empty scene batch")):
        return "invalid_json"
    return "error"


def _shot_looks_like_legacy_fallback(sh: Shot, narr: str) -> bool:
    """Return True when a shot without `scene_source` came from fallback/raw narration (empty continuity or raw text)."""
    cont = getattr(sh, "continuity", None)
    has_cont = bool(
        getattr(sh, "location_id", None)
        or (
            cont is not None
            and (
                cont.location_id
                or cont.time_of_day
                or cont.weather
                or cont.lighting
                or cont.props
                or cont.character_state
            )
        )
    )
    if not has_cont:
        return True
    if narr and str(sh.scene_en or "").strip() == narr.strip():
        return True
    return False


def assign_scenes_and_prompts_for_shots(
    manifest: ProjectManifest,
    *,
    target_shot_ids: Optional[Sequence[str]] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    cache_dir: Optional[Path] = None,
    force: bool = False,
    allow_llm: bool = True,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    raise_on_error: bool = False,
    retry_failed_only: bool = False,
) -> Dict[str, Any]:
    """Assign continuous scene_id, characters_in_shot, location_id, continuity, and scene_en per shot (F2, F3, FA1.1, FA2.1)."""
    all_shots = [sh for ch in (manifest.chapters or []) for sh in (ch.shots or [])]
    if not all_shots:
        return {
            "source": "empty",
            "ai_used": False,
            "llm_calls": 0,
            "cache_hits": 0,
            "shot_count": 0,
            "total_batches": 0,
            "completed_batches": 0,
            "batches_ok": 0,
            "batches_failed": 0,
            "fallback_shots": 0,
            "failed_shot_ids": [],
            "failure_reason": None,
            "failure_detail": "",
        }

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
        reason = "no_llm_key" if allow_llm else None
        return {
            "source": "fallback",
            "ai_used": False,
            "llm_calls": 0,
            "cache_hits": 0,
            "shot_count": len(target_set),
            "total_batches": 0,
            "completed_batches": 0,
            "batches_ok": 0,
            "batches_failed": 0,
            "fallback_shots": len(target_set),
            "failed_shot_ids": sorted(target_set) if allow_llm else [],
            "failure_reason": reason,
            "failure_detail": str(readiness.get("message") or "") if allow_llm else "",
        }

    cache_path = _scene_cache_file(cache_dir)
    cache = _load_scene_cache(cache_path)
    llm_calls = 0
    cache_hits = 0
    batches_ok = 0
    batches_failed = 0
    failed_shot_ids: List[str] = []
    failure_reasons: List[str] = []
    first_failure_detail = ""
    quota_tripped_reason: Optional[str] = None
    used_source = "ai"

    # Pre-plan active chapters & total batches for accurate progress & ETA reporting (FA1.1, FA2.1)
    planned_chapters: List[Tuple[Chapter, List[Tuple[Shot, str]], List[List[Tuple[int, Shot, str]]]]] = []
    total_batches = 0
    for ch in manifest.chapters or []:
        ch_shots_sorted = sorted(ch.shots or [], key=lambda s: (s.shot_index, s.shot_id))
        ch_narrs: List[Tuple[Shot, str]] = [(sh, _shot_narration_text(ch, sh)) for sh in ch_shots_sorted]
        if not ch_narrs:
            continue
        needs_compute = force or any(
            (sh.shot_id in target_set)
            and (
                not sh.scene_en
                or not sh.scene_id
                or getattr(sh, "scene_source", None) == "fallback"
                or (
                    not getattr(sh, "scene_source", None)
                    and _shot_looks_like_legacy_fallback(sh, narr)
                )
                or (
                    not retry_failed_only
                    and getattr(sh, "scene_source", None) == "ai"
                    and bool(getattr(sh, "scene_bible_hash", None))
                    and sh.scene_bible_hash != bible_sig
                )
            )
            for sh, narr in ch_narrs
        )
        if not needs_compute:
            continue
        ch_batches: List[List[Tuple[int, Shot, str]]] = []
        for start in range(0, len(ch_narrs), SCENE_BATCH_SIZE):
            slice_items = ch_narrs[start:start + SCENE_BATCH_SIZE]
            ch_batches.append([(start + idx, sh, narr) for idx, (sh, narr) in enumerate(slice_items)])
        total_batches += len(ch_batches)
        planned_chapters.append((ch, ch_narrs, ch_batches))

    completed_batches = 0
    live_durations: List[float] = []
    global_shot_offset = 0

    for ch, ch_narrs, ch_batches in planned_chapters:
        prev_state: Dict[str, Any] = {}
        for batch_indexed in ch_batches:
            batch_tuples = [(ch, sh, narr) for _gi, sh, narr in batch_indexed]
            batch_num = completed_batches + 1
            shot_start = global_shot_offset + batch_indexed[0][0] + 1
            shot_end = global_shot_offset + batch_indexed[-1][0] + 1

            sig_parts = [
                f"{sh.shot_id}:{sh.shot_hash or ''}:{hash_string(narr)}"
                for _gi, sh, narr in batch_indexed
            ]
            prev_sig = hash_string(json.dumps(prev_state, sort_keys=True))
            batch_key = hash_string(f"v102_f2_batch|ch{ch.chapter_index}|{bible_sig}|{prev_sig}|{'|'.join(sig_parts)}")

            if callable(progress_callback):
                avg_sec = (sum(live_durations) / len(live_durations)) if live_durations else 12.0
                rem_batches = max(0, total_batches - completed_batches)
                progress_callback({
                    "batch_index": batch_num,
                    "total_batches": total_batches,
                    "shot_start": shot_start,
                    "shot_end": shot_end,
                    "total_shots": len(target_set),
                    "eta_seconds": int(round(avg_sec * rem_batches)),
                    "stage": "starting",
                })

            llm_items: Optional[List[Dict[str, Any]]] = None
            all_ai_ready_in_batch = all(
                getattr(sh, "scene_source", None) == "ai"
                and bool(sh.scene_id)
                and bool(sh.scene_en)
                for _gi, sh, _narr in batch_indexed
            )

            if not force and isinstance(cache.get(batch_key), dict) and isinstance(cache[batch_key].get("items"), list):
                llm_items = cache[batch_key]["items"]
                cache_hits += 1
                batches_ok += 1
                logger.info(f"scene batch {batch_num}/{total_batches} shots {shot_start}–{shot_end}: cache hit")
            elif retry_failed_only and not force and all_ai_ready_in_batch:
                cache_hits += 1
                batches_ok += 1
                logger.info(f"scene batch {batch_num}/{total_batches} shots {shot_start}–{shot_end}: cache hit")
                last_sh = batch_indexed[-1][1]
                last_cont = getattr(last_sh, "continuity", None)
                prev_state = {
                    "scene_id": str(last_sh.scene_id or ""),
                    "location_id": last_sh.location_id or (last_cont.location_id if last_cont else None),
                    "time_of_day": str(last_cont.time_of_day if last_cont else ""),
                    "weather": str(last_cont.weather if last_cont else ""),
                    "lighting": str(last_cont.lighting if last_cont else ""),
                    "props": list(last_cont.props if last_cont else []),
                    "character_state": str(last_cont.character_state if last_cont else ""),
                }
                completed_batches += 1
                if callable(progress_callback):
                    progress_callback({
                        "batch_index": completed_batches,
                        "total_batches": total_batches,
                        "shot_start": shot_start,
                        "shot_end": shot_end,
                        "total_shots": len(target_set),
                        "eta_seconds": 0,
                        "stage": "done",
                    })
                continue
            elif quota_tripped_reason is not None and not raise_on_error:
                used_source = "fallback"
                batches_failed += 1
                failure_reasons.append("quota")
                b_ids = [sh.shot_id for _gi, sh, _narr in batch_indexed if sh.shot_id in target_set]
                failed_shot_ids.extend(b_ids)
                logger.warning(f"scene batch {batch_num}/{total_batches} shots {shot_start}–{shot_end}: failed: {quota_tripped_reason}")
                _apply_fallback_chapter_continuity(ch, {sh.shot_id for _gi, sh, _narr in batch_indexed}, bible)
                prev_state = {}
                completed_batches += 1
                if callable(progress_callback):
                    progress_callback({
                        "batch_index": completed_batches,
                        "total_batches": total_batches,
                        "shot_start": shot_start,
                        "shot_end": shot_end,
                        "total_shots": len(target_set),
                        "eta_seconds": 0,
                        "stage": "done",
                    })
                continue
            else:
                prompt = _build_scene_batch_prompt(manifest, ch, batch_indexed, ch_narrs, prev_state)
                parsed_items: List[Dict[str, Any]] = []
                last_exc: Optional[Exception] = None
                t_b0 = time.monotonic()
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
                    except (LLMCreditError, LLMDailyQuotaError, LLMModelNotFoundError) as exc:
                        last_exc = exc
                        if raise_on_error:
                            reason = _classify_batch_failure(exc)
                            raise ExternalPromptGenerationError(
                                reason,
                                str(exc),
                                completed_batches=batches_ok,
                                total_batches=total_batches,
                            ) from exc
                        break
                    except Exception as exc:
                        last_exc = exc
                        safe_err = redact_api_secrets(str(exc))[:140]
                        logger.warning(f"Scene batch ch{ch.chapter_index} attempt {attempt + 1} failed: {safe_err}")
                        if _classify_batch_failure(exc) == "quota":
                            break

                if parsed_items:
                    live_durations.append(max(1.0, time.monotonic() - t_b0))
                    llm_items = parsed_items
                    # Persist batch immediately so mid-run failures preserve completed batches in cache (FA1.1)
                    cache[batch_key] = {"items": parsed_items}
                    _save_scene_cache(cache_path, cache)
                    batches_ok += 1
                    logger.info(f"scene batch {batch_num}/{total_batches} shots {shot_start}–{shot_end}: ok")
                else:
                    err_obj = last_exc or ValueError("Empty scene batch JSON from LLM")
                    reason = _classify_batch_failure(err_obj)
                    safe_detail = redact_api_secrets(str(err_obj)).replace("\n", " ").strip()[:160]
                    if raise_on_error:
                        raise ExternalPromptGenerationError(
                            reason,
                            safe_detail or reason,
                            completed_batches=batches_ok,
                            total_batches=total_batches,
                        )
                    used_source = "fallback"
                    batches_failed += 1
                    failure_reasons.append(reason)
                    if not first_failure_detail:
                        first_failure_detail = safe_detail or reason
                    if reason == "quota":
                        quota_tripped_reason = safe_detail or reason
                    b_ids = [sh.shot_id for _gi, sh, _narr in batch_indexed if sh.shot_id in target_set]
                    failed_shot_ids.extend(b_ids)
                    logger.warning(f"scene batch {batch_num}/{total_batches} shots {shot_start}–{shot_end}: failed: {safe_detail or reason}")
                    _apply_fallback_chapter_continuity(ch, {sh.shot_id for _gi, sh, _narr in batch_indexed}, bible)
                    prev_state = {}
                    completed_batches += 1
                    if callable(progress_callback):
                        avg_sec = (sum(live_durations) / len(live_durations)) if live_durations else 10.0
                        rem_batches = max(0, total_batches - completed_batches)
                        progress_callback({
                            "batch_index": completed_batches,
                            "total_batches": total_batches,
                            "shot_start": shot_start,
                            "shot_end": shot_end,
                            "total_shots": len(target_set),
                            "eta_seconds": int(round(avg_sec * rem_batches)),
                            "stage": "done",
                        })
                    continue

            prev_state = _apply_batch_continuity(batch_tuples, llm_items, bible, prev_state, bible_sig=bible_sig)
            completed_batches += 1

            if callable(progress_callback):
                avg_sec = (sum(live_durations) / len(live_durations)) if live_durations else 10.0
                rem_batches = max(0, total_batches - completed_batches)
                progress_callback({
                    "batch_index": completed_batches,
                    "total_batches": total_batches,
                    "shot_start": shot_start,
                    "shot_end": shot_end,
                    "total_shots": len(target_set),
                    "eta_seconds": int(round(avg_sec * rem_batches)),
                    "stage": "done",
                })

        global_shot_offset += len(ch_narrs)

    target_objs = [s for s in all_shots if s.shot_id in target_set]
    fallback_count = len(failed_shot_ids) if planned_chapters else sum(
        1 for s in target_objs if getattr(s, "scene_source", None) == "fallback"
    )
    if total_batches > 0:
        ai_used = batches_ok > 0
    else:
        ai_used = bool(target_objs) and all(getattr(s, "scene_source", None) == "ai" for s in target_objs)

    return {
        "source": used_source,
        "ai_used": ai_used,
        "llm_calls": llm_calls,
        "cache_hits": cache_hits,
        "shot_count": len(target_set),
        "total_batches": total_batches,
        "completed_batches": completed_batches,
        "batches_ok": batches_ok,
        "batches_failed": batches_failed,
        "fallback_shots": fallback_count,
        "failed_shot_ids": failed_shot_ids,
        "failure_reason": failure_reasons[0] if failure_reasons else None,
        "failure_detail": first_failure_detail,
    }
