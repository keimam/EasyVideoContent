"""High-Level Voice Synthesis and Audio Management Service for Longform Projects (3.1-3.7, R1-R4).

Coordinates:
- Project-wide audio synthesis across all chapters and groups with incremental resume
- Immediate WAV PCM conversion and sentence alignment
- Word match ratio tracking and fallback diagnostics (R2)
- Word-level timing sidecar generation and timeline synchronization (R3)
- Assembly of chapter audio timelines with configurable pauses
- Advisory project locking and safe Ctrl+C interruption handling
- Detailed audio status reporting with alignment method and match ratio diagnostics
- Project voice configuration and voice catalog queries
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services import voice as upstream_voice
from app.services.longform.audio_preview import (
    build_chapter_audio_and_timeline,
    generate_chapter_preview_and_srt,
)
from app.services.longform.audio_util import measure_wav_duration_and_samples
from app.services.longform.hashing import compute_audio_group_hash
from app.services.longform.planner import compute_plan
from app.services.longform.schema import ItemStatus, ProjectManifest, TtsGroup
from app.services.longform.store import (
    ProjectLock,
    ProjectLockedError,
    load_manifest,
    save_manifest,
)
from app.services.longform.tts_adapter import (
    TTSAdapter,
    TTSAdapterError,
    TTSConfigurationError,
)


class VoiceService:
    """Manages audio synthesis, chapter timeline assembly, and voice configuration for a project."""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir).resolve()

    def synthesize_all(
        self,
        provider: Optional[str] = None,
        concurrency: int = 2,
        fake_delay: float = 0.0,
        fail_at_group: Optional[str] = None,
        word_cue_threshold: float = 0.85,
    ) -> ProjectManifest:
        """Synthesize all pending or stale audio groups with resume and atomic updates (3.1-3.7, R2, R3)."""
        with ProjectLock(self.project_dir):
            manifest = load_manifest(self.project_dir)

            selected_provider = provider
            if not selected_provider:
                preset_tts = manifest.preset_snapshot.get("tts_provider", "edge")
                selected_provider = preset_tts

            preset_voice = manifest.preset_snapshot.get("default_voice", "en-US-ChristopherNeural-Male")
            pause_ms = manifest.preset_snapshot.get("pause_between_groups_ms", 350)
            wpm = manifest.preset_snapshot.get("words_per_minute", 145)
            eff_threshold = manifest.preset_snapshot.get("word_cue_threshold", word_cue_threshold)

            adapter = TTSAdapter(
                provider=selected_provider,
                voice_name=preset_voice,
                voice_rate=1.0,
                output_dir=self.project_dir,
                fake_delay=fake_delay,
                words_per_minute=wpm,
                word_cue_threshold=eff_threshold,
            )

            adapter.validate_configuration()

            plan = compute_plan(manifest, self.project_dir)
            audio_units = [u for u in plan.units if u.unit_type == "tts_group_audio" and u.status != ItemStatus.completed]

            logger.info(
                f"Starting audio synthesis for project '{manifest.project_id}'. "
                f"Total audio groups to process: {len(audio_units)} (Provider: {adapter.provider}, Voice: {preset_voice})"
            )

            for ch in manifest.chapters:
                ch_idx = ch.chapter_index
                effective_voice = ch.voice_override or preset_voice

                groups_to_synth: List[Tuple[TtsGroup, str, str]] = []
                for group in ch.tts_groups:
                    g_idx = group.group_index
                    unit_id = f"ch{ch_idx}_g{g_idx}_audio"

                    expected_hash = compute_audio_group_hash(
                        paragraph_text=group.get_effective_paragraph_text(),
                        tts_provider=adapter.provider,
                        tts_model="",
                        voice_name=effective_voice,
                        voice_rate=group.audio.voice_rate or 1.0,
                        text_norm_version=group.audio.text_norm_version,
                        audio_format="wav_48k_pcm",
                    )

                    target_wav = self.project_dir / (group.audio.file_path or f"assets/audio/groups/ch{ch_idx}_g{g_idx}.wav")

                    is_valid_on_disk = (
                        target_wav.is_file()
                        and target_wav.stat().st_size > 44
                        and group.status == ItemStatus.completed
                        and group.audio.audio_hash == expected_hash
                    )

                    if is_valid_on_disk:
                        logger.debug(f"Audio group {unit_id} already up-to-date. Skipping.")
                        continue

                    groups_to_synth.append((group, unit_id, expected_hash))

                if not groups_to_synth:
                    ch_wav = self.project_dir / f"assets/audio/chapters/chapter_{ch_idx:02d}.wav"
                    if not ch_wav.is_file() and all(g.status == ItemStatus.completed for g in ch.tts_groups):
                        build_chapter_audio_and_timeline(
                            project_dir=self.project_dir,
                            chapter=ch,
                            pause_between_groups_ms=pause_ms,
                        )
                        ch_ratios = [g.audio.word_match_ratio for g in ch.tts_groups if g.audio.word_match_ratio is not None]
                        if ch_ratios:
                            ch.min_word_match_ratio = round(min(ch_ratios), 3)
                            ch.avg_word_match_ratio = round(sum(ch_ratios) / float(len(ch_ratios)), 3)
                        save_manifest(manifest, self.project_dir)
                    continue

                def _synth_worker(item: Tuple[TtsGroup, str, str]):
                    grp, u_id, exp_hash = item
                    if fail_at_group and fail_at_group == u_id:
                        raise RuntimeError(f"Simulated failure at audio unit {u_id}")
                    logger.info(f"Synthesizing audio for {u_id}...")
                    res = adapter.synthesize_group(
                        group=grp,
                        chapter_index=ch_idx,
                        voice_override=ch.voice_override,
                        force_refresh=True,
                    )
                    return grp, u_id, exp_hash, res

                eff_concurrency = 1 if (fail_at_group or concurrency <= 1) else min(concurrency, len(groups_to_synth))
                if eff_concurrency > 1:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=eff_concurrency) as executor:
                        future_to_item = {executor.submit(_synth_worker, item): item for item in groups_to_synth}
                        for future in concurrent.futures.as_completed(future_to_item):
                            grp, u_id, exp_hash, res = future.result()
                            grp.audio.file_path = f"assets/audio/groups/ch{ch_idx}_g{grp.group_index}.wav"
                            grp.audio.audio_hash = exp_hash
                            grp.audio.duration_seconds = res.duration_seconds
                            grp.audio.alignment_method = res.alignment_method
                            grp.audio.tts_provider = adapter.provider
                            grp.audio.voice_name = effective_voice
                            grp.audio.sample_rate = res.sample_rate
                            grp.audio.channels = 1
                            grp.audio.audio_format = "wav_48k_pcm"
                            grp.audio.word_match_ratio = res.word_match_ratio
                            grp.audio.fallback_reason = res.fallback_reason
                            grp.audio.word_timings_file = res.word_timings_file
                            grp.status = ItemStatus.completed
                            grp.segments = res.updated_segments
                            save_manifest(manifest, self.project_dir)
                else:
                    for item in groups_to_synth:
                        grp, u_id, exp_hash, res = _synth_worker(item)
                        grp.audio.file_path = f"assets/audio/groups/ch{ch_idx}_g{grp.group_index}.wav"
                        grp.audio.audio_hash = exp_hash
                        grp.audio.duration_seconds = res.duration_seconds
                        grp.audio.alignment_method = res.alignment_method
                        grp.audio.tts_provider = adapter.provider
                        grp.audio.voice_name = effective_voice
                        grp.audio.sample_rate = res.sample_rate
                        grp.audio.channels = 1
                        grp.audio.audio_format = "wav_48k_pcm"
                        grp.audio.word_match_ratio = res.word_match_ratio
                        grp.audio.fallback_reason = res.fallback_reason
                        grp.audio.word_timings_file = res.word_timings_file
                        grp.status = ItemStatus.completed
                        grp.segments = res.updated_segments
                        save_manifest(manifest, self.project_dir)

                all_groups_done = all(g.status == ItemStatus.completed for g in ch.tts_groups)
                if all_groups_done:
                    build_chapter_audio_and_timeline(
                        project_dir=self.project_dir,
                        chapter=ch,
                        pause_between_groups_ms=pause_ms,
                    )
                    ch_ratios = [g.audio.word_match_ratio for g in ch.tts_groups if g.audio.word_match_ratio is not None]
                    if ch_ratios:
                        ch.min_word_match_ratio = round(min(ch_ratios), 3)
                        ch.avg_word_match_ratio = round(sum(ch_ratios) / float(len(ch_ratios)), 3)
                    save_manifest(manifest, self.project_dir)

            return manifest

    def get_status(self) -> Dict[str, Any]:
        """Compute detailed audio status summary across all chapters (3.7, R2)."""
        manifest = load_manifest(self.project_dir)
        total_groups = 0
        completed_groups = 0
        total_duration = 0.0
        method_counts: Dict[str, int] = {"word_cue": 0, "whisper": 0, "proportional": 0}
        chapters_info = []
        all_ratios: List[float] = []

        is_fake_audio = False

        for ch in manifest.chapters:
            ch_groups = len(ch.tts_groups)
            ch_completed = 0
            ch_duration = ch.audio_duration_seconds or 0.0
            ch_methods: Dict[str, int] = {"word_cue": 0, "whisper": 0, "proportional": 0}
            ch_ratios: List[float] = []

            for g in ch.tts_groups:
                total_groups += 1
                if g.audio.tts_provider == "fake":
                    is_fake_audio = True

                wav_file = self.project_dir / (g.audio.file_path or f"assets/audio/groups/ch{ch.chapter_index}_g{g.group_index}.wav")
                if wav_file.is_file() and wav_file.stat().st_size > 44 and g.status == ItemStatus.completed:
                    completed_groups += 1
                    ch_completed += 1
                    method = g.audio.alignment_method or "word_cue"
                    method_counts[method] = method_counts.get(method, 0) + 1
                    ch_methods[method] = ch_methods.get(method, 0) + 1

                if g.audio.word_match_ratio is not None:
                    ch_ratios.append(g.audio.word_match_ratio)
                    all_ratios.append(g.audio.word_match_ratio)

            total_duration += ch_duration

            ch_min = round(min(ch_ratios), 3) if ch_ratios else 0.0
            ch_avg = round(sum(ch_ratios) / float(len(ch_ratios)), 3) if ch_ratios else 0.0

            chapters_info.append(
                {
                    "chapter_index": ch.chapter_index,
                    "chapter_title": ch.chapter_title,
                    "total_groups": ch_groups,
                    "completed_groups": ch_completed,
                    "duration_seconds": round(ch_duration, 3),
                    "audio_file": ch.audio_file,
                    "preview_video_file": ch.preview_video_file,
                    "alignment_methods": ch_methods,
                    "word_match_min": ch_min,
                    "word_match_avg": ch_avg,
                }
            )

        root_min = round(min(all_ratios), 3) if all_ratios else 0.0
        root_avg = round(sum(all_ratios) / float(len(all_ratios)), 3) if all_ratios else 0.0

        return {
            "project_id": manifest.project_id,
            "is_fake": is_fake_audio,
            "total_groups": total_groups,
            "completed_groups": completed_groups,
            "total_duration_seconds": round(total_duration, 3),
            "alignment_method_counts": method_counts,
            "word_match_min": root_min,
            "word_match_avg": root_avg,
            "chapters": chapters_info,
            "is_fully_synthesized": (total_groups > 0 and completed_groups == total_groups),
        }

    get_audio_status = get_status

    def set_voice(self, voice_name: str, voice_rate: float = 1.0) -> ProjectManifest:
        """Set project default narrator voice using hash-based invalidation (3.5, 3.7, R4, V3).

        V3 fix: Instead of clearing audio_hash and marking all groups stale unconditionally,
        we recompute the expected audio_hash for each group with the new voice+rate. If the
        new hash matches the stored hash (meaning the group was previously synthesized with
        this exact voice+rate and the file is still present), the group stays 'completed'
        without re-synthesis. This makes A→B→A a no-op for synthesis.
        """
        clean_voice = voice_name.strip()
        with ProjectLock(self.project_dir):
            manifest = load_manifest(self.project_dir)

            manifest.preset_snapshot["default_voice"] = clean_voice
            manifest.preset_snapshot["voice_rate"] = float(voice_rate)

            for ch in manifest.chapters:
                ch_needs_stale = False
                for g in ch.tts_groups:
                    # Compute what the hash would be for the new voice
                    new_hash = compute_audio_group_hash(
                        paragraph_text=g.get_effective_paragraph_text(),
                        tts_provider=g.audio.tts_provider or "edge",
                        tts_model=g.audio.tts_model or "",
                        voice_name=clean_voice,
                        voice_rate=float(voice_rate),
                        text_norm_version=g.audio.text_norm_version or "v1",
                        audio_format=g.audio.audio_format or "wav_48k_pcm",
                    )

                    # Update voice metadata unconditionally
                    g.audio.voice_name = clean_voice
                    g.audio.voice_rate = float(voice_rate)

                    # V3: Hash-based staleness — if hash matches, group is still valid
                    target_wav = self.project_dir / (g.audio.file_path or f"assets/audio/groups/ch{ch.chapter_index}_g{g.group_index}.wav")
                    if g.audio.audio_hash == new_hash and target_wav.is_file() and target_wav.stat().st_size > 44:
                        # Audio on disk was synthesized with this voice/rate: keep/restore completed!
                        g.status = ItemStatus.completed
                        logger.debug(f"Group ch{ch.chapter_index}_g{g.group_index}: hash matches new voice, keeping completed")
                    else:
                        # Setting does not match audio on disk: mark stale
                        # (Preserve g.audio.audio_hash so switching back restores completed without synth)
                        g.status = ItemStatus.stale
                        ch_needs_stale = True

                if ch_needs_stale:
                    ch.status = ItemStatus.stale
                elif all(grp.status == ItemStatus.completed for grp in ch.tts_groups):
                    ch.status = ItemStatus.completed

            if any(c.status == ItemStatus.stale for c in manifest.chapters):
                manifest.status = ItemStatus.stale

            save_manifest(manifest, self.project_dir)
            return manifest

    def generate_preview(self, chapter_index: int) -> Tuple[Path, Path, Path]:
        """Generate audio preview WAV, SRT subtitles, and 720p black preview MP4 (3.6, R1)."""
        manifest = load_manifest(self.project_dir)
        pause_ms = manifest.preset_snapshot.get("pause_between_groups_ms", 350)
        return generate_chapter_preview_and_srt(
            project_dir=self.project_dir,
            manifest=manifest,
            chapter_index=chapter_index,
            pause_between_groups_ms=pause_ms,
        )


def list_available_voices(language_prefix: Optional[str] = None) -> List[str]:
    """Query available Edge TTS voices from upstream azure_voices catalog (3.6)."""
    filters = [language_prefix.strip()] if language_prefix else None
    return upstream_voice.get_all_azure_voices(filters)
