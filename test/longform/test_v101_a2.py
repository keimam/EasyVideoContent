"""Tests for V101-A2: Visual pipeline phase timing, speed optimizations, cap proof, and image preset UI test state."""

from __future__ import annotations

import json
from pathlib import Path
from PIL import Image

from app.services.longform.schema import Chapter, ProjectManifest, Shot
from app.services.longform.store import save_manifest
from app.services.longform.visual.service import VisualService


def _write_test_image(path: Path, color=(40, 120, 180)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), color=color).save(path)
    return path


def test_a2_1_pipeline_phase_telemetry_recorded_and_lazy_thumbnails(tmp_path: Path):
    """A2.1: run_pipeline records full per-phase timings, cache hits, manifest save count, and 0.0s thumbnail time."""
    proj_dir = tmp_path / "a21_telemetry"
    lib_dir = proj_dir / "assets" / "visual" / "library"
    _write_test_image(lib_dir / "snowy_mountain_forest.jpg")

    shots = [
        Shot(
            shot_id="ch01_shot001",
            chapter_index=1,
            shot_index=1,
            duration_seconds=2.5,
            target_frames=62,
            visual_prompt="snowy mountain forest",
            search_query="snowy mountain forest",
            search_query_en="snowy mountain forest",
            search_query_source="ai",
        )
    ]
    manifest = ProjectManifest(
        project_id="a21_telemetry",
        title="A2.1 Telemetry",
        preset_id="umum.en",
        preset_snapshot={"min_relevance_score": 0.35},
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=shots)],
        visual_anchor_terms=["forest", "winter"],
    )
    save_manifest(manifest, proj_dir)

    svc = VisualService(proj_dir)
    _m_out, stats = svc.run_pipeline(manifest, provider_name="local", allow_fallback=False)

    metrics = stats.get("pipeline_metrics")
    assert isinstance(metrics, dict)
    for required_key in (
        "total_seconds",
        "allocate_seconds",
        "fetch_seconds",
        "ai_keywords_seconds",
        "ai_keywords_llm_calls",
        "ai_keywords_retries",
        "search_wall_seconds",
        "search_per_source",
        "thumbnail_seconds",
        "asset_download_seconds",
        "download_cache_hits",
        "download_misses",
        "build_seconds",
        "ffmpeg_build_seconds",
        "built_count",
        "manifest_save_seconds",
        "manifest_save_count",
    ):
        assert required_key in metrics, f"Missing telemetry key: {required_key}"

    assert metrics["thumbnail_seconds"] == 0.0
    assert metrics["built_count"] == 1
    assert metrics["manifest_save_count"] >= 3
    assert "local" in metrics["search_per_source"]
    assert metrics["search_per_source"]["local"]["tier_attempts"] >= 1

    metrics_file = proj_dir / "visual_metrics.json"
    assert metrics_file.is_file()
    persisted = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert persisted["project_id"] == "a21_telemetry"
    assert persisted["built_count"] == 1


def test_a2_2_cached_json_get_fails_fast_and_opens_cooldown_on_504_or_timeout(tmp_path: Path, monkeypatch):
    """A2.2: HTTP 504 / Timeout fails fast without retrying and opens 120s cooldown after 2 consecutive failures."""
    from unittest.mock import MagicMock
    import requests
    from app.services.longform.visual.base import VisualError
    from app.services.longform.visual.cache import GlobalMediaCache
    from app.services.longform.visual.free_providers import cached_json_get, reset_provider_circuit_breakers

    reset_provider_circuit_breakers()
    cache = GlobalMediaCache(tmp_path / "cache")

    calls = {"count": 0}
    resp_504 = MagicMock()
    resp_504.status_code = 504
    resp_504.text = "Gateway Time-out"

    def _fake_get(*a, **kw):
        calls["count"] += 1
        assert kw.get("timeout", 99) <= 8
        if calls["count"] == 1:
            return resp_504
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr("requests.get", _fake_get)

    try:
        # Call 1 -> 504 fails in 1 attempt (not 2)
        try:
            cached_json_get("openverse", "https://api.openverse.org/v1/images/", params={"q": "snow"}, cache=cache, min_interval_sec=0)
            assert False, "Expected VisualError on 504"
        except VisualError as exc:
            assert "504" in str(exc)
        assert calls["count"] == 1

        # Call 2 -> ReadTimeout fails in 1 attempt and trips cooldown
        try:
            cached_json_get("openverse", "https://api.openverse.org/v1/images/", params={"q": "forest"}, cache=cache, min_interval_sec=0)
            assert False, "Expected VisualError on ReadTimeout"
        except VisualError as exc:
            assert "Timeout" in str(exc)
        assert calls["count"] == 2

        # Call 3 -> skipped immediately via cooldown without calling requests.get!
        try:
            cached_json_get("openverse", "https://api.openverse.org/v1/images/", params={"q": "village"}, cache=cache, min_interval_sec=0)
            assert False, "Expected cooldown VisualError"
        except VisualError as exc:
            assert "cooldown active" in str(exc).lower()
        assert calls["count"] == 2
    finally:
        reset_provider_circuit_breakers()


def test_a2_2_visual_completion_duration_banner_i18n(tmp_path: Path, monkeypatch):
    """A2.2: Visual completion elapsed banner formats cleanly in ID and EN from visual_metrics.json."""
    from app.services.longform.ui.components import _get_visual_completion_duration_text

    proj_dir = tmp_path / "dur_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "visual_metrics.json").write_text(json.dumps({"total_seconds": 165.4}), encoding="utf-8")

    cur_lang = {"v": "id"}
    monkeypatch.setattr("app.services.longform.ui.i18n.get_ui_language", lambda: cur_lang["v"])

    txt_id = _get_visual_completion_duration_text(proj_dir, "dur_proj")
    assert txt_id == "Selesai dalam 2 menit 45 detik"

    cur_lang["v"] = "en"
    txt_en = _get_visual_completion_duration_text(proj_dir, "dur_proj")
    assert txt_en == "Completed in 2m 45s"

    (proj_dir / "visual_metrics.json").write_text(json.dumps({"total_seconds": 42.0}), encoding="utf-8")
    cur_lang["v"] = "id"
    assert _get_visual_completion_duration_text(proj_dir, "dur_proj") == "Selesai dalam 42 detik"
    cur_lang["v"] = "en"
    assert _get_visual_completion_duration_text(proj_dir, "dur_proj") == "Completed in 42s"

