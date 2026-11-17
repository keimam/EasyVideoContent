"""AI Video per-shot generation adapter for Long Video (Stage 8B.2, F3).

Connects Long Video shots to MoneyPrinterTurbo's configured AI Video backends
(prioritizing WaveSpeed while supporting all MPT AI Video providers), handles
duration rounding to provider-supported values, reads editable pricing tables
from config/longform.toml, builds ONLY the target shot clip, locks the shot,
and updates its thumbnail.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from loguru import logger

from app.config import config
from app.models.schema import VideoAspect
from app.services import material as mpt_material
from app.services.longform.config import load_ai_video_pricing
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, Shot
from app.services.longform.shot import compute_shot_hash
from app.services.longform.store import is_stop_requested, save_manifest
from app.services.longform.visual.base import compute_file_content_hash, invalidate_shot_thumbnail_cache
from app.services.longform.visual.builder import ShotClipBuilder, is_valid_media_file
from webui.settings_providers import configured_material_providers

AI_VIDEO_PROVIDER_ORDER = (
    "wavespeed",
    "ofox",
    "volcengine_seedance",
    "metaso_minimax",
    "infistar",
    "jalapeno",
    "muapi",
    "loomloom",
)

AI_VIDEO_PROVIDER_META: Dict[str, Dict[str, Any]] = {
    "wavespeed": {
        "label": "WaveSpeed",
        "config_model_key": "wavespeed_text_to_video_model",
        "default_model": "wavespeed-ai/wan-2.2/t2v-480p-ultra-fast",
        "models": [
            "wavespeed-ai/wan-2.2/t2v-480p-ultra-fast",
            "wavespeed-ai/wan-2.2/t2v-5b-720p",
            "bytedance/seedance-2.0-fast/text-to-video",
            "bytedance/seedance-1-pro-fast/text-to-video",
            "alibaba/wan-2.5/text-to-video",
            "kwaivgi/kling-v2.1-t2v-std",
        ],
        "resolutions": ["480p", "720p", "1080p"],
        "durations": [4, 5, 6, 8, 10, 12, 15],
    },
    "ofox": {
        "label": "OFox AI Video",
        "config_model_key": "ofox_text_to_video_model",
        "default_model": "bytedance/seedance-2.0-fast",
        "models": [
            "bytedance/seedance-2.0-fast",
            "bytedance/seedance-1-0-pro",
        ],
        "resolutions": ["480p", "720p", "1080p"],
        "durations": [4, 5, 6, 8, 10, 12, 15],
    },
    "volcengine_seedance": {
        "label": "Volcano Engine Seedance",
        "config_model_key": "volcengine_seedance_model_id",
        "default_model": "doubao-seedance-1-0-pro-250528",
        "models": ["doubao-seedance-1-0-pro-250528"],
        "resolutions": ["480p", "720p", "1080p"],
        "durations": [4, 5, 6, 8, 10, 12],
    },
    "metaso_minimax": {
        "label": "Metaso MiniMax H3",
        "config_model_key": "metaso_minimax_model_id",
        "default_model": "MiniMax-H3",
        "models": ["MiniMax-H3"],
        "resolutions": ["720p", "1080p", "2K"],
        "durations": [4, 5, 6, 8, 10, 15],
    },
    "infistar": {
        "label": "Infistar AI Video",
        "config_model_key": "infistar_video_model_id",
        "default_model": "doubao-seedance-2-0-fast-260128",
        "models": ["doubao-seedance-2-0-fast-260128"],
        "resolutions": ["480p", "720p", "1080p"],
        "durations": [4, 5, 8, 10],
    },
    "jalapeno": {
        "label": "Jalapeno Cloud AI Video",
        "config_model_key": "jalapeno_video_model_id",
        "default_model": "Doubao-Seedance-2-5-260628",
        "models": ["Doubao-Seedance-2-5-260628"],
        "resolutions": ["480p", "720p", "1080p"],
        "durations": [4, 5, 8, 10],
    },
    "muapi": {
        "label": "MuAPI AI Video",
        "config_model_key": "muapi_endpoint",
        "default_model": "seedance-lite-t2v",
        "models": ["seedance-lite-t2v"],
        "resolutions": ["480p", "720p", "1080p"],
        "durations": [3, 5, 8, 10, 12],
    },
    "loomloom": {
        "label": "Shengsuan Cloud AI Video",
        "config_model_key": "loomloom_video_model_id",
        "default_model": "veo31fast",
        "models": ["veo31fast"],
        "resolutions": ["720p", "1080p"],
        "durations": [4, 5, 8],
    },
}


def list_configured_ai_video_providers() -> List[Dict[str, Any]]:
    """Return configured MPT AI Video providers, prioritizing WaveSpeed first."""
    groups = {"ai_video": AI_VIDEO_PROVIDER_ORDER}
    mat_map = configured_material_providers(config.app, groups, codex_connected=False)
    active_ids = list(mat_map.get("ai_video") or ())

    # Ensure WaveSpeed appears first whenever configured
    ordered = []
    if "wavespeed" in active_ids:
        ordered.append("wavespeed")
    for pid in active_ids:
        if pid != "wavespeed":
            ordered.append(pid)

    results: List[Dict[str, Any]] = []
    for pid in ordered:
        meta = get_ai_video_provider_meta(pid)
        results.append({
            "provider_id": pid,
            "label": meta["label"],
            "active_model": meta["active_model"],
            "models": meta["models"],
            "resolutions": meta["resolutions"],
            "durations": meta["durations"],
        })
    return results


def list_supported_ai_video_providers() -> List[Dict[str, Any]]:
    """Return all MoneyPrinterTurbo-supported AI Video providers with metadata (F9)."""
    items: List[Dict[str, Any]] = []
    for pid in AI_VIDEO_PROVIDER_ORDER:
        meta = get_ai_video_provider_meta(pid)
        items.append({
            "provider_id": pid,
            "label": meta["label"],
            "default_model": meta["active_model"],
            "models": meta["models"],
            "resolutions": meta["resolutions"],
            "durations": meta["durations"],
        })
    return items


def get_ai_video_provider_meta(provider_id: str) -> Dict[str, Any]:
    """Return metadata, models, resolutions, and supported durations for a provider."""
    pid = (provider_id or "wavespeed").strip().lower()
    base = AI_VIDEO_PROVIDER_META.get(pid, AI_VIDEO_PROVIDER_META["wavespeed"])
    cfg_model = str(config.app.get(base["config_model_key"], "") or "").strip()
    active_model = cfg_model or base["default_model"]
    models = list(base["models"])
    if active_model and active_model not in models:
        models.insert(0, active_model)
    return {
        "provider_id": pid,
        "label": base["label"],
        "active_model": active_model,
        "models": models,
        "resolutions": list(base["resolutions"]),
        "durations": list(base["durations"]),
    }


def get_model_supported_durations(provider_id: str, model_id: Optional[str] = None) -> List[int]:
    """Return sorted provider/model supported clip durations in integer seconds."""
    meta = get_ai_video_provider_meta(provider_id)
    m_low = (model_id or meta["active_model"] or "").lower()
    if "kling" in m_low or "wan-" in m_low:
        return [5, 10]
    return sorted(meta["durations"])


def round_up_supported_duration(shot_duration_seconds: float, supported_durations: List[int]) -> int:
    """Round shot duration UP to the smallest supported duration >= shot duration (clamped to max)."""
    opts = sorted({int(d) for d in supported_durations if int(d) > 0})
    if not opts:
        return max(4, int(round(shot_duration_seconds or 4)))
    dur = max(float(shot_duration_seconds or 0.0), 0.1)
    for d in opts:
        if d >= dur:
            return d
    return opts[-1]


def build_ai_video_prompt(
    narration: str,
    keywords: str = "",
    preset_snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    """Construct an editable English AI video generation prompt from narration, keywords, and preset style."""
    snap = preset_snapshot or {}
    style = str(snap.get("visual_style") or "cinematic documentary footage").strip()
    kw_part = str(keywords or "").strip()
    narr_part = str(narration or "").strip()

    # Prefer English keywords/subject so prompt is always English-friendly
    core = kw_part or narr_part or "atmospheric documentary scene"
    if len(core) > 180:
        core = core[:177].rsplit(" ", 1)[0] + "..."
    return f"{core}, {style}, realistic lighting, smooth natural camera motion, high detail"


def estimate_ai_video_cost(
    model_id: str,
    duration_seconds: float,
    resolution: str = "720p",
    *,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Estimate cost in USD using the user-editable pricing table in config/longform.toml."""
    pricing = load_ai_video_pricing(config_path)
    models = pricing.get("models") or {}
    raw_key = (model_id or "").strip()

    entry = models.get(raw_key)
    if entry is None:
        # Case-insensitive fallback lookup
        low = raw_key.lower()
        for k, v in models.items():
            if k.lower() == low:
                entry = v
                break

    updated_at = str(pricing.get("updated_at") or "")
    source = str(pricing.get("source") or "")

    if entry is None:
        return {
            "known": False,
            "estimated_usd": None,
            "updated_at": updated_at,
            "source": source,
        }

    dur = max(float(duration_seconds or 0.0), 1.0)
    row_url = source
    row_checked = updated_at
    if isinstance(entry, (int, float)):
        est = float(entry) * dur
    elif isinstance(entry, dict):
        if entry.get("url"):
            row_url = str(entry["url"])
        if entry.get("checked_at"):
            row_checked = str(entry["checked_at"])
        if "per_clip" in entry:
            est = float(entry["per_clip"])
        else:
            per_sec = float(entry.get("per_second", 0.0) or 0.0)
            mults = entry.get("resolution_multipliers") or {}
            mult = float(mults.get(resolution, 1.0) or 1.0)
            est = per_sec * dur * mult
    else:
        return {
            "known": False,
            "estimated_usd": None,
            "updated_at": updated_at,
            "source": source,
            "url": "",
        }

    return {
        "known": True,
        "estimated_usd": round(est, 4),
        "updated_at": row_checked,
        "source": row_url,
        "url": row_url,
    }


