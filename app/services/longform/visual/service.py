"""High-level Visual Service orchestrating visual asset acquisition, shot generation, inspection, and locking (4.1 - 4.4, V1-V6).

V1: Provider is part of the shot asset hash. Fake assets from previous runs are considered
    stale when real providers are requested. Default stock order: Pixabay → Pexels.
V2: visual status reads actual nb_frames from clip files via ffprobe instead of relying on manifest numbers.
V3: set_voice / set_provider changes are hash-based: switching back to previous voice restores completed state.
V4: KeyboardInterrupt handled globally in main(); visual service cleans up partial FFmpeg output files.
V5: Lazy imports for heavy upstream modules (material.py, video.py) only in providers that need them.
V6: local provider is a full first-class real source (non-fake, no [FAKE] label, credits recorded as "local").
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from app.services.longform.schema import Chapter, ItemStatus, MotionType, ProjectManifest, Shot
from app.services.longform.store import is_stop_requested, load_manifest, save_manifest, update_job_phase
from app.services.longform.shot import (
    compute_shot_hash,
    form_chapter_shots,
)
# Base classes and utilities imported eagerly (lightweight, no upstream deps)
from app.services.longform.visual.base import (
    DEFAULT_VISUAL_SOURCE_ORDER,
    KEYLESS_FREE_VISUAL_SOURCES,
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualConfigurationError,
    VisualError,
    canonical_candidate_dedup_key,
    compute_file_content_hash,
    deduplicate_candidates,
    invalidate_shot_thumbnail_cache,
    is_dev_or_test_mode,
    license_requires_attribution,
    reset_io_telemetry,
    sanitize_source_error,
    snapshot_io_telemetry,
)
# Builder imported eagerly (pure FFmpeg subprocess, no upstream deps)
from app.services.longform.visual.builder import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ShotClipBuilder,
)
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.credits import CreditsManager
# V5: Heavy providers (stock/ai_image) imported lazily in get_provider()
from app.services.longform.visual.fake import FakeVisualProvider
from app.services.longform.visual.local import LocalLibraryManager, LocalVisualProvider
from PIL import Image, ImageDraw


def _get_clip_frame_count(clip_path: Path) -> Optional[int]:
    """Read actual nb_frames from a rendered clip via ffprobe. Returns None on failure (V2)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=nb_frames",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(clip_path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        raw = result.stdout.strip()
        if raw and raw.isdigit():
            return int(raw)
        # Fallback: count packets (slower but reliable for all containers)
        result2 = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-count_packets",
                "-show_entries", "stream=nb_read_packets",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(clip_path),
            ],
            capture_output=True, text=True, timeout=20,
        )
        raw2 = result2.stdout.strip()
        if raw2 and raw2.isdigit():
            return int(raw2)
    except Exception:
        pass
    return None


