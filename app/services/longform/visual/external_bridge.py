"""External Image Bridge: export shot prompts & bulk-import user-created visuals (Stage V101 V3)."""

from __future__ import annotations

import csv
import io
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, SceneContinuity, Shot, VisualBible
from app.services.longform.shot import compute_shot_hash
from app.services.longform.store import save_manifest
from app.services.longform.visual.base import (
    compute_file_content_hash,
    invalidate_shot_thumbnail_cache,
)
from app.services.longform.visual.builder import ShotClipBuilder
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.scene_continuity import (
    _match_characters_by_aliases,
    assign_scenes_and_prompts_for_shots,
)
from app.services.longform.visual.service import VisualService
from app.services.longform.visual.visual_bible import (
    build_template_visual_bible,
    clean_world_slug_prefix,
    ensure_project_visual_bible,
    validate_visual_bible,
)

EXTERNAL_IMPORT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".mp4"}
EXTERNAL_ATTRIBUTION_TEXT = "Gambar dibuat pengguna dengan alat eksternal"
GENERIC_SUBJECT_TOKENS = (
    "the witch",
    "child",
    "children",
    "kid",
    "boy",
    "girl",
    "you",
    "she",
    "he",
    "woman",
    "man",
    "person",
)

# Exact word/delimiter boundaries prevent 'ch01_shot001' matching 'ch01_shot0010'
_SHOT_ID_EXACT_RE = re.compile(r"(?:^|[^a-z0-9])(ch\d+_shot\d+)(?=$|[^a-z0-9])", re.IGNORECASE)
_TRAILING_SEQ_RE = re.compile(r"(?:^|[_\-\s#()])0*(\d+)(?:[_\-\s()]*)$")


def _extract_visual_style(manifest: ProjectManifest) -> str:
    if getattr(manifest, "visual_bible", None) and str(manifest.visual_bible.style or "").strip():
        return str(manifest.visual_bible.style).strip()
    snap = manifest.preset_snapshot or {}
    vis = snap.get("visual") if isinstance(snap, dict) else None
    if isinstance(vis, dict):
        return str(vis.get("style") or "").strip()
    return str(snap.get("style") or "").strip() if isinstance(snap, dict) else ""


def format_continuity_clause(continuity: Optional[SceneContinuity]) -> str:
    """Format a SceneContinuity object deterministically so every shot in the same scene matches word-for-word (F3)."""
    if continuity is None:
        return ""
    parts: List[str] = []
    if str(continuity.time_of_day or "").strip():
        parts.append(f"time: {str(continuity.time_of_day).strip()}")
    if str(continuity.weather or "").strip():
        parts.append(f"weather: {str(continuity.weather).strip()}")
    if str(continuity.lighting or "").strip():
        parts.append(f"lighting: {str(continuity.lighting).strip()}")
    if continuity.props:
        props_clean = [str(p).strip() for p in continuity.props if str(p or "").strip()]
        if props_clean:
            parts.append(f"props: {', '.join(props_clean)}")
    if str(continuity.character_state or "").strip():
        parts.append(f"state: {str(continuity.character_state).strip()}")
    return "; ".join(parts)


def _replace_untethered_second_person(scene_en: str, locked_chars: List[Any]) -> str:
    """Replace bare POV 'you'/'your' in fallback scene text when a locked character is present."""
    if not scene_en or len(locked_chars) != 1:
        return scene_en
    c = locked_chars[0]
    name = str(getattr(c, "name", "") or "").strip()
    if not name:
        return scene_en
    out = re.sub(r"\byour\b", f"{name}'s", scene_en, flags=re.IGNORECASE)
    out = re.sub(r"\byourself\b", name, out, flags=re.IGNORECASE)
    out = re.sub(r"\byou\b", name, out, flags=re.IGNORECASE)
    return out


