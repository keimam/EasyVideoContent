"""Pytest fixtures for longform video test suite (Stage 7B.2 Z4)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def mock_capcut_not_running(monkeypatch, request):
    """Ensure CapCut is treated as NOT running by default across all longform tests,
    unless explicitly testing CapCut running detection.
    """
    test_name = request.node.name
    # Don't mock if test specifically checks running CapCut
    if "running_capcut" in test_name or "capcut_running" in test_name:
        return
    if "real_capcut" in request.keywords or "allow_real_capcut" in request.keywords:
        return

    monkeypatch.setattr("app.services.longform.capcut.detector.is_capcut_running", lambda: False)
    monkeypatch.setattr("app.services.longform.capcut.service.is_capcut_running", lambda: False)
