"""Tests for Stage 8A.2: Long Video UI overhaul matching mockups and bilingual i18n (L1-L5)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    ItemStatus,
    MotionType,
    Segment,
    Shot,
    TtsGroup,
)
from app.services.longform.store import create_project, save_manifest
from app.services.longform.ui import i18n
from app.services.longform.ui.i18n import get_ui_language, t


def get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


def test_8a2_longvideo_home_renders_cards(tmp_path, monkeypatch):
    """Verify L1 Mockup 1: Beranda renders 3-column cards, filter pills, open creation form, 16:9 thumbs, 5-segment bar."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    # Create 2 test projects
    p1, p1_dir = create_project("proj_alpha", "Alpha Muscle Cars", "automotive.muscle_car", projects_root=tmp_path)
    p2, p2_dir = create_project("proj_beta", "Beta JDM Classics", "automotive.jdm", projects_root=tmp_path)

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=15)

    assert not at.exception, f"AppTest threw: {[e.value for e in at.exception]}"

    # 1. Verify "Mulai video baru" form inputs are directly available without opening an expander
    topic_inputs = [ti for ti in at.text_input if "Topik" in (ti.label or "") or "Topic" in (ti.label or "")]
    assert len(topic_inputs) >= 1, "Creation form topic input should be directly rendered"

    submit_btns = [b for b in at.button if "Buat Proyek" in (b.label or "") or "Create Project" in (b.label or "")]
    assert len(submit_btns) >= 1, "Primary submit button must be present in creation form"

    # 2. Verify filter pills
    assert len(at.pills) >= 1, "Filter pills must be rendered"
    pills = at.pills[0]
    pills_options_str = " ".join(pills.options)
    assert "Semua" in pills_options_str or "All" in pills_options_str

    # 3. Verify project cards with 16:9 thumbnail and 5-segment progress bar HTML
    md_all = " ".join(m.value for m in at.markdown)
    assert "efs-project-card-thumb" in md_all, "Project card thumbnail container must be rendered"
    assert "efs-progress-bar" in md_all, "5-segment progress bar must be rendered"

    # 4. Verify open project action buttons
    open_btns = [b for b in at.button if "Buka proyek" in (b.label or "") or "Open project" in (b.label or "")]
    assert len(open_btns) == 2, f"Expected 2 open project buttons for 2 projects, found {len(open_btns)}"


