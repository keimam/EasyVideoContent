"""Base visual abstractions and data structures for longform production (4.1).

Defines:
- VisualCandidate: Search result metadata for top-3 candidate selection
- VisualAsset: Local downloaded/generated asset with verified content hash
- BaseVisualProvider: Common abstract contract for stock, AI, local, and fake visual engines
- Exceptions for configuration and fetch failures
"""

from __future__ import annotations

import hashlib
import os
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
    title: Optional[str] = None

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
            "title": self.title,
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
            title=data.get("title"),
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
    title: Optional[str] = None

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

    is_legacy_fake = bool(
        getattr(shot, "is_fake", False)
        or str(getattr(shot, "actual_source", "") or "").lower() == "fake"
    )
    is_missing_visual = bool(
        (not has_asset and not has_clip)
        or (not has_asset and has_clip and str(getattr(shot, "fallback_reason", "") or "") == "no_visual_found" and str(getattr(shot, "actual_source", "") or "").lower() != "neutral_backdrop")
    )

    return {
        "source_kind": source_kind,
        "source_path": source_path,
        "source_hash": source_hash,
        "has_clip": has_clip,
        "has_asset": has_asset,
        "not_built": not_built,
        "needs_rebuild": needs_rebuild,
        "is_needs_update": is_needs_update,
        "is_missing_visual": is_missing_visual,
        "is_legacy_fake": is_legacy_fake,
    }


def is_dev_or_test_mode() -> bool:
    """Return True ONLY in explicit dev mode (EFS_DEV=1) or under pytest (D8).

    In installed mode and normal source mode, the synthetic 'fake' provider
    must never be selected automatically or used as fallback.
    """
    efs_dev = os.environ.get("EFS_DEV", "").strip().lower()
    if efs_dev in ("1", "true", "yes", "on"):
        return True
    if os.environ.get("PYTEST_CURRENT_TEST", "").strip():
        return True
    return False


KEYLESS_FREE_VISUAL_SOURCES = ("wikimedia", "nasa", "openverse")
DEFAULT_VISUAL_SOURCE_ORDER = ("pixabay", "coverr", "wikimedia", "nasa", "openverse")


def is_commercial_license_allowed(license_name: Optional[str]) -> tuple[bool, str]:
    """Validate whether a media license permits commercial derivative video use (D9.2).

    Allowed: Public Domain / PD-USGov / NASA / CC0 / CC-PDM / CC BY / CC BY-SA.
    Rejected: NC (NonCommercial), ND (NoDerivatives), All Rights Reserved, Fair Use, Unknown.
    """
    raw = str(license_name or "").strip()
    if not raw:
        return False, "Missing license metadata"
    low = raw.lower().replace("_", "-")

    # Hard reject non-commercial / no-derivatives / restrictive licenses
    for forbidden in ("nc", "-nd", " nd", "no-deriv", "noderiv", "noncommercial", "non-commercial", "all rights reserved", "fair use", "fair-use", "copyrighted"):
        if forbidden in low:
            # Guard against substring 'pd' / 'background' false hits: check whole tokens for short tokens
            if forbidden in ("nc", " nd"):
                import re
                if re.search(rf"(?<![a-z]){forbidden.strip()}(?![a-z])", low):
                    return False, f"Disallowed restrictive license clause '{forbidden.strip()}' in '{raw}'"
            else:
                return False, f"Disallowed restrictive license '{raw}'"

    allowed_tokens = (
        "public domain",
        "pd-",
        "pd/",
        "pdm",
        "usgov",
        "gov-work",
        "nasa",
        "cc0",
        "cc-0",
        "cc zero",
        "cc-zero",
        "cc-by",
        "cc by",
        "attribution",
    )
    if low in ("pd", "cc0", "pdm", "by", "by-sa") or any(tok in low for tok in allowed_tokens):
        return True, raw
    return False, f"Unrecognized or non-commercial license '{raw}'"


def license_requires_attribution(license_name: Optional[str]) -> bool:
    """Return True for licenses requiring creator attribution (CC BY, CC BY-SA, NASA credit) (D9.3)."""
    low = str(license_name or "").strip().lower().replace("_", "-")
    if not low:
        return False
    if "cc-by" in low or "cc by" in low or low in ("by", "by-sa") or "by-sa" in low or "nasa" in low or "attribution" in low:
        return True
    if "cc0" in low or "public domain" in low or low == "pd" or "pdm" in low or "pixabay" in low or "pexels" in low or "coverr" in low:
        return False
    return False


