"""Local media library provider for longform projects (4.1).

Enforces Rule K7:
All media added via `visual library add <id> --folder <path>` are copied
directly into `storage/longform_projects/<id>/assets/visual/library/`.
No external filesystem paths outside the project root are ever referenced.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualError,
    compute_file_content_hash,
)
from app.services.longform.visual.stock import score_candidate_relevance

SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
ALL_SUPPORTED_EXTS = SUPPORTED_VIDEO_EXTS | SUPPORTED_IMAGE_EXTS


def extract_local_file_tags(file_path: Path, library_dir: Path) -> List[str]:
    """Extract normalized lowercase tags from filename stem and subfolder names (V0)."""
    try:
        rel = file_path.relative_to(library_dir)
    except ValueError:
        rel = Path(file_path.name)

    parts_to_split = list(rel.parent.parts) + [file_path.stem]
    tags: List[str] = []
    seen: Set[str] = set()
    for part in parts_to_split:
        if not part or part == ".":
            continue
        for tok in re.split(r"[_\-\.\s]+", part.lower()):
            tok = tok.strip()
            if not tok:
                continue
            if tok not in seen:
                seen.add(tok)
                tags.append(tok)
            m = re.match(r"^([a-z]{2,})\d+$", tok)
            if m:
                stem_tok = m.group(1)
                if stem_tok not in seen:
                    seen.add(stem_tok)
                    tags.append(stem_tok)
    return tags


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

    def resolve_unique_filename(self, filename: str) -> str:
        """Return a collision-free filename in library_dir using numeric suffixes."""
        clean_name = Path(filename).name
        stem = Path(clean_name).stem
        ext = Path(clean_name).suffix.lower()
        candidate = clean_name
        idx = 1
        while (self.library_dir / candidate).exists():
            candidate = f"{stem}_{idx}{ext}"
            idx += 1
        return candidate

    def add_file(
        self,
        file_content: bytes,
        filename: str,
        *,
        on_conflict: str = "overwrite",
        target_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save a single file from raw bytes into the project library (V3, V2).

        on_conflict:
          - 'overwrite': replace existing file with same name (legacy default)
          - 'raise': raise FileExistsError if target already exists
          - 'rename': automatically pick a unique suffixed filename
        """
        clean_name = Path(target_name or filename).name
        ext = Path(clean_name).suffix.lower()
        if ext not in ALL_SUPPORTED_EXTS:
            raise ValueError(f"Unsupported file extension '{ext}'. Supported: {sorted(ALL_SUPPORTED_EXTS)}")

        target_path = self.library_dir / clean_name
        if target_path.exists():
            if on_conflict == "raise":
                raise FileExistsError(f"File already exists in local library: {clean_name}")
            if on_conflict == "rename":
                clean_name = self.resolve_unique_filename(clean_name)
                target_path = self.library_dir / clean_name

        target_path.write_bytes(file_content)
        c_hash = compute_file_content_hash(target_path)
        asset_type = "stock_video" if ext in SUPPORTED_VIDEO_EXTS else "stock_image"

        asset_info = {
            "filename": clean_name,
            "relative_path": f"assets/visual/library/{clean_name}",
            "asset_type": asset_type,
            "size_bytes": target_path.stat().st_size,
            "content_hash": c_hash,
            "file_path": str(target_path),
        }
        logger.info(f"Saved uploaded file into library: {clean_name} ({asset_type})")
        return asset_info

    def get_asset_tags(self, file_path: Path | str) -> List[str]:
        """Return extracted lowercase tags for a local asset path (filename + subfolders)."""
        return extract_local_file_tags(Path(file_path), self.library_dir)

    def list_assets(self) -> List[Path]:
        """List all valid media files in the project library (including subfolders)."""
        if not self.library_dir.is_dir():
            return []
        files = [
            p for p in self.library_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in ALL_SUPPORTED_EXTS
        ]
        return sorted(files, key=lambda p: p.relative_to(self.library_dir).as_posix().lower())


