"""Unit tests for Stage 7A.3 Part E minor fixes (E1–E5)."""

from pathlib import Path
import pytest

from app.services.longform.presets import get_preset
from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    ItemStatus,
    ProjectManifest,
    TtsGroup,
)
from app.services.longform.script_import import clean_chapter_title, parse_markdown_script
from app.services.longform.status import get_impactful_preset_diffs
from app.services.longform.store import (
    create_project,
    get_projects_root,
    load_manifest,
    request_job_stop,
    clear_stop_request,
)
from app.services.longform.ui.security import check_llm_status, mask_api_key
from app.services.longform.render.chapter_render import ChapterRenderer
from app.services.longform.render.service import RenderService


def test_e1_repeated_chapter_prefix_cleaning(tmp_path):
    """Verify E1 strips repeated 'Chapter 1: Chapter 1:' prefixes and preserves manifest."""
    assert clean_chapter_title("Chapter 1: Chapter 1: Detroit Legend") == "Detroit Legend"
    assert clean_chapter_title("Bab 2: Bab 2 - V8 Engine") == "V8 Engine"
    assert clean_chapter_title("Ch. 3: Ch 3. Aero") == "Aero"

    # Verify when loaded through store.py:load_manifest
    manifest, p_dir = create_project(
        project_id="test_e1_proj",
        title="Test E1 Project",
        preset_id="documentary_en",
        projects_root=tmp_path,
    )

    # Artificially inject double chapter title in manifest file
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Chapter 1: Chapter 1: Foundations of Land",
            tts_groups=[
                TtsGroup(group_index=1, paragraph_text="Paragraph 1 text.")
            ],
            status=ItemStatus.pending,
        )
    ]
    from app.services.longform.store import save_manifest
    save_manifest(manifest, p_dir)

    loaded = load_manifest(p_dir)
    assert loaded.chapters[0].chapter_title == "Foundations of Land"


def test_e2_preset_diff_default_equivalence():
    """Verify E2 treats None vs effective default values as identical."""
    snapshot = {
        "voice_rate": None,
        "pause_between_groups_ms": None,
        "min_relevance_score": None,
    }
    current = {
        "voice_rate": 1.0,
        "pause_between_groups_ms": 350,
        "min_relevance_score": 0.6,
    }
    diffs = get_impactful_preset_diffs(snapshot, current)
    assert len(diffs) == 0

    # True change should still be flagged
    changed_current = {
        "voice_rate": 1.25,
        "pause_between_groups_ms": 350,
        "min_relevance_score": 0.6,
    }
    diffs_changed = get_impactful_preset_diffs(snapshot, changed_current)
    assert len(diffs_changed) == 1
    assert diffs_changed[0]["field"] == "voice_rate"
    assert diffs_changed[0]["current_val"] == 1.25


def test_e3_muscle_car_preset_capcut_text_size():
    """Verify E3 automotive.muscle_car has capcut_text_size == 8."""
    preset = get_preset("automotive.muscle_car")
    assert preset.capcut_text_size == 8


def test_e4_render_stop_marker_check(tmp_path):
    """Verify E4 halts rendering when stop marker exists."""
    manifest, p_dir = create_project(
        project_id="test_e4_proj",
        title="Test E4 Project",
        preset_id="documentary_en",
        projects_root=tmp_path,
    )
    manifest.chapters = [
        Chapter(chapter_index=1, chapter_title="Chapter 1", status=ItemStatus.pending)
    ]

    renderer = ChapterRenderer(p_dir)
    request_job_stop(p_dir)

    with pytest.raises(InterruptedError, match="stop requested"):
        renderer.render_chapter(manifest, 1)

    # Test render_all breaks early
    svc = RenderService(p_dir)
    _, summary = svc.render_all(manifest)
    assert summary["chapters_rendered"] == 0
    assert summary["final_stats"].get("status") == "stopped"

    clear_stop_request(p_dir)


def test_e5_api_key_masking_and_gemini_validation(monkeypatch):
    """Verify E5 masks keys as ****abcd and validates Gemini AI Studio key prefix."""
    assert mask_api_key("sk-1234567890abcdef") == "****cdef"
    assert mask_api_key("AIzaSyD9876543210wxyz") == "****wxyz"
    assert mask_api_key("short") == "****"

    from app.config import config
    # Test invalid Gemini key (does not start with AIza)
    monkeypatch.setitem(config.app, "llm_provider", "gemini")
    monkeypatch.setitem(config.app, "gemini_api_key", "invalid_random_key_1234")

    status = check_llm_status()
    assert status["configured"] is False
    assert status.get("is_invalid") is True
    assert "Key Gemini tampaknya tidak valid" in status["message"]

    # Test valid Gemini key
    monkeypatch.setitem(config.app, "gemini_api_key", "AIzaSyValidStudioKey999")
    status_valid = check_llm_status()
    assert status_valid["configured"] is True
    assert status_valid["masked_key"] == "****y999"
