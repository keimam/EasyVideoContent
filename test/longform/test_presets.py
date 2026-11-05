"""Tests for Preset loading, validation, BOM support, multi-tier inheritance, and storage overrides (B1-B6)."""

import json
from pathlib import Path
import pytest

from app.services.longform.presets import (
    PresetError,
    PresetNotFoundError,
    PresetValidationError,
    deep_merge_dicts,
    format_preset_tree,
    get_preset,
    get_preset_inheritance_chain,
    list_presets,
    load_preset_file,
    validate_preset_file,
)
from app.services.longform.schema import Preset


def test_builtin_presets_load_successfully():
    """Verify all built-in presets load, resolve inheritance, and validate properly."""
    presets = list_presets()
    assert "documentary_en" in presets
    assert "documentary_id" in presets
    assert "automotive" in presets
    assert "automotive.muscle_car" in presets
    assert "automotive.jdm" in presets
    assert "automotive.supercar" in presets
    assert "automotive.classic_restoration" in presets

    en = get_preset("documentary_en")
    assert en.language == "en-US"
    assert en.words_per_minute == 140
    assert en.kind == "niche"

    muscle = get_preset("automotive.muscle_car")
    assert muscle.kind == "sub_niche"
    assert muscle.extends == "automotive"
    # Appended keywords
    assert "big block V8" in muscle.topic_keywords
    assert "horsepower" in muscle.topic_keywords  # inherited from automotive
    assert "hood scoop shaker" in muscle.visual_keywords
    assert "tachometer needle redline" in muscle.visual_keywords  # inherited


