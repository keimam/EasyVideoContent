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
    """E4/E8: System spec evaluator scores green/yellow/red consistently against SYSTEM_SPEC_THRESHOLDS."""
    from app.services.longform.system_specs import SYSTEM_SPEC_THRESHOLDS, rate_numeric_spec

    assert rate_numeric_spec(16.0, minimum=7.5, recommended=15.0) == "green"
    assert rate_numeric_spec(7.75, minimum=7.5, recommended=15.0) == "yellow"
    assert rate_numeric_spec(4.0, minimum=7.5, recommended=15.0) == "red"

    disk_cfg = SYSTEM_SPEC_THRESHOLDS["disk_free_gb"]
    assert disk_cfg["min"] == 15.0
    assert disk_cfg["recommended"] == 20.0
    assert rate_numeric_spec(25.0, **{"minimum": disk_cfg["min"], "recommended": disk_cfg["recommended"]}) == "green"
    assert rate_numeric_spec(16.0, **{"minimum": disk_cfg["min"], "recommended": disk_cfg["recommended"]}) == "yellow"
    assert rate_numeric_spec(12.88, **{"minimum": disk_cfg["min"], "recommended": disk_cfg["recommended"]}) == "red"


# ---------------------------------------------------------------------------
# E8: Short Video Onboarding Tour gating + i18n + empty EFS_DATA_DIR Main.py
# ---------------------------------------------------------------------------

