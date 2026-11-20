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


@pytest.fixture(autouse=True)
def default_ui_language_id(monkeypatch):
    """Isolate tests from host config.toml language setting by defaulting to 'id' unless overridden."""
    from app.config import config

    monkeypatch.setitem(config.ui, "language", "id")


@pytest.fixture(autouse=True)
def isolate_host_config_toml(monkeypatch):
    """Prevent any test or AppTest from mutating or saving to the host user's config.toml."""
    import copy
    from app.config import config

    orig_app = copy.deepcopy(config.app)
    orig_ui = copy.deepcopy(config.ui)

    monkeypatch.setattr(config, "save_config", lambda *a, **kw: None)
    monkeypatch.setattr(config, "try_save_config", lambda *a, **kw: True)
    monkeypatch.setattr(config, "_schedule_deferred_config_flush", lambda *a, **kw: None)
    monkeypatch.setattr(config, "_run_deferred_config_flush", lambda *a, **kw: None)

    try:
        yield
    finally:
        config.app.clear()
        config.app.update(orig_app)
        config.ui.clear()
        config.ui.update(orig_ui)


@pytest.fixture(autouse=True)
def default_setup_completed_for_legacy_tests(monkeypatch, request):
    """Treat first-run setup as completed for legacy UI/branding tests that do not test the Wizard."""
    name = request.node.name.lower()
    if any(tok in name for tok in ("wizard", "first_run", "onboarding_tour", "_e1_", "_e2_", "_e5_", "_e8_", "_e9_")):
        return
    monkeypatch.setattr("app.services.longform.first_run.is_setup_completed", lambda: True)
