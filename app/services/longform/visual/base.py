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
