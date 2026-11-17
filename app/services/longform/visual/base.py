"""Base visual abstractions and data structures for longform production (4.1).

Defines:
- VisualCandidate: Search result metadata for top-3 candidate selection
- VisualAsset: Local downloaded/generated asset with verified content hash
- BaseVisualProvider: Common abstract contract for stock, AI, local, and fake visual engines
- Exceptions for configuration and fetch failures
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class VisualError(Exception):
    """Base exception for visual asset retrieval and processing."""
    pass


class VisualConfigurationError(VisualError):
    """Raised when visual provider lacks required credentials or setup and fallback is disallowed."""
    pass


@dataclass
class VisualCandidate:
    """Metadata for a discovered visual asset candidate (4.1)."""

    candidate_id: str
    provider: str  # "pexels" | "pixabay" | "openai" | "local" | "fake"
    source_type: str  # "stock_video" | "stock_image" | "ai_image" | "local_file" | "fake"
    url: Optional[str] = None
    local_path: Optional[str] = None
    author: Optional[str] = None
    author_url: Optional[str] = None
    source_url: Optional[str] = None
    license: str = "Commercial Free"
    width: int = 1920
    height: int = 1080
    duration_seconds: float = 0.0
    source_info: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    relevance_score: float = 0.0
    rendition_name: Optional[str] = None
    is_low_res: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "provider": self.provider,
            "source_type": self.source_type,
            "url": self.url,
            "local_path": self.local_path,
            "author": self.author,
            "author_url": self.author_url,
            "source_url": self.source_url,
            "license": self.license,
            "width": self.width,
            "height": self.height,
            "duration_seconds": round(self.duration_seconds, 2),
            "source_info": self.source_info,
            "tags": self.tags,
            "relevance_score": round(self.relevance_score, 3),
            "rendition_name": self.rendition_name,
            "is_low_res": self.is_low_res,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VisualCandidate:
        return cls(
            candidate_id=data["candidate_id"],
            provider=data["provider"],
            source_type=data["source_type"],
            url=data.get("url"),
            local_path=data.get("local_path"),
            author=data.get("author"),
            author_url=data.get("author_url"),
            source_url=data.get("source_url"),
            license=data.get("license", "Commercial Free"),
            width=data.get("width", 1920),
            height=data.get("height", 1080),
            duration_seconds=data.get("duration_seconds", 0.0),
            source_info=data.get("source_info", {}),
            tags=data.get("tags") or [],
            relevance_score=float(data.get("relevance_score") or 0.0),
            rendition_name=data.get("rendition_name"),
            is_low_res=bool(data.get("is_low_res", False)),
        )


@dataclass
class VisualAsset:
    """A verified, locally saved visual file with calculated content hash (4.1)."""

    asset_id: str
    provider: str
    source_type: str
    local_path: Path
    content_hash: str
    author: Optional[str] = None
    author_url: Optional[str] = None
    source_url: Optional[str] = None
    license: str = "Commercial Free"
    width: int = 1920
    height: int = 1080
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "provider": self.provider,
            "source_type": self.source_type,
            "local_path": str(self.local_path),
            "content_hash": self.content_hash,
            "author": self.author,
            "author_url": self.author_url,
            "source_url": self.source_url,
            "license": self.license,
            "width": self.width,
            "height": self.height,
            "duration_seconds": round(self.duration_seconds, 2),
        }


def compute_file_content_hash(path: Path | str) -> str:
    """Compute deterministic SHA-256 hash of a file on disk."""
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Cannot compute hash for missing file: {p}")
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def invalidate_shot_thumbnail_cache(project_dir: Path, shot_id: str) -> int:
    """Delete both legacy and hash-keyed cached thumbnails for a shot (F14)."""
    t_dir = Path(project_dir) / "assets" / "visual" / "thumbnails"
    if not t_dir.is_dir():
        return 0
    removed = 0
    legacy = t_dir / f"{shot_id}.jpg"
    if legacy.is_file():
        legacy.unlink(missing_ok=True)
        removed += 1
    for p in t_dir.glob(f"{shot_id}_*.jpg"):
        p.unlink(missing_ok=True)
        removed += 1
    return removed


def resolve_shot_visual_source(project_dir: Path, shot: Any, fps: int = 25) -> Dict[str, Any]:
    """Single source of truth for shot preview/thumbnail & rebuild state (F14).

    Rule:
    1. Gallery thumbnail AND detail preview BOTH read `shot.rendered_clip` whenever present.
    2. If `rendered_clip` is missing while `asset_path` exists, BOTH read `asset_path` (`not_built=True`).
    3. If `asset_path` changed without rebuilding `rendered_clip` (`shot_hash is None`,
       asset content hash changed vs manifest, or asset mtime > clip mtime),
       `needs_rebuild=True` and shot status should reflect stale/needs-update.
    """
    _ = fps  # kept for caller uniformity
    p_dir = Path(project_dir)
    clip_rel = getattr(shot, "rendered_clip", None)
    asset_rel = getattr(shot, "asset_path", None)
    clip_p = (p_dir / clip_rel) if clip_rel and (p_dir / clip_rel).is_file() else None
    asset_p = (p_dir / asset_rel) if asset_rel and (p_dir / asset_rel).is_file() else None

    has_clip = clip_p is not None
    has_asset = asset_p is not None

    asset_newer_than_clip = False
    if has_clip and has_asset:
        if getattr(shot, "shot_hash", None) is None:
            asset_newer_than_clip = True
        else:
            try:
                if asset_p.stat().st_mtime > clip_p.stat().st_mtime + 0.5:
                    asset_newer_than_clip = True
            except OSError:
                pass

    needs_rebuild = bool(asset_newer_than_clip)
    not_built = bool(has_asset and not has_clip)

    st_obj = getattr(shot, "status", None)
    st_val = getattr(st_obj, "value", st_obj)
    is_stale = (str(st_val or "").lower() == "stale")
    is_needs_update = bool(needs_rebuild or is_stale)

    if clip_p is not None:
        source_kind = "clip"
        source_path: Optional[Path] = clip_p
    elif asset_p is not None:
        source_kind = "asset"
        source_path = asset_p
    else:
        source_kind = None
        source_path = None

    source_hash: Optional[str] = None
    if source_path is not None:
        manifest_sig = (
            getattr(shot, "shot_hash", None)
            if source_kind == "clip"
            else getattr(shot, "asset_content_hash", None)
        ) or ""
        try:
            st = source_path.stat()
            sig_str = f"{source_kind}|{manifest_sig}|{st.st_size}|{st.st_mtime_ns}"
            source_hash = hashlib.sha256(sig_str.encode("utf-8")).hexdigest()[:16]
        except Exception:
            source_hash = hashlib.sha256(f"{source_kind}|{manifest_sig}".encode("utf-8")).hexdigest()[:16]

    return {
        "source_kind": source_kind,
        "source_path": source_path,
        "source_hash": source_hash,
        "has_clip": has_clip,
        "has_asset": has_asset,
        "not_built": not_built,
        "needs_rebuild": needs_rebuild,
        "is_needs_update": is_needs_update,
    }


class BaseVisualProvider(ABC):
    """Abstract visual provider contract (4.1)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'pexels', 'pixabay', 'openai', 'local', 'fake')."""
        pass

    @property
    @abstractmethod
    def is_fake(self) -> bool:
        """True if this provider is a synthetic mock."""
        pass

    @abstractmethod
    def validate_credentials(self) -> None:
        """Validate API keys or environmental readiness."""
        pass

    @abstractmethod
    def search_candidates(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[set[str]] = None,
        **kwargs: Any,
    ) -> List[VisualCandidate]:
        """Search available candidates matching query and minimum duration."""
        pass

    @abstractmethod
    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        """Download or materialize candidate into local asset file."""
        pass
