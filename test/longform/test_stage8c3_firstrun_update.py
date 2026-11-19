"""Tests for Stage 8C.3: First-Run Wizard (E1), App Update Checker (E2), CapCut Version Detection (E3), System Specs (E4), and Output/Credits UI (E5)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.services.longform.schema import AudioSpec, Chapter, ItemStatus, Segment, Shot, TtsGroup
from app.services.longform.store import create_project, save_manifest


def get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


# ---------------------------------------------------------------------------
# E1: First-run wizard marker, EFS_DATA_DIR isolation, starter project, UI flow
# ---------------------------------------------------------------------------

def test_e1_wizard_marker_efs_data_dir_and_sample_project(tmp_path: Path, monkeypatch):
    """E1: Empty EFS_DATA_DIR has no setup marker; skip/complete/reset + starter project isolate cleanly."""
    from app.services.longform import app_paths, first_run, store

    repo_root = Path(__file__).resolve().parents[2].resolve()
    data_dir = (tmp_path / "empty_user_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("EFS_INSTALLED", raising=False)

    assert app_paths.get_data_dir() == data_dir
    assert first_run.is_setup_completed() is False
    assert first_run.get_setup_marker_path() == data_dir / ".efs_setup_complete.json"
    assert not (repo_root / ".efs_setup_complete.json").exists()

    # Mark skipped -> marker exists cleanly inside EFS_DATA_DIR only
    marker = first_run.mark_setup_completed(skipped=True)
    assert marker.is_file()
    assert marker.parent == data_dir
    assert first_run.is_setup_completed() is True
    meta = first_run.read_setup_marker()
    assert meta and meta.get("skipped") is True

    # Reset -> marker removed cleanly ("Jalankan wizard lagi")
    assert first_run.reset_setup_marker() is True
    assert first_run.is_setup_completed() is False

    # Starter sample project builds inside EFS_DATA_DIR projects folder
    pid, p_dir = first_run.create_starter_sample_project(lang="id")
    assert p_dir.parent == store.get_projects_root()
    assert p_dir.parent == data_dir / "storage" / "longform_projects"
    loaded = store.load_manifest(p_dir)
    assert loaded.project_id == pid
    assert len(loaded.chapters) == 2
    assert loaded.target_duration_minutes == 2


def test_e1_wizard_auto_opens_steps_skip_and_rerun_ui(tmp_path: Path, monkeypatch):
    """E1: Empty EFS_DATA_DIR opens Wizard automatically, steps 1..6 work, and Skip persists marker."""
    data_dir = (tmp_path / "wiz_ui_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("EFS_UPDATE_URL", "")
    # Prevent live internet checks inside test environment
    monkeypatch.setattr("app.services.longform.platform_utils.check_internet_connectivity", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.longform.update_checker.start_background_update_check", lambda *a, **kw: False)

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=30)
    assert not at.exception, f"Wizard initial render threw: {[e.value for e in at.exception]}"

    md_all = " ".join(m.value for m in at.markdown)
    assert "Wizard Pengaturan Pertama Kali" in md_all or "First-Run Setup Wizard" in md_all

    # Advance to Step 2 (System Check)
    next_btns = [b for b in at.button if b.key == "btn_wiz_next_home"]
    assert len(next_btns) == 1
    next_btns[0].click().run(timeout=20)
    assert not at.exception
    md_step2 = " ".join(m.value for m in at.markdown)
    assert "RAM" in md_step2 and "CPU" in md_step2 and "FFmpeg" in md_step2

    # Advance Step 3 -> 4 -> 5 -> 6 and Skip setup
    skip_btns = [b for b in at.button if b.key == "btn_wiz_skip_home"]
    assert len(skip_btns) == 1
    skip_btns[0].click().run(timeout=20)
    assert not at.exception

    # Marker written & wizard hidden after skip
    assert (data_dir / ".efs_setup_complete.json").is_file()
    md_after = " ".join(m.value for m in at.markdown)
    assert "Wizard Pengaturan Pertama Kali" not in md_after


# ---------------------------------------------------------------------------
# E2: Update checker version comparison, 24h throttle, offline & skip logic
# ---------------------------------------------------------------------------

def test_e2_update_checker_logic_throttle_offline_skip_and_banner(tmp_path: Path, monkeypatch):
    """E2: Semver compare, local file:/// EFS_UPDATE_URL, offline silent, 24h throttle, skip version, and banner UI."""
    from app.services.longform import first_run, update_checker

    data_dir = (tmp_path / "upd_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    first_run.mark_setup_completed(skipped=False)

    # 1. Version comparisons
    assert update_checker.is_newer_version("1.0.1", "1.0.0") is True
    assert update_checker.is_newer_version("v1.1.0", "1.0.0") is True
    assert update_checker.is_newer_version("1.0.0", "1.0.0") is False
    assert update_checker.is_newer_version("0.9.9", "1.0.0") is False

    # 2. Local file:/// manifest with newer version 1.0.1
    manifest_file = tmp_path / "latest.json"
    manifest_file.write_text(
        json.dumps({
            "version": "1.0.1",
            "released": "2026-12-01",
            "notes_id": "Perbaikan baru ID",
            "notes_en": "New release EN",
            "download_url": "https://example.com/setup-1.0.1.exe",
            "min_supported": "1.0.0",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("EFS_UPDATE_URL", manifest_file.as_uri())

    t0 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    res1 = update_checker.check_for_updates(force=False, now=t0)
    assert res1["checked_now"] is True
    assert res1["has_update"] is True
    assert res1["show_banner"] is True
    assert res1["remote_version"] == "1.0.1"

    # 3. 24-hour throttle prevents re-checking after 3 hours
    manifest_file.write_text(json.dumps({"version": "1.0.2"}), encoding="utf-8")
    res_3h = update_checker.check_for_updates(force=False, now=t0 + timedelta(hours=3))
    assert res_3h["checked_now"] is False
    assert res_3h["remote_version"] == "1.0.1"

    # After 25 hours -> runs fresh fetch and sees 1.0.2
    res_25h = update_checker.check_for_updates(force=False, now=t0 + timedelta(hours=25))
    assert res_25h["checked_now"] is True
    assert res_25h["remote_version"] == "1.0.2"

    # 4. Same version -> has_update=False, show_banner=False
    manifest_file.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
    res_same = update_checker.check_for_updates(force=True, now=t0 + timedelta(hours=26))
    assert res_same["has_update"] is False
    assert res_same["show_banner"] is False

    # 5. Offline / broken URL -> never raises, error captured silently
    monkeypatch.setenv("EFS_UPDATE_URL", "http://127.0.0.1:9/nonexistent.json")
    res_off = update_checker.check_for_updates(force=True, timeout=0.4, now=t0 + timedelta(hours=27))
    assert res_off["checked_now"] is True
    assert res_off["error"] is not None
    assert res_off["show_banner"] is False

    # 6. Restore 1.0.1 & verify UI Banner + Skip Version button persistence
    manifest_file.write_text(
        json.dumps({
            "version": "1.0.1",
            "released": "2026-12-01",
            "notes_id": "Catatan uji versi 1.0.1",
            "notes_en": "Test notes v1.0.1",
            "download_url": "https://example.com/setup-1.0.1.exe",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("EFS_UPDATE_URL", manifest_file.as_uri())
    update_checker.write_update_state({"auto_check_enabled": True, "last_checked_at": None, "skipped_version": None, "latest_manifest": None})

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=30)
    assert not at.exception
    md_all = " ".join(m.value for m in at.markdown)
    assert "v1.0.1" in md_all and "Catatan uji versi 1.0.1" in md_all

    skip_btn = [b for b in at.button if b.key == "btn_upd_skip_home"]
    assert len(skip_btn) == 1
    skip_btn[0].click().run(timeout=20)
    assert not at.exception
    assert update_checker.read_update_state().get("skipped_version") == "1.0.1"
    assert update_checker.get_active_update_banner() is None

    # 7. Auto check toggle disables automatic checks when force=False
    update_checker.set_auto_check_enabled(False)
    res_disabled = update_checker.check_for_updates(force=False, now=t0 + timedelta(days=5))
    assert res_disabled["checked_now"] is False


# ---------------------------------------------------------------------------
# E3: CapCut version parsing, tested status, macOS stub, UI non-blocking export
# ---------------------------------------------------------------------------

def test_e3_capcut_version_parsing_tested_status_and_macos_stub(tmp_path: Path, monkeypatch):
    """E3: ProductInfo.xml parsing, tested vs untested status, macOS NotImplementedError stub, and custom folder."""
    from app.services.longform import first_run, platform_utils
    from app.services.longform.capcut import detector

    monkeypatch.setenv("EFS_DATA_DIR", str(tmp_path / "cc_data"))

    # 1. XML parsing for tested version 8.9.0
    xml_890 = tmp_path / "ProductInfo_890.xml"
    xml_890.write_text(
        '<packet><appver value="8.9.0" /><full_appver value="8.9.0.3794" /></packet>',
        encoding="utf-8",
    )
    parsed = detector.parse_capcut_version_xml(xml_890)
    assert parsed["installed"] is True
    assert parsed["app_ver"] == "8.9.0"
    assert parsed["full_ver"] == "8.9.0.3794"
    assert detector.is_capcut_version_tested("8.9.0") is True
    assert detector.is_capcut_version_tested("8.9.0.3794") is True
    assert detector.is_capcut_version_tested("9.2.0") is False

    # 2. Untested version -> status_kind='untested', UI still enables Export
    monkeypatch.setattr(
        "app.services.longform.capcut.detector.detect_capcut_version",
        lambda *a, **kw: {"installed": True, "app_ver": "9.2.0", "full_ver": "9.2.0.100", "apps_dir": str(tmp_path), "unsupported_os_reason": None},
    )
    monkeypatch.setattr("app.services.longform.capcut.detector.is_capcut_running", lambda: False)
    draft_dir = tmp_path / "capcut_drafts"
    draft_dir.mkdir(parents=True)
    first_run.set_custom_capcut_drafts_dir(draft_dir)
    assert detector.detect_draft_folder() == draft_dir

    rep_untested = detector.get_capcut_doctor_report()
    assert rep_untested["status_kind"] == "untested"
    assert rep_untested["version_tested"] is False

    # 3. macOS platform_utils stub raises clear NotImplementedError; doctor handles gracefully
    monkeypatch.setattr("app.services.longform.platform_utils.get_platform_key", lambda: "darwin")
    with pytest.raises(NotImplementedError, match="macOS"):
        platform_utils.get_capcut_version_info()


def test_e4_system_spec_thresholds_green_yellow_red():
    """E4: System spec evaluator scores green/yellow/red consistently against SYSTEM_SPEC_THRESHOLDS."""
    from app.services.longform.system_specs import rate_numeric_spec

    assert rate_numeric_spec(16.0, minimum=7.5, recommended=15.0) == "green"
    assert rate_numeric_spec(7.75, minimum=7.5, recommended=15.0) == "yellow"
    assert rate_numeric_spec(4.0, minimum=7.5, recommended=15.0) == "red"


# ---------------------------------------------------------------------------
# E5: Completed project card output & credits.md viewer dialog
# ---------------------------------------------------------------------------

def test_e5_completed_project_output_and_credits_viewer_ui(tmp_path: Path, monkeypatch):
    """E5: Completed project card shows 'Buka folder output' & 'Buka credits.md'; clicking opens credits dialog."""
    data_dir = (tmp_path / "e5_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setattr("app.services.longform.update_checker.start_background_update_check", lambda *a, **kw: False)
    from app.services.longform import first_run

    first_run.mark_setup_completed(skipped=True)
    projects_root = data_dir / "storage" / "longform_projects"
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: projects_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: projects_root)

    manifest, p_dir = create_project("done_e5_proj", "Completed Doc E5", "documentary_id", projects_root=projects_root)
    # Populate completed files so compute_project_step_status scores 5/5 done
    grp_wav_rel = "assets/audio/groups/ch1_g1.wav"
    (p_dir / grp_wav_rel).parent.mkdir(parents=True, exist_ok=True)
    (p_dir / grp_wav_rel).write_bytes(b"RIFF" + b"\x00" * 100)
    wav_rel = "assets/audio/chapters/ch01.wav"
    (p_dir / wav_rel).parent.mkdir(parents=True, exist_ok=True)
    (p_dir / wav_rel).write_bytes(b"RIFF" + b"\x00" * 100)
    clip_rel = "assets/visual/clips/ch01_shot001_clip.mp4"
    (p_dir / clip_rel).parent.mkdir(parents=True, exist_ok=True)
    (p_dir / clip_rel).write_bytes(b"\x00" * 100)
    fin_rel = "export/final_video.mp4"
    (p_dir / fin_rel).parent.mkdir(parents=True, exist_ok=True)
    (p_dir / fin_rel).write_bytes(b"\x00" * 100)
    cc_rel = "export/capcut/EFS_done_e5_proj_20261119"
    (p_dir / cc_rel).mkdir(parents=True, exist_ok=True)
    (p_dir / cc_rel / "draft_content.json").write_text("{}", encoding="utf-8")
    (p_dir / "export" / "credits.md").write_text(
        "# Credits\n\n- Shot 1: Apollo Archive (`CC BY 2.0`) — https://images.nasa.gov/details/as11\n",
        encoding="utf-8",
    )

    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Chapter 1",
            audio_file=wav_rel,
            audio_duration_seconds=5.0,
            status=ItemStatus.completed,
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Sample completed narration.",
                    audio=AudioSpec(file_path=grp_wav_rel, duration_seconds=5.0),
                    segments=[Segment(segment_index=1, narration_text="Sample completed narration.")],
                    status=ItemStatus.completed,
                )
            ],
            shots=[
                Shot(
                    shot_id="ch01_shot001",
                    chapter_index=1,
                    shot_index=1,
                    segment_indices=[1],
                    duration_seconds=5.0,
                    target_frames=150,
                    actual_source="nasa",
                    rendered_clip=clip_rel,
                    status=ItemStatus.completed,
                )
            ],
        )
    ]
    manifest.final_video_file = fin_rel
    manifest.status = ItemStatus.completed
    save_manifest(manifest, p_dir)

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=30)
    assert not at.exception

    btn_out = [b for b in at.button if b.key == "btn_card_output_done_e5_proj"]
    btn_cred = [b for b in at.button if b.key == "btn_card_credits_done_e5_proj"]
    assert len(btn_out) == 1
    assert len(btn_cred) == 1

    # Click Open credits.md -> renders in-app viewer dialog with attribution content & Copy button
    btn_cred[0].click().run(timeout=20)
    assert not at.exception
    md_all = " ".join(m.value for m in at.markdown)
    assert "Apollo Archive" in md_all
    assert any(b.key == "btn_credits_copy_done_e5_proj" for b in at.button)
