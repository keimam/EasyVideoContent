"""CapCut Draft Builder module.

Constructs valid CapCut Desktop 8.9.0 drafts by adapting the verified template,
generating unique identifiers, calculating microseconds time offsets, managing media
references (absolute project paths or portable local copies), synchronizing the Media
panel (draft_virtual_store.json & draft_meta_info.json), and formatting subtitles.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.longform.capcut.detector import detect_capcut_version, detect_draft_folder


def format_capcut_subtitle(raw_text: str, max_chars_per_line: int = 42) -> tuple[str, int]:
    """
    Format subtitle text for CapCut Desktop (C1):
    - Strips all existing newlines and collapses whitespace.
    - If length <= max_chars_per_line: returns 1 line.
    - If length > max_chars_per_line: cleanly wraps into at most 2 lines
      at a natural word boundary near the middle of the text.
    - Returns (formatted_text, line_count). Never returns > 2 lines.
    """
    clean = " ".join(raw_text.strip().replace("\n", " ").split())
    if len(clean) <= max_chars_per_line:
        return clean, 1

    words = clean.split()
    if len(words) <= 1:
        return clean, 1

    mid = len(clean) / 2.0
    best_idx = 1
    best_diff = float("inf")
    curr_len = 0
    for i in range(len(words) - 1):
        curr_len += len(words[i]) + (1 if i > 0 else 0)
        diff = abs(curr_len - mid)
        if diff < best_diff:
            best_diff = diff
            best_idx = i + 1

    line1 = " ".join(words[:best_idx])
    line2 = " ".join(words[best_idx:])
    return f"{line1}\n{line2}", 2


def calculate_bottom_anchored_y(line_count: int, font_size: float) -> float:
    """
    Calculate bottom-anchored vertical center position (C2):
    - Target margin bottom: ~7.0% from bottom of screen (setara ASS MarginV 80).
    - In normalized coordinates [-1.0, 1.0], bottom canvas edge is -1.0.
    - Bottom of text block is anchored at y_bottom = -0.86 (14% of canvas height = 75.6px margin).
    - transform.y is the center of the text block:
        y_center = y_bottom + (total_height / 2)
      where total_height = line_count * line_height.
    - At 1080p, 1 line height in normalized units is ~ (font_size * 9.0) / 540.
    - Result:
        y_center(1) ~= -0.80
        y_center(2) ~= -0.74 (expands upward, bottom stays anchored at -0.86).
    """
    line_h_norm = (font_size * 9.0) / 540.0
    y_center = -0.86 + ((line_count * line_h_norm) / 2.0)
    return round(y_center, 4)


class CapCutDraftBuilder:
    """Builder for CapCut Desktop 8.9.0 drafts."""

    def __init__(self, template_dir: Path | None = None) -> None:
        if template_dir is None:
            repo_root = Path(__file__).resolve().parents[4]
            self.template_dir = repo_root / "docs" / "longform" / "capcut_template_8.9.0"
        else:
            self.template_dir = Path(template_dir)

        if not self.template_dir.is_dir():
            raise FileNotFoundError(f"CapCut template directory not found: {self.template_dir}")

    def build_draft(
        self,
        output_dir: Path,
        draft_name: str,
        video_clips: list[dict[str, Any]],
        narration_clips: list[dict[str, Any]] | None = None,
        bgm_clip: dict[str, Any] | None = None,
        subtitles: list[dict[str, Any]] | None = None,
        draft_root_path: str | None = None,
        portable: bool = False,
        font_path: str | None = None,
        audio_clip: dict[str, Any] | None = None,
        default_text_size: int = 7,
    ) -> Path:
        """
        Build a complete, standalone CapCut draft folder.

        :param output_dir: Destination folder path for the draft.
        :param draft_name: Display name for the draft (e.g. MPT_uji_nokey_20261109-1440).
        :param video_clips: List of dicts with 'path' (Path) and 'duration_seconds' (float).
        :param narration_clips: List of dicts with 'path', 'duration_seconds', and 'start_offset_seconds'.
        :param bgm_clip: Optional dict with 'path' and 'duration_seconds' for ducked BGM stem.
        :param subtitles: List of dicts with 'text' (str), 'start_seconds' (float), 'duration_seconds' (float).
        :param draft_root_path: Optional CapCut root directory (defaults to detected or parent).
        :param portable: If True, copies media into <draft>/media/; if False, references absolute paths.
        :param font_path: Optional font file path.
        :return: Path to created draft directory.
        """
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        media_dest_dir = output_dir / "media"
        if portable:
            media_dest_dir.mkdir(parents=True, exist_ok=True)

        if draft_root_path is None:
            detected = detect_draft_folder()
            if detected:
                draft_root_path = detected.resolve().as_posix()
            else:
                draft_root_path = output_dir.parent.resolve().as_posix()

        draft_fold_path = output_dir.resolve().as_posix()

        # 1. Copy auxiliary template files and directories
        self._copy_template_scaffolding(output_dir)

        # 2. Process video clips
        prepared_videos = []
        for idx, vc in enumerate(video_clips):
            src_path = Path(vc["path"]).resolve()
            if not src_path.is_file():
                raise FileNotFoundError(f"Video clip not found: {src_path}")

            if portable:
                dest_name = f"video_{idx + 1:03d}{src_path.suffix}"
                dest_path = media_dest_dir / dest_name
                shutil.copy2(src_path, dest_path)
                final_path = dest_path.resolve().as_posix()
                file_name = dest_name
            else:
                final_path = src_path.as_posix()
                file_name = src_path.name

            dur_us = int(round(vc["duration_seconds"] * 1_000_000))
            prepared_videos.append({
                "path": final_path,
                "name": file_name,
                "duration_us": dur_us,
                "duration_seconds": vc["duration_seconds"],
            })

        # Process narration clips
        if audio_clip and not narration_clips:
            narration_clips = [{
                "path": audio_clip["path"],
                "duration_seconds": audio_clip["duration_seconds"],
                "start_offset_seconds": 0.0,
            }]
        elif narration_clips is None:
            narration_clips = []

        prepared_narrations = []
        for idx, nc in enumerate(narration_clips):
            src_path = Path(nc["path"]).resolve()
            if not src_path.is_file():
                raise FileNotFoundError(f"Narration file not found: {src_path}")

            if portable:
                if audio_clip and len(narration_clips) == 1:
                    dest_name = f"audio_track{src_path.suffix}"
                else:
                    dest_name = f"narration_{idx + 1:02d}{src_path.suffix}"
                dest_path = media_dest_dir / dest_name
                shutil.copy2(src_path, dest_path)
                final_path = dest_path.resolve().as_posix()
                file_name = dest_name
            else:
                final_path = src_path.as_posix()
                file_name = src_path.name

            dur_us = int(round(nc["duration_seconds"] * 1_000_000))
            offset_us = int(round(nc.get("start_offset_seconds", 0.0) * 1_000_000))
            prepared_narrations.append({
                "path": final_path,
                "name": file_name,
                "duration_us": dur_us,
                "start_offset_us": offset_us,
                "duration_seconds": nc["duration_seconds"],
            })

        # Process BGM clip (if provided)
        prepared_bgm = None
        if bgm_clip and bgm_clip.get("path"):
            src_bgm = Path(bgm_clip["path"]).resolve()
            if src_bgm.is_file():
                if portable:
                    dest_bgm = media_dest_dir / f"bgm_ducked{src_bgm.suffix}"
                    shutil.copy2(src_bgm, dest_bgm)
                    final_bgm_path = dest_bgm.resolve().as_posix()
                    file_name = dest_bgm.name
                else:
                    final_bgm_path = src_bgm.as_posix()
                    file_name = src_bgm.name

                bgm_dur_us = int(round(bgm_clip["duration_seconds"] * 1_000_000))
                prepared_bgm = {
                    "path": final_bgm_path,
                    "name": file_name,
                    "duration_us": bgm_dur_us,
                    "duration_seconds": bgm_clip["duration_seconds"],
                }

        # Generate cover image from first video clip using ffmpeg
        cover_path = output_dir / "draft_cover.jpg"
        if prepared_videos:
            self._extract_cover_image(Path(prepared_videos[0]["path"]), cover_path)

        # Total duration is determined by video clips sum
        total_duration_us = sum(v["duration_us"] for v in prepared_videos)

        # Resolve font path
        if not font_path:
            ver_info = detect_capcut_version()
            if ver_info.get("installed") and ver_info.get("full_ver") and ver_info.get("apps_dir"):
                candidate = Path(ver_info["apps_dir"]) / ver_info["full_ver"] / "Resources" / "Font" / "SystemFont" / "en.ttf"
                if candidate.is_file():
                    font_path = candidate.resolve().as_posix()

        if not font_path:
            repo_font = Path(__file__).resolve().parents[4] / "resource" / "fonts" / "BeVietnamPro-Bold.ttf"
            if repo_font.is_file():
                font_path = repo_font.resolve().as_posix()
            else:
                font_path = ""

        # Generate unique IDs
        draft_id = str(uuid.uuid4()).upper()
        timeline_id = str(uuid.uuid4()).upper()
        now_us = int(time.time() * 1_000_000)

        # 3. Build draft_content.json and gather materials for meta info & virtual store
        draft_content, draft_meta_materials, media_child_ids = self._build_draft_content(
            timeline_id=timeline_id,
            draft_name=draft_name,
            total_duration_us=total_duration_us,
            prepared_videos=prepared_videos,
            prepared_narrations=prepared_narrations,
            prepared_bgm=prepared_bgm,
            subtitles=subtitles or [],
            font_path=font_path or "",
            now_us=now_us,
            default_text_size=default_text_size,
        )

        content_str = json.dumps(draft_content, indent=2, ensure_ascii=False)
        (output_dir / "draft_content.json").write_text(content_str, encoding="utf-8")
        (output_dir / "draft_content.json.bak").write_text(content_str, encoding="utf-8")

        # 4. Build Timelines/project.json and Timelines/<timeline_id>/
        timelines_dir = output_dir / "Timelines"
        timelines_dir.mkdir(parents=True, exist_ok=True)

        project_json = {
            "config": {
                "color_space": -1,
                "render_index_track_mode_on": False,
                "use_float_render": False,
            },
            "create_time": now_us,
            "id": str(uuid.uuid4()).upper(),
            "main_timeline_id": timeline_id,
            "timelines": [
                {
                    "create_time": now_us,
                    "id": timeline_id,
                    "is_marked_delete": False,
                    "name": "Timeline 01",
                    "update_time": now_us,
                }
            ],
            "update_time": now_us,
            "version": 0,
        }
        (timelines_dir / "project.json").write_text(json.dumps(project_json, indent=2), encoding="utf-8")
        (timelines_dir / "project.json.bak").write_text(json.dumps(project_json, indent=2), encoding="utf-8")

        active_tl_dir = timelines_dir / timeline_id
        active_tl_dir.mkdir(parents=True, exist_ok=True)
        (active_tl_dir / "draft_content.json").write_text(content_str, encoding="utf-8")
        (active_tl_dir / "draft_content.json.bak").write_text(content_str, encoding="utf-8")
        if cover_path.is_file():
            shutil.copy2(cover_path, active_tl_dir / "draft_cover.jpg")

        # Copy common attachments inside active timeline directory
        for att_file in ["attachment_editing.json", "attachment_pc_common.json"]:
            if (output_dir / att_file).is_file():
                shutil.copy2(output_dir / att_file, active_tl_dir / att_file)

        if (output_dir / "common_attachment").is_dir():
            dest_common = active_tl_dir / "common_attachment"
            if dest_common.exists():
                shutil.rmtree(dest_common)
            shutil.copytree(output_dir / "common_attachment", dest_common)

        # 5. Build draft_meta_info.json (with extra_info and roughcut_time_range)
        self._build_draft_meta_info(
            output_dir=output_dir,
            draft_id=draft_id,
            draft_name=draft_name,
            draft_fold_path=draft_fold_path,
            draft_root_path=draft_root_path,
            total_duration_us=total_duration_us,
            draft_materials=draft_meta_materials,
            now_us=now_us,
        )

        # 6. Build draft_virtual_store.json (A2 fix: Media panel bin registration)
        self._build_draft_virtual_store(
            output_dir=output_dir,
            media_child_ids=media_child_ids,
        )

        logger.info(f"CapCut draft '{draft_name}' successfully built at {output_dir}")
        return output_dir

    def _copy_template_scaffolding(self, output_dir: Path) -> None:
        """Copy all non-timeline template files to the draft directory."""
        files_to_copy = [
            "draft_agency_config.json",
            "draft_biz_config.json",
            "draft_settings",
            "draft_virtual_store.json",
            "key_value.json",
            "performance_opt_info.json",
            "timeline_layout.json",
            "attachment_editing.json",
            "attachment_pc_common.json",
        ]
        for fn in files_to_copy:
            src = self.template_dir / fn
            if src.is_file():
                shutil.copy2(src, output_dir / fn)
            elif not (output_dir / fn).exists():
                (output_dir / fn).touch()

        src_common = self.template_dir / "common_attachment"
        if src_common.is_dir():
            dest_common = output_dir / "common_attachment"
            if dest_common.exists():
                shutil.rmtree(dest_common)
            shutil.copytree(src_common, dest_common)

    def _extract_cover_image(self, video_file: Path, output_cover: Path) -> None:
        """Extract a single frame from video for draft cover thumbnail."""
        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                "0.5",
                "-i",
                str(video_file),
                "-vframes",
                "1",
                "-q:v",
                "2",
                str(output_cover),
            ]
            subprocess.run(cmd, capture_output=True, check=False, timeout=10)
        except Exception as exc:
            logger.warning(f"Could not generate cover image: {exc}")

    def _build_draft_content(
        self,
        timeline_id: str,
        draft_name: str,
        total_duration_us: int,
        prepared_videos: list[dict[str, Any]],
        prepared_narrations: list[dict[str, Any]],
        prepared_bgm: dict[str, Any] | None,
        subtitles: list[dict[str, Any]],
        font_path: str,
        now_us: int,
        default_text_size: int = 7,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        """Construct the draft_content.json data structure."""
        template_content_path = self.template_dir / "draft_content.json"
        with open(template_content_path, "r", encoding="utf-8") as f:
            content = json.load(f)

        content["id"] = timeline_id
        content["name"] = draft_name
        content["duration"] = total_duration_us
        content["fps"] = 30.0
        content["create_time"] = now_us
        content["update_time"] = now_us
        content["canvas_config"] = {
            "background": None,
            "height": 1080,
            "ratio": "original",
            "width": 1920,
        }

        content["tracks"] = []
        materials = content.get("materials", {})
        for k in list(materials.keys()):
            if isinstance(materials[k], list):
                materials[k] = []

        draft_meta_materials: list[dict[str, Any]] = []
        media_child_ids: list[str] = []

        # --- TRACK 0: VIDEO (all clips sequentially) ---
        video_track_id = str(uuid.uuid4()).upper()
        video_track = {
            "attribute": 0,
            "flag": 0,
            "id": video_track_id,
            "is_default_name": True,
            "name": "",
            "segments": [],
            "type": "video",
        }

        current_v_offset = 0
        for vid in prepared_videos:
            mat_id = str(uuid.uuid4()).upper()
            seg_id = str(uuid.uuid4()).upper()
            local_mat_id = str(uuid.uuid4()).lower()
            media_child_ids.append(local_mat_id)

            speed_id = str(uuid.uuid4()).upper()
            placeholder_id = str(uuid.uuid4()).upper()
            canvas_id = str(uuid.uuid4()).upper()
            channel_id = str(uuid.uuid4()).upper()
            color_id = str(uuid.uuid4()).upper()
            vocal_id = str(uuid.uuid4()).upper()

            video_mat = {
                "id": mat_id,
                "type": "video",
                "duration": vid["duration_us"],
                "path": vid["path"],
                "width": 1920,
                "height": 1080,
                "category_name": "local",
                "material_name": vid["name"],
                "local_material_id": local_mat_id,
                "has_audio": True,
                "crop": {
                    "upper_left_x": 0.0,
                    "upper_left_y": 0.0,
                    "upper_right_x": 1.0,
                    "upper_right_y": 0.0,
                    "lower_left_x": 0.0,
                    "lower_left_y": 1.0,
                    "lower_right_x": 1.0,
                    "lower_right_y": 1.0,
                },
                "crop_ratio": "free",
                "crop_scale": 1.0,
                "extra_type_option": 0,
            }
            materials["videos"].append(video_mat)

            materials["speeds"].append({
                "curve_speed": None,
                "id": speed_id,
                "mode": 0,
                "speed": 1.0,
                "type": "speed",
            })
            materials["placeholder_infos"].append({
                "error_path": "",
                "error_text": "",
                "id": placeholder_id,
                "meta_type": 0,
                "res_path": "",
                "res_text": "",
                "type": "placeholder_info",
            })
            materials["canvases"].append({
                "album_image": "",
                "blur": 0.0,
                "color": "",
                "id": canvas_id,
                "image": "",
                "image_id": "",
                "image_name": "",
                "source_platform": 0,
                "team_id": "",
                "type": "canvas_color",
            })
            materials["sound_channel_mappings"].append({
                "audio_channel_mapping": 0,
                "id": channel_id,
                "is_config_open": False,
                "type": "none",
            })
            materials["material_colors"].append({
                "gradient_angle": 90.0,
                "gradient_colors": [],
                "gradient_percents": [],
                "height": 0.0,
                "id": color_id,
                "is_color_clip": False,
                "is_gradient": False,
                "solid_color": "",
                "type": "",
                "width": 0.0,
            })
            materials["vocal_separations"].append({
                "choice": 0,
                "enter_from": "",
                "final_algorithm": "",
                "id": vocal_id,
                "production_path": "",
                "removed_sounds": [],
                "time_range": None,
                "type": "vocal_separation",
            })

            v_seg = {
                "id": seg_id,
                "material_id": mat_id,
                "target_timerange": {
                    "start": current_v_offset,
                    "duration": vid["duration_us"],
                },
                "source_timerange": {
                    "start": 0,
                    "duration": vid["duration_us"],
                },
                "extra_material_refs": [
                    speed_id,
                    placeholder_id,
                    canvas_id,
                    channel_id,
                    color_id,
                    vocal_id,
                ],
                "speed": 1.0,
                "volume": 1.0,
                "render_index": 0,
                "visible": True,
            }
            video_track["segments"].append(v_seg)
            current_v_offset += vid["duration_us"]

            # Add to meta info materials (A2 fix: enter_from=0, extra_info=name, roughcut_time_range)
            draft_meta_materials.append({
                "ai_group_type": "",
                "create_time": int(time.time()),
                "duration": vid["duration_us"],
                "enter_from": 0,
                "extra_info": vid["name"],
                "file_Path": vid["path"],
                "height": 1080,
                "id": local_mat_id,
                "import_time": int(time.time()),
                "import_time_ms": int(time.time() * 1000),
                "item_source": 1,
                "md5": "",
                "metetype": "video",
                "roughcut_time_range": {"duration": vid["duration_us"], "start": 0},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": 1920,
            })

        content["tracks"].append(video_track)

        # --- TRACK 1: TEXT (SUBTITLES with calibrated size & bottom-anchored positioning C1, C2) ---
        if subtitles:
            text_track_id = str(uuid.uuid4()).upper()
            text_track = {
                "attribute": 0,
                "flag": 0,
                "id": text_track_id,
                "is_default_name": True,
                "name": "",
                "segments": [],
                "type": "text",
            }

            for sub in subtitles:
                text_id = str(uuid.uuid4()).upper()
                seg_id = str(uuid.uuid4()).upper()
                raw_text = sub["text"].strip()
                eff_size = int(sub.get("size") or default_text_size or 7)

                # C1: Single wrap mechanism (max 2 lines, word-balanced, no double wrap)
                wrapped_text, line_count = format_capcut_subtitle(raw_text, max_chars_per_line=42)

                # C2: Bottom-anchored vertical center position
                y_pos = calculate_bottom_anchored_y(line_count, eff_size)

                start_us = int(round(sub["start_seconds"] * 1_000_000))
                dur_us = int(round(sub["duration_seconds"] * 1_000_000))

                text_content_payload = {
                    "text": wrapped_text,
                    "styles": [
                        {
                            "fill": {
                                "content": {
                                    "render_type": "solid",
                                    "solid": {"color": [1.0, 1.0, 1.0]},
                                }
                            },
                            "font": {"path": font_path, "id": ""},
                            "size": eff_size,
                            "range": [0, len(wrapped_text)],
                        }
                    ],
                }

                text_mat = {
                    "id": text_id,
                    "type": "text",
                    "content": json.dumps(text_content_payload, ensure_ascii=False),
                    "font_size": float(eff_size),
                    "text_size": int(eff_size * 2),
                    "alignment": 1,
                    "font_path": font_path,
                    "text_color": "#FFFFFF",
                    "text_alpha": 1.0,
                    "border_alpha": 1.0,
                    "border_color": "#000000",
                    "border_width": 0.15,
                    "border_mode": 0,
                    "bold_width": 0.20,
                    "has_shadow": True,
                    "shadow_color": "#000000",
                    "shadow_alpha": 0.85,
                    "shadow_smoothing": 0.45,
                    "shadow_distance": 5.0,
                    "shadow_angle": -45.0,
                    "force_apply_line_max_width": False,
                    "line_feed": 1,
                    "line_max_width": 0.82,
                }
                materials["texts"].append(text_mat)

                t_seg = {
                    "id": seg_id,
                    "material_id": text_id,
                    "target_timerange": {
                        "start": start_us,
                        "duration": dur_us,
                    },
                    "source_timerange": None,
                    "extra_material_refs": [],
                    "speed": 1.0,
                    "volume": 1.0,
                    "render_index": 14000,
                    "visible": True,
                    "clip": {
                        "alpha": 1.0,
                        "flip": {"horizontal": False, "vertical": False},
                        "rotation": 0.0,
                        "scale": {"x": 1.0, "y": 1.0},
                        "transform": {"x": 0.0, "y": y_pos},
                    },
                    "uniform_scale": {
                        "on": True,
                        "value": 1.0,
                    },
                }
                text_track["segments"].append(t_seg)

            content["tracks"].append(text_track)

        # --- TRACK 2: NARRATION (independent WAV per chapter) ---
        if prepared_narrations:
            narr_track_id = str(uuid.uuid4()).upper()
            narr_track = {
                "attribute": 0,
                "flag": 0,
                "id": narr_track_id,
                "is_default_name": True,
                "name": "",
                "segments": [],
                "type": "audio",
            }

            for narr in prepared_narrations:
                aud_mat_id = str(uuid.uuid4()).upper()
                aud_seg_id = str(uuid.uuid4()).upper()
                aud_local_id = str(uuid.uuid4()).lower()
                media_child_ids.append(aud_local_id)

                aud_speed_id = str(uuid.uuid4()).upper()
                aud_placeholder_id = str(uuid.uuid4()).upper()
                aud_channel_id = str(uuid.uuid4()).upper()
                aud_vocal_id = str(uuid.uuid4()).upper()
                aud_beats_id = str(uuid.uuid4()).upper()

                audio_mat = {
                    "id": aud_mat_id,
                    "type": "extract_music",
                    "name": narr["name"],
                    "duration": narr["duration_us"],
                    "path": narr["path"],
                    "category_name": "local",
                    "local_material_id": aud_local_id,
                    "check_flag": 1,
                }
                materials["audios"].append(audio_mat)

                materials["speeds"].append({
                    "curve_speed": None,
                    "id": aud_speed_id,
                    "mode": 0,
                    "speed": 1.0,
                    "type": "speed",
                })
                materials["placeholder_infos"].append({
                    "error_path": "",
                    "error_text": "",
                    "id": aud_placeholder_id,
                    "meta_type": 0,
                    "res_path": "",
                    "res_text": "",
                    "type": "placeholder_info",
                })
                materials["beats"].append({
                    "ai_beats": {
                        "beat_speed_infos": [],
                        "beats_path": "",
                        "beats_url": "",
                        "melody_path": "",
                        "melody_percents": [0.0],
                        "melody_url": "",
                    },
                    "enable_ai_beats": False,
                    "gear": 404,
                    "id": aud_beats_id,
                    "mode": 404,
                    "type": "beats",
                    "user_beats": [],
                    "user_delete_ai_beats": None,
                })
                materials["sound_channel_mappings"].append({
                    "audio_channel_mapping": 0,
                    "id": aud_channel_id,
                    "is_config_open": False,
                    "type": "none",
                })
                materials["vocal_separations"].append({
                    "choice": 0,
                    "enter_from": "",
                    "final_algorithm": "",
                    "id": aud_vocal_id,
                    "production_path": "",
                    "removed_sounds": [],
                    "time_range": None,
                    "type": "vocal_separation",
                })

                aud_seg = {
                    "id": aud_seg_id,
                    "material_id": aud_mat_id,
                    "target_timerange": {
                        "start": narr["start_offset_us"],
                        "duration": narr["duration_us"],
                    },
                    "source_timerange": {
                        "start": 0,
                        "duration": narr["duration_us"],
                    },
                    "extra_material_refs": [
                        aud_speed_id,
                        aud_placeholder_id,
                        aud_beats_id,
                        aud_channel_id,
                        aud_vocal_id,
                    ],
                    "speed": 1.0,
                    "volume": 1.0,
                    "render_index": 2,
                    "visible": True,
                }
                narr_track["segments"].append(aud_seg)

                draft_meta_materials.append({
                    "ai_group_type": "",
                    "create_time": int(time.time()),
                    "duration": narr["duration_us"],
                    "enter_from": 0,
                    "extra_info": narr["name"],
                    "file_Path": narr["path"],
                    "height": 0,
                    "id": aud_local_id,
                    "import_time": int(time.time()),
                    "import_time_ms": int(time.time() * 1000),
                    "item_source": 1,
                    "md5": "",
                    "metetype": "music",
                    "roughcut_time_range": {"duration": narr["duration_us"], "start": 0},
                    "sub_time_range": {"duration": -1, "start": -1},
                    "type": 0,
                    "width": 0,
                })

            content["tracks"].append(narr_track)

        # --- TRACK 3: BGM (ducked stem) ---
        if prepared_bgm:
            bgm_track_id = str(uuid.uuid4()).upper()
            bgm_track = {
                "attribute": 0,
                "flag": 0,
                "id": bgm_track_id,
                "is_default_name": True,
                "name": "",
                "segments": [],
                "type": "audio",
            }

            bgm_mat_id = str(uuid.uuid4()).upper()
            bgm_seg_id = str(uuid.uuid4()).upper()
            bgm_local_id = str(uuid.uuid4()).lower()
            media_child_ids.append(bgm_local_id)

            bgm_speed_id = str(uuid.uuid4()).upper()
            bgm_placeholder_id = str(uuid.uuid4()).upper()
            bgm_channel_id = str(uuid.uuid4()).upper()
            bgm_vocal_id = str(uuid.uuid4()).upper()
            bgm_beats_id = str(uuid.uuid4()).upper()

            bgm_mat = {
                "id": bgm_mat_id,
                "type": "extract_music",
                "name": prepared_bgm["name"],
                "duration": prepared_bgm["duration_us"],
                "path": prepared_bgm["path"],
                "category_name": "local",
                "local_material_id": bgm_local_id,
                "check_flag": 1,
            }
            materials["audios"].append(bgm_mat)

            materials["speeds"].append({
                "curve_speed": None,
                "id": bgm_speed_id,
                "mode": 0,
                "speed": 1.0,
                "type": "speed",
            })
            materials["placeholder_infos"].append({
                "error_path": "",
                "error_text": "",
                "id": bgm_placeholder_id,
                "meta_type": 0,
                "res_path": "",
                "res_text": "",
                "type": "placeholder_info",
            })
            materials["beats"].append({
                "ai_beats": {
                    "beat_speed_infos": [],
                    "beats_path": "",
                    "beats_url": "",
                    "melody_path": "",
                    "melody_percents": [0.0],
                    "melody_url": "",
                },
                "enable_ai_beats": False,
                "gear": 404,
                "id": bgm_beats_id,
                "mode": 404,
                "type": "beats",
                "user_beats": [],
                "user_delete_ai_beats": None,
            })
            materials["sound_channel_mappings"].append({
                "audio_channel_mapping": 0,
                "id": bgm_channel_id,
                "is_config_open": False,
                "type": "none",
            })
            materials["vocal_separations"].append({
                "choice": 0,
                "enter_from": "",
                "final_algorithm": "",
                "id": bgm_vocal_id,
                "production_path": "",
                "removed_sounds": [],
                "time_range": None,
                "type": "vocal_separation",
            })

            bgm_seg = {
                "id": bgm_seg_id,
                "material_id": bgm_mat_id,
                "target_timerange": {
                    "start": 0,
                    "duration": total_duration_us,
                },
                "source_timerange": {
                    "start": 0,
                    "duration": total_duration_us,
                },
                "extra_material_refs": [
                    bgm_speed_id,
                    bgm_placeholder_id,
                    bgm_beats_id,
                    bgm_channel_id,
                    bgm_vocal_id,
                ],
                "speed": 1.0,
                "volume": 1.0,
                "render_index": 3,
                "visible": True,
            }
            bgm_track["segments"].append(bgm_seg)
            content["tracks"].append(bgm_track)

            draft_meta_materials.append({
                "ai_group_type": "",
                "create_time": int(time.time()),
                "duration": prepared_bgm["duration_us"],
                "enter_from": 0,
                "extra_info": prepared_bgm["name"],
                "file_Path": prepared_bgm["path"],
                "height": 0,
                "id": bgm_local_id,
                "import_time": int(time.time()),
                "import_time_ms": int(time.time() * 1000),
                "item_source": 1,
                "md5": "",
                "metetype": "music",
                "roughcut_time_range": {"duration": prepared_bgm["duration_us"], "start": 0},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": 0,
            })

        content["materials"] = materials
        return content, draft_meta_materials, media_child_ids

    def _build_draft_meta_info(
        self,
        output_dir: Path,
        draft_id: str,
        draft_name: str,
        draft_fold_path: str,
        draft_root_path: str,
        total_duration_us: int,
        draft_materials: list[dict[str, Any]],
        now_us: int,
    ) -> None:
        """Construct and write draft_meta_info.json."""
        meta_template_path = self.template_dir / "draft_meta_info.json"
        with open(meta_template_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        meta["draft_id"] = draft_id
        meta["draft_name"] = draft_name
        meta["draft_fold_path"] = draft_fold_path
        meta["draft_root_path"] = draft_root_path
        meta["tm_duration"] = total_duration_us
        meta["tm_draft_create"] = now_us
        meta["tm_draft_modified"] = now_us

        if "draft_materials" in meta and len(meta["draft_materials"]) > 0:
            meta["draft_materials"][0]["value"] = draft_materials
        else:
            meta["draft_materials"] = [{"type": 0, "value": draft_materials}]

        (output_dir / "draft_meta_info.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _build_draft_virtual_store(
        self,
        output_dir: Path,
        media_child_ids: list[str],
    ) -> None:
        """Register all imported media items in draft_virtual_store.json (A2 fix)."""
        store_template_path = self.template_dir / "draft_virtual_store.json"
        with open(store_template_path, "r", encoding="utf-8") as f:
            store = json.load(f)

        # type 1 in draft_virtual_store holds the list of media child_ids
        media_entries = [
            {"child_id": cid, "parent_id": ""}
            for cid in media_child_ids
        ]

        if "draft_virtual_store" in store and len(store["draft_virtual_store"]) > 1:
            store["draft_virtual_store"][1]["value"] = media_entries
        else:
            store["draft_virtual_store"] = [
                {"type": 0, "value": []},
                {"type": 1, "value": media_entries},
                {"type": 2, "value": []},
            ]

        (output_dir / "draft_virtual_store.json").write_text(
            json.dumps(store, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
