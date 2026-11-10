import json
import subprocess
import pytest
from pathlib import Path

from app.services.longform.presets import (
    get_preset,
    get_preset_directories,
    get_preset_override_path,
    has_preset_override,
    get_all_preset_overrides,
    format_preset_tree,
    list_presets,
    load_preset_file,
    validate_preset_file,
)
from app.services.longform.capcut.service import set_capcut_text_size
from app.services.longform.store import create_project, get_project_dir, load_manifest, refresh_project_preset


def test_preset_override_layer(tmp_path, monkeypatch):
    preset_id = "automotive.classic_restoration"
    _, storage_dir = get_preset_directories()
    override_file = get_preset_override_path(preset_id)

    # Ensure cleanup before and after
    if override_file.is_file():
        override_file.unlink()

    try:
        # 1. Base preset initial state
        initial_preset = get_preset(preset_id)
        assert not has_preset_override(preset_id)
        before_status = subprocess.run(["git", "status", "--porcelain", "resource/"], capture_output=True, text=True).stdout

        # 2. Apply override via set_capcut_text_size
        target_path = set_capcut_text_size(preset_id, 12)
        assert target_path == override_file
        assert override_file.is_file()

        # Verify resource/ is completely untouched by checking git status before vs after
        after_status = subprocess.run(["git", "status", "--porcelain", "resource/"], capture_output=True, text=True).stdout
        assert before_status == after_status, f"resource/ directory was modified by set_capcut_text_size: {after_status}"

        # 3. Verify override is detected and loaded
        assert has_preset_override(preset_id)
        all_ovrs = get_all_preset_overrides()
        assert preset_id in all_ovrs

        updated_preset = get_preset(preset_id)
        assert updated_preset.capcut_text_size == 12

        # 4. Verify format_preset_tree displays override badge
        tree = format_preset_tree()
        assert f"{preset_id} [override]" in tree or f"{preset_id} [OVERRIDE]" in tree

        # 5. Verify validation of preset file reports override
        resource_dir, _ = get_preset_directories()
        base_file = resource_dir / f"{preset_id}.json"
        is_valid, err = validate_preset_file(base_file)
        assert is_valid, f"Validation failed: {err}"

        # 6. Verify project snapshot and refresh-preset workflow
        test_pid = f"test_proj_ovr_{tmp_path.name}"
        manifest, proj_dir = create_project(
            project_id=test_pid,
            title="Test Override Project",
            preset_id=preset_id,
        )

        # Initial snapshot matches current updated preset
        assert manifest.preset_snapshot["capcut_text_size"] == 12

        # Now change override again to 18
        set_capcut_text_size(preset_id, 18)
        current_preset = get_preset(preset_id)
        assert current_preset.capcut_text_size == 18

        # Project snapshot should NOT auto-update
        m_after = load_manifest(proj_dir)
        assert m_after.preset_snapshot["capcut_text_size"] == 12
        assert m_after.preset_snapshot != current_preset.model_dump()

        # Running refresh_project_preset updates the snapshot
        refreshed_m, details = refresh_project_preset(proj_dir)
        assert details["snapshot_updated"] is True
        assert refreshed_m.preset_snapshot["capcut_text_size"] == 18
        assert refreshed_m.preset_snapshot == current_preset.model_dump()

    finally:
        if override_file.is_file():
            override_file.unlink()
        try:
            import shutil
            p_dir = get_project_dir(f"test_proj_ovr_{tmp_path.name}")
            if p_dir.is_dir():
                shutil.rmtree(p_dir, ignore_errors=True)
        except Exception:
            pass
