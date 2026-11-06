"""High-level Visual Service orchestrating visual asset acquisition, shot generation, inspection, and locking (4.1 - 4.4).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from app.services.longform.schema import Chapter, ItemStatus, MotionType, ProjectManifest, Shot
from app.services.longform.store import load_manifest, save_manifest
from app.services.longform.shot import (
    compute_shot_hash,
    form_chapter_shots,
)
from app.services.longform.visual.ai_image import AIImageProvider
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualConfigurationError,
    VisualError,
    compute_file_content_hash,
)
from app.services.longform.visual.builder import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ShotClipBuilder,
)
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.contact_sheet import generate_chapter_contact_sheet
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.fake import FakeVisualProvider
from app.services.longform.visual.local import LocalLibraryManager, LocalVisualProvider
from app.services.longform.visual.stock import StockVisualProvider


class VisualService:
    """Orchestrates shot formation, candidate search, asset fetching, and clip rendering."""

    def __init__(self, project_dir: Path | str) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.cache = GlobalMediaCache()
        self.credits_manager = CreditsManager(self.project_dir)
        self.local_library = LocalLibraryManager(self.project_dir)

    def get_provider(
        self,
        provider_name: str,
        manifest: ProjectManifest,
    ) -> BaseVisualProvider:
        """Instantiate visual provider based on name or preset preferences."""
        p_name = provider_name.lower().strip()
        if p_name == "fake":
            return FakeVisualProvider(mode="ai_image")
        elif p_name in ("pexels", "pixabay", "stock"):
            sub = "pexels" if p_name == "stock" else p_name
            return StockVisualProvider(
                provider_name=sub,
                cache=self.cache,
                credits_manager=self.credits_manager,
            )
        elif p_name in ("ai_image", "openai", "ai"):
            template = None
            style = None
            if manifest.preset_snapshot:
                style = manifest.preset_snapshot.visual.get("style") if hasattr(manifest.preset_snapshot, "visual") else None
            return AIImageProvider(prompt_template=template, visual_style=style)
        elif p_name == "local":
            return LocalVisualProvider(self.project_dir)
        else:
            raise ValueError(f"Unknown visual provider: {provider_name}")

    def prepare_shots(
        self,
        manifest: ProjectManifest,
        fps: Optional[int] = None,
        default_source: str = "ai_image",
    ) -> ProjectManifest:
        """Form shots for all chapters in the project."""
        effective_fps = fps or getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
        for ch in manifest.chapters:
            form_chapter_shots(ch, fps=effective_fps, default_source=default_source)
        return manifest

    def fetch_all(
        self,
        manifest: ProjectManifest,
        provider_name: Optional[str] = None,
        allow_fallback: bool = False,
    ) -> ProjectManifest:
        """Search and download/generate visual assets for all pending shots (4.1)."""
        # Ensure shots exist
        has_shots = any(len(ch.shots) > 0 for ch in manifest.chapters)
        if not has_shots:
            effective_fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
            self.prepare_shots(manifest, fps=effective_fps)

        # Track used candidate IDs across project for deduplication
        used_candidate_ids: Set[str] = set()
        for ch in manifest.chapters:
            for s in ch.shots:
                if s.candidates:
                    for c in s.candidates:
                        if c.get("candidate_id"):
                            used_candidate_ids.add(c["candidate_id"])

        for ch in manifest.chapters:
            for shot in ch.shots:
                # Skip locked shots
                if shot.locked:
                    logger.info(f"Skipping locked shot: {shot.shot_id}")
                    continue

                # Check if asset already exists and valid
                if shot.asset_path and shot.asset_content_hash:
                    asset_file = (self.project_dir / shot.asset_path).resolve()
                    if asset_file.is_file() and compute_file_content_hash(asset_file) == shot.asset_content_hash:
                        logger.debug(f"Shot {shot.shot_id} asset already up-to-date, skipping fetch")
                        continue

                # Determine target provider
                target_provider_name = provider_name or shot.requested_source or "ai_image"
                active_provider: BaseVisualProvider
                fallback_reason: Optional[str] = None

                try:
                    active_provider = self.get_provider(target_provider_name, manifest)
                    active_provider.validate_credentials()
                except VisualConfigurationError as conf_err:
                    if not allow_fallback:
                        raise
                    # Try falling back to fake
                    fallback_reason = f"Provider '{target_provider_name}' credentials missing ({conf_err}); fell back to fake provider"
                    logger.warning(f"Shot {shot.shot_id}: {fallback_reason}")
                    active_provider = FakeVisualProvider(mode=target_provider_name)
                except Exception as exc:
                    if not allow_fallback:
                        raise
                    fallback_reason = f"Provider '{target_provider_name}' error ({exc}); fell back to fake provider"
                    logger.warning(f"Shot {shot.shot_id}: {fallback_reason}")
                    active_provider = FakeVisualProvider(mode=target_provider_name)

                # Search candidates
                query = shot.search_query or shot.visual_prompt or "cinematic historical scene"
                candidates = active_provider.search_candidates(
                    query=query,
                    target_duration=shot.duration_seconds,
                    limit=3,
                    excluded_ids=used_candidate_ids,
                )

                if not candidates:
                    if allow_fallback and not active_provider.is_fake:
                        fallback_reason = f"Provider '{active_provider.name}' found 0 candidates for query; fell back to fake"
                        active_provider = FakeVisualProvider(mode=target_provider_name)
                        candidates = active_provider.search_candidates(
                            query=query,
                            target_duration=shot.duration_seconds,
                            limit=3,
                        )
                    else:
                        raise VisualError(f"No candidates found for shot {shot.shot_id} with query '{query}'")

                # Store candidate options
                shot.candidates = [c.to_dict() for c in candidates]
                selected_candidate = candidates[0]
                used_candidate_ids.add(selected_candidate.candidate_id)

                # Fetch selected asset
                ext = ".mp4" if selected_candidate.source_type == "stock_video" else ".jpg"
                rel_dest = f"assets/visual/shots/{shot.shot_id}_asset{ext}"
                dest_file = self.project_dir / rel_dest

                logger.info(f"Fetching asset for {shot.shot_id} via {active_provider.name}...")
                asset = active_provider.fetch_asset(selected_candidate, dest_file)

                # Update shot metadata
                shot.asset_path = rel_dest
                shot.asset_content_hash = asset.content_hash
                shot.actual_source = active_provider.name
                shot.is_fake = active_provider.is_fake
                shot.fallback_reason = fallback_reason
                shot.status = ItemStatus.pending

        return manifest

    def build_all(
        self,
        manifest: ProjectManifest,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Build rendered video clips for all shots in manifest (4.3)."""
        fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
        builder = ShotClipBuilder(fps=fps)
        built_count = 0
        skipped_count = 0
        total_time = 0.0

        for ch in manifest.chapters:
            for shot in ch.shots:
                if not shot.asset_path:
                    raise VisualError(f"Cannot build shot {shot.shot_id}: asset_path is missing. Run visual fetch first.")

                asset_file = (self.project_dir / shot.asset_path).resolve()
                if not asset_file.is_file():
                    raise FileNotFoundError(f"Shot asset file missing on disk: {asset_file}")

                # Compute expected shot hash
                expected_hash = compute_shot_hash(
                    shot_id=shot.shot_id,
                    duration_seconds=shot.duration_seconds,
                    target_frames=shot.target_frames,
                    motion=shot.motion.value,
                    asset_content_hash=shot.asset_content_hash,
                    fps=fps,
                )

                clip_rel = f"assets/visual/shots/{shot.shot_id}.mp4"
                clip_path = (self.project_dir / clip_rel).resolve()

                # Check if already completed and hash matches
                if shot.shot_hash == expected_hash and clip_path.is_file() and shot.status == ItemStatus.completed:
                    skipped_count += 1
                    continue

                logger.info(f"Building shot clip {shot.shot_id} ({shot.target_frames} frames, {shot.motion.value})...")
                _, elapsed = builder.build_shot_clip(
                    asset_path=asset_file,
                    output_path=clip_path,
                    motion=shot.motion,
                    target_frames=shot.target_frames,
                )
                built_count += 1
                total_time += elapsed

                shot.rendered_clip = clip_rel
                shot.shot_hash = expected_hash
                shot.status = ItemStatus.completed

            # Update chapter duration & frames
            ch.visual_duration_seconds = round(sum(s.duration_seconds for s in ch.shots), 3)
            ch.visual_frames = sum(s.target_frames for s in ch.shots)

        avg_speed = total_time / built_count if built_count > 0 else 0.0
        stats = {
            "built_count": built_count,
            "skipped_count": skipped_count,
            "total_build_seconds": round(total_time, 2),
            "average_seconds_per_clip": round(avg_speed, 2),
        }
        return manifest, stats

    def generate_preview(
        self,
        manifest: ProjectManifest,
        chapter_index: int,
    ) -> Path:
        """Concatenate all rendered shot clips in a chapter with chapter audio (4.4)."""
        ch = next((c for c in manifest.chapters if c.chapter_index == chapter_index), None)
        if not ch:
            raise ValueError(f"Chapter {chapter_index} not found in project")

        if not ch.shots:
            raise ValueError(f"Chapter {chapter_index} has no shots")

        for shot in ch.shots:
            if not shot.rendered_clip:
                raise VisualError(f"Shot {shot.shot_id} has not been rendered. Run visual build first.")
            p = (self.project_dir / shot.rendered_clip).resolve()
            if not p.is_file():
                raise FileNotFoundError(f"Rendered clip file missing: {p}")

        # Audio file
        if not ch.audio_file:
            raise VisualError(f"Chapter {chapter_index} has no audio_file. Run audio synthesize first.")
        audio_path = (self.project_dir / ch.audio_file).resolve()
        if not audio_path.is_file():
            raise FileNotFoundError(f"Chapter audio file missing: {audio_path}")

        preview_dir = self.project_dir / "export" / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_file = preview_dir / f"chapter_{chapter_index:02d}_visual.mp4"

        # Write concat list
        concat_txt = preview_dir / f"concat_ch{chapter_index:02d}.txt"
        lines = []
        for shot in ch.shots:
            clip_full = (self.project_dir / shot.rendered_clip).resolve()
            # Escape path for ffmpeg concat demuxer
            safe_str = str(clip_full).replace("\\", "/")
            lines.append(f"file '{safe_str}'")

        concat_txt.write_text("\n".join(lines), encoding="utf-8")

        # Run FFmpeg concat with audio
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_txt),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            str(preview_file),
        ]

        logger.info(f"Generating visual preview for chapter {chapter_index}...")
        res = subprocess.run(cmd, capture_output=True, text=True)
        concat_txt.unlink(missing_ok=True)

        if res.returncode != 0 or not preview_file.is_file():
            raise VisualError(f"FFmpeg visual preview concat failed: {res.stderr[-500:]}")

        return preview_file

    def replace_shot(
        self,
        manifest: ProjectManifest,
        shot_id: str,
        file_path: Optional[Path | str] = None,
        candidate_index: Optional[int] = None,
        provider_name: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> Shot:
        """Replace visual asset for a shot and invalidate its rendered clip (4.4)."""
        target_shot: Optional[Shot] = None
        for ch in manifest.chapters:
            for s in ch.shots:
                if s.shot_id == shot_id:
                    target_shot = s
                    break
            if target_shot:
                break

        if not target_shot:
            raise ValueError(f"Shot {shot_id} not found in project")

        if file_path:
            src = Path(file_path).resolve()
            if not src.is_file():
                raise FileNotFoundError(f"Replacement file not found: {src}")
            ext = src.suffix.lower()
            rel_dest = f"assets/visual/shots/{shot_id}_asset{ext}"
            dest = self.project_dir / rel_dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

            target_shot.asset_path = rel_dest
            target_shot.asset_content_hash = compute_file_content_hash(dest)
            target_shot.actual_source = "local"
            target_shot.status = ItemStatus.pending
            target_shot.shot_hash = None  # invalidate
            return target_shot

        if candidate_index is not None:
            if not target_shot.candidates or candidate_index >= len(target_shot.candidates):
                raise ValueError(f"Candidate index {candidate_index} out of range for shot {shot_id}")
            cand_dict = target_shot.candidates[candidate_index]
            candidate = VisualCandidate.from_dict(cand_dict)
            provider = self.get_provider(candidate.provider, manifest)
            ext = ".mp4" if candidate.source_type == "stock_video" else ".jpg"
            rel_dest = f"assets/visual/shots/{shot_id}_asset{ext}"
            dest = self.project_dir / rel_dest
            asset = provider.fetch_asset(candidate, dest)

            target_shot.asset_path = rel_dest
            target_shot.asset_content_hash = asset.content_hash
            target_shot.actual_source = candidate.provider
            target_shot.status = ItemStatus.pending
            target_shot.shot_hash = None
            return target_shot

        if provider_name or prompt:
            prov_name = provider_name or target_shot.requested_source
            prov = self.get_provider(prov_name, manifest)
            q = prompt or target_shot.search_query or target_shot.visual_prompt
            cands = prov.search_candidates(q, target_shot.duration_seconds, limit=3)
            if not cands:
                raise VisualError(f"No candidates found for prompt '{q}'")
            target_shot.candidates = [c.to_dict() for c in cands]
            sel = cands[0]
            ext = ".mp4" if sel.source_type == "stock_video" else ".jpg"
            rel_dest = f"assets/visual/shots/{shot_id}_asset{ext}"
            dest = self.project_dir / rel_dest
            asset = prov.fetch_asset(sel, dest)

            target_shot.asset_path = rel_dest
            target_shot.asset_content_hash = asset.content_hash
            target_shot.actual_source = prov_name
            target_shot.is_fake = prov.is_fake
            target_shot.status = ItemStatus.pending
            target_shot.shot_hash = None
            return target_shot

        raise ValueError("Must provide --file, --candidate, or --provider/--prompt for replace")

    def set_lock(
        self,
        manifest: ProjectManifest,
        shot_id: str,
        locked: bool = True,
    ) -> Shot:
        """Lock or unlock a shot to protect it from automated overwrites (4.4)."""
        target_shot: Optional[Shot] = None
        for ch in manifest.chapters:
            for s in ch.shots:
                if s.shot_id == shot_id:
                    target_shot = s
                    break
            if target_shot:
                break

        if not target_shot:
            raise ValueError(f"Shot {shot_id} not found in project")

        target_shot.locked = locked
        return target_shot
