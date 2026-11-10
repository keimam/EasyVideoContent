"""High-level CapCut service coordinating doctor checks, smoke draft, full project export, and backup asset pack."""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.longform.capcut.builder import (
    CapCutDraftBuilder,
    calculate_bottom_anchored_y,
    format_capcut_subtitle,
)
from app.services.longform.capcut.detector import (
    detect_draft_folder,
    get_capcut_doctor_report,
    is_capcut_running,
)
from app.services.longform.render.bgm import concatenate_wav_files, resolve_bgm_file
from app.services.longform.store import get_project_dir, load_manifest


def parse_srt_entries(srt_path: Path, max_entries: int | None = None) -> list[dict[str, Any]]:
    """Parse an SRT file into dicts with text and timing in seconds."""
    if not srt_path.is_file():
        return []

    content = srt_path.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(
        r"(\d+)\s*\r?\n(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\r?\n(.*?)(?=\r?\n\r?\n|\Z)",
        re.DOTALL,
    )

    entries = []
    for match in pattern.finditer(content):
        (
            idx,
            sh, sm, ss, sms,
            eh, em, es, ems,
            text_block,
        ) = match.groups()

        start_s = (
            int(sh) * 3600
            + int(sm) * 60
            + int(ss)
            + int(sms) / 1000.0
        )
        end_s = (
            int(eh) * 3600
            + int(em) * 60
            + int(es)
            + int(ems) / 1000.0
        )
        dur_s = max(0.1, end_s - start_s)
        clean_text = " ".join(line.strip() for line in text_block.splitlines() if line.strip())

        entries.append({
            "text": clean_text,
            "start_seconds": start_s,
            "duration_seconds": dur_s,
        })
        if max_entries is not None and len(entries) >= max_entries:
            break

    return entries


def resolve_capcut_text_size(manifest: Any) -> int:
    """
    Resolve capcut_text_size from project preset snapshot, preset file, or fallback to 7 (C3).
    """
    if hasattr(manifest, "preset_snapshot") and manifest.preset_snapshot:
        snap = manifest.preset_snapshot
        if isinstance(snap, dict) and snap.get("capcut_text_size"):
            return int(snap["capcut_text_size"])
        elif hasattr(snap, "capcut_text_size") and snap.capcut_text_size:
            return int(snap.capcut_text_size)

    preset_id = getattr(manifest, "preset_id", None)
    if preset_id:
        repo_root = Path(__file__).resolve().parents[4]
        for p_dir in [repo_root / "storage" / "presets", repo_root / "resource" / "presets"]:
            p_file = p_dir / f"{preset_id}.json"
            if p_file.is_file():
                try:
                    data = json.loads(p_file.read_text(encoding="utf-8"))
                    if data.get("capcut_text_size"):
                        return int(data["capcut_text_size"])
                except Exception:
                    pass

    return 7


def set_capcut_text_size(preset_id: str, size: int) -> Path:
    """
    Update capcut_text_size in storage/presets/<preset_id>.override.json (B1).
    Leaves resource/presets/ completely untouched.
    """
    if not (4 <= size <= 24):
        raise ValueError(f"capcut_text_size must be between 4 and 24, got {size}")

    from app.services.longform.presets import get_preset, get_preset_directories
    # Validate that the preset actually exists
    get_preset(preset_id)

    _, storage_dir = get_preset_directories()
    storage_dir.mkdir(parents=True, exist_ok=True)
    override_path = storage_dir / f"{preset_id}.override.json"

    if override_path.is_file():
        try:
            data = json.loads(override_path.read_text(encoding="utf-8"))
        except Exception:
            data = {"id": preset_id}
    else:
        data = {"id": preset_id}

    data["capcut_text_size"] = int(size)
    override_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(f"Updated preset override for '{preset_id}' with capcut_text_size = {size} at {override_path}")
    return override_path


