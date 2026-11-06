"""Local media library provider for longform projects (4.1).

Enforces Rule K7:
All media added via `visual library add <id> --folder <path>` are copied
directly into `storage/longform_projects/<id>/assets/visual/library/`.
No external filesystem paths outside the project root are ever referenced.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualError,
    compute_file_content_hash,
)

SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ALL_SUPPORTED_EXTS = SUPPORTED_VIDEO_EXTS | SUPPORTED_IMAGE_EXTS


class LocalLibraryManager:
    """Manages the project-local assets/visual/library/ directory."""

    def __init__(self, project_dir: Path | str) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.library_dir = self.project_dir / "assets" / "visual" / "library"
        self.library_dir.mkdir(parents=True, exist_ok=True)

    def add_from_folder(self, source_folder: Path | str) -> List[Dict[str, Any]]:
        """Scan source folder and copy all supported media files into project library."""
        src = Path(source_folder).resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"Source folder does not exist: {src}")

        added_assets: List[Dict[str, Any]] = []

        for item in sorted(src.iterdir()):
            if not item.is_file():
                continue
            ext = item.suffix.lower()
            if ext not in ALL_SUPPORTED_EXTS:
                continue

            target_path = self.library_dir / item.name
            # If target already exists and has identical size, check hash or overwrite
            shutil.copyfile(item, target_path)
            c_hash = compute_file_content_hash(target_path)
            asset_type = "stock_video" if ext in SUPPORTED_VIDEO_EXTS else "stock_image"

            added_assets.append({
                "filename": item.name,
                "relative_path": f"assets/visual/library/{item.name}",
                "asset_type": asset_type,
                "size_bytes": target_path.stat().st_size,
                "content_hash": c_hash,
            })
            logger.info(f"Copied local asset into library: {item.name} ({asset_type})")

        return added_assets

    def list_assets(self) -> List[Path]:
        """List all valid media files in the project library."""
        if not self.library_dir.is_dir():
            return []
        return [
            p for p in sorted(self.library_dir.iterdir())
            if p.is_file() and p.suffix.lower() in ALL_SUPPORTED_EXTS
        ]


class LocalVisualProvider(BaseVisualProvider):
    """Local media provider selecting from project-local library (4.1, V6)."""

    def __init__(
        self,
        project_dir: Path | str,
        credits_manager: Optional["CreditsManager"] = None,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.manager = LocalLibraryManager(self.project_dir)
        self.credits_manager = credits_manager

    @property
    def name(self) -> str:
        return "local"

    @property
    def is_fake(self) -> bool:
        return False

    def validate_credentials(self) -> None:
        """Local provider never needs remote credentials, but library must exist and have files."""
        available = self.manager.list_assets()
        if not available:
            raise VisualError(
                "Local visual library is empty. Please add media files first via:\n"
                f"  python cli_longform.py visual library add <project_id> --folder <path_to_images_or_videos>"
            )

    def search_candidates(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[Set[str]] = None,
    ) -> List[VisualCandidate]:
        """Search local library files matching query or sequential fallback."""
        excluded = excluded_ids or set()
        available = self.manager.list_assets()
        if not available:
            return []

        candidates: List[VisualCandidate] = []
        tokens = [t.lower() for t in query.split() if len(t) > 2]

        # 1. First pass: keyword matching on filename
        matched_files = []
        for p in available:
            cand_id = f"local_{p.stem}"
            if cand_id in excluded:
                continue
            name_lower = p.name.lower()
            if any(t in name_lower for t in tokens):
                matched_files.append(p)

        # 2. Second pass: fallback to any unused files
        for p in available:
            cand_id = f"local_{p.stem}"
            if cand_id in excluded:
                continue
            if p not in matched_files:
                matched_files.append(p)

        for p in matched_files[:limit]:
            cand_id = f"local_{p.stem}"
            ext = p.suffix.lower()
            source_type = "stock_video" if ext in SUPPORTED_VIDEO_EXTS else "stock_image"

            candidates.append(
                VisualCandidate(
                    candidate_id=cand_id,
                    provider="local",
                    source_type=source_type,
                    url=None,
                    local_path=str(p.relative_to(self.project_dir)).replace("\\", "/"),
                    author="Local User Library",
                    author_url=None,
                    source_url=None,
                    license="Local User Media",
                    width=1920,
                    height=1080,
                    duration_seconds=target_duration,
                    source_info={"filename": p.name},
                )
            )

        # 3. Third pass: if no candidates found because all library files were already used, cycle available files
        if not candidates and available:
            for idx, p in enumerate(available[:limit]):
                cand_id = f"local_{p.stem}_{abs(hash(query + str(idx))) % 100000}"
                ext = p.suffix.lower()
                source_type = "stock_video" if ext in SUPPORTED_VIDEO_EXTS else "stock_image"
                candidates.append(
                    VisualCandidate(
                        candidate_id=cand_id,
                        provider="local",
                        source_type=source_type,
                        url=None,
                        local_path=str(p.relative_to(self.project_dir)).replace("\\", "/"),
                        author="Local User Library",
                        author_url=None,
                        source_url=None,
                        license="Local User Media",
                        width=1920,
                        height=1080,
                        duration_seconds=target_duration,
                        source_info={"filename": p.name},
                    )
                )

        return candidates[:limit]

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        """Copy selected library asset into shot asset destination and verify hash."""
        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        rel_path = candidate.local_path or f"assets/visual/library/{candidate.source_info.get('filename')}"
        src_path = (self.project_dir / rel_path).resolve()

        if not src_path.is_file():
            raise VisualError(f"Local library asset not found: {src_path}")

        if src_path != dest:
            shutil.copyfile(src_path, dest)

        c_hash = compute_file_content_hash(dest)

        # Record credit for local asset (V6)
        if self.credits_manager:
            self.credits_manager.record_asset_credit(candidate)

        return VisualAsset(
            asset_id=candidate.candidate_id,
            provider="local",
            source_type=candidate.source_type,
            local_path=dest,
            content_hash=c_hash,
            author=candidate.author,
            author_url=candidate.author_url,
            source_url=None,
            license=candidate.license,
            width=candidate.width,
            height=candidate.height,
            duration_seconds=candidate.duration_seconds,
        )