def test_8a2_longvideo_project_layout(tmp_path, monkeypatch):
    """Verify L2 Mockup 2: Project detail has clean title, meta badges, 5-card stepper, and 2-column visual detail."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "proj_layout_test"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Dodge Charger RT 1969",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # Setup chapter with 2 shots and audio
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1: The Legend",
        audio_duration_seconds=8.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="The Dodge Charger RT is a classic muscle car.",
                audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=8.0),
                segments=[Segment(segment_index=1, narration_text="The Dodge Charger RT is a classic muscle car.")],
            ),
        ],
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                segment_indices=[1],
                visual_prompt="Front grill of 1969 Dodge Charger RT",
                duration_seconds=4.0,
                target_frames=100,
                motion=MotionType.zoom_in,
                status=ItemStatus.completed,
            ),
            Shot(
                shot_id="ch01_shot002",
                chapter_index=1,
                shot_index=2,
                segment_indices=[1],
                visual_prompt="V8 engine rumbling",
                duration_seconds=4.0,
                target_frames=100,
                motion=MotionType.pan_right,
                status=ItemStatus.pending,
            ),
        ],
    )
    manifest.chapters = [ch]
    (p_dir / "assets" / "audio" / "groups").mkdir(parents=True, exist_ok=True)
    (p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav").write_bytes(b"RIFF" + b"\x00" * 100)
    save_manifest(manifest, p_dir)

    at = AppTest.from_file(get_long_video_app_file())
    at.query_params["project"] = test_pid
    at.run(timeout=15)

    assert not at.exception, f"AppTest threw: {[e.value for e in at.exception]}"

    # 1. Clean Title without double emoji
    all_titles = " ".join(t.value for t in at.title)
    assert "Dodge Charger RT 1969" in all_titles

    # 2. Stepper with 5 step cards
    stepper_buttons = [
        b for b in at.button
        if any(step in (b.label or "") for step in ["① Naskah", "② Suara", "③ Visual", "④ Render", "⑤ CapCut"])
    ]
    assert len(stepper_buttons) == 5, f"Expected 5 stepper buttons, found {len(stepper_buttons)}"

    # 3. Switch to Visual Step (③ Visual)
    vis_btn = next(b for b in stepper_buttons if "③ Visual" in b.label)
    vis_btn.click().run(timeout=15)
    assert not at.exception

    # 4. In Visual Step: verify 2-column layout elements (detail panel replacement tabs)
    tab_labels = [t.label for t in at.tabs]
    assert any("Kandidat" in lbl for lbl in tab_labels), "Replacement candidate tab should be present"
    assert any("Cari" in lbl for lbl in tab_labels), "Search footage tab should be present"
    assert any("Unggah" in lbl for lbl in tab_labels), "Upload local footage tab should be present"


def test_8a2_longvideo_completed_view(tmp_path, monkeypatch):
    """Verify L3 Mockup 3: Completed project shows green success banner, 2-column layout, and YouTube timestamps."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "proj_completed_test"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Completed Documentary",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # Setup 1 chapter with audio, shots, and final export
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1: The Roar",
        audio_duration_seconds=10.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Narration 1",
                audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=10.0),
                segments=[Segment(segment_index=1, narration_text="Narration 1")],
            ),
        ],
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                segment_indices=[1],
                visual_prompt="Dodge Charger",
                duration_seconds=10.0,
                target_frames=250,
                motion=MotionType.zoom_in,
                status=ItemStatus.completed,
                rendered_clip="assets/visual/shots/ch01_shot001.mp4",
            ),
        ],
    )
    manifest.chapters = [ch]

    # Create dummy files for completed state
    (p_dir / "assets" / "audio" / "groups").mkdir(parents=True, exist_ok=True)
    (p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav").write_bytes(b"RIFF" + b"\x00" * 100)

    (p_dir / "assets" / "visual" / "shots").mkdir(parents=True, exist_ok=True)
    (p_dir / "assets" / "visual" / "shots" / "ch01_shot001.mp4").write_bytes(b"ftypisom" + b"\x00" * 100)

    (p_dir / "export").mkdir(parents=True, exist_ok=True)
    (p_dir / "export" / "final.mp4").write_bytes(b"ftypisom" + b"\x00" * 200)
    manifest.final_video_file = "export/final.mp4"

    # YouTube chapters file
    yt_file = p_dir / "export" / "youtube_chapters.txt"
    yt_file.write_text("00:00 Bab 1: The Roar\n", encoding="utf-8")

    # CapCut draft
    cc_draft_dir = p_dir / "export" / "capcut" / f"EFS_{test_pid}_20261114-120000"
    cc_draft_dir.mkdir(parents=True, exist_ok=True)
    (cc_draft_dir / "draft_content.json").write_text("{}", encoding="utf-8")

    save_manifest(manifest, p_dir)

    at = AppTest.from_file(get_long_video_app_file())
    at.query_params["project"] = test_pid
    at.run(timeout=15)

    assert not at.exception, f"AppTest threw: {[e.value for e in at.exception]}"

    # 1. Verify Green Success Banner is rendered
    md_all = " ".join(m.value for m in at.markdown)
    assert "efs-success-banner" in md_all, "Green success banner must be rendered when all steps are done"

    # 2. Switch to Render step (④ Render) to see side-by-side view
    render_stepper_btns = [b for b in at.button if "④ Render" in (b.label or "")]
    assert len(render_stepper_btns) == 1
    render_stepper_btns[0].click().run(timeout=15)
    assert not at.exception

    # 3. Verify YouTube timestamps code block
    code_blocks = [c.value for c in at.code]
    assert any("Bab 1: The Roar" in cb for cb in code_blocks), "YouTube chapter timestamps code block must be present"


def test_8a2_i18n_switching(monkeypatch):
    """Verify L4: Bilingual switching between 'id' and 'en' properly localizes all key strings."""
    from app.config import config

    # 1. Test Indonesian
    monkeypatch.setitem(config.ui, "language", "id")
    assert get_ui_language() == "id"
    assert t("nav_long_video") == "Video Panjang"
    assert t("stepper_script") == "Naskah"
    assert t("stepper_visual") == "Visual"
    assert t("filter_all") == "Semua"
    assert t("btn_create_project") == "Buat Proyek"
    assert "Video siap" in t("success_banner_title")

    # 2. Test English
    monkeypatch.setitem(config.ui, "language", "en")
    assert get_ui_language() == "en"
    assert t("nav_long_video") == "Long Video"
    assert t("stepper_script") == "Script"
    assert t("stepper_visual") == "Visual"
    assert t("filter_all") == "All"
    assert t("btn_create_project") == "Create Project"
    assert "ready to upload" in t("success_banner_title").lower()