def render_ducked_bgm_stem(
    project_dir: Path,
    chapter_wavs: list[Path],
    output_bgm_wav: Path,
    preset_snapshot: dict[str, Any],
    total_duration_seconds: float,
) -> Path | None:
    """Render a standalone ducked BGM audio stem (WAV) following Stage 5 parameters."""
    bgm_file = resolve_bgm_file(project_dir, preset_snapshot)
    if not bgm_file or not bgm_file.is_file():
        logger.info("No background music file resolved; skipping BGM stem.")
        return None

    output_bgm_wav.parent.mkdir(parents=True, exist_ok=True)
    temp_narr_wav = output_bgm_wav.parent / f"temp_narr_{uuid.uuid4().hex[:6]}.wav"

    try:
        concatenate_wav_files(chapter_wavs, temp_narr_wav)

        bgm_vol = float(preset_snapshot.get("bgm_volume", 0.20))
        duck_thresh = float(preset_snapshot.get("bgm_ducking_threshold", 0.03))
        duck_ratio = float(preset_snapshot.get("bgm_ducking_ratio", 4.0))
        duck_attack = int(preset_snapshot.get("bgm_ducking_attack_ms", 200))
        duck_release = int(preset_snapshot.get("bgm_ducking_release_ms", 1000))

        fade_out_start = max(0.0, total_duration_seconds - 3.0)

        filter_complex = (
            f"[0:a]volume={bgm_vol},"
            f"afade=t=in:ss=0:d=2,"
            f"afade=t=out:st={fade_out_start:.2f}:d=3[bgm_faded];"
            f"[1:a]asplit=1[narr_sc];"
            f"[bgm_faded][narr_sc]sidechaincompress="
            f"threshold={duck_thresh}:ratio={duck_ratio}:attack={duck_attack}:release={duck_release}[ducked_bgm]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(bgm_file.resolve()),
            "-i", str(temp_narr_wav.resolve()),
            "-filter_complex", filter_complex,
            "-map", "[ducked_bgm]",
            "-t", f"{total_duration_seconds:.3f}",
            "-c:a", "pcm_s16le",
            str(output_bgm_wav.resolve()),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)
        if res.returncode != 0 or not output_bgm_wav.is_file():
            logger.warning(f"Failed to render ducked BGM stem: {res.stderr}")
            return None

        return output_bgm_wav
    finally:
        if temp_narr_wav.is_file():
            temp_narr_wav.unlink(missing_ok=True)


def get_unique_draft_name(base_name: str, check_dirs: list[Path]) -> str:
    """Ensure draft name does not collide with existing draft directories (B3)."""
    candidate = base_name
    idx = 1
    while any((d / candidate).exists() for d in check_dirs if d and d.is_dir()):
        candidate = f"{base_name}_{idx}"
        idx += 1
    return candidate


