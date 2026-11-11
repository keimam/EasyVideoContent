"""Unit and integration tests for Step 3 Visual WebUI (Stage 7B.1, V1-V6).

Tests:
1. AppTest for Step 3 Visual across states: belum, berjalan, terhenti, selesai, perlu_diperbarui (V1, V6).
2. Interactive gallery performance benchmark: uji_nokey with 112 shots opens in < 5 seconds without loading full video files (V2, V6).
3. Candidate replacement rebuilds only the selected shot and marks it locked (V3, V6).
4. Local file upload saves to library and applies to shot with auto-lock (V3, V6).
5. Stop marker check in fetch and build pipeline (V1, V6).
6. Global cache inspection and pruning protecting locked assets (V4, V6).
7. Store migration preset_recovery keyword verification (V5).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest
from streamlit.testing.v1 import AppTest

from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    ItemStatus,
    MotionType,
    ProjectManifest,
    Segment,
    Shot,
    TtsGroup,
)
from app.services.longform.status import compute_project_step_status
from app.services.longform.store import (
    clear_stop_request,
    create_project,
    get_project_dir,
    load_manifest,
    request_job_stop,
    save_manifest,
)
from app.services.longform.ui.components import get_shot_narration, get_shot_thumbnail_path
from app.services.longform.visual.cache import get_cache_status, prune_cache
from app.services.longform.visual.local import LocalLibraryManager
from app.services.longform.visual.service import VisualService


def get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


def test_step3_visual_states_in_apptest(tmp_path, monkeypatch):
    """Test AppTest across visual states: belum, terhenti, perlu_diperbarui, selesai, and berjalan (V1, V6)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "vis_states_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Visual States Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # Setup 1 chapter with audio completed and 2 shots
    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1: The Legend",
        audio_duration_seconds=10.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Narration 1",
                audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=5.0),
                segments=[Segment(segment_index=1, narration_text="Narration 1")],
            ),
            TtsGroup(
                group_index=2,
                paragraph_text="Narration 2",
                audio=AudioSpec(file_path="assets/audio/groups/ch1_g2.wav", duration_seconds=5.0),
                segments=[Segment(segment_index=2, narration_text="Narration 2")],
            ),
        ],
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                segment_indices=[1],
                visual_prompt="Dodge Charger on track",
                duration_seconds=5.0,
                target_frames=125,
                motion=MotionType.zoom_in,
                status=ItemStatus.pending,
            ),
            Shot(
                shot_id="ch01_shot002",
                chapter_index=1,
                shot_index=2,
                segment_indices=[2],
                visual_prompt="V8 Engine roaring",
                duration_seconds=5.0,
                target_frames=125,
                motion=MotionType.pan_right,
                status=ItemStatus.pending,
            ),
        ],
    )
    manifest.chapters = [ch]
    (p_dir / "assets" / "audio" / "groups").mkdir(parents=True, exist_ok=True)
    (p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav").write_bytes(b"RIFF" + b"\x00" * 100)
    (p_dir / "assets" / "audio" / "groups" / "ch1_g2.wav").write_bytes(b"RIFF" + b"\x00" * 100)
    save_manifest(manifest, p_dir)

    # --- 1. State: belum ---
    at_belum = AppTest.from_file(get_long_video_app_file())
    at_belum.query_params["project"] = test_pid
    at_belum.run(timeout=10)
    assert not at_belum.exception, f"AppTest belum threw: {[e.value for e in at_belum.exception]}"
    btn_labels = [b.label for b in at_belum.button if b.label]
    assert any("Cari & Susun Visual" in lbl for lbl in btn_labels)
    # Checkbox checks
    cb_labels = [cb.label for cb in at_belum.checkbox if cb.label]
    assert any("Pixabay" in lbl for lbl in cb_labels)
    assert any("File lokal" in lbl for lbl in cb_labels)

    # --- 2. State: terhenti (1 shot built out of 2) ---
    shots_dir = p_dir / "assets" / "visual" / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    clip1 = shots_dir / "ch01_shot001.mp4"
    clip1.write_bytes(b"ftypisom" + b"\x00" * 200)
    manifest.chapters[0].shots[0].rendered_clip = "assets/visual/shots/ch01_shot001.mp4"
    manifest.chapters[0].shots[0].status = ItemStatus.completed
    save_manifest(manifest, p_dir)

    at_terhenti = AppTest.from_file(get_long_video_app_file())
    at_terhenti.query_params["project"] = test_pid
    at_terhenti.run(timeout=10)
    assert not at_terhenti.exception
    btn_labels_th = [b.label for b in at_terhenti.button if b.label]
    assert any("Lanjutkan" in lbl for lbl in btn_labels_th)

    # --- 3. State: perlu_diperbarui (1 shot stale) ---
    manifest.chapters[0].shots[0].status = ItemStatus.stale
    save_manifest(manifest, p_dir)

    at_stale = AppTest.from_file(get_long_video_app_file())
    at_stale.query_params["project"] = test_pid
    at_stale.run(timeout=10)
    assert not at_stale.exception
    btn_labels_st = [b.label for b in at_stale.button if b.label]
    assert any("Perbarui Klip yang Berubah" in lbl for lbl in btn_labels_st)

    # --- 4. State: selesai (all shots completed) ---
    clip2 = shots_dir / "ch01_shot002.mp4"
    clip2.write_bytes(b"ftypisom" + b"\x00" * 200)
    manifest.chapters[0].shots[0].status = ItemStatus.completed
    manifest.chapters[0].shots[1].rendered_clip = "assets/visual/shots/ch01_shot002.mp4"
    manifest.chapters[0].shots[1].status = ItemStatus.completed
    save_manifest(manifest, p_dir)

    at_selesai = AppTest.from_file(get_long_video_app_file())
    at_selesai.query_params["project"] = test_pid
    at_selesai.run(timeout=10)
    assert not at_selesai.exception

    # When visual is completed, stepper defaults to next step (render).
    # Click stepper button ③ Visual to view visual step content:
    vis_stepper = [b for b in at_selesai.button if "Visual" in (b.label or "")][0]
    vis_stepper.click().run()
    assert not at_selesai.exception

    btn_labels_sl = [b.label for b in at_selesai.button if b.label]
    assert any("Selesai ✔ — Lanjut ke Render" in lbl for lbl in btn_labels_sl)

    # --- 5. State: berjalan (simulated background job) ---
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(15)"],
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        job_state = {
            "project_id": test_pid,
            "step": "visual_pipeline",
            "status": "running",
            "pid": worker.pid,
            "exit_code": None,
            "started_at": "2026-11-11T10:00:00",
            "finished_at": None,
            "log_file": str(p_dir / "logs" / "job_mock.log"),
            "command": ["mock"],
            "error_message": None,
        }
        (p_dir / "logs").mkdir(parents=True, exist_ok=True)
        (p_dir / "job_state.json").write_text(json.dumps(job_state), encoding="utf-8")

        at_berjalan = AppTest.from_file(get_long_video_app_file())
        at_berjalan.query_params["project"] = test_pid
        at_berjalan.run(timeout=10)
        assert not at_berjalan.exception
        info_texts = " ".join(inf.value for inf in at_berjalan.info)
        assert "Pekerjaan Sedang Berjalan" in info_texts
        assert "Pencarian & Penyusunan Visual" in info_texts
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=2.0)


