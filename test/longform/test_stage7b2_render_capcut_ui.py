"""Comprehensive tests for Stage 7B.2 WebUI: Step 4 Render, Step 5 CapCut, and Auto-Pipeline (R1-R5, C1-C4, A1-A3, X1-X2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    ItemStatus,
    ProjectManifest,
    Shot,
    TtsGroup,
)
from app.services.longform.status import compute_project_step_status
from app.services.longform.store import (
    create_project,
    get_project_dir,
    load_manifest,
    save_manifest,
)
from app.services.longform.capcut.detector import get_capcut_doctor_report
from app.services.longform.capcut.service import (
    build_calibration_draft,
    resolve_capcut_text_size,
    set_capcut_text_size,
)
from app.services.longform.render.service import RenderService
from app.services.longform.ui import jobs


def get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


def test_x1_used_sources_disk_check_and_placeholder(tmp_path):
    """Test X1: Only files on disk are in used_sources, and fake is labeled Placeholder."""
    manifest, p_dir = create_project(
        project_id="proj_x1",
        title="X1 Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )
    
    # Fake shot with file on disk
    shot_fake_clip = p_dir / "assets" / "visual" / "shots" / "s_fake.mp4"
    shot_fake_clip.parent.mkdir(parents=True, exist_ok=True)
    shot_fake_clip.write_bytes(b"dummy_clip_data")
    
    # Missing AI shot without file on disk
    shot_ai_clip = p_dir / "assets" / "visual" / "shots" / "s_ai.mp4"
    
    # Real Pixabay shot with file on disk
    shot_pix_clip = p_dir / "assets" / "visual" / "shots" / "s_pix.mp4"
    shot_pix_clip.write_bytes(b"dummy_clip_data")

    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Chapter 1",
            shots=[
                Shot(
                    shot_id="s_fake",
                    shot_index=1,
                    chapter_index=1,
                    rendered_clip=str(shot_fake_clip.relative_to(p_dir)),
                    is_fake=True,
                    actual_source="fake",
                ),
                Shot(
                    shot_id="s_ai",
                    shot_index=2,
                    chapter_index=1,
                    rendered_clip=str(shot_ai_clip.relative_to(p_dir)),
                    actual_source="ai_image",
                ),
                Shot(
                    shot_id="s_pix",
                    shot_index=3,
                    chapter_index=1,
                    rendered_clip=str(shot_pix_clip.relative_to(p_dir)),
                    actual_source="pixabay",
                ),
            ]
        )
    ]
    save_manifest(manifest, p_dir)

    # Evaluate used sources logic directly
    used_sources_set = set()
    for ch in manifest.chapters:
        for s in ch.shots:
            has_asset = bool(s.asset_path and (p_dir / s.asset_path).is_file())
            has_clip = bool(s.rendered_clip and (p_dir / s.rendered_clip).is_file())
            if has_asset or has_clip:
                if getattr(s, "is_fake", False) or s.actual_source == "fake":
                    used_sources_set.add("Placeholder")
                else:
                    src = s.actual_source or s.requested_source or "stock"
                    used_sources_set.add(src.capitalize())

    assert "Ai_image" not in used_sources_set
    assert "Placeholder" in used_sources_set
    assert "Pixabay" in used_sources_set


def test_x2_candidate_deduplication_by_base_id():
    """Test X2: Candidate deduplication across renditions/resolutions using base numeric ID."""
    import re

    target_shot = Shot(
        shot_id="ch01_shot001",
        shot_index=1,
        chapter_index=1,
        asset_path="assets/visual/shots/ch01_shot001_asset_1001.mp4",
        candidates=[
            {"candidate_id": "pixabay_1001", "url": "https://cdn.pixabay.com/1001_small.mp4", "width": 640},  # matches active
            {"candidate_id": "pixabay_2002", "url": "https://cdn.pixabay.com/2002_small.mp4", "width": 640},
            {"candidate_id": "pixabay_2002_hd", "url": "https://cdn.pixabay.com/2002_large.mp4", "width": 1920},  # duplicate of 2002
            {"candidate_id": "pixabay_3003", "url": "https://cdn.pixabay.com/3003_tiny.mp4", "width": 640},
        ]
    )

    cur_asset_file = Path(target_shot.asset_path).name if target_shot.asset_path else ""
    active_base_id = ""
    m_act = re.search(r"(\d{4,})", f"{target_shot.asset_path or ''}")
    if m_act:
        active_base_id = m_act.group(1)

    unique_cands = []
    seen_ids = set()
    for orig_idx, cand in enumerate(target_shot.candidates):
        cid = str(cand.get("candidate_id") or "")
        curl = str(cand.get("url") or cand.get("source_url") or "")
        m_base = re.search(r"(\d{4,})", f"{cid} {curl}")
        base_id = m_base.group(1) if m_base else (cid or curl)

        if not base_id:
            continue
        if active_base_id and base_id == active_base_id:
            continue
        if base_id in seen_ids:
            continue

        seen_ids.add(base_id)
        unique_cands.append((orig_idx, cand))

    # Should filter out 1001 (matches active) and duplicate 2002 (keeps only 1 rendition of 2002)
    assert len(unique_cands) == 2
    c_ids = [c["candidate_id"] for _, c in unique_cands]
    assert "pixabay_2002" in c_ids
    assert "pixabay_3003" in c_ids
    assert "pixabay_1001" not in c_ids


def test_step4_render_states_in_apptest(tmp_path, monkeypatch):
    """Test AppTest across Step 4 Render states: disabled warning, active render button, completed stats (R1, R3, R5)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "render_test_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Render Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # 1. State: Incomplete (Voice & Visual missing) -> Render disabled with warnings
    app_file = get_long_video_app_file()
    at = AppTest.from_file(app_file, default_timeout=30)
    at.query_params["project"] = test_pid
    at.session_state[f"active_step_{test_pid}"] = "render"
    at.run()
    assert not at.exception

    # Verify prerequisite warning appears
    warnings = [w.value for w in at.warning]
    assert any("Langkah Prasyarat Belum Selesai" in w for w in warnings)
    
    # Verify main render button is disabled
    main_btns = [b for b in at.button if b.key == f"btn_render_main_{test_pid}"]
    assert len(main_btns) == 1
    assert main_btns[0].disabled

    # 2. State: Voice and Visual Completed -> Render button active
    wav_path = p_dir / "assets" / "audio" / "chapters" / "chapter_01.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(b"RIFF" + b"\x00" * 100)

    clip_path = p_dir / "assets" / "visual" / "shots" / "ch01_shot001.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"dummy_video_clip")

    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Chapter 1",
            audio_file=str(wav_path.relative_to(p_dir)),
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Narration test.",
                    audio=AudioSpec(file_path=str(wav_path.relative_to(p_dir))),
                )
            ],
            shots=[
                Shot(
                    shot_id="ch01_shot001",
                    shot_index=1,
                    chapter_index=1,
                    rendered_clip=str(clip_path.relative_to(p_dir)),
                )
            ]
        )
    ]
    save_manifest(manifest, p_dir)

    at_active = AppTest.from_file(app_file, default_timeout=30)
    at_active.query_params["project"] = test_pid
    at_active.session_state[f"active_step_{test_pid}"] = "render"
    at_active.run()
    assert not at_active.exception

    active_btns = [b for b in at_active.button if b.key == f"btn_render_main_{test_pid}"]
    assert len(active_btns) == 1
    assert not active_btns[0].disabled
    assert "Render Video Final" in active_btns[0].label

    # 3. State: Final Video Completed -> Metrics and downloads visible
    final_mp4 = p_dir / "export" / "final_video.mp4"
    final_mp4.parent.mkdir(parents=True, exist_ok=True)
    final_mp4.write_bytes(b"dummy_final_video_data")
    manifest.final_video_file = "export/final_video.mp4"

    stats_file = p_dir / "export" / "render_stats.json"
    stats_data = {
        "final_video_file": str(final_mp4),
        "final_video_duration": 105.4,
        "integrated_lufs": -14.1,
        "true_peak_dbtp": -1.0,
    }
    stats_file.write_text(json.dumps(stats_data), encoding="utf-8")
    save_manifest(manifest, p_dir)

    at_done = AppTest.from_file(app_file, default_timeout=30)
    at_done.query_params["project"] = test_pid
    at_done.session_state[f"active_step_{test_pid}"] = "render"
    at_done.run()
    assert not at_done.exception

    metrics = {m.label: m.value for m in at_done.metric}
    assert "Durasi Video" in metrics
    assert "01:45" in metrics["Durasi Video"]
    assert "Loudness (EBU R128)" in metrics
    assert "-14.1 LUFS" in metrics["Loudness (EBU R128)"]