def test_preset_bom_handling(tmp_path: Path):
    """Verify that presets saved with UTF-8 BOM are loaded seamlessly."""
    bom_file = tmp_path / "bom_preset.json"
    data = {
        "id": "bom_test",
        "name": "BOM Test Preset",
        "schema_version": "1.0.0",
        "kind": "niche",
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
    with open(bom_file, "w", encoding="utf-8-sig") as fp:
        json.dump(data, fp)

    loaded = load_preset_file(bom_file)
    assert loaded.id == "bom_test"
    assert loaded.name == "BOM Test Preset"


def test_preset_validation_error_cites_file_and_field(tmp_path: Path):
    """Verify PresetValidationError includes filename and exact missing/invalid field."""
    invalid_file = tmp_path / "invalid_preset.json"
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


def test_preset_inheritance_simple_and_chain(tmp_path: Path, monkeypatch):
    """Verify multi-tier inheritance (3 levels: A -> B -> C) (B1, B6)."""
    storage_dir = tmp_path / "storage_presets"
    storage_dir.mkdir(parents=True, exist_ok=True)

    # Level 1: Grandparent
    level1 = {
        "id": "tier1",
        "name": "Tier 1 Root",
        "schema_version": "1.0.0",
        "kind": "niche",
        "language": "en-US",
        "audience": "General",
        "narration_style": "Broad style",
        "chapter_structure": ["Ch1", "Ch2"],
        "hook_rules": "Hook 1",
        "words_per_minute": 130,
        "subtitle_style": {"font_size": 18, "font_name": "Arial"},
        "visual_style": "Natural",
        "image_prompt_template": {"prefix": "photo of "},
        "visual_source_mix": {"ai_image": 0.5},
        "default_voice": "en-US-ChristopherNeural",
        "default_bgm": {"volume": 0.2},
        "ken_burns_style": "alternating",
        "topic_keywords": ["root_kw1", "root_kw2"],
        "visual_keywords": ["root_vis1"],
    }
    with open(storage_dir / "tier1.json", "w", encoding="utf-8") as fp:
        json.dump(level1, fp)

    # Level 2: Parent extending tier1
    level2 = {
        "id": "tier2",
        "name": "Tier 2 Middle",
        "schema_version": "1.0.0",
        "kind": "sub_niche",
        "extends": "tier1",
        "words_per_minute": 140,
        "subtitle_style": {"font_size": 22},  # partial dict override
        "+topic_keywords": ["mid_kw1"],  # append to list
    }
    with open(storage_dir / "tier2.json", "w", encoding="utf-8") as fp:
        json.dump(level2, fp)

    # Level 3: Child extending tier2
    level3 = {
        "id": "tier3",
        "name": "Tier 3 Leaf",
        "schema_version": "1.0.0",
        "kind": "sub_niche",
        "extends": "tier2",
        "hook_rules": "Specialized Leaf Hook",
        "+topic_keywords": ["leaf_kw1"],
    }
    with open(storage_dir / "tier3.json", "w", encoding="utf-8") as fp:
        json.dump(level3, fp)

    from app.services.longform import presets
    repo_root = Path(__file__).resolve().parent.parent.parent
    builtin_dir = repo_root / "resource" / "presets"
    monkeypatch.setattr(presets, "get_preset_directories", lambda: (builtin_dir, storage_dir))

    p3 = presets.get_preset("tier3")
    assert p3.name == "Tier 3 Leaf"
    assert p3.words_per_minute == 140  # inherited from tier2
    assert p3.language == "en-US"  # inherited from tier1
    assert p3.hook_rules == "Specialized Leaf Hook"
    assert p3.subtitle_style["font_size"] == 22  # overridden in tier2
    assert p3.subtitle_style["font_name"] == "Arial"  # inherited from tier1
    # 3-level list append: root + mid + leaf
    assert p3.topic_keywords == ["root_kw1", "root_kw2", "mid_kw1", "leaf_kw1"]

    chain = presets.get_preset_inheritance_chain("tier3")
    assert chain == ["tier1", "tier2", "tier3"]


def test_preset_inheritance_cycle_detected(tmp_path: Path, monkeypatch):
    """Verify that inheritance cycles (A -> B -> A) are rejected with clear error (B1)."""
    storage_dir = tmp_path / "storage_cycle"
    storage_dir.mkdir(parents=True, exist_ok=True)

    cycle_a = {"id": "cycle_a", "extends": "cycle_b", "name": "A"}
    cycle_b = {"id": "cycle_b", "extends": "cycle_a", "name": "B"}

    with open(storage_dir / "cycle_a.json", "w", encoding="utf-8") as fp:
        json.dump(cycle_a, fp)
    with open(storage_dir / "cycle_b.json", "w", encoding="utf-8") as fp:
        json.dump(cycle_b, fp)

    from app.services.longform import presets
    repo_root = Path(__file__).resolve().parent.parent.parent
    builtin_dir = repo_root / "resource" / "presets"
    monkeypatch.setattr(presets, "get_preset_directories", lambda: (builtin_dir, storage_dir))

    with pytest.raises(PresetValidationError) as exc_info:
        presets.list_presets()

    assert "cycle detected" in str(exc_info.value).lower()


def test_preset_missing_parent_rejected(tmp_path: Path, monkeypatch):
    """Verify extending an unknown/non-existent parent is rejected with clear error (B1)."""
    storage_dir = tmp_path / "storage_missing"
    storage_dir.mkdir(parents=True, exist_ok=True)

    orphan = {"id": "orphan", "extends": "ghost_parent_xyz", "name": "Orphan"}
    with open(storage_dir / "orphan.json", "w", encoding="utf-8") as fp:
        json.dump(orphan, fp)

    from app.services.longform import presets
    repo_root = Path(__file__).resolve().parent.parent.parent
    builtin_dir = repo_root / "resource" / "presets"
    monkeypatch.setattr(presets, "get_preset_directories", lambda: (builtin_dir, storage_dir))

    with pytest.raises(PresetValidationError) as exc_info:
        presets.list_presets()

    err = str(exc_info.value)
    assert "ghost_parent_xyz" in err
    assert "orphan" in err


def test_preset_deep_merge_dicts_and_append():
    """Verify deep_merge_dicts logic: dict recursive merge, list replace vs append."""
    parent = {
        "title": "Old",
        "sub": {"a": 1, "b": 2},
        "tags": ["alpha", "beta"],
        "keep": ["x"],
    }
    child = {
        "title": "New",
        "sub": {"b": 99, "c": 3},
        "+tags": ["gamma"],  # append
        "keep": ["y"],  # replace
    }
    merged = deep_merge_dicts(parent, child)
    assert merged["title"] == "New"
    assert merged["sub"] == {"a": 1, "b": 99, "c": 3}
    assert merged["tags"] == ["alpha", "beta", "gamma"]
    assert merged["keep"] == ["y"]
    assert "+tags" not in merged


def test_format_preset_tree():
    """Verify format_preset_tree generates expected ASCII tree representation."""
    tree_str = format_preset_tree()
    assert "automotive" in tree_str
    assert "automotive.muscle_car" in tree_str
    assert "documentary_en" in tree_str
    assert "├── " in tree_str or "└── " in tree_str