def build_smoke_draft(project_id: str, install: bool = False) -> dict[str, Any]:
    """
    Build small smoke test draft 'MPT_SMOKE_<id>' (A3).
    Includes 3 video clips, trimmed narration, and 2 calibrated subtitles.
    """
    project_dir = get_project_dir(project_id)
    manifest = load_manifest(project_dir)

    if not manifest.chapters:
        raise ValueError(f"Project '{project_id}' has no chapters in manifest.")

    ch1 = manifest.chapters[0]
    if len(ch1.shots) < 3:
        raise ValueError(f"Chapter 1 has fewer than 3 shots.")

    shots = ch1.shots[:3]

    video_clips = []
    total_shots_duration = 0.0
    for s in shots:
        clip_rel = s.rendered_clip or s.asset_path
        if not clip_rel:
            raise FileNotFoundError(f"Shot '{s.shot_id}' has no clip path.")
        clip_path = project_dir / clip_rel
        if not clip_path.is_file():
            alt = project_dir / "assets" / "visual" / "shots" / f"{s.shot_id}.mp4"
            if alt.is_file():
                clip_path = alt
            else:
                raise FileNotFoundError(f"Clip not found: {clip_path}")

        dur = s.duration_seconds or 5.0
        total_shots_duration += dur
        video_clips.append({"path": clip_path, "duration_seconds": dur})

    if not ch1.audio_file:
        raise FileNotFoundError("Chapter 1 has no audio_file.")
    ch1_audio_path = project_dir / ch1.audio_file

    temp_audio_dir = Path(tempfile.mkdtemp(prefix="mpt_smoke_audio_"))
    trimmed_audio_path = temp_audio_dir / "audio_track.wav"

    try:
        trim_cmd = [
            "ffmpeg", "-y",
            "-i", str(ch1_audio_path),
            "-t", f"{total_shots_duration:.3f}",
            "-c:a", "pcm_s16le",
            str(trimmed_audio_path),
        ]
        res = subprocess.run(trim_cmd, capture_output=True, text=True, check=False, timeout=15)
        if res.returncode != 0 or not trimmed_audio_path.is_file():
            raise RuntimeError(f"FFmpeg trim failed: {res.stderr}")

        srt_candidates = [
            project_dir / "export" / "preview" / "chapter_01_preview.srt",
            project_dir / "export" / "subtitles" / "full_timeline.srt",
        ]
        subtitles = []
        for sc in srt_candidates:
            if sc.is_file():
                subtitles = parse_srt_entries(sc, max_entries=2)
                if subtitles:
                    break

        if not subtitles:
            subtitles = [
                {"text": "Subtitle Preview 1", "start_seconds": 0.0, "duration_seconds": 5.0},
                {"text": "Subtitle Preview 2", "start_seconds": 5.0, "duration_seconds": 5.0},
            ]

        draft_name = f"MPT_SMOKE_{project_id}"
        export_draft_dir = project_dir / "export" / "capcut" / draft_name

        capcut_text_size = resolve_capcut_text_size(manifest)
        builder = CapCutDraftBuilder()
        builder.build_draft(
            output_dir=export_draft_dir,
            draft_name=draft_name,
            video_clips=video_clips,
            narration_clips=[{
                "path": trimmed_audio_path,
                "duration_seconds": total_shots_duration,
                "start_offset_seconds": 0.0,
            }],
            subtitles=subtitles,
            portable=True,
            default_text_size=capcut_text_size,
        )

        installed_path = None
        if install:
            if is_capcut_running():
                raise RuntimeError("CapCut is currently running! Please close CapCut before installing.")

            draft_root = detect_draft_folder()
            if not draft_root or not draft_root.is_dir():
                raise RuntimeError("Could not locate CapCut draft directory (com.lveditor.draft).")

            target_install_dir = draft_root / draft_name
            builder.build_draft(
                output_dir=target_install_dir,
                draft_name=draft_name,
                video_clips=video_clips,
                narration_clips=[{
                    "path": export_draft_dir / "media" / "narration_01.wav",
                    "duration_seconds": total_shots_duration,
                    "start_offset_seconds": 0.0,
                }],
                subtitles=subtitles,
                portable=True,
                draft_root_path=draft_root.resolve().as_posix(),
                default_text_size=capcut_text_size,
            )
            installed_path = str(target_install_dir)

        return {
            "project_id": project_id,
            "draft_name": draft_name,
            "export_path": str(export_draft_dir),
            "installed": install,
            "installed_path": installed_path,
            "shots_count": len(video_clips),
            "total_duration_seconds": total_shots_duration,
            "subtitles_count": len(subtitles),
            "capcut_text_size": capcut_text_size,
        }
    finally:
        shutil.rmtree(temp_audio_dir, ignore_errors=True)


