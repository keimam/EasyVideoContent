"""Tests for dev-simulation pipeline, failure injection, and resume capability."""

import argparse
from pathlib import Path
import pytest

from cli_longform import cmd_project_create, cmd_project_dev_simulate
from app.services.longform.planner import compute_plan
from app.services.longform.schema import ItemStatus
from app.services.longform.store import get_project_dir, load_manifest


def test_dev_simulate_failure_and_resume(tmp_path: Path, monkeypatch):
    """Verify that dev-simulate handles simulated failure and successfully resumes."""
    # Use tmp_path as storage root
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

    # Chapter 1 units should be completed
    assert "ch1_g1_audio" in completed_ids
    assert "ch1_render" in completed_ids
    # Chapter 2 units should not be completed
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

    # Verify everything is completed
    manifest_done = load_manifest(project_dir)
    assert manifest_done.status == ItemStatus.completed
    plan_done = compute_plan(manifest_done, project_dir)
    assert plan_done.is_fully_completed

    # 4. Idempotent re-run
    rerun_res = cmd_project_dev_simulate(sim_resume_args)
    assert rerun_res == 0
