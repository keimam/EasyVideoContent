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


def test_b2_source_status_aggregation_redacted_and_persisted(tmp_path: Path, monkeypatch):
    """B2: fetch_all aggregates per-source health (ok, cooldown, auth_blocked, timeout, redacted last_error) into visual_metrics.json."""
    import requests
    from app.services.longform.visual.coverr_client import reset_coverr_auth_block
    from app.services.longform.visual.free_providers import reset_provider_circuit_breakers

    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    monkeypatch.setenv("EFS_DEV", "1")
    monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-secret-xyz987654"])

    proj_dir = tmp_path / "b2_proj"
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
        for i in range(1, 3)
    ]
    manifest = ProjectManifest(
        project_id="b2_proj",
        title="B2 Source Status",
        preset_id="umum.en",
        preset_snapshot={"min_relevance_score": 0.35},
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=shots)],
        visual_anchor_terms=["snow", "forest"],
        visual_search_mode="mix_best",
    )
    save_manifest(manifest, proj_dir)

    def _fake_get(url, *a, **kw):
        u = str(url)
        resp = MagicMock()
        resp.headers = {}
        if "coverr" in u:
            resp.status_code = 403
            resp.text = "Rejected Bearer cov-secret-xyz987654"
            return resp
        if "openverse" in u:
            resp.status_code = 504
            resp.text = "Gateway Time-out " + ("x" * 200)
            return resp
        raise AssertionError(f"Unexpected HTTP call: {url}")

    monkeypatch.setattr(requests, "get", _fake_get)

    try:
        svc = VisualService(proj_dir)
        svc.fetch_all(
            manifest,
            providers_order=["coverr", "openverse", "local"],
            translate_keywords=False,
        )

        metrics_path = proj_dir / "visual_metrics.json"
        assert metrics_path.is_file()
        data = json.loads(metrics_path.read_text(encoding="utf-8"))
        src_status = data.get("source_status")
        assert isinstance(src_status, dict)

        assert src_status["coverr"]["ok"] is False
        assert src_status["coverr"]["auth_blocked"] is True
        assert src_status["coverr"]["last_error"] is not None
        assert len(src_status["coverr"]["last_error"]) <= 120
        assert "cov-secret-xyz987654" not in src_status["coverr"]["last_error"]

        assert src_status["openverse"]["ok"] is False
        assert src_status["openverse"]["timeout"] >= 2
        assert src_status["openverse"]["cooldown"] >= 1
        assert "504" in str(src_status["openverse"]["last_error"])
        assert len(src_status["openverse"]["last_error"]) <= 120

        assert src_status["local"]["ok"] is True
        assert src_status["local"]["auth_blocked"] is False
        assert src_status["local"]["cooldown"] == 0
        assert src_status["local"]["timeout"] == 0
    finally:
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()


def test_b2_visual_step_renders_warning_for_auth_blocked_and_cooldown_sources(tmp_path: Path, monkeypatch):
    """B2: Visual step renders a warning box near summary for unhealthy sources (auth_blocked / cooldown) in ID & EN."""
    from streamlit.testing.v1 import AppTest
    from app.services.longform.schema import ItemStatus, Segment, TtsGroup
    from app.services.longform.store import create_project

    proj_root = tmp_path / "projects_b2"
    proj_root.mkdir()
    manifest, p_dir = create_project("proj_b2", "B2 UI Warn", "umum.id", language="id-ID", projects_root=proj_root)
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Hutan salju dingin",
                    segments=[Segment(segment_index=1, narration_text="Hutan salju dingin", duration_seconds=3.0)],
                    status=ItemStatus.completed,
                )
            ],
            shots=[
                Shot(
                    shot_id="ch01_shot001",
                    chapter_index=1,
                    shot_index=1,
                    duration_seconds=3.0,
                    target_frames=75,
                    visual_prompt="winter forest",
                    search_query="winter forest",
                    search_query_en="winter forest",
                    status=ItemStatus.pending,
                )
            ],
            status=ItemStatus.completed,
        )
    ]
    save_manifest(manifest, p_dir)
    (p_dir / "visual_metrics.json").write_text(
        json.dumps({
            "project_id": "proj_b2",
            "total_seconds": 125.0,
            "source_status": {
                "coverr": {"ok": False, "cooldown": 0, "auth_blocked": True, "timeout": 0, "last_error": "HTTP 403"},
                "openverse": {"ok": False, "cooldown": 2, "auth_blocked": False, "timeout": 4, "last_error": "HTTP 504"},
                "pixabay": {"ok": True, "cooldown": 0, "auth_blocked": False, "timeout": 0, "last_error": None},
            },
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)

    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    at = AppTest.from_file(app_file, default_timeout=20)
    at.query_params["project"] = "proj_b2"
    at.query_params["step"] = "visual"
    at.run()
    assert not at.exception

    warn_texts = "\n".join(str(w.value) for w in at.warning)
    assert "Coverr dilewati: API key ditolak (403)" in warn_texts
    assert "Openverse dijeda 2×" in warn_texts
    assert "504" in warn_texts

    # Switch to EN and verify English localization
    monkeypatch.setitem(config.ui, "language", "en")
    at2 = AppTest.from_file(app_file, default_timeout=20)
    at2.query_params["project"] = "proj_b2"
    at2.query_params["step"] = "visual"
    at2.query_params["lang"] = "en"
    at2.run()
    assert not at2.exception
    warn_en = "\n".join(str(w.value) for w in at2.warning)
    assert "Coverr skipped: API key rejected (403)" in warn_en
    assert "Openverse paused 2×" in warn_en