def export_full_project(
    project_id: str,
    install: bool = False,
    portable: bool = False,
    yes: bool = False,
    custom_draft_name: str | None = None,
) -> dict[str, Any]:
    """
    Export the full multi-chapter project to CapCut Desktop draft (B1–B5).

    - Video track: All shots in sequence, exact microseconds
    - Narration track: Chapter WAVs with exact start offsets
    - BGM track: Ducked BGM stem from Stage 5 parameters
    - Text track: All subtitle sentence chunks (calibrated size 8, y=-0.80)
    - Media handling: Default absolute project drive references, --portable copies with disk check
    - Backup Asset Pack: export/capcut_assets/ with numbered shots, audios, SRT, README
    """
    project_dir = get_project_dir(project_id)
    manifest = load_manifest(project_dir)

    if not manifest.chapters:
        raise ValueError(f"Project '{project_id}' has no chapters.")

    timestamp_str = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    base_draft_name = custom_draft_name or f"MPT_{project_id}_{timestamp_str}"

    export_capcut_root = project_dir / "export" / "capcut"
    export_capcut_root.mkdir(parents=True, exist_ok=True)

    if install and is_capcut_running():
        raise RuntimeError("CapCut sedang berjalan. Tutup CapCut sebelum ekspor untuk mencegah korupsi draft.")

    draft_root = detect_draft_folder()
    check_dirs = [export_capcut_root]
    if install and draft_root:
        check_dirs.append(draft_root)

    # Name collision check (B3)
    draft_name = get_unique_draft_name(base_draft_name, check_dirs)

    # --- 1. GATHER ALL VIDEO SHOTS (B1) ---
    all_video_clips = []
    total_timeline_duration = 0.0
    fps = float(getattr(manifest.encoder_settings, "fps", 30.0) or 30.0)

    for ch in manifest.chapters:
        for s in sorted(ch.shots, key=lambda x: x.shot_index):
            clip_rel = s.rendered_clip or s.asset_path
            if not clip_rel:
                raise FileNotFoundError(f"Shot '{s.shot_id}' in chapter {ch.chapter_index} has no clip path.")
            clip_path = project_dir / clip_rel
            if not clip_path.is_file():
                alt = project_dir / "assets" / "visual" / "shots" / f"{s.shot_id}.mp4"
                if alt.is_file():
                    clip_path = alt
                else:
                    raise FileNotFoundError(f"Clip file not found: {clip_path}")

            dur = (s.target_frames / fps) if s.target_frames else float(s.duration_seconds or 5.0)
            all_video_clips.append({
                "path": clip_path,
                "duration_seconds": dur,
                "chapter_index": ch.chapter_index,
                "shot_id": s.shot_id,
            })
            total_timeline_duration += dur

    # --- 2. GATHER NARRATION CLIPS WITH CHAPTER OFFSETS (B1) ---
    all_narrations = []
    current_chapter_offset = 0.0
    chapter_wav_paths = []

    for ch in manifest.chapters:
        if not ch.audio_file:
            raise FileNotFoundError(f"Chapter {ch.chapter_index} has no audio_file.")
        audio_path = project_dir / ch.audio_file
        if not audio_path.is_file():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        chapter_wav_paths.append(audio_path)

        # Calculate chapter video duration to advance offset
        ch_shots = [s for s in all_video_clips if s["chapter_index"] == ch.chapter_index]
        ch_duration = sum(s["duration_seconds"] for s in ch_shots)

        audio_dur = float(ch.audio_duration_seconds or ch_duration)

        all_narrations.append({
            "path": audio_path,
            "duration_seconds": audio_dur,
            "start_offset_seconds": current_chapter_offset,
            "chapter_index": ch.chapter_index,
        })
        current_chapter_offset += ch_duration

    # --- 3. RENDER DUCKED BGM STEM (B1) ---
    capcut_media_dir = project_dir / "export" / "capcut_media"
    capcut_media_dir.mkdir(parents=True, exist_ok=True)

    preset_snap = manifest.preset_snapshot or {}
    ducked_bgm_path = capcut_media_dir / "bgm_ducked.wav"

    rendered_bgm = render_ducked_bgm_stem(
        project_dir=project_dir,
        chapter_wavs=chapter_wav_paths,
        output_bgm_wav=ducked_bgm_path,
        preset_snapshot=preset_snap,
        total_duration_seconds=total_timeline_duration,
    )
    bgm_clip = None
    if rendered_bgm and rendered_bgm.is_file():
        bgm_clip = {
            "path": rendered_bgm,
            "duration_seconds": total_timeline_duration,
        }

    # --- 4. GATHER SUBTITLES (B1) ---
    subtitles_srt = project_dir / "export" / "subtitles" / "full_timeline.srt"
    all_subtitles = []
    if subtitles_srt.is_file():
        all_subtitles = parse_srt_entries(subtitles_srt)
    else:
        # Fallback to preview SRTs if available
        for ch in manifest.chapters:
            prev_srt = project_dir / "export" / "preview" / f"chapter_{ch.chapter_index:02d}_preview.srt"
            if prev_srt.is_file():
                all_subtitles.extend(parse_srt_entries(prev_srt))

    # --- 5. VALIDATION BEFORE WRITING (B3) ---
    final_video_path = project_dir / "export" / "final_video.mp4"
    final_video_duration = None
    if final_video_path.is_file():
        try:
            p_res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(final_video_path)],
                capture_output=True, text=True, check=True
            )
            final_video_duration = float(p_res.stdout.strip())
            dur_diff = abs(total_timeline_duration - final_video_duration)
            one_frame_tolerance = (1.0 / fps) + 0.002
            if dur_diff > one_frame_tolerance:
                raise RuntimeError(
                    f"Timeline duration ({total_timeline_duration:.3f}s) differs from final_video.mp4 "
                    f"({final_video_duration:.3f}s) by {dur_diff:.3f}s (> 1 frame: {one_frame_tolerance:.4f}s)."
                )
        except Exception as exc:
            logger.warning(f"Could not probe final_video.mp4 duration: {exc}")

    # Calculate total media size
    total_media_bytes = sum(Path(v["path"]).stat().st_size for v in all_video_clips)
    total_media_bytes += sum(Path(n["path"]).stat().st_size for n in all_narrations)
    if bgm_clip:
        total_media_bytes += Path(bgm_clip["path"]).stat().st_size

    total_media_mb = total_media_bytes / (1024 * 1024)

    # --- 6. DISK SPACE CHECK FOR PORTABLE (B2) ---
    if portable:
        dest_check_dir = draft_root if (install and draft_root) else export_capcut_root
        free_bytes = shutil.disk_usage(dest_check_dir).free
        free_mb = free_bytes / (1024 * 1024)

        if not yes:
            raise RuntimeError(
                f"--portable requires copying {total_media_mb:.1f} MB of media files. "
                f"Available space on {dest_check_dir.anchor}: {free_mb:.1f} MB. "
                f"Please pass --yes to confirm."
            )

        if free_bytes < (total_media_bytes * 1.2):
            raise RuntimeError(
                f"Insufficient disk space on {dest_check_dir.anchor}: "
                f"Required {total_media_mb * 1.2:.1f} MB (with 20% margin), but only {free_mb:.1f} MB available."
            )

    # --- 7. BUILD CAPCUT DRAFT (B1, B2, C1, C2, C3) ---
    export_draft_dir = export_capcut_root / draft_name

    capcut_text_size = resolve_capcut_text_size(manifest)
    builder = CapCutDraftBuilder()
    builder.build_draft(
        output_dir=export_draft_dir,
        draft_name=draft_name,
        video_clips=all_video_clips,
        narration_clips=all_narrations,
        bgm_clip=bgm_clip,
        subtitles=all_subtitles,
        portable=portable,
        default_text_size=capcut_text_size,
    )

    # --- 8. BUILD BACKUP ASSET PACK (B5) ---
    build_backup_asset_pack(
        project_dir=project_dir,
        manifest=manifest,
        all_video_clips=all_video_clips,
        chapter_wav_paths=chapter_wav_paths,
        ducked_bgm_path=ducked_bgm_path if (bgm_clip and ducked_bgm_path.is_file()) else None,
        subtitles_srt=subtitles_srt if subtitles_srt.is_file() else None,
    )

    # --- 9. POST-WRITING VALIDATION (B3) ---
    validate_built_draft(export_draft_dir)

    installed_path = None
    if install:
        if is_capcut_running():
            raise RuntimeError("CapCut sedang berjalan. Tutup CapCut sebelum ekspor untuk mencegah korupsi draft.")

        if not draft_root or not draft_root.is_dir():
            raise RuntimeError("Could not locate CapCut draft directory (com.lveditor.draft).")

        target_install_dir = draft_root / draft_name

        if portable:
            builder.build_draft(
                output_dir=target_install_dir,
                draft_name=draft_name,
                video_clips=all_video_clips,
                narration_clips=all_narrations,
                bgm_clip=bgm_clip,
                subtitles=all_subtitles,
                portable=True,
                draft_root_path=draft_root.resolve().as_posix(),
                default_text_size=capcut_text_size,
            )
        else:
            # Non-portable: draft in C: points directly to media on project drive
            builder.build_draft(
                output_dir=target_install_dir,
                draft_name=draft_name,
                video_clips=all_video_clips,
                narration_clips=all_narrations,
                bgm_clip=bgm_clip,
                subtitles=all_subtitles,
                portable=False,
                draft_root_path=draft_root.resolve().as_posix(),
                default_text_size=capcut_text_size,
            )

        validate_built_draft(target_install_dir)
        installed_path = str(target_install_dir)
        logger.info(f"Draft successfully installed into CapCut projects: {installed_path}")

    return {
        "project_id": project_id,
        "draft_name": draft_name,
        "export_path": str(export_draft_dir),
        "installed": install,
        "installed_path": installed_path,
        "portable": portable,
        "media_size_mb": total_media_mb,
        "video_segments_count": len(all_video_clips),
        "narration_segments_count": len(all_narrations),
        "bgm_segments_count": 1 if bgm_clip else 0,
        "subtitles_count": len(all_subtitles),
        "total_timeline_duration": total_timeline_duration,
        "final_video_duration": final_video_duration,
        "capcut_text_size": capcut_text_size,
        "backup_pack_path": str(project_dir / "export" / "capcut_assets"),
    }