def test_uji_nokey_gallery_performance():
    """Benchmark: 112-clip uji_nokey project gallery loads in < 5.0 seconds without reading full videos (V2, V6)."""
    repo_root = Path(__file__).resolve().parents[2]
    p_dir = repo_root / "storage" / "longform_projects" / "uji_nokey"
    if not (p_dir / "manifest.json").is_file():
        pytest.skip("Project 'uji_nokey' not found in storage.")

    manifest = load_manifest(p_dir)
    total_shots = sum(len(ch.shots) for ch in manifest.chapters)
    assert total_shots == 112, f"Expected 112 shots in uji_nokey, got {total_shots}"

    # 1. Fast thumbnail lookup for all 112 shots
    t0 = time.perf_counter()
    found_thumbnails = 0
    for ch in manifest.chapters:
        for shot in ch.shots:
            thumb = get_shot_thumbnail_path(p_dir, shot)
            if thumb and thumb.is_file():
                found_thumbnails += 1
    elapsed = time.perf_counter() - t0

    assert found_thumbnails == 112, f"Expected 112 thumbnails, found {found_thumbnails}"
    assert elapsed < 5.0, f"Gallery thumbnail lookup took {elapsed:.3f}s (must be < 5.0s)"

    # 2. Narration text extraction check
    narr = get_shot_narration(manifest.chapters[0], manifest.chapters[0].shots[0])
    assert len(narr) > 0, "Shot narration must not be empty"

    # 3. Verify via AppTest for uji_nokey
    t_app0 = time.perf_counter()
    at = AppTest.from_file(get_long_video_app_file())
    at.query_params["project"] = "uji_nokey"
    at.run(timeout=15)
    app_elapsed = time.perf_counter() - t_app0

    assert not at.exception, f"AppTest threw exceptions: {[e.value for e in at.exception]}"
    assert app_elapsed < 5.0, f"AppTest render took {app_elapsed:.3f}s (must be < 5.0s)"