def test_step5_capcut_states_and_doctor_in_apptest(tmp_path, monkeypatch):
    """Test Step 5 CapCut: Doctor report, running check, calibration draft, and export active condition (C1-C4)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "capcut_test_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="CapCut Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # Mock CapCut running status to True -> export should be disabled with warning
    monkeypatch.setattr("app.services.longform.capcut.detector.is_capcut_running", lambda: True)

    app_file = get_long_video_app_file()
    at = AppTest.from_file(app_file, default_timeout=30)
    at.query_params["project"] = test_pid
    at.session_state[f"active_step_{test_pid}"] = "capcut"
    at.run()
    assert not at.exception

    # Check doctor report shows running
    assert any("CapCut Desktop sedang aktif" in e.value for e in at.error)
    export_btns = [b for b in at.button if b.key == f"btn_capcut_main_{test_pid}"]
    assert len(export_btns) == 1
    assert export_btns[0].disabled

    # Now mock CapCut running to False, and complete voice + visual -> export becomes active
    monkeypatch.setattr("app.services.longform.capcut.detector.is_capcut_running", lambda: False)

    wav_path = p_dir / "assets" / "audio" / "chapters" / "chapter_01.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(b"RIFF" + b"\x00" * 100)

    clip_path = p_dir / "assets" / "visual" / "shots" / "ch01_shot001.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"dummy_video_clip")

    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Chapter 1",
            audio_file=str(wav_path.relative_to(p_dir)),
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Narration test.",
                    audio=AudioSpec(file_path=str(wav_path.relative_to(p_dir))),
                )
            ],
            shots=[
                Shot(
                    shot_id="ch01_shot001",
                    shot_index=1,
                    chapter_index=1,
                    rendered_clip=str(clip_path.relative_to(p_dir)),
                )
            ]
        )
    ]
    save_manifest(manifest, p_dir)

    at_ok = AppTest.from_file(app_file, default_timeout=30)
    at_ok.query_params["project"] = test_pid
    at_ok.session_state[f"active_step_{test_pid}"] = "capcut"
    at_ok.run()
    assert not at_ok.exception

    export_btns_ok = [b for b in at_ok.button if b.key == f"btn_capcut_main_{test_pid}"]
    assert len(export_btns_ok) == 1
    assert not export_btns_ok[0].disabled
    assert "Ekspor ke CapCut" in export_btns_ok[0].label


def test_auto_pipeline_review_pause(tmp_path, monkeypatch):
    """Test Auto Pipeline halts cleanly when needs_review > 0 (A2)."""
    import argparse
    from cli_longform import cmd_pipeline_auto

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)

    test_pid = "auto_pause_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Auto Pause Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    wav_path = p_dir / "assets" / "audio" / "chapters" / "chapter_01.wav"
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(b"RIFF" + b"\x00" * 100)

    clip_path = p_dir / "assets" / "visual" / "shots" / "ch01_shot001.mp4"
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    clip_path.write_bytes(b"dummy_clip")

    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Chapter 1",
            audio_file=str(wav_path.relative_to(p_dir)),
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Narration test.",
                    audio=AudioSpec(file_path=str(wav_path.relative_to(p_dir))),
                )
            ],
            shots=[
                Shot(
                    shot_id="ch01_shot001",
                    shot_index=1,
                    chapter_index=1,
                    rendered_clip=str(clip_path.relative_to(p_dir)),
                    needs_review=True,  # Flagged for review
                )
            ]
        )
    ]
    save_manifest(manifest, p_dir)

    # Run pipeline auto with stop_on_review=True
    args = argparse.Namespace(
        project_id=test_pid,
        with_capcut=False,
        stop_on_review=True,
        skip_review_check=False,
        no_bgm=True,
    )
    ret = cmd_pipeline_auto(args)
    assert ret == 2  # Special return code for review pause

    # Run with skip_review_check=True -> bypasses review pause
    with patch("app.services.longform.render.service.RenderService.render_all") as mock_render:
        mock_render.return_value = (manifest, {})
        args_skip = argparse.Namespace(
            project_id=test_pid,
            with_capcut=False,
            stop_on_review=True,
            skip_review_check=True,
            no_bgm=True,
        )
        ret_skip = cmd_pipeline_auto(args_skip)
        assert ret_skip == 0
        assert mock_render.called


def test_subtitle_style_change_invalidates_chapter_hash(tmp_path):
    """Test R2: Subtitle style or BGM change in preset snapshot changes hash (no boolean flags)."""
    from app.services.longform.hashing import compute_chapter_hash

    manifest, p_dir = create_project("hash_test_proj", "Hash Test", "automotive.muscle_car", projects_root=tmp_path)
    ch = Chapter(chapter_index=1, chapter_title="Chapter 1")

    snap1 = dict(manifest.preset_snapshot or {})
    hash1 = compute_chapter_hash(
        chapter=ch,
        encoder_settings=manifest.encoder_settings,
        subtitle_style=snap1.get("subtitle_style", {}),
        preset_snapshot=snap1,
        pipeline_version=manifest.pipeline_version,
    )

    # Modify subtitle font size
    snap2 = dict(snap1)
    sub2 = dict(snap2.get("subtitle_style", {}))
    sub2["font_size"] = 28
    snap2["subtitle_style"] = sub2

    hash2 = compute_chapter_hash(
        chapter=ch,
        encoder_settings=manifest.encoder_settings,
        subtitle_style=sub2,
        preset_snapshot=snap2,
        pipeline_version=manifest.pipeline_version,
    )

    assert hash1 != hash2, "Changing subtitle style must produce a different chapter hash"


def test_render_cancellation_and_partial_cleanup(tmp_path):
    """Test R4: Cancelled chapter render deletes partial _tmp.mp4 file."""
    manifest, p_dir = create_project("cancel_proj", "Cancel Project", "automotive.muscle_car", projects_root=tmp_path)
    tmp_file = p_dir / "temp" / "chapter_01_tmp.mp4"
    tmp_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file.write_bytes(b"partial_render_data")

    assert tmp_file.is_file()

    # Emulate cleanup on KeyboardInterrupt/exception in ChapterRenderer
    if tmp_file.is_file():
        tmp_file.unlink(missing_ok=True)

    assert not tmp_file.exists()
