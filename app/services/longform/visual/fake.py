"""Deterministic Fake Visual Provider for Offline Development and Testing (4.1).

Generates synthetic Pillow images and short FFmpeg test clips labeled with
explicit '[FAKE]' watermark badges, prompt text, and shot metadata to allow
full pipeline execution without external API keys or network calls.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    compute_file_content_hash,
)
from app.services.longform.visual.stock import (
    extract_query_keywords,
    score_candidate_relevance,
)


class FakeVisualProvider(BaseVisualProvider):
    """Generates synthetic labeled test images and video clips (4.1, S1)."""

    def __init__(
        self,
        mode: str = "ai_image",
        mock_candidates: Optional[List[VisualCandidate]] = None,
        mock_tags_pool: Optional[List[List[str]]] = None,
    ) -> None:
        self.mode = mode.lower()  # "ai_image" or "stock_video"
        self.mock_candidates = mock_candidates
        self.mock_tags_pool = mock_tags_pool

    @property
    def name(self) -> str:
        return "fake"

    @property
    def is_fake(self) -> bool:
        return True

    def validate_credentials(self) -> None:
        """Fake provider never requires external API credentials."""
        pass

    def search_candidates(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[set[str]] = None,
        required_subject_terms: Optional[List[str]] = None,
        blocked_terms: Optional[List[str]] = None,
        visual_keywords: Optional[List[str]] = None,
    ) -> List[VisualCandidate]:
        """Generate deterministic fake candidates for a query, applying S1 relevance rules."""
        excluded = excluded_ids or set()
        candidates: List[VisualCandidate] = []
        source_type = "stock_video" if "stock" in self.mode or "video" in self.mode else "ai_image"

        if self.mock_candidates:
            raw_cands = [c for c in self.mock_candidates if c.candidate_id not in excluded]
            for c in raw_cands:
                is_valid, score, _ = score_candidate_relevance(
                    candidate_tags=c.tags,
                    search_query=query,
                    visual_keywords=visual_keywords,
                    required_subject_terms=required_subject_terms,
                    blocked_terms=blocked_terms,
                )
                if is_valid:
                    c.relevance_score = score
                    candidates.append(c)
            candidates.sort(key=lambda c: c.relevance_score, reverse=True)
            return candidates[:limit]

        i = 1
        base_hash = abs(hash(query)) % 100000
        pool_idx = 0
        while len(candidates) < limit and i <= 50:
            cand_id = f"fake_{source_type}_{base_hash}_{i}"
            i += 1
            if cand_id in excluded:
                continue

            # Assign tags: from pool if available, otherwise from query
            if self.mock_tags_pool:
                tags = list(self.mock_tags_pool[pool_idx % len(self.mock_tags_pool)])
                pool_idx += 1
            else:
                tags = extract_query_keywords(query)
                if not tags:
                    tags = ["scenic", "nature"]

            # Filter & score
            is_valid, score, _ = score_candidate_relevance(
                candidate_tags=tags,
                search_query=query,
                visual_keywords=visual_keywords,
                required_subject_terms=required_subject_terms,
                blocked_terms=blocked_terms,
            )
            if not is_valid:
                continue

            candidates.append(
                VisualCandidate(
                    candidate_id=cand_id,
                    provider="fake",
                    source_type=source_type,
                    url=None,
                    local_path=None,
                    author="Fake Test Generator",
                    author_url="https://local.test/synthetic",
                    source_url=f"https://local.test/synthetic/{cand_id}",
                    license="Synthetic Test License",
                    width=1920,
                    height=1080,
                    duration_seconds=max(target_duration, 5.0) if source_type == "stock_video" else 0.0,
                    source_info={"query": query, "candidate_num": i},
                    tags=tags,
                    relevance_score=score,
                    rendition_name="fake_1080p (1920x1080)",
                )
            )

        candidates.sort(key=lambda c: c.relevance_score, reverse=True)
        return candidates[:limit]

    def create_synthetic_image(
        self,
        dest_path: Path,
        prompt: str,
        shot_id: str = "shot",
        width: int = 1920,
        height: int = 1080,
    ) -> Path:
        """Create a stylized 1080p synthetic image with clear text overlays."""
        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.parent / f"{dest.name}.tmp.{uuid.uuid4().hex}.jpg"

        # Background color: deep blue-gray gradient or dark slate
        bg_color = (24, 32, 47)
        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)

        # Draw decorative border
        draw.rectangle([40, 40, width - 40, height - 40], outline=(70, 90, 120), width=4)
        draw.rectangle([60, 60, width - 60, height - 60], outline=(40, 60, 80), width=2)

        # Draw FAKE watermarks
        try:
            # Try default or standard font
            font_title = ImageFont.load_default()
            font_body = ImageFont.load_default()
        except Exception:
            font_title = None
            font_body = None

        draw.text((100, 100), "[FAKE TEST ASSET]", fill=(240, 70, 70))
        draw.text((100, 140), f"Shot: {shot_id}", fill=(200, 220, 240))
        draw.text((100, 180), f"Mode: {self.mode.upper()}", fill=(180, 200, 220))
        draw.text((100, 220), f"Resolution: {width}x{height}", fill=(160, 180, 200))

        # Wrap prompt text across lines
        words = prompt.split()
        lines = []
        cur_line = []
        for w in words:
            cur_line.append(w)
            if len(" ".join(cur_line)) > 60:
                lines.append(" ".join(cur_line))
                cur_line = []
        if cur_line:
            lines.append(" ".join(cur_line))

        y = 300
        draw.text((100, y), "Prompt / Search Query:", fill=(255, 215, 0))
        y += 40
        for line in lines[:8]:
            draw.text((120, y), f"\"{line}\"", fill=(240, 240, 240))
            y += 35

        img.save(tmp, format="JPEG", quality=90)
        os.replace(tmp, dest)
        return dest

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        """Materialize synthetic candidate to local JPEG or MP4 test file."""
        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        query = candidate.source_info.get("query", "Synthetic visual scene")

        if candidate.source_type == "stock_video":
            # Generate intermediate JPEG then convert to looping test MP4
            tmp_jpg = dest.parent / f"{dest.stem}.tmp_frame.{uuid.uuid4().hex}.jpg"
            self.create_synthetic_image(tmp_jpg, prompt=query, shot_id=candidate.candidate_id)

            dur = max(float(candidate.duration_seconds) + 3.0, 10.0)
            cmd = [
                "ffmpeg", "-y",
                "-framerate", "25",
                "-loop", "1",
                "-i", str(tmp_jpg),
                "-t", str(dur),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-r", "25",
                str(dest),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            tmp_jpg.unlink(missing_ok=True)
            if res.returncode != 0 or not dest.is_file():
                raise RuntimeError(f"Failed to generate synthetic fake video: {res.stderr[-300:]}")
        else:
            self.create_synthetic_image(dest, prompt=query, shot_id=candidate.candidate_id)

        c_hash = compute_file_content_hash(dest)
        return VisualAsset(
            asset_id=candidate.candidate_id,
            provider="fake",
            source_type=candidate.source_type,
            local_path=dest,
            content_hash=c_hash,
            author="Fake Test Generator",
            author_url="https://local.test/synthetic",
            source_url=f"https://local.test/synthetic/{candidate.candidate_id}",
            license=candidate.license,
            width=1920,
            height=1080,
            duration_seconds=candidate.duration_seconds,
        )
