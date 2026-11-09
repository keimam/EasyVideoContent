"""AI Image generator adapter for longform projects via OpenAI DALL-E / compatible APIs (4.1).

Wraps upstream AI image generation function:
- app/services/material.py:1801 (generate_images_openai)

Features:
- Prompt compilation incorporating preset template, visual prompt, and visual style.
- Landscape 16:9 generation (1792x1024 or 1920x1080).
- Fail-fast configuration validation.
- Output file placement in project structure with deterministic SHA-256 hash.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from app.config import config
from app.models.schema import VideoAspect
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualConfigurationError,
    VisualError,
    compute_file_content_hash,
)
from app.services.material import generate_images_openai


class AIImageProvider(BaseVisualProvider):
    """OpenAI / compatible image generation adapter (4.1)."""

    def __init__(
        self,
        prompt_template: Optional[str] = None,
        visual_style: Optional[str] = None,
    ) -> None:
        self.prompt_template = prompt_template or "{visual_prompt}, {visual_style}, cinematic 16:9, highly detailed, photorealistic"
        self.visual_style = visual_style or "cinematic documentary style, dramatic lighting"

    @property
    def name(self) -> str:
        return "ai_image"

    @property
    def is_fake(self) -> bool:
        return False

    def validate_credentials(self) -> None:
        """Validate presence of openai_image_base_url and openai_image_model in config."""
        base_url = str(config.app.get("openai_image_base_url", "") or "").strip()
        model = str(config.app.get("openai_image_model", "") or "").strip()
        if not base_url or not model:
            missing = []
            if not base_url:
                missing.append("openai_image_base_url")
            if not model:
                missing.append("openai_image_model")
            raise VisualConfigurationError(
                f"AI image provider credentials missing: {', '.join(missing)}. "
                f"Please configure them in config.toml ({config.config_file}) or WebUI Settings."
            )

    def build_full_prompt(self, visual_prompt: str) -> str:
        """Interpolate segment prompt into preset image prompt template."""
        clean_prompt = visual_prompt.strip()
        clean_style = self.visual_style.strip()
        try:
            return self.prompt_template.format(
                visual_prompt=clean_prompt,
                visual_style=clean_style,
            )
        except Exception:
            return f"{clean_prompt}, {clean_style}"

    def search_candidates(
        self,
        query: str,
        target_duration: float,
        limit: int = 1,
        excluded_ids: Optional[Set[str]] = None,
    ) -> List[VisualCandidate]:
        """Produce descriptor candidates for AI image generation."""
        self.validate_credentials()
        full_prompt = self.build_full_prompt(query)
        prompt_hash = hashlib.sha256(full_prompt.encode("utf-8")).hexdigest()[:12]
        cand_id = f"ai_img_{prompt_hash}"

        return [
            VisualCandidate(
                candidate_id=cand_id,
                provider="openai_image",
                source_type="ai_image",
                url=None,
                local_path=None,
                author="OpenAI DALL-E / Compatible",
                author_url="https://openai.com",
                source_url=None,
                license="Generated AI Content",
                width=1792,
                height=1024,
                duration_seconds=target_duration,
                source_info={
                    "query": query,
                    "full_prompt": full_prompt,
                    "model": config.app.get("openai_image_model", "dall-e-3"),
                },
            )
        ]

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        """Call generate_images_openai, save image to dest_path, and compute hash."""
        self.validate_credentials()
        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        full_prompt = candidate.source_info.get("full_prompt") or self.build_full_prompt(candidate.candidate_id)
        target_duration = max(int(candidate.duration_seconds), 2)

        # Call upstream generator: app/services/material.py:1801
        logger.info(f"Generating AI image: prompt={full_prompt[:80]}...")
        results = generate_images_openai(
            search_term=full_prompt,
            minimum_duration=target_duration,
            video_aspect=VideoAspect.landscape,
            save_dir=str(dest.parent),
        )

        if not results or not results[0].url:
            raise VisualError(f"AI image generation failed to produce an image for candidate {candidate.candidate_id}")

        generated_file = Path(results[0].url).resolve()
        if not generated_file.is_file() or generated_file.stat().st_size == 0:
            raise VisualError(f"Generated AI image file not found or empty: {generated_file}")

        if generated_file != dest:
            shutil.copyfile(generated_file, dest)
            try:
                generated_file.unlink(missing_ok=True)
            except Exception:
                pass

        content_hash = compute_file_content_hash(dest)
        rendition = results[0].source_info.get("rendition", {}) if hasattr(results[0], "source_info") else {}

        return VisualAsset(
            asset_id=candidate.candidate_id,
            provider="openai_image",
            source_type="ai_image",
            local_path=dest,
            content_hash=content_hash,
            author="OpenAI DALL-E / Compatible",
            author_url="https://openai.com",
            source_url=None,
            license="Generated AI Content",
            width=int(rendition.get("width") or 1792),
            height=int(rendition.get("height") or 1024),
            duration_seconds=candidate.duration_seconds,
        )
