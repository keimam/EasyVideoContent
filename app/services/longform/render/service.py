"""Render coordination service for chapter renders and final assembly (Stage 5).

Provides high-level methods to render single chapters, resume/render all chapters,
assemble final video, and query render status.
"""
from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.longform.hashing import compute_chapter_hash, compute_final_concat_hash
from app.services.longform.planner import compute_plan
from app.services.longform.render.chapter_render import ChapterRenderer
from app.services.longform.render.final_concat import FinalConcatenator
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest

logger = logging.getLogger(__name__)


class RenderService:
    """Coordinates chapter rendering, resume execution, and final video assembly (5.1-5.5)."""

    def __init__(self, project_dir: Path | str):
        self.project_dir = Path(project_dir).resolve()
        self.chapter_renderer = ChapterRenderer(self.project_dir)
        self.final_concatenator = FinalConcatenator(self.project_dir)

    def render_chapter(
        self,
        manifest: ProjectManifest,
        chapter_index: int,
        force: bool = False,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Render a single chapter MP4 (5.1, 5.5)."""
        ch = next((c for c in manifest.chapters if c.chapter_index == chapter_index), None)
        if not ch:
            raise ValueError(f"Chapter {chapter_index} not found in project '{manifest.project_id}'")

        preset_snap = manifest.preset_snapshot or {}
        expected_hash = compute_chapter_hash(
            chapter=ch,
            encoder_settings=manifest.encoder_settings,
            subtitle_style=preset_snap.get("subtitle_style", {}),
            preset_snapshot=preset_snap,
            pipeline_version=manifest.pipeline_version,
            sub_niche_focus=manifest.sub_niche_focus or "",
        )

        out_file = ch.output_file and (self.project_dir / ch.output_file).is_file()
        if not force and out_file and ch.chapter_hash == expected_hash and ch.status == ItemStatus.completed:
            logger.info(f"Chapter {chapter_index} is up-to-date, skipping render")
            return manifest, {
                "chapter_index": chapter_index,
                "status": "skipped",
                "output_file": ch.output_file,
            }

        mp4_path, stats = self.chapter_renderer.render_chapter(manifest, chapter_index, force=force)
        stats["status"] = "rendered"
        return manifest, stats

    def render_all(
        self,
        manifest: ProjectManifest,
        no_bgm: bool = False,
        force: bool = False,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Resume-aware render of all chapters followed by final video concat (5.1, 5.3, 5.5)."""
        total_chapters = len(manifest.chapters)
        chapter_stats: List[Dict[str, Any]] = []

        rendered_count = 0
        skipped_count = 0
        t0 = time.perf_counter()

        # Render chapters in sequence (resume per chapter)
        for ch in sorted(manifest.chapters, key=lambda c: c.chapter_index):
            manifest, c_stats = self.render_chapter(manifest, ch.chapter_index, force=force)
            if c_stats.get("status") == "rendered":
                rendered_count += 1
            else:
                skipped_count += 1
            chapter_stats.append(c_stats)

        # Final video assembly
        final_mp4, final_stats = self.final_concatenator.concatenate_final_video(
            manifest=manifest,
            no_bgm=no_bgm,
        )

        total_elapsed = time.perf_counter() - t0
        summary = {
            "total_chapters": total_chapters,
            "chapters_rendered": rendered_count,
            "chapters_skipped": skipped_count,
            "chapter_stats": chapter_stats,
            "final_stats": final_stats,
            "total_elapsed_seconds": round(total_elapsed, 2),
        }
        return manifest, summary

    def render_final(
        self,
        manifest: ProjectManifest,
        no_bgm: bool = False,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Assemble final video directly from existing rendered chapters (5.3, 5.5)."""
        # Verify all chapters are rendered
        for ch in manifest.chapters:
            if ch.status != ItemStatus.completed or not ch.output_file or not (self.project_dir / ch.output_file).is_file():
                raise ValueError(
                    f"Chapter {ch.chapter_index} is not completed or missing video file. "
                    f"Run 'render chapter {manifest.project_id} --chapter {ch.chapter_index}' first."
                )

        final_mp4, stats = self.final_concatenator.concatenate_final_video(
            manifest=manifest,
            no_bgm=no_bgm,
        )
        return manifest, stats

    def get_render_status(self, manifest: ProjectManifest) -> Dict[str, Any]:
        """Query render status for all chapters and final video (5.5, R2)."""
        plan = compute_plan(manifest, self.project_dir)
        ch_units = [u for u in plan.all_units if u.unit_type == "chapter_render"]
        final_unit = next((u for u in plan.all_units if u.unit_type == "final_concat"), None)

        status_items = []
        for u in ch_units:
            ch = next((c for c in manifest.chapters if c.chapter_index == u.chapter_index), None)
            status_items.append({
                "chapter_index": u.chapter_index,
                "title": ch.chapter_title if ch else "Unknown",
                "status": u.status.value,
                "reason": u.reason,
                "output_file": u.target_file,
                "expected_hash": u.expected_hash,
                "current_hash": u.current_hash,
            })

        # Font preflight check (R2)
        font_name = manifest.preset_snapshot.get("subtitle_style", {}).get("font_name", "BeVietnamPro-Bold")
        fonts_dir = self.project_dir / "resource" / "fonts"
        if not fonts_dir.is_dir():
            fonts_dir = Path("resource/fonts").resolve()
        from app.services.longform.render.subtitle import check_font_availability
        font_available, font_path = check_font_availability(font_name, fonts_dir)

        return {
            "project_id": manifest.project_id,
            "chapters": status_items,
            "final_status": final_unit.status.value if final_unit else "unknown",
            "final_reason": final_unit.reason if final_unit else "",
            "final_video_file": manifest.final_video_file,
            "font_status": {
                "font_name": font_name,
                "available": font_available,
                "path": str(font_path) if font_path else None,
            },
        }

    def preview_subtitles(
        self,
        manifest: ProjectManifest,
        chapter_index: int,
        timestamps_seconds: List[float],
        font_size: Optional[int] = None,
        margin_v: Optional[int] = None,
        output_dir: Optional[Path] = None,
    ) -> List[Path]:
        """Generate burned subtitle PNG preview frames at given timestamps without full chapter re-render (R1)."""
        from app.services.longform.render.subtitle import generate_subtitle_preview_frames
        return generate_subtitle_preview_frames(
            project_dir=self.project_dir,
            manifest=manifest,
            chapter_index=chapter_index,
            timestamps_seconds=timestamps_seconds,
            font_size_override=font_size,
            margin_v_override=margin_v,
            output_dir=output_dir,
        )
