"""Tests for Preset loading, validation, BOM support, and storage overrides."""

import json
from pathlib import Path
import pytest

from app.services.longform.presets import (
    PresetError,
    PresetNotFoundError,
    PresetValidationError,
    get_preset,
    list_presets,
    load_preset_file,
    validate_preset_file,
)
from app.services.longform.schema import Preset


def test_builtin_presets_load_successfully():
    """Verify all built-in presets load and validate properly."""
    presets = list_presets()
    assert "documentary_en" in presets
    assert "documentary_id" in presets

    en = get_preset("documentary_en")
    assert en.language == "en-US"
    assert en.words_per_minute == 140
    assert en.schema_version == "1.0.0"

    id_preset = get_preset("documentary_id")
    assert id_preset.language == "id-ID"
    assert id_preset.words_per_minute == 130


def test_preset_bom_handling(tmp_path: Path):
    """Verify that presets saved with UTF-8 BOM are loaded seamlessly."""
    bom_file = tmp_path / "bom_preset.json"
    data = {
        "id": "bom_test",
        "name": "BOM Test Preset",
        "schema_version": "1.0.0",
        "language": "en-US",
        "audience": "Test Audience",
        "narration_style": "Test Style",
        "chapter_structure": ["Intro", "Outro"],
        "hook_rules": "Hook within 10s",
        "words_per_minute": 150,
        "subtitle_style": {},
        "visual_style": "Realistic",
        "image_prompt_template": {},
        "visual_source_mix": {"ai_image": 1.0},
        "default_voice": "en-US-ChristopherNeural",
        "default_bgm": {},
        "ken_burns_style": "alternating",
    }
    # Write with utf-8-sig (BOM)
    with open(bom_file, "w", encoding="utf-8-sig") as fp:
        json.dump(data, fp)

    loaded = load_preset_file(bom_file)
    assert loaded.id == "bom_test"
    assert loaded.name == "BOM Test Preset"


def test_preset_validation_error_cites_file_and_field(tmp_path: Path):
    """Verify PresetValidationError includes filename and exact missing/invalid field."""
    invalid_file = tmp_path / "invalid_preset.json"
    # Missing required field 'default_voice'
    data = {
        "id": "invalid_test",
        "name": "Invalid Preset",
        "schema_version": "1.0.0",
        "language": "en-US",
        "audience": "Test",
        "narration_style": "Test",
        "chapter_structure": ["Intro"],
        "hook_rules": "Test",
        "words_per_minute": 150,
        "subtitle_style": {},
        "visual_style": "Realistic",
        "image_prompt_template": {},
        "visual_source_mix": {},
        # Missing 'default_voice'
        "default_bgm": {},
        "ken_burns_style": "alternating",
    }
    with open(invalid_file, "w", encoding="utf-8") as fp:
        json.dump(data, fp)

    with pytest.raises(PresetValidationError) as exc_info:
        load_preset_file(invalid_file)

    err_msg = str(exc_info.value)
    assert "invalid_preset.json" in err_msg
    assert "default_voice" in err_msg


def test_preset_forbids_unknown_fields(tmp_path: Path):
    """Verify that unknown extra fields are rejected according to schema."""
    unknown_file = tmp_path / "extra_fields.json"
    data = {
        "id": "extra_test",
        "name": "Extra Test",
        "schema_version": "1.0.0",
        "language": "en-US",
        "audience": "Test",
        "narration_style": "Test",
        "chapter_structure": ["Intro"],
        "hook_rules": "Test",
        "words_per_minute": 150,
        "subtitle_style": {},
        "visual_style": "Realistic",
        "image_prompt_template": {},
        "visual_source_mix": {},
        "default_voice": "en-US-ChristopherNeural",
        "default_bgm": {},
        "ken_burns_style": "alternating",
        "unknown_extra_field": "disallowed",
    }
    with open(unknown_file, "w", encoding="utf-8") as fp:
        json.dump(data, fp)

    with pytest.raises(PresetValidationError) as exc_info:
        load_preset_file(unknown_file)

    err_msg = str(exc_info.value)
    assert "extra_fields.json" in err_msg
    assert "unknown_extra_field" in err_msg


def test_storage_preset_overrides_resource_preset(monkeypatch, tmp_path: Path):
    """Verify that a preset in storage/presets overrides resource/presets with the same ID."""
    storage_dir = tmp_path / "storage_presets"
    storage_dir.mkdir(parents=True, exist_ok=True)

    override_file = storage_dir / "documentary_en.json"
    data = {
        "id": "documentary_en",
        "name": "Overridden Custom Documentary",
        "schema_version": "1.0.0",
        "language": "en-GB",
        "audience": "Custom Audience",
        "narration_style": "Custom Style",
        "chapter_structure": ["Part 1", "Part 2"],
        "hook_rules": "Custom Hook",
        "words_per_minute": 160,
        "subtitle_style": {},
        "visual_style": "Custom Visual",
        "image_prompt_template": {},
        "visual_source_mix": {"ai_image": 1.0},
        "default_voice": "en-GB-RyanNeural",
        "default_bgm": {},
        "ken_burns_style": "static",
    }
    with open(override_file, "w", encoding="utf-8") as fp:
        json.dump(data, fp)

    from app.services.longform import presets

    repo_root = Path(__file__).resolve().parent.parent.parent
    builtin_dir = repo_root / "resource" / "presets"

    monkeypatch.setattr(presets, "get_preset_directories", lambda: (builtin_dir, storage_dir))

    loaded_presets = presets.list_presets()
    assert loaded_presets["documentary_en"].name == "Overridden Custom Documentary"
    assert loaded_presets["documentary_en"].language == "en-GB"
    assert loaded_presets["documentary_en"].words_per_minute == 160


def test_preset_not_found():
    """Verify requesting non-existent preset raises PresetNotFoundError."""
    with pytest.raises(PresetNotFoundError) as exc_info:
        get_preset("non_existent_preset_id_xyz")
    assert "non_existent_preset_id_xyz" in str(exc_info.value)
