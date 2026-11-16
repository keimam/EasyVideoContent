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
from app.services.material import (
    generate_images_codex,
    generate_images_openai,
    is_openai_image_enabled,
)


def _is_codex_image_ready() -> bool:
    """Return True when Codex OAuth is connected and codex_image_model_name is set."""
    model = str(config.app.get("codex_image_model_name", "") or "").strip()
    if not model:
        return False
    try:
        from app.services import codex_oauth

        return bool(codex_oauth.account_status())
    except Exception:
        return False


def get_ai_image_readiness(preferred_backend: Optional[str] = None) -> Dict[str, Any]:
    """Return active MPT AI Image provider readiness (OpenAI Images and/or Codex Image)."""
    configured: List[str] = []
    if is_openai_image_enabled(config.app):
        configured.append("openai_image")
    if _is_codex_image_ready():
        configured.append("codex_image")

    pref = (preferred_backend or "").strip().lower()
    cfg_source = str(config.app.get("video_source", "") or "").strip().lower()

    active = ""
    if pref in configured:
        active = pref
    elif cfg_source in configured:
        active = cfg_source
    elif configured:
        active = configured[0]

    label_map = {
        "openai_image": "OpenAI Images",
        "codex_image": "Codex Image",
    }
    model = ""
    if active == "openai_image":
        model = str(config.app.get("openai_image_model", "") or "").strip()
    elif active == "codex_image":
        model = str(config.app.get("codex_image_model_name", "") or "").strip()

    return {
        "ready": bool(active),
        "backend": active,
        "backend_label": label_map.get(active, ""),
        "model": model,
        "configured_backends": configured,
    }


def estimate_ai_image_usage(manifest: Any, project_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Estimate number of AI images needed for unfinished shots/segments before running."""
    chapters = getattr(manifest, "chapters", None) or []
    pending_shots = 0
    total_shots = 0
    for ch in chapters:
        shots = getattr(ch, "shots", None) or []
        for s in shots:
            total_shots += 1
            asset_rel = getattr(s, "asset_path", None)
            has_asset = bool(project_dir and asset_rel and (project_dir / asset_rel).is_file())
            if not has_asset:
                pending_shots += 1

    if total_shots == 0:
        # Shots not yet formed: estimate ~1 visual shot per segment (or group if segments empty)
        est = 0
        for ch in chapters:
            for grp in getattr(ch, "tts_groups", None) or []:
                segs = getattr(grp, "segments", None) or []
                est += max(len(segs), 1)
        estimated_count = est
    else:
        estimated_count = pending_shots

    info = get_ai_image_readiness()
    return {
        "estimated_count": int(estimated_count),
        "backend": info["backend"],
        "backend_label": info["backend_label"],
        "model": info["model"],
        "has_unit_price": False,
    }


class AIImageProvider(BaseVisualProvider):
    """Unified MPT AI image generation adapter (OpenAI Images & Codex Image) (4.1, 8B.1)."""

    def __init__(
        self,
        prompt_template: Optional[str] = None,
        visual_style: Optional[str] = None,
        backend: Optional[str] = None,
    ) -> None:
        self.prompt_template = prompt_template or "{visual_prompt}, {visual_style}, cinematic 16:9, highly detailed, photorealistic"
        self.visual_style = visual_style or "cinematic documentary style, dramatic lighting"
        self.preferred_backend = (backend or "").strip().lower() or None

    def _resolve_backend(self) -> str:
        readiness = get_ai_image_readiness(self.preferred_backend)
        if readiness["ready"]:
            return readiness["backend"]
        return self.preferred_backend or "openai_image"

    @property
    def name(self) -> str:
        return "ai_image"

    @property
    def is_fake(self) -> bool:
        return False

    def validate_credentials(self) -> None:
        """Validate that either MPT OpenAI Images or Codex Image is configured and ready."""
        readiness = get_ai_image_readiness(self.preferred_backend)
        if readiness["ready"]:
            return

        if self.preferred_backend == "codex_image":
            raise VisualConfigurationError(
                "AI image provider credentials missing: Codex OAuth login and codex_image_model_name are required."
            )

        base_url = str(config.app.get("openai_image_base_url", "") or "").strip()
        model = str(config.app.get("openai_image_model", "") or "").strip()
        missing = []
        if not base_url:
            missing.append("openai_image_base_url")
        if not model:
            missing.append("openai_image_model")
        if not missing:
            missing.append("codex_image_model_name / openai_image")
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
        backend = self._resolve_backend()
        full_prompt = self.build_full_prompt(query)
        prompt_hash = hashlib.sha256(full_prompt.encode("utf-8")).hexdigest()[:12]
        cand_id = f"ai_img_{prompt_hash}"

        model_name = (
            config.app.get("codex_image_model_name", "")
            if backend == "codex_image"
            else config.app.get("openai_image_model", "dall-e-3")
        )
        author_label = "OpenAI Codex Image" if backend == "codex_image" else "OpenAI DALL-E / Compatible"

        return [
            VisualCandidate(
                candidate_id=cand_id,
                provider=backend,
                source_type="ai_image",
                url=None,
                local_path=None,
                author=author_label,
                author_url="https://openai.com",
                source_url=None,
                license="Generated AI Content",
                width=1792,
                height=1024,
                duration_seconds=target_duration,
                source_info={
                    "query": query,
                    "full_prompt": full_prompt,
                    "backend": backend,
                    "model": model_name,
                },
            )
        ]

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        """Call active MPT AI image generator (OpenAI or Codex), save to dest_path, and hash."""
        self.validate_credentials()
        backend = str((candidate.source_info or {}).get("backend") or self._resolve_backend())
        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        full_prompt = candidate.source_info.get("full_prompt") or self.build_full_prompt(candidate.candidate_id)
        target_duration = max(int(candidate.duration_seconds), 2)

        logger.info(f"Generating AI image via {backend}: prompt={full_prompt[:80]}...")
        if backend == "codex_image":
            results = generate_images_codex(
                search_term=full_prompt,
                minimum_duration=target_duration,
                video_aspect=VideoAspect.landscape,
                save_dir=str(dest.parent),
            )
            author_label = "OpenAI Codex Image"
        else:
            results = generate_images_openai(
                search_term=full_prompt,
                minimum_duration=target_duration,
                video_aspect=VideoAspect.landscape,
                save_dir=str(dest.parent),
            )
            author_label = "OpenAI DALL-E / Compatible"

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
            provider=backend,
            source_type="ai_image",
            local_path=dest,
            content_hash=content_hash,
            author=author_label,
            author_url="https://openai.com",
            source_url=None,
            license="Generated AI Content",
            width=int(rendition.get("width") or 1792),
            height=int(rendition.get("height") or 1024),
            duration_seconds=candidate.duration_seconds,
        )
