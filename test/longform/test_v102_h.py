"""Offline tests for V102-H (H1 Pixabay cache/lock/throttle, H2 Coverr quota vs auth_blocked, H3 partial status & 0-hit fallback).

All network/LLM calls are strictly mocked — 0 real requests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import config
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, Shot
from app.services.longform.store import save_manifest
from app.services.longform.ui.components import _get_visual_source_warnings
from app.services.longform.visual.coverr_client import (
    CoverrAuthBlockedError,
    CoverrQuotaExhaustedError,
    get_coverr_auth_status,
    reset_coverr_auth_block,
    search_coverr_videos,
)
from app.services.longform.visual.free_providers import reset_provider_circuit_breakers
from app.services.longform.visual.service import VisualService


def test_h1_pixabay_429_and_rate_limit_header_triggers_cooldown(tmp_path: Path, monkeypatch):
    """H1.4: HTTP 429 or X-RateLimit-Remaining=0 triggers B1/B3 cooldown and redacts key."""
    from app.services.longform.visual.cache import GlobalMediaCache
    from app.services.longform.visual.free_providers import check_provider_cooldown
    from app.services.longform.visual.stock import StockVisualProvider
    from app.services.longform.visual.base import VisualError

    reset_provider_circuit_breakers()
    monkeypatch.setitem(config.app, "pixabay_api_keys", ["pix-secret-998877"])
    cache = GlobalMediaCache(tmp_path / "cache")
    prov = StockVisualProvider("pixabay", cache=cache)
    monkeypatch.setattr(StockVisualProvider, "PIXABAY_MIN_INTERVAL_SEC", 0.01)

    calls = {"n": 0}

    def fake_429(url, params=None, **kw):
        calls["n"] += 1
        return SimpleNamespace(
            status_code=429,
            headers={"Retry-After": "1", "X-RateLimit-Remaining": "0"},
            text="Rate limit exceeded for pix-secret-998877",
        )

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_429):
        with patch("app.services.longform.visual.free_providers.time.sleep"):
            with pytest.raises(VisualError) as exc_info:
                prov.search_candidates("harbor", 4.0)
            assert "pix-secret-998877" not in str(exc_info.value)
            assert check_provider_cooldown("pixabay") > 0.0

    # Warm run reuse with existing cache succeeds without HTTP
    reset_provider_circuit_breakers()
    warm_payload = {
        "hits": [
            {
                "id": 7711,
                "duration": 7,
                "tags": "harbor, ship, sea",
                "user": "Author",
                "user_id": 1,
                "pageURL": "https://pixabay.com/videos/7711/",
                "videos": {"medium": {"url": "https://cdn.pix/7711.mp4", "width": 1920, "height": 1080, "size": 1000}},
            }
        ]
    }
    calls["n"] = 0

    def fake_200(url, params=None, **kw):
        calls["n"] += 1
        return SimpleNamespace(
            status_code=200,
            headers={"X-RateLimit-Remaining": "99", "X-RateLimit-Reset": "60"},
            text=json.dumps(warm_payload),
            json=lambda: warm_payload,
            raise_for_status=lambda: None,
        )

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_200):
        c1 = prov.search_candidates("harbor ship", 4.0)
        assert len(c1) == 1 and calls["n"] == 1
        c2 = prov.search_candidates("harbor ship", 4.0)
        assert len(c2) == 1 and calls["n"] == 1  # Run 2 hits 24h cache -> 0 new HTTP requests!


def test_h2_coverr_403_quota_limit_classified_as_quota_exhausted_not_auth_blocked(tmp_path: Path, monkeypatch):
    """H2: HTTP 403 '{"message":"Exceeded query limit"}' classifies as quota_exhausted with localized quota message."""
    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-demo-key-4321"])

    calls = {"n": 0}

    def fake_quota_403(url, **kw):
        calls["n"] += 1
        return SimpleNamespace(
            status_code=403,
            headers={},
            text='{"message":"Exceeded query limit"}',
        )

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_quota_403):
        with pytest.raises(CoverrQuotaExhaustedError):
            search_coverr_videos("plague street", 4)

        st = get_coverr_auth_status()
        assert st["quota_exhausted"] is True
        assert st["auth_blocked"] is False
        assert st["run_paused"] is True

        # Subsequent calls in same run make 0 HTTP calls
        with pytest.raises(CoverrQuotaExhaustedError):
            search_coverr_videos("medieval city", 4)
        assert calls["n"] == 1

    # Verify warning message in both ID and EN mentions quota, NOT rejected API key
    (tmp_path / "visual_metrics.json").write_text(
        json.dumps({
            "source_status": {
                "coverr": {
                    "ok": False,
                    "cooldown": 1,
                    "auth_blocked": False,
                    "quota_exhausted": True,
                    "timeout": 0,
                    "last_error": 'HTTP 403: {"message":"Exceeded query limit"}',
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setitem(config.ui, "language", "id")
    warns_id = _get_visual_source_warnings(tmp_path)
    assert any("batas kuota per jam habis" in w and "key ditolak" not in w for w in warns_id), warns_id

    monkeypatch.setitem(config.ui, "language", "en")
    warns_en = _get_visual_source_warnings(tmp_path)
    assert any("hourly query quota exhausted" in w and "API key rejected" not in w for w in warns_en), warns_en

    reset_coverr_auth_block()


def test_h2_coverr_403_without_quota_marker_stays_auth_blocked(monkeypatch):
    """H2: HTTP 403 without quota marker remains auth_blocked per A1 rule."""
    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-bad-key-0001"])

    def fake_bad_403(url, **kw):
        return SimpleNamespace(status_code=403, headers={}, text="Invalid authentication credentials")

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_bad_403):
        with pytest.raises(CoverrAuthBlockedError):
            search_coverr_videos("plague street", 4)
        st = get_coverr_auth_status()
        assert st["auth_blocked"] is True
        assert st["quota_exhausted"] is False

    reset_coverr_auth_block()
