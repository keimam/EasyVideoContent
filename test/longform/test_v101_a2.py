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