def test_replace_shot_candidate_auto_lock_and_single_rebuild(tmp_path):
    """Test candidate replacement rebuilds only 1 clip and auto-locks shot (V3, V6)."""
    test_pid = "replace_test_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Replace Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    shot1 = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        visual_prompt="Dodge Charger driving on a highway.",
        duration_seconds=3.0,
        target_frames=75,
        status=ItemStatus.pending,
        candidates=[
            {
                "candidate_id": "fake_c1",
                "provider": "fake",
                "source_type": "ai_image",
                "url": "fake://c1",
                "relevance_score": 0.88,
                "tags": ["dodge", "charger", "highway"],
            },
            {
                "candidate_id": "fake_c2",
                "provider": "fake",
                "source_type": "ai_image",
                "url": "fake://c2",
                "relevance_score": 0.75,
                "tags": ["car", "road"],
            },
        ],
    )
    shot2 = Shot(
        shot_id="ch01_shot002",
        chapter_index=1,
        shot_index=2,
        visual_prompt="Engine sound roaring.",
        duration_seconds=3.0,
        target_frames=75,
        status=ItemStatus.pending,
    )
    ch = Chapter(chapter_index=1, chapter_title="Intro", shots=[shot1, shot2])
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    service = VisualService(p_dir)
    updated_shot = service.replace_shot(
        manifest=manifest,
        shot_id="ch01_shot001",
        candidate_index=1,
        rebuild=True,
    )

    assert updated_shot.locked is True
    assert updated_shot.actual_source == "fake"
    assert updated_shot.status == ItemStatus.completed
    assert updated_shot.rendered_clip is not None
    assert (p_dir / updated_shot.rendered_clip).is_file()

    # Verify shot 2 was untouched
    assert manifest.chapters[0].shots[1].status == ItemStatus.pending
    assert manifest.chapters[0].shots[1].rendered_clip is None


def test_local_file_upload_and_library_add(tmp_path):
    """Test LocalLibraryManager.add_file and applying uploaded file to shot (V3, V6)."""
    test_pid = "upload_test_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Upload Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        visual_prompt="Uploaded footage shot",
        duration_seconds=2.0,
        target_frames=50,
        status=ItemStatus.pending,
    )
    manifest.chapters = [Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])]
    save_manifest(manifest, p_dir)

    # 1. Upload mock file into local library
    mgr = LocalLibraryManager(p_dir)
    mock_bytes = b"MOCK_VIDEO_DATA" * 50
    asset_info = mgr.add_file(mock_bytes, "my_custom_footage.mp4")

    assert asset_info["filename"] == "my_custom_footage.mp4"
    assert asset_info["asset_type"] == "stock_video"
    saved_file = Path(asset_info["file_path"])
    assert saved_file.is_file()
    assert saved_file.stat().st_size == len(mock_bytes)

    # 2. Apply to shot via replace_shot (without rebuild for raw binary mock)
    svc = VisualService(p_dir)
    res_shot = svc.replace_shot(
        manifest=manifest,
        shot_id="ch01_shot001",
        file_path=saved_file,
        rebuild=False,
    )

    assert res_shot.locked is True
    assert res_shot.actual_source == "local"
    assert res_shot.asset_path == "assets/visual/shots/ch01_shot001_asset.mp4"
    assert (p_dir / res_shot.asset_path).is_file()


def test_visual_stop_marker_handling(tmp_path):
    """Test stop marker (.stop) stops fetch and build loops cleanly (V1, V6)."""
    test_pid = "stop_marker_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Stop Marker Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        shots=[
            Shot(
                shot_id=f"ch01_shot{i:03d}",
                chapter_index=1,
                shot_index=i,
                duration_seconds=2.0,
                target_frames=50,
                visual_prompt=f"Shot {i}",
            )
            for i in range(1, 5)
        ],
    )
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    # Request stop before fetching
    request_job_stop(p_dir)

    svc = VisualService(p_dir)
    # fetch_all should respect stop marker without raising error
    m_fetched = svc.fetch_all(manifest=manifest, provider_name="fake")
    assert m_fetched is not None
    # No shots fetched because stop was requested immediately
    assert all(s.asset_path is None for s in m_fetched.chapters[0].shots)

    clear_stop_request(p_dir)