def validate_built_draft(draft_dir: Path) -> None:
    """Validate post-write draft integrity (B3)."""
    content_file = draft_dir / "draft_content.json"
    meta_file = draft_dir / "draft_meta_info.json"
    project_file = draft_dir / "Timelines" / "project.json"

    if not content_file.is_file() or not meta_file.is_file() or not project_file.is_file():
        raise RuntimeError("Draft directory missing mandatory files.")

    # 1. JSON parse validation
    content_data = json.loads(content_file.read_text(encoding="utf-8"))
    meta_data = json.loads(meta_file.read_text(encoding="utf-8"))
    project_data = json.loads(project_file.read_text(encoding="utf-8"))

    timeline_id = content_data["id"]
    active_tl_content = draft_dir / "Timelines" / timeline_id / "draft_content.json"

    # 2. Dual copy identical match
    if not active_tl_content.is_file():
        raise RuntimeError(f"Timeline subdirectory draft_content.json missing at {active_tl_content}")

    if content_file.read_bytes() != active_tl_content.read_bytes():
        raise RuntimeError("Root draft_content.json and Timelines/<ID>/draft_content.json are not identical!")

    # 3. Main timeline ID match
    if project_data.get("main_timeline_id") != timeline_id:
        raise RuntimeError(f"Timelines/project.json main_timeline_id does not match timeline id ({timeline_id})")

    # 4. Duration match
    if content_data["duration"] != meta_data["tm_duration"]:
        raise RuntimeError(
            f"Duration mismatch between draft_content ({content_data['duration']}) and draft_meta_info ({meta_data['tm_duration']})"
        )


