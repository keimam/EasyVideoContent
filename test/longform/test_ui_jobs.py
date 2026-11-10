import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from app.services.longform.store import create_project, get_project_dir
from app.services.longform.ui import jobs


@pytest.fixture
def temp_project(tmp_path):
    project_id = f"test_job_proj_{tmp_path.name}"
    manifest, proj_dir = create_project(
        project_id=project_id,
        title="Test Jobs Project",
        preset_id="automotive.muscle_car",
    )
    yield project_id, proj_dir
    if proj_dir.is_dir():
        shutil.rmtree(proj_dir, ignore_errors=True)


def test_job_runner_lifecycle(temp_project):
    project_id, proj_dir = temp_project

    # 1. Start a fast dummy job that sleeps briefly and outputs text
    dummy_code = "import time, sys; print('JOB_STARTED'); sys.stdout.flush(); time.sleep(0.5); print('JOB_DONE'); sys.stdout.flush()"
    cmd = [sys.executable, "-c", dummy_code]

    state = jobs.start_job(project_id, "dummy_step", cmd)
    assert state["status"] == "running"
    assert state["pid"] > 0
    assert Path(state["log_file"]).is_file()

    # 2. Verify single active job enforcement
    with pytest.raises(RuntimeError, match="currently running"):
        jobs.start_job(project_id, "second_step", cmd)

    # 3. Wait for dummy job to complete
    time.sleep(1.2)
    state_after = jobs.get_job_state(project_id)
    assert state_after["status"] == "completed"
    assert state_after["exit_code"] == 0

    # 4. Verify log reading
    logs = jobs.get_job_logs(project_id, max_lines=10)
    assert any("JOB_STARTED" in line for line in logs)
    assert any("JOB_DONE" in line for line in logs)


def test_job_runner_cancellation(temp_project):
    project_id, proj_dir = temp_project

    # Start a long-running job
    dummy_code = "import time, sys; print('LONG_JOB_RUNNING'); sys.stdout.flush(); time.sleep(20)"
    cmd = [sys.executable, "-c", dummy_code]

    state = jobs.start_job(project_id, "long_step", cmd)
    assert state["status"] == "running"

    time.sleep(0.3)
    # Cancel job
    cancelled = jobs.cancel_job(project_id, timeout_seconds=2.0)
    assert cancelled is True

    state_after = jobs.get_job_state(project_id)
    assert state_after["status"] == "cancelled"


def test_job_state_persistence_across_restarts(temp_project):
    project_id, proj_dir = temp_project

    # Start a dummy job
    dummy_code = "import time; time.sleep(0.1)"
    cmd = [sys.executable, "-c", dummy_code]

    state = jobs.start_job(project_id, "persist_step", cmd)
    assert state["status"] == "running"

    # Simulate UI restart by clearing the in-memory active process registry
    jobs._ACTIVE_PROCESSES.clear()

    # Wait for process to finish
    time.sleep(0.5)

    # State read from disk should detect process ended
    recovered_state = jobs.get_job_state(project_id)
    assert recovered_state is not None
    assert recovered_state["status"] in ("completed", "failed")
    assert recovered_state["step"] == "persist_step"
