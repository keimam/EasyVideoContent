"""Shot Unit Formation, Splitting, Merging, and Hash Calculation (4.2).

Rules:
- Default: 1 segment = 1 shot.
- Merge: If segment duration < min_shot_seconds (2.0s), merge with adjacent segment.
- Split: If segment duration > max_shot_seconds (8.0s), split into multiple shots.
- Frame carry-over: Total chapter visual frames strictly equal round(chapter_audio_duration * fps) ± 1.
- Shot hash: SHA-256 over asset hash, motion, duration, target_frames, resolution, fps, encoder parameters.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional

from app.services.longform.schema import Chapter, ItemStatus, MotionType, ProjectManifest, Segment, Shot

DEFAULT_MIN_SHOT_SECONDS = 2.0
DEFAULT_MAX_SHOT_SECONDS = 8.0
MOTION_CYCLE = [
    MotionType.zoom_in,
    MotionType.zoom_out,
    MotionType.pan_left,
    MotionType.pan_right,
    MotionType.static,
]


def compute_shot_hash(
    shot_id: str,
    duration_seconds: float,
    target_frames: int,
    motion: str,
    asset_content_hash: Optional[str],
    width: int = 1920,
    height: int = 1080,
    fps: int | float = 25,
    codec: str = "libx264",
    profile: str = "high",
    pix_fmt: str = "yuv420p",
) -> str:
    """Compute deterministic SHA-256 hash for a visual shot work unit."""
    canonical_dict = {
        "shot_id": shot_id,
        "duration_seconds": round(duration_seconds, 3),
        "target_frames": target_frames,
        "motion": str(motion),
        "asset_content_hash": asset_content_hash or "none",
        "width": width,
        "height": height,
        "fps": int(round(fps)),
        "codec": codec,
        "profile": profile,
        "pix_fmt": pix_fmt,
    }
    dumped = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def form_chapter_shots(
    chapter: Chapter,
    min_shot_seconds: float = DEFAULT_MIN_SHOT_SECONDS,
    max_shot_seconds: float = DEFAULT_MAX_SHOT_SECONDS,
    fps: int = 25,
    default_source: str = "ai_image",
) -> List[Shot]:
    """Form, merge, and split segments in a chapter into Shot units with frame carry-over."""
    # Collect all segments across all groups in chronological order
    flat_segments: List[Segment] = []
    for g in chapter.tts_groups:
        for seg in g.segments:
            flat_segments.append(seg)

    if not flat_segments:
        return []

    # Calculate total chapter audio duration
    ch_audio_duration = chapter.audio_duration_seconds
    if ch_audio_duration is None or ch_audio_duration <= 0.0:
        ch_audio_duration = sum(s.duration_seconds or 3.0 for s in flat_segments)

    # Step 1: Initial grouping with merge for short segments
    raw_shot_groups: List[Dict[str, Any]] = []
    i = 0
    while i < len(flat_segments):
        seg = flat_segments[i]
        dur = seg.duration_seconds or 3.0
        start = seg.chapter_start_seconds or 0.0
        end = seg.chapter_end_seconds or (start + dur)

        curr_group = {
            "segment_indices": [seg.segment_index],
            "start_seconds": start,
            "end_seconds": end,
            "duration_seconds": dur,
            "visual_prompt": seg.visual.prompt if (seg.visual and seg.visual.prompt) else "",
            "search_query": seg.visual.search_keywords if (seg.visual and seg.visual.search_keywords) else seg.narration_text,
            "motion": seg.visual.motion if seg.visual else MotionType.zoom_in,
            "requested_source": (seg.visual.requested_source if seg.visual and seg.visual.requested_source else default_source),
        }

        # Merge forward if shorter than min_shot_seconds and there is a next segment
        while curr_group["duration_seconds"] < min_shot_seconds and i + 1 < len(flat_segments):
            next_seg = flat_segments[i + 1]
            next_dur = next_seg.duration_seconds or 3.0
            next_end = next_seg.chapter_end_seconds or (curr_group["end_seconds"] + next_dur)

            curr_group["segment_indices"].append(next_seg.segment_index)
            curr_group["end_seconds"] = next_end
            curr_group["duration_seconds"] = curr_group["end_seconds"] - curr_group["start_seconds"]
            i += 1

        # If still < min_shot_seconds at the end of chapter and we have a previous group, merge backward
        if curr_group["duration_seconds"] < min_shot_seconds and raw_shot_groups:
            prev = raw_shot_groups[-1]
            prev["segment_indices"].extend(curr_group["segment_indices"])
            prev["end_seconds"] = curr_group["end_seconds"]
            prev["duration_seconds"] = prev["end_seconds"] - prev["start_seconds"]
        else:
            raw_shot_groups.append(curr_group)
        i += 1

    # Step 2: Split groups longer than max_shot_seconds
    split_groups: List[Dict[str, Any]] = []
    for g in raw_shot_groups:
        dur = g["duration_seconds"]
        if dur <= max_shot_seconds:
            split_groups.append(g)
        else:
            # Determine number of sub-shots
            num_splits = math.ceil(dur / max_shot_seconds)
            sub_dur = dur / num_splits
            t = g["start_seconds"]
            for s_idx in range(num_splits):
                s_end = t + sub_dur if s_idx < num_splits - 1 else g["end_seconds"]
                split_groups.append({
                    "segment_indices": list(g["segment_indices"]),
                    "start_seconds": t,
                    "end_seconds": s_end,
                    "duration_seconds": s_end - t,
                    "visual_prompt": g["visual_prompt"],
                    "search_query": g["search_query"],
                    "motion": g["motion"],
                    "requested_source": g["requested_source"],
                })
                t = s_end

    # Step 3: Assign shots, motion rotation, and carry-over frame counts
    expected_total_frames = int(round(ch_audio_duration * fps))
    accumulated_frames = 0
    shots: List[Shot] = []

    for idx, grp in enumerate(split_groups):
        shot_id = f"ch{chapter.chapter_index:02d}_shot{idx + 1:03d}"
        motion = grp["motion"]
        # Rotate motion if default or not explicitly set
        if motion == MotionType.zoom_in and idx > 0:
            motion = MOTION_CYCLE[idx % len(MOTION_CYCLE)]

        # Cumulative carry-over frame calculation
        if idx == len(split_groups) - 1:
            shot_frames = max(expected_total_frames - accumulated_frames, 1)
        else:
            target_cumulative = int(round(grp["end_seconds"] * fps))
            shot_frames = max(target_cumulative - accumulated_frames, 1)

        accumulated_frames += shot_frames

        shot = Shot(
            shot_id=shot_id,
            chapter_index=chapter.chapter_index,
            shot_index=idx + 1,
            segment_indices=grp["segment_indices"],
            start_seconds=round(grp["start_seconds"], 3),
            end_seconds=round(grp["end_seconds"], 3),
            duration_seconds=round(grp["duration_seconds"], 3),
            target_frames=shot_frames,
            requested_source=grp["requested_source"],
            actual_source=grp["requested_source"],
            visual_prompt=grp["visual_prompt"],
            search_query=grp["search_query"],
            motion=motion,
            asset_path=f"assets/visual/shots/{shot_id}_asset.mp4" if "stock" in grp["requested_source"] else f"assets/visual/shots/{shot_id}_asset.jpg",
            rendered_clip=f"assets/visual/shots/{shot_id}.mp4",
            status=ItemStatus.pending,
        )
        shots.append(shot)

    # Step 4: Map back to segments
    shot_map = {s.shot_id: s for s in shots}
    for seg in flat_segments:
        # Find which shot contains this segment index
        matching_shots = [s for s in shots if seg.segment_index in s.segment_indices]
        if matching_shots and seg.visual:
            seg.visual.shot_id = matching_shots[0].shot_id

    # Step 5: Update chapter metadata
    chapter.shots = shots
    chapter.visual_duration_seconds = round(sum(s.duration_seconds for s in shots), 3)
    chapter.visual_frames = sum(s.target_frames for s in shots)

    return shots