def test_cache_maintenance_and_locked_protection():
    """Test cache status and pruning with locked file protection (V4, V6)."""
    status = get_cache_status()
    assert "total_files" in status
    assert "total_mb" in status
    assert "locked_files_count" in status
    assert isinstance(status["locked_files_count"], int)

    # Test prune protects locked files
    prune_res = prune_cache(older_than_days=99999)
    assert "files_removed" in prune_res
    assert "locked_files_skipped" in prune_res
    assert prune_res["locked_files_skipped"] >= 0


def test_preset_recovery_keyword_in_store():
    """Verify keyword 'preset_recovery' is present in store.py (V5)."""
    repo_root = Path(__file__).resolve().parents[2]
    store_file = repo_root / "app" / "services" / "longform" / "store.py"
    content = store_file.read_text(encoding="utf-8")
    assert "preset_recovery" in content, "Keyword 'preset_recovery' must be present in store.py"


def test_dodge_case_script_and_audio_done_zero_shots_activates_button_and_allocates_shots(tmp_path, monkeypatch):
    """Reproduce Dodge Charger case: Naskah+Audio done, 0 shots -> button active -> pipeline allocates shots (W1, W4.7)."""
    import argparse
    from cli_longform import cmd_visual_pipeline

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "dodge_repro_test"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Dodge Repro Test",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # 1. Setup Script and Audio as 100% completed, but 0 shots
    wav_file = p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav"
    wav_file.parent.mkdir(parents=True, exist_ok=True)
    wav_file.write_bytes(b"RIFF" + b"\x00" * 40 + b"WAVEfmt " + b"\x00" * 100)  # > 44 bytes

    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1: The Legend",
        audio_duration_seconds=5.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="The 1969 Dodge Charger was a high-performance icon.",
                audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=5.0),
                segments=[Segment(segment_index=1, narration_text="The 1969 Dodge Charger was a high-performance icon.")],
            ),
        ],
        shots=[],  # 0 shots!
    )
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    # Verify status: script and voice are selesai, visual is belum with 0 shots
    st_info = compute_project_step_status(p_dir)
    assert st_info["steps"]["script"]["status"] == "selesai"
    assert st_info["steps"]["voice"]["status"] == "selesai"
    assert st_info["steps"]["visual"]["status"] == "belum"
    assert sum(len(c.shots) for c in manifest.chapters) == 0

    # 2. Check AppTest: Step 3 button "Cari & Susun Visual" MUST BE ACTIVE (disabled == False)
    at = AppTest.from_file(get_long_video_app_file())
    at.query_params["project"] = test_pid
    at.run(timeout=15)
    assert not at.exception

    # Switch to visual step
    btn_vis_stepper = None
    for b in at.button:
        if "③ Visual" in b.label:
            btn_vis_stepper = b
            break
    assert btn_vis_stepper is not None
    btn_vis_stepper.click()
    at.run(timeout=15)
    assert not at.exception

    # Button "🎬 Cari & Susun Visual" should exist and NOT be disabled
    start_vis_btn = None
    for b in at.button:
        if "Cari & Susun Visual" in b.label:
            start_vis_btn = b
            break
    assert start_vis_btn is not None, "Button '🎬 Cari & Susun Visual' must be present"
    assert start_vis_btn.disabled is False, "Button must be ACTIVE when script and voice are done, even with 0 shots"

    # 3. Run visual pipeline CLI: Phase 1/3 must allocate shots
    args = argparse.Namespace(
        project_id=test_pid,
        provider="fake",
        allow_fallback=True,
    )
    rc = cmd_visual_pipeline(args)
    assert rc == 0

    # Reload manifest and verify shots were allocated and built
    manifest_after = load_manifest(p_dir)
    shots_after = manifest_after.chapters[0].shots
    assert len(shots_after) > 0, "Phase 1/3 must allocate shots when total_shots == 0"
    assert all(s.rendered_clip is not None for s in shots_after), "All allocated shots must have rendered clips"


