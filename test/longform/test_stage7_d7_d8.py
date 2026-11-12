"""Tests for Stage 7A.3 D7 (MPT-style Project Creation Form & Preset umum) and D8 (Custom Niche/Sub-Niche)."""

import json
from pathlib import Path
import pytest

from app.services.longform.presets import (
    PresetError,
    PresetValidationError,
    delete_custom_preset,
    format_preset_tree,
    get_builtin_presets,
    get_custom_presets,
    get_preset,
    get_preset_directories,
    list_presets,
    save_custom_preset,
    suggest_niche_keywords,
)
from app.services.longform.schema import ItemStatus, ProjectManifest
from app.services.longform.store import create_project, get_projects_root, load_manifest


def test_d7_umum_preset_exists_and_extends_documentary():
    """Verify built-in 'umum.id' and 'umum.en' presets exist and extend their documentary parents (H1)."""
    preset_id = get_preset("umum.id")
    assert preset_id.id == "umum.id"
    assert preset_id.name == "Umum (Dokumenter Naratif)"
    assert preset_id.extends == "documentary_id"
    assert preset_id.language == "id-ID"
    assert preset_id.capcut_text_size == 8

    preset_en = get_preset("umum.en")
    assert preset_en.id == "umum.en"
    assert preset_en.name == "General Documentary"
    assert preset_en.extends == "documentary_en"
    assert preset_en.language == "en-US"
    assert preset_en.capcut_text_size == 8


def test_d7_create_project_with_only_topic(tmp_path):
    """Verify creating project with only topic defaults to umum.id preset, saves topic in manifest (D7/H1)."""
    manifest, p_dir = create_project(
        project_id="test_mpt_topic",
        title="Sejarah Singkat AI",
        topic="Sejarah Singkat AI",
        target_duration_minutes=15,
        preset_id="umum",
        projects_root=tmp_path,
    )

    assert manifest.project_id == "test_mpt_topic"
    assert manifest.title == "Sejarah Singkat AI"
    assert manifest.topic == "Sejarah Singkat AI"
    assert manifest.target_duration_minutes == 15
    assert manifest.preset_id == "umum.id"
    assert manifest.aspect_ratio == "16:9"

    # Verify manifest file on disk
    loaded = load_manifest(p_dir)
    assert loaded.topic == "Sejarah Singkat AI"
    assert loaded.target_duration_minutes == 15


def test_d7_create_project_umum_en_us_adaptation(tmp_path):
    """Verify language en-US with umum preset adapts to umum.en preset, en-US voice, and 140 WPM (D7/H1)."""
    manifest, _ = create_project(
        project_id="test_mpt_en",
        title="History of Computing",
        topic="History of Computing",
        preset_id="umum",
        language="en-US",
        projects_root=tmp_path,
    )

    assert manifest.preset_id == "umum.en"
    assert manifest.preset_snapshot["language"] == "en-US"
    assert manifest.preset_snapshot["default_voice"] == "en-US-ChristopherNeural"
    assert manifest.preset_snapshot["words_per_minute"] == 140


def test_d8_custom_preset_save_and_retrieve():
    """Verify custom preset is saved to storage/presets/ with custom. prefix and extends parent (D8)."""
    resource_dir, storage_dir = get_preset_directories()

    custom_data = {
        "id": "custom.cyberpunk",
        "name": "Cyberpunk Dystopia",
        "schema_version": "1.0.0",
        "kind": "niche",
        "extends": "umum",
        "language": "id-ID",
        "+topic_keywords": ["neon", "megacorporation", "cyberware"],
        "+visual_keywords": ["neon rain footage", "cyberpunk street"],
        "capcut_text_size": 8,
    }

    saved_path = save_custom_preset(custom_data, allow_overwrite=True)
    try:
        assert saved_path.is_file()
        assert storage_dir in saved_path.parents
        assert resource_dir not in saved_path.parents

        # Retrieve via get_preset
        preset = get_preset("custom.cyberpunk")
        assert preset.id == "custom.cyberpunk"
        assert preset.name == "Cyberpunk Dystopia"
        assert preset.extends == "umum"
        assert "neon" in preset.topic_keywords

        # Check get_custom_presets includes it
        custom_map = get_custom_presets()
        assert "custom.cyberpunk" in custom_map

        # Check format_preset_tree labels it with [kustom]
        tree = format_preset_tree()
        assert "custom.cyberpunk [kustom]" in tree
    finally:
        if saved_path.is_file():
            saved_path.unlink()


def test_d8_custom_preset_duplicate_and_builtin_collision():
    """Verify duplicate IDs and collision with built-in presets are rejected with clear errors (D8)."""
    # 1. Collision with built-in
    collision_data = {
        "id": "automotive",
        "name": "Collision Niche",
        "kind": "niche",
    }
    with pytest.raises(PresetValidationError, match="bertabrakan dengan preset bawaan"):
        save_custom_preset(collision_data)

    # 2. Duplicate rejection without allow_overwrite
    custom_data = {
        "id": "custom.duplicate_test",
        "name": "Duplicate Test",
        "kind": "niche",
        "extends": "umum",
    }
    saved_path = save_custom_preset(custom_data, allow_overwrite=True)
    try:
        with pytest.raises(PresetValidationError, match="sudah ada"):
            save_custom_preset(custom_data, allow_overwrite=False)
    finally:
        if saved_path.is_file():
            saved_path.unlink()


def test_d8_delete_guard_when_used_by_project(tmp_path, monkeypatch):
    """Verify custom preset deletion is rejected if used by any project, naming the project (D8)."""
    custom_data = {
        "id": "custom.in_use_niche",
        "name": "In Use Niche",
        "kind": "niche",
        "extends": "umum",
    }
    saved_path = save_custom_preset(custom_data, allow_overwrite=True)

    try:
        # Create a project using this custom preset
        from app.services.longform import store
        monkeypatch.setattr(store, "get_projects_root", lambda: tmp_path)

        manifest, _ = create_project(
            project_id="proj_using_custom",
            title="Active Custom Project",
            preset_id="custom.in_use_niche",
            projects_root=tmp_path,
        )

        # Attempt to delete custom preset: must raise PresetError naming the project
        with pytest.raises(PresetError, match="Active Custom Project") as exc_info:
            delete_custom_preset("custom.in_use_niche")
        assert "proj_using_custom" in str(exc_info.value)

        # Deleting a built-in preset is also rejected
        with pytest.raises(PresetError, match="preset bawaan"):
            delete_custom_preset("documentary_id")

    finally:
        if saved_path.is_file():
            saved_path.unlink()


def test_d8_suggest_niche_keywords_heuristic():
    """Verify keyword proposal generates meaningful keywords from niche and topic words without LLM."""
    res = suggest_niche_keywords(
        niche_name="Penerbangan Tempur Militer",
        topic="Jet Tempur Supersonik Perang Dingin",
        use_llm=False,
    )
    assert "topic_keywords" in res
    assert "visual_keywords" in res
    assert "required_subject_terms" in res
    assert "negative_terms" in res
    assert "blocked_terms" in res

    # Check words from niche/topic appear in keywords
    flat_kw = " ".join(res["topic_keywords"] + res["visual_keywords"])
    assert "tempur" in flat_kw or "militer" in flat_kw or "supersonik" in flat_kw
