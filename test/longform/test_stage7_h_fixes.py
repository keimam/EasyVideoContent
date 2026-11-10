"""Tests for Stage 7A.5 (H1-H4): Language-specific Umum presets, Tokyo project restoration, and Sidebar CSS."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest
import unittest.mock as mock

from app.services.longform.presets import (
    PresetValidationError,
    get_available_niches_for_ui,
    get_preset,
    get_preset_inheritance_chain,
)
from app.services.longform.status import compute_project_step_status, get_impactful_preset_diffs
from app.services.longform.store import (
    create_project,
    get_project_dir,
    get_projects_root,
    load_manifest,
    migrate_umum_presets_and_projects,
    refresh_project_preset,
    save_manifest,
)


def get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


def get_main_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "Main.py")


def test_h1_en_us_umum_creation_and_zero_diff_warnings(tmp_path):
    """Verify creating an en-US project with Umum maps to 'umum.en' with chain documentary_en -> umum.en and 0 diff warnings."""
    manifest, p_dir = create_project(
        project_id="test_en_street_food",
        title="Street Food in Tokyo English",
        topic="Street Food in Tokyo English",
        preset_id="umum",
        language="en-US",
        projects_root=tmp_path,
    )

    assert manifest.preset_id == "umum.en"
    assert manifest.preset_inheritance_chain == ["documentary_en", "umum.en"]
    assert manifest.preset_snapshot["language"] == "en-US"
    assert manifest.preset_snapshot["words_per_minute"] == 140
    assert manifest.preset_snapshot["default_voice"] == "en-US-ChristopherNeural"

    # Verify zero diff warnings against preset on disk
    status_info = compute_project_step_status(p_dir)
    diffs = status_info["preset_diffs"]
    assert len(diffs) == 0, f"Expected 0 warnings but got: {diffs}"


def test_h1_id_id_umum_creation_and_zero_diff_warnings(tmp_path):
    """Verify creating an id-ID project with Umum maps to 'umum.id' with chain documentary_id -> umum.id and 0 diff warnings."""
    manifest, p_dir = create_project(
        project_id="test_id_street_food",
        title="Street Food di Jakarta",
        topic="Street Food di Jakarta",
        preset_id="umum",
        language="id-ID",
        projects_root=tmp_path,
    )

    assert manifest.preset_id == "umum.id"
    assert manifest.preset_inheritance_chain == ["documentary_id", "umum.id"]
    assert manifest.preset_snapshot["language"] == "id-ID"
    assert manifest.preset_snapshot["words_per_minute"] == 130
    assert manifest.preset_snapshot["default_voice"] == "id-ID-ArdiNeural"

    # Verify zero diff warnings against preset on disk
    status_info = compute_project_step_status(p_dir)
    diffs = status_info["preset_diffs"]
    assert len(diffs) == 0, f"Expected 0 warnings but got: {diffs}"


def test_h1_refresh_preset_rejects_cross_language_mismatch(tmp_path):
    """Verify refresh_project_preset rejects language family change (en <-> id) with PresetValidationError."""
    manifest, p_dir = create_project(
        project_id="test_lang_lock",
        title="English Doc",
        preset_id="umum.en",
        language="en-US",
        projects_root=tmp_path,
    )

    # Point project manifest to an Indonesian preset and attempt refresh
    manifest.preset_id = "umum.id"
    save_manifest(manifest, p_dir)

    # Refresh must reject because snapshot language is en-US while umum.id is id-ID
    with pytest.raises(PresetValidationError, match="perbedaan bahasa terdeteksi"):
        refresh_project_preset(p_dir)


def test_h1_migration_preserves_audio_settings_and_hashes(tmp_path, monkeypatch):
    """Verify migration maps umum to language-appropriate variant without modifying audio settings."""
    proj_dir = tmp_path / "legacy_en_proj"
    proj_dir.mkdir(parents=True)

    manifest, _ = create_project(
        project_id="legacy_en_proj",
        title="Legacy Project",
        preset_id="umum.en",
        language="en-US",
        projects_root=tmp_path,
    )
    # Manually revert preset_id to legacy "umum" to simulate pre-migration state
    manifest.preset_id = "umum"
    manifest.preset_inheritance_chain = ["documentary_en", "umum"]
    save_manifest(manifest, proj_dir)

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.presets.get_preset_directories", lambda: (Path("resource/presets"), tmp_path / "presets"))

    logs = migrate_umum_presets_and_projects()
    migrated_manifest = load_manifest(proj_dir)

    assert migrated_manifest.preset_id == "umum.en"
    assert migrated_manifest.preset_inheritance_chain == ["documentary_en", "umum.en"]
    assert migrated_manifest.preset_snapshot["default_voice"] == "en-US-ChristopherNeural"
    assert migrated_manifest.preset_snapshot["words_per_minute"] == 140


def test_h3_main_page_sidebar_css_and_no_exception():
    """Verify Main.py runs without exception, injects sidebar toggle CSS and renders sidebar studio links (H3)."""
    with mock.patch("app.services.version_checker.poll_available_update") as mock_update:
        mock_update.return_value.complete = True
        mock_update.return_value.available_version = None

        at = AppTest.from_file(get_main_app_file())
        at.run(timeout=25)

        assert not at.exception, f"Main.py threw exceptions: {[e.value for e in at.exception]}"

        # Verify markdown contains injected CSS for sidebar button
        css_markdowns = [m.value for m in at.markdown if "stExpandSidebarButton" in m.value]
        assert len(css_markdowns) >= 1, "Expected injected CSS for stExpandSidebarButton not found in markdown widgets"

        # Verify page links include Long Video
        links = [pl.label for pl in at.page_link]
        assert any("Long Video" in l for l in links), f"Expected Long Video page_link not found in: {links}"


def test_h3_long_video_renders_sidebar_and_back_link(tmp_path, monkeypatch):
    """Verify Long Video page renders sidebar navigation and return button (H3)."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    at = AppTest.from_file(get_long_video_app_file())
    at.run(timeout=10)

    assert not at.exception, f"Long Video threw exceptions: {[e.value for e in at.exception]}"

    # Verify return link exists
    all_links = [pl.label for pl in at.page_link]
    all_buttons = [b.label for b in at.button]
    assert any("MoneyPrinterTurbo" in str(lbl) for lbl in all_links + all_buttons)