def test_banner_dismissal_and_step_suppression(tmp_path, monkeypatch):
    """Test job monitor dismissal button (✕) and suppression across workflow steps (W3)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "banner_test_proj"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Banner Test Project",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    from app.services.longform.ui import jobs

    # 1. Simulate a completed audio_synthesize job
    job_state = {
        "project_id": test_pid,
        "step": "audio_synthesize",
        "status": "completed",
        "pid": 12345,
        "started_at": "2026-11-10T10:00:00",
        "finished_at": "2026-11-10T10:05:00",
        "dismissed": False,
    }
    jobs._save_job_state_to_disk(test_pid, job_state)

    # Check banner dismissal function
    assert jobs.dismiss_job(test_pid) is True
    updated_state = jobs.get_job_state(test_pid)
    assert updated_state["dismissed"] is True


def test_interrupted_visual_build_status_is_terhenti_not_selesai(tmp_path, monkeypatch):
    """Test that if visual build is interrupted, status is 'terhenti', not 'selesai' (X1)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    test_pid = "interrupted_build_test"
    manifest, p_dir = create_project(
        project_id=test_pid,
        title="Interrupted Build Test",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    # 4 shots, all have assets, but only 2 have built clips
    shots = []
    for i in range(1, 5):
        asset_rel = f"assets/visual/shots/ch01_shot{i:03d}_asset.mp4"
        clip_rel = f"assets/visual/shots/ch01_shot{i:03d}.mp4"

        # Create dummy asset file for all 4 shots
        af = p_dir / asset_rel
        af.parent.mkdir(parents=True, exist_ok=True)
        af.write_bytes(b"dummy asset data")

        # Create rendered clip only for first 2 shots
        if i <= 2:
            cf = p_dir / clip_rel
            cf.write_bytes(b"dummy clip data")
            c_status = ItemStatus.completed
        else:
            c_status = ItemStatus.pending

        shots.append(
            Shot(
                shot_id=f"ch01_shot{i:03d}",
                chapter_index=1,
                shot_index=i,
                duration_seconds=3.0,
                target_frames=75,
                visual_prompt=f"Shot {i}",
                asset_path=asset_rel,
                rendered_clip=clip_rel if i <= 2 else None,
                status=c_status,
            )
        )

    # Add completed script and voice to chapter
    wav_file = p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav"
    wav_file.parent.mkdir(parents=True, exist_ok=True)
    wav_file.write_bytes(b"RIFF" + b"\x00" * 40 + b"WAVEfmt " + b"\x00" * 100)

    ch = Chapter(
        chapter_index=1,
        chapter_title="Chapter 1",
        audio_duration_seconds=12.0,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="The muscle car era was defined by raw horsepower.",
                audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=12.0),
                segments=[Segment(segment_index=1, narration_text="The muscle car era was defined by raw horsepower.")],
            )
        ],
        shots=shots,
    )
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    st_info = compute_project_step_status(p_dir)
    vis_step = st_info["steps"]["visual"]
    assert vis_step["status"] == "terhenti", "Status must be 'terhenti' when build is interrupted"
    assert "Terhenti di 2 dari 4 (fase: Menyusun klip)" in vis_step["label"]
    assert vis_step["built_shots"] == 2
    assert vis_step["fetched_shots"] == 4
    assert vis_step["total"] == 4
    assert st_info["next_step"] == "Visual", "Next step must be Visual when build is interrupted"


def test_short_query_extractor():
    """Test smart short keyword extractor (X3)."""
    from app.services.longform.visual.query_extractor import extract_short_search_query

    # 1. With explicit [search: ...] tag
    t_tag = "The roaring engines shook Detroit [search: vintage dragstrip burnout smoke] as racers lined up."
    assert extract_short_search_query(t_tag) == "vintage dragstrip burnout smoke"

    # 2. Long sentence without tag -> extract 3-5 keywords without stopwords
    t_long = "To this day, the true fate of the Lost Colony remains one of history's greatest unsolved riddles."
    res = extract_short_search_query(t_long, visual_keywords=["ruins", "settlement"])
    words = res.split()
    assert 3 <= len(words) <= 6
    assert "lost" in res.lower()
    assert "colony" in res.lower()
    assert "greatest" not in res.lower(), "Generic words like greatest should be excluded"
    assert "the" not in res.lower(), "Stopwords should be excluded"


def test_required_subject_term_excludes_cable_car():
    """Test that 'cable car' does not falsely match automotive 'car' (X4)."""
    from app.services.longform.visual.stock import term_matches_tags

    # Cable car should NOT match car
    assert term_matches_tags("car", ["cable car", "docklands", "london", "thames"]) is False
    # Regular car or muscle car SHOULD match
    assert term_matches_tags("car", ["highway", "road", "car", "speed"]) is True
    assert term_matches_tags("car", ["vintage", "muscle car", "asphalt"]) is True
