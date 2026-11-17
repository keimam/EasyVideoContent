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

import os
import shutil
import subprocess
import time
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
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualConfigurationError,
    VisualError,
    compute_file_content_hash,
    invalidate_shot_thumbnail_cache,
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
            return FakeVisualProvider(mode="ai_image")
        elif p_name in ("pexels", "pixabay", "coverr", "stock"):
            # V5: lazy import of StockVisualProvider (pulls in material.py)
            from app.services.longform.visual.stock import StockVisualProvider  # noqa: F811
            return StockVisualProvider(
                provider_name=p_name,
                cache=self.cache,
                credits_manager=self.credits_manager,
            )
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
            return LocalVisualProvider(self.project_dir, credits_manager=self.credits_manager)
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
            return preferred.lower().strip()

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

    def fetch_all(
        self,
        manifest: ProjectManifest,
        provider_name: Optional[str] = None,
        allow_fallback: bool = False,
        check_ai_relevance: bool = False,
        translate_keywords: bool = True,
        _pct_start: float = 0.0,
        _pct_end: float = 99.0,
    ) -> ProjectManifest:
        """Search and download/generate visual assets for all pending shots (4.1, V1).

        V1 fix:
        - provider_name=None resolves to real default (pixabay→pexels→ai_image), never fake.
        - Fake assets are stale whenever a real provider is requested.
        - Fail-fast with clear error message if provider lacks credentials.
        - allow_fallback must be explicit; fallback is recorded per shot.
        """
        # Resolve the effective provider name (V1: never implicit fake)
        effective_provider_name = provider_name
        if not effective_provider_name:
            effective_provider_name = self._resolve_default_provider(manifest)

        # Ensure shots exist
        has_shots = any(len(ch.shots) > 0 for ch in manifest.chapters)
        if not has_shots:
            effective_fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
            self.prepare_shots(manifest, fps=effective_fps)

        # Early credentials validation for the effective provider (V1 fail-fast)
        # Only validate once upfront, not per-shot, for real providers
        global_fallback_reason: Optional[str] = None
        if effective_provider_name not in ("fake", "local"):
            try:
                probe_provider = self.get_provider(effective_provider_name, manifest)
                probe_provider.validate_credentials()
            except VisualConfigurationError as conf_err:
                if not allow_fallback:
                    # Build a clear informative error (V1)
                    provider_guidance = (
                        "To configure stock video credentials:\n"
                        "  1. Obtain a free API key from Pixabay: https://pixabay.com/api/docs/\n"
                        "  2. Or from Pexels: https://www.pexels.com/api/ (note: new key issuance currently paused)\n"
                        "  3. Set 'pixabay_api_keys' or 'pexels_api_keys' in config.toml\n"
                        "     or via WebUI → Basic Settings.\n"
                        "  Alternatively: use '--provider local' (visual library add --folder <path>)\n"
                        "  or '--provider fake' for offline keyless testing."
                    )
                    raise VisualConfigurationError(
                        f"Provider '{effective_provider_name}' is not properly configured.\n"
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

        # F12: Generate/load cached project anchor & avoid terms once per project for non-fake fetch
        if effective_provider_name != "fake" and not getattr(manifest, "visual_anchor_terms", None):
            try:
                ensure_project_visual_anchors(manifest)
            except Exception as exc:
                logger.warning(f"Could not populate project visual anchors: {exc}")

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
                    if kw_res.get("warning"):
                        translation_warnings.append(str(kw_res["warning"]))
                    if kw_res.get("fallback_used") and is_non_english_language(proj_lang):
                        tr_res = translate_chapter_queries_to_english(
                            shots_needing_kw_gen,
                            preset_snapshot=preset_snap,
                            project_lang=proj_lang,
                        )
                        if tr_res.get("warning"):
                            translation_warnings.append(str(tr_res["warning"]))
                elif shots_needing_kw_gen and is_non_english_language(proj_lang):
                    tr_res = translate_chapter_queries_to_english(
                        shots_needing_kw_gen,
                        preset_snapshot=preset_snap,
                        project_lang=proj_lang,
                    )
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

                # Determine target provider for this shot
                target_provider_name = effective_provider_name
                active_provider: BaseVisualProvider
                fallback_reason: Optional[str] = global_fallback_reason

                try:
                    active_provider = self.get_provider(target_provider_name, manifest)
                    if target_provider_name in ("fake", "local"):
                        active_provider.validate_credentials()
                except VisualConfigurationError as conf_err:
                    if not allow_fallback:
                        raise
                    fallback_reason = f"Provider '{target_provider_name}' credentials missing ({conf_err}); fell back to fake provider"
                    logger.warning(f"Shot {shot.shot_id}: {fallback_reason}")
                    active_provider = FakeVisualProvider(mode=target_provider_name)
                except Exception as exc:
                    if not allow_fallback:
                        raise
                    fallback_reason = f"Provider '{target_provider_name}' error ({exc}); fell back to fake provider"
                    logger.warning(f"Shot {shot.shot_id}: {fallback_reason}")
                    active_provider = FakeVisualProvider(mode=target_provider_name)

                # F13: Clear stale candidate list/tags on unlocked shots being re-fetched
                shot.candidates = []
                shot.tags = []

                # S5 / F12 / F13 Multi-tier escalation search
                from app.services.longform.visual.stock import (
                    build_short_core_queries,
                    extract_narrower_query,
                    restructure_stock_query,
                )

                raw_query = (
                    getattr(shot, "search_query_en", None)
                    or shot.search_query
                    or shot.visual_prompt
                    or "cinematic scene"
                )
                # Build ordered base queries (`search_query_en` + `search_queries_en` alternatives)
                ordered_base_queries: List[str] = []
                for q_cand in ([raw_query] + list(getattr(shot, "search_queries_en", []) or [])):
                    qc = str(q_cand or "").strip()
                    if qc and qc.lower() not in [x.lower() for x in ordered_base_queries]:
                        ordered_base_queries.append(qc)

                candidates: List[VisualCandidate] = []
                best_candidates: List[VisualCandidate] = []
                cand_origin_query: Dict[str, str] = {}
                seen_best_ids: Set[str] = set()
                chosen_query = raw_query

                query_tiers: List[Tuple[str, Optional[str]]] = []
                # Tier 1 & 2: Primary query + alternative concrete queries
                for bq in ordered_base_queries:
                    if req_terms:
                        restruct_q = restructure_stock_query(bq, req_terms, topic_kws)
                        query_tiers.append((restruct_q, stock_cat))
                        query_tiers.append((restruct_q, None))
                    query_tiers.append((bq, stock_cat))
                    query_tiers.append((bq, None))

                # Tier 3: Shortened 2-3 core word queries (helps Pixabay when 5-word queries yield < 3 hits)
                for bq in ordered_base_queries:
                    for short_q in build_short_core_queries(bq, proj_anchors):
                        query_tiers.append((short_q, None))
                narrow_q = extract_narrower_query(raw_query, req_terms)
                if narrow_q:
                    query_tiers.append((narrow_q, None))

                # Tier 4: Project anchor_terms & preset visual_keywords fallbacks
                if proj_anchors:
                    if len(proj_anchors) >= 2 and " " not in proj_anchors[0] and " " not in proj_anchors[1]:
                        query_tiers.append((f"{proj_anchors[0]} {proj_anchors[1]}", None))
                    for anc_term in proj_anchors[:5]:
                        if anc_term:
                            query_tiers.append((anc_term, None))
                for vk in visual_kws[:3]:
                    if vk:
                        query_tiers.append((vk, stock_cat))

                # Deduplicate preserving strict tier priority order
                seen_tiers: set[Tuple[str, Optional[str]]] = set()
                dedup_tiers: List[Tuple[str, Optional[str]]] = []
                for qt_pair in query_tiers:
                    k_pair = (qt_pair[0].lower(), qt_pair[1])
                    if k_pair not in seen_tiers:
                        seen_tiers.add(k_pair)
                        dedup_tiers.append(qt_pair)

                for q_text, q_cat in dedup_tiers:
                    search_kwargs = {
                        "query": q_text,
                        "target_duration": shot.duration_seconds,
                        "limit": 3,
                        "excluded_ids": used_candidate_ids,
                    }
                    try:
                        tier_cands = active_provider.search_candidates(
                            required_subject_terms=req_terms,
                            blocked_terms=block_terms,
                            negative_terms=neg_terms,
                            anchor_terms=proj_anchors,
                            avoid_terms=proj_avoids,
                            min_relevance_score=min_rel_score,
                            visual_keywords=visual_kws,
                            category=q_cat,
                            **search_kwargs,
                        )
                    except TypeError:
                        try:
                            tier_cands = active_provider.search_candidates(
                                required_subject_terms=req_terms,
                                blocked_terms=block_terms,
                                visual_keywords=visual_kws,
                                **search_kwargs,
                            )
                        except TypeError:
                            tier_cands = active_provider.search_candidates(**search_kwargs)

                    added_cnt = 0
                    for tc in tier_cands or []:
                        if tc.candidate_id not in seen_best_ids:
                            seen_best_ids.add(tc.candidate_id)
                            best_candidates.append(tc)
                            cand_origin_query[tc.candidate_id] = q_text
                            added_cnt += 1

                    logger.info(
                        f"Shot {shot.shot_id}: stock query '{q_text}' (cat={q_cat}) -> "
                        f"{len(tier_cands or [])} valid hit(s), added={added_cnt}, accumulated={len(best_candidates)}"
                    )
                    if len(best_candidates) >= 3:
                        break

                if best_candidates:
                    best_candidates.sort(key=lambda c: c.relevance_score, reverse=True)
                    candidates = best_candidates[:3]
                    chosen_query = cand_origin_query.get(candidates[0].candidate_id, chosen_query)
                    logger.info(
                        f"Shot {shot.shot_id}: selected top candidate '{candidates[0].candidate_id}' "
                        f"(score={candidates[0].relevance_score:.3f}) via query '{chosen_query}' "
                        f"from {len(candidates)} candidate(s)"
                    )

                # Step 4: Fallback to next provider or mark needs_review
                if not candidates:
                    if allow_fallback and not active_provider.is_fake:
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
                        # S1, S5: Mark shot needs_review rather than silently using an irrelevant clip
                        logger.warning(f"Shot {shot.shot_id}: no acceptable candidates found. Marked needs_review.")
                        shot.needs_review = True
                        shot.status = ItemStatus.pending
                        shot.relevance_score = 0.0
                        shot.tags = []
                        shot.candidates = []
                        shot.fallback_reason = "No candidates matched required subject terms or query"
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

                # Store candidate options
                shot.candidates = [c.to_dict() for c in candidates]
                selected_candidate = candidates[0]
                used_candidate_ids.add(selected_candidate.candidate_id)

                # Fetch selected asset
                ext = ".mp4" if selected_candidate.source_type == "stock_video" else ".jpg"
                rel_dest = f"assets/visual/shots/{shot.shot_id}_asset{ext}"
                dest_file = self.project_dir / rel_dest

                logger.info(f"Fetching asset for {shot.shot_id} via {active_provider.name} (score: {selected_candidate.relevance_score:.2f}, {selected_candidate.rendition_name or 'std'})...")
                asset = active_provider.fetch_asset(selected_candidate, dest_file)
                invalidate_shot_thumbnail_cache(self.project_dir, shot.shot_id)

                # Update shot metadata
                shot.asset_path = rel_dest
                shot.asset_content_hash = asset.content_hash
                shot.requested_source = effective_provider_name
                shot.actual_source = active_provider.name
                shot.is_fake = active_provider.is_fake
                shot.fallback_reason = fallback_reason
                shot.tags = selected_candidate.tags
                shot.relevance_score = selected_candidate.relevance_score
                shot.chosen_rendition = selected_candidate.rendition_name
                shot.is_low_res = getattr(selected_candidate, "is_low_res", False)
                shot.effective_query = chosen_query
                shot.ai_relevance_status = None
                shot.ai_relevance_reason = None
                shot.ai_relevance_asset_hash = None
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
                save_manifest(manifest, self.project_dir)
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
            rel_stats = check_project_relevance(manifest, self.project_dir)
            logger.info(
                f"AI relevance check completed for '{manifest.project_id}': "
                f"checked={rel_stats.get('checked')}, cached={rel_stats.get('cached')}, "
                f"ya={rel_stats.get('ya')}, tidak={rel_stats.get('tidak')}, ragu={rel_stats.get('ragu')}"
            )
            save_manifest(manifest, self.project_dir)

        return manifest

    def build_all(
        self,
        manifest: ProjectManifest,
        _pct_start: float = 0.0,
        _pct_end: float = 99.0,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Build rendered video clips for all shots in manifest (4.3, V1).

        V1 fix: shot hash now includes requested_provider + actual_provider so that
        switching providers correctly invalidates the rendered clip.
        """
        fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
        builder = ShotClipBuilder(fps=fps)
        built_count = 0
        skipped_count = 0
        total_time = 0.0

        # F15: Pre-partition target build shots vs already-valid skipped shots
        expected_hashes_by_id: Dict[str, str] = {}
        target_build_ids: Set[str] = set()
        total_project_shots = 0
        for ch in manifest.chapters:
            for shot in ch.shots:
                total_project_shots += 1
                expected_hash = compute_shot_hash(
                    shot_id=shot.shot_id,
                    duration_seconds=shot.duration_seconds,
                    target_frames=shot.target_frames,
                    motion=shot.motion.value,
                    asset_content_hash=shot.asset_content_hash,
                    fps=fps,
                    requested_provider=shot.requested_source or "",
                    actual_provider=shot.actual_source or "",
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
                if not shot.asset_path:
                    raise VisualError(f"Cannot build shot {shot.shot_id}: asset_path is missing. Run visual fetch first.")

                asset_file = (self.project_dir / shot.asset_path).resolve()
                if not asset_file.is_file():
                    raise FileNotFoundError(f"Shot asset file missing on disk: {asset_file}")

                expected_hash = expected_hashes_by_id[shot.shot_id]
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
                save_manifest(manifest, self.project_dir)

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

        prov_name = provider_name or target_shot.requested_source or "pixabay"
        prov = self.get_provider(prov_name, manifest)

        preset_snap = manifest.preset_snapshot or {}
        req_terms = preset_snap.get("required_subject_terms") or []
        block_terms = preset_snap.get("blocked_terms") or []
        neg_terms = preset_snap.get("negative_terms") or []
        min_rel = preset_snap.get("min_relevance_score", 0.35)
        stock_cat = preset_snap.get("stock_category")
        vis_kws = preset_snap.get("visual_keywords") or []

        q = search_query or getattr(target_shot, "search_query_en", None) or target_shot.search_query or target_shot.visual_prompt or "cinematic scene"
        proj_anchors = list(getattr(manifest, "visual_anchor_terms", []) or [])
        proj_avoids = list(getattr(manifest, "visual_avoid_terms", []) or [])

        candidates = prov.search_candidates(
            query=q,
            target_duration=target_shot.duration_seconds,
            limit=limit,
            required_subject_terms=req_terms,
            blocked_terms=block_terms,
            negative_terms=neg_terms,
            anchor_terms=proj_anchors,
            avoid_terms=proj_avoids,
            min_relevance_score=min_rel,
            visual_keywords=vis_kws,
            category=stock_cat,
        )

        if not candidates and stock_cat:
            candidates = prov.search_candidates(
                query=q,
                target_duration=target_shot.duration_seconds,
                limit=limit,
                required_subject_terms=req_terms,
                blocked_terms=block_terms,
                negative_terms=neg_terms,
                anchor_terms=proj_anchors,
                avoid_terms=proj_avoids,
                min_relevance_score=min_rel,
                visual_keywords=vis_kws,
                category=None,
            )

        if not candidates:
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

            target_shot.asset_path = rel_dest
            target_shot.asset_content_hash = asset.content_hash
            target_shot.actual_source = candidate.provider
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
            prov_name = provider_name or target_shot.requested_source or "pixabay"
            prov = self.get_provider(prov_name, manifest)
            q = prompt or target_shot.search_query or target_shot.visual_prompt
            preset_snap = manifest.preset_snapshot or {}
            cands = prov.search_candidates(
                query=q,
                target_duration=target_shot.duration_seconds,
                limit=5,
                required_subject_terms=preset_snap.get("required_subject_terms"),
                blocked_terms=preset_snap.get("blocked_terms"),
                negative_terms=preset_snap.get("negative_terms"),
                min_relevance_score=preset_snap.get("min_relevance_score", 0.35),
            )
            if not cands:
                raise VisualError(f"No valid candidates found for query '{q}'")
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
        allow_fallback: bool = True,
        check_ai_relevance: bool = False,
    ) -> Tuple[ProjectManifest, Dict[str, Any]]:
        """Execute the full 3-phase visual pipeline sequentially (Allocation -> Fetch -> Build).

        Single canonical implementation used identically by CLI `visual pipeline`, WebUI button ③,
        and `pipeline_auto` (Stage 7B.2 Z3).
        """
        if manifest is None:
            manifest = load_manifest(self.project_dir)

        stats: Dict[str, Any] = {
            "allocated_shots": 0,
            "fetched_count": 0,
            "built_count": 0,
            "skipped_count": 0,
        }

        # Phase 1: Membagi shot (Shot Allocation) if 0 shots
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
            save_manifest(manifest, self.project_dir)
            stats["allocated_shots"] = sum(len(ch.shots) for ch in manifest.chapters)
            print(f"  -> {stats['allocated_shots']} shot berhasil dialokasikan.")
        else:
            print(f"\n[Visual Pipeline] Phase 1/3: Shot sudah teralokasi ({total_shots} shot).")

        if is_stop_requested(self.project_dir):
            print(f"\n[Visual Pipeline] Stop requested after shot allocation; exiting.")
            return manifest, stats

        # Phase 2: Mencari footage (Fetch Assets)
        print(f"\n[Visual Pipeline] Phase 2/3: Mencari footage untuk proyek '{manifest.project_id}'...")
        manifest = self.fetch_all(
            manifest=manifest,
            provider_name=provider_name,
            allow_fallback=allow_fallback,
            check_ai_relevance=check_ai_relevance,
            _pct_start=0.0,
            _pct_end=45.0,
        )
        save_manifest(manifest, self.project_dir)

        if is_stop_requested(self.project_dir):
            print(f"\n[Visual Pipeline] Stop requested after footage search; skipping clip building.")
            return manifest, stats

        # Phase 3: Menyusun klip (Build Clips)
        print(f"\n[Visual Pipeline] Phase 3/3: Menyusun klip visual untuk proyek '{manifest.project_id}'...")
        build_pct_start = 45.0 if getattr(self, "_last_fetch_target_count", 0) > 0 else 0.0
        manifest, build_stats = self.build_all(
            manifest,
            _pct_start=build_pct_start,
            _pct_end=99.0,
        )
        save_manifest(manifest, self.project_dir)
        stats.update(build_stats)

        return manifest, stats

    # Alias pipeline to run_pipeline for backwards compatibility
    pipeline = run_pipeline

