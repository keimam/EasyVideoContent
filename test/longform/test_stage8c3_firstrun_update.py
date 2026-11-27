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

def _snapshot_repo_runtime_files(repo_root: Path) -> dict[Path, tuple[bool, bytes]]:
    """Snapshot dev-root runtime files so tests neither depend on nor mutate user state (E0)."""
    targets = (
        repo_root / ".efs_setup_complete.json",
        repo_root / ".efs_launcher.lock.json",
        repo_root / "config.toml",
    )
    return {p: (p.exists(), p.read_bytes() if p.is_file() else b"") for p in targets}


def _assert_repo_runtime_files_unchanged(before: dict[Path, tuple[bool, bytes]]) -> None:
    for p, (existed, raw) in before.items():
        assert p.exists() == existed, f"Test altered existence of repo-root file {p}"
        after_bytes = p.read_bytes() if p.is_file() else b""
        assert after_bytes == raw, f"Test modified repo-root file {p}"


def test_e1_wizard_marker_efs_data_dir_and_sample_project(tmp_path: Path, monkeypatch):
    """E1: Empty EFS_DATA_DIR has no setup marker; skip/complete/reset + starter project isolate cleanly."""
    from app.services.longform import app_paths, first_run, store

    repo_root = Path(__file__).resolve().parents[2].resolve()
    before_repo_files = _snapshot_repo_runtime_files(repo_root)
    data_dir = (tmp_path / "empty_user_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("EFS_INSTALLED", raising=False)

    assert app_paths.get_data_dir() == data_dir
    assert first_run.is_setup_completed() is False
    assert first_run.get_setup_marker_path() == data_dir / ".efs_setup_complete.json"
    _assert_repo_runtime_files_unchanged(before_repo_files)

    # Mark skipped -> marker exists cleanly inside EFS_DATA_DIR only
    marker = first_run.mark_setup_completed(skipped=True)
    assert marker.is_file()
    assert marker.parent == data_dir
    assert first_run.is_setup_completed() is True
    meta = first_run.read_setup_marker()
    assert meta and meta.get("skipped") is True
    _assert_repo_runtime_files_unchanged(before_repo_files)

    # Reset -> marker removed cleanly ("Jalankan wizard lagi")
    assert first_run.reset_setup_marker() is True
    assert first_run.is_setup_completed() is False
    _assert_repo_runtime_files_unchanged(before_repo_files)

    # Starter sample project builds inside EFS_DATA_DIR projects folder
    pid, p_dir = first_run.create_starter_sample_project(lang="id")
    assert p_dir.parent == store.get_projects_root()
    assert p_dir.parent == data_dir / "storage" / "longform_projects"
    loaded = store.load_manifest(p_dir)
    assert loaded.project_id == pid
    assert len(loaded.chapters) == 2
    assert loaded.target_duration_minutes == 2
    _assert_repo_runtime_files_unchanged(before_repo_files)


def test_e1_wizard_auto_opens_steps_skip_and_rerun_ui(tmp_path: Path, monkeypatch):
    """E1: Empty EFS_DATA_DIR opens Wizard automatically, steps 1..6 work, and Skip persists marker."""
    data_dir = (tmp_path / "wiz_ui_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("EFS_UPDATE_URL", "")
    # Prevent live internet checks inside test environment
    monkeypatch.setattr("app.services.longform.platform_utils.check_internet_connectivity", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.longform.update_checker.start_background_update_check", lambda *a, **kw: False)

    at = AppTest.from_file(get_long_video_app_file(), default_timeout=45)
    at.run(timeout=45)
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

    # 2. Local file:/// manifest with newer version
    from app.services.branding.constants import APP_VERSION
    _p = [int(x) for x in APP_VERSION.split(".")[:3]]
    _next1 = f"{_p[0]}.{_p[1]}.{_p[2] + 1}"
    _next2 = f"{_p[0]}.{_p[1]}.{_p[2] + 2}"

    manifest_file = tmp_path / "latest.json"
    manifest_file.write_text(
        json.dumps({
            "version": _next1,
            "released": "2026-12-01",
            "notes_id": "Perbaikan baru ID",
            "notes_en": "New release EN",
            "download_url": f"https://example.com/setup-{_next1}.exe",
            "min_supported": APP_VERSION,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("EFS_UPDATE_URL", manifest_file.as_uri())

    t0 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    res1 = update_checker.check_for_updates(force=False, now=t0)
    assert res1["checked_now"] is True
    assert res1["has_update"] is True
    assert res1["show_banner"] is True
    assert res1["remote_version"] == _next1

    # 3. 24-hour throttle prevents re-checking after 3 hours
    manifest_file.write_text(json.dumps({"version": _next2}), encoding="utf-8")
    res_3h = update_checker.check_for_updates(force=False, now=t0 + timedelta(hours=3))
    assert res_3h["checked_now"] is False
    assert res_3h["remote_version"] == _next1

    # After 25 hours -> runs fresh fetch and sees _next2
    res_25h = update_checker.check_for_updates(force=False, now=t0 + timedelta(hours=25))
    assert res_25h["checked_now"] is True
    assert res_25h["remote_version"] == _next2

    # 4. Same version -> has_update=False, show_banner=False
    manifest_file.write_text(json.dumps({"version": APP_VERSION}), encoding="utf-8")
    res_same = update_checker.check_for_updates(force=True, now=t0 + timedelta(hours=26))
    assert res_same["has_update"] is False
    assert res_same["show_banner"] is False

    # 5. Offline / broken URL -> never raises, error captured silently
    monkeypatch.setenv("EFS_UPDATE_URL", "http://127.0.0.1:9/nonexistent.json")
    res_off = update_checker.check_for_updates(force=True, timeout=0.4, now=t0 + timedelta(hours=27))
    assert res_off["checked_now"] is True
    assert res_off["error"] is not None
    assert res_off["show_banner"] is False

    # 6. Restore _next1 & verify UI Banner + Skip Version button persistence
    manifest_file.write_text(
        json.dumps({
            "version": _next1,
            "released": "2026-12-01",
            "notes_id": f"Catatan uji versi {_next1}",
            "notes_en": f"Test notes v{_next1}",
            "download_url": f"https://example.com/setup-{_next1}.exe",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("EFS_UPDATE_URL", manifest_file.as_uri())
    update_checker.write_update_state({"auto_check_enabled": True, "last_checked_at": None, "skipped_version": None, "latest_manifest": None})

    at = AppTest.from_file(get_long_video_app_file(), default_timeout=45)
    at.run(timeout=45)
    assert not at.exception
    md_all = " ".join(m.value for m in at.markdown)
    assert f"v{_next1}" in md_all and f"Catatan uji versi {_next1}" in md_all

    skip_btn = [b for b in at.button if b.key == "btn_upd_skip_home"]
    assert len(skip_btn) == 1
    skip_btn[0].click().run(timeout=20)
    assert not at.exception
    assert update_checker.read_update_state().get("skipped_version") == _next1
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

    at = AppTest.from_file(get_long_video_app_file(), default_timeout=45)
    at.run(timeout=45)
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


# ---------------------------------------------------------------------------
# E10: Installed fileWatcherType=none, EFS_UPDATE_URL server restart & logging
# ---------------------------------------------------------------------------

def test_e10_launcher_file_watcher_disabled_and_update_url_restart(tmp_path: Path, monkeypatch):
    """E10.1/E10.2: Installed mode disables file watcher; changed EFS_UPDATE_URL restarts stale server."""
    from types import SimpleNamespace
    from app.services.longform import launcher_runtime

    data_dir = (tmp_path / "e10_launcher_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("EFS_INSTALLED", "1")
    monkeypatch.delenv("EFS_UPDATE_URL", raising=False)

    launched = []
    terminated = []

    def _fake_launch(cmd, *, cwd, log_file, env):
        launched.append({"cmd": list(cmd), "env": dict(env)})
        return SimpleNamespace(pid=42000 + len(launched), poll=lambda: None)

    monkeypatch.setattr("app.services.longform.platform_utils.launch_desktop_app_detached", _fake_launch)
    monkeypatch.setattr("app.services.longform.platform_utils.is_pid_alive", lambda pid: int(pid) >= 42000)
    monkeypatch.setattr("app.services.longform.platform_utils.terminate_process_tree", lambda pid, **kw: terminated.append(int(pid)) or True)
    monkeypatch.setattr("app.services.longform.launcher_runtime.is_streamlit_server_healthy", lambda *a, **kw: True)
    monkeypatch.setattr("app.services.longform.launcher_runtime.find_free_port", lambda *a, **kw: 8599)

    info1 = launcher_runtime.launch_or_focus_app(open_browser=False)
    assert info1["reused"] is False
    assert "--server.fileWatcherType" in launched[0]["cmd"]
    idx = launched[0]["cmd"].index("--server.fileWatcherType")
    assert launched[0]["cmd"][idx + 1] == "none"
    assert launched[0]["env"].get("STREAMLIT_SERVER_FILE_WATCHER_TYPE") == "none"

    # Re-launch with same env -> reuses running server
    info2 = launcher_runtime.launch_or_focus_app(open_browser=False)
    assert info2["reused"] is True
    assert len(launched) == 1

    # Re-launch with new EFS_UPDATE_URL -> terminates old server and forwards new EFS_UPDATE_URL
    monkeypatch.setenv("EFS_UPDATE_URL", "file:///F:/latest_test.json")
    info3 = launcher_runtime.launch_or_focus_app(open_browser=False)
    assert info3["reused"] is False
    assert terminated == [42001]
    assert len(launched) == 2
    assert launched[1]["env"].get("EFS_UPDATE_URL") == "file:///F:/latest_test.json"


def test_e10_update_checker_logging_sync_startup_and_settings_results(tmp_path: Path, monkeypatch):
    """E10.2/E10.3: Update checks log URL/versions/decision; Settings manual button shows available/up-to-date/failed."""
    from loguru import logger
    from app.services.longform import first_run, update_checker

    data_dir = (tmp_path / "e10_upd_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    first_run.mark_setup_completed(skipped=False)

    assert update_checker._normalize_manifest_url("file://F:/latest_test.json") == "file:///F:/latest_test.json"

    log_lines = []
    sink_id = logger.add(lambda msg: log_lines.append(str(msg)), level="INFO")
    try:
        manifest_path = tmp_path / "latest_test.json"
        from app.services.branding.constants import APP_VERSION
        _parts = [int(p) for p in APP_VERSION.split(".")[:3]]
        _next_ver = f"{_parts[0]}.{_parts[1]}.{_parts[2] + 1}"
        manifest_path.write_text(
            json.dumps({
                "version": _next_ver,
                "released": "2026-12-01",
                "notes_id": f"Rilis uji {_next_ver}",
                "notes_en": f"Test release {_next_ver}",
                "download_url": f"https://example.com/{_next_ver}.exe",
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("EFS_UPDATE_URL", manifest_path.as_uri())

        # Startup check completes synchronously and immediately populates active banner
        assert update_checker.start_background_update_check() is True
        banner = update_checker.get_active_update_banner()
        assert banner is not None and banner["remote_version"] == _next_ver
        assert any(f"local_version={APP_VERSION}" in ln and f"remote_version={_next_ver}" in ln and "decision=update_available" in ln for ln in log_lines)

        # Manual check inside Settings reports 'available', 'up_to_date', and 'failed + reason'
        at = AppTest.from_file(get_long_video_app_file())
        at.session_state["settings_dialog_open"] = True
        at.run(timeout=30)
        assert not at.exception

        # Available (newer)
        at.button(key="btn_check_updates_general_settings").click().run(timeout=20)
        assert not at.exception
        assert any(_next_ver in el.value for el in at.info)

        # Up to date (current APP_VERSION)
        manifest_path.write_text(json.dumps({"version": APP_VERSION}), encoding="utf-8")
        at.button(key="btn_check_updates_general_settings").click().run(timeout=20)
        assert not at.exception
        assert any(APP_VERSION in el.value for el in at.success)
        assert any("decision=up_to_date" in ln for ln in log_lines)

        # Failed + concise error reason (invalid URL)
        monkeypatch.setenv("EFS_UPDATE_URL", "http://127.0.0.1:9/missing_latest.json")
        at.button(key="btn_check_updates_general_settings").click().run(timeout=20)
        assert not at.exception
        assert len(at.warning) >= 1
        assert any("decision=failed:" in ln for ln in log_lines)
    finally:
        logger.remove(sink_id)


def test_e11_empty_prod_url_friendly_errors_about_and_sidebar_badge(tmp_path: Path, monkeypatch):
    """E11.1–E11.4 & R1.1: Single URL source, all decision logs, friendly 404/offline, About & sidebar badge."""
    from loguru import logger
    from app.services.branding.constants import UPDATE_MANIFEST_URL
    from app.services.longform import first_run, update_checker

    assert UPDATE_MANIFEST_URL == "https://raw.githubusercontent.com/keimam/EasyFacelessStudio-updates/main/latest.json"

    data_dir = (tmp_path / "e11_data").resolve()
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("EFS_UPDATE_URL", raising=False)
    monkeypatch.setattr(update_checker, "UPDATE_MANIFEST_URL", "")
    first_run.mark_setup_completed(skipped=False)

    log_lines = []
    sink_id = logger.add(lambda msg: log_lines.append(str(msg)), level="INFO")
    try:
        # 1. Empty default placeholder: auto-check disabled without HTTP error
        assert update_checker.get_update_manifest_url() == ""
        assert update_checker.start_background_update_check() is False
        res_no = update_checker.check_for_updates(force=True)
        assert res_no["error_kind"] == "no_url"
        assert any("decision=failed:no_update_url" in ln for ln in log_lines)

        # 2. Override dynamically with EFS_UPDATE_URL -> 404/missing local file friendly msg & skipped_version / skipped_throttle logs
        missing_uri = (tmp_path / "does_not_exist.json").as_uri()
        monkeypatch.setenv("EFS_UPDATE_URL", missing_uri)
        assert update_checker.get_update_manifest_url() == missing_uri
        res_404 = update_checker.check_for_updates(force=True)
        assert res_404["error_kind"] == "http_404"
        assert any("decision=failed:" in ln for ln in log_lines)

        # 3. Valid newer manifest -> throttle & skipped_version logs + About dialog & sidebar badge UI
        from app.services.branding.constants import APP_VERSION
        _p = [int(x) for x in APP_VERSION.split(".")[:3]]
        _next_v = f"{_p[0]}.{_p[1]}.{_p[2] + 1}"
        manifest_path = tmp_path / "latest_next.json"
        manifest_path.write_text(
            json.dumps({
                "version": _next_v,
                "released": "2026-12-01",
                "notes_id": "Catatan uji E11",
                "notes_en": "E11 test notes",
                "download_url": f"https://example.com/{_next_v}.exe",
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("EFS_UPDATE_URL", manifest_path.as_uri())
        res_upd = update_checker.check_for_updates(force=True)
        assert res_upd["has_update"] is True
        assert any("decision=update_available" in ln for ln in log_lines)

        # Subsequent force=False within 24h logs skipped_throttle
        update_checker.check_for_updates(force=False)
        assert any("decision=skipped_throttle" in ln for ln in log_lines)

        # Open Long Video -> sidebar update badge rendered & clicking About shows update check controls
        at = AppTest.from_file(get_long_video_app_file(), default_timeout=60)
        at.run(timeout=60)
        assert not at.exception
        assert any(b.key == "efs_btn_sidebar_update_longform" for b in at.button)

        at.session_state["efs_manual_update_result"] = res_upd
        at.button(key="efs_btn_about_longform").click().run(timeout=20)
        assert not at.exception
        assert any(b.key == "btn_check_updates_about_dialog" for b in at.button)
        assert any(c.key == "chk_auto_update_about_dialog" for c in at.checkbox)
        assert any(b.key == "btn_upd_manual_dl_about_dialog" for b in at.button)
        about_md = "\n".join(m.value for m in at.markdown)
        after_info = "\n".join(i.value for i in at.info)
        assert "· build " in about_md
        assert "Catatan uji E11" in about_md
        assert f"v{_next_v}" in after_info
        assert "Pengaturan" not in after_info and "Settings" not in after_info

        # Skipped version logs decision=skipped_version on force=True
        update_checker.skip_update_version(_next_v)
        update_checker.check_for_updates(force=True)
        assert any("decision=skipped_version" in ln for ln in log_lines)
    finally:
        logger.remove(sink_id)


def test_e12_launcher_build_signature_reuse_and_replace_server(tmp_path, monkeypatch):
    """E12.1/E12.3/E12.4: Same version+build reuses active server; mismatched version/build replaces it (warns on active jobs)."""
    from types import SimpleNamespace
    from app.services.branding import constants
    from app.services.longform import app_paths, launcher_runtime, platform_utils

    data_dir = tmp_path / "data"
    install_dir = tmp_path / "app_root"
    (install_dir / "webui").mkdir(parents=True, exist_ok=True)
    (install_dir / "webui" / "Main.py").write_text("# stub main\n", encoding="utf-8")
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("EFS_INSTALL_ROOT", str(install_dir))
    monkeypatch.setattr(app_paths, "get_data_dir", lambda create=False: data_dir)
    monkeypatch.setattr(app_paths, "get_install_root", lambda: install_dir)
    monkeypatch.setattr(app_paths, "is_installed_mode", lambda: True)

    # Embedded _build_meta.json reading (E12.3)
    meta_file = tmp_path / "_build_meta.json"
    meta_file.write_text(
        json.dumps(
            {
                "git_commit": "abc1234",
                "git_commit_full": "abc1234deadbeef",
                "build_time": "2026-11-20 17:30:00",
                "build_id": "abc1234-20261120-173000",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(constants, "BUILD_META_PATH", meta_file)
    sig = constants.get_app_build_signature()
    assert sig["app_version"] == constants.APP_VERSION
    assert sig["git_commit"] == "abc1234"
    assert sig["build_id"] == "abc1234-20261120-173000"

    opened_urls = []
    terminated_pids = []
    launched_envs = []

    monkeypatch.setattr(platform_utils, "is_pid_alive", lambda pid: True)
    monkeypatch.setattr(launcher_runtime, "is_streamlit_server_healthy", lambda port, host="127.0.0.1", timeout=1.0: True)
    monkeypatch.setattr(platform_utils, "open_url_in_browser", lambda u: opened_urls.append(u) or True)
    monkeypatch.setattr(
        platform_utils,
        "terminate_process_tree",
        lambda pid, force=True, timeout=5.0: terminated_pids.append(pid) or True,
    )
    monkeypatch.setattr(launcher_runtime, "find_free_port", lambda start_port=8501, host="127.0.0.1": 8519)

    def fake_detached(cmd, cwd=None, log_file=None, env=None):
        launched_envs.append(dict(env or {}))
        return SimpleNamespace(pid=99002, poll=lambda: None, returncode=0)

    monkeypatch.setattr(platform_utils, "launch_desktop_app_detached", fake_detached)
    monkeypatch.setattr(launcher_runtime, "list_active_running_jobs", lambda: [])

    # 1. Matching version + build_id -> reuse existing server
    launcher_runtime.write_launcher_lock(
        {
            "pid": 12924,
            "port": 8501,
            "url": "http://127.0.0.1:8501",
            "update_url": "",
            "app_version": sig["app_version"],
            "build_id": sig["build_id"],
        }
    )
    res_reuse = launcher_runtime.launch_or_focus_app(open_browser=False)
    assert res_reuse["reused"] is True
    assert res_reuse["pid"] == 12924
    assert terminated_pids == []

    # 2. Outdated/mismatched build_id -> replace old server cleanly
    launcher_runtime.write_launcher_lock(
        {
            "pid": 12924,
            "port": 8501,
            "url": "http://127.0.0.1:8501",
            "update_url": "",
            "app_version": "1.0.0",
            "build_id": "old0000-20261120-125400",
        }
    )
    res_replace = launcher_runtime.launch_or_focus_app(open_browser=False)
    assert res_replace["reused"] is False
    assert res_replace["pid"] == 99002
    assert 12924 in terminated_pids
    assert launched_envs[-1]["EFS_SERVER_BUILD_ID"] == "abc1234-20261120-173000"
    assert launched_envs[-1]["EFS_SERVER_APP_VERSION"] == sig["app_version"]
    new_lock = launcher_runtime.read_launcher_lock()
    assert new_lock and new_lock["pid"] == 99002 and new_lock["build_id"] == "abc1234-20261120-173000"

    # 3. Mismatched build_id WITH active running job: decline keeps old, accept stops job & replaces
    terminated_pids.clear()
    monkeypatch.setattr(
        launcher_runtime,
        "list_active_running_jobs",
        lambda: [{"project_id": "proj_1", "step": "tts", "pid": 77777}],
    )
    launcher_runtime.write_launcher_lock(
        {
            "pid": 55555,
            "port": 8501,
            "url": "http://127.0.0.1:8501",
            "update_url": "",
            "app_version": "1.0.0",
            "build_id": "older-build",
        }
    )
    res_declined = launcher_runtime.launch_or_focus_app(
        open_browser=False,
        confirm_jobs_callback=lambda jobs: False,
    )
    assert res_declined["reused"] is True and res_declined.get("restart_cancelled") is True
    assert terminated_pids == []

    res_confirmed = launcher_runtime.launch_or_focus_app(
        open_browser=False,
        confirm_jobs_callback=lambda jobs: True,
    )
    assert res_confirmed["reused"] is False and res_confirmed["pid"] == 99002
    assert 77777 in terminated_pids and 55555 in terminated_pids

    log_txt = (data_dir / "logs" / "launcher.log").read_text(encoding="utf-8")
    assert "Reusing active server instance" in log_txt
    assert "Replacing outdated active server (pid=12924" in log_txt
    assert "WARNING: 1 active background job(s) running" in log_txt


def test_r1_release_url_friendly_404_make_latest_json_and_process_leak_guard(tmp_path: Path, monkeypatch):
    """R1.1–R1.6: Production URL, friendly 404 text, make_latest_json valid/invalid checks, leak guard, and release docs."""
    import http.server
    import subprocess
    import sys
    import threading
    import time
    from app.services.branding.constants import APP_VERSION, UPDATE_MANIFEST_URL
    from app.services.longform import first_run, update_checker
    from app.services.longform.ui.i18n import t
    from scripts.build import make_latest_json, verify_package

    # R1.1: Exact production URL + friendly 404 text (id & en) when remote latest.json returns 404
    assert UPDATE_MANIFEST_URL == "https://raw.githubusercontent.com/keimam/EasyFacelessStudio-updates/main/latest.json"
    monkeypatch.delenv("EFS_UPDATE_URL", raising=False)
    assert update_checker.get_update_manifest_url() == UPDATE_MANIFEST_URL

    assert t("update_err_404", lang="id") == "Informasi versi belum tersedia"
    assert t("update_err_404", lang="en") == "Version information is not available yet"

    class _Handler404(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

        def log_message(self, *args):
            return

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler404)
    port = int(srv.server_address[1])
    thr = threading.Thread(target=srv.serve_forever, daemon=True)
    thr.start()
    try:
        data_dir = (tmp_path / "r1_404_data").resolve()
        monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
        first_run.mark_setup_completed(skipped=False)
        monkeypatch.setenv("EFS_UPDATE_URL", f"http://127.0.0.1:{port}/latest.json")
        res_404 = update_checker.check_for_updates(force=True)
        assert res_404["error_kind"] == "http_404"
        assert res_404["has_update"] is False

        # App UI renders normally and displays friendly 404 message in About dialog without crashing
        at = AppTest.from_file(get_long_video_app_file(), default_timeout=60)
        at.session_state["efs_manual_update_result"] = res_404
        at.run(timeout=60)
        assert not at.exception
        at.button(key="efs_btn_about_longform").click().run(timeout=20)
        assert not at.exception
        warnings = "\n".join(w.value for w in at.warning)
        assert "Informasi versi belum tersedia" in warnings
    finally:
        srv.shutdown()
        srv.server_close()

    # R1.2 & R1.7: Member notes separation + valid latest.json (live or prior pre-publish) + rejection of BOM/internal patterns
    repo_root = Path(__file__).resolve().parents[2]
    committed_latest = repo_root / "packaging" / "release" / "latest.json"
    committed_raw = json.loads(committed_latest.read_text(encoding="utf-8"))
    committed_ver = str(committed_raw.get("version") or "").strip()
    assert committed_ver == APP_VERSION or update_checker.is_newer_version(APP_VERSION, committed_ver)
    committed_data = make_latest_json.validate_latest_json_file(committed_latest, expected_version=committed_ver)
    assert (
        committed_data["download_url"] == "GANTI_DENGAN_LINK_GOOGLE_DRIVE"
        or str(committed_data["download_url"]).startswith("https://drive.google.com/")
    )
    assert (repo_root / "packaging" / "release" / "notes" / f"{APP_VERSION}.id.md").is_file()
    assert (repo_root / "packaging" / "release" / "notes" / f"{APP_VERSION}.en.md").is_file()
    cur_notes_id, cur_notes_en = make_latest_json.load_member_release_notes(APP_VERSION)
    assert "EasyFaceless Studio" in cur_notes_id and len(cur_notes_id) >= 30
    assert "EasyFaceless Studio" in cur_notes_en and len(cur_notes_en) >= 30

    # Missing notes files for a version must fail cleanly
    with pytest.raises(FileNotFoundError):
        make_latest_json.load_member_release_notes("9.9.9", notes_dir=tmp_path)

    # Internal stage/task codes ("Tahap", R1.1, E12, D9) must be rejected at generation AND validation
    for bad_note in (
        "Perbaikan Tahap R1 untuk user",
        "Pembaruan URL (R1.1)",
        "Upgrade otomatis E12",
        "Sumber stok D9.7",
    ):
        with pytest.raises(ValueError, match="internal reference"):
            make_latest_json.validate_member_release_note(bad_note, label="notes_id")
        out_bad_internal = tmp_path / "latest_internal.json"
        out_bad_internal.write_text(
            json.dumps({**committed_data, "version": APP_VERSION, "notes_id": bad_note}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="internal reference"):
            make_latest_json.validate_latest_json_file(out_bad_internal, expected_version=APP_VERSION)

    out_ok = tmp_path / "latest_valid.json"
    make_latest_json.write_latest_json(
        download_url="https://drive.google.com/file/d/EXAMPLE/view?usp=sharing",
        output_path=out_ok,
        released="2026-11-20",
    )
    parsed = make_latest_json.validate_latest_json_file(out_ok, expected_version=APP_VERSION)
    assert parsed["version"] == APP_VERSION
    assert not out_ok.read_bytes().startswith(b"\xef\xbb\xbf")

    # Reject empty URL, BOM, invalid JSON, version mismatch
    with pytest.raises(ValueError):
        make_latest_json.build_latest_manifest_payload(download_url="   ")
    out_bom = tmp_path / "latest_bom.json"
    out_bom.write_bytes(b"\xef\xbb\xbf" + out_ok.read_bytes())
    with pytest.raises(ValueError, match="BOM"):
        make_latest_json.validate_latest_json_file(out_bom, expected_version=APP_VERSION)
    out_bad_ver = tmp_path / "latest_bad_ver.json"
    out_bad_ver.write_text(json.dumps({**parsed, "version": "9.9.9"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Version mismatch"):
        make_latest_json.validate_latest_json_file(out_bad_ver, expected_version=APP_VERSION)
    out_bad_json = tmp_path / "latest_bad.json"
    out_bad_json.write_text("{broken json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        make_latest_json.validate_latest_json_file(out_bad_json, expected_version=APP_VERSION)

    # R1.4: Process leak detection & cleanup catches stray process tied to staging directory
    fake_staging = tmp_path / "fake_staging_dir"
    fake_staging.mkdir(parents=True, exist_ok=True)
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(fake_staging)])
    try:
        time.sleep(0.25)
        detected = verify_package.find_staging_processes(fake_staging)
        assert any(int(p["pid"]) == sleeper.pid for p in detected)
        killed = verify_package.cleanup_staging_processes(fake_staging)
        assert sleeper.pid in killed
        sleeper.wait(timeout=5)
        assert verify_package.find_staging_processes(fake_staging) == []
    finally:
        verify_package._stop_popen(sleeper)

    # R1.3 & R1.6: Release documentation requirements
    faq_id = (repo_root / "docs" / "user" / "id" / "faq-troubleshooting.md").read_text(encoding="utf-8")
    faq_en = (repo_root / "docs" / "user" / "en" / "faq-troubleshooting.md").read_text(encoding="utf-8")
    assert "Google Drive" in faq_id and "Get-FileHash" in faq_id and "SmartScreen" in faq_id
    assert "Google Drive" in faq_en and "Get-FileHash" in faq_en and "SmartScreen" in faq_en
    release_doc = (repo_root / "docs" / "dev" / "RELEASE.md").read_text(encoding="utf-8")
    assert "packaging/release/notes/" in release_doc
    clean_test_doc = (repo_root / "docs" / "dev" / "CLEAN_INSTALL_TEST.md").read_text(encoding="utf-8")
    numbered_steps = [ln for ln in clean_test_doc.splitlines() if ln.strip() and ln.strip()[0].isdigit() and ". " in ln[:5]]
    assert 8 <= len(numbered_steps) <= 15