class LocalVisualProvider(BaseVisualProvider):
    """Local media provider selecting from project-local library (4.1, V6, V0)."""

    def __init__(
        self,
        project_dir: Path | str,
        credits_manager: Optional["CreditsManager"] = None,
        *,
        fill_local_random: bool = False,
    ) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.manager = LocalLibraryManager(self.project_dir)
        self.credits_manager = credits_manager
        self.fill_local_random = bool(fill_local_random)

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

    def _base_candidate_id(self, asset_path: Path) -> str:
        rel = asset_path.relative_to(self.manager.library_dir)
        if len(rel.parts) == 1:
            return f"local_{asset_path.stem}"
        rel_no_ext = rel.with_suffix("").as_posix().replace("/", "_")
        return f"local_{rel_no_ext}"

    def _make_candidate(
        self,
        asset_path: Path,
        *,
        cand_id: str,
        target_duration: float,
        tags: List[str],
        score: float,
    ) -> VisualCandidate:
        ext = asset_path.suffix.lower()
        source_type = "stock_video" if ext in SUPPORTED_VIDEO_EXTS else "stock_image"
        rel_lib = asset_path.relative_to(self.manager.library_dir).as_posix()
        return VisualCandidate(
            candidate_id=cand_id,
            provider="local",
            source_type=source_type,
            url=None,
            local_path=asset_path.relative_to(self.project_dir).as_posix(),
            author="Local User Library",
            author_url=None,
            source_url=None,
            license="Local User Media",
            width=1920,
            height=1080,
            duration_seconds=target_duration,
            tags=tags,
            relevance_score=score,
            source_info={"filename": asset_path.name, "relative_library_path": rel_lib},
        )

    def search_with_diagnostics(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[Set[str]] = None,
        *,
        required_subject_terms: Optional[List[str]] = None,
        blocked_terms: Optional[List[str]] = None,
        negative_terms: Optional[List[str]] = None,
        anchor_terms: Optional[List[str]] = None,
        avoid_terms: Optional[List[str]] = None,
        min_relevance_score: Optional[float] = None,
        visual_keywords: Optional[List[str]] = None,
        category: Optional[str] = None,
        narration_text: Optional[str] = None,
        strict_ai_relevance: bool = False,
        fill_local_random: Optional[bool] = None,
        **kwargs: Any,
    ) -> Tuple[List[VisualCandidate], int, List[str]]:
        """Score local files using score_candidate_relevance; pass 2/3 run only when fill_local_random=True."""
        del category, kwargs  # uniform signature with remote providers
        excluded = excluded_ids or set()
        available = self.manager.list_assets()
        raw_count = len(available)
        if not available:
            return [], 0, ["Local library is empty"]

        allow_random = bool(self.fill_local_random if fill_local_random is None else fill_local_random)
        q_raw_tokens = {tok for tok in re.split(r"[_\-\.\s]+", str(query or "").lower()) if tok}

        passing: List[Tuple[float, int, str, VisualCandidate]] = []
        unused_nonmatching: List[Tuple[float, str, VisualCandidate]] = []
        rejected_reasons: List[str] = []

        for p in available:
            base_id = self._base_candidate_id(p)
            if base_id in excluded:
                rejected_reasons.append(f"{p.name}: excluded already-used asset")
                continue

            tags = self.manager.get_asset_tags(p)
            accepted, score, reason = score_candidate_relevance(
                candidate_tags=tags,
                search_query=query,
                visual_keywords=visual_keywords,
                required_subject_terms=required_subject_terms,
                blocked_terms=blocked_terms,
                negative_terms=negative_terms,
                anchor_terms=anchor_terms,
                avoid_terms=avoid_terms,
                min_relevance_score=min_relevance_score,
                is_low_res=False,
                narration_text=narration_text,
                strict_ai_relevance=strict_ai_relevance,
            )

            rel_lib = p.relative_to(self.manager.library_dir).as_posix()
            raw_overlap = sum(1 for qt in q_raw_tokens if qt in tags)
            cand = self._make_candidate(
                p,
                cand_id=base_id,
                target_duration=target_duration,
                tags=tags,
                score=score,
            )

            if accepted:
                passing.append((score, raw_overlap, rel_lib, cand))
            else:
                rejected_reasons.append(f"{rel_lib}: {reason}")
                if "Blocked term matched" not in reason and "Avoid term matched" not in reason:
                    unused_nonmatching.append((score, rel_lib, cand))

        passing.sort(key=lambda item: (-item[0], -item[1], item[2].lower()))
        results: List[VisualCandidate] = [item[3] for item in passing[:limit]]

        # Pass 2 (explicit opt-in only): fallback to any unused non-blocked local files
        if allow_random and len(results) < limit and unused_nonmatching:
            unused_nonmatching.sort(key=lambda item: (-item[0], item[1].lower()))
            for _sc, _rel, cand in unused_nonmatching:
                if len(results) >= limit:
                    break
                results.append(cand)

        # Pass 3 (explicit opt-in only): deterministic SHA-1 cycling of library files if all were excluded
        if allow_random and not results and available:
            for idx, p in enumerate(available[:limit]):
                base_id = self._base_candidate_id(p)
                digest = hashlib.sha1(f"{query}:{idx}".encode("utf-8")).hexdigest()[:8]
                tags = self.manager.get_asset_tags(p)
                _, score, _ = score_candidate_relevance(
                    candidate_tags=tags,
                    search_query=query,
                    visual_keywords=visual_keywords,
                    required_subject_terms=required_subject_terms,
                    blocked_terms=blocked_terms,
                    negative_terms=negative_terms,
                    anchor_terms=anchor_terms,
                    avoid_terms=avoid_terms,
                    min_relevance_score=min_relevance_score,
                    is_low_res=False,
                    narration_text=narration_text,
                    strict_ai_relevance=strict_ai_relevance,
                )
                results.append(
                    self._make_candidate(
                        p,
                        cand_id=f"{base_id}_{digest}",
                        target_duration=target_duration,
                        tags=tags,
                        score=score,
                    )
                )

        return results[:limit], raw_count, rejected_reasons

    def search_candidates(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[Set[str]] = None,
        **kwargs: Any,
    ) -> List[VisualCandidate]:
        """Search local library files via scored search_with_diagnostics."""
        candidates, _raw_count, _reasons = self.search_with_diagnostics(
            query=query,
            target_duration=target_duration,
            limit=limit,
            excluded_ids=excluded_ids,
            **kwargs,
        )
        return candidates

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
