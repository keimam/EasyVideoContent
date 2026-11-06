"""Visual Contact Sheet Generator for Chapter Review (4.4).

Renders a high-resolution grid image containing:
- Thumbnails for all shots in the chapter
- Badges for source type: [STOCK], [AI], [LOCAL], [FAKE]
- Shot duration, frame count, lock status
- Visual prompt / keywords
Saved to: `storage/longform_projects/<id>/export/contact_sheets/chapter_NN_contact_sheet.jpg`
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

from app.services.longform.schema import Chapter, Shot
from app.services.longform.visual.builder import IMAGE_EXTENSIONS


def _extract_or_load_thumbnail(
    shot: Shot,
    project_dir: Path,
    thumb_w: int = 400,
    thumb_h: int = 225,
) -> Image.Image:
    """Extract a thumbnail from the rendered clip or original asset."""
    # Try rendered clip first
    if shot.rendered_clip:
        clip_path = (project_dir / shot.rendered_clip).resolve()
        if clip_path.is_file():
            # Extract frame at 0.5s via ffmpeg
            cmd = [
                "ffmpeg", "-y",
                "-ss", "0.5",
                "-i", str(clip_path),
                "-vframes", "1",
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-",
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, check=True)
                if res.stdout:
                    import io
                    img = Image.open(io.BytesIO(res.stdout)).convert("RGB")
                    return img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            except Exception:
                pass

    # Try asset path
    if shot.asset_path:
        asset_p = (project_dir / shot.asset_path).resolve()
        if asset_p.is_file() and asset_p.suffix.lower() in IMAGE_EXTENSIONS:
            try:
                img = Image.open(asset_p).convert("RGB")
                return img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
            except Exception:
                pass

    # Fallback placeholder image
    img = Image.new("RGB", (thumb_w, thumb_h), color=(30, 35, 45))
    draw = ImageDraw.Draw(img)
    draw.text((20, thumb_h // 2 - 10), f"No preview: {shot.shot_id}", fill=(180, 180, 180))
    return img


def generate_chapter_contact_sheet(
    chapter: Chapter,
    project_dir: Path | str,
    output_path: Optional[Path | str] = None,
    columns: int = 4,
) -> Path:
    """Generate chapter visual contact sheet image."""
    proj = Path(project_dir).resolve()
    shots = chapter.shots
    if not shots:
        raise ValueError(f"Chapter {chapter.chapter_index} has no shots to generate contact sheet")

    if output_path is None:
        out = proj / "export" / "contact_sheets" / f"chapter_{chapter.chapter_index:02d}_contact_sheet.jpg"
    else:
        out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    thumb_w = 420
    thumb_h = 236
    cell_padding = 16
    footer_h = 82
    cell_w = thumb_w + cell_padding * 2
    cell_h = thumb_h + footer_h + cell_padding * 2

    num_shots = len(shots)
    cols = min(columns, num_shots)
    rows = math.ceil(num_shots / cols)

    header_h = 80
    sheet_w = cols * cell_w
    sheet_h = header_h + rows * cell_h

    sheet = Image.new("RGB", (sheet_w, sheet_h), color=(18, 22, 28))
    draw = ImageDraw.Draw(sheet)

    # Header
    title = f"Chapter {chapter.chapter_index}: {chapter.chapter_title} — Visual Contact Sheet"
    stats = f"Total Shots: {num_shots} | Duration: {chapter.visual_duration_seconds or 0.0:.1f}s | Frames: {chapter.visual_frames or 0}"
    draw.text((24, 20), title, fill=(240, 240, 240))
    draw.text((24, 48), stats, fill=(160, 180, 200))
    draw.line([(0, header_h - 2), (sheet_w, header_h - 2)], fill=(50, 60, 75), width=2)

    for idx, shot in enumerate(shots):
        r = idx // cols
        c = idx % cols
        x0 = c * cell_w + cell_padding
        y0 = header_h + r * cell_h + cell_padding

        # Draw cell background card
        draw.rectangle([x0 - 6, y0 - 6, x0 + thumb_w + 6, y0 + thumb_h + footer_h + 6], fill=(28, 34, 44), outline=(45, 55, 70), width=1)

        # Thumbnail
        thumb = _extract_or_load_thumbnail(shot, proj, thumb_w, thumb_h)
        sheet.paste(thumb, (x0, y0))

        # Badges
        source_badge = "[FAKE]" if shot.is_fake else f"[{shot.actual_source.upper()}]"
        badge_color = (255, 100, 100) if shot.is_fake else ((100, 220, 120) if "stock" in shot.actual_source else (120, 180, 255))
        if shot.locked:
            source_badge += " [LOCKED]"
        if shot.needs_review:
            source_badge += " [NEEDS REVIEW]"
            badge_color = (255, 140, 40)

        # Score snippet (S1)
        score_str = f"score: {shot.relevance_score:.2f}" if shot.relevance_score is not None else ""

        ty = y0 + thumb_h + 8
        header_line = f"{shot.shot_id}  {source_badge}"
        if score_str:
            header_line += f"  ({score_str})"
        draw.text((x0, ty), header_line, fill=badge_color)
        draw.text((x0 + thumb_w - 90, ty), f"{shot.duration_seconds:.2f}s ({shot.target_frames}f)", fill=(220, 220, 100))

        # Prompt snippet
        prompt_txt = shot.visual_prompt or shot.search_query or ""
        if len(prompt_txt) > 55:
            prompt_txt = prompt_txt[:52] + "..."
        draw.text((x0, ty + 24), prompt_txt, fill=(180, 190, 200))
        rend_info = f" | {shot.chosen_rendition}" if shot.chosen_rendition else ""
        draw.text((x0, ty + 42), f"Motion: {shot.motion.value}{rend_info}", fill=(140, 150, 160))

    sheet.save(out, format="JPEG", quality=92)
    return out