@contextmanager
def _scoped_config_override(overrides: Dict[str, Any]) -> Iterator[None]:
    """Temporarily apply provider model/resolution overrides to config.app during a single call."""
    prev: Dict[str, Tuple[bool, Any]] = {}
    for k, v in overrides.items():
        if v is None:
            continue
        prev[k] = (k in config.app, config.app.get(k))
        config.app[k] = v
    try:
        yield
    finally:
        for k, (existed, old_val) in prev.items():
            if existed:
                config.app[k] = old_val
            else:
                config.app.pop(k, None)


def _download_generated_video_to_path(url: str, dest: Path, provider_id: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = dest.parent / "_ai_vid_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    saved = mpt_material._save_generated_video_with_retry(url, str(tmp_dir), provider_id)
    if not saved or not Path(saved).is_file():
        raise RuntimeError(f"Failed to download generated AI video from {provider_id}")
    shutil.move(str(saved), str(dest))
    try:
        tmp_dir.rmdir()
    except OSError:
        pass
    return dest


def _default_provider_generate_to_file(
    *,
    provider_id: str,
    model_id: str,
    prompt: str,
    resolution: str,
    duration_seconds: int,
    aspect: VideoAspect,
    dest: Path,
) -> Path:
    pid = provider_id.strip().lower()
    dur = int(duration_seconds)

    if pid == "wavespeed":
        overrides = {
            "wavespeed_text_to_video_model": model_id,
            "wavespeed_min_duration": dur,
            "wavespeed_max_duration": max(dur, 15),
        }
        with _scoped_config_override(overrides):
            items = mpt_material.generate_videos_wavespeed(
                search_term=prompt,
                minimum_duration=dur,
                video_aspect=aspect,
            )
    elif pid == "volcengine_seedance":
        from app.services import volcengine_seedance
        with _scoped_config_override({
            "volcengine_seedance_model_id": model_id,
            "volcengine_seedance_resolution": resolution,
        }):
            items = volcengine_seedance.generate_videos(
                search_term=prompt, minimum_duration=dur, video_aspect=aspect
            )
    elif pid == "ofox":
        from app.services import ofox
        with _scoped_config_override({
            "ofox_text_to_video_model": model_id,
            "ofox_resolution": resolution,
        }):
            items = ofox.generate_videos(
                search_term=prompt, minimum_duration=dur, video_aspect=aspect
            )
    elif pid == "metaso_minimax":
        from app.services import metaso_minimax
        with _scoped_config_override({"metaso_minimax_resolution": resolution}):
            items = metaso_minimax.generate_videos(
                search_term=prompt, minimum_duration=dur, video_aspect=aspect
            )
    elif pid == "muapi":
        from app.services import muapi
        with _scoped_config_override({"muapi_endpoint": model_id, "muapi_resolution": resolution}):
            items = muapi.generate_videos(
                search_term=prompt, minimum_duration=dur, video_aspect=aspect
            )
    elif pid == "jalapeno":
        from app.services import jalapeno_video
        with _scoped_config_override({
            "jalapeno_video_model_id": model_id,
            "jalapeno_video_resolution": resolution,
        }):
            items = jalapeno_video.generate_videos(
                search_term=prompt, minimum_duration=dur, video_aspect=aspect
            )
    elif pid == "infistar":
        from app.services import infistar_video
        tmp_dir = dest.parent / "_ai_vid_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with _scoped_config_override({"infistar_video_model_id": model_id}):
            item = infistar_video.generate_video(
                search_term=prompt, video_aspect=aspect, output_dir=str(tmp_dir)
            )
        local_p = getattr(item, "file_path", None) or getattr(item, "local_path", None)
        if local_p and Path(local_p).is_file():
            shutil.move(str(local_p), str(dest))
            return dest
        items = [item] if getattr(item, "url", None) else []
    else:
        raise ValueError(f"Unsupported AI Video provider: {provider_id}")

    if not items:
        raise RuntimeError(f"AI Video provider '{provider_id}' returned no output.")
    first = items[0]
    if getattr(first, "url", None):
        return _download_generated_video_to_path(first.url, dest, pid)
    raise RuntimeError(f"AI Video provider '{provider_id}' produced no downloadable video URL.")


def generate_ai_video_for_shot(
    *,
    project_dir: Path | str,
    manifest: ProjectManifest,
    shot_id: str,
    provider_id: str,
    model_id: str,
    prompt: str,
    resolution: str = "720p",
    duration_seconds: Optional[int] = None,
    rebuild: bool = True,
    video_generator_fn: Optional[Callable[..., Path]] = None,
) -> Shot:
    """Generate an AI video for a single shot, lock it, rebuild ONLY its clip, and refresh its thumbnail."""
    p_dir = Path(project_dir)
    target_shot: Optional[Shot] = None
    for ch in manifest.chapters:
        for s in ch.shots:
            if s.shot_id == shot_id:
                target_shot = s
                break
        if target_shot:
            break

    if target_shot is None:
        raise ValueError(f"Shot '{shot_id}' not found in manifest.")

    supported = get_model_supported_durations(provider_id, model_id)
    req_dur = duration_seconds if duration_seconds else round_up_supported_duration(target_shot.duration_seconds, supported)
    rounded_dur = round_up_supported_duration(float(req_dur), supported)

    # Determine aspect ratio from project encoder resolution
    res_wh = getattr(getattr(manifest, "encoder_settings", None), "resolution", (1920, 1080)) or (1920, 1080)
    aspect = VideoAspect.landscape if res_wh[0] >= res_wh[1] else VideoAspect.portrait

    rel_dest = f"assets/visual/shots/{shot_id}_asset.mp4"
    dest_file = (p_dir / rel_dest).resolve()
    dest_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        f"Generating AI Video for shot {shot_id} via {provider_id}/{model_id} "
        f"({resolution}, {rounded_dur}s for shot {target_shot.duration_seconds:.2f}s)"
    )

    if video_generator_fn is not None:
        produced = Path(
            video_generator_fn(
                provider_id=provider_id,
                model_id=model_id,
                prompt=prompt,
                resolution=resolution,
                duration_seconds=rounded_dur,
                aspect=aspect,
                dest=dest_file,
            )
        )
        if produced.resolve() != dest_file:
            shutil.copyfile(produced, dest_file)
    else:
        _default_provider_generate_to_file(
            provider_id=provider_id,
            model_id=model_id,
            prompt=prompt,
            resolution=resolution,
            duration_seconds=rounded_dur,
            aspect=aspect,
            dest=dest_file,
        )

    ok_media, reason, _ = is_valid_media_file(dest_file)
    if not ok_media:
        raise RuntimeError(f"Generated AI video failed media check for {shot_id}: {reason}")

    meta = get_ai_video_provider_meta(provider_id)
    target_shot.asset_path = rel_dest
    target_shot.asset_content_hash = compute_file_content_hash(dest_file)
    target_shot.requested_source = "ai_video"
    target_shot.actual_source = f"AI Video · {meta['label']} · {model_id}"
    target_shot.ai_video_provider = provider_id
    target_shot.ai_video_model = model_id
    target_shot.is_fake = False
    target_shot.locked = True
    target_shot.needs_review = False
    target_shot.fallback_reason = None

    if rebuild:
        fps = getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25
        builder = ShotClipBuilder(fps=fps, width=res_wh[0], height=res_wh[1])
        clip_rel = f"assets/visual/shots/{shot_id}.mp4"
        clip_path = (p_dir / clip_rel).resolve()
        builder.build_shot_clip(
            asset_path=dest_file,
            output_path=clip_path,
            motion=target_shot.motion,
            target_frames=target_shot.target_frames,
        )
        target_shot.asset_content_hash = compute_file_content_hash(dest_file)
        target_shot.rendered_clip = clip_rel
        target_shot.status = ItemStatus.completed
        target_shot.shot_hash = compute_shot_hash(
            shot_id=target_shot.shot_id,
            duration_seconds=target_shot.duration_seconds,
            target_frames=target_shot.target_frames,
            motion=target_shot.motion.value,
            asset_content_hash=target_shot.asset_content_hash,
            width=res_wh[0],
            height=res_wh[1],
            fps=fps,
            requested_provider=target_shot.requested_source or "",
            actual_provider=target_shot.actual_source or "",
        )
    else:
        has_stale_clip = bool(target_shot.rendered_clip and (p_dir / target_shot.rendered_clip).is_file())
        target_shot.status = ItemStatus.stale if has_stale_clip else ItemStatus.pending
        target_shot.shot_hash = None

    # Refresh shot thumbnail cleanly (F14)
    invalidate_shot_thumbnail_cache(p_dir, shot_id)
    save_manifest(manifest, p_dir)
    return target_shot


