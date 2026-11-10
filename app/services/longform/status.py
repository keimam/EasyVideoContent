"""Single canonical status and progress resolver for Long Video projects (C2, C3).

Used identically by CLI `project show` and Streamlit WebUI.
Derives state from manifest, disk assets, and active background jobs.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.longform.presets import get_preset
from app.services.longform.schema import ItemStatus, ProjectManifest
from app.services.longform.script_import import estimate_script_duration
from app.services.longform.store import get_project_dir, load_manifest
from app.services.longform.ui import jobs


# Fields in preset definitions that actually impact generated assets
_IMPACTFUL_PRESET_FIELDS = {
    "topic_keywords": "Kata kunci topik naskah (memengaruhi naskah)",
    "visual_keywords": "Kata kunci pencarian visual (memengaruhi pemilihan shot)",
    "words_per_minute": "Kecepatan narasi WPM (memengaruhi estimasi durasi)",
    "chapter_structure": "Struktur bab (memengaruhi pembagian naskah)",
    "hook_rules": "Aturan hook narasi (memengaruhi naskah)",
    "tts_provider": "Penyedia TTS (memengaruhi sintesis audio)",
    "default_voice": "Suara narator default (memengaruhi audio)",
    "voice_rate": "Kecepatan bicara (memengaruhi audio)",
    "pause_between_groups_ms": "Jeda antar grup audio (memengaruhi audio)",
    "recommended_visual_sources": "Sumber visual (memengaruhi visual)",
    "min_relevance_score": "Ambang relevansi visual (memengaruhi visual)",
    "required_subject_terms": "Syarat istilah subjek (memengaruhi visual)",
    "blocked_terms": "Istilah visual terlarang (memengaruhi visual)",
    "subtitle_style": "Gaya subtitle (memengaruhi render final)",
    "capcut_text_size": "Ukuran font CapCut (memengaruhi ekspor CapCut)",
}


# Default fallback values for fields when evaluating diffs (E2)
_FIELD_DEFAULTS: Dict[str, Any] = {
    "voice_rate": 1.0,
    "pause_between_groups_ms": 350,
    "min_relevance_score": 0.6,
    "words_per_minute": 140,
    "tts_provider": "edge",
}


def _normalize_val(val: Any) -> Any:
    """Normalize empty or None values for comparison."""
    if val is None or val == "" or val == [] or val == {}:
        return None
    return val


def _normalize_field_val(field: str, val: Any) -> Any:
    """Normalize values and resolve effective defaults (E2)."""
    norm = _normalize_val(val)
    if norm is None and field in _FIELD_DEFAULTS:
        return _FIELD_DEFAULTS[field]
    return norm


def get_impactful_preset_diffs(
    snapshot: Optional[Dict[str, Any]],
    current: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Compare project preset snapshot with effective disk preset definition (C3, E2).

    Filters out legacy None-vs-empty differences, default equivalence, and non-impactful metadata.
    """
    if not snapshot or not current:
        return []

    diffs: List[Dict[str, Any]] = []
    for field, description in _IMPACTFUL_PRESET_FIELDS.items():
        raw_snap = snapshot.get(field)
        raw_curr = current.get(field)

        snap_v = _normalize_field_val(field, raw_snap)
        curr_v = _normalize_field_val(field, raw_curr)

        # Rule D4: Field completely absent from snapshot (None) is not considered a change
        if snap_v is None:
            continue
        if curr_v is None:
            continue

        if snap_v != curr_v:
            diffs.append({
                "field": field,
                "description": description,
                "snapshot_val": snap_v,
                "current_val": curr_v,
            })

    return diffs


def find_latest_capcut_draft(project_dir: Path, project_id: str) -> Optional[Dict[str, Any]]:
    """Scan disk for the latest valid CapCut draft export (C2)."""
    capcut_dirs: List[Path] = []
    candidate_bases = [
        project_dir / "export" / "capcut",
        project_dir / "capcut_export",
        project_dir,
    ]

    for base in candidate_bases:
        if base.is_dir():
            for d in base.iterdir():
                if d.is_dir() and ((d / "draft_content.json").is_file() or (d / "draft_meta_info.json").is_file()):
                    capcut_dirs.append(d)

    if not capcut_dirs:
        return None

    # Prioritize drafts matching project name
    matching_project = [d for d in capcut_dirs if d.name.startswith(f"MPT_{project_id}")]
    target = max(matching_project, key=lambda d: d.stat().st_mtime) if matching_project else max(capcut_dirs, key=lambda d: d.stat().st_mtime)

    mtime_dt = datetime.fromtimestamp(target.stat().st_mtime)
    return {
        "draft_name": target.name,
        "draft_dir": str(target),
        "exported_at": mtime_dt.strftime("%Y-%m-%d %H:%M"),
    }


