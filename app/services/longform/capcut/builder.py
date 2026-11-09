"""CapCut Draft Builder module.

Constructs valid CapCut Desktop 8.9.0 drafts by adapting the verified template,
generating unique identifiers, calculating microseconds time offsets, copying media
into portable project-relative folders, and synchronizing metadata.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.longform.capcut.detector import detect_draft_folder, detect_capcut_version


class CapCutDraftBuilder:
    """Builder for CapCut Desktop 8.9.0 drafts."""

    def __init__(self, template_dir: Path | None = None) -> None:
        if template_dir is None:
            # Default to repo template copy
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
        audio_clip: dict[str, Any],
        subtitles: list[dict[str, Any]],
        draft_root_path: str | None = None,
        font_path: str | None = None,
    ) -> Path:
        """
        Build a complete, standalone CapCut draft folder.

        :param output_dir: Destination folder path for the draft.
        :param draft_name: Display name for the draft (e.g. MPT_SMOKE_uji_nokey).
        :param video_clips: List of dicts with 'path' (Path) and 'duration_seconds' (float).
        :param audio_clip: Dict with 'path' (Path) and 'duration_seconds' (float).
        :param subtitles: List of dicts with 'text' (str), 'start_seconds' (float), 'duration_seconds' (float).
        :param draft_root_path: Optional CapCut root directory (defaults to detected or parent).
        :param font_path: Optional font file path.
        :return: Path to created draft directory.
        """
        output_dir = Path(output_dir).resolve()
        media_dir = output_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        if draft_root_path is None:
            detected = detect_draft_folder()
            if detected:
                draft_root_path = detected.resolve().as_posix()
            else:
                draft_root_path = output_dir.parent.resolve().as_posix()

        draft_fold_path = output_dir.resolve().as_posix()

        # 1. Copy auxiliary template files and directories
        self._copy_template_scaffolding(output_dir)

        # 2. Copy and prepare media files
        prepared_videos = []
        for idx, vc in enumerate(video_clips):
            src_path = Path(vc["path"])
            dest_name = f"video_{idx + 1:03d}{src_path.suffix}"
            dest_path = media_dir / dest_name
            shutil.copy2(src_path, dest_path)
            dur_us = int(round(vc["duration_seconds"] * 1_000_000))
            prepared_videos.append({
                "path": dest_path.resolve().as_posix(),
                "name": dest_name,
                "duration_us": dur_us,
                "duration_seconds": vc["duration_seconds"],
            })

        # Audio file
        src_audio = Path(audio_clip["path"])
        dest_audio = media_dir / f"audio_track{src_audio.suffix}"
        shutil.copy2(src_audio, dest_audio)
        audio_dur_us = int(round(audio_clip["duration_seconds"] * 1_000_000))
        prepared_audio = {
            "path": dest_audio.resolve().as_posix(),
            "name": dest_audio.name,
            "duration_us": audio_dur_us,
            "duration_seconds": audio_clip["duration_seconds"],
        }

        # Generate cover image from first video clip using ffmpeg
        cover_path = output_dir / "draft_cover.jpg"
        if prepared_videos:
            self._extract_cover_image(Path(prepared_videos[0]["path"]), cover_path)

        # Total duration is determined by video clips sum
        total_duration_us = sum(v["duration_us"] for v in prepared_videos)

        # Font resolution
        if not font_path:
            # Check if CapCut system font exists
            ver_info = detect_capcut_version()
            if ver_info.get("installed") and ver_info.get("full_ver") and ver_info.get("apps_dir"):
                candidate = Path(ver_info["apps_dir"]) / ver_info["full_ver"] / "Resources" / "Font" / "SystemFont" / "en.ttf"
                if candidate.is_file():
                    font_path = candidate.resolve().as_posix()

        if not font_path:
            # Fallback to repo font
            repo_font = Path(__file__).resolve().parents[4] / "resource" / "fonts" / "BeVietnamPro-Bold.ttf"
            if repo_font.is_file():
                font_path = repo_font.resolve().as_posix()
            else:
                font_path = ""

        # Generate unique IDs
        draft_id = str(uuid.uuid4()).upper()
        timeline_id = str(uuid.uuid4()).upper()

        now_us = int(time.time() * 1_000_000)

        # 3. Build draft_content.json
        draft_content, draft_meta_materials = self._build_draft_content(
            timeline_id=timeline_id,
            draft_name=draft_name,
            total_duration_us=total_duration_us,
            prepared_videos=prepared_videos,
            prepared_audio=prepared_audio,
            subtitles=subtitles,
            font_path=font_path or "",
            now_us=now_us,
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

        # 5. Build draft_meta_info.json
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

        # Copy common_attachment directory
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
        prepared_audio: dict[str, Any],
        subtitles: list[dict[str, Any]],
        font_path: str,
        now_us: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Construct the draft_content.json data structure."""
        # Load base template draft_content
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

        # Clear existing tracks and materials
        content["tracks"] = []
        materials = content.get("materials", {})
        for k in list(materials.keys()):
            if isinstance(materials[k], list):
                materials[k] = []

        draft_meta_materials: list[dict[str, Any]] = []

        # --- TRACK 0: VIDEO ---
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

            speed_id = str(uuid.uuid4()).upper()
            placeholder_id = str(uuid.uuid4()).upper()
            canvas_id = str(uuid.uuid4()).upper()
            channel_id = str(uuid.uuid4()).upper()
            color_id = str(uuid.uuid4()).upper()
            vocal_id = str(uuid.uuid4()).upper()

            # Material video object
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

            # Extra materials
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

            # Video segment
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

            # Add to meta info materials
            draft_meta_materials.append({
                "ai_group_type": "",
                "create_time": now_us,
                "duration": vid["duration_us"],
                "enter_from": "",
                "extra_info": "",
                "file_Path": vid["path"],
                "height": 1080,
                "id": local_mat_id,
                "import_time": int(time.time()),
                "import_time_ms": int(time.time() * 1000),
                "item_source": 1,
                "md5": "",
                "metetype": "video",
                "roughcut_time_range": {"duration": 0, "start": 0},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": 1920,
            })

        content["tracks"].append(video_track)

        # --- TRACK 1: TEXT (SUBTITLES) ---
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
                sub_text = sub["text"]
                start_us = int(round(sub["start_seconds"] * 1_000_000))
                dur_us = int(round(sub["duration_seconds"] * 1_000_000))

                text_content_payload = {
                    "text": sub_text,
                    "styles": [
                        {
                            "fill": {
                                "content": {
                                    "render_type": "solid",
                                    "solid": {"color": [1, 1, 1]},
                                }
                            },
                            "font": {"path": font_path, "id": ""},
                            "size": 15,
                            "range": [0, len(sub_text)],
                        }
                    ],
                }

                text_mat = {
                    "id": text_id,
                    "type": "text",
                    "content": json.dumps(text_content_payload, ensure_ascii=False),
                    "font_size": 15.0,
                    "alignment": 1,
                    "font_path": font_path,
                    "text_color": "#FFFFFF",
                    "text_alpha": 1.0,
                    "border_alpha": 1.0,
                    "border_color": "#000000",
                    "border_width": 0.08,
                    "has_shadow": False,
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
                    "render_index": 1,
                    "visible": True,
                }
                text_track["segments"].append(t_seg)

            content["tracks"].append(text_track)

        # --- TRACK 2: AUDIO ---
        audio_track_id = str(uuid.uuid4()).upper()
        audio_track = {
            "attribute": 0,
            "flag": 0,
            "id": audio_track_id,
            "is_default_name": True,
            "name": "",
            "segments": [],
            "type": "audio",
        }

        aud_mat_id = str(uuid.uuid4()).upper()
        aud_seg_id = str(uuid.uuid4()).upper()
        aud_local_id = str(uuid.uuid4()).lower()

        aud_speed_id = str(uuid.uuid4()).upper()
        aud_placeholder_id = str(uuid.uuid4()).upper()
        aud_channel_id = str(uuid.uuid4()).upper()
        aud_vocal_id = str(uuid.uuid4()).upper()
        aud_beats_id = str(uuid.uuid4()).upper()

        audio_mat = {
            "id": aud_mat_id,
            "type": "extract_music",
            "name": prepared_audio["name"],
            "duration": prepared_audio["duration_us"],
            "path": prepared_audio["path"],
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
                "start": 0,
                "duration": prepared_audio["duration_us"],
            },
            "source_timerange": {
                "start": 0,
                "duration": prepared_audio["duration_us"],
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
        audio_track["segments"].append(aud_seg)
        content["tracks"].append(audio_track)

        draft_meta_materials.append({
            "ai_group_type": "",
            "create_time": now_us,
            "duration": prepared_audio["duration_us"],
            "enter_from": "",
            "extra_info": "",
            "file_Path": prepared_audio["path"],
            "height": 0,
            "id": aud_local_id,
            "import_time": int(time.time()),
            "import_time_ms": int(time.time() * 1000),
            "item_source": 1,
            "md5": "",
            "metetype": "music",
            "roughcut_time_range": {"duration": 0, "start": 0},
            "sub_time_range": {"duration": -1, "start": -1},
            "type": 0,
            "width": 0,
        })

        content["materials"] = materials
        return content, draft_meta_materials

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

        # Materials inventory in index 0
        if "draft_materials" in meta and len(meta["draft_materials"]) > 0:
            meta["draft_materials"][0]["value"] = draft_materials
        else:
            meta["draft_materials"] = [{"type": 0, "value": draft_materials}]

        (output_dir / "draft_meta_info.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
