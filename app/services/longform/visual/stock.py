"""Stock video provider for longform projects via Pixabay, Pexels, and Coverr (4.1, V1).

Wraps upstream stock video search functions:
- Pixabay: app/services/material.py:507 (search_videos_pixabay)
- Pexels: app/services/material.py:404 (search_videos_pexels)
- Coverr: app/services/material.py:649 (search_videos_coverr)

Features:
- Enforces landscape aspect ratio (16:9).
- Deduplication per provider + asset ID across the project.
- Top-3 candidate ranking per shot.
- Fail-fast credential checks with clear configuration messages covering all 3 providers.
- Default search priority order: Pixabay -> Pexels -> Coverr.
- Downloads cached through GlobalMediaCache (storage/longform_cache/).
- Automatic attribution tracking in export/credits.txt.
"""

from __future__ import annotations

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
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.credits import CreditsManager
from app.services.material import (
    search_videos_coverr,
    search_videos_pexels,
    search_videos_pixabay,
)


def _has_non_empty_key(cfg_key: str) -> bool:
    """Check if config key has at least one non-empty string."""
    val = config.app.get(cfg_key)
    if isinstance(val, (list, tuple)):
        return any(bool(str(k or "").strip()) for k in val)
    return bool(str(val or "").strip())


class StockVisualProvider(BaseVisualProvider):
    """Pixabay, Pexels, and Coverr video adapter implementing BaseVisualProvider (4.1, V1)."""

    def __init__(
        self,
        provider_name: str = "pixabay",
        cache: Optional[GlobalMediaCache] = None,
        credits_manager: Optional[CreditsManager] = None,
    ) -> None:
        self.provider_name = provider_name.lower().strip()
        if self.provider_name not in ("pexels", "pixabay", "coverr", "stock"):
            raise ValueError(f"Unsupported stock provider: {provider_name}")
        self.cache = cache or GlobalMediaCache()
        self.credits_manager = credits_manager

    @property
    def name(self) -> str:
        return self.provider_name

    @property
    def is_fake(self) -> bool:
        return False

    def validate_credentials(self) -> None:
        """Validate API key presence, raising VisualConfigurationError if missing (V1)."""
        has_pixabay = _has_non_empty_key("pixabay_api_keys")
        has_pexels = _has_non_empty_key("pexels_api_keys")
        has_coverr = _has_non_empty_key("coverr_api_keys")

        if self.provider_name == "pixabay" and not has_pixabay:
            raise VisualConfigurationError(
                "Pixabay API key is not configured. Please set 'pixabay_api_keys' "
                f"in config.toml ({config.config_file}) or WebUI Settings before fetching stock visual assets."
            )
        elif self.provider_name == "pexels" and not has_pexels:
            raise VisualConfigurationError(
                "Pexels API key is not configured. Please set 'pexels_api_keys' "
                f"in config.toml ({config.config_file}) or WebUI Settings. "
                "(Note: Pexels has paused new API key issuance; you can use Pixabay or Coverr instead)."
            )
        elif self.provider_name == "coverr" and not has_coverr:
            raise VisualConfigurationError(
                "Coverr API key is not configured. Please set 'coverr_api_keys' "
                f"in config.toml ({config.config_file}) or WebUI Settings before fetching stock visual assets."
            )
        elif self.provider_name == "stock":
            if not (has_pixabay or has_pexels or has_coverr):
                raise VisualConfigurationError(
                    "No stock video API key is configured. Stock providers supported upstream:\n"
                    "  1. Pixabay ('pixabay_api_keys' in config.toml or WebUI Settings)\n"
                    "  2. Pexels ('pexels_api_keys' in config.toml or WebUI Settings - Note: new API key issuance paused)\n"
                    "  3. Coverr ('coverr_api_keys' in config.toml or WebUI Settings, material.py:649)\n"
                    "Please set at least one key, or use '--provider local' (visual library add) or '--provider fake'."
                )

    def search_candidates(
        self,
        query: str,
        target_duration: float,
        limit: int = 3,
        excluded_ids: Optional[Set[str]] = None,
    ) -> List[VisualCandidate]:
        """Search stock video candidates in 16:9 landscape using priority order: Pixabay -> Pexels -> Coverr."""
        self.validate_credentials()
        excluded = excluded_ids or set()
        candidates: List[VisualCandidate] = []
        min_sec = max(int(target_duration), 2)

        has_pixabay = _has_non_empty_key("pixabay_api_keys")
        has_pexels = _has_non_empty_key("pexels_api_keys")
        has_coverr = _has_non_empty_key("coverr_api_keys")

        # 1. Pixabay search (First in order: reliable, free, open registration)
        if (self.provider_name == "pixabay" or (self.provider_name == "stock" and has_pixabay)) and len(candidates) < limit:
            try:
                # Upstream call: app/services/material.py:507
                results = search_videos_pixabay(
                    search_term=query,
                    minimum_duration=min_sec,
                    video_aspect=VideoAspect.landscape,
                )
                for item in results:
                    s_info = getattr(item, "source_info", {}) or {}
                    asset_id = s_info.get("asset_id") or str(abs(hash(item.url)))
                    cand_id = f"pixabay_{asset_id}"
                    if cand_id in excluded or any(c.candidate_id == cand_id for c in candidates):
                        continue

                    creator = s_info.get("creator") or {}
                    rendition = s_info.get("rendition") or {}

                    candidates.append(
                        VisualCandidate(
                            candidate_id=cand_id,
                            provider="pixabay",
                            source_type="stock_video",
                            url=item.url,
                            local_path=None,
                            author=creator.get("name"),
                            author_url=creator.get("url"),
                            source_url=s_info.get("source_page"),
                            license="Pixabay Content License",
                            width=int(rendition.get("width") or 1920),
                            height=int(rendition.get("height") or 1080),
                            duration_seconds=float(getattr(item, "duration", target_duration) or target_duration),
                            source_info=s_info,
                        )
                    )
                    if len(candidates) >= limit:
                        break
            except Exception as exc:
                logger.error(f"Pixabay stock video search error for query '{query}': {exc}")
                if isinstance(exc, VisualConfigurationError):
                    raise

        # 2. Pexels search (Second in order: used if key is present)
        if (self.provider_name == "pexels" or (self.provider_name == "stock" and has_pexels)) and len(candidates) < limit:
            try:
                # Upstream call: app/services/material.py:404
                results = search_videos_pexels(
                    search_term=query,
                    minimum_duration=min_sec,
                    video_aspect=VideoAspect.landscape,
                )
                for item in results:
                    s_info = getattr(item, "source_info", {}) or {}
                    asset_id = s_info.get("asset_id") or str(abs(hash(item.url)))
                    cand_id = f"pexels_{asset_id}"
                    if cand_id in excluded or any(c.candidate_id == cand_id for c in candidates):
                        continue

                    creator = s_info.get("creator") or {}
                    rendition = s_info.get("rendition") or {}

                    candidates.append(
                        VisualCandidate(
                            candidate_id=cand_id,
                            provider="pexels",
                            source_type="stock_video",
                            url=item.url,
                            local_path=None,
                            author=creator.get("name"),
                            author_url=creator.get("url"),
                            source_url=s_info.get("source_page"),
                            license="Pexels Commercial Free License",
                            width=int(rendition.get("width") or 1920),
                            height=int(rendition.get("height") or 1080),
                            duration_seconds=float(getattr(item, "duration", target_duration) or target_duration),
                            source_info=s_info,
                        )
                    )
                    if len(candidates) >= limit:
                        break
            except Exception as exc:
                logger.error(f"Pexels stock video search error for query '{query}': {exc}")
                if isinstance(exc, VisualConfigurationError):
                    raise

        # 3. Coverr search (Third in order: app/services/material.py:649)
        if (self.provider_name == "coverr" or (self.provider_name == "stock" and has_coverr)) and len(candidates) < limit:
            try:
                # Upstream call: app/services/material.py:649
                results = search_videos_coverr(
                    search_term=query,
                    minimum_duration=min_sec,
                    video_aspect=VideoAspect.landscape,
                )
                for item in results:
                    s_info = getattr(item, "source_info", {}) or {}
                    asset_id = s_info.get("asset_id") or str(abs(hash(item.url)))
                    cand_id = f"coverr_{asset_id}"
                    if cand_id in excluded or any(c.candidate_id == cand_id for c in candidates):
                        continue

                    creator = s_info.get("creator") or {}
                    rendition = s_info.get("rendition") or {}

                    candidates.append(
                        VisualCandidate(
                            candidate_id=cand_id,
                            provider="coverr",
                            source_type="stock_video",
                            url=item.url,
                            local_path=None,
                            author=creator.get("name"),
                            author_url=creator.get("url"),
                            source_url=s_info.get("source_page"),
                            license="Coverr License",
                            width=int(rendition.get("width") or 1920),
                            height=int(rendition.get("height") or 1080),
                            duration_seconds=float(getattr(item, "duration", target_duration) or target_duration),
                            source_info=s_info,
                        )
                    )
                    if len(candidates) >= limit:
                        break
            except Exception as exc:
                logger.error(f"Coverr stock video search error for query '{query}': {exc}")
                if isinstance(exc, VisualConfigurationError):
                    raise

        return candidates[:limit]

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        """Download remote stock video using GlobalMediaCache, record credit, compute hash."""
        if not candidate.url:
            raise VisualError(f"Candidate {candidate.candidate_id} has no remote download URL")

        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Download via global cache
        self.cache.download_or_get(candidate.url, dest)

        # Compute content hash
        content_hash = compute_file_content_hash(dest)

        # Record credit
        if self.credits_manager:
            self.credits_manager.record_asset_credit(candidate)

        return VisualAsset(
            asset_id=candidate.candidate_id,
            provider=candidate.provider,
            source_type=candidate.source_type,
            local_path=dest,
            content_hash=content_hash,
            author=candidate.author,
            author_url=candidate.author_url,
            source_url=candidate.source_url,
            license=candidate.license,
            width=candidate.width,
            height=candidate.height,
            duration_seconds=candidate.duration_seconds,
        )

    def fetch_asset(
        self,
        candidate: VisualCandidate,
        dest_path: Path,
    ) -> VisualAsset:
        """Download remote stock video using GlobalMediaCache, record credit, compute hash."""
        if not candidate.url:
            raise VisualError(f"Candidate {candidate.candidate_id} has no remote download URL")

        dest = Path(dest_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Download via global cache
        self.cache.download_or_get(candidate.url, dest)

        # Compute content hash
        content_hash = compute_file_content_hash(dest)

        # Record credit
        if self.credits_manager:
            self.credits_manager.record_asset_credit(candidate)

        return VisualAsset(
            asset_id=candidate.candidate_id,
            provider=candidate.provider,
            source_type=candidate.source_type,
            local_path=dest,
            content_hash=content_hash,
            author=candidate.author,
            author_url=candidate.author_url,
            source_url=candidate.source_url,
            license=candidate.license,
            width=candidate.width,
            height=candidate.height,
            duration_seconds=candidate.duration_seconds,
        )
