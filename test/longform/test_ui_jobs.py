import json
import os
import shutil
import subprocess
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

    # State read from disk should detect process ended and mark stopped because manifest wasn't finished
    recovered_state = jobs.get_job_state(project_id)
    assert recovered_state is not None
    assert recovered_state["status"] in ("completed", "failed", "stopped")
    assert recovered_state["step"] == "persist_step"


def test_stale_lock_cleanup(temp_project):
    """Verify that stale .lock files from dead PIDs are automatically cleaned up (C1)."""
    project_id, proj_dir = temp_project
    lock_file = proj_dir / ".lock"

    # Write a fake stale lock with an impossible dead PID
    dead_pid = 999998
    lock_file.write_text(json.dumps({"pid": dead_pid, "time": "2026-01-01"}), encoding="utf-8")
    assert lock_file.is_file()

    # cleanup_stale_lock should remove it
    cleaned = jobs.cleanup_stale_lock(project_id)
    assert cleaned is True
    assert not lock_file.is_file()


def test_detached_process_survives_parent_termination(temp_project):
    """Verify on Windows that killing the parent process does NOT kill the detached child job (C1)."""
    project_id, proj_dir = temp_project

    # Run a parent script that imports jobs, calls start_job with a 10s sleep, prints child PID, and gets killed
    parent_script = f"""
import sys, time
from pathlib import Path
from app.services.longform.ui import jobs

child_cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
state = jobs.start_job("{project_id}", "detached_step", child_cmd)
print(f"CHILD_PID:{{state['pid']}}", flush=True)
time.sleep(5)
"""
    parent_proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    child_pid = None
    # Read child PID from parent output
    for line in parent_proc.stdout:
        if "CHILD_PID:" in line:
            child_pid = int(line.strip().split("CHILD_PID:")[1])
            break

    assert child_pid is not None, "Failed to get child PID from parent process"
    assert jobs.is_pid_alive(child_pid), "Child job should be alive initially"

    # Kill the parent process forcefully
    parent_proc.kill()
    parent_proc.wait(timeout=2.0)
    assert not jobs.is_pid_alive(parent_proc.pid), "Parent process should be dead"

    # Verify the detached child process IS STILL ALIVE!
    try:
        assert jobs.is_pid_alive(child_pid), "Detached child job must remain alive after parent termination"
    finally:
        # Cleanup child process
        jobs.cancel_job(project_id)
