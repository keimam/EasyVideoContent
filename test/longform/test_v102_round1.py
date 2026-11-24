"""Tests for V102 Round 1 (B1 Coverr auth_blocked, B2 source_status UI, B3 network_profile, B4 thread cap, B5 preset keys)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from app.config import config
from app.services.longform.schema import Chapter, ProjectManifest, Shot
from app.services.longform.store import save_manifest
from app.services.longform.visual.service import VisualService


def _write_local_img(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), color=(40, 90, 140)).save(path)
    return path


def test_b1_coverr_stops_after_first_403_and_resets_on_key_change(tmp_path: Path, monkeypatch):
    """B1: HTTP 403 on shot 1 marks Coverr auth_blocked and makes 0 Coverr HTTP requests on shots 2..N."""
    import requests
    from app.services.longform.visual.coverr_client import (
        get_coverr_auth_status,
        reset_coverr_auth_block,
    )
    from app.services.longform.visual.free_providers import reset_provider_circuit_breakers

    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    monkeypatch.setenv("EFS_DEV", "1")
    monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-bad-key-1111"])

    proj_dir = tmp_path / "b1_proj"
    _write_local_img(proj_dir / "assets" / "visual" / "library" / "snow_winter_forest.jpg")

    shots = [
        Shot(
            shot_id=f"ch01_shot00{i}",
            chapter_index=1,
            shot_index=i,
            duration_seconds=3.0,
            target_frames=75,
            visual_prompt="snow winter forest",
            search_query="snow winter forest",
            search_query_en="snow winter forest",
            search_query_source="ai",
        )
        for i in range(1, 5)
    ]
    manifest = ProjectManifest(
        project_id="b1_proj",
        title="B1 Coverr Test",
        preset_id="umum.en",
        preset_snapshot={"min_relevance_score": 0.35},
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=shots)],
        visual_anchor_terms=["snow", "forest"],
    )
    save_manifest(manifest, proj_dir)

    coverr_calls = {"count": 0}

    def _fake_get(url, *a, **kw):
        if "coverr" in str(url):
            coverr_calls["count"] += 1
            resp = MagicMock()
            resp.status_code = 403
            resp.text = "Forbidden"
            resp.headers = {}
            return resp
        raise AssertionError(f"Unexpected HTTP call: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    try:
        svc = VisualService(proj_dir)
        svc.fetch_all(
            manifest,
            providers_order=["coverr", "local"],
            translate_keywords=False,
        )

        # Shot 1 hit 403 -> shots 2..4 made 0 HTTP requests to Coverr!
        assert coverr_calls["count"] == 1
        st_auth = get_coverr_auth_status()
        assert st_auth["auth_blocked"] is True
        assert st_auth["status_code"] == 403

        # Changing the Coverr API key automatically clears auth_blocked
        monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-new-key-2222"])
        assert get_coverr_auth_status()["auth_blocked"] is False
    finally:
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()


def test_b1_coverr_maps_candidate_fields_without_downloading_mp4_on_search(monkeypatch):
    """B1: Coverr search hits /videos only, never GETs urls.mp4_download, and preserves VisualCandidate fields."""
    import requests
    from app.services.longform.visual.coverr_client import reset_coverr_auth_block
    from app.services.longform.visual.free_providers import reset_provider_circuit_breakers
    from app.services.longform.visual.stock import StockVisualProvider

    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-valid-key-9999"])

    seen_urls = []
    seen_params = []

    def _fake_get(url, params=None, headers=None, **kw):
        seen_urls.append(str(url))
        seen_params.append(dict(params or {}))
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.json.return_value = {
            "hits": [
                {
                    "id": "abc99",
                    "duration": "12.5",
                    "max_width": 1920,
                    "max_height": 1080,
                    "is_vertical": False,
                    "title": "Snowy winter mountain path",
                    "canonical_url": "https://coverr.co/videos/snowy-path-abc99?secret=dropme",
                    "creator": {"name": "Coverr Creator", "url": "https://coverr.co/creators/alice"},
                    "urls": {
                        "mp4_download": "https://cdn.coverr.co/videos/abc99/download?token=jwt123",
                    },
                }
            ]
        }
        return resp

    monkeypatch.setattr(requests, "get", _fake_get)

    try:
        prov = StockVisualProvider("coverr")
        cands = prov.search_candidates(
            query="snowy winter path",
            target_duration=4.0,
            limit=3,
            min_relevance_score=0.2,
        )
        assert len(seen_urls) == 1
        assert seen_urls[0] == "https://api.coverr.co/videos"
        assert seen_params[0]["urls"] == "true"
        assert seen_params[0]["filter"] == "is_vertical:false"
        assert seen_params[0]["sort"] == "popular"
        assert not any("mp4" in u or "download" in u for u in seen_urls)

        assert len(cands) == 1
        c = cands[0]
        assert c.candidate_id == "coverr_abc99"
        assert c.provider == "coverr"
        assert c.license == "Coverr License"
        assert c.rendition_name == "coverr (1920x1080)"
        assert c.url == "https://cdn.coverr.co/videos/abc99/download?token=jwt123"
        assert c.source_url == "https://coverr.co/videos/snowy-path-abc99"
        assert c.source_info["asset_id"] == "abc99"
        assert c.source_info["rendition"]["id"] == "mp4_download"
    finally:
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()