def plan_needs_review_ai_video_batch(
    manifest: ProjectManifest,
    provider_id: str,
    model_id: str,
    resolution: str = "720p",
) -> Dict[str, Any]:
    """Summarize shots marked needs_review and estimate cumulative AI Video cost."""
    supported = get_model_supported_durations(provider_id, model_id)
    targets: List[Dict[str, Any]] = []
    total_usd = 0.0
    all_known = True

    for ch in manifest.chapters:
        for s in ch.shots:
            if not getattr(s, "needs_review", False) or getattr(s, "locked", False):
                continue
            rounded_d = round_up_supported_duration(s.duration_seconds, supported)
            cost_info = estimate_ai_video_cost(model_id, rounded_d, resolution)
            est = cost_info.get("estimated_usd")
            if est is None:
                all_known = False
            else:
                total_usd += float(est)
            targets.append({
                "shot_id": s.shot_id,
                "chapter_index": ch.chapter_index,
                "shot_index": s.shot_index,
                "rounded_duration": rounded_d,
                "estimated_usd": est,
                "keywords": s.search_query_en or s.search_query or s.visual_prompt or "",
            })

    return {
        "count": len(targets),
        "targets": targets,
        "all_costs_known": all_known,
        "total_estimated_usd": round(total_usd, 4) if all_known and targets else (round(total_usd, 4) if total_usd > 0 else None),
    }