def validate_external_prompt(
    prompt_text: str,
    *,
    scene_en: Optional[str] = None,
    locked_character_ids: Optional[Sequence[str]] = None,
    bible: Optional[VisualBible] = None,
) -> str:
    """Inspect an external prompt for unlocked generic human subjects or missing world/style (F3, F6c, F6j).

    Returns empty string when valid, or a warning string prefixed with '⚠'.
    """
    issues: List[str] = []

    char_map = {c.id: c for c in (bible.characters if bible else [])}
    locked_ids = [str(cid).strip() for cid in (locked_character_ids or []) if str(cid or "").strip()]
    locked_chars = [char_map[cid] for cid in locked_ids if cid in char_map and str(char_map[cid].description or "").strip()]

    has_inline_char_clause = bool(re.search(r"Characters:\s*[^;\n]+\([^)]+\)", str(prompt_text or "")))
    has_locked_chars = bool(locked_chars) or has_inline_char_clause

    # Inspect scene body excluding Avoid / Style / Setting / Characters tail so bible avoid terms do not trigger false alarms
    if scene_en is not None:
        check_body = str(scene_en)
    else:
        check_body = re.split(
            r"\b(?:Characters:|Location:|Continuity:|Setting:|Style:|Palette(?:\s*&\s*lighting)?:|Avoid:)\b",
            str(prompt_text or ""),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]

    covered_aliases: set[str] = set()
    for lc in locked_chars:
        covered_aliases.add(lc.name.lower().strip())
        for a in lc.aliases or []:
            clean_a = re.sub(r"\([^)]*\)", "", str(a)).strip().lower()
            if clean_a:
                covered_aliases.add(clean_a)

    found_generic: List[str] = []
    for tok in GENERIC_SUBJECT_TOKENS:
        if has_locked_chars and tok in covered_aliases:
            continue
        pat = r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])"
        if re.search(pat, check_body, flags=re.IGNORECASE):
            found_generic.append(tok)

    if locked_ids and not locked_chars:
        issues.append("tokoh belum memiliki deskripsi fisik lengkap")
    elif found_generic and not has_locked_chars:
        issues.append(f"subjek umum belum mengunci tokoh ({', '.join(found_generic[:3])})")
    elif found_generic and any(t == "the witch" or t not in covered_aliases for t in found_generic):
        # Flag additional generic actors mentioned in scene_en who were not covered by locked characters
        extra = [t for t in found_generic if t not in covered_aliases]
        if extra and not has_inline_char_clause:
            issues.append(f"subjek umum belum mengunci tokoh ({', '.join(extra[:3])})")

    has_world = bool(
        (bible and str(bible.world or "").strip())
        or re.search(r"\b(?:Setting|World):\s*\S+", str(prompt_text or ""), re.IGNORECASE)
    )
    has_style = bool(
        (bible and str(bible.style or "").strip())
        or re.search(r"\bStyle:\s*\S+", str(prompt_text or ""), re.IGNORECASE)
    )
    if not has_world or not has_style:
        missing_ws = []
        if not has_world:
            missing_ws.append("world")
        if not has_style:
            missing_ws.append("style")
        issues.append(f"konteks belum lengkap ({'/'.join(missing_ws)})")

    if bible and str(bible.style or "").strip() and str(bible.avoid or "").strip():
        style_low = str(bible.style).lower()
        avoid_terms = [x.strip().lower() for x in re.split(r"[,;\n]+", str(bible.avoid)) if x.strip()]
        conflicts: List[str] = []
        for av in avoid_terms:
            # Check exact avoid phrase OR its meaningful tokens (>= 4 chars) inside Style
            if re.search(r"(?<![a-z0-9])" + re.escape(av) + r"(?![a-z0-9])", style_low):
                conflicts.append(av)
                continue
            for tok in re.findall(r"[a-z0-9]{4,}", av):
                if tok not in {"modern", "realistic", "film", "still", "light", "look", "style"} and re.search(
                    r"(?<![a-z0-9])" + re.escape(tok) + r"(?![a-z0-9])", style_low
                ):
                    conflicts.append(tok)
                    break
        if conflicts:
            issues.append(f"kata Avoid muncul di Style ({', '.join(dict.fromkeys(conflicts))})")

    if bible is not None:
        # Check palette_lighting for time/light tokens (FA1.3) - keep alias checks via validate_visual_bible directly when editing
        vb_pal_issues = [
            msg for msg in validate_visual_bible(bible)
            if "palette_lighting" in msg or "slug proyek" in msg
        ]
        issues.extend(vb_pal_issues)

    if not issues:
        return ""
    return "⚠ " + "; ".join(issues)