def compute_project_step_status(project_dir: str | Path) -> Dict[str, Any]:
    """Compute verified status across all 5 workflow steps from manifest and disk (C2).

    Single source of truth for UI and CLI.
    Status values: 'belum' | 'berjalan' | 'terhenti' | 'perlu_diperbarui' | 'selesai'.
    """
    p_dir = Path(project_dir).resolve()
    manifest = load_manifest(p_dir)
    p_id = manifest.project_id

    # 0. Check background job state
    job_state = jobs.get_job_state(p_id)
    is_job_running = (job_state and job_state.get("status") == "running")
    active_step = job_state.get("step") if is_job_running else None

    # Clean up stale project .lock if process is dead
    lock_file = p_dir / ".lock"
    if lock_file.is_file():
        try:
            import json
            from app.services.longform.store import is_pid_alive
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            l_pid = data.get("pid")
            if l_pid and not is_pid_alive(l_pid):
                lock_file.unlink(missing_ok=True)
                logger.info(f"Reclaimed stale project lock from dead PID {l_pid}")
        except Exception:
            pass

    # --- Step 1: Naskah (Script) ---
    total_chapters = len(manifest.chapters)
    completed_chapters = sum(
        1 for ch in manifest.chapters
        if (any(g.paragraph_text and g.paragraph_text.strip() for g in ch.tts_groups)
            or (p_dir / "script" / f"chapter_{ch.chapter_index:02d}.md").is_file())
    )

    if active_step in ("script_outline", "script_generate"):
        script_status = "berjalan"
        script_label = f"Sedang menulis ({completed_chapters}/{total_chapters} bab)"
    elif total_chapters == 0:
        script_status = "belum"
        script_label = "Belum dibuat"
    elif completed_chapters == total_chapters and total_chapters > 0:
        script_status = "selesai"
        script_label = f"Selesai ({total_chapters} bab)"
    elif completed_chapters > 0:
        script_status = "terhenti"
        script_label = f"Terhenti di {completed_chapters} dari {total_chapters} bab"
    else:
        script_status = "belum"
        script_label = f"Belum dibuat ({total_chapters} bab terencana)"

    # --- Step 2: Suara (Voice / Audio) ---
    total_groups = sum(len(ch.tts_groups) for ch in manifest.chapters)
    completed_groups = 0
    stale_groups = 0
    for ch in manifest.chapters:
        for g in ch.tts_groups:
            if g.status == ItemStatus.stale:
                stale_groups += 1
            if g.audio and g.audio.file_path:
                af = p_dir / g.audio.file_path
                if af.is_file() and af.stat().st_size > 44:
                    completed_groups += 1

    if active_step == "audio_synthesize":
        voice_status = "berjalan"
        voice_label = f"Sedang menyintesis ({completed_groups}/{total_groups} grup)"
    elif total_groups == 0:
        voice_status = "belum"
        voice_label = "Belum ada naskah"
    elif stale_groups > 0:
        voice_status = "perlu_diperbarui"
        voice_label = f"{stale_groups} grup perlu diperbarui"
    elif completed_groups == total_groups and total_groups > 0:
        voice_status = "selesai"
        voice_label = f"Selesai ({total_groups}/{total_groups} grup)"
    elif completed_groups > 0:
        voice_status = "terhenti"
        voice_label = f"Terhenti di {completed_groups} dari {total_groups} grup"
    else:
        voice_status = "belum"
        voice_label = f"Belum disintesis ({total_groups} grup)"

    # --- Step 3: Visual ---
    total_shots = sum(len(ch.shots) for ch in manifest.chapters)
    valid_shots = 0
    stale_shots = 0
    for ch in manifest.chapters:
        for sh in ch.shots:
            if sh.status == ItemStatus.stale:
                stale_shots += 1
            if (sh.rendered_clip and (p_dir / sh.rendered_clip).is_file()) or (sh.asset_path and (p_dir / sh.asset_path).is_file()):
                valid_shots += 1

    if active_step == "visual_generate":
        visual_status = "berjalan"
        visual_label = f"Sedang mencari visual ({valid_shots}/{total_shots} shot)"
    elif total_shots == 0:
        visual_status = "belum"
        visual_label = "Belum dialokasikan"
    elif stale_shots > 0:
        visual_status = "perlu_diperbarui"
        visual_label = f"{stale_shots} shot perlu diperbarui"
    elif valid_shots == total_shots and total_shots > 0:
        visual_status = "selesai"
        visual_label = f"Selesai ({total_shots}/{total_shots} klip)"
    elif valid_shots > 0:
        visual_status = "terhenti"
        visual_label = f"Terhenti di {valid_shots} dari {total_shots} klip"
    else:
        visual_status = "belum"
        visual_label = f"Belum dibuat ({total_shots} shot)"

    # --- Step 4: Render ---
    final_video = manifest.final_video_file
    has_final_video = bool(final_video and (p_dir / final_video).is_file())

    if active_step == "render_final":
        render_status = "berjalan"
        render_label = "Sedang merender video final"
    elif has_final_video and manifest.status != ItemStatus.stale:
        render_status = "selesai"
        render_label = "Selesai (Video final siap)"
    elif has_final_video and manifest.status == ItemStatus.stale:
        render_status = "perlu_diperbarui"
        render_label = "Perlu render ulang"
    else:
        render_status = "belum"
        render_label = "Belum dirender"

    # --- Step 5: CapCut ---
    capcut_info = find_latest_capcut_draft(p_dir, p_id)
    if capcut_info:
        capcut_status = "selesai"
        capcut_label = f"Selesai ({capcut_info['draft_name']})"
    else:
        capcut_status = "belum"
        capcut_label = "Belum diekspor"

    # Determine next recommended step (first uncompleted step)
    steps_order = [
        ("Naskah", script_status),
        ("Suara", voice_status),
        ("Visual", visual_status),
        ("Render", render_status),
        ("CapCut", capcut_status),
    ]

    next_step_raw = "Semua Selesai ✔"
    next_step_name = "Semua Selesai ✔"
    for name, st_val in steps_order:
        if st_val != "selesai":
            next_step_raw = name
            next_step_name = f"Berikutnya: {name}"
            break

    # Preset impactful difference check (C3)
    curr_preset_obj = None
    try:
        curr_preset_obj = get_preset(manifest.preset_id)
    except Exception:
        pass

    preset_diffs = []
    if curr_preset_obj:
        preset_diffs = get_impactful_preset_diffs(manifest.preset_snapshot, curr_preset_obj.model_dump())

    return {
        "project_id": p_id,
        "title": manifest.title,
        "preset_id": manifest.preset_id,
        "preset_inheritance_chain": manifest.preset_inheritance_chain or [manifest.preset_id],
        "sub_niche_focus": manifest.sub_niche_focus,
        "aspect_ratio": manifest.aspect_ratio,
        "duration_minutes": round(estimate_script_duration(manifest.chapters, 140)["estimated_duration_seconds"] / 60.0, 1),
        "steps": {
            "script": {
                "status": script_status,
                "label": script_label,
                "completed": completed_chapters,
                "total": total_chapters,
            },
            "voice": {
                "status": voice_status,
                "label": voice_label,
                "completed": completed_groups,
                "total": total_groups,
            },
            "visual": {
                "status": visual_status,
                "label": visual_label,
                "completed": valid_shots,
                "total": total_shots,
            },
            "render": {
                "status": render_status,
                "label": render_label,
                "final_video_file": final_video if has_final_video else None,
            },
            "capcut": {
                "status": capcut_status,
                "label": capcut_label,
                "draft_info": capcut_info,
            },
        },
        "next_step": next_step_raw,
        "next_step_name": next_step_name,
        "preset_has_impactful_diff": len(preset_diffs) > 0,
        "preset_diffs": preset_diffs,
        "preset_diff_details": preset_diffs,
        "active_job": job_state if is_job_running else None,
        "manifest": manifest,
        "dir": p_dir,
    }