def generate_ai_video_for_needs_review(
    *,
    project_dir: Path | str,
    manifest: ProjectManifest,
    provider_id: str,
    model_id: str,
    resolution: str = "720p",
    max_budget_usd: float = 5.0,
    shot_ids: Optional[List[str]] = None,
    video_generator_fn: Optional[Callable[..., Path]] = None,
) -> Dict[str, Any]:
    """Sequentially generate AI videos for needs_review shots while respecting max_budget_usd."""
    p_dir = Path(project_dir)
    supported = get_model_supported_durations(provider_id, model_id)
    preset_snap = manifest.preset_snapshot or {}

    shot_chapter_pairs: List[Tuple[Chapter, Shot]] = []
    for ch in manifest.chapters:
        for s in ch.shots:
            if shot_ids is not None:
                if s.shot_id in shot_ids:
                    shot_chapter_pairs.append((ch, s))
            elif getattr(s, "needs_review", False) and not getattr(s, "locked", False):
                shot_chapter_pairs.append((ch, s))

    generated = 0
    skipped_budget = 0
    spent_usd = 0.0
    errors: List[str] = []

    for ch, shot in shot_chapter_pairs:
        if is_stop_requested(p_dir):
            break
        rounded_d = round_up_supported_duration(shot.duration_seconds, supported)
        cost_info = estimate_ai_video_cost(model_id, rounded_d, resolution)
        shot_cost = float(cost_info["estimated_usd"]) if cost_info.get("estimated_usd") is not None else 0.0
        if max_budget_usd >= 0 and (spent_usd + shot_cost) > float(max_budget_usd):
            skipped_budget += 1
            logger.info(
                f"Budget cap reached (${spent_usd:.2f} + ${shot_cost:.2f} > ${max_budget_usd:.2f}); "
                f"stopping batch before shot {shot.shot_id}"
            )
            break

        # Build shot narration cleanly
        narr_parts: List[str] = []
        if shot.segment_indices and getattr(ch, "tts_groups", None):
            for g in ch.tts_groups:
                for seg in getattr(g, "segments", []):
                    if seg.segment_index in shot.segment_indices and seg.narration_text:
                        narr_parts.append(seg.narration_text.strip())
        narr = " ".join(narr_parts)
        kw = shot.search_query_en or shot.search_query or shot.visual_prompt or ""
        prompt = build_ai_video_prompt(narr, kw, preset_snap)

        try:
            generate_ai_video_for_shot(
                project_dir=p_dir,
                manifest=manifest,
                shot_id=shot.shot_id,
                provider_id=provider_id,
                model_id=model_id,
                prompt=prompt,
                resolution=resolution,
                duration_seconds=rounded_d,
                rebuild=True,
                video_generator_fn=video_generator_fn,
            )
            generated += 1
            spent_usd += shot_cost
        except Exception as exc:
            errors.append(f"{shot.shot_id}: {exc}")

    return {
        "generated": generated,
        "skipped_budget": skipped_budget,
        "spent_usd": round(spent_usd, 4),
        "errors": errors,
    }