def has_real_visual_source(
    project_dir: Path | str,
    cfg_app: Optional[Dict[str, Any]] = None,
    enabled_sources: Optional[List[str] | Tuple[str, ...]] = None,
) -> bool:
    """Return True if at least one active visual source is usable (D8, D9.2).

    Keyless free providers ('wikimedia', 'nasa', 'openverse') count as valid real
    sources whenever checked/enabled (including default configuration). If the caller
    passes an explicit `enabled_sources` list (e.g. UI checkboxes), only checked
    sources are evaluated; unchecking everything returns False.
    """
    p_dir = Path(project_dir)
    lib_dir = p_dir / "assets" / "visual" / "library"
    has_local_files = False
    try:
        has_local_files = bool(
            lib_dir.is_dir() and any(f.is_file() and not f.name.startswith(".") for f in lib_dir.iterdir())
        )
    except OSError:
        has_local_files = False

    app_map = cfg_app
    if app_map is None:
        try:
            from app.config import config as _cfg
            app_map = _cfg.app
        except Exception:
            app_map = {}

    def _has_val(key: str) -> bool:
        val = (app_map or {}).get(key)
        if isinstance(val, (list, tuple)):
            return any(bool(str(x or "").strip()) for x in val)
        return bool(str(val or "").strip())

    # Determine enabled providers
    if enabled_sources is not None:
        norm = {str(s).strip().lower() for s in enabled_sources if str(s or "").strip()}
        if not norm:
            return False
        if any(s in KEYLESS_FREE_VISUAL_SOURCES for s in norm):
            return True
        if "pixabay" in norm and _has_val("pixabay_api_keys"):
            return True
        if "pexels" in norm and _has_val("pexels_api_keys"):
            return True
        if "coverr" in norm and _has_val("coverr_api_keys"):
            return True
        if "local" in norm and has_local_files:
            return True
        if "ai_image" in norm and (_has_val("openai_image_api_key") or bool((app_map or {}).get("codex_image_enabled", False))):
            return True
        return False

    # Default (no explicit filter passed): keyless sources in DEFAULT_FREE_VISUAL_SOURCES are enabled by default
    # unless cfg_app explicitly disables all of them via `free_visual_sources_disabled`.
    if not bool((app_map or {}).get("free_visual_sources_disabled", False)):
        return True
    if has_local_files:
        return True
    if _has_val("pixabay_api_keys") or _has_val("pexels_api_keys") or _has_val("coverr_api_keys"):
        return True
    if _has_val("openai_image_api_key") or bool((app_map or {}).get("codex_image_enabled", False)):
        return True
    return False


def inspect_project_missing_visuals(
    project_dir: Path | str,
    manifest: Any,
) -> Dict[str, Any]:
    """Inspect all shots for missing visuals or legacy 'fake' placeholders (D8).

    Returns counts and structured shot entries so Visual gallery, Render confirmation,
    and CapCut confirmation share a single source of truth.
    """
    p_dir = Path(project_dir)
    missing_shots: List[Dict[str, Any]] = []
    legacy_fake_shots: List[Dict[str, Any]] = []
    unready_shots: List[Dict[str, Any]] = []

    global_idx = 0
    for ch in getattr(manifest, "chapters", []) or []:
        ch_idx = getattr(ch, "chapter_index", 1)
        seg_text_map: Dict[int, str] = {}
        for g in getattr(ch, "tts_groups", []) or []:
            for seg in getattr(g, "segments", []) or []:
                s_idx = getattr(seg, "segment_index", None)
                s_txt = str(getattr(seg, "narration_text", "") or "").strip()
                if s_idx is not None and s_txt:
                    seg_text_map[s_idx] = s_txt

        for sh in getattr(ch, "shots", []) or []:
            global_idx += 1
            res = resolve_shot_visual_source(p_dir, sh)
            seg_idxs = getattr(sh, "segment_indices", None) or []
            matched_narr = " ".join(seg_text_map[i] for i in seg_idxs if i in seg_text_map).strip()
            narr = (
                matched_narr
                or str(getattr(sh, "visual_prompt", "") or "").strip()
                or str(getattr(sh, "search_query", "") or "").strip()
            )
            snippet = (narr[:52] + "…") if len(narr) > 52 else narr
            is_neutral = str(getattr(sh, "actual_source", "") or "").lower() == "neutral_backdrop"
            is_fake_shot = bool(res.get("is_legacy_fake"))
            # Shot lacks a real visual if neither built clip nor asset exists (or marked no_visual_found before neutral fallback)
            no_real_visual = bool(
                (not res["has_clip"] and not res["has_asset"])
                or (
                    not res["has_asset"]
                    and str(getattr(sh, "fallback_reason", "") or "") == "no_visual_found"
                    and not is_neutral
                )
            )
            entry = {
                "global_idx": global_idx,
                "chapter_index": ch_idx,
                "shot_id": getattr(sh, "shot_id", ""),
                "snippet": snippet or getattr(sh, "shot_id", ""),
                "is_legacy_fake": is_fake_shot,
                "is_missing_visual": no_real_visual and not is_fake_shot,
            }
            if is_fake_shot:
                legacy_fake_shots.append(entry)
                unready_shots.append(entry)
            elif no_real_visual:
                missing_shots.append(entry)
                unready_shots.append(entry)

    return {
        "missing_shots": missing_shots,
        "legacy_fake_shots": legacy_fake_shots,
        "unready_shots": unready_shots,
        "missing_count": len(missing_shots),
        "legacy_fake_count": len(legacy_fake_shots),
        "unready_count": len(unready_shots),
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