def compose_shot_external_prompt(
    manifest: ProjectManifest,
    chapter: Optional[Chapter],
    shot: Shot,
) -> Dict[str, Any]:
    """Deterministically compose a self-contained external image prompt from scene + locked bible fields (F3)."""
    bible = manifest.visual_bible or build_template_visual_bible(manifest)
    aspect_ratio = str(getattr(manifest, "aspect_ratio", None) or "16:9")

    char_map = {c.id: c for c in (bible.characters or [])}
    loc_map = {loc.id: loc for loc in (bible.locations or [])}

    narr_raw = VisualService._get_shot_narration_text(manifest, shot)
    char_ids = list(getattr(shot, "characters_in_shot", None) or [])
    if not char_ids and bible.characters and narr_raw:
        char_ids = _match_characters_by_aliases(str(shot.scene_en or narr_raw), bible)

    resolved_chars = [char_map[cid] for cid in char_ids if cid in char_map]
    scene_en = str(getattr(shot, "scene_en", None) or "").strip()
    if not scene_en:
        scene_en = str(
            narr_raw
            or getattr(shot, "search_query_en", None)
            or shot.search_query
            or shot.visual_prompt
            or "Visual scene matching narration"
        ).strip()
    scene_en = _replace_untethered_second_person(scene_en, resolved_chars)
    scene_clean = scene_en.rstrip(" .")

    eff_loc_id = getattr(shot, "location_id", None) or (
        shot.continuity.location_id if getattr(shot, "continuity", None) else None
    )
    loc_obj = loc_map.get(eff_loc_id) if eff_loc_id else None

    eff_cont = getattr(shot, "continuity", None)
    cont_str = format_continuity_clause(eff_cont)

    ch_idx = chapter.chapter_index if chapter is not None else shot.chapter_index
    ch_ov = next((ov for ov in (bible.chapter_overrides or []) if ov.chapter_index == ch_idx), None)
    base_world = clean_world_slug_prefix(str(bible.world or "").strip(), project_id=manifest.project_id)
    if ch_ov is not None and str(ch_ov.world or "").strip():
        eff_world = clean_world_slug_prefix(str(ch_ov.world).strip(), project_id=manifest.project_id)
        ov_era = str(ch_ov.era or "").strip()
        world_text = f"{eff_world}, {ov_era}" if (ov_era and ov_era.lower() not in eff_world.lower()) else eff_world
    else:
        ov_era = str(ch_ov.era or "").strip() if ch_ov is not None else ""
        if base_world and ov_era and ov_era.lower() not in base_world.lower():
            world_text = f"{base_world}, {ov_era}"
        else:
            world_text = base_world or ov_era

    segments: List[str] = [f"{scene_clean}."]
    if resolved_chars:
        char_clauses = [
            f"{c.name} ({c.description})" if str(c.description or "").strip() else c.name
            for c in resolved_chars
        ]
        segments.append(f"Characters: {'; '.join(char_clauses)}.")
    if loc_obj is not None:
        loc_desc = str(loc_obj.description or loc_obj.name or "").strip()
        if loc_desc:
            segments.append(f"Location: {loc_desc}.")
    if cont_str:
        segments.append(f"Continuity: {cont_str}.")
    if world_text:
        segments.append(f"Setting: {world_text}.")
    if str(bible.style or "").strip():
        segments.append(f"Style: {str(bible.style).strip()}.")
    if str(bible.palette_lighting or "").strip():
        segments.append(f"Palette & lighting: {str(bible.palette_lighting).strip()}.")
    segments.append(f"{aspect_ratio} widescreen.")
    if str(bible.avoid or "").strip():
        segments.append(f"Avoid: {str(bible.avoid).strip()}.")

    prompt_en = " ".join(segments)
    lock_warn = validate_external_prompt(
        prompt_en,
        scene_en=scene_en,
        locked_character_ids=char_ids,
        bible=bible,
    )
    missing_char_lock = bool(lock_warn and "mengunci tokoh" in lock_warn)

    return {
        "shot_id": shot.shot_id,
        "scene_id": getattr(shot, "scene_id", None) or f"ch{ch_idx:02d}_scene01",
        "scene_en": scene_en,
        "characters_in_shot": char_ids,
        "location_id": eff_loc_id or "",
        "continuity": cont_str,
        "continuity_obj": eff_cont,
        "lock_warning": lock_warn,
        "has_lock_warning": bool(lock_warn),
        "missing_character_lock": missing_char_lock,
        "prompt_en": prompt_en,
    }