def build_backup_asset_pack(
    project_dir: Path,
    manifest: Any,
    all_video_clips: list[dict[str, Any]],
    chapter_wav_paths: list[Path],
    ducked_bgm_path: Path | None,
    subtitles_srt: Path | None,
) -> Path:
    """Generate standalone backup asset pack (B5)."""
    pack_dir = project_dir / "export" / "capcut_assets"
    pack_dir.mkdir(parents=True, exist_ok=True)

    # 1. Chapter video folders with numbered clips
    for ch in manifest.chapters:
        ch_dir = pack_dir / f"chapter_{ch.chapter_index:02d}"
        ch_dir.mkdir(parents=True, exist_ok=True)

        ch_shots = [s for s in all_video_clips if s["chapter_index"] == ch.chapter_index]
        for idx, shot in enumerate(ch_shots, start=1):
            src_clip = Path(shot["path"])
            dest_clip = ch_dir / f"{idx:03d}_{src_clip.name}"
            if not dest_clip.is_file() or dest_clip.stat().st_size != src_clip.stat().st_size:
                try:
                    # Try hardlink for zero-copy efficiency on same NTFS volume
                    os.link(src_clip, dest_clip)
                except Exception:
                    shutil.copy2(src_clip, dest_clip)

    # 2. Audio folder
    audio_dir = pack_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    for idx, c_wav in enumerate(chapter_wav_paths, start=1):
        dest_wav = audio_dir / f"chapter_{idx:02d}_narration.wav"
        if not dest_wav.is_file() or dest_wav.stat().st_size != c_wav.stat().st_size:
            shutil.copy2(c_wav, dest_wav)

    if ducked_bgm_path and ducked_bgm_path.is_file():
        dest_bgm = audio_dir / "bgm_ducked.wav"
        if not dest_bgm.is_file() or dest_bgm.stat().st_size != ducked_bgm_path.stat().st_size:
            shutil.copy2(ducked_bgm_path, dest_bgm)

    # 3. Subtitles folder
    sub_dir = pack_dir / "subtitles"
    sub_dir.mkdir(parents=True, exist_ok=True)
    if subtitles_srt and subtitles_srt.is_file():
        shutil.copy2(subtitles_srt, sub_dir / "full_timeline.srt")

    # 4. README instructions (B5: ringkasan proyek, fps, resolusi, daftar bab & shot)
    readme_path = pack_dir / "README.txt"
    fps = float(getattr(manifest.encoder_settings, "fps", 30.0) or 30.0)
    width = getattr(manifest.encoder_settings, "width", 1920) or 1920
    height = getattr(manifest.encoder_settings, "height", 1080) or 1080
    total_shots = len(all_video_clips)
    total_dur_sec = sum(s["duration_seconds"] for s in all_video_clips)

    lines = [
        "======================================================================",
        "PANDUAN PERAKITAN MANUAL PROYEK CAPCUT DESKTOP (BACKUP ASSET PACK)",
        "======================================================================",
        f"Proyek ID:       {getattr(manifest, 'project_id', 'unknown')}",
        f"Resolusi:        {width}x{height}",
        f"Frame Rate:      {fps:.2f} fps",
        f"Total Bab:       {len(manifest.chapters)} bab",
        f"Total Shot:      {total_shots} shot klip",
        f"Total Durasi:    {total_dur_sec:.3f} s ({int(total_dur_sec // 60):02d}:{int(total_dur_sec % 60):02d})",
        "",
        "--- DAFTAR BAB & SHOT ---",
    ]

    for ch in manifest.chapters:
        ch_shots = [s for s in all_video_clips if s["chapter_index"] == ch.chapter_index]
        ch_dur = sum(s["duration_seconds"] for s in ch_shots)
        ch_title = getattr(ch, "chapter_title", None) or f"Bab {ch.chapter_index}"
        lines.append(
            f"Bab {ch.chapter_index:02d}: {ch_title} "
            f"({len(ch_shots)} shots, {ch_dur:.2f}s)"
        )
        for s in ch_shots:
            lines.append(f"  - [{s.get('shot_id', '')}] {Path(s['path']).name} ({s['duration_seconds']:.2f}s)")

    lines.extend([
        "",
        "--- PANDUAN IMPOR MANUAL ---",
        "Gunakan paket aset ini jika berkas draft otomatis tidak dapat dibuka",
        "pada versi CapCut lain atau saat memindahkan proyek ke komputer lain:",
        "",
        "1. Buka CapCut Desktop dan buat 'Proyek Baru' (16:9, 1920x1080).",
        "2. Impor seluruh klip video dari folder 'chapter_01' s/d 'chapter_04'",
        "   ke Media panel, lalu seret berurutan ke Track Video utama.",
        "3. Impor audio narasi dari folder 'audio/' (chapter_01_narration.wav dst)",
        "   ke Track Audio 1. Letakkan masing-masing tepat di awal setiap bab.",
        "4. Impor 'bgm_ducked.wav' ke Track Audio 2 (mulai dari detik 0:00).",
        "5. Impor 'full_timeline.srt' dari folder 'subtitles/' melalui menu",
        "   Teks -> Subtitle Lokal (Import SRT).",
        "======================================================================",
    ])

    readme_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pack_dir


