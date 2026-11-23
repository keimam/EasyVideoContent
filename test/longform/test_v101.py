"""Unit & integration tests for EasyFaceless Studio 1.0.1 (Stage V101 v2)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
import pytest

from app.services.longform.schema import Chapter, ProjectManifest, Shot
from app.services.longform.store import save_manifest
from app.services.longform.visual.local import (
    LocalLibraryManager,
    LocalVisualProvider,
    extract_local_file_tags,
)
from app.services.longform.visual.service import VisualService


def _write_img(path: Path, color: tuple[int, int, int] = (80, 120, 160)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), color=color).save(path)
    return path


def test_v0_local_filename_and_subfolder_tag_extraction(tmp_path: Path):
    """V0: Extract lowercase tags from stem separators (_, -, .) and subfolder names."""
    lib_dir = tmp_path / "assets" / "visual" / "library"
    nested = lib_dir / "norse-history" / "viking_ship.storm-2026.jpg"
    _write_img(nested)

    tags = extract_local_file_tags(nested, lib_dir)
    assert "norse" in tags
    assert "history" in tags
    assert "viking" in tags
    assert "ship" in tags
    assert "storm" in tags
    assert "2026" in tags


def test_v0_local_scoring_prefers_match_and_returns_empty_on_no_match(tmp_path: Path):
    """V0: viking_ship_storm.jpg matches 'viking ship'; 'pyramid' returns 0 candidates when fill_local_random=False."""
    proj_dir = tmp_path / "v0_proj"
    lib_dir = proj_dir / "assets" / "visual" / "library"
    _write_img(lib_dir / "viking_ship_storm.jpg", (40, 80, 120))
    _write_img(lib_dir / "random_cat.png", (180, 90, 60))

    mgr = LocalLibraryManager(proj_dir)
    assert len(mgr.list_assets()) == 2

    prov = LocalVisualProvider(proj_dir)
    assert prov.fill_local_random is False

    cands_viking, raw_count, reasons = prov.search_with_diagnostics("viking ship", target_duration=4.0, limit=5)
    assert raw_count == 2
    assert len(cands_viking) == 1
    assert cands_viking[0].source_info["filename"] == "viking_ship_storm.jpg"
    assert cands_viking[0].relevance_score >= 0.35
    assert any("random_cat.png" in r for r in reasons)

    cands_pyramid, raw_pyr, reasons_pyr = prov.search_with_diagnostics("pyramid", target_duration=4.0, limit=5)
    assert raw_pyr == 2
    assert cands_pyramid == []
    assert len(reasons_pyr) == 2


def test_v0_local_anchor_and_avoid_rules_enforced(tmp_path: Path):
    """V0: Preset/project anchor_terms and avoid_terms apply cleanly to local assets."""
    proj_dir = tmp_path / "v0_rules"
    lib_dir = proj_dir / "assets" / "visual" / "library"
    _write_img(lib_dir / "norse_viking_battle.jpg")
    _write_img(lib_dir / "cartoon_viking_toy.png")

    prov = LocalVisualProvider(proj_dir)
    cands, _raw, reasons = prov.search_with_diagnostics(
        "viking",
        target_duration=4.0,
        anchor_terms=["norse"],
        avoid_terms=["cartoon"],
    )
    assert [c.source_info["filename"] for c in cands] == ["norse_viking_battle.jpg"]
    assert any("Avoid term matched: cartoon" in r for r in reasons)


def test_v0_local_opt_in_random_fill_and_deterministic_pass3(tmp_path: Path):
    """V0: Pass 2/3 run ONLY when fill_local_random=True, and pass 3 uses deterministic sha1."""
    proj_dir = tmp_path / "v0_optin"
    lib_dir = proj_dir / "assets" / "visual" / "library"
    _write_img(lib_dir / "random_cat.png")
    _write_img(lib_dir / "desk_lamp.jpg")

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        visual_prompt="ancient Egyptian pyramid",
        search_query="egyptian pyramid",
        search_query_en="egyptian pyramid",
    )
    manifest = ProjectManifest(
        project_id="v0_optin",
        title="V0 Optin",
        preset_id="documentary_en",
        preset_snapshot={},
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])],
    )
    assert manifest.fill_local_random is False
    save_manifest(manifest, proj_dir)

    svc = VisualService(proj_dir)
    manifest = svc.fetch_all(manifest, provider_name="local")
    assert manifest.chapters[0].shots[0].actual_source is None
    assert manifest.chapters[0].shots[0].fallback_reason == "no_visual_found"

    # Opt in explicitly -> pass 2 selects an unused local asset
    manifest.fill_local_random = True
    manifest = svc.fetch_all(manifest, provider_name="local")
    assert manifest.chapters[0].shots[0].actual_source == "local"

    # Pass 3 deterministic ID across invocations
    prov = LocalVisualProvider(proj_dir, fill_local_random=True)
    all_excluded = {"local_random_cat", "local_desk_lamp"}
    cands_a, _, _ = prov.search_with_diagnostics("egyptian pyramid", 3.0, excluded_ids=all_excluded)
    cands_b, _, _ = prov.search_with_diagnostics("egyptian pyramid", 3.0, excluded_ids=all_excluded)
    assert cands_a and cands_b
    assert [c.candidate_id for c in cands_a] == [c.candidate_id for c in cands_b]
    assert len(cands_a[0].candidate_id.rsplit("_", 1)[-1]) == 8
