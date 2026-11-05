"""Tests for dev-simulation pipeline, failure injection, KeyboardInterrupt handling, and resume capability (A3)."""

import argparse
import time
from pathlib import Path
from unittest.mock import patch
import pytest

from cli_longform import cmd_project_create, cmd_project_dev_simulate
from app.services.longform.planner import compute_plan
from app.services.longform.schema import ItemStatus
from app.services.longform.store import get_project_dir, load_manifest


def test_dev_simulate_failure_and_resume(tmp_path: Path, monkeypatch):
    """Verify that dev-simulate handles simulated failure and successfully resumes."""
    storage_root = tmp_path / "longform_projects"
    storage_root.mkdir(parents=True, exist_ok=True)

    from app.services.longform import store
    monkeypatch.setattr(store, "get_projects_root", lambda: storage_root)

    repo_root = Path(__file__).resolve().parent.parent.parent
    sample_script_path = repo_root / "docs" / "longform" / "examples" / "sample_script.md"
    assert sample_script_path.is_file()

    # 1. Create project
    create_args = argparse.Namespace(
        title="Simulate Test",
        preset="documentary_en",
        script=str(sample_script_path),
        id="sim_test_proj",
        aspect_ratio="16:9",
        focus=None,
    )
    res = cmd_project_create(create_args)
    assert res == 0

    project_dir = storage_root / "sim_test_proj"
    manifest = load_manifest(project_dir)
    assert manifest.status == ItemStatus.pending

    initial_plan = compute_plan(manifest, project_dir)
    assert len(initial_plan.pending_units) == initial_plan.total_count

    # 2. Simulate run with failure at ch2_g1_audio
    sim_fail_args = argparse.Namespace(
        project_id="sim_test_proj",
        delay=0.0,
        fail_at="ch2_g1_audio",
    )
    fail_res = cmd_project_dev_simulate(sim_fail_args)
    assert fail_res == 1  # Exited with error code 1

    # Verify Chapter 1 completed, Chapter 2 still pending
    manifest_interrupted = load_manifest(project_dir)
    plan_interrupted = compute_plan(manifest_interrupted, project_dir)
    completed_ids = {u.unit_id for u in plan_interrupted.completed_units}

    assert "ch1_g1_audio" in completed_ids
    assert "ch1_render" in completed_ids
    assert "ch2_g1_audio" not in completed_ids
    assert "final_concat" not in completed_ids

    # 3. Resume simulation without fail_at
    sim_resume_args = argparse.Namespace(
        project_id="sim_test_proj",
        delay=0.0,
        fail_at=None,
    )
    resume_res = cmd_project_dev_simulate(sim_resume_args)
    assert resume_res == 0

    # Verify everything is completed (28/28)
    manifest_done = load_manifest(project_dir)
    assert manifest_done.status == ItemStatus.completed
    plan_done = compute_plan(manifest_done, project_dir)
    assert plan_done.is_fully_completed
    assert len(plan_done.completed_units) == 28

    # 4. Idempotent re-run
    rerun_res = cmd_project_dev_simulate(sim_resume_args)
    assert rerun_res == 0


def test_dev_simulate_keyboard_interrupt_releases_lock_and_preserves_atomic_state(tmp_path: Path, monkeypatch):
    """Verify KeyboardInterrupt in dev-simulate safely releases lock and leaves interrupted unit uncompleted (A3)."""
    storage_root = tmp_path / "longform_projects_ki"
    storage_root.mkdir(parents=True, exist_ok=True)

    from app.services.longform import store
    monkeypatch.setattr(store, "get_projects_root", lambda: storage_root)

    repo_root = Path(__file__).resolve().parent.parent.parent
    sample_script_path = repo_root / "docs" / "longform" / "examples" / "sample_script.md"

    create_args = argparse.Namespace(
        title="KI Test",
        preset="documentary_en",
        script=str(sample_script_path),
        id="ki_proj",
        aspect_ratio="16:9",
        focus=None,
    )
    assert cmd_project_create(create_args) == 0

    project_dir = storage_root / "ki_proj"

    # Simulate KeyboardInterrupt when processing unit 5 (ch1_g2_audio)
    call_count = 0
    real_time_sleep = time.sleep

    def fake_sleep_with_interrupt(secs):
        nonlocal call_count
        call_count += 1
        if call_count == 5:
            raise KeyboardInterrupt("Simulated Ctrl+C")
        real_time_sleep(0.0)

    monkeypatch.setattr(time, "sleep", fake_sleep_with_interrupt)

    sim_args = argparse.Namespace(
        project_id="ki_proj",
        delay=0.01,
        fail_at=None,
    )
    exit_code = cmd_project_dev_simulate(sim_args)
    assert exit_code == 130  # Interrupted status code

    # Lock must be released
    lock_file = project_dir / ".lock"
    assert not lock_file.exists()

    # Unit 5 (ch1_g2_audio) must NOT be recorded as completed
    manifest_post_ki = load_manifest(project_dir)
    plan_post_ki = compute_plan(manifest_post_ki, project_dir)
    completed_ids = {u.unit_id for u in plan_post_ki.completed_units}

    assert "ch1_g1_audio" in completed_ids  # completed before interrupt
    assert "ch1_g2_audio" not in completed_ids  # interrupted unit NOT completed

    # Now restore real sleep and resume
    monkeypatch.setattr(time, "sleep", real_time_sleep)
    sim_resume_args = argparse.Namespace(
        project_id="ki_proj",
        delay=0.0,
        fail_at=None,
    )
    resume_code = cmd_project_dev_simulate(sim_resume_args)
    assert resume_code == 0

    final_plan = compute_plan(load_manifest(project_dir), project_dir)
    assert final_plan.is_fully_completed
    assert len(final_plan.completed_units) == 28