def build_calibration_draft(
    project_id: str = "uji_nokey",
    install: bool = True,
) -> dict[str, Any]:
    """
    Generate an empirical calibration draft 'MPT_CALIBRATE' (C3).

    6 sequential text segments @ 3.0s each with sizes 5, 6, 7, 8, 9, 10.
    Each segment displays a 2-line test sentence (~80 chars) with label '[SIZE=N]'.
    Overlaid on top of 18 seconds of real shot footage from the project.
    """
    project_dir = get_project_dir(project_id)
    manifest = load_manifest(project_dir)

    if not manifest.chapters:
        raise ValueError(f"Project '{project_id}' has no chapters.")

    ch1 = manifest.chapters[0]
    shots = sorted(ch1.shots, key=lambda s: s.shot_index)
    if not shots:
        raise ValueError("Chapter 1 has no shots.")

    # Gather real video clips to cover >= 18.0s (6 segments * 3.0s)
    target_total_dur = 18.0
    accumulated_dur = 0.0
    selected_video_clips = []

    for s in shots:
        clip_rel = s.rendered_clip or s.asset_path
        clip_path = project_dir / clip_rel if clip_rel else None
        if not clip_path or not clip_path.is_file():
            alt = project_dir / "assets" / "visual" / "shots" / f"{s.shot_id}.mp4"
            if alt.is_file():
                clip_path = alt
            else:
                continue

        fps = float(getattr(manifest.encoder_settings, "fps", 30.0) or 30.0)
        dur = (s.target_frames / fps) if s.target_frames else float(s.duration_seconds or 5.0)

        selected_video_clips.append({"path": clip_path, "duration_seconds": dur})
        accumulated_dur += dur
        if accumulated_dur >= target_total_dur:
            break

    if not selected_video_clips:
        raise FileNotFoundError(f"No valid video shot clips found in project '{project_id}'.")

    while accumulated_dur < target_total_dur:
        last = selected_video_clips[-1]
        selected_video_clips.append(last)
        accumulated_dur += last["duration_seconds"]

    # 6 test segments @ 3.0s each with sizes 5, 6, 7, 8, 9, 10
    sizes = [5, 6, 7, 8, 9, 10]
    test_subtitles = []
    sentence_p1 = "The asphalt trembled under the immense vibration"
    sentence_p2 = "of Detroit steel as the engine roared to life."

    for idx, sz in enumerate(sizes):
        start_sec = idx * 3.0
        test_subtitles.append({
            "text": f"[SIZE={sz}] {sentence_p1}\n{sentence_p2}",
            "start_seconds": start_sec,
            "duration_seconds": 3.0,
            "size": sz,
        })

    # Narration: trim 18.0s from chapter 1 audio if available
    trimmed_audio_path = None
    temp_audio_dir = None
    if ch1.audio_file:
        ch1_audio = project_dir / ch1.audio_file
        if ch1_audio.is_file():
            temp_audio_dir = Path(tempfile.mkdtemp(prefix="mpt_calibrate_audio_"))
            trimmed_audio_path = temp_audio_dir / "calibrate_audio.wav"
            trim_cmd = [
                "ffmpeg", "-y",
                "-i", str(ch1_audio),
                "-t", f"{accumulated_dur:.3f}",
                "-c:a", "pcm_s16le",
                str(trimmed_audio_path),
            ]
            subprocess.run(trim_cmd, capture_output=True, check=False, timeout=15)

    draft_name = "MPT_CALIBRATE"
    export_draft_dir = project_dir / "export" / "capcut" / draft_name

    builder = CapCutDraftBuilder()
    builder.build_draft(
        output_dir=export_draft_dir,
        draft_name=draft_name,
        video_clips=selected_video_clips,
        narration_clips=[{
            "path": trimmed_audio_path,
            "duration_seconds": accumulated_dur,
            "start_offset_seconds": 0.0,
        }] if (trimmed_audio_path and trimmed_audio_path.is_file()) else [],
        subtitles=test_subtitles,
        portable=False,
    )

    installed_path = None
    if install:
        if is_capcut_running():
            raise RuntimeError("CapCut sedang berjalan. Tutup CapCut sebelum ekspor untuk mencegah korupsi draft.")

        draft_root = detect_draft_folder()
        if not draft_root or not draft_root.is_dir():
            raise RuntimeError("Could not locate CapCut draft directory (com.lveditor.draft).")

        target_install_dir = draft_root / draft_name
        builder.build_draft(
            output_dir=target_install_dir,
            draft_name=draft_name,
            video_clips=selected_video_clips,
            narration_clips=[{
                "path": trimmed_audio_path,
                "duration_seconds": accumulated_dur,
                "start_offset_seconds": 0.0,
            }] if (trimmed_audio_path and trimmed_audio_path.is_file()) else [],
            subtitles=test_subtitles,
            portable=False,
            draft_root_path=draft_root.resolve().as_posix(),
        )
        validate_built_draft(target_install_dir)
        installed_path = str(target_install_dir)
        logger.info(f"Calibration draft installed at {installed_path}")

    validate_built_draft(export_draft_dir)

    return {
        "draft_name": draft_name,
        "export_path": str(export_draft_dir),
        "installed": install,
        "installed_path": installed_path,
        "segments_count": len(test_subtitles),
        "sizes_tested": sizes,
        "total_duration_seconds": accumulated_dur,
    }