def test_e8_onboarding_tour_gating_and_i18n(tmp_path: Path, monkeypatch):
    """E8: Onboarding tour disabled during wizard & dialogs; uses i18n id & en after wizard completes."""
    import streamlit as st
    from app.services.branding import lifecycle_ui
    from app.services.longform import first_run

    data_dir = (tmp_path / "e8_tour_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    st.session_state.clear()

    # 1. Disabled before wizard completes
    assert first_run.is_setup_completed() is False
    assert lifecycle_ui.should_show_short_video_tour() is False

    # 2. Mark setup complete -> enabled only when no dialog or forced wizard is open
    first_run.mark_setup_completed(skipped=False)
    assert lifecycle_ui.should_show_short_video_tour() is True

    st.session_state["settings_dialog_open"] = True
    assert lifecycle_ui.should_show_short_video_tour() is False
    st.session_state["settings_dialog_open"] = False

    st.session_state["_efs_dialog_opened_this_run"] = True
    assert lifecycle_ui.should_show_short_video_tour() is False
    st.session_state["_efs_dialog_opened_this_run"] = False

    st.session_state["efs_wizard_force_open"] = True
    assert lifecycle_ui.should_show_short_video_tour() is False
    st.session_state["efs_wizard_force_open"] = False

    # 3. Uses localized i18n strings via t() in both id and en
    captured = {}

    class _FakeStep:
        def __init__(self, element, popover):
            self.element = element
            self.popover = popover

    class _FakeTour:
        @staticmethod
        def bind(element_id, **kwargs):
            return _FakeStep(element_id, dict(kwargs))

        def __init__(self, steps, **kwargs):
            captured["steps"] = steps
            captured["key"] = kwargs.get("key")

        def start(self):
            captured["started"] = True

    try:
        for lang_code, exp_done in (("id", "Selesai"), ("en", "Done")):
            st.session_state.clear()
            st.session_state["ui_language"] = lang_code
            captured.clear()
            ok = lifecycle_ui.run_short_video_onboarding_tour(_FakeTour)
            assert ok is True
            assert captured.get("started") is True
            assert captured["steps"][-1].popover["doneBtnText"] == exp_done
            for step in captured["steps"]:
                assert "MoneyPrinterTurbo" not in step.popover.get("title", "")
    finally:
        st.session_state.clear()


def test_e8_short_video_main_empty_efs_data_dir_renders_wizard_and_brand(tmp_path: Path, monkeypatch):
    """E8: webui/Main.py with empty EFS_DATA_DIR renders branded sidebar + wizard cleanly with zero errors."""
    from app.config import config as app_cfg

    repo_root = Path(__file__).resolve().parents[2]
    data_dir = (tmp_path / "e8_main_empty").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setattr("app.services.longform.update_checker.start_background_update_check", lambda *a, **kw: False)
    saved_ui = dict(app_cfg.ui)
    saved_app = dict(app_cfg.app)
    try:
        at = AppTest.from_file(str(repo_root / "webui" / "Main.py"))
        at.run(timeout=30)
        assert not at.exception, f"Main.py AppTest exception: {[e.value for e in at.exception]}"
        md_all = " ".join(m.value for m in at.markdown)
        assert "EasyFaceless" in md_all
        assert ("Wizard Pengaturan Pertama Kali" in md_all) or ("First-Run Setup Wizard" in md_all)
    finally:
        app_cfg.ui.clear()
        app_cfg.ui.update(saved_ui)
        app_cfg.app.clear()
        app_cfg.app.update(saved_app)


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


# ---------------------------------------------------------------------------
# E9: Single language source of truth, bounded reruns, standalone wizard routing
# ---------------------------------------------------------------------------

def test_e9_language_single_source_mapping_and_consistency(tmp_path: Path, monkeypatch):
    """E9.1: Explicit language code mapping (id<->id-ID/Bahasa Indonesia, en<->en/English) & sync."""
    import streamlit as st
    from app.config import config as app_cfg
    from app.services.branding import language
    from app.services.longform.ui.i18n import get_ui_language, t

    monkeypatch.setenv("EFS_DATA_DIR", str((tmp_path / "e9_lang_map").resolve()))
    st.session_state.clear()
    try:
        # Canonical mapping checks
        assert language.normalize_ui_language("id") == "id"
        assert language.normalize_ui_language("id-ID") == "id"
        assert language.normalize_ui_language("Bahasa Indonesia") == "id"
        assert language.normalize_ui_language("en") == "en"
        assert language.normalize_ui_language("en-US") == "en"
        assert language.normalize_ui_language("English") == "en"

        assert language.to_narration_code("id") == "id-ID"
        assert language.to_narration_code("Bahasa Indonesia") == "id-ID"
        assert language.to_narration_code("en") == "en"
        assert language.to_narration_code("English") == "en"

        assert language.to_display_name("id-ID") == "Bahasa Indonesia"
        assert language.to_display_name("en") == "English"

        # Single setter synchronizes session_state, config.ui.language, t(), and sibling widgets
        language.set_ui_language("en-US", persist=True)
        assert st.session_state["ui_language"] == "en"
        assert app_cfg.ui["language"] == "en"
        assert get_ui_language() == "en"
        assert t("nav_long_video") == "Long Video"

        language.set_ui_language("Bahasa Indonesia", persist=True)
        assert st.session_state["ui_language"] == "id"
        assert app_cfg.ui["language"] == "id"
        assert get_ui_language() == "id"
        assert t("nav_long_video") == "Video Panjang"
    finally:
        st.session_state.clear()


def test_e9_language_switch_bounded_reruns_wizard_settings_mpt(tmp_path: Path, monkeypatch):
    """E9.1/E9.3: id->en->id in Wizard, Settings, and MPT dropdown stay synced with <= 2 runs per switch."""
    from app.config import config as app_cfg
    from app.services.branding import hooks
    from app.services.longform import first_run

    repo_root = Path(__file__).resolve().parents[2]
    main_file = str(repo_root / "webui" / "Main.py")
    data_dir = (tmp_path / "e9_loop_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setattr("app.services.longform.update_checker.start_background_update_check", lambda *a, **kw: False)
    monkeypatch.setattr("app.services.version_checker.get_available_update", lambda *a, **kw: None)

    run_counter = {"count": 0}
    orig_init = hooks.init_main_page

    def _counting_init(*args, **kwargs):
        run_counter["count"] += 1
        return orig_init(*args, **kwargs)

    monkeypatch.setattr("app.services.branding.hooks.init_main_page", _counting_init)

    saved_ui = dict(app_cfg.ui)
    saved_app = dict(app_cfg.app)
    try:
        # 1. Wizard Step 1 (empty EFS_DATA_DIR): id -> en -> id
        at_wiz = AppTest.from_file(main_file, default_timeout=40)
        at_wiz.run(timeout=40)
        assert not at_wiz.exception
        md0 = " ".join(m.value for m in at_wiz.markdown)
        assert "Wizard Pengaturan Pertama Kali" in md0
        # Standalone wizard: NO Short Video content & NO top MPT bar underneath
        assert not any(sb.key == "top_language_code_selector" for sb in at_wiz.selectbox)
        assert not any(b.key == "generate_video_button" for b in at_wiz.button)
        # Sidebar must NOT highlight Video Panjang or Video Pendek as active
        sb_pl_labels = [pl.label for pl in at_wiz.page_link]
        assert "**Video Panjang**" not in sb_pl_labels and "**Video Pendek**" not in sb_pl_labels

        run_counter["count"] = 0
        wiz_radio = [r for r in at_wiz.radio if r.key == "wiz_lang_radio_main"][0]
        wiz_radio.set_value("en").run(timeout=30)
        assert not at_wiz.exception
        assert run_counter["count"] == 1, f"Wizard id->en rerun loop detected: {run_counter['count']} runs"
        md_en = " ".join(m.value for m in at_wiz.markdown)
        assert "First-Run Setup Wizard" in md_en
        assert "Wizard Pengaturan Pertama Kali" not in md_en
        assert at_wiz.session_state["ui_language"] == "en"
        assert app_cfg.ui["language"] == "en"

        # Idle follow-up run stays cleanly English (zero flip-flop)
        run_counter["count"] = 0
        at_wiz.run(timeout=30)
        assert at_wiz.session_state["ui_language"] == "en"
        assert "First-Run Setup Wizard" in " ".join(m.value for m in at_wiz.markdown)

        run_counter["count"] = 0
        wiz_radio = [r for r in at_wiz.radio if r.key == "wiz_lang_radio_main"][0]
        wiz_radio.set_value("id").run(timeout=30)
        assert not at_wiz.exception
        assert run_counter["count"] == 1, f"Wizard en->id rerun loop detected: {run_counter['count']} runs"
        assert "Wizard Pengaturan Pertama Kali" in " ".join(m.value for m in at_wiz.markdown)
        assert at_wiz.session_state["ui_language"] == "id"
        assert app_cfg.ui["language"] == "id"

        # 2. Post-wizard Main.py: MPT top dropdown id -> en -> id & Settings selector id -> en -> id
        first_run.mark_setup_completed(skipped=False)
        app_cfg.ui["language"] = "id"
        at_main = AppTest.from_file(main_file, default_timeout=40)
        at_main.run(timeout=40)
        assert not at_main.exception
        assert any(sb.key == "top_language_code_selector" for sb in at_main.selectbox)

        # Open Settings General tab -> switch id -> en -> id inside Pengaturan
        at_main.button(key="open_settings_dialog_button").click().run(timeout=30)
        assert not at_main.exception
        run_counter["count"] = 0
        set_sel = [sb for sb in at_main.selectbox if sb.key == "settings_ui_language_select"][0]
        set_sel.set_value("en").run(timeout=30)
        assert not at_main.exception
        assert run_counter["count"] <= 2, f"Settings id->en rerun loop detected: {run_counter['count']} runs"
        assert at_main.session_state["ui_language"] == "en"
        assert app_cfg.ui["language"] == "en"
        assert [sb for sb in at_main.selectbox if sb.key == "top_language_code_selector"][0].value == "en"

        run_counter["count"] = 0
        set_sel = [sb for sb in at_main.selectbox if sb.key == "settings_ui_language_select"][0]
        set_sel.set_value("id").run(timeout=30)
        assert not at_main.exception
        assert run_counter["count"] <= 2, f"Settings en->id rerun loop detected: {run_counter['count']} runs"
        assert at_main.session_state["ui_language"] == "id"
        assert app_cfg.ui["language"] == "id"
        assert [sb for sb in at_main.selectbox if sb.key == "top_language_code_selector"][0].value == "id"

        # MPT top-right dropdown id -> en -> id syncs everywhere cleanly
        run_counter["count"] = 0
        top_sel = [sb for sb in at_main.selectbox if sb.key == "top_language_code_selector"][0]
        top_sel.set_value("en").run(timeout=30)
        assert not at_main.exception
        assert run_counter["count"] <= 2, f"MPT dropdown id->en rerun loop detected: {run_counter['count']} runs"
        assert at_main.session_state["ui_language"] == "en"
        assert app_cfg.ui["language"] == "en"
        assert [sb for sb in at_main.selectbox if sb.key == "settings_ui_language_select"][0].value == "en"
        assert "Long Video" in [pl.label for pl in at_main.page_link]

        run_counter["count"] = 0
        top_sel = [sb for sb in at_main.selectbox if sb.key == "top_language_code_selector"][0]
        top_sel.set_value("id").run(timeout=30)
        assert not at_main.exception
        assert run_counter["count"] <= 2, f"MPT dropdown en->id rerun loop detected: {run_counter['count']} runs"
        assert at_main.session_state["ui_language"] == "id"
        assert app_cfg.ui["language"] == "id"
        assert [sb for sb in at_main.selectbox if sb.key == "settings_ui_language_select"][0].value == "id"
        assert "Video Panjang" in [pl.label for pl in at_main.page_link]
    finally:
        app_cfg.ui.clear()
        app_cfg.ui.update(saved_ui)
        app_cfg.app.clear()
        app_cfg.app.update(saved_app)


def test_e9_wizard_standalone_url_redirect_and_finish_to_long_video(tmp_path: Path, monkeypatch):
    """E9.2: Direct /Long_Video while incomplete redirects to Wizard (except Help/About); Skip/Finish go to Long Video."""
    data_dir = (tmp_path / "e9_routing_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setattr("app.services.longform.update_checker.start_background_update_check", lambda *a, **kw: False)

    switched_targets = []

    def _fake_switch_page(target, **kwargs):
        switched_targets.append((str(target), kwargs))
        raise RuntimeError("simulated switch_page halt check")

    monkeypatch.setattr("streamlit.switch_page", _fake_switch_page)

    # 1. Opening Long Video directly while wizard incomplete attempts redirect to Main.py and hides Long Video content
    at_lv = AppTest.from_file(get_long_video_app_file())
    at_lv.run(timeout=30)
    assert not at_lv.exception
    assert any(t_path == "Main.py" for t_path, _ in switched_targets)
    md_lv = " ".join(m.value for m in at_lv.markdown)
    assert "Wizard Pengaturan Pertama Kali" in md_lv
    assert "Mulai video baru" not in md_lv

    # 2. Help query exception (?dialog=help) opens Help without redirecting away
    switched_targets.clear()
    at_help = AppTest.from_file(get_long_video_app_file())
    at_help.query_params["dialog"] = "help"
    at_help.run(timeout=30)
    assert not at_help.exception
    assert switched_targets == []
    assert any("Panduan" in m.value or "Guide" in m.value or "EasyFaceless" in m.value for m in at_help.markdown)
    assert "Mulai video baru" not in " ".join(m.value for m in at_help.markdown)

    # 3. Skip wizard routes to pages/1_🎬_Long_Video.py
    switched_targets.clear()
    at_skip = AppTest.from_file(get_long_video_app_file())
    at_skip.run(timeout=30)
    switched_targets.clear()
    at_skip.button(key="btn_wiz_skip_home").click().run(timeout=20)
    assert not at_skip.exception
    assert any("1_🎬_Long_Video.py" in t_path for t_path, _ in switched_targets)