def _shot_is_missing_visual(shot: Shot, project_dir: Path) -> bool:
    src_low = str(getattr(shot, "actual_source", None) or "").strip().lower()
    if getattr(shot, "is_fake", False) or src_low in ("", "fake", "neutral_backdrop"):
        return True
    if str(getattr(shot, "fallback_reason", None) or "") == "no_visual_found":
        return True
    has_asset = bool(shot.asset_path and (project_dir / shot.asset_path).is_file())
    has_clip = bool(shot.rendered_clip and (project_dir / shot.rendered_clip).is_file())
    return not (has_asset or has_clip)


def _iter_project_shots(manifest: ProjectManifest) -> List[Shot]:
    return [sh for ch in (manifest.chapters or []) for sh in (ch.shots or [])]


def _format_visual_context_txt_block(bible: VisualBible) -> str:
    """Format the top KONTEKS VISUAL header block for .txt prompt exports (F5)."""
    lines: List[str] = ["=== KONTEKS VISUAL ==="]
    lines.append(f"World: {bible.world or '-'}")
    lines.append(f"Style: {bible.style or '-'}")
    lines.append(f"Palette & Lighting: {bible.palette_lighting or '-'}")
    if bible.characters:
        lines.append("Characters:")
        for c in bible.characters:
            alias_str = f" [aliases: {', '.join(c.aliases)}]" if c.aliases else ""
            lines.append(f"  - {c.id} ({c.name}): {c.description}{alias_str}")
    else:
        lines.append("Characters: (none / documentary)")
    if bible.locations:
        lines.append("Locations:")
        for loc in bible.locations:
            lines.append(f"  - {loc.id} ({loc.name}): {loc.description}")
    if bible.chapter_overrides:
        lines.append("Chapter Overrides:")
        for ov in bible.chapter_overrides:
            lines.append(f"  - Chapter {ov.chapter_index}: world={ov.world or '-'}; era={ov.era or '-'}")
    if bible.avoid:
        lines.append(f"Avoid: {bible.avoid}")
    return "\n".join(lines)


def _format_scene_heading(scene_id: str, composed: Dict[str, Any], bible: VisualBible) -> str:
    """Format readable scene header e.g. '== Scene ch01_scene03: dusk, forest clearing ==' (F5)."""
    cont_obj = composed.get("continuity_obj")
    loc_id = str(composed.get("location_id") or "").strip()
    loc_label = ""
    if loc_id:
        loc_obj = next((loc for loc in (bible.locations or []) if loc.id == loc_id), None)
        loc_label = loc_obj.name if (loc_obj and loc_obj.name) else loc_id
    summary_parts: List[str] = []
    if cont_obj and str(cont_obj.time_of_day or "").strip():
        summary_parts.append(str(cont_obj.time_of_day).strip())
    if loc_label:
        summary_parts.append(loc_label)
    if cont_obj and str(cont_obj.weather or "").strip():
        summary_parts.append(str(cont_obj.weather).strip())
    if summary_parts:
        return f"== Scene {scene_id}: {', '.join(summary_parts)} =="
    return f"== Scene {scene_id} =="


