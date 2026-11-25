"""Project Visual Bible builder for consistent external image generation (V102 F1)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    ProjectManifest,
    VisualBible,
    VisualBibleChapterOverride,
    VisualBibleCharacter,
    VisualBibleLocation,
)
from app.services.longform.visual.cache import get_global_cache_dir
from app.services.longform.visual.keyword_translator import _invoke_translation_llm

STYLE_MODE_OPTIONS = ("realistic", "illustration", "painting", "custom")
STYLE_MODE_BASE_TEXTS: Dict[str, str] = {
    "realistic": "photorealistic cinematic film still, 35mm, natural light, realistic skin and fabric textures, period-accurate costumes and architecture",
    "illustration": "detailed storybook illustration",
    "painting": "oil painting, visible brushwork",
    "custom": "",
}
REALISTIC_AVOID_TERMS = (
    "illustration",
    "painting",
    "drawing",
    "cartoon",
    "anime",
    "CGI",
    "3D render",
)
ARTISTIC_CONFLICT_AVOID_TOKENS = (
    "illustration",
    "drawing",
    "painting",
    "digital painting",
    "watercolor",
    "sketch",
)


def normalize_style_mode(value: Any) -> str:
    low = str(value or "realistic").strip().lower()
    mapping = {
        "realistic": "realistic",
        "realistis": "realistic",
        "realistis sinematik": "realistic",
        "cinematic realistic": "realistic",
        "illustration": "illustration",
        "ilustrasi": "illustration",
        "painting": "painting",
        "lukisan": "painting",
        "custom": "custom",
        "kustom": "custom",
    }
    return mapping.get(low, "custom" if low else "realistic")


def reconcile_avoid_with_style(avoid_text: str, style_mode: str) -> str:
    """Keep Avoid terms strictly consistent with chosen style_mode (FA1.2)."""
    mode = normalize_style_mode(style_mode)
    raw_items = [x.strip() for x in re.split(r"[,;\n]+", str(avoid_text or "")) if x.strip()]

    if mode in ("illustration", "painting"):
        filtered: List[str] = []
        for item in raw_items:
            low = item.lower()
            if any(tok in low for tok in ARTISTIC_CONFLICT_AVOID_TOKENS):
                continue
            filtered.append(item)
        raw_items = filtered
    elif mode == "realistic":
        existing_low = {x.lower() for x in raw_items}
        for req_term in REALISTIC_AVOID_TERMS:
            if req_term.lower() not in existing_low:
                raw_items.append(req_term)
                existing_low.add(req_term.lower())

    dedup: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        low = item.lower()
        if low not in seen:
            seen.add(low)
            dedup.append(item)
    return ", ".join(dedup)


def _visual_bible_cache_file(cache_dir: Optional[Path] = None) -> Path:
    root = Path(cache_dir) if cache_dir else get_global_cache_dir()
    path = root / "visual_bible_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_cache(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Failed to persist visual bible cache: {exc}")


def collect_project_script_text(manifest: ProjectManifest, *, max_chars: int = 24000) -> str:
    """Collect full narration script per chapter cleanly from manifest."""
    sections: List[str] = []
    for ch in manifest.chapters or []:
        lines: List[str] = []
        for grp in ch.tts_groups or []:
            segs = grp.segments or []
            if segs:
                seg_text = " ".join(s.narration_text.strip() for s in segs if s.narration_text and s.narration_text.strip())
                if seg_text:
                    lines.append(seg_text)
            elif grp.paragraph_text and grp.paragraph_text.strip():
                lines.append(grp.paragraph_text.strip())
        if not lines:
            for sh in ch.shots or []:
                vp = str(sh.visual_prompt or sh.search_query or "").strip()
                if vp:
                    lines.append(vp)
        if lines:
            header = f"[Chapter {ch.chapter_index}: {ch.chapter_title}]"
            sections.append(header + "\n" + "\n".join(lines))
    joined = "\n\n".join(sections).strip()
    if len(joined) <= max_chars:
        return joined
    head_len = int(max_chars * 0.7)
    tail_len = max_chars - head_len - 40
    return joined[:head_len] + "\n...[middle excerpt trimmed]...\n" + joined[-tail_len:]


def _preset_style_from_manifest(manifest: ProjectManifest) -> str:
    snap = manifest.preset_snapshot or {}
    if isinstance(snap, dict):
        vis = snap.get("visual")
        if isinstance(vis, dict) and str(vis.get("style") or "").strip():
            return str(vis.get("style")).strip()
        if str(snap.get("style") or "").strip():
            return str(snap.get("style")).strip()
    return ""


def build_template_visual_bible(
    manifest: ProjectManifest,
    *,
    style_mode: Optional[str] = None,
) -> VisualBible:
    """Build an editable template VisualBible when LLM is unavailable (F1, FA1.2)."""
    topic = str(manifest.topic or manifest.title or "").strip()
    anchors = [str(a).strip() for a in (manifest.visual_anchor_terms or []) if str(a).strip()]
    avoids = [str(a).strip() for a in (manifest.visual_avoid_terms or []) if str(a).strip()]

    world_parts: List[str] = []
    if topic:
        world_parts.append(topic)
    if manifest.visual_summary:
        world_parts.append(str(manifest.visual_summary).strip())
    elif anchors:
        world_parts.append(", ".join(anchors[:6]))
    world = " — ".join(world_parts) if world_parts else "Documentary scene consistent with project narration"

    preset_style = _preset_style_from_manifest(manifest)
    if style_mode is not None:
        eff_mode = normalize_style_mode(style_mode)
    elif preset_style:
        low_ps = preset_style.lower()
        if "paint" in low_ps:
            eff_mode = "painting"
        elif "illustrat" in low_ps:
            eff_mode = "illustration"
        elif "photo" in low_ps or "realistic" in low_ps or "35mm" in low_ps:
            eff_mode = "realistic"
        else:
            eff_mode = "custom"
    else:
        eff_mode = "realistic"

    if style_mode is not None:
        style = STYLE_MODE_BASE_TEXTS.get(eff_mode) or preset_style or STYLE_MODE_BASE_TEXTS["realistic"]
    else:
        style = preset_style or STYLE_MODE_BASE_TEXTS.get(eff_mode) or STYLE_MODE_BASE_TEXTS["realistic"]

    palette_lighting = "cohesive atmospheric color tones and mood without fixed time of day"

    avoid_pool: List[str] = list(avoids)
    for item in ("text", "watermark", "logo", "UI overlay", "collage"):
        if item.lower() not in {x.lower() for x in avoid_pool}:
            avoid_pool.append(item)
    avoid_str = reconcile_avoid_with_style(", ".join(avoid_pool), eff_mode)

    return VisualBible(
        world=world,
        style_mode=eff_mode,
        style=style,
        palette_lighting=palette_lighting,
        characters=[],
        locations=[],
        chapter_overrides=[],
        avoid=avoid_str,
        source="template",
    )


def _slugify_id(raw: str, prefix: str = "item", index: int = 1) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    return cleaned or f"{prefix}_{index}"


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    """Extract and recover a JSON dict from LLM output (F1 recovery)."""
    text = str(raw_text or "").strip()
    if not text:
        return {}
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()

    candidates: List[str] = [text]
    m_brace = re.search(r"\{[\s\S]*\}", text)
    if m_brace:
        candidates.append(m_brace.group(0))

    for cand in candidates:
        # Try direct load and trailing-comma cleanup
        for attempt_txt in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):
            try:
                parsed = json.loads(attempt_txt)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue

    # Partial salvage when output was cut off mid-object
    if "{" in text:
        tail = text[text.find("{"):]
        for closing in ("}", "]}", '"}', '"]}'):
            try:
                fixed = re.sub(r",\s*$", "", tail.rstrip()) + closing
                fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
                parsed = json.loads(fixed)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return {}


def _coerce_style_preserving_mode(raw_style: str, style_mode: str, fallback_style: str) -> str:
    """Keep AI style elaboration compatible with chosen style_mode without medium drift (FA1.2)."""
    mode = normalize_style_mode(style_mode)
    base = STYLE_MODE_BASE_TEXTS.get(mode, "")
    candidate = str(raw_style or "").strip()
    if mode == "custom":
        return candidate or fallback_style
    if not candidate:
        return base or fallback_style
    low = candidate.lower()
    if mode == "realistic":
        if any(tok in low for tok in ("illustration", "painting", "watercolor", "cartoon", "anime", "drawing")):
            return base
        if "photorealistic" not in low and "35mm" not in low and "film still" not in low:
            return f"{base}, {candidate}"
        return candidate
    if mode == "illustration":
        if any(tok in low for tok in ("photorealistic", "film still", "35mm")):
            return base
        if "illustrat" not in low:
            return f"{base}, {candidate}"
        return candidate
    if mode == "painting":
        if any(tok in low for tok in ("photorealistic", "film still", "35mm")):
            return base
        if "paint" not in low:
            return f"{base}, {candidate}"
        return candidate
    return candidate


def _coerce_visual_bible(
    raw: Dict[str, Any],
    *,
    fallback: VisualBible,
    source: str = "ai",
    style_mode: Optional[str] = None,
) -> VisualBible:
    eff_mode = normalize_style_mode(style_mode or fallback.style_mode)
    world = str(raw.get("world") or "").strip() or fallback.world
    style = _coerce_style_preserving_mode(str(raw.get("style") or ""), eff_mode, fallback.style)
    palette_lighting = str(raw.get("palette_lighting") or "").strip() or fallback.palette_lighting

    avoid_val = raw.get("avoid")
    if isinstance(avoid_val, list):
        avoid_raw = ", ".join(str(x).strip() for x in avoid_val if str(x or "").strip())
    else:
        avoid_raw = str(avoid_val or "").strip()
    if not avoid_raw:
        avoid_raw = fallback.avoid
    avoid = reconcile_avoid_with_style(avoid_raw, eff_mode)

    chars: List[VisualBibleCharacter] = []
    seen_cids: set[str] = set()
    raw_chars = raw.get("characters")
    if isinstance(raw_chars, list):
        for idx, item in enumerate(raw_chars, start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            desc = str(item.get("description") or "").strip()
            if not name and not desc:
                continue
            cid = _slugify_id(str(item.get("id") or name or f"char_{idx}"), prefix="char", index=idx)
            while cid in seen_cids:
                cid = f"{cid}_{idx}"
            seen_cids.add(cid)
            aliases_raw = item.get("aliases") or []
            if isinstance(aliases_raw, str):
                aliases = [x.strip() for x in re.split(r"[,;\n]+", aliases_raw) if x.strip()]
            elif isinstance(aliases_raw, list):
                aliases = [str(x).strip() for x in aliases_raw if str(x or "").strip()]
            else:
                aliases = []
            chars.append(
                VisualBibleCharacter(
                    id=cid,
                    name=name or cid,
                    aliases=aliases,
                    role=str(item.get("role") or "").strip(),
                    description=desc,
                )
            )

    locs: List[VisualBibleLocation] = []
    seen_lids: set[str] = set()
    raw_locs = raw.get("locations")
    if isinstance(raw_locs, list):
        for idx, item in enumerate(raw_locs, start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            desc = str(item.get("description") or "").strip()
            if not name and not desc:
                continue
            lid = _slugify_id(str(item.get("id") or name or f"loc_{idx}"), prefix="loc", index=idx)
            while lid in seen_lids:
                lid = f"{lid}_{idx}"
            seen_lids.add(lid)
            locs.append(
                VisualBibleLocation(
                    id=lid,
                    name=name or lid,
                    description=desc,
                )
            )

    overrides: List[VisualBibleChapterOverride] = []
    raw_ov = raw.get("chapter_overrides")
    if isinstance(raw_ov, list):
        for item in raw_ov:
            if not isinstance(item, dict):
                continue
            try:
                c_idx = int(item.get("chapter_index"))
            except (TypeError, ValueError):
                continue
            w = str(item.get("world") or "").strip()
            e = str(item.get("era") or "").strip()
            if w or e:
                overrides.append(VisualBibleChapterOverride(chapter_index=c_idx, world=w, era=e))

    return VisualBible(
        world=world,
        style_mode=eff_mode,
        style=style,
        palette_lighting=palette_lighting,
        characters=chars,
        locations=locs,
        chapter_overrides=overrides,
        avoid=avoid,
        source=source,
    )


def _build_visual_bible_llm_prompt(
    manifest: ProjectManifest,
    script_text: str,
    *,
    style_mode: str = "realistic",
) -> str:
    snap = manifest.preset_snapshot or {}
    preset_name = str(snap.get("name") or manifest.preset_id or "").strip() if isinstance(snap, dict) else ""
    eff_mode = normalize_style_mode(style_mode)
    base_style = STYLE_MODE_BASE_TEXTS.get(eff_mode) or _preset_style_from_manifest(manifest)
    anchors = ", ".join(str(x) for x in (manifest.visual_anchor_terms or []))
    avoids = ", ".join(str(x) for x in (manifest.visual_avoid_terms or []))
    ch_titles = "; ".join(f"Ch{ch.chapter_index}: {ch.chapter_title}" for ch in (manifest.chapters or []) if ch.chapter_title)

    return (
        "You are a visual continuity director preparing a reusable English Visual Bible JSON for an image generator.\n"
        "The output MUST work accurately for ANY genre (folklore, history, modern drama, documentary without recurring characters, science, engineering).\n\n"
        f"Project title: {manifest.title}\n"
        f"Topic: {manifest.topic or manifest.title}\n"
        f"Preset/Niche: {preset_name} ({manifest.preset_id})\n"
        f"Chosen style_mode: {eff_mode}\n"
        + (f"Base style text (preserve this medium, only enrich compatible details): {base_style}\n" if base_style else "")
        + (f"Visual summary: {manifest.visual_summary}\n" if manifest.visual_summary else "")
        + (f"Visual anchors: {anchors}\n" if anchors else "")
        + (f"Existing avoid terms: {avoids}\n" if avoids else "")
        + (f"Chapters: {ch_titles}\n" if ch_titles else "")
        + "\nFull narration script:\n"
        + (script_text or "(no narration provided)")
        + "\n\nRules (strict):\n"
        "1. NEVER invent characters that do not appear in the script.\n"
        "2. Derive each character's gender, age, hair, outfit (with specific colors), and one distinguishing detail from the script or well-known source tale.\n"
        "   - Primary entry age MUST reflect the current main timeline, NOT a flashback.\n"
        "   - If a character appears at a distinctly younger/older age in a flashback, create a separate entry.\n"
        "3. If the script is a documentary/explainer without recurring people (e.g. machines, vehicles, science, geopolitics), set `\"characters\": []` and maintain visual consistency through `world`, `style`, `palette_lighting`, `locations`, and `chapter_overrides` (`chapter_index`, `world`, `era`) when chapters move across eras or regions.\n"
        f"4. STRICTLY respect the user's chosen `style_mode` ({eff_mode}). Do NOT switch medium (e.g. do NOT change photorealistic film still into illustration/painting, and do NOT change illustration/painting into photography). Only refine compatible visual details.\n"
        "5. `locations` lists recurring places used across multiple shots with concrete architectural/environmental details.\n"
        "6. `avoid` MUST be consistent with `style` (never put artistic terms inside `avoid` that contradict `style`). Include anachronisms/distractors like `modern buildings, cars, electric lights, plastic, modern clothing, text, watermark` when historically appropriate.\n"
        "\nReturn ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "world": "place and historical era/setting in English",\n'
        '  "style": "cohesive visual art/camera style for this project",\n'
        '  "palette_lighting": "color palette and dominant lighting mood",\n'
        '  "characters": [\n'
        '    {"id": "snake_case_id", "name": "Name", "aliases": ["name", "you", "pronoun"], "role": "protagonist", "description": "gender, age, hair, colored outfit, one distinguishing trait"}\n'
        "  ],\n"
        '  "locations": [\n'
        '    {"id": "snake_case_location_id", "name": "Location Name", "description": "fixed environmental and architectural description"}\n'
        "  ],\n"
        '  "chapter_overrides": [\n'
        '    {"chapter_index": 1, "world": "optional chapter setting", "era": "optional era"}\n'
        "  ],\n"
        '  "avoid": "comma-separated items to exclude"\n'
        "}\n"
    )


def ensure_project_visual_bible(
    manifest: ProjectManifest,
    *,
    cache_dir: Optional[Path] = None,
    llm_adapter: Optional[LLMAdapter] = None,
    force: bool = False,
    allow_llm: bool = True,
    raise_on_error: bool = False,
    style_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Load, generate (with active LLM + JSON recovery), or template-build `manifest.visual_bible` (F1, FA1.2)."""
    if not force and manifest.visual_bible is not None:
        return {
            "visual_bible": manifest.visual_bible,
            "source": manifest.visual_bible.source or "manifest",
            "cached": False,
        }

    eff_mode = normalize_style_mode(
        style_mode if style_mode is not None else (manifest.visual_bible.style_mode if manifest.visual_bible else "realistic")
    )
    fallback_bible = build_template_visual_bible(
        manifest,
        style_mode=(style_mode if style_mode is not None else None),
    )
    if style_mode is None and manifest.visual_bible is None:
        eff_mode = fallback_bible.style_mode

    script_text = collect_project_script_text(manifest)
    sig_raw = "|".join(
        [
            str(manifest.title or "").strip().lower(),
            str(manifest.topic or "").strip().lower(),
            str(manifest.preset_id or "").strip().lower(),
            eff_mode,
            str(manifest.visual_summary or "").strip().lower(),
            ",".join(str(x).lower() for x in (manifest.visual_anchor_terms or [])),
            hash_string(normalize_text(script_text)),
        ]
    )
    c_key = hash_string(f"v102_fa1_vb|{sig_raw}")
    cache_path = _visual_bible_cache_file(cache_dir)
    cache = _load_cache(cache_path)

    if not force and isinstance(cache.get(c_key), dict) and cache[c_key].get("bible"):
        try:
            loaded_vb = VisualBible.model_validate(cache[c_key]["bible"])
            manifest.visual_bible = loaded_vb
            return {
                "visual_bible": loaded_vb,
                "source": str(cache[c_key].get("source") or "cache"),
                "cached": True,
            }
        except Exception:
            pass

    readiness = get_llm_readiness()
    llm_ready = (llm_adapter is not None) or (
        bool(readiness.get("configured")) and not bool(readiness.get("is_invalid"))
    )

    if not allow_llm or not llm_ready:
        manifest.visual_bible = fallback_bible
        return {
            "visual_bible": fallback_bible,
            "source": "template",
            "cached": False,
        }

    prompt = _build_visual_bible_llm_prompt(manifest, script_text, style_mode=eff_mode)
    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            raw_content, prov, model_name = _invoke_translation_llm(
                prompt,
                llm_adapter=llm_adapter,
                max_tokens=1500,
            )
            parsed = _extract_json_object(raw_content)
            if not parsed or (not parsed.get("world") and not parsed.get("style")):
                raise ValueError("LLM VisualBible JSON missing required world/style fields")
            vb = _coerce_visual_bible(parsed, fallback=fallback_bible, source="ai", style_mode=eff_mode)
            manifest.visual_bible = vb
            cache[c_key] = {
                "bible": vb.model_dump(mode="json"),
                "source": "ai",
                "provider": prov,
                "model": model_name,
            }
            _save_cache(cache_path, cache)
            return {
                "visual_bible": vb,
                "source": "ai",
                "cached": False,
                "provider": prov,
                "model": model_name,
            }
        except (LLMCreditError, LLMDailyQuotaError, LLMModelNotFoundError):
            raise
        except Exception as exc:
            last_err = exc
            logger.warning(f"VisualBible LLM attempt {attempt + 1} failed: {exc}")

    if raise_on_error and last_err is not None:
        raise last_err
    logger.warning(f"VisualBible falling back to template after LLM failure: {last_err}")
    manifest.visual_bible = fallback_bible
    return {
        "visual_bible": fallback_bible,
        "source": "template_fallback",
        "cached": False,
    }
