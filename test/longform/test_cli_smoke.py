"""Smoke tests for all CLI longform commands (F2).

Executes every single CLI subcommand on a test project with the deterministic
fake provider and verifies zero crashes, zero tracebacks, and exit code 0.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLI_PATH = REPO_ROOT / "cli_longform.py"


@pytest.fixture
def temp_project_id():
    """Generate a unique test project ID and ensure clean teardown."""
    proj_id = f"smoke_test_{uuid.uuid4().hex[:8]}"
    proj_dir = REPO_ROOT / "storage" / "longform_projects" / proj_id
    yield proj_id
    if proj_dir.is_dir():
        shutil.rmtree(proj_dir, ignore_errors=True)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Helper to run cli_longform.py with UTF-8 encoding."""
    cmd = [sys.executable, "-X", "utf8", str(CLI_PATH)] + list(args)
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc


def test_cli_all_subcommands_smoke(temp_project_id: str):
    """Run all CLI subcommands in sequence and assert exit code 0 and no crash (F2)."""
    p_id = temp_project_id

    # 1. presets list
    res = run_cli("presets", "list")
    assert res.returncode == 0, f"presets list failed: {res.stderr}"
    assert "automotive" in res.stdout

    # 2. presets validate
    preset_file = REPO_ROOT / "resource" / "presets" / "automotive.muscle_car.json"
    res = run_cli("presets", "validate", str(preset_file))
    assert res.returncode == 0, f"presets validate failed: {res.stderr}"
    assert "SUCCESS: Preset" in res.stdout

    # 3. project create
    res = run_cli("project", "create", "--id", p_id, "--title", "Smoke Test Video", "--preset", "automotive.muscle_car", "--topic", "Smoke Test Car")
    assert res.returncode == 0, f"project create failed: {res.stderr}"
    assert "project created successfully" in res.stdout.lower()

    # 4. project show
    res = run_cli("project", "show", p_id)
    assert res.returncode == 0, f"project show failed: {res.stderr}"
    assert p_id in res.stdout

    # 5. project plan (on fresh manual project)
    res = run_cli("project", "plan", p_id)
    assert res.returncode == 0, f"project plan failed: {res.stderr}"
    assert "Execution Plan for Project:" in res.stdout
    assert "Total Work Units:" in res.stdout

    # 6. script outline (using explicit fake provider)
    res = run_cli("script", "outline", p_id, "--topic", "1969 Shelby GT500", "--minutes", "8", "--provider", "fake")
    assert res.returncode == 0, f"script outline failed: {res.stderr}"
    assert "Script Outline Generated Successfully" in res.stdout

    # 7. script status (after outline)
    res = run_cli("script", "status", p_id)
    assert res.returncode == 0, f"script status failed: {res.stderr}"
    assert "Outline Created:     Yes" in res.stdout

    # 8. script write (using explicit fake provider)
    res = run_cli("script", "write", p_id, "--provider", "fake")
    assert res.returncode == 0, f"script write failed: {res.stderr}"
    assert "Script Writing Completed Successfully" in res.stdout
    assert "Quality Report:" in res.stdout

    # 9. script status (after write)
    res = run_cli("script", "status", p_id)
    assert res.returncode == 0, f"script status failed: {res.stderr}"
    assert "Full Script Created: Yes" in res.stdout

    # 10. script quality (manual re-run)
    res = run_cli("script", "quality", p_id)
    assert res.returncode == 0, f"script quality failed: {res.stderr}"
    assert "Quality Inspection Summary:" in res.stdout

    # 11. project plan (after script generation)
    res = run_cli("project", "plan", p_id)
    assert res.returncode == 0, f"project plan post-write failed: {res.stderr}"
    assert "Execution Plan for Project:" in res.stdout

    # 12. project update-script (re-importing generated full_script.md)
    full_script_file = REPO_ROOT / "storage" / "longform_projects" / p_id / "script" / "full_script.md"
    res = run_cli("project", "update-script", p_id, "--script", str(full_script_file))
    assert res.returncode == 0, f"project update-script failed: {res.stderr}"
    assert "Manifest updated successfully" in res.stdout

    # 13. project refresh-preset
    res = run_cli("project", "refresh-preset", p_id)
    assert res.returncode == 0, f"project refresh-preset failed: {res.stderr}"
    assert "Preset Snapshot Refreshed" in res.stdout

    # 14. voice list (Stage 3)
    res = run_cli("voice", "list", "--language", "en-US")
    assert res.returncode == 0, f"voice list failed: {res.stderr}"
    assert "Available Edge TTS Voices" in res.stdout

    # 15. project set-voice (Stage 3)
    res = run_cli("project", "set-voice", p_id, "en-US-ChristopherNeural-Male", "--rate", "1.0")
    assert res.returncode == 0, f"project set-voice failed: {res.stderr}"
    assert "Narrator Voice Updated" in res.stdout

    # 16. audio synth with fake provider (Stage 3)
    res = run_cli("audio", "synth", p_id, "--provider", "fake")
    assert res.returncode == 0, f"audio synth failed: {res.stderr}"
    assert "Audio Synthesis Completed Successfully" in res.stdout

    # 17. audio status (Stage 3)
    res = run_cli("audio", "status", p_id)
    assert res.returncode == 0, f"audio status failed: {res.stderr}"
    assert "Audio Synthesis Status:" in res.stdout
    assert "Alignment Methods:" in res.stdout

    # 18. audio preview (Stage 3)
    res = run_cli("audio", "preview", p_id, "--chapter", "1")
    assert res.returncode == 0, f"audio preview failed: {res.stderr}"
    assert "Audio Preview Generated Successfully" in res.stdout

    # 19. project dev-simulate
    res = run_cli("project", "dev-simulate", p_id)
    assert res.returncode == 0, f"project dev-simulate failed: {res.stderr}"
    assert "Pipeline run simulated successfully" in res.stdout

    # 20. project plan (fully completed)
    res = run_cli("project", "plan", p_id)
    assert res.returncode == 0, f"project plan completed failed: {res.stderr}"
    assert "Project is fully completed and up to date" in res.stdout