def export_external_image_prompts(
    manifest: ProjectManifest,
    project_dir: Path | str,
    *,
    scope: str = "all",
    selected_shot_ids: Optional[Sequence[str]] = None,
    auto_prepare_llm: bool = False,
    llm_adapter: Optional[Any] = None,
    cache_dir: Optional[Path] = None,
    force_prepare: bool = False,
    progress_callback: Optional[Any] = None,
    raise_on_llm_error: bool = False,
) -> Dict[str, Any]:
    """Build .txt and UTF-8 .csv prompt exports with visual_bible, scenes, continuity & lock validation (V3, F1-F5)."""
    p_dir = Path(project_dir).resolve()
    sel_set = {str(x).strip() for x in (selected_shot_ids or []) if str(x).strip()}
    eff_scope = str(scope or "all").strip().lower()

    all_shots = _iter_project_shots(manifest)
    if eff_scope == "selected" and sel_set:
        target_shots = [s for s in all_shots if s.shot_id in sel_set]
    elif eff_scope == "missing_visual":
        target_shots = [s for s in all_shots if _shot_is_missing_visual(s, p_dir)]
    else:
        target_shots = all_shots

    use_llm = bool(auto_prepare_llm or llm_adapter is not None)
    if manifest.visual_bible is None:
        ensure_project_visual_bible(
            manifest,
            cache_dir=cache_dir,
            llm_adapter=llm_adapter,
            force=False,
            allow_llm=use_llm,
            raise_on_error=bool(use_llm and raise_on_llm_error),
        )

    assign_scenes_and_prompts_for_shots(
        manifest,
        target_shot_ids=[s.shot_id for s in target_shots],
        llm_adapter=llm_adapter,
        cache_dir=cache_dir,
        force=force_prepare,
        allow_llm=use_llm,
        progress_callback=progress_callback,
        raise_on_error=bool(use_llm and raise_on_llm_error),
    )

    eff_bible = manifest.visual_bible or build_template_visual_bible(manifest)
    ch_map = {ch.chapter_index: ch for ch in (manifest.chapters or [])}
    visual_style = _extract_visual_style(manifest)
    aspect_ratio = str(getattr(manifest, "aspect_ratio", None) or "16:9")

    rows: List[Dict[str, Any]] = []
    txt_sections: List[str] = []
    if target_shots:
        txt_sections.append(_format_visual_context_txt_block(eff_bible))

    warning_count = 0
    unlocked_char_count = 0
    last_scene_id: Optional[str] = None

    for sh in target_shots:
        narr_full = VisualService._get_shot_narration_text(manifest, sh)
        narr_snip = (narr_full[:140] + "...") if len(narr_full) > 140 else narr_full
        composed = compose_shot_external_prompt(manifest, ch_map.get(sh.chapter_index), sh)
        if composed["has_lock_warning"]:
            warning_count += 1
        if composed["missing_character_lock"]:
            unlocked_char_count += 1

        row = {
            "shot_id": sh.shot_id,
            "duration_seconds": round(float(sh.duration_seconds), 2),
            "narration_snippet": narr_snip,
            "scene_id": composed["scene_id"],
            "scene_en": composed["scene_en"],
            "characters_in_shot": ", ".join(composed["characters_in_shot"]),
            "location_id": composed["location_id"],
            "continuity": composed["continuity"],
            "lock_warning": composed["lock_warning"],
            "prompt_en": composed["prompt_en"],
            "aspect_ratio": aspect_ratio,
            "visual_style": visual_style or "-",
        }
        rows.append(row)
        if composed["scene_id"] != last_scene_id:
            txt_sections.append(_format_scene_heading(composed["scene_id"], composed, eff_bible))
            last_scene_id = composed["scene_id"]
        txt_sections.append(f"[{sh.shot_id}] ({row['duration_seconds']:.1f}s | {aspect_ratio})\n{composed['prompt_en']}")

    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf, lineterminator="\n")
    writer.writerow([
        "shot_id",
        "duration_seconds",
        "narration_snippet",
        "scene_id",
        "scene_en",
        "characters_in_shot",
        "location_id",
        "continuity",
        "lock_warning",
        "prompt_en",
        "aspect_ratio",
        "visual_style",
    ])
    for r in rows:
        writer.writerow([
            r["shot_id"],
            f"{r['duration_seconds']:.2f}",
            r["narration_snippet"],
            r["scene_id"],
            r["scene_en"],
            r["characters_in_shot"],
            r["location_id"],
            r["continuity"],
            r["lock_warning"],
            r["prompt_en"],
            r["aspect_ratio"],
            r["visual_style"],
        ])

    manifest.last_prompt_export_shot_ids = [r["shot_id"] for r in rows]
    save_manifest(manifest, p_dir)

    return {
        "count": len(rows),
        "warning_count": warning_count,
        "unlocked_char_count": unlocked_char_count,
        "shot_ids": [r["shot_id"] for r in rows],
        "rows": rows,
        "txt": ("\n\n".join(txt_sections) + "\n") if txt_sections else "",
        "csv": csv_buf.getvalue(),
    }


