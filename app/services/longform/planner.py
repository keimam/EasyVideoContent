"""Execution Planner and Resume State Machine for Longform Video Generation.

Analyzes the ProjectManifest against actual files on disk (presence and size > 0)
and computed canonical hashes to produce an exact execution plan of WorkUnits
(tts_group_audio, visual_segment, chapter_render, final_concat).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from app.services.longform.hashing import (
    compute_audio_group_hash,
    compute_chapter_hash,
    compute_final_concat_hash,
    compute_visual_segment_hash,
)
from app.services.longform.schema import ItemStatus, ProjectManifest
from app.services.longform.shot import compute_shot_hash


@dataclass
class WorkUnit:
    """A discrete unit of work in the longform generation pipeline."""

    unit_id: str
    unit_type: str  # tts_group_audio | visual_segment | chapter_render | final_concat
    chapter_index: int
    group_index: Optional[int] = None
    segment_index: Optional[int] = None
    status: ItemStatus = ItemStatus.pending
    reason: str = ""
    target_file: Optional[str] = None
    expected_hash: Optional[str] = None
    current_hash: Optional[str] = None


@dataclass
class ExecutionPlan:
    """Summary of all pending, stale, and completed units of work for a project."""

    project_id: str
    all_units: List[WorkUnit] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.all_units)

    @property
    def total_units(self) -> int:
        return len(self.all_units)

    @property
    def pending_units(self) -> List[WorkUnit]:
        return [u for u in self.all_units if u.status == ItemStatus.pending]

    @property
    def pending_count(self) -> int:
        return len(self.pending_units)

    @property
    def stale_units(self) -> List[WorkUnit]:
        return [u for u in self.all_units if u.status == ItemStatus.stale]

    @property
    def stale_count(self) -> int:
        return len(self.stale_units)

    @property
    def completed_units(self) -> List[WorkUnit]:
        return [u for u in self.all_units if u.status == ItemStatus.completed]

    @property
    def completed_count(self) -> int:
        return len(self.completed_units)

    @property
    def is_fully_completed(self) -> bool:
        return self.total_count > 0 and len(self.completed_units) == self.total_count

    @property
    def has_work_remaining(self) -> bool:
        return len(self.pending_units) > 0 or len(self.stale_units) > 0

    @property
    def units(self) -> List[WorkUnit]:
        return self.all_units

    def summary(self) -> str:
        """Return human-readable ASCII summary of plan status."""
        lines = [
            f"Execution Plan for Project: {self.project_id}",
            f"Total Work Units: {self.total_count}",
            f"  - Completed: {len(self.completed_units)}",
            f"  - Stale:     {len(self.stale_units)}",
            f"  - Pending:   {len(self.pending_units)}",
        ]
        if self.is_fully_completed:
            lines.append("Status: All stages are UP TO DATE (Ready for distribution)")
        elif self.stale_units:
            lines.append(f"Status: Incremental update required ({len(self.stale_units)} stale, {len(self.pending_units)} pending)")
        else:
            lines.append(f"Status: Incomplete ({len(self.pending_units)} units remaining)")
        return "\n".join(lines)


def _check_file_validity(project_dir: Path, rel_path: Optional[str]) -> Tuple[bool, str]:
    """Check if file exists and has size > 0."""
    if not rel_path:
        return False, "Target file path not specified"
    abs_path = project_dir / rel_path
    if not abs_path.is_file():
        return False, f"File does not exist: {rel_path}"
    try:
        size = abs_path.stat().st_size
        if size == 0:
            return False, f"File is empty (0 bytes): {rel_path}"
    except OSError as exc:
        return False, f"Cannot access file: {exc}"
    return True, "File exists and is non-empty"


def compute_plan(manifest: ProjectManifest, project_dir: str | Path) -> ExecutionPlan:
    """Analyze ProjectManifest against disk state and return ExecutionPlan."""
    p_dir = Path(project_dir).resolve()
    units: List[WorkUnit] = []

    subtitle_style = manifest.preset_snapshot.get("subtitle_style", {})
    all_chapters_ready = True

    for ch in manifest.chapters:
        ch_idx = ch.chapter_index
        ch_elements_ready = True

        for group in ch.tts_groups:
            g_idx = group.group_index
            target_audio = group.audio.file_path or f"assets/audio/groups/ch{ch_idx}_g{g_idx}.wav"
            effective_para = group.get_effective_paragraph_text()
            effective_voice = ch.voice_override or group.audio.voice_name
            expected_audio_hash = compute_audio_group_hash(
                paragraph_text=effective_para,
                tts_provider=group.audio.tts_provider,
                tts_model=group.audio.tts_model,
                voice_name=effective_voice,
                voice_rate=group.audio.voice_rate,
                text_norm_version=group.audio.text_norm_version,
                audio_format=group.audio.audio_format,
            )

            file_ok, file_reason = _check_file_validity(p_dir, target_audio)
            if not file_ok:
                audio_status = ItemStatus.pending
                reason = file_reason
                ch_elements_ready = False
            elif group.status == ItemStatus.stale:
                audio_status = ItemStatus.stale
                reason = "Audio group marked stale"
                ch_elements_ready = False
            elif group.audio.audio_hash != expected_audio_hash:
                audio_status = ItemStatus.stale
                reason = f"Audio hash mismatch (manifest: {group.audio.audio_hash}, expected: {expected_audio_hash})"
                ch_elements_ready = False
            else:
                audio_status = ItemStatus.completed
                reason = "Audio file exists and hash is valid"

            units.append(
                WorkUnit(
                    unit_id=f"ch{ch_idx}_g{g_idx}_audio",
                    unit_type="tts_group_audio",
                    chapter_index=ch_idx,
                    group_index=g_idx,
                    status=audio_status,
                    reason=reason,
                    target_file=target_audio,
                    expected_hash=expected_audio_hash,
                    current_hash=group.audio.audio_hash,
                )
            )

            # Check visual units: If ch.shots are defined, use visual_shot; else fallback to legacy segment units
            if not ch.shots:
                for seg in group.segments:
                    s_idx = seg.segment_index
                    target_clip = seg.visual.rendered_clip or f"assets/visual/clips/ch{ch_idx}_s{s_idx}.mp4"
                    expected_visual_hash = compute_visual_segment_hash(
                        source_content_hash=seg.visual.source_content_hash or "",
                        motion=seg.visual.motion.value,
                        duration=seg.duration_seconds or 5.0,
                    )

                    clip_ok, clip_reason = _check_file_validity(p_dir, target_clip)
                    if not clip_ok:
                        visual_status = ItemStatus.pending
                        reason = clip_reason
                        ch_elements_ready = False
                    elif seg.visual.visual_hash != expected_visual_hash:
                        visual_status = ItemStatus.stale
                        reason = f"Visual hash mismatch (manifest: {seg.visual.visual_hash}, expected: {expected_visual_hash})"
                        ch_elements_ready = False
                    else:
                        visual_status = ItemStatus.completed
                        reason = "Visual clip exists and hash is valid"

                    units.append(
                        WorkUnit(
                            unit_id=f"ch{ch_idx}_s{s_idx}_visual",
                            unit_type="visual_segment",
                            chapter_index=ch_idx,
                            group_index=g_idx,
                            segment_index=s_idx,
                            status=visual_status,
                            reason=reason,
                            target_file=target_clip,
                            expected_hash=expected_visual_hash,
                            current_hash=seg.visual.visual_hash,
                        )
                    )

        # If chapter has shots formed, check each visual_shot work unit
        if ch.shots:
            fps = int(round(getattr(getattr(manifest, "encoder_settings", None), "fps", 25) or 25))
            for shot in ch.shots:
                target_clip = shot.rendered_clip or f"assets/visual/shots/{shot.shot_id}.mp4"
                expected_shot_hash = compute_shot_hash(
                    shot_id=shot.shot_id,
                    duration_seconds=shot.duration_seconds,
                    target_frames=shot.target_frames,
                    motion=shot.motion.value,
                    asset_content_hash=shot.asset_content_hash,
                    fps=fps,
                    requested_provider=shot.requested_source or "",
                    actual_provider=shot.actual_source or "",
                )

                clip_ok, clip_reason = _check_file_validity(p_dir, target_clip)
                if not clip_ok:
                    shot_status = ItemStatus.pending
                    reason = clip_reason
                    ch_elements_ready = False
                elif shot.status == ItemStatus.stale:
                    shot_status = ItemStatus.stale
                    reason = "Visual shot marked stale"
                    ch_elements_ready = False
                elif shot.shot_hash != expected_shot_hash:
                    shot_status = ItemStatus.stale
                    reason = f"Shot hash mismatch (manifest: {shot.shot_hash}, expected: {expected_shot_hash})"
                    ch_elements_ready = False
                else:
                    shot_status = ItemStatus.completed
                    reason = "Shot clip exists and hash is valid"

                units.append(
                    WorkUnit(
                        unit_id=shot.shot_id,
                        unit_type="visual_shot",
                        chapter_index=ch_idx,
                        status=shot_status,
                        reason=reason,
                        target_file=target_clip,
                        expected_hash=expected_shot_hash,
                        current_hash=shot.shot_hash,
                    )
                )

        # Chapter render work unit
        target_ch_file = ch.output_file or f"assets/chapters/chapter_{ch_idx}.mp4"
        expected_ch_hash = compute_chapter_hash(
            chapter=ch,
            encoder_settings=manifest.encoder_settings,
            subtitle_style=subtitle_style,
            preset_snapshot=manifest.preset_snapshot,
            pipeline_version=manifest.pipeline_version,
            sub_niche_focus=manifest.sub_niche_focus or "",
        )

        ch_ok, ch_file_reason = _check_file_validity(p_dir, target_ch_file)
        if not ch_elements_ready:
            ch_status = ItemStatus.stale if ch_ok else ItemStatus.pending
            reason = "Preceding audio groups or visual segments are not completed"
            all_chapters_ready = False
        elif not ch_ok:
            ch_status = ItemStatus.pending
            reason = ch_file_reason
            all_chapters_ready = False
        elif ch.chapter_hash != expected_ch_hash:
            ch_status = ItemStatus.stale
            reason = f"Chapter hash mismatch (manifest: {ch.chapter_hash}, expected: {expected_ch_hash})"
            all_chapters_ready = False
        else:
            ch_status = ItemStatus.completed
            reason = "Chapter video exists and is up to date"

        units.append(
            WorkUnit(
                unit_id=f"ch{ch_idx}_render",
                unit_type="chapter_render",
                chapter_index=ch_idx,
                status=ch_status,
                reason=reason,
                target_file=target_ch_file,
                expected_hash=expected_ch_hash,
                current_hash=ch.chapter_hash,
            )
        )

    # Final Concat work unit
    target_final = manifest.final_video_file or "output/final_video.mp4"
    bgm_hash = ""
    try:
        from app.services.longform.render.bgm import resolve_bgm_file
        from app.services.longform.hashing import hash_file
        bgm_file = resolve_bgm_file(p_dir, manifest.preset_snapshot)
        if bgm_file and bgm_file.is_file():
            bgm_hash = hash_file(bgm_file)
    except Exception:
        bgm_hash = ""

    expected_final_hash = compute_final_concat_hash(
        manifest.chapters,
        bgm_hash=bgm_hash,
        pipeline_version=manifest.pipeline_version,
    )

    final_ok, final_reason = _check_file_validity(p_dir, target_final)
    if not all_chapters_ready:
        final_status = ItemStatus.stale if final_ok else ItemStatus.pending
        reason = "One or more chapters are not completed"
    elif not final_ok:
        final_status = ItemStatus.pending
        reason = final_reason
    elif manifest.final_video_hash != expected_final_hash:
        final_status = ItemStatus.stale
        reason = "Final video hash mismatch with current chapters"
    else:
        final_status = ItemStatus.completed
        reason = "Final video file exists and all chapters are up to date"

    units.append(
        WorkUnit(
            unit_id="final_concat",
            unit_type="final_concat",
            chapter_index=0,
            status=final_status,
            reason=reason,
            target_file=target_final,
            expected_hash=expected_final_hash,
            current_hash=manifest.final_video_hash,
        )
    )

    return ExecutionPlan(project_id=manifest.project_id, all_units=units)