class VisualService:
    """Orchestrates shot formation, candidate search, asset fetching, and clip rendering."""

    def __init__(self, project_dir: Path | str) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.cache = GlobalMediaCache()
        self.credits_manager = CreditsManager(self.project_dir)
        self.local_library = LocalLibraryManager(self.project_dir)
        self._telemetry_lock = threading.Lock()
        self._active_search_seconds: Optional[Dict[str, float]] = None
        self._active_manifest_save_seconds: float = 0.0
        self._active_manifest_save_count: int = 0

    def _save_manifest_timed(self, manifest: ProjectManifest) -> None:
        t0 = time.monotonic()
        save_manifest(manifest, self.project_dir)
        dt = time.monotonic() - t0
        with self._telemetry_lock:
            self._active_manifest_save_seconds += dt
            self._active_manifest_save_count += 1

    def _record_provider_search_time(self, prov_name: str, elapsed_sec: float) -> None:
        pk = str(prov_name or "unknown").lower().strip()
        with self._telemetry_lock:
            if self._active_search_seconds is not None:
                self._active_search_seconds[pk] = float(self._active_search_seconds.get(pk, 0.0)) + float(elapsed_sec)

    def get_provider(
        self,
        provider_name: str,
        manifest: ProjectManifest,
    ) -> BaseVisualProvider:
        """Instantiate visual provider based on name or preset preferences.

        V5: stock and ai_image providers are imported lazily here to avoid loading
        upstream material.py / video.py for lightweight commands like 'visual status'.
        """
        p_name = provider_name.lower().strip()
        if p_name == "fake":
            if not is_dev_or_test_mode():
                raise VisualConfigurationError(
                    "Penyedia visual 'fake' hanya aktif pada mode dev/tes (EFS_DEV=1 atau pytest). "
                    "Isi API key Pexels/Pixabay di API & Penyedia AI atau gunakan Pustaka Lokal."
                )
            return FakeVisualProvider(mode="ai_image")
        elif p_name in ("pexels", "pixabay", "coverr", "stock"):
            # V5: lazy import of StockVisualProvider (pulls in material.py)
            from app.services.longform.visual.stock import StockVisualProvider  # noqa: F811
            return StockVisualProvider(
                provider_name=p_name,
                cache=self.cache,
                credits_manager=self.credits_manager,
            )
        elif p_name in ("wikimedia", "nasa", "openverse"):
            from app.services.longform.visual.free_providers import (
                NasaVisualProvider,
                OpenverseVisualProvider,
                WikimediaVisualProvider,
            )
            if p_name == "wikimedia":
                return WikimediaVisualProvider(cache=self.cache, credits_manager=self.credits_manager)
            elif p_name == "nasa":
                return NasaVisualProvider(cache=self.cache, credits_manager=self.credits_manager)
            else:
                return OpenverseVisualProvider(cache=self.cache, credits_manager=self.credits_manager)
        elif p_name in ("ai_image", "openai", "ai", "openai_image", "codex_image"):
            # V5: lazy import of AIImageProvider (pulls in material.py openai/codex path)
            from app.services.longform.visual.ai_image import AIImageProvider  # noqa: F811
            template = None
            style = None
            if manifest.preset_snapshot:
                style = manifest.preset_snapshot.visual.get("style") if hasattr(manifest.preset_snapshot, "visual") else None
            backend = p_name if p_name in ("openai_image", "codex_image") else None
            return AIImageProvider(prompt_template=template, visual_style=style, backend=backend)
        elif p_name == "local":
            return LocalVisualProvider(
                self.project_dir,
                credits_manager=self.credits_manager,
                fill_local_random=bool(getattr(manifest, "fill_local_random", False)),
            )
        else:
            raise ValueError(f"Unknown visual provider: {provider_name}")

    def prepare_shots(
        self,
        manifest: ProjectManifest,
        fps: Optional[int] = None,
        default_source: Optional[str] = None,
    ) -> ProjectManifest:
        """Form shots for all chapters in the project."""
        effective_fps = fps or getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
        for ch in manifest.chapters:
            form_chapter_shots(ch, fps=effective_fps, default_source=default_source)
        return manifest

    def _resolve_default_provider(self, manifest: ProjectManifest) -> str:
        """Resolve the default visual provider from preset snapshot (V1).

        Order: local (if library has files) → pixabay (if key present) → pexels (if key present) → ai_image.
        Unlike Stage 2, 'fake' is NEVER the implicit default; it must be explicitly requested.
        """
        # Check preset snapshot for explicit preferred provider
        preferred = None
        if manifest.preset_snapshot:
            preferred = manifest.preset_snapshot.get("preferred_visual_source")
        if preferred and preferred != "auto":
            pref_low = str(preferred).lower().strip()
            if pref_low != "fake" or is_dev_or_test_mode():
                return pref_low

        # Check local library
        lib_dir = self.project_dir / "assets" / "visual" / "library"
        if lib_dir.is_dir() and any(lib_dir.iterdir()):
            return "local"

        # V5: lazy import for credential check only (no search performed)
        from app.config import config as _cfg

        def _has_key(k: str) -> bool:
            val = _cfg.app.get(k)
            if isinstance(val, (list, tuple)):
                return any(bool(str(x or "").strip()) for x in val)
            return bool(str(val or "").strip())

        # Default stock order: Pixabay → Pexels → Coverr (V1)
        if _has_key("pixabay_api_keys"):
            return "pixabay"
        if _has_key("pexels_api_keys"):
            return "pexels"
        if _has_key("coverr_api_keys"):
            return "coverr"

        # If no stock keys are present, return "stock" so validate_credentials() will fail-fast
        # with clear guidance mentioning Pixabay, Pexels, and Coverr.
        return "stock"

    def resolve_provider_waterfall(
        self,
        manifest: ProjectManifest,
        provider_name: Optional[str] = None,
        providers_order: Optional[List[str]] = None,
    ) -> List[str]:
        """Resolve ordered list of usable visual providers for D9.2 tiered fallback."""
        from app.config import config as _cfg

        def _has_key(k: str) -> bool:
            val = _cfg.app.get(k)
            if isinstance(val, (list, tuple)):
                return any(bool(str(x or "").strip()) for x in val)
            return bool(str(val or "").strip())

        def _is_provider_usable(name: str) -> bool:
            low = name.lower().strip()
            if low in KEYLESS_FREE_VISUAL_SOURCES:
                return True
            if low == "pixabay":
                return _has_key("pixabay_api_keys")
            if low == "pexels":
                return _has_key("pexels_api_keys")
            if low == "coverr":
                return _has_key("coverr_api_keys")
            if low == "local":
                from app.services.longform.visual.local import ALL_SUPPORTED_EXTS
                lib_dir = self.project_dir / "assets" / "visual" / "library"
                return bool(
                    lib_dir.is_dir()
                    and any(
                        f.is_file() and not f.name.startswith(".") and f.suffix.lower() in ALL_SUPPORTED_EXTS
                        for f in lib_dir.rglob("*")
                    )
                )
            if low == "ai_image":
                return _has_key("openai_image_api_key") or bool(_cfg.app.get("codex_image_enabled", False))
            if low == "fake":
                return is_dev_or_test_mode()
            return False

        if providers_order:
            usable = [p.lower().strip() for p in providers_order if _is_provider_usable(p)]
            return usable

        if provider_name and provider_name.lower().strip() not in ("auto",):
            return [provider_name.lower().strip()]

        # Respect per-project source order if configured, otherwise default D9.2 order
        custom_order = [str(p or "").lower().strip() for p in (getattr(manifest, "visual_source_order", []) or []) if str(p or "").strip()]
        candidates_order: List[str] = custom_order or ["local", "pixabay", "coverr", "pexels", "wikimedia", "nasa", "openverse"]
        # Append any known default source not yet present in custom_order
        for d_src in ["local", "pixabay", "coverr", "pexels", "wikimedia", "nasa", "openverse"]:
            if d_src not in candidates_order:
                candidates_order.append(d_src)
        usable_default = [p for p in candidates_order if _is_provider_usable(p)]
        return usable_default

    def _summarize_search_failures(self, attempts_log: List[Dict[str, Any]]) -> str:
        """Build concise human summary of why no visual was found per provider (D9.1)."""
        if not attempts_log:
            return "Semua sumber aktif tidak menghasilkan kandidat yang sesuai."

        by_prov: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for att in attempts_log:
            p = str(att.get("provider") or "unknown")
            if p not in by_prov:
                by_prov[p] = {"raw": 0, "accepted": 0, "reasons": [], "errors": []}
                order.append(p)
            by_prov[p]["raw"] += int(att.get("raw_count") or 0)
            by_prov[p]["accepted"] += int(att.get("accepted_count") or 0)
            for r in att.get("rejected_reasons") or []:
                if r and r not in by_prov[p]["reasons"]:
                    by_prov[p]["reasons"].append(r)
            if att.get("error"):
                by_prov[p]["errors"].append(str(att["error"])[:80])

        parts: List[str] = []
        for p in order:
            info = by_prov[p]
            disp = p.capitalize()
            if info["errors"] and info["raw"] == 0:
                parts.append(f"{disp}: error ({info['errors'][0]})")
            elif info["raw"] == 0:
                parts.append(f"{disp}: 0 kandidat")
            else:
                rejs = info["reasons"]
                has_lic = any("license" in r.lower() or "lisensi" in r.lower() for r in rejs)
                if has_lic and info["accepted"] == 0:
                    parts.append(f"{disp}: {info['raw']} kandidat (ditolak lisensi/relevansi)")
                else:
                    parts.append(f"{disp}: {info['raw']} kandidat (semua ditolak relevansi)")
        return "; ".join(parts)

    def _is_shot_asset_valid_for_provider(
        self,
        shot: Shot,
        target_provider_name: str,
        required_subject_terms: Optional[List[str]] = None,
        blocked_terms: Optional[List[str]] = None,
        negative_terms: Optional[List[str]] = None,
        min_relevance_score: Optional[float] = None,
        visual_keywords: Optional[List[str]] = None,
        anchor_terms: Optional[List[str]] = None,
        avoid_terms: Optional[List[str]] = None,
    ) -> bool:
        """Check if the shot's current asset is genuinely valid (V1, S4, S5, T3).

        'valid' MUST mean:
        1. If shot is locked, it is always valid (user intent protected).
        2. File exists on disk.
        3. Content hash matches manifest.
        4. Not fake / placeholder asset when real provider requested.
        5. Actual source matches requested provider.
        6. Decodable via ffprobe (has video/image stream, duration > 0, width > 0, height > 0).
        7. Candidate tags satisfy strict subject criteria (T3): subject in first 5 tags or >=2 subjects,
           negative terms penalty, and score >= min_relevance_score.
        """
        if shot.locked:
            return True

        if getattr(shot, "status", None) == ItemStatus.stale:
            return False

        if not shot.asset_path or not shot.asset_content_hash:
            return False

        asset_file = (self.project_dir / shot.asset_path).resolve()
        if not asset_file.is_file():
            return False
        try:
            if compute_file_content_hash(asset_file) != shot.asset_content_hash:
                return False
        except Exception:
            return False

        # V1: Fake assets are never considered valid when a real provider is requested
        if shot.is_fake and target_provider_name.lower() != "fake":
            return False

        # V1: If the shot was fetched from a different real provider, re-fetch
        # (When target_provider_name is "auto", any valid non-fake real source is acceptable)
        if (
            not shot.is_fake
            and shot.actual_source
            and target_provider_name.lower() not in ("", "auto")
            and shot.actual_source.lower() != target_provider_name.lower()
        ):
            return False

        # S4: ffprobe decodability check (for real media files)
        if target_provider_name.lower() != "fake" and not shot.is_fake:
            from app.services.longform.visual.builder import is_valid_media_file
            valid, reason, _ = is_valid_media_file(asset_file)
            if not valid:
                logger.warning(f"Shot {shot.shot_id} asset {asset_file} failed media decode check: {reason}")
                return False

        # F13: If keyword changed (effective_query does not match current query set/short/anchor), invalidate stale asset
        eff_q = str(getattr(shot, "effective_query", None) or "").strip().lower()
        cur_en_q = str(getattr(shot, "search_query_en", None) or "").strip()
        if not shot.locked and cur_en_q and eff_q:
            from app.services.longform.visual.stock import (
                build_short_core_queries,
                extract_narrower_query,
                restructure_stock_query,
            )

            allowed_eff: Set[str] = set()
            base_qs = [
                str(q or "").strip()
                for q in ([cur_en_q] + list(getattr(shot, "search_queries_en", []) or []) + [shot.search_query])
                if str(q or "").strip()
            ]
            for bq in base_qs:
                allowed_eff.add(bq.lower())
                for sq in build_short_core_queries(bq, anchor_terms):
                    allowed_eff.add(sq.lower())
                if required_subject_terms:
                    allowed_eff.add(restructure_stock_query(bq, required_subject_terms).lower())
                narrow = extract_narrower_query(bq, required_subject_terms)
                if narrow:
                    allowed_eff.add(narrow.lower())
            for extra in list(anchor_terms or []) + list(visual_keywords or []):
                ex_s = str(extra or "").strip().lower()
                if ex_s:
                    allowed_eff.add(ex_s)
            if eff_q not in allowed_eff:
                logger.info(
                    f"Shot {shot.shot_id} effective_query '{eff_q}' outdated vs current keyword '{cur_en_q}'; marking stale for re-fetch"
                )
                return False

        # S5, T3, F12: Subject relevance, strict negative terms, and anchor/avoid check on existing stock asset tags
        is_stock_asset = (not shot.is_fake) and str(shot.actual_source or target_provider_name or "").lower() in (
            "stock",
            "pixabay",
            "pexels",
            "coverr",
            "wikimedia",
            "nasa",
            "openverse",
        )
        has_anchor_check = bool((anchor_terms or avoid_terms) and is_stock_asset)
        if required_subject_terms or blocked_terms or negative_terms or has_anchor_check:
            if not shot.tags:
                logger.info(f"Shot {shot.shot_id} has no metadata tags to verify subject terms; marking stale for re-fetch")
                return False
            from app.services.longform.visual.stock import score_candidate_relevance
            is_valid, score, reason = score_candidate_relevance(
                candidate_tags=shot.tags,
                search_query=shot.search_query or shot.visual_prompt,
                visual_keywords=visual_keywords,
                required_subject_terms=required_subject_terms,
                blocked_terms=blocked_terms,
                negative_terms=negative_terms,
                anchor_terms=anchor_terms if has_anchor_check else None,
                avoid_terms=avoid_terms if has_anchor_check else None,
                min_relevance_score=min_relevance_score,
                is_low_res=shot.is_low_res,
            )
            if not is_valid:
                logger.info(f"Shot {shot.shot_id} existing asset failed strict relevance check ({reason}); marking stale for re-fetch")
                return False
            shot.relevance_score = round(score, 3)
            if not shot.effective_query:
                shot.effective_query = shot.search_query or shot.visual_prompt

        return True

    @staticmethod
    def _get_shot_narration_text(manifest: ProjectManifest, shot: Shot) -> str:
        """Return concatenated narration text for a shot from its chapter segments (D9.7)."""
        seg_ids = set(getattr(shot, "segment_indices", []) or [])
        for ch in getattr(manifest, "chapters", []) or []:
            if ch.chapter_index != shot.chapter_index:
                continue
            if seg_ids:
                parts = [
                    str(seg.narration_text or "").strip()
                    for g in (getattr(ch, "tts_groups", []) or [])
                    for seg in (getattr(g, "segments", []) or [])
                    if seg.segment_index in seg_ids and str(seg.narration_text or "").strip()
                ]
                if parts:
                    return " ".join(parts)
            return str(getattr(shot, "visual_prompt", "") or "").strip()
        return str(getattr(shot, "visual_prompt", "") or "").strip()

    @staticmethod
    def _build_shot_query_ladder(
        shot: Shot,
        manifest: ProjectManifest,
        *,
        explicit_prompt: Optional[str] = None,
    ) -> Tuple[str, List[Tuple[str, Optional[str]]]]:
        """Build deduplicated multi-tier (query, category) escalation ladder for any shot search (D9.7)."""
        from app.services.longform.visual.stock import (
            build_generalized_visual_queries,
            build_short_core_queries,
            extract_narrower_query,
            restructure_stock_query,
        )

        preset_snap = manifest.preset_snapshot or {}
        req_terms = preset_snap.get("required_subject_terms") or []
        stock_cat = preset_snap.get("stock_category")
        topic_kws = preset_snap.get("topic_keywords") or []
        visual_kws = preset_snap.get("visual_keywords") or []
        rec_sources = preset_snap.get("recommended_visual_sources") if isinstance(preset_snap.get("recommended_visual_sources"), dict) else None
        proj_anchors = list(getattr(manifest, "visual_anchor_terms", []) or [])

        raw_query = (
            str(explicit_prompt or "").strip()
            or getattr(shot, "search_query_en", None)
            or shot.search_query
            or shot.visual_prompt
            or "cinematic scene"
        )
        ordered_base_queries: List[str] = []
        extra_shot_q = [] if explicit_prompt else [getattr(shot, "search_query", None)]
        for q_cand in ([raw_query] + list(getattr(shot, "search_queries_en", []) or []) + extra_shot_q):
            qc = str(q_cand or "").strip()
            if qc and qc.lower() not in [x.lower() for x in ordered_base_queries]:
                ordered_base_queries.append(qc)

        query_tiers: List[Tuple[str, Optional[str]]] = []
        for bq in ordered_base_queries:
            if req_terms:
                restruct_q = restructure_stock_query(bq, req_terms, topic_kws)
                query_tiers.append((restruct_q, stock_cat))
                query_tiers.append((restruct_q, None))
            query_tiers.append((bq, stock_cat))
            query_tiers.append((bq, None))

        for bq in ordered_base_queries:
            for short_q in build_short_core_queries(bq, proj_anchors):
                query_tiers.append((short_q, None))
            for gen_q in build_generalized_visual_queries(
                bq,
                anchor_terms=proj_anchors,
                topic_keywords=topic_kws,
                visual_keywords=visual_kws,
            ):
                query_tiers.append((gen_q, None))
        narrow_q = extract_narrower_query(raw_query, req_terms)
        if narrow_q:
            query_tiers.append((narrow_q, None))

        if proj_anchors:
            if len(proj_anchors) >= 2 and " " not in proj_anchors[0] and " " not in proj_anchors[1]:
                query_tiers.append((f"{proj_anchors[0]} {proj_anchors[1]}", None))
            for anc_term in proj_anchors[:5]:
                if anc_term:
                    query_tiers.append((anc_term, None))
        for vk in visual_kws[:3]:
            if vk:
                query_tiers.append((vk, stock_cat))

        seen_tiers: set[Tuple[str, Optional[str]]] = set()
        dedup_tiers: List[Tuple[str, Optional[str]]] = []
        for qt_pair in query_tiers:
            k_pair = (qt_pair[0].lower(), qt_pair[1])
            if qt_pair[0].strip() and k_pair not in seen_tiers:
                seen_tiers.add(k_pair)
                dedup_tiers.append(qt_pair)
        return raw_query, dedup_tiers

    def _call_provider_search(
        self,
        provider: BaseVisualProvider,
        *,
        query: str,
        target_duration: float,
        limit: int,
        excluded_ids: Optional[Set[str]],
        req_terms: List[str],
        block_terms: List[str],
        neg_terms: List[str],
        anchor_terms: Optional[List[str]],
        avoid_terms: List[str],
        min_rel_score: Optional[float],
        visual_kws: List[str],
        category: Optional[str],
        narration_text: str,
        strict_ai_relevance: bool,
        recommended_visual_sources: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[VisualCandidate], int, List[str]]:
        """Invoke provider search_with_diagnostics or search_candidates with graceful stub compatibility."""
        search_kwargs: Dict[str, Any] = {
            "query": query,
            "target_duration": target_duration,
            "limit": limit,
            "excluded_ids": excluded_ids,
        }
        if recommended_visual_sources:
            search_kwargs["recommended_visual_sources"] = recommended_visual_sources
        if hasattr(provider, "search_with_diagnostics"):
            try:
                return provider.search_with_diagnostics(
                    required_subject_terms=req_terms,
                    blocked_terms=block_terms,
                    negative_terms=neg_terms,
                    anchor_terms=anchor_terms,
                    avoid_terms=avoid_terms,
                    min_relevance_score=min_rel_score,
                    visual_keywords=visual_kws,
                    category=category,
                    narration_text=narration_text,
                    strict_ai_relevance=strict_ai_relevance,
                    **search_kwargs,
                )
            except TypeError:
                return provider.search_with_diagnostics(
                    required_subject_terms=req_terms,
                    blocked_terms=block_terms,
                    negative_terms=neg_terms,
                    anchor_terms=anchor_terms,
                    avoid_terms=avoid_terms,
                    min_relevance_score=min_rel_score,
                    visual_keywords=visual_kws,
                    category=category,
                    **search_kwargs,
                )

        try:
            cands = provider.search_candidates(
                required_subject_terms=req_terms,
                blocked_terms=block_terms,
                negative_terms=neg_terms,
                anchor_terms=anchor_terms,
                avoid_terms=avoid_terms,
                min_relevance_score=min_rel_score,
                visual_keywords=visual_kws,
                category=category,
                narration_text=narration_text,
                strict_ai_relevance=strict_ai_relevance,
                **search_kwargs,
            )
        except TypeError:
            try:
                cands = provider.search_candidates(
                    required_subject_terms=req_terms,
                    blocked_terms=block_terms,
                    negative_terms=neg_terms,
                    anchor_terms=anchor_terms,
                    avoid_terms=avoid_terms,
                    min_relevance_score=min_rel_score,
                    visual_keywords=visual_kws,
                    category=category,
                    **search_kwargs,
                )
            except TypeError:
                try:
                    cands = provider.search_candidates(
                        required_subject_terms=req_terms,
                        blocked_terms=block_terms,
                        visual_keywords=visual_kws,
                        **search_kwargs,
                    )
                except TypeError:
                    cands = provider.search_candidates(**search_kwargs)
        cands_list = list(cands or [])
        return cands_list, len(cands_list), []

    @staticmethod
    def _provider_query_tiers(
        prov_name: str,
        dedup_tiers: List[Tuple[str, Optional[str]]],
    ) -> List[Tuple[str, Optional[str]]]:
        """Slice query ladder cleanly per provider capabilities (D9.7, V1)."""
        if prov_name in ("pixabay", "pexels", "coverr", "stock"):
            return list(dedup_tiers[:6])
        seen_q_only: set[str] = set()
        prov_tiers: List[Tuple[str, Optional[str]]] = []
        for q_t, _c in dedup_tiers:
            ql = q_t.lower()
            if ql not in seen_q_only:
                seen_q_only.add(ql)
                prov_tiers.append((q_t, None))
        return prov_tiers[:3] if prov_name == "openverse" else prov_tiers[:4]

    def _search_single_provider_tiers(
        self,
        active_provider: BaseVisualProvider,
        prov_name: str,
        shot: Shot,
        prov_tiers: List[Tuple[str, Optional[str]]],
        *,
        limit: int,
        excluded_ids: Optional[Set[str]],
        req_terms: List[str],
        block_terms: List[str],
        neg_terms: List[str],
        proj_anchors: Optional[List[str]],
        proj_avoids: List[str],
        min_rel_score: Optional[float],
        visual_kws: List[str],
        narration_txt: str,
        strict_ai_relevance: bool,
        stop_on_first_hit: bool = False,
        existing_ids: Optional[Set[str]] = None,
        recommended_visual_sources: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[VisualCandidate], Dict[Tuple[str, str], str], List[Dict[str, Any]]]:
        """Run sequential query tiers against one provider (used by both Priority and Mix Best)."""
        best_candidates: List[VisualCandidate] = []
        cand_origin_query: Dict[Tuple[str, str], str] = {}
        seen_best_ids: Set[str] = set(existing_ids or ())
        seen_canon_keys: Set[str] = set()
        attempts: List[Dict[str, Any]] = []
        zero_raw_streak = 0

        for q_text, q_cat in prov_tiers:
            if is_stop_requested(self.project_dir):
                break
            raw_cnt = 0
            rej_reasons: List[str] = []
            err_txt: Optional[str] = None
            tier_cands: List[VisualCandidate] = []
            try:
                _t_call0 = time.monotonic()
                tier_cands, raw_cnt, rej_reasons = self._call_provider_search(
                    active_provider,
                    query=q_text,
                    target_duration=shot.duration_seconds,
                    limit=limit,
                    excluded_ids=excluded_ids,
                    req_terms=req_terms,
                    block_terms=block_terms,
                    neg_terms=neg_terms,
                    anchor_terms=proj_anchors,
                    avoid_terms=proj_avoids,
                    min_rel_score=min_rel_score,
                    visual_kws=visual_kws,
                    category=q_cat,
                    narration_text=narration_txt,
                    strict_ai_relevance=strict_ai_relevance,
                    recommended_visual_sources=recommended_visual_sources,
                )
            except Exception as search_exc:
                err_txt = str(search_exc)[:120]
                logger.warning(f"Shot {shot.shot_id}: {prov_name} query '{q_text}' error: {err_txt}")
            finally:
                self._record_provider_search_time(prov_name, time.monotonic() - _t_call0)

            attempts.append({
                "provider": prov_name,
                "query": q_text,
                "raw_count": raw_cnt,
                "accepted_count": len(tier_cands or []),
                "rejected_reasons": rej_reasons[:3],
                "error": err_txt,
            })

            added_cnt = 0
            for tc in tier_cands or []:
                canon_k = canonical_candidate_dedup_key(tc)
                if tc.candidate_id in seen_best_ids or (canon_k and canon_k in seen_canon_keys):
                    continue
                seen_best_ids.add(tc.candidate_id)
                if canon_k:
                    seen_canon_keys.add(canon_k)
                best_candidates.append(tc)
                cand_origin_query[(tc.provider, tc.candidate_id)] = q_text
                added_cnt += 1

            logger.info(
                f"Shot {shot.shot_id}: [{prov_name}] query '{q_text}' (cat={q_cat}) -> "
                f"raw={raw_cnt}, valid={len(tier_cands or [])}, added={added_cnt}, accumulated={len(best_candidates)}"
            )
            if err_txt:
                err_low = err_txt.lower()
                if (
                    "429" in err_txt
                    or "rate" in err_low
                    or "403" in err_txt
                    or "401" in err_txt
                    or "auth_blocked" in err_low
                    or "cooldown" in err_low
                    or "504" in err_txt
                    or "503" in err_txt
                    or "502" in err_txt
                    or "timed out" in err_low
                    or "timeout" in err_low
                ):
                    break
            if raw_cnt == 0 and not err_txt:
                zero_raw_streak += 1
                if prov_name == "coverr" and zero_raw_streak >= 2:
                    break
            else:
                zero_raw_streak = 0
            if stop_on_first_hit and best_candidates:
                break
            if len(best_candidates) >= limit:
                break

        return best_candidates, cand_origin_query, attempts

    @staticmethod
    def _select_candidate_mix_best(
        candidates: List[VisualCandidate],
        *,
        providers_order: List[str],
        source_usage_counts: Dict[str, int],
        recent_sources: List[str],
        total_project_shots: int,
        max_source_share: float = 0.6,
        avoid_consecutive_same_source: bool = True,
        prioritize_local: bool = True,
    ) -> VisualCandidate:
        """Pick winning candidate in mix_best mode using Local priority, tie-breaking <=0.02 by least-used, and diversity caps."""
        if not candidates:
            raise ValueError("Cannot select candidate from empty pool")

        if prioritize_local and "local" in [p.lower().strip() for p in providers_order]:
            local_cands = [c for c in candidates if str(c.provider).lower().strip() == "local"]
            if local_cands:
                local_cands.sort(key=lambda c: -c.relevance_score)
                return local_cands[0]

        order_rank = {p.lower().strip(): idx for idx, p in enumerate(providers_order)}
        n_total = max(1, int(total_project_shots))

        consec_blocked: Optional[str] = None
        if avoid_consecutive_same_source and len(recent_sources) >= 3:
            last3 = [str(s).lower().strip() for s in recent_sources[-3:]]
            if len(set(last3)) == 1:
                consec_blocked = last3[0]

        cap_blocked: Set[str] = set()
        if max_source_share < 1.0:
            for prov_k, cnt in source_usage_counts.items():
                if cnt >= 1 and ((cnt + 1) / n_total) > (float(max_source_share) + 1e-9):
                    cap_blocked.add(str(prov_k).lower().strip())

        pool = [
            c for c in candidates
            if str(c.provider).lower().strip() != consec_blocked
            and str(c.provider).lower().strip() not in cap_blocked
        ]
        if not pool and consec_blocked:
            pool = [c for c in candidates if str(c.provider).lower().strip() != consec_blocked]
        if not pool and cap_blocked:
            pool = [c for c in candidates if str(c.provider).lower().strip() not in cap_blocked]
        if not pool:
            pool = list(candidates)

        top_score = max(float(c.relevance_score) for c in pool)
        near_tied = [c for c in pool if (top_score - float(c.relevance_score)) <= 0.02 + 1e-9]
        return min(
            near_tied,
            key=lambda c: (
                int(source_usage_counts.get(str(c.provider).lower().strip(), 0)),
                -round(float(c.relevance_score), 6),
                order_rank.get(str(c.provider).lower().strip(), 999),
            ),
        )

    def _search_shot_across_providers(
        self,
        shot: Shot,
        manifest: ProjectManifest,
        *,
        providers_order: List[str],
        explicit_prompt: Optional[str] = None,
        limit: int = 5,
        excluded_ids: Optional[Set[str]] = None,
        allow_waterfall: bool = True,
        allow_fake_fallback: bool = False,
        strict_ai_relevance: bool = False,
        initial_fallback_reason: Optional[str] = None,
        search_mode: Optional[str] = None,
    ) -> Tuple[List[VisualCandidate], Optional[BaseVisualProvider], str, List[Dict[str, Any]], Optional[str]]:
        """Shared candidate search used identically by fetch_all, Otomatis, Search again, and candidate grid (D9.7, V1)."""
        preset_snap = manifest.preset_snapshot or {}
        req_terms = preset_snap.get("required_subject_terms") or []
        block_terms = preset_snap.get("blocked_terms") or []
        neg_terms = preset_snap.get("negative_terms") or []
        min_rel_score = preset_snap.get("min_relevance_score", 0.35)
        visual_kws = preset_snap.get("visual_keywords") or []
        rec_sources = preset_snap.get("recommended_visual_sources") if isinstance(preset_snap.get("recommended_visual_sources"), dict) else None
        proj_anchors = list(getattr(manifest, "visual_anchor_terms", []) or [])
        proj_avoids = list(getattr(manifest, "visual_avoid_terms", []) or [])

        raw_query, dedup_tiers = self._build_shot_query_ladder(
            shot, manifest, explicit_prompt=explicit_prompt
        )
        narration_txt = self._get_shot_narration_text(manifest, shot)

        eff_mode = (
            str(search_mode or getattr(manifest, "visual_search_mode", None) or "priority")
            .lower()
            .strip()
        )
        if eff_mode not in ("priority", "mix_best") or len(providers_order) <= 1 or not allow_waterfall:
            eff_mode = "priority"

        # Local prioritized first in BOTH modes when checked & present in providers_order
        ordered_providers: List[str] = []
        if "local" in [p.lower().strip() for p in providers_order]:
            ordered_providers.append("local")
        for p in providers_order:
            pl = str(p).lower().strip()
            if pl and pl not in ordered_providers:
                ordered_providers.append(pl)

        candidates: List[VisualCandidate] = []
        chosen_provider: Optional[BaseVisualProvider] = None
        fallback_reason: Optional[str] = initial_fallback_reason
        chosen_query = raw_query
        attempts_log: List[Dict[str, Any]] = []
        origin_map: Dict[Tuple[str, str], str] = {}
        self._last_shot_origin_queries = origin_map

        # Resolve usable providers up front
        resolved_provs: List[Tuple[str, BaseVisualProvider, List[Tuple[str, Optional[str]]]]] = []
        for prov_name in ordered_providers:
            if is_stop_requested(self.project_dir):
                break
            if prov_name == "coverr":
                from app.services.longform.visual.coverr_client import get_coverr_auth_status

                c_auth = get_coverr_auth_status()
                if c_auth.get("auth_blocked"):
                    attempts_log.append({
                        "provider": "coverr",
                        "query": raw_query,
                        "raw_count": 0,
                        "accepted_count": 0,
                        "rejected_reasons": [],
                        "error": f"auth_blocked ({c_auth.get('error') or 'HTTP 403'})",
                    })
                    continue
            try:
                active_provider = self.get_provider(prov_name, manifest)
                if prov_name in ("fake", "local"):
                    active_provider.validate_credentials()
            except VisualConfigurationError as conf_err:
                if not allow_waterfall and allow_fake_fallback:
                    fallback_reason = f"Provider '{prov_name}' credentials missing ({conf_err}); fell back to fake provider"
                    active_provider = FakeVisualProvider(mode=prov_name)
                elif allow_waterfall:
                    attempts_log.append({
                        "provider": prov_name,
                        "query": raw_query,
                        "raw_count": 0,
                        "accepted_count": 0,
                        "rejected_reasons": [],
                        "error": str(conf_err)[:120],
                    })
                    continue
                else:
                    raise
            except Exception as exc:
                if allow_waterfall:
                    attempts_log.append({
                        "provider": prov_name,
                        "query": raw_query,
                        "raw_count": 0,
                        "accepted_count": 0,
                        "rejected_reasons": [],
                        "error": str(exc)[:120],
                    })
                    continue
                if not allow_fake_fallback:
                    raise
                fallback_reason = f"Provider '{prov_name}' error ({exc}); fell back to fake provider"
                active_provider = FakeVisualProvider(mode=prov_name)

            resolved_provs.append((prov_name, active_provider, self._provider_query_tiers(prov_name, dedup_tiers)))

        if eff_mode == "priority":
            for prov_name, active_provider, prov_tiers in resolved_provs:
                if is_stop_requested(self.project_dir):
                    break
                best_cands, p_origin, p_attempts = self._search_single_provider_tiers(
                    active_provider,
                    prov_name,
                    shot,
                    prov_tiers,
                    limit=limit,
                    excluded_ids=excluded_ids,
                    req_terms=req_terms,
                    block_terms=block_terms,
                    neg_terms=neg_terms,
                    proj_anchors=proj_anchors,
                    proj_avoids=proj_avoids,
                    min_rel_score=min_rel_score,
                    visual_kws=visual_kws,
                    narration_txt=narration_txt,
                    strict_ai_relevance=strict_ai_relevance,
                    recommended_visual_sources=rec_sources,
                )
                attempts_log.extend(p_attempts)
                origin_map.update(p_origin)
                if best_cands:
                    best_cands.sort(key=lambda c: c.relevance_score, reverse=True)
                    candidates = best_cands[:limit]
                    chosen_provider = active_provider
                    chosen_query = origin_map.get((candidates[0].provider, candidates[0].candidate_id), chosen_query)
                    logger.info(
                        f"Shot {shot.shot_id}: [Priority] selected '{candidates[0].candidate_id}' from {prov_name} "
                        f"(score={candidates[0].relevance_score:.3f}) via query '{chosen_query}'"
                    )
                    break
        else:
            # V1: Parallel Mix Best across ALL active sources
            pool_candidates: List[VisualCandidate] = []
            seen_pool_keys: Set[Tuple[str, str]] = set()
            seen_pool_canon: Set[str] = set()
            seen_per_prov_ids: Dict[str, Set[str]] = {pn: set() for pn, _, _ in resolved_provs}
            prov_obj_map: Dict[str, BaseVisualProvider] = {pn: po for pn, po, _ in resolved_provs}

            def _run_parallel_phase(phase_slices: List[Tuple[str, BaseVisualProvider, List[Tuple[str, Optional[str]]]]]) -> None:
                if not phase_slices:
                    return
                max_workers = min(6, len(phase_slices))
                with ThreadPoolExecutor(max_workers=max_workers) as pool_exec:
                    fut_map = {
                        pool_exec.submit(
                            self._search_single_provider_tiers,
                            p_obj,
                            p_name,
                            shot,
                            p_tiers,
                            limit=limit,
                            excluded_ids=excluded_ids,
                            req_terms=req_terms,
                            block_terms=block_terms,
                            neg_terms=neg_terms,
                            proj_anchors=proj_anchors,
                            proj_avoids=proj_avoids,
                            min_rel_score=min_rel_score,
                            visual_kws=visual_kws,
                            narration_txt=narration_txt,
                            strict_ai_relevance=strict_ai_relevance,
                            existing_ids=seen_per_prov_ids.get(p_name),
                            recommended_visual_sources=rec_sources,
                        ): p_name
                        for p_name, p_obj, p_tiers in phase_slices
                    }
                    # Collect deterministically in provider list order via Ordered futures map
                    results_by_prov: Dict[str, Tuple[List[VisualCandidate], Dict[Tuple[str, str], str], List[Dict[str, Any]]]] = {}
                    for fut in as_completed(fut_map):
                        p_name = fut_map[fut]
                        try:
                            results_by_prov[p_name] = fut.result()
                        except Exception as exc:
                            results_by_prov[p_name] = (
                                [],
                                {},
                                [{
                                    "provider": p_name,
                                    "query": raw_query,
                                    "raw_count": 0,
                                    "accepted_count": 0,
                                    "rejected_reasons": [],
                                    "error": str(exc)[:120],
                                }],
                            )
                    for p_name, _, _ in phase_slices:
                        p_cands, p_orig, p_att = results_by_prov.get(p_name, ([], {}, []))
                        attempts_log.extend(p_att)
                        origin_map.update(p_orig)
                        for tc in p_cands:
                            seen_per_prov_ids[p_name].add(tc.candidate_id)
                            k = (tc.provider, tc.candidate_id)
                            ck = canonical_candidate_dedup_key(tc)
                            if k not in seen_pool_keys and not (ck and ck in seen_pool_canon):
                                seen_pool_keys.add(k)
                                if ck:
                                    seen_pool_canon.add(ck)
                                pool_candidates.append(tc)

            # Phase A: max 2 query tiers per source in parallel
            phase_a = [(pn, po, pt[:2]) for pn, po, pt in resolved_provs if pt[:2]]
            _run_parallel_phase(phase_a)

            # Phase B: escalate wider query tiers ONLY when zero passing candidates across ALL sources
            if not pool_candidates:
                phase_b = [(pn, po, pt[2:]) for pn, po, pt in resolved_provs if pt[2:]]
                _run_parallel_phase(phase_b)

            if pool_candidates:
                # Local passing candidates stay prioritized at the top; rest sorted descending by relevance_score
                local_hits = sorted(
                    [c for c in pool_candidates if str(c.provider).lower() == "local"],
                    key=lambda c: (-c.relevance_score, c.candidate_id),
                )
                other_hits = sorted(
                    [c for c in pool_candidates if str(c.provider).lower() != "local"],
                    key=lambda c: (-c.relevance_score, c.provider, c.candidate_id),
                )
                ordered_pool = local_hits + other_hits
                candidates = ordered_pool[: max(limit, 12)]
                top_cand = candidates[0]
                chosen_provider = prov_obj_map.get(top_cand.provider) or self.get_provider(top_cand.provider, manifest)
                chosen_query = origin_map.get((top_cand.provider, top_cand.candidate_id), chosen_query)
                logger.info(
                    f"Shot {shot.shot_id}: [MixBest] pool={len(pool_candidates)} candidates; top='{top_cand.candidate_id}' "
                    f"from {top_cand.provider} (score={top_cand.relevance_score:.3f}) via '{chosen_query}'"
                )

        # Pass 2: If strict project anchor_terms blocked candidates, retry shot concrete queries without
        # anchor lock (still preserving avoid_terms, blocked_terms, required_subject_terms, narration ranking).
        if not candidates and proj_anchors and any(
            "anchor_terms" in str(r)
            for att in attempts_log
            for r in (att.get("rejected_reasons") or [])
        ):
            relaxed_queries = list(dict.fromkeys([q for q, _c in dedup_tiers[:4] if q]))
            relaxed_tiers = [(rq, None) for rq in relaxed_queries]
            for prov_name, rel_prov, _pt in resolved_provs:
                rel_best, rel_origin, rel_att = self._search_single_provider_tiers(
                    rel_prov,
                    prov_name,
                    shot,
                    relaxed_tiers,
                    limit=limit,
                    excluded_ids=excluded_ids,
                    req_terms=req_terms,
                    block_terms=block_terms,
                    neg_terms=neg_terms,
                    proj_anchors=None,
                    proj_avoids=proj_avoids,
                    min_rel_score=min_rel_score,
                    visual_kws=visual_kws,
                    narration_txt=narration_txt,
                    strict_ai_relevance=strict_ai_relevance,
                    stop_on_first_hit=True,
                    recommended_visual_sources=rec_sources,
                )
                attempts_log.extend(rel_att)
                origin_map.update(rel_origin)
                if rel_best:
                    rel_best.sort(key=lambda c: c.relevance_score, reverse=True)
                    candidates = rel_best[:limit]
                    chosen_provider = rel_prov
                    chosen_query = origin_map.get((candidates[0].provider, candidates[0].candidate_id), chosen_query)
                    logger.info(
                        f"Shot {shot.shot_id}: relaxed-anchor selected '{candidates[0].candidate_id}' "
                        f"from {prov_name} (score={candidates[0].relevance_score:.3f}) via query '{chosen_query}'"
                    )
                    if eff_mode == "priority":
                        break

        candidates = deduplicate_candidates(candidates)
        self._last_shot_origin_queries = origin_map
        return candidates, chosen_provider, chosen_query, attempts_log, fallback_reason

    def fetch_all(
        self,
        manifest: ProjectManifest,
        provider_name: Optional[str] = None,
        providers_order: Optional[List[str]] = None,
        allow_fallback: bool = False,
        check_ai_relevance: bool = False,
        translate_keywords: bool = True,
        _pct_start: float = 0.0,
        _pct_end: float = 99.0,
    ) -> ProjectManifest:
        """Search and download/generate visual assets for all pending shots (4.1, V1, D9).

        D9.1/D9.2:
        - Supports ordered multi-source waterfall (`providers_order` or `provider_name="auto"`),
          defaulting to Pixabay -> Coverr -> Wikimedia -> NASA -> Openverse when waterfall requested.
        - Tiered search per shot: AI/concrete queries -> generalized noun/concept queries -> next source.
        - Records detailed `search_attempts` diagnostics and `no_visual_reason_summary`.
        """
        use_waterfall = bool(providers_order) or (str(provider_name or "").lower().strip() == "auto")
        if use_waterfall:
            waterfall_providers = self.resolve_provider_waterfall(
                manifest,
                provider_name=provider_name,
                providers_order=providers_order,
            )
            if not waterfall_providers and not (bool(allow_fallback) and is_dev_or_test_mode()):
                raise VisualConfigurationError(
                    "Tidak ada sumber visual aktif. Centang minimal satu sumber gratis (Wikimedia/NASA/Openverse) "
                    "atau isi API key Pixabay/Coverr di API & Penyedia AI."
                )
            effective_provider_name = "auto"
        else:
            waterfall_providers = []
            effective_provider_name = provider_name
            if not effective_provider_name:
                effective_provider_name = self._resolve_default_provider(manifest)

        # Ensure shots exist
        has_shots = any(len(ch.shots) > 0 for ch in manifest.chapters)
        if not has_shots:
            effective_fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
            self.prepare_shots(manifest, fps=effective_fps)

        # Early credentials validation for explicit single-provider calls (V1/D8 fail-fast)
        allow_fake_fallback = bool(allow_fallback) and is_dev_or_test_mode()
        global_fallback_reason: Optional[str] = None
        if effective_provider_name == "fake" and not is_dev_or_test_mode():
            raise VisualConfigurationError(
                "Penyedia visual 'fake' tidak diperbolehkan pada mode normal/terpasang. "
                "Isi API key Pexels/Pixabay di API & Penyedia AI atau gunakan sumber gratis (Wikimedia/NASA/Openverse)."
            )
        if not use_waterfall and effective_provider_name not in ("fake", "local", "auto"):
            try:
                probe_provider = self.get_provider(effective_provider_name, manifest)
                probe_provider.validate_credentials()
            except VisualConfigurationError as conf_err:
                if not allow_fake_fallback:
                    provider_guidance = (
                        "Isi API key Pexels/Pixabay di menu API & Penyedia AI (atau aktifkan sumber gratis Wikimedia/NASA/Openverse).\n"
                        "To configure stock video credentials:\n"
                        "  1. Obtain a free API key from Pixabay: https://pixabay.com/api/docs/\n"
                        "  2. Or from Pexels: https://www.pexels.com/api/ (new key registration paused)\n"
                        "  3. Open API & Penyedia AI in the sidebar or use '--provider local'."
                    )
                    raise VisualConfigurationError(
                        f"Provider '{effective_provider_name}' is not properly configured (belum terkonfigurasi).\n"
                        f"Reason: {conf_err}\n\n{provider_guidance}"
                    ) from conf_err
                global_fallback_reason = (
                    f"Provider '{effective_provider_name}' credentials missing ({conf_err}); fell back to fake provider"
                )
                logger.warning(global_fallback_reason)
                effective_provider_name = "fake"

        # Track used candidate IDs across project for deduplication (skip invalidated/stale shots so re-fetch isn't blocked)
        used_candidate_ids: Set[str] = set()
        for ch in manifest.chapters:
            for s in ch.shots:
                if s.locked or getattr(s, "status", None) != ItemStatus.stale:
                    if s.candidates:
                        for c in s.candidates:
                            if c.get("candidate_id"):
                                used_candidate_ids.add(c["candidate_id"])

        # S5, T3: Load preset filtering and scoring rules from snapshot or PresetStore
        preset_snap = manifest.preset_snapshot or {}
        req_terms = preset_snap.get("required_subject_terms") or []
        block_terms = preset_snap.get("blocked_terms") or []
        neg_terms = preset_snap.get("negative_terms") or []
        min_rel_score = preset_snap.get("min_relevance_score", 0.35)
        visual_kws = preset_snap.get("visual_keywords") or []
        stock_cat = preset_snap.get("stock_category")
        topic_kws = preset_snap.get("topic_keywords") or []

        # Backfill if missing in snapshot
        if not req_terms or not stock_cat or not neg_terms:
            try:
                from app.services.longform.presets import get_preset
                p_obj = get_preset(manifest.preset_id)
                if not req_terms and p_obj.required_subject_terms:
                    req_terms = p_obj.required_subject_terms
                    preset_snap["required_subject_terms"] = req_terms
                if not stock_cat and getattr(p_obj, "stock_category", None):
                    stock_cat = p_obj.stock_category
                    preset_snap["stock_category"] = stock_cat
                if not block_terms and p_obj.blocked_terms:
                    block_terms = p_obj.blocked_terms
                    preset_snap["blocked_terms"] = block_terms
                if not neg_terms and getattr(p_obj, "negative_terms", None):
                    neg_terms = p_obj.negative_terms
                    preset_snap["negative_terms"] = neg_terms
                if "min_relevance_score" not in preset_snap:
                    min_rel_score = getattr(p_obj, "min_relevance_score", 0.35)
                    preset_snap["min_relevance_score"] = min_rel_score
                manifest.preset_snapshot = preset_snap
            except Exception as e:
                logger.warning(f"Could not backfill preset snapshot for preset '{manifest.preset_id}': {e}")

        stop_requested = False
        translation_warnings: List[str] = []
        from app.services.longform.language_guard import resolve_project_language
        from app.services.longform.visual.ai_keywords import (
            ensure_project_visual_anchors,
            generate_chapter_visual_keywords_ai,
        )
        from app.services.longform.visual.keyword_translator import (
            is_non_english_language,
            translate_chapter_queries_to_english,
        )
        proj_lang = resolve_project_language(manifest)

        # V1/A2.1: Initialize telemetry, source distribution & sequential tracking
        reset_io_telemetry()
        fetch_t0 = time.monotonic()
        with self._telemetry_lock:
            self._active_search_seconds = {}
            self._active_manifest_save_seconds = 0.0
            self._active_manifest_save_count = 0
        anchor_seconds = 0.0
        anchor_llm_calls = 0
        ai_kw_seconds = 0.0
        ai_kw_llm_calls = 0
        ai_kw_retries = 0
        ai_kw_cached = 0
        translate_seconds = 0.0
        search_wall_seconds = 0.0
        asset_download_seconds = 0.0
        ai_rel_seconds = 0.0
        thumbnail_seconds = 0.0  # A2.1: Candidate thumbnails remain lazy (UI grid only)

        # F12: Generate/load cached project anchor & avoid terms once per project for non-fake fetch
        if effective_provider_name != "fake" and not getattr(manifest, "visual_anchor_terms", None):
            _t_anc0 = time.monotonic()
            try:
                anc_res = ensure_project_visual_anchors(manifest)
                if isinstance(anc_res, dict) and anc_res.get("source") == "ai" and not anc_res.get("cached"):
                    anchor_llm_calls += 1
            except Exception as exc:
                logger.warning(f"Could not populate project visual anchors: {exc}")
            anchor_seconds += time.monotonic() - _t_anc0

        proj_anchors = list(getattr(manifest, "visual_anchor_terms", []) or [])
        proj_avoids = list(getattr(manifest, "visual_avoid_terms", []) or [])

        all_shots_flat = [sh for ch in manifest.chapters for sh in ch.shots]
        target_fetch_ids = {
            sh.shot_id
            for sh in all_shots_flat
            if not sh.locked and not self._is_shot_asset_valid_for_provider(
                sh,
                effective_provider_name,
                req_terms,
                block_terms,
                neg_terms,
                min_rel_score,
                visual_kws,
                anchor_terms=proj_anchors,
                avoid_terms=proj_avoids,
            )
        }
        target_fetch_total = len(target_fetch_ids)
        skipped_fetch_count = len(all_shots_flat) - target_fetch_total
        done_fetch_count = 0
        self._last_fetch_target_count = target_fetch_total

        # V1: Initialize source distribution & sequential tracking for Mix Best / Priority
        eff_search_mode = (
            str(getattr(manifest, "visual_search_mode", None) or "priority").lower().strip()
        )
        if eff_search_mode not in ("priority", "mix_best"):
            eff_search_mode = "priority"
        max_source_share = float(getattr(manifest, "visual_max_source_share", 0.6) or 0.6)
        avoid_consec_same = bool(getattr(manifest, "visual_avoid_consecutive_same_source", True))
        total_project_shots = max(1, len(all_shots_flat))
        source_usage_counts: Dict[str, int] = {}
        recent_sources: List[str] = []
        requests_by_provider: Dict[str, int] = {}
        attempt_errors_by_provider: Dict[str, str] = {}
        attempt_err_counts_by_provider: Dict[str, int] = {}
        for sh in all_shots_flat:
            if sh.shot_id not in target_fetch_ids and sh.actual_source:
                sk = str(sh.actual_source).lower().strip()
                source_usage_counts[sk] = source_usage_counts.get(sk, 0) + 1
                recent_sources.append(sk)

        if target_fetch_total > 0:
            update_job_phase(
                self.project_dir,
                phase=f"Mencari footage (0/{target_fetch_total} shot)…",
                phase_key="prog_visual_fetch",
                phase_args={"done": 0, "total": target_fetch_total},
                percentage=_pct_start,
                current_unit=0,
                total_units=target_fetch_total,
                skipped_units=skipped_fetch_count,
                unit_label="shot",
                is_indeterminate=False,
                partial=True,
            )

        for ch in manifest.chapters:
            if is_stop_requested(self.project_dir):
                logger.info(f"Stop requested for project '{manifest.project_id}'. Exiting visual matching gracefully.")
                stop_requested = True
                break

            # F8 + F2 + F12: Generate concrete 2-3 queries per shot when missing
            if translate_keywords:
                shots_needing_fetch = [
                    sh for sh in ch.shots
                    if not sh.locked and not self._is_shot_asset_valid_for_provider(
                        sh,
                        effective_provider_name,
                        req_terms,
                        block_terms,
                        neg_terms,
                        min_rel_score,
                        visual_kws,
                        anchor_terms=proj_anchors,
                        avoid_terms=proj_avoids,
                    )
                ]
                shots_needing_kw_gen = [
                    sh for sh in shots_needing_fetch
                    if (
                        getattr(sh, "search_query_source", None) != "ai"
                        or not getattr(sh, "search_query_en", None)
                    )
                ]
                if shots_needing_kw_gen and effective_provider_name != "fake":
                    if target_fetch_total > 0:
                        kw_pct = _pct_start + ((done_fetch_count / max(1, target_fetch_total)) * (_pct_end - _pct_start))
                        update_job_phase(
                            self.project_dir,
                            phase="Memperbarui kata kunci AI…",
                            phase_key="prog_visual_ai_keywords",
                            phase_args={},
                            percentage=kw_pct,
                            current_unit=done_fetch_count,
                            total_units=target_fetch_total,
                            skipped_units=skipped_fetch_count,
                            unit_label="shot",
                            is_indeterminate=True,
                            partial=True,
                        )
                    kw_res = generate_chapter_visual_keywords_ai(
                        shots_needing_kw_gen,
                        ch,
                        preset_snapshot=preset_snap,
                        project_lang=proj_lang,
                        project_topic=manifest.topic or manifest.title,
                        sub_niche_focus=getattr(manifest, "sub_niche_focus", None),
                        visual_summary=getattr(manifest, "visual_summary", None),
                        anchor_terms=proj_anchors,
                        avoid_terms=proj_avoids,
                    )
                    ai_kw_seconds += float(kw_res.get("elapsed_seconds") or 0.0)
                    ai_kw_llm_calls += int(kw_res.get("llm_calls") or 0)
                    ai_kw_retries += int(kw_res.get("llm_retries") or 0)
                    ai_kw_cached += int(kw_res.get("cached") or 0)
                    if kw_res.get("warning"):
                        translation_warnings.append(str(kw_res["warning"]))
                    if kw_res.get("fallback_used") and is_non_english_language(proj_lang):
                        _t_tr0 = time.monotonic()
                        tr_res = translate_chapter_queries_to_english(
                            shots_needing_kw_gen,
                            preset_snapshot=preset_snap,
                            project_lang=proj_lang,
                        )
                        translate_seconds += time.monotonic() - _t_tr0
                        if tr_res.get("warning"):
                            translation_warnings.append(str(tr_res["warning"]))
                elif shots_needing_kw_gen and is_non_english_language(proj_lang):
                    _t_tr0 = time.monotonic()
                    tr_res = translate_chapter_queries_to_english(
                        shots_needing_kw_gen,
                        preset_snapshot=preset_snap,
                        project_lang=proj_lang,
                    )
                    translate_seconds += time.monotonic() - _t_tr0
                    if tr_res.get("warning"):
                        translation_warnings.append(str(tr_res["warning"]))

            for shot in ch.shots:
                if is_stop_requested(self.project_dir):
                    logger.info(f"Stop requested for project '{manifest.project_id}'. Exiting visual matching gracefully.")
                    stop_requested = True
                    break
                # Skip locked shots
                if shot.locked:
                    logger.info(f"Skipping locked shot: {shot.shot_id}")
                    continue

                # V1, S4, S5, T3, F12: Check if asset is valid for requested provider AND meets strict subject/anchor filter
                if self._is_shot_asset_valid_for_provider(
                    shot,
                    effective_provider_name,
                    req_terms,
                    block_terms,
                    neg_terms,
                    min_rel_score,
                    visual_kws,
                    anchor_terms=proj_anchors,
                    avoid_terms=proj_avoids,
                ):
                    logger.debug(f"Shot {shot.shot_id} asset valid for provider '{effective_provider_name}', skipping fetch")
                    continue

                # Determine candidate provider list for this shot (waterfall in D9.2 vs single provider)
                shot_providers = list(waterfall_providers) if use_waterfall else [effective_provider_name]
                if not shot_providers and allow_fake_fallback:
                    shot_providers = ["fake"]

                # F13: Clear stale candidate list/tags on unlocked shots being re-fetched
                shot.candidates = []
                shot.tags = []
                shot.search_attempts = []
                shot.no_visual_reason_summary = None

                raw_query = (
                    getattr(shot, "search_query_en", None)
                    or shot.search_query
                    or shot.visual_prompt
                    or "cinematic scene"
                )
                _t_sw0 = time.monotonic()
                candidates, chosen_provider, chosen_query, attempts_log, fallback_reason = self._search_shot_across_providers(
                    shot,
                    manifest,
                    providers_order=shot_providers,
                    explicit_prompt=None,
                    limit=5,
                    excluded_ids=used_candidate_ids,
                    allow_waterfall=use_waterfall,
                    allow_fake_fallback=allow_fake_fallback,
                    strict_ai_relevance=bool(check_ai_relevance),
                    initial_fallback_reason=global_fallback_reason,
                    search_mode=eff_search_mode if use_waterfall else "priority",
                )
                search_wall_seconds += time.monotonic() - _t_sw0
                for att in attempts_log:
                    p_k = str(att.get("provider") or "unknown").lower().strip()
                    requests_by_provider[p_k] = requests_by_provider.get(p_k, 0) + 1
                    att_err = sanitize_source_error(att.get("error"))
                    if att_err:
                        attempt_errors_by_provider[p_k] = att_err
                        attempt_err_counts_by_provider[p_k] = attempt_err_counts_by_provider.get(p_k, 0) + 1

                active_provider = chosen_provider or self.get_provider(shot_providers[0], manifest)
                shot.search_attempts = attempts_log[-25:]

                # Step 4: Fallback to fake ONLY in dev/test mode; otherwise mark no visual cleanly (D8)
                if not candidates:
                    if allow_fake_fallback and not active_provider.is_fake:
                        fallback_reason = f"Provider '{active_provider.name}' found 0 valid candidates for query; fell back to fake"
                        active_provider = FakeVisualProvider(mode=target_provider_name)
                        try:
                            candidates = active_provider.search_candidates(
                                query=raw_query,
                                target_duration=shot.duration_seconds,
                                limit=3,
                                required_subject_terms=req_terms,
                                blocked_terms=block_terms,
                                visual_keywords=visual_kws,
                            )
                        except TypeError:
                            candidates = active_provider.search_candidates(
                                query=raw_query,
                                target_duration=shot.duration_seconds,
                                limit=3,
                            )

                    if not candidates:
                        # D8 / D9.1: Mark shot cleanly as 'Belum ada visual' with structured failure reason
                        reason_summary = self._summarize_search_failures(attempts_log)
                        logger.warning(f"Shot {shot.shot_id}: no acceptable candidates found ({reason_summary}). Marked 'no_visual_found'.")
                        shot.asset_path = None
                        shot.rendered_clip = None
                        shot.shot_hash = None
                        shot.asset_content_hash = None
                        shot.actual_source = None
                        shot.is_fake = False
                        shot.needs_review = True
                        shot.status = ItemStatus.pending
                        shot.relevance_score = 0.0
                        shot.tags = []
                        shot.candidates = []
                        shot.fallback_reason = "no_visual_found"
                        shot.no_visual_reason_summary = reason_summary
                        shot.attribution_title = None
                        shot.attribution_author = None
                        shot.attribution_author_url = None
                        shot.attribution_license = None
                        shot.attribution_url = None
                        shot.requires_attribution = False
                        invalidate_shot_thumbnail_cache(self.project_dir, shot.shot_id)
                        if shot.shot_id in target_fetch_ids and target_fetch_total > 0:
                            done_fetch_count += 1
                            pct = _pct_start + ((done_fetch_count / target_fetch_total) * (_pct_end - _pct_start))
                            update_job_phase(
                                self.project_dir,
                                phase=f"Mencari footage ({done_fetch_count}/{target_fetch_total} shot)…",
                                phase_key="prog_visual_fetch",
                                phase_args={"done": done_fetch_count, "total": target_fetch_total},
                                percentage=pct,
                                current_unit=done_fetch_count,
                                total_units=target_fetch_total,
                                skipped_units=skipped_fetch_count,
                                unit_label="shot",
                                is_indeterminate=False,
                                partial=True,
                            )
                        continue

                # V1: In Mix Best mode, select winner with Local priority, tie-breaking <= 0.02 by least-used source, and diversity limits
                if use_waterfall and eff_search_mode == "mix_best" and candidates:
                    selected_candidate = self._select_candidate_mix_best(
                        candidates,
                        providers_order=shot_providers,
                        source_usage_counts=source_usage_counts,
                        recent_sources=recent_sources,
                        total_project_shots=total_project_shots,
                        max_source_share=max_source_share,
                        avoid_consecutive_same_source=avoid_consec_same,
                        prioritize_local=("local" in shot_providers),
                    )
                    reordered = [selected_candidate] + [
                        c for c in candidates
                        if not (c.provider == selected_candidate.provider and c.candidate_id == selected_candidate.candidate_id)
                    ]
                    candidates = reordered[:8]
                else:
                    selected_candidate = candidates[0]

                # Always resolve download provider from candidate.provider (never assumed active_provider)
                if not getattr(active_provider, "is_fake", False) or str(selected_candidate.provider).lower().strip() == "fake":
                    if str(selected_candidate.provider).lower().strip() != str(active_provider.name).lower().strip():
                        active_provider = self.get_provider(selected_candidate.provider, manifest)
                origin_map = getattr(self, "_last_shot_origin_queries", {}) or {}
                chosen_query = origin_map.get((selected_candidate.provider, selected_candidate.candidate_id), chosen_query)

                # Store candidate options
                shot.candidates = [c.to_dict() for c in candidates]
                used_candidate_ids.add(selected_candidate.candidate_id)
                win_prov = str(active_provider.name).lower().strip()
                source_usage_counts[win_prov] = source_usage_counts.get(win_prov, 0) + 1
                recent_sources.append(win_prov)

                # Fetch selected asset
                ext = ".mp4" if selected_candidate.source_type == "stock_video" else ".jpg"
                rel_dest = f"assets/visual/shots/{shot.shot_id}_asset{ext}"
                dest_file = self.project_dir / rel_dest

                logger.info(f"Fetching asset for {shot.shot_id} via {active_provider.name} (score: {selected_candidate.relevance_score:.2f}, {selected_candidate.rendition_name or 'std'})...")
                _t_dl0 = time.monotonic()
                asset = active_provider.fetch_asset(selected_candidate, dest_file)
                asset_download_seconds += time.monotonic() - _t_dl0
                asset_local = Path(getattr(asset, "local_path", None) or dest)
                try:
                    rel_dest = asset_local.resolve().relative_to(self.project_dir.resolve()).as_posix()
                except Exception:
                    rel_dest = f"assets/visual/shots/{asset_local.name}"
                invalidate_shot_thumbnail_cache(self.project_dir, shot.shot_id)

                # Update shot metadata & D9.3 attribution info
                shot.asset_path = rel_dest
                shot.asset_content_hash = asset.content_hash
                shot.source_candidate_id = selected_candidate.candidate_id
                shot.requested_source = active_provider.name if use_waterfall else effective_provider_name
                shot.actual_source = active_provider.name
                shot.is_fake = active_provider.is_fake
                shot.fallback_reason = fallback_reason
                shot.no_visual_reason_summary = None
                shot.tags = selected_candidate.tags
                shot.relevance_score = selected_candidate.relevance_score
                shot.chosen_rendition = selected_candidate.rendition_name
                shot.is_low_res = getattr(selected_candidate, "is_low_res", False)
                shot.effective_query = chosen_query
                shot.attribution_title = getattr(selected_candidate, "title", None) or getattr(asset, "title", None)
                shot.attribution_author = getattr(selected_candidate, "author", None) or getattr(asset, "author", None)
                shot.attribution_author_url = getattr(selected_candidate, "author_url", None) or getattr(asset, "author_url", None)
                shot.attribution_license = getattr(selected_candidate, "license", None) or getattr(asset, "license", None)
                shot.attribution_url = getattr(selected_candidate, "source_url", None) or getattr(asset, "source_url", None)
                shot.requires_attribution = license_requires_attribution(shot.attribution_license)
                shot.ai_relevance_status = None
                shot.ai_relevance_reason = None
                shot.ai_relevance_asset_hash = None
                self.credits_manager.record_asset_credit(selected_candidate, shot_id=shot.shot_id)
                if req_terms:
                    from app.services.longform.visual.stock import term_matches_tags
                    has_subj = any(term_matches_tags(req, shot.tags or []) for req in req_terms)
                    shot.needs_review = not has_subj
                elif proj_anchors and not shot.is_fake and shot.tags:
                    from app.services.longform.visual.stock import term_matches_tags
                    has_anchor = any(term_matches_tags(anc, shot.tags or []) for anc in proj_anchors)
                    shot.needs_review = not has_anchor
                else:
                    shot.needs_review = False
                has_stale_clip = bool(shot.rendered_clip and (self.project_dir / shot.rendered_clip).is_file())
                if has_stale_clip:
                    shot.shot_hash = None
                    shot.status = ItemStatus.stale
                else:
                    shot.status = ItemStatus.pending
                self._save_manifest_timed(manifest)
                if shot.shot_id in target_fetch_ids and target_fetch_total > 0:
                    done_fetch_count += 1
                    pct = _pct_start + ((done_fetch_count / target_fetch_total) * (_pct_end - _pct_start))
                    update_job_phase(
                        self.project_dir,
                        phase=f"Mencari footage ({done_fetch_count}/{target_fetch_total} shot)…",
                        phase_key="prog_visual_fetch",
                        phase_args={"done": done_fetch_count, "total": target_fetch_total},
                        percentage=pct,
                        current_unit=done_fetch_count,
                        total_units=target_fetch_total,
                        skipped_units=skipped_fetch_count,
                        unit_label="shot",
                        is_indeterminate=False,
                        partial=True,
                    )

        if translation_warnings:
            logger.warning(f"Keyword translation warnings for '{manifest.project_id}': {translation_warnings[0]}")

        # F1: Optional AI Vision relevance verification (default off, strictly after fetch)
        if check_ai_relevance and not stop_requested and not is_stop_requested(self.project_dir):
            from app.services.longform.visual.ai_relevance import check_project_relevance

            if target_fetch_total > 0:
                update_job_phase(
                    self.project_dir,
                    phase=f"Mengecek relevansi AI ({done_fetch_count}/{target_fetch_total} shot)…",
                    phase_key="prog_visual_ai_relevance",
                    phase_args={"done": done_fetch_count, "total": target_fetch_total},
                    percentage=_pct_end,
                    current_unit=done_fetch_count,
                    total_units=target_fetch_total,
                    skipped_units=skipped_fetch_count,
                    unit_label="shot",
                    is_indeterminate=True,
                    partial=True,
                )
            _t_air0 = time.monotonic()
            rel_stats = check_project_relevance(manifest, self.project_dir)
            ai_rel_seconds += time.monotonic() - _t_air0
            logger.info(
                f"AI relevance check completed for '{manifest.project_id}': "
                f"checked={rel_stats.get('checked')}, cached={rel_stats.get('cached')}, "
                f"ya={rel_stats.get('ya')}, tidak={rel_stats.get('tidak')}, ragu={rel_stats.get('ragu')}"
            )
            self._save_manifest_timed(manifest)

        try:
            self.credits_manager.write_credits_markdown(manifest)
        except Exception as cred_exc:
            logger.warning(f"Failed to write credits.md for '{manifest.project_id}': {cred_exc}")

        # V1: Record structured search/distribution telemetry for benchmark & UI
        dist_counts: Dict[str, int] = {}
        rel_scores: List[float] = []
        for _ch in manifest.chapters:
            for _sh in _ch.shots:
                if _sh.actual_source:
                    sk = str(_sh.actual_source).lower().strip()
                    dist_counts[sk] = dist_counts.get(sk, 0) + 1
                if _sh.relevance_score is not None and float(_sh.relevance_score) > 0:
                    rel_scores.append(float(_sh.relevance_score))
        io_snap = snapshot_io_telemetry()
        with self._telemetry_lock:
            search_sec_by_prov = {k: round(v, 3) for k, v in (self._active_search_seconds or {}).items()}
            manifest_save_sec_fetch = round(self._active_manifest_save_seconds, 3)
            manifest_save_cnt_fetch = int(self._active_manifest_save_count)
            self._active_search_seconds = None

        search_per_source: Dict[str, Dict[str, Any]] = {}
        all_prov_keys = set(requests_by_provider.keys()) | set(search_sec_by_prov.keys()) | set(io_snap["search_cache_hits"].keys()) | set(io_snap["search_http_requests"].keys())
        for pk in sorted(all_prov_keys):
            search_per_source[pk] = {
                "tier_attempts": int(requests_by_provider.get(pk, 0)),
                "http_requests": int(io_snap["search_http_requests"].get(pk, 0)),
                "cache_hits": int(io_snap["search_cache_hits"].get(pk, 0)),
                "elapsed_seconds": float(search_sec_by_prov.get(pk, 0.0)),
            }

        ev_map = io_snap.get("provider_status_events") or {}
        source_status: Dict[str, Dict[str, Any]] = {}
        status_keys = sorted(
            all_prov_keys
            | set(ev_map.keys())
            | set(attempt_errors_by_provider.keys())
        )
        for pk in status_keys:
            ev = ev_map.get(pk) or {}
            cd_cnt = int(ev.get("cooldown") or 0)
            to_cnt = int(ev.get("timeout") or 0)
            auth_blk = bool(ev.get("auth_blocked", False))
            last_err = sanitize_source_error(ev.get("last_error") or attempt_errors_by_provider.get(pk))
            if pk == "coverr":
                try:
                    from app.services.longform.visual.coverr_client import get_coverr_auth_status

                    c_auth = get_coverr_auth_status()
                    if c_auth.get("auth_blocked"):
                        auth_blk = True
                        if not last_err:
                            last_err = sanitize_source_error(c_auth.get("error") or "HTTP 403")
                except Exception:
                    pass
            if last_err and not auth_blk and to_cnt == 0 and cd_cnt == 0:
                low_e = last_err.lower()
                if "auth_blocked" in low_e or "http 401" in low_e or "http 403" in low_e:
                    auth_blk = True
                elif "timeout" in low_e or "timed out" in low_e or "504" in low_e or "503" in low_e or "502" in low_e:
                    to_cnt = max(1, int(attempt_err_counts_by_provider.get(pk, 1)))
            is_ok = (not auth_blk) and (cd_cnt == 0) and (to_cnt == 0) and (not last_err)
            source_status[pk] = {
                "ok": is_ok,
                "cooldown": cd_cnt,
                "auth_blocked": auth_blk,
                "timeout": to_cnt,
                "last_error": last_err,
            }

        fetch_elapsed = round(time.monotonic() - fetch_t0, 3)
        self._last_fetch_metrics = {
            "search_mode": eff_search_mode if use_waterfall else "single",
            "elapsed_seconds": fetch_elapsed,
            "anchor_seconds": round(anchor_seconds, 3),
            "anchor_llm_calls": anchor_llm_calls,
            "ai_keywords_seconds": round(ai_kw_seconds, 3),
            "ai_keywords_llm_calls": ai_kw_llm_calls,
            "ai_keywords_retries": ai_kw_retries,
            "ai_keywords_cached": ai_kw_cached,
            "translate_seconds": round(translate_seconds, 3),
            "search_wall_seconds": round(search_wall_seconds, 3),
            "search_per_source": search_per_source,
            "source_status": source_status,
            "thumbnail_seconds": round(thumbnail_seconds, 3),
            "asset_download_seconds": round(asset_download_seconds, 3),
            "download_cache_hits": int(io_snap["download_cache_hits"]),
            "download_misses": int(io_snap["download_misses"]),
            "ai_relevance_seconds": round(ai_rel_seconds, 3),
            "manifest_save_seconds_fetch": manifest_save_sec_fetch,
            "manifest_save_count_fetch": manifest_save_cnt_fetch,
            "requests_total": sum(requests_by_provider.values()),
            "requests_by_provider": dict(requests_by_provider),
            "source_distribution": dist_counts,
            "avg_relevance_score": round(sum(rel_scores) / len(rel_scores), 3) if rel_scores else 0.0,
            "assigned_shots": sum(dist_counts.values()),
            "total_shots": len(all_shots_flat),
        }

        try:
            metrics_file = self.project_dir / "visual_metrics.json"
            existing_m: Dict[str, Any] = {}
            if metrics_file.is_file():
                try:
                    existing_m = json.loads(metrics_file.read_text(encoding="utf-8"))
                    if not isinstance(existing_m, dict):
                        existing_m = {}
                except Exception:
                    existing_m = {}
            existing_m["source_status"] = source_status
            existing_m.setdefault("project_id", manifest.project_id)
            metrics_tmp = metrics_file.with_suffix(".json.tmp")
            metrics_tmp.write_text(json.dumps(existing_m, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(metrics_tmp, metrics_file)
        except Exception:
            pass

        logger.info(
            f"[Visual Fetch Telemetry] project='{manifest.project_id}' mode={self._last_fetch_metrics['search_mode']} "
            f"total={fetch_elapsed:.2f}s | anchors={anchor_seconds:.2f}s (llm={anchor_llm_calls}) | "
            f"ai_keywords={ai_kw_seconds:.2f}s (llm={ai_kw_llm_calls}, retries={ai_kw_retries}, cached={ai_kw_cached}) | "
            f"search_wall={search_wall_seconds:.2f}s | asset_download={asset_download_seconds:.2f}s "
            f"(dl_cache_hit={io_snap['download_cache_hits']}, dl_miss={io_snap['download_misses']}) | "
            f"thumbnails={thumbnail_seconds:.3f}s (lazy_ui) | manifest_save={manifest_save_sec_fetch:.2f}s ({manifest_save_cnt_fetch}x)"
        )
        if search_per_source:
            prov_parts = [
                f"{pk}:tiers={st['tier_attempts']},http={st['http_requests']},hit={st['cache_hits']},sec={st['elapsed_seconds']:.2f}s"
                for pk, st in search_per_source.items()
            ]
            logger.info(f"[Visual Fetch Search Sources] {' | '.join(prov_parts)}")

        return manifest

    def build_all(
        self,
        manifest: ProjectManifest,
        use_neutral_fallback: bool = False,
        _pct_start: float = 0.0,
        _pct_end: float = 99.0,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Build rendered video clips for all shots in manifest (4.3, V1, D8).

        V1 fix: shot hash now includes requested_provider + actual_provider so that
        switching providers correctly invalidates the rendered clip.
        D8: shots without a fetched asset are skipped cleanly instead of failing the
        entire batch, unless use_neutral_fallback=True (user confirmed continue in
        Render / CapCut), which builds a clean subtle dark-gradient backdrop clip.
        """
        fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
        builder = ShotClipBuilder(fps=fps)
        built_count = 0
        skipped_count = 0
        missing_asset_count = 0
        neutral_built_count = 0
        total_time = 0.0

        # F15: Pre-partition target build shots vs already-valid skipped shots
        expected_hashes_by_id: Dict[str, str] = {}
        target_build_ids: Set[str] = set()
        total_project_shots = 0
        for ch in manifest.chapters:
            for shot in ch.shots:
                total_project_shots += 1
                is_legacy_fake_shot = bool(shot.is_fake or str(shot.actual_source or "").lower() == "fake")
                asset_ok = bool(shot.asset_path and (self.project_dir / shot.asset_path).is_file())
                needs_neutral = bool(use_neutral_fallback and (not asset_ok or is_legacy_fake_shot))
                if not asset_ok and not needs_neutral:
                    expected_hashes_by_id[shot.shot_id] = ""
                    continue
                eff_hash_input = "neutral_backdrop_v1" if needs_neutral else (shot.asset_content_hash or "")
                eff_actual = "neutral_backdrop" if needs_neutral else (shot.actual_source or "")
                expected_hash = compute_shot_hash(
                    shot_id=shot.shot_id,
                    duration_seconds=shot.duration_seconds,
                    target_frames=shot.target_frames,
                    motion=shot.motion.value,
                    asset_content_hash=eff_hash_input,
                    fps=fps,
                    requested_provider=shot.requested_source or "",
                    actual_provider=eff_actual,
                )
                expected_hashes_by_id[shot.shot_id] = expected_hash
                clip_path = (self.project_dir / f"assets/visual/shots/{shot.shot_id}.mp4").resolve()
                is_already_valid = (
                    shot.shot_hash == expected_hash
                    and clip_path.is_file()
                    and shot.status == ItemStatus.completed
                )
                if not is_already_valid:
                    target_build_ids.add(shot.shot_id)

        target_build_total = len(target_build_ids)
        skipped_build_init = total_project_shots - target_build_total
        if target_build_total > 0:
            update_job_phase(
                self.project_dir,
                phase=f"Menyusun klip (0/{target_build_total} shot)…",
                phase_key="prog_visual_build",
                phase_args={"done": 0, "total": target_build_total},
                percentage=_pct_start,
                current_unit=0,
                total_units=target_build_total,
                skipped_units=skipped_build_init,
                unit_label="shot",
                is_indeterminate=False,
                partial=True,
            )

        stop_requested = False
        for ch in manifest.chapters:
            if is_stop_requested(self.project_dir):
                logger.info(f"Stop requested for project '{manifest.project_id}'. Exiting visual matching gracefully.")
                stop_requested = True
                break
            for shot in ch.shots:
                if is_stop_requested(self.project_dir):
                    logger.info(f"Stop requested for project '{manifest.project_id}'. Exiting visual matching gracefully.")
                    stop_requested = True
                    break

                is_legacy_fake_shot = bool(shot.is_fake or str(shot.actual_source or "").lower() == "fake")
                asset_file = (self.project_dir / shot.asset_path).resolve() if shot.asset_path else None
                asset_exists = bool(asset_file and asset_file.is_file())
                needs_neutral = bool(use_neutral_fallback and (not asset_exists or is_legacy_fake_shot))

                if not asset_exists and not needs_neutral:
                    missing_asset_count += 1
                    logger.warning(f"Skipping shot {shot.shot_id}: no visual asset available yet.")
                    continue

                expected_hash = expected_hashes_by_id[shot.shot_id]
                clip_rel = f"assets/visual/shots/{shot.shot_id}.mp4"
                clip_path = (self.project_dir / clip_rel).resolve()

                # Check if already completed and hash matches
                if shot.shot_hash == expected_hash and clip_path.is_file() and shot.status == ItemStatus.completed:
                    skipped_count += 1
                    continue

                if needs_neutral:
                    logger.info(f"Building neutral backdrop clip for {shot.shot_id} ({shot.target_frames} frames)...")
                    _, elapsed = builder.build_neutral_backdrop_clip(
                        output_path=clip_path,
                        target_frames=shot.target_frames,
                    )
                    built_count += 1
                    neutral_built_count += 1
                    total_time += elapsed
                    shot.asset_path = None
                    shot.asset_content_hash = "neutral_backdrop_v1"
                    shot.actual_source = "neutral_backdrop"
                    shot.is_fake = False
                    shot.fallback_reason = "neutral_backdrop_confirmed"
                else:
                    logger.info(f"Building shot clip {shot.shot_id} ({shot.target_frames} frames, {shot.motion.value})...")
                    _, elapsed = builder.build_shot_clip(
                        asset_path=asset_file,
                        output_path=clip_path,
                        motion=shot.motion,
                        target_frames=shot.target_frames,
                    )
                    built_count += 1
                    total_time += elapsed

                    # S2: In case source trimming modified asset_file, update asset content hash & shot hash
                    updated_content_hash = compute_file_content_hash(asset_file)
                    shot.asset_content_hash = updated_content_hash

                shot.shot_hash = compute_shot_hash(
                    shot_id=shot.shot_id,
                    duration_seconds=shot.duration_seconds,
                    target_frames=shot.target_frames,
                    motion=shot.motion.value,
                    asset_content_hash=shot.asset_content_hash,
                    fps=fps,
                    requested_provider=shot.requested_source or "",
                    actual_provider=shot.actual_source or "",
                )
                shot.rendered_clip = clip_rel
                shot.status = ItemStatus.completed
                invalidate_shot_thumbnail_cache(self.project_dir, shot.shot_id)
                self._save_manifest_timed(manifest)

                if target_build_total > 0:
                    pct = _pct_start + ((built_count / target_build_total) * (_pct_end - _pct_start))
                    update_job_phase(
                        self.project_dir,
                        phase=f"Menyusun klip ({built_count}/{target_build_total} shot)…",
                        phase_key="prog_visual_build",
                        phase_args={"done": built_count, "total": target_build_total},
                        percentage=pct,
                        current_unit=built_count,
                        total_units=target_build_total,
                        skipped_units=skipped_build_init,
                        unit_label="shot",
                        is_indeterminate=False,
                        partial=True,
                    )

            # Update chapter duration & frames
            ch.visual_duration_seconds = round(sum(s.duration_seconds for s in ch.shots), 3)
            ch.visual_frames = sum(s.target_frames for s in ch.shots)

        avg_speed = total_time / built_count if built_count > 0 else 0.0
        stats = {
            "built_count": built_count,
            "skipped_count": skipped_count,
            "missing_asset_count": missing_asset_count,
            "neutral_built_count": neutral_built_count,
            "total_build_seconds": round(total_time, 2),
            "average_seconds_per_clip": round(avg_speed, 2),
        }
        return manifest, stats

    def get_chapter_actual_frames(self, chapter: "Chapter") -> Optional[int]:
        """Sum actual video frame counts from all rendered clip files using ffprobe (V2).

        Returns None if any clip cannot be probed (falls back to manifest value).
        """
        total = 0
        for shot in chapter.shots:
            if not shot.rendered_clip:
                return None
            clip_path = (self.project_dir / shot.rendered_clip).resolve()
            if not clip_path.is_file():
                return None
            n = _get_clip_frame_count(clip_path)
            if n is None:
                return None
            total += n
        return total

    def generate_preview(
        self,
        manifest: ProjectManifest,
        chapter_index: int,
    ) -> Path:
        """Concatenate all rendered shot clips in a chapter with chapter audio (4.4, V4).

        V4: FFmpeg subprocess is started with Popen so KeyboardInterrupt can terminate it cleanly.
        Partial output file is removed if interrupted.
        """
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

        # Run FFmpeg concat with audio (V4: use Popen for clean interrupt handling)
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
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, stderr_bytes = proc.communicate()
            returncode = proc.returncode
        except KeyboardInterrupt:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            concat_txt.unlink(missing_ok=True)
            if preview_file.is_file():
                preview_file.unlink(missing_ok=True)
            raise

        concat_txt.unlink(missing_ok=True)

        if returncode != 0 or not preview_file.is_file():
            stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")[-500:]
            raise VisualError(f"FFmpeg visual preview concat failed: {stderr_text}")

        return preview_file

    def generate_candidate_preview_grid(
        self,
        shot: Shot,
        candidates: List[VisualCandidate],
        output_path: Path,
    ) -> Path:
        """Create a temporary preview contact grid image of up to 5 candidates for manual review (T4)."""
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        n = len(candidates)
        if n == 0:
            raise ValueError(f"No candidates to preview for shot {shot.shot_id}")

        tile_w = 320
        tile_h = 180
        pad = 16
        header_h = 45
        footer_h = 75
        col_w = tile_w
        total_w = pad + n * (col_w + pad)
        total_h = header_h + tile_h + footer_h + pad

        grid_img = Image.new("RGB", (total_w, total_h), color=(20, 22, 26))
        draw = ImageDraw.Draw(grid_img)

        # Header line
        draw.text(
            (pad, 14),
            f"Candidate Options for Shot {shot.shot_id} (Duration: {shot.duration_seconds:.2f}s | Target: {shot.target_frames} frames)",
            fill=(255, 255, 255),
        )

        for idx, cand in enumerate(candidates):
            x = pad + idx * (col_w + pad)
            y = header_h

            # Extract small thumbnail
            thumb = None
            if cand.local_path and Path(cand.local_path).is_file():
                try:
                    thumb = Image.open(cand.local_path).convert("RGB")
                    thumb.thumbnail((tile_w, tile_h))
                except Exception:
                    pass
            elif cand.url:
                tmp_thumb = output_path.parent / f"thumb_{shot.shot_id}_{idx}.jpg"
                try:
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", "0.5",
                        "-i", cand.url,
                        "-vframes", "1",
                        "-s", f"{tile_w}x{tile_h}",
                        str(tmp_thumb),
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=12)
                    if tmp_thumb.is_file():
                        thumb = Image.open(tmp_thumb).convert("RGB")
                except Exception:
                    pass
                finally:
                    if tmp_thumb.exists():
                        tmp_thumb.unlink(missing_ok=True)

            if thumb is None:
                thumb = Image.new("RGB", (tile_w, tile_h), color=(35, 40, 48))
                tdraw = ImageDraw.Draw(thumb)
                tdraw.text((15, tile_h // 2 - 10), f"Preview unavailable\n{cand.candidate_id}", fill=(180, 180, 180))

            # Paste thumb centered in tile
            tx = x + (tile_w - thumb.width) // 2
            ty = y + (tile_h - thumb.height) // 2
            grid_img.paste(thumb, (tx, ty))

            # Outline
            draw.rectangle([x, y, x + tile_w, y + tile_h], outline=(70, 80, 95), width=1)

            # Footer info
            score_color = (100, 230, 120) if cand.relevance_score >= 0.50 else (240, 200, 60) if cand.relevance_score >= 0.35 else (255, 80, 80)
            draw.text((x, y + tile_h + 6), f"[{idx + 1}] {cand.candidate_id}", fill=(255, 255, 255))
            draw.text((x + tile_w - 90, y + tile_h + 6), f"Score: {cand.relevance_score:.2f}", fill=score_color)

            res_label = f"{cand.width}x{cand.height}"
            if cand.is_low_res:
                res_label += " [LOW RES]"
            draw.text((x, y + tile_h + 24), res_label, fill=(180, 190, 200))

            # First 5 tags
            tags_snippet = ", ".join(cand.tags[:5])
            if len(tags_snippet) > 42:
                tags_snippet = tags_snippet[:39] + "..."
            draw.text((x, y + tile_h + 42), tags_snippet, fill=(140, 150, 160))

        grid_img.save(output_path, format="JPEG", quality=90)
        return output_path

    def search_and_preview_candidates(
        self,
        manifest: ProjectManifest,
        shot_id: str,
        search_query: Optional[str] = None,
        provider_name: Optional[str] = None,
        limit: int = 5,
    ) -> Tuple[List[VisualCandidate], Path]:
        """Search top candidates for shot and create preview grid file (T4)."""
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

        raw_prov = str(provider_name or "").strip().lower()
        use_multi = (not raw_prov) or raw_prov == "auto"
        prov_order = self.resolve_provider_waterfall(manifest) if use_multi else [raw_prov]
        candidates, chosen_provider, _q_used, _att, _fb = self._search_shot_across_providers(
            target_shot,
            manifest,
            providers_order=prov_order,
            explicit_prompt=search_query,
            limit=limit,
            excluded_ids=None,
            allow_waterfall=use_multi,
            allow_fake_fallback=False,
            strict_ai_relevance=False,
        )

        if not candidates:
            q = search_query or getattr(target_shot, "search_query_en", None) or target_shot.search_query or target_shot.visual_prompt or "cinematic scene"
            raise VisualError(f"No valid candidates found for shot {shot_id} with query '{q}'")

        target_shot.candidates = [c.to_dict() for c in candidates]
        if search_query:
            target_shot.effective_query = search_query

        grid_file = self.project_dir / "export" / f"temp_replace_{shot_id}_grid.jpg"
        self.generate_candidate_preview_grid(target_shot, candidates, grid_file)
        return candidates, grid_file

    def replace_shot(
        self,
        manifest: ProjectManifest,
        shot_id: str,
        file_path: Optional[Path | str] = None,
        candidate_index: Optional[int] = None,
        provider_name: Optional[str] = None,
        prompt: Optional[str] = None,
        rebuild: bool = False,
        check_ai_relevance: bool = False,
    ) -> Shot:
        """Replace visual asset for a shot, lock it, and immediately re-build clip (T4)."""
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

        fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
        builder = ShotClipBuilder(fps=fps)
        clip_rel = f"assets/visual/shots/{shot_id}.mp4"
        clip_path = (self.project_dir / clip_rel).resolve()

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
            target_shot.is_fake = False
            target_shot.fallback_reason = None
            target_shot.no_visual_reason_summary = None
            target_shot.attribution_title = src.name
            target_shot.attribution_author = None
            target_shot.attribution_license = "Local Library"
            target_shot.attribution_url = None
            target_shot.requires_attribution = False
            target_shot.locked = True  # T4 auto-lock
            target_shot.needs_review = False

            if rebuild:
                builder.build_shot_clip(
                    asset_path=dest,
                    output_path=clip_path,
                    motion=target_shot.motion,
                    target_frames=target_shot.target_frames,
                )
                target_shot.rendered_clip = clip_rel
                target_shot.status = ItemStatus.completed
                target_shot.shot_hash = compute_shot_hash(
                    shot_id=target_shot.shot_id,
                    duration_seconds=target_shot.duration_seconds,
                    target_frames=target_shot.target_frames,
                    motion=target_shot.motion.value,
                    asset_content_hash=target_shot.asset_content_hash,
                    fps=fps,
                    requested_provider=target_shot.requested_source or "",
                    actual_provider=target_shot.actual_source or "",
                )
            else:
                has_stale_clip = bool(target_shot.rendered_clip and (self.project_dir / target_shot.rendered_clip).is_file())
                target_shot.status = ItemStatus.stale if has_stale_clip else ItemStatus.pending
                target_shot.shot_hash = None
            invalidate_shot_thumbnail_cache(self.project_dir, shot_id)
            return target_shot

        if candidate_index is not None:
            # 1-indexed to 0-indexed conversion
            idx = (candidate_index - 1) if candidate_index >= 1 else candidate_index
            if not target_shot.candidates or idx >= len(target_shot.candidates):
                raise ValueError(f"Candidate index {candidate_index} out of range for shot {shot_id}")
            cand_dict = target_shot.candidates[idx]
            candidate = VisualCandidate.from_dict(cand_dict)
            provider = self.get_provider(candidate.provider, manifest)
            ext = ".mp4" if candidate.source_type == "stock_video" else ".jpg"
            rel_dest = f"assets/visual/shots/{shot_id}_asset{ext}"
            dest = self.project_dir / rel_dest
            asset = provider.fetch_asset(candidate, dest)
            asset_local = Path(getattr(asset, "local_path", None) or dest)
            try:
                rel_dest = asset_local.resolve().relative_to(self.project_dir.resolve()).as_posix()
            except Exception:
                rel_dest = f"assets/visual/shots/{asset_local.name}"
            dest = (self.project_dir / rel_dest).resolve()

            target_shot.asset_path = rel_dest
            target_shot.asset_content_hash = asset.content_hash
            target_shot.source_candidate_id = candidate.candidate_id
            target_shot.actual_source = candidate.provider
            target_shot.is_fake = False
            target_shot.fallback_reason = None
            target_shot.no_visual_reason_summary = None
            target_shot.attribution_title = getattr(candidate, "title", None) or getattr(asset, "title", None)
            target_shot.attribution_author = getattr(candidate, "author", None) or getattr(asset, "author", None)
            target_shot.attribution_author_url = getattr(candidate, "author_url", None) or getattr(asset, "author_url", None)
            target_shot.attribution_license = getattr(candidate, "license", None) or getattr(asset, "license", None)
            target_shot.attribution_url = getattr(candidate, "source_url", None) or getattr(asset, "source_url", None)
            target_shot.requires_attribution = license_requires_attribution(target_shot.attribution_license)
            if hasattr(self.credits_manager, "record_asset_credit"):
                self.credits_manager.record_asset_credit(candidate, shot_id=target_shot.shot_id)
            self.credits_manager.write_credits_markdown(manifest)
            target_shot.tags = candidate.tags
            target_shot.relevance_score = candidate.relevance_score
            target_shot.is_low_res = candidate.is_low_res
            target_shot.locked = True  # T4 auto-lock
            target_shot.needs_review = False

            if rebuild:
                builder.build_shot_clip(
                    asset_path=dest,
                    output_path=clip_path,
                    motion=target_shot.motion,
                    target_frames=target_shot.target_frames,
                )
                target_shot.rendered_clip = clip_rel
                target_shot.status = ItemStatus.completed
                target_shot.shot_hash = compute_shot_hash(
                    shot_id=target_shot.shot_id,
                    duration_seconds=target_shot.duration_seconds,
                    target_frames=target_shot.target_frames,
                    motion=target_shot.motion.value,
                    asset_content_hash=target_shot.asset_content_hash,
                    fps=fps,
                    requested_provider=target_shot.requested_source or "",
                    actual_provider=target_shot.actual_source or "",
                )
            else:
                has_stale_clip = bool(target_shot.rendered_clip and (self.project_dir / target_shot.rendered_clip).is_file())
                target_shot.status = ItemStatus.stale if has_stale_clip else ItemStatus.pending
                target_shot.shot_hash = None
            invalidate_shot_thumbnail_cache(self.project_dir, shot_id)
            return target_shot

        if provider_name or prompt:
            raw_prov = str(provider_name or "").strip().lower()
            use_multi = (not raw_prov) or raw_prov == "auto"
            prov_order = self.resolve_provider_waterfall(manifest) if use_multi else [raw_prov]
            q_input = prompt or getattr(target_shot, "search_query_en", None) or target_shot.search_query or target_shot.visual_prompt
            cands, used_prov, used_q, attempts_log, _fb = self._search_shot_across_providers(
                target_shot,
                manifest,
                providers_order=prov_order,
                explicit_prompt=q_input,
                limit=5,
                excluded_ids=None,
                allow_waterfall=use_multi,
                allow_fake_fallback=False,
                strict_ai_relevance=bool(check_ai_relevance),
            )
            target_shot.search_attempts = attempts_log[-25:]
            if not cands:
                target_shot.no_visual_reason_summary = self._summarize_search_failures(attempts_log)
                raise VisualError(f"No valid candidates found for query '{q_input}'")
            sel = cands[0]
            prov = self.get_provider(sel.provider, manifest) if str(getattr(sel, "provider", "") or "").lower() != "fake" else (used_prov or self.get_provider(prov_order[0], manifest))
            q = used_q or q_input
            target_shot.candidates = [c.to_dict() for c in cands]
            ext = ".mp4" if sel.source_type == "stock_video" else ".jpg"
            rel_dest = f"assets/visual/shots/{shot_id}_asset{ext}"
            dest = self.project_dir / rel_dest
            asset = prov.fetch_asset(sel, dest)
            asset_local = Path(getattr(asset, "local_path", None) or dest)
            try:
                rel_dest = asset_local.resolve().relative_to(self.project_dir.resolve()).as_posix()
            except Exception:
                rel_dest = f"assets/visual/shots/{asset_local.name}"
            dest = (self.project_dir / rel_dest).resolve()

            target_shot.asset_path = rel_dest
            target_shot.asset_content_hash = asset.content_hash
            target_shot.source_candidate_id = sel.candidate_id
            target_shot.actual_source = prov.name
            target_shot.is_fake = prov.is_fake
            target_shot.fallback_reason = None
            target_shot.no_visual_reason_summary = None
            target_shot.attribution_title = getattr(sel, "title", None) or getattr(asset, "title", None)
            target_shot.attribution_author = getattr(sel, "author", None) or getattr(asset, "author", None)
            target_shot.attribution_author_url = getattr(sel, "author_url", None) or getattr(asset, "author_url", None)
            target_shot.attribution_license = getattr(sel, "license", None) or getattr(asset, "license", None)
            target_shot.attribution_url = getattr(sel, "source_url", None) or getattr(asset, "source_url", None)
            target_shot.requires_attribution = license_requires_attribution(target_shot.attribution_license)
            if hasattr(self.credits_manager, "record_asset_credit"):
                self.credits_manager.record_asset_credit(sel, shot_id=target_shot.shot_id)
            self.credits_manager.write_credits_markdown(manifest)
            target_shot.tags = sel.tags
            target_shot.relevance_score = sel.relevance_score
            target_shot.is_low_res = sel.is_low_res
            target_shot.effective_query = q
            target_shot.locked = True  # T4 auto-lock
            target_shot.needs_review = False

            if rebuild:
                builder.build_shot_clip(
                    asset_path=dest,
                    output_path=clip_path,
                    motion=target_shot.motion,
                    target_frames=target_shot.target_frames,
                )
                target_shot.rendered_clip = clip_rel
                target_shot.status = ItemStatus.completed
                target_shot.shot_hash = compute_shot_hash(
                    shot_id=target_shot.shot_id,
                    duration_seconds=target_shot.duration_seconds,
                    target_frames=target_shot.target_frames,
                    motion=target_shot.motion.value,
                    asset_content_hash=target_shot.asset_content_hash,
                    fps=fps,
                    requested_provider=target_shot.requested_source or "",
                    actual_provider=target_shot.actual_source or "",
                )
            else:
                has_stale_clip = bool(target_shot.rendered_clip and (self.project_dir / target_shot.rendered_clip).is_file())
                target_shot.status = ItemStatus.stale if has_stale_clip else ItemStatus.pending
                target_shot.shot_hash = None
            invalidate_shot_thumbnail_cache(self.project_dir, shot_id)
            return target_shot

        raise ValueError("Must provide --file, --candidate, or --search/--prompt for replace")

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

    def evaluate_needs_review(self, manifest: ProjectManifest) -> int:
        """Scan manifest shots and flag any shot lacking required_subject_terms or matching avoid_terms / missing anchor_terms (X4, F12)."""
        preset_snap = manifest.preset_snapshot or {}
        req_terms = preset_snap.get("required_subject_terms") or []
        proj_anchors = list(getattr(manifest, "visual_anchor_terms", []) or [])
        proj_avoids = list(getattr(manifest, "visual_avoid_terms", []) or [])
        if not req_terms and not proj_anchors and not proj_avoids:
            return 0

        from app.services.longform.visual.stock import term_matches_tags

        flagged = 0
        for ch in manifest.chapters:
            for s in ch.shots:
                if s.tags:
                    has_subj = (
                        any(term_matches_tags(req, s.tags) for req in req_terms)
                        if req_terms
                        else (any(term_matches_tags(anc, s.tags) for anc in proj_anchors) if proj_anchors else True)
                    )
                    has_avoid = any(term_matches_tags(av, s.tags) for av in proj_avoids) if proj_avoids else False
                    if (not has_subj or has_avoid) and not s.needs_review:
                        s.needs_review = True
                        flagged += 1
                elif s.asset_path and not s.locked:
                    if not s.needs_review:
                        s.needs_review = True
                        flagged += 1
        return flagged

    def update_keywords_with_ai(
        self,
        manifest: Optional[ProjectManifest] = None,
        *,
        chapter_index: Optional[int] = None,
        force: bool = True,
        mark_stale: bool = True,
        llm_adapter: Any = None,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Regenerate visual search keywords with AI for unlocked shots and mark changed shots stale (F8)."""
        from app.services.longform.visual.ai_keywords import update_project_visual_keywords_ai

        if manifest is None:
            manifest = load_manifest(self.project_dir)

        stats = update_project_visual_keywords_ai(
            manifest,
            chapter_index=chapter_index,
            force=force,
            mark_stale=mark_stale,
            llm_adapter=llm_adapter,
        )
        save_manifest(manifest, self.project_dir)
        return manifest, stats

    def run_pipeline(
        self,
        manifest: Optional[ProjectManifest] = None,
        provider_name: Optional[str] = None,
        providers_order: Optional[List[str]] = None,
        allow_fallback: bool = False,
        check_ai_relevance: bool = False,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Execute the full 3-phase visual pipeline sequentially (Allocation -> Fetch -> Build).

        Single canonical implementation used identically by CLI `visual pipeline`, WebUI button ③,
        and `pipeline_auto` (Stage 7B.2 Z3).
        """
        if manifest is None:
            manifest = load_manifest(self.project_dir)

        pipe_t0 = time.monotonic()
        with self._telemetry_lock:
            self._active_manifest_save_seconds = 0.0
            self._active_manifest_save_count = 0

        stats: Dict[str, Any] = {
            "allocated_shots": 0,
            "fetched_count": 0,
            "built_count": 0,
            "skipped_count": 0,
        }

        # Phase 1: Membagi shot (Shot Allocation) if 0 shots
        alloc_t0 = time.monotonic()
        total_shots = sum(len(ch.shots) for ch in manifest.chapters)
        if total_shots == 0:
            update_job_phase(
                self.project_dir,
                phase="Membagi shot…",
                phase_key="prog_visual_allocate",
                phase_args={},
                percentage=0.0,
                current_unit=0,
                total_units=0,
                skipped_units=0,
                unit_label="shot",
                is_indeterminate=True,
                partial=True,
            )
            print(f"\n[Visual Pipeline] Phase 1/3: Membagi shot untuk proyek '{manifest.project_id}'...")
            effective_fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
            manifest = self.prepare_shots(manifest, fps=effective_fps)
            self._save_manifest_timed(manifest)
            stats["allocated_shots"] = sum(len(ch.shots) for ch in manifest.chapters)
            print(f"  -> {stats['allocated_shots']} shot berhasil dialokasikan.")
        else:
            print(f"\n[Visual Pipeline] Phase 1/3: Shot sudah teralokasi ({total_shots} shot).")
        alloc_seconds = round(time.monotonic() - alloc_t0, 3)

        if is_stop_requested(self.project_dir):
            print(f"\n[Visual Pipeline] Stop requested after shot allocation; exiting.")
            return manifest, stats

        # Phase 2: Mencari footage (Fetch Assets)
        print(f"\n[Visual Pipeline] Phase 2/3: Mencari footage untuk proyek '{manifest.project_id}'...")
        effective_order = providers_order
        if effective_order is None and not provider_name:
            effective_order = self.resolve_provider_waterfall(manifest)
        manifest = self.fetch_all(
            manifest=manifest,
            provider_name=provider_name,
            providers_order=effective_order,
            allow_fallback=allow_fallback,
            check_ai_relevance=check_ai_relevance,
            _pct_start=0.0,
            _pct_end=45.0,
        )
        fetch_metrics = dict(getattr(self, "_last_fetch_metrics", {}) or {})
        self._save_manifest_timed(manifest)

        if is_stop_requested(self.project_dir):
            print(f"\n[Visual Pipeline] Stop requested after footage search; skipping clip building.")
            return manifest, stats

        # Phase 3: Menyusun klip (Build Clips)
        print(f"\n[Visual Pipeline] Phase 3/3: Menyusun klip visual untuk proyek '{manifest.project_id}'...")
        with self._telemetry_lock:
            self._active_manifest_save_seconds = 0.0
            self._active_manifest_save_count = 0
        build_t0 = time.monotonic()
        build_pct_start = 45.0 if getattr(self, "_last_fetch_target_count", 0) > 0 else 0.0
        manifest, build_stats = self.build_all(
            manifest,
            _pct_start=build_pct_start,
            _pct_end=99.0,
        )
        self._save_manifest_timed(manifest)
        build_wall_seconds = round(time.monotonic() - build_t0, 3)
        stats.update(build_stats)

        with self._telemetry_lock:
            manifest_save_build_sec = round(self._active_manifest_save_seconds, 3)
            manifest_save_build_cnt = int(self._active_manifest_save_count)

        total_pipeline_seconds = round(time.monotonic() - pipe_t0, 3)
        total_manifest_save_seconds = round(
            float(fetch_metrics.get("manifest_save_seconds_fetch", 0.0)) + manifest_save_build_sec,
            3,
        )
        total_manifest_save_count = int(fetch_metrics.get("manifest_save_count_fetch", 0)) + manifest_save_build_cnt

        pipeline_metrics: Dict[str, Any] = {
            "project_id": manifest.project_id,
            "total_seconds": total_pipeline_seconds,
            "allocate_seconds": alloc_seconds,
            "fetch_seconds": float(fetch_metrics.get("elapsed_seconds", 0.0)),
            "anchor_seconds": float(fetch_metrics.get("anchor_seconds", 0.0)),
            "anchor_llm_calls": int(fetch_metrics.get("anchor_llm_calls", 0)),
            "ai_keywords_seconds": float(fetch_metrics.get("ai_keywords_seconds", 0.0)),
            "ai_keywords_llm_calls": int(fetch_metrics.get("ai_keywords_llm_calls", 0)),
            "ai_keywords_retries": int(fetch_metrics.get("ai_keywords_retries", 0)),
            "ai_keywords_cached": int(fetch_metrics.get("ai_keywords_cached", 0)),
            "translate_seconds": float(fetch_metrics.get("translate_seconds", 0.0)),
            "search_wall_seconds": float(fetch_metrics.get("search_wall_seconds", 0.0)),
            "search_per_source": fetch_metrics.get("search_per_source", {}),
            "source_status": fetch_metrics.get("source_status", {}),
            "thumbnail_seconds": float(fetch_metrics.get("thumbnail_seconds", 0.0)),
            "asset_download_seconds": float(fetch_metrics.get("asset_download_seconds", 0.0)),
            "download_cache_hits": int(fetch_metrics.get("download_cache_hits", 0)),
            "download_misses": int(fetch_metrics.get("download_misses", 0)),
            "ai_relevance_seconds": float(fetch_metrics.get("ai_relevance_seconds", 0.0)),
            "build_seconds": build_wall_seconds,
            "ffmpeg_build_seconds": float(build_stats.get("total_build_seconds", 0.0)),
            "built_count": int(build_stats.get("built_count", 0)),
            "skipped_count": int(build_stats.get("skipped_count", 0)),
            "manifest_save_seconds": total_manifest_save_seconds,
            "manifest_save_count": total_manifest_save_count,
            "source_distribution": fetch_metrics.get("source_distribution", {}),
            "total_shots": int(fetch_metrics.get("total_shots", 0)),
        }
        stats["pipeline_metrics"] = pipeline_metrics
        self._last_pipeline_metrics = pipeline_metrics

        try:
            metrics_file = self.project_dir / "visual_metrics.json"
            metrics_tmp = metrics_file.with_suffix(".json.tmp")
            metrics_tmp.write_text(json.dumps(pipeline_metrics, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(metrics_tmp, metrics_file)
        except Exception as m_exc:
            logger.debug(f"Could not save visual_metrics.json: {m_exc}")

        logger.info(
            f"[Visual Pipeline Summary] project='{manifest.project_id}' total={total_pipeline_seconds:.2f}s | "
            f"alloc={alloc_seconds:.2f}s | fetch={pipeline_metrics['fetch_seconds']:.2f}s "
            f"(ai_kw={pipeline_metrics['ai_keywords_seconds']:.2f}s/calls={pipeline_metrics['ai_keywords_llm_calls']}/ret={pipeline_metrics['ai_keywords_retries']}, "
            f"search_wall={pipeline_metrics['search_wall_seconds']:.2f}s, "
            f"download={pipeline_metrics['asset_download_seconds']:.2f}s, thumbs=0.000s) | "
            f"build={build_wall_seconds:.2f}s (clips={pipeline_metrics['built_count']}, ffmpeg={pipeline_metrics['ffmpeg_build_seconds']:.2f}s) | "
            f"manifest_save={total_manifest_save_seconds:.2f}s ({total_manifest_save_count}x)"
        )

        return manifest, stats

    # Alias pipeline to run_pipeline for backwards compatibility
    pipeline = run_pipeline