def match_external_filename_to_shot(
    filename: str,
    manifest: ProjectManifest,
) -> Tuple[Optional[str], str]:
    """Match an imported filename to a project shot_id (V3).

    Order:
    1. Exact 'ch\\d+_shot\\d+' token with strict boundaries ('ch01_shot001' != 'ch01_shot0010').
    2. Trailing sequence number (1-based) using last prompt export order or full project order.
    """
    clean_name = Path(str(filename or "")).name
    stem = Path(clean_name).stem

    all_shots = _iter_project_shots(manifest)
    shot_lookup = {s.shot_id.lower(): s.shot_id for s in all_shots}

    exact_hits = _SHOT_ID_EXACT_RE.findall(stem)
    if exact_hits:
        for tok in exact_hits:
            matched = shot_lookup.get(tok.lower())
            if matched:
                return matched, "exact_shot_id"
        # Had an explicit chXX_shotYYY token that does not exist in this project:
        # never fall back to trailing integer (e.g., ch01_shot0010 must stay unmatched).
        return None, "unmatched_shot_id"

    m_seq = _TRAILING_SEQ_RE.search(stem)
    if m_seq:
        seq_idx = int(m_seq.group(1))
        export_order = [
            sid for sid in (getattr(manifest, "last_prompt_export_shot_ids", None) or [])
            if sid.lower() in shot_lookup
        ]
        target_pool = export_order if export_order else [s.shot_id for s in all_shots]
        if 1 <= seq_idx <= len(target_pool):
            return target_pool[seq_idx - 1], ("export_sequence" if export_order else "project_sequence")

    return None, "unmatched"


def plan_bulk_external_import(
    manifest: ProjectManifest,
    files: Sequence[Tuple[str, bytes]],
) -> List[Dict[str, Any]]:
    """Build editable mapping rows for uploaded external images/videos (V3)."""
    rows: List[Dict[str, Any]] = []
    supported_str = ", ".join(sorted(EXTERNAL_IMPORT_EXTENSIONS))

    for idx, (raw_name, content) in enumerate(files):
        clean_name = Path(str(raw_name or "")).name
        ext = Path(clean_name).suffix.lower()
        supported = bool(clean_name and ext in EXTERNAL_IMPORT_EXTENSIONS)
        if not supported:
            rows.append({
                "index": idx,
                "filename": clean_name or str(raw_name),
                "ext": ext,
                "supported": False,
                "error": f"Unsupported format '{ext}'. Supported: {supported_str}",
                "matched_shot_id": None,
                "match_reason": "unsupported_format",
                "size_bytes": len(content or b""),
                "content": content,
            })
            continue

        matched_sid, reason = match_external_filename_to_shot(clean_name, manifest)
        rows.append({
            "index": idx,
            "filename": clean_name,
            "ext": ext,
            "supported": True,
            "error": None,
            "matched_shot_id": matched_sid,
            "match_reason": reason,
            "size_bytes": len(content or b""),
            "content": content,
        })

    return rows


