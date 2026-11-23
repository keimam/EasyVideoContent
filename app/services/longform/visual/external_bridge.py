"""External Image Bridge: export shot prompts & bulk-import user-created visuals (Stage V101 V3)."""

from __future__ import annotations

import csv
import io
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.services.longform.schema import ItemStatus, ProjectManifest, Shot
from app.services.longform.shot import compute_shot_hash
from app.services.longform.store import save_manifest
from app.services.longform.visual.base import (
    compute_file_content_hash,
    invalidate_shot_thumbnail_cache,
)
from app.services.longform.visual.builder import ShotClipBuilder
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.service import VisualService

EXTERNAL_IMPORT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".mp4"}
EXTERNAL_ATTRIBUTION_TEXT = "Gambar dibuat pengguna dengan alat eksternal"

# Exact word/delimiter boundaries prevent 'ch01_shot001' matching 'ch01_shot0010'
_SHOT_ID_EXACT_RE = re.compile(r"(?:^|[^a-z0-9])(ch\d+_shot\d+)(?=$|[^a-z0-9])", re.IGNORECASE)
_TRAILING_SEQ_RE = re.compile(r"(?:^|[_\-\s#()])0*(\d+)(?:[_\-\s()]*)$")


def _extract_visual_style(manifest: ProjectManifest) -> str:
    snap = manifest.preset_snapshot or {}
    vis = snap.get("visual") if isinstance(snap, dict) else None
    if isinstance(vis, dict):
        return str(vis.get("style") or "").strip()
    return str(snap.get("style") or "").strip() if isinstance(snap, dict) else ""


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


def export_external_image_prompts(
    manifest: ProjectManifest,
    project_dir: Path | str,
    *,
    scope: str = "all",
    selected_shot_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Build .txt and UTF-8 .csv prompt exports and persist last exported shot order (V3)."""
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

    visual_style = _extract_visual_style(manifest)
    aspect_ratio = str(getattr(manifest, "aspect_ratio", None) or "16:9")

    rows: List[Dict[str, Any]] = []
    txt_blocks: List[str] = []

    for sh in target_shots:
        narr_full = VisualService._get_shot_narration_text(manifest, sh)
        narr_snip = (narr_full[:140] + "...") if len(narr_full) > 140 else narr_full
        base_en = str(
            getattr(sh, "search_query_en", None)
            or sh.search_query
            or sh.visual_prompt
            or "cinematic documentary scene"
        ).strip()
        style_suffix = f", {visual_style}" if visual_style and visual_style.lower() not in base_en.lower() else ""
        prompt_en = f"{base_en}{style_suffix}, {aspect_ratio} widescreen, photographic documentary still"

        row = {
            "shot_id": sh.shot_id,
            "duration_seconds": round(float(sh.duration_seconds), 2),
            "narration_snippet": narr_snip,
            "prompt_en": prompt_en,
            "aspect_ratio": aspect_ratio,
            "visual_style": visual_style or "-",
        }
        rows.append(row)
        txt_blocks.append(f"[{sh.shot_id}] ({row['duration_seconds']:.1f}s | {aspect_ratio})\n{prompt_en}")

    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf, lineterminator="\n")
    writer.writerow(["shot_id", "duration_seconds", "narration_snippet", "prompt_en", "aspect_ratio", "visual_style"])
    for r in rows:
        writer.writerow([
            r["shot_id"],
            f"{r['duration_seconds']:.2f}",
            r["narration_snippet"],
            r["prompt_en"],
            r["aspect_ratio"],
            r["visual_style"],
        ])

    manifest.last_prompt_export_shot_ids = [r["shot_id"] for r in rows]
    save_manifest(manifest, p_dir)

    return {
        "count": len(rows),
        "shot_ids": [r["shot_id"] for r in rows],
        "rows": rows,
        "txt": ("\n\n".join(txt_blocks) + "\n") if txt_blocks else "",
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