def apply_bulk_external_import(
    project_dir: Path | str,
    manifest: ProjectManifest,
    mappings: Sequence[Dict[str, Any]],
    *,
    rebuild: bool = True,
) -> Dict[str, Any]:
    """Copy confirmed external assets into project folder, render clips, lock shots, update credits & manifest (V3)."""
    p_dir = Path(project_dir).resolve()
    fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
    builder = ShotClipBuilder(fps=fps)

    shot_map: Dict[str, Shot] = {s.shot_id: s for s in _iter_project_shots(manifest)}
    applied_shots: List[str] = []
    skipped_files: List[str] = []
    errors: List[str] = []

    for row in mappings:
        if not row.get("supported", True):
            skipped_files.append(str(row.get("filename") or ""))
            continue
        shot_id = str(row.get("matched_shot_id") or "").strip()
        if not shot_id:
            skipped_files.append(str(row.get("filename") or ""))
            continue
        target_shot = shot_map.get(shot_id)
        if target_shot is None:
            errors.append(f"{row.get('filename')}: unknown shot '{shot_id}'")
            continue

        clean_name = Path(str(row.get("filename") or f"{shot_id}.png")).name
        ext = Path(clean_name).suffix.lower()
        if ext not in EXTERNAL_IMPORT_EXTENSIONS:
            errors.append(f"{clean_name}: unsupported extension '{ext}'")
            continue

        rel_dest = f"assets/visual/shots/{shot_id}_asset{ext}"
        dest = (p_dir / rel_dest).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        content = row.get("content")
        src_path = row.get("source_path")
        if content is not None:
            dest.write_bytes(bytes(content))
        elif src_path:
            shutil.copyfile(Path(src_path).resolve(), dest)
        else:
            errors.append(f"{clean_name}: no file data")
            continue

        clip_rel = f"assets/visual/shots/{shot_id}.mp4"
        clip_path = (p_dir / clip_rel).resolve()

        target_shot.asset_path = rel_dest
        target_shot.asset_content_hash = compute_file_content_hash(dest)
        target_shot.requested_source = "external_image"
        target_shot.actual_source = "external_image"
        target_shot.is_fake = False
        target_shot.fallback_reason = None
        target_shot.no_visual_reason_summary = None
        target_shot.attribution_title = clean_name
        target_shot.attribution_author = None
        target_shot.attribution_author_url = None
        target_shot.attribution_license = EXTERNAL_ATTRIBUTION_TEXT
        target_shot.attribution_url = None
        target_shot.requires_attribution = False
        target_shot.locked = True
        target_shot.needs_review = False
        target_shot.relevance_score = 1.0

        if rebuild:
            builder.build_shot_clip(
                asset_path=dest,
                output_path=clip_path,
                motion=target_shot.motion,
                target_frames=target_shot.target_frames,
            )
            target_shot.rendered_clip = clip_rel
            target_shot.status = ItemStatus.completed
            target_shot.shot_hash = compute_shot_hash(
                shot_id=target_shot.shot_id,
                duration_seconds=target_shot.duration_seconds,
                target_frames=target_shot.target_frames,
                motion=target_shot.motion.value,
                asset_content_hash=target_shot.asset_content_hash,
                fps=fps,
                requested_provider=target_shot.requested_source or "",
                actual_provider=target_shot.actual_source or "",
            )
        else:
            has_stale = bool(target_shot.rendered_clip and (p_dir / target_shot.rendered_clip).is_file())
            target_shot.status = ItemStatus.stale if has_stale else ItemStatus.pending
            target_shot.shot_hash = None

        invalidate_shot_thumbnail_cache(p_dir, shot_id)
        applied_shots.append(shot_id)
        logger.info(f"External bridge applied '{clean_name}' -> {shot_id} (locked, source=external_image)")

    if applied_shots:
        CreditsManager(p_dir).write_credits_markdown(manifest)
        save_manifest(manifest, p_dir)

    return {
        "applied_shots": applied_shots,
        "skipped_files": skipped_files,
        "errors": errors,
    }
