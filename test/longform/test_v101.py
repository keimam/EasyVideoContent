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


def _make_stub_provider_cls(name: str, score_fn_or_map, query_calls: list, img_bytes_color=(60, 120, 180)):
    from app.services.longform.visual.base import BaseVisualProvider, VisualAsset, VisualCandidate, compute_file_content_hash

    class _StubProv(BaseVisualProvider):
        def __init__(self, project_dir: Path):
            self._project_dir = project_dir

        @property
        def name(self) -> str:
            return name

        @property
        def is_fake(self) -> bool:
            return False

        def validate_credentials(self) -> None:
            return None

        def search_with_diagnostics(self, query: str, target_duration: float, limit: int = 3, **kwargs):
            query_calls.append((name, query, threading_ident()))
            idx_hint = kwargs.get("excluded_ids") or set()
            # Map call counter or callable for deterministic per-shot score
            call_num = sum(1 for n, _, _ in query_calls if n == name)
            if callable(score_fn_or_map):
                sc = float(score_fn_or_map(query, call_num))
            elif isinstance(score_fn_or_map, dict):
                sc = float(score_fn_or_map.get(call_num, 0.5))
            else:
                sc = float(score_fn_or_map)
            if sc <= 0:
                return [], 1, [f"{name}: below threshold"]
            cid = f"{name}_{call_num}_{abs(hash(query)) % 10000}"
            if cid in idx_hint:
                cid = f"{cid}_b"
            return (
                [
                    VisualCandidate(
                        candidate_id=cid,
                        provider=name,
                        source_type="stock_image",
                        url=f"https://example.com/{cid}.jpg",
                        author=f"{name} Author",
                        license="CC0",
                        width=1280,
                        height=720,
                        duration_seconds=target_duration,
                        tags=["folklore", "village", name],
                        relevance_score=sc,
                    )
                ],
                1,
                [],
            )

        def search_candidates(self, query: str, target_duration: float, limit: int = 3, **kwargs):
            c, _, _ = self.search_with_diagnostics(query, target_duration, limit=limit, **kwargs)
            return c

        def fetch_asset(self, candidate: VisualCandidate, dest_path: Path) -> VisualAsset:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            _write_img(dest_path, img_bytes_color)
            return VisualAsset(
                asset_id=candidate.candidate_id,
                provider=name,
                source_type=candidate.source_type,
                local_path=dest_path,
                content_hash=compute_file_content_hash(dest_path),
                author=candidate.author,
                license=candidate.license,
                width=1280,
                height=720,
                duration_seconds=candidate.duration_seconds,
            )

    return _StubProv


def threading_ident() -> int:
    import threading
    return threading.get_ident()


def test_v1_new_project_defaults_mix_best_and_legacy_defaults_priority(tmp_path: Path):
    """V1: create_project defaults to mix_best, whereas legacy manifests missing visual_search_mode load as priority."""
    import json
    from app.services.longform.store import create_project, load_manifest

    manifest_new, p_dir_new = create_project(
        project_id="v1_new_proj",
        title="New Project",
        preset_id="umum.en",
        projects_root=tmp_path,
    )
    assert manifest_new.visual_search_mode == "mix_best"
    assert manifest_new.visual_max_source_share == 0.6
    assert manifest_new.visual_avoid_consecutive_same_source is True
    assert manifest_new.fill_local_random is False

    loaded_new = load_manifest(p_dir_new)
    assert loaded_new.visual_search_mode == "mix_best"

    # Simulate legacy manifest saved prior to v1.0.1 (field omitted)
    raw = json.loads((p_dir_new / "manifest.json").read_text(encoding="utf-8"))
    for k in ("visual_search_mode", "visual_source_order", "visual_max_source_share", "visual_avoid_consecutive_same_source", "fill_local_random", "last_prompt_export_shot_ids"):
        raw.pop(k, None)
    (p_dir_new / "manifest.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")

    legacy_loaded = load_manifest(p_dir_new)
    assert legacy_loaded.visual_search_mode == "priority"


def test_v1_mix_best_parallel_2tier_budget_tiebreak_and_provider_download(tmp_path: Path, monkeypatch):
    """V1: mix_best searches all sources with 2-tier cap, breaks <=0.02 ties by least-used, and downloads via winner."""
    proj_dir = tmp_path / "v1_mix"
    proj_dir.mkdir(parents=True, exist_ok=True)
    shots = [
        Shot(
            shot_id=f"ch01_shot00{i}",
            chapter_index=1,
            shot_index=i,
            duration_seconds=3.0,
            target_frames=75,
            visual_prompt=f"ancient folklore forest lantern {i}",
            search_query=f"ancient folklore forest lantern {i}",
            search_query_en=f"ancient folklore forest lantern {i}",
            search_queries_en=[f"ancient folklore forest lantern {i}", f"lantern night woods {i}", f"extra tier3 query {i}", f"extra tier4 query {i}"],
            search_query_source="ai",
        )
        for i in range(1, 5)
    ]
    manifest = ProjectManifest(
        project_id="v1_mix",
        title="Mix Best",
        preset_id="umum.en",
        preset_snapshot={"min_relevance_score": 0.35},
        visual_search_mode="mix_best",
        visual_max_source_share=1.0,
        visual_avoid_consecutive_same_source=False,
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=shots)],
    )
    save_manifest(manifest, proj_dir)

    calls: list = []
    # Wikimedia scores 0.85 on all shots; NASA scores 0.84 (diff 0.01 <= 0.02!) except shot 3 where Wikimedia wins 0.95 vs 0.65
    wiki_cls = _make_stub_provider_cls("wikimedia", {1: 0.85, 2: 0.85, 3: 0.85, 4: 0.85, 5: 0.95, 6: 0.95, 7: 0.85, 8: 0.85}, calls)
    nasa_cls = _make_stub_provider_cls("nasa", {1: 0.84, 2: 0.84, 3: 0.84, 4: 0.84, 5: 0.65, 6: 0.65, 7: 0.84, 8: 0.84}, calls)
    svc = VisualService(proj_dir)

    def _mock_get_provider(pname: str, m: ProjectManifest):
        if pname == "wikimedia":
            return wiki_cls(proj_dir)
        if pname == "nasa":
            return nasa_cls(proj_dir)
        raise ValueError(pname)

    monkeypatch.setattr(svc, "get_provider", _mock_get_provider)
    manifest = svc.fetch_all(manifest, provider_name="auto", providers_order=["wikimedia", "nasa"], translate_keywords=False)

    actuals = [s.actual_source for s in manifest.chapters[0].shots]
    # Shot 1: tie 0.85 vs 0.84 -> wikimedia (0.85) wins
    # Shot 2: tie 0.85 vs 0.84, wiki usage=1 vs nasa usage=0 -> nasa wins!
    # Shot 3: 0.95 vs 0.65 (diff 0.30 > 0.02) -> wikimedia wins on quality!
    # Shot 4: wiki usage=2 vs nasa usage=1 -> nasa wins!
    assert actuals == ["wikimedia", "nasa", "wikimedia", "nasa"]
    # Verify 2-tier budget respected when hits found in Phase A (max 2 tiers per provider * 4 shots = 8 calls per provider)
    wiki_calls = [q for n, q, _ in calls if n == "wikimedia"]
    nasa_calls = [q for n, q, _ in calls if n == "nasa"]
    assert len(wiki_calls) == 8
    assert len(nasa_calls) == 8
    assert not any("extra tier3" in q for _, q, _ in calls)
    # Multi-source candidates populated on shot for Candidate Grid
    assert {c["provider"] for c in manifest.chapters[0].shots[0].candidates} == {"wikimedia", "nasa"}


def test_v1_diversity_cap_consecutive_and_fallback_when_no_alternative(tmp_path: Path, monkeypatch):
    """V1: Cap single source <=60% and avoid >3 consecutive only when an alternative passing candidate exists."""
    proj_dir = tmp_path / "v1_div"
    proj_dir.mkdir(parents=True, exist_ok=True)
    shots = [
        Shot(
            shot_id=f"ch01_shot00{i}",
            chapter_index=1,
            shot_index=i,
            duration_seconds=3.0,
            target_frames=75,
            visual_prompt=f"subject {i}",
            search_query=f"subject {i}",
            search_query_en=f"subject {i}",
            search_query_source="ai",
        )
        for i in range(1, 6)
    ]
    manifest = ProjectManifest(
        project_id="v1_div",
        title="Diversity",
        preset_id="umum.en",
        preset_snapshot={"min_relevance_score": 0.35},
        visual_search_mode="mix_best",
        visual_max_source_share=0.6,
        visual_avoid_consecutive_same_source=True,
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=shots)],
    )
    save_manifest(manifest, proj_dir)

    calls: list = []
    # Wikimedia strong 0.95 on all 5 shots; NASA passes at 0.70 on shots 1-4 (calls 1..8), fails (0.0) on shot 5 (calls 9+)
    wiki_cls = _make_stub_provider_cls("wikimedia", 0.95, calls)
    nasa_cls = _make_stub_provider_cls("nasa", lambda _q, n: 0.0 if n >= 9 else 0.70, calls)
    svc = VisualService(proj_dir)

    def _mock_get_provider(pname: str, m: ProjectManifest):
        return wiki_cls(proj_dir) if pname == "wikimedia" else nasa_cls(proj_dir)

    monkeypatch.setattr(svc, "get_provider", _mock_get_provider)
    manifest = svc.fetch_all(manifest, provider_name="auto", providers_order=["wikimedia", "nasa"], translate_keywords=False)

    actuals = [s.actual_source for s in manifest.chapters[0].shots]
    # Shots 1-3 -> wikimedia (60% cap = 3/5 and 3 consecutive reached);
    # Shot 4 -> forced to nasa because wiki blocked by both >3 consecutive & 60% cap while nasa passed!
    # Shot 5 -> nasa had 0 passing candidates, so wiki IS picked as fallback (never leaves empty!)
    assert actuals[:4] == ["wikimedia", "wikimedia", "wikimedia", "nasa"]
    assert actuals[4] == "wikimedia"


def test_v1_priority_mode_stops_at_first_and_local_prioritized_when_passing(tmp_path: Path, monkeypatch):
    """V1: Priority mode stops at first provider in visual_source_order, and Local leads both modes when matching."""
    proj_dir = tmp_path / "v1_prio"
    proj_dir.mkdir(parents=True, exist_ok=True)
    lib_dir = proj_dir / "assets" / "visual" / "library"
    _write_img(lib_dir / "viking_ship_harbor.jpg")

    shots = [
        Shot(
            shot_id="ch01_shot001",
            chapter_index=1,
            shot_index=1,
            duration_seconds=3.0,
            target_frames=75,
            visual_prompt="viking ship harbor",
            search_query="viking ship harbor",
            search_query_en="viking ship harbor",
            search_query_source="ai",
        ),
        Shot(
            shot_id="ch01_shot002",
            chapter_index=1,
            shot_index=2,
            duration_seconds=3.0,
            target_frames=75,
            visual_prompt="desert pyramid dunes",
            search_query="desert pyramid dunes",
            search_query_en="desert pyramid dunes",
            search_query_source="ai",
        ),
    ]
    manifest = ProjectManifest(
        project_id="v1_prio",
        title="Viking Ship Harbor",
        preset_id="umum.en",
        preset_snapshot={"min_relevance_score": 0.35},
        visual_anchor_terms=["viking"],
        visual_search_mode="priority",
        visual_source_order=["nasa", "wikimedia"],
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=shots)],
    )
    save_manifest(manifest, proj_dir)

    calls: list = []
    nasa_cls = _make_stub_provider_cls("nasa", 0.80, calls)
    wiki_cls = _make_stub_provider_cls("wikimedia", 0.99, calls)
    svc = VisualService(proj_dir)

    def _mock_get_provider(pname: str, m: ProjectManifest):
        if pname == "local":
            return LocalVisualProvider(proj_dir, fill_local_random=bool(m.fill_local_random))
        return nasa_cls(proj_dir) if pname == "nasa" else wiki_cls(proj_dir)

    monkeypatch.setattr(svc, "get_provider", _mock_get_provider)
    manifest = svc.fetch_all(manifest, provider_name="auto", providers_order=["nasa", "wikimedia", "local"], translate_keywords=False)

    # Shot 1 matches local file -> local wins first
    # Shot 2 has no local match -> priority puts NASA first, stops at NASA even though wikimedia would score 0.99!
    assert [s.actual_source for s in manifest.chapters[0].shots] == ["local", "nasa"]
    assert not any(n == "wikimedia" for n, _, _ in calls)


def test_v1_search_again_auto_and_thread_safe_rate_limit(tmp_path: Path, monkeypatch):
    """V1: Search again/preview with 'auto' shares mix_best parallel strategy; rate limiter is thread-safe."""
    from concurrent.futures import ThreadPoolExecutor
    from app.services.longform.visual.base import respect_provider_rate_limit

    proj_dir = tmp_path / "v1_srch_auto"
    proj_dir.mkdir(parents=True, exist_ok=True)
    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        visual_prompt="lighthouse ocean fog",
        search_query="lighthouse ocean fog",
        search_query_en="lighthouse ocean fog",
        search_query_source="ai",
    )
    manifest = ProjectManifest(
        project_id="v1_srch_auto",
        title="Lighthouse Fog",
        preset_id="umum.en",
        preset_snapshot={"min_relevance_score": 0.35},
        visual_search_mode="mix_best",
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])],
    )
    save_manifest(manifest, proj_dir)

    calls: list = []
    wiki_cls = _make_stub_provider_cls("wikimedia", 0.62, calls)
    nasa_cls = _make_stub_provider_cls("nasa", 0.91, calls)
    svc = VisualService(proj_dir)

    def _mock_get_provider(pname: str, m: ProjectManifest):
        return wiki_cls(proj_dir) if pname == "wikimedia" else nasa_cls(proj_dir)

    monkeypatch.setattr(svc, "get_provider", _mock_get_provider)
    monkeypatch.setattr(svc, "resolve_provider_waterfall", lambda m, **kw: ["wikimedia", "nasa"])

    cands, _grid = svc.search_and_preview_candidates(manifest, "ch01_shot001", search_query="lighthouse ocean fog", provider_name="auto")
    assert len(cands) >= 2
    assert {c.provider for c in cands} == {"wikimedia", "nasa"}
    assert cands[0].provider == "nasa"

    updated = svc.replace_shot(manifest, "ch01_shot001", provider_name="auto", prompt="lighthouse ocean fog", rebuild=False)
    assert updated.actual_source == "nasa"

    # Concurrent calls on two different providers + same provider must complete without race errors
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(respect_provider_rate_limit, "p_a" if i % 2 == 0 else "p_b", 0.01) for i in range(8)]
        for f in futs:
            f.result()


def test_v2_local_multi_upload_conflict_and_invalid_extension_handling(tmp_path: Path):
    """V2: Multi-file upload rejects unsupported extensions clearly and never silently overwrites on collision."""
    from io import BytesIO
    from app.services.longform.visual.local import ALL_SUPPORTED_EXTS, save_uploaded_library_files
    from app.services.longform.ui.i18n import t

    proj_dir = tmp_path / "v2_upload"
    mgr = LocalLibraryManager(proj_dir)

    buf1 = BytesIO()
    Image.new("RGB", (640, 360), color=(10, 20, 30)).save(buf1, format="JPEG")
    jpg_bytes_1 = buf1.getvalue()

    buf2 = BytesIO()
    Image.new("RGB", (640, 360), color=(200, 100, 50)).save(buf2, format="JPEG")
    jpg_bytes_2 = buf2.getvalue()

    res1 = save_uploaded_library_files(
        mgr,
        [("old_lighthouse_night_fog.jpg", jpg_bytes_1), ("malicious_script.exe", b"nope")],
        conflict_policy="ask",
    )
    assert len(res1["saved"]) == 1
    assert len(res1["rejected"]) == 1
    assert "malicious_script.exe" in res1["rejected"][0][0]
    assert len(res1["conflicts"]) == 0

    # Uploading same filename again with conflict_policy='ask' must NOT overwrite existing file!
    res_ask = save_uploaded_library_files(
        mgr,
        [("old_lighthouse_night_fog.jpg", jpg_bytes_2)],
        conflict_policy="ask",
    )
    assert len(res_ask["saved"]) == 0
    assert len(res_ask["conflicts"]) == 1
    assert (mgr.library_dir / "old_lighthouse_night_fog.jpg").read_bytes() == jpg_bytes_1

    # Resolving conflict with 'rename' creates a suffixed copy while keeping original intact
    res_ren = save_uploaded_library_files(mgr, res_ask["conflicts"], conflict_policy="rename")
    assert len(res_ren["saved"]) == 1
    assert res_ren["saved"][0]["filename"] == "old_lighthouse_night_fog_1.jpg"
    assert (mgr.library_dir / "old_lighthouse_night_fog.jpg").read_bytes() == jpg_bytes_1
    assert (mgr.library_dir / "old_lighthouse_night_fog_1.jpg").read_bytes() == jpg_bytes_2

    assert "old_lighthouse_night_fog.jpg" in t("local_upload_guide_text", lang="id", videos=".mp4", images=".jpg")
    assert "old_lighthouse_night_fog.jpg" in t("local_upload_guide_text", lang="en", videos=".mp4", images=".jpg")
    assert ".mp4" in ALL_SUPPORTED_EXTS and ".png" in ALL_SUPPORTED_EXTS


def test_v3_export_prompts_matching_import_and_post_import_survival(tmp_path: Path):
    """V3: Export prompts (txt/csv), boundary-safe bulk import, Ken Burns build, credits.md, and survival after fetch/AI-kw."""
    import csv
    import io
    from io import BytesIO
    from app.services.longform.schema import Segment, TtsGroup
    from app.services.longform.store import load_manifest
    from app.services.longform.visual.external_bridge import (
        EXTERNAL_ATTRIBUTION_TEXT,
        apply_bulk_external_import,
        export_external_image_prompts,
        match_external_filename_to_shot,
        plan_bulk_external_import,
    )

    proj_dir = tmp_path / "v3_ext"
    proj_dir.mkdir(parents=True, exist_ok=True)
    shots = [
        Shot(
            shot_id=f"ch01_shot00{i}",
            chapter_index=1,
            shot_index=i,
            segment_indices=[1],
            duration_seconds=1.5,
            target_frames=38,
            motion="zoom_in",
            visual_prompt=f"historical scene {i}",
            search_query=f"historical scene {i}",
            search_query_en=f"historical scene {i}",
            search_query_source="ai",
        )
        for i in range(1, 5)
    ]
    ch = Chapter(
        chapter_index=1,
        chapter_title="Ch1",
        tts_groups=[TtsGroup(group_index=1, paragraph_text="In ancient times ships sailed afar.", segments=[Segment(segment_index=1, narration_text="In ancient times ships sailed afar.")])],
        shots=shots,
    )
    manifest = ProjectManifest(
        project_id="v3_ext",
        title="External Bridge Test",
        preset_id="umum.en",
        preset_snapshot={"visual": {"style": "dramatic oil painting"}, "min_relevance_score": 0.35},
        chapters=[ch],
    )
    save_manifest(manifest, proj_dir)

    # 1. Export only selected subset (shots 2 and 4) -> persists export order [ch01_shot002, ch01_shot004]
    exp = export_external_image_prompts(manifest, proj_dir, scope="selected", selected_shot_ids=["ch01_shot002", "ch01_shot004"])
    assert exp["count"] == 2
    assert exp["shot_ids"] == ["ch01_shot002", "ch01_shot004"]
    assert "[ch01_shot002]" in exp["txt"] and "[ch01_shot004]" in exp["txt"]
    reader = list(csv.DictReader(io.StringIO(exp["csv"])))
    assert len(reader) == 2
    assert reader[0]["aspect_ratio"] == "16:9"
    assert reader[0]["visual_style"] == "dramatic oil painting"
    assert "ships sailed" in reader[0]["narration_snippet"]
    assert load_manifest(proj_dir).last_prompt_export_shot_ids == ["ch01_shot002", "ch01_shot004"]

    # 2. Boundary matching safety: ch01_shot001 must NOT match ch01_shot0010!
    sid_exact, r_exact = match_external_filename_to_shot("flow_ch01_shot001_final.png", manifest)
    assert sid_exact == "ch01_shot001" and r_exact == "exact_shot_id"

    sid_10, r_10 = match_external_filename_to_shot("ch01_shot0010.png", manifest)
    assert sid_10 is None

    # Export-sequence numeric matching: 1 -> ch01_shot002, 2 -> ch01_shot004
    sid_seq1, r_seq1 = match_external_filename_to_shot("image_1.webp", manifest)
    sid_seq2, r_seq2 = match_external_filename_to_shot("02.jpg", manifest)
    assert sid_seq1 == "ch01_shot002" and r_seq1 == "export_sequence"
    assert sid_seq2 == "ch01_shot004" and r_seq2 == "export_sequence"

    # 3. Bulk import (PNG + WEBP + bad .txt)
    png_b = BytesIO()
    Image.new("RGB", (640, 360), color=(30, 100, 200)).save(png_b, format="PNG")
    webp_b = BytesIO()
    Image.new("RGB", (640, 360), color=(200, 90, 30)).save(webp_b, format="WEBP")

    plan = plan_bulk_external_import(
        manifest,
        [
            ("ch01_shot001.png", png_b.getvalue()),
            ("image_1.webp", webp_b.getvalue()),
            ("ch01_shot0010.png", png_b.getvalue()),
            ("notes.txt", b"hello"),
        ],
    )
    assert plan[0]["matched_shot_id"] == "ch01_shot001"
    assert plan[1]["matched_shot_id"] == "ch01_shot002"
    assert plan[2]["matched_shot_id"] is None
    assert plan[3]["supported"] is False

    app_res = apply_bulk_external_import(proj_dir, manifest, plan, rebuild=True)
    assert app_res["applied_shots"] == ["ch01_shot001", "ch01_shot002"]
    assert not app_res["errors"]

    reloaded = load_manifest(proj_dir)
    sh1, sh2 = reloaded.chapters[0].shots[0], reloaded.chapters[0].shots[1]
    assert sh1.actual_source == "external_image" and sh1.locked is True
    assert sh2.actual_source == "external_image" and sh2.locked is True
    assert (proj_dir / sh1.rendered_clip).is_file()
    assert (proj_dir / sh2.rendered_clip).is_file()

    credits_md = (proj_dir / "export" / "credits.md").read_text(encoding="utf-8")
    assert EXTERNAL_ATTRIBUTION_TEXT in credits_md

    # 4. Mandatory survival verification: run Search all (fetch_all) + AI keyword update after import
    orig_h1, orig_h2 = sh1.asset_content_hash, sh2.asset_content_hash
    svc = VisualService(proj_dir)
    reloaded = svc.fetch_all(reloaded, provider_name="fake", allow_fallback=True, translate_keywords=False)
    assert reloaded.chapters[0].shots[0].actual_source == "external_image"
    assert reloaded.chapters[0].shots[0].asset_content_hash == orig_h1
    assert reloaded.chapters[0].shots[1].actual_source == "external_image"
    assert reloaded.chapters[0].shots[1].asset_content_hash == orig_h2

    save_manifest(reloaded, proj_dir)
    after_kw, kw_stats = svc.update_keywords_with_ai(force=True, mark_stale=True)
    assert kw_stats["skipped_locked"] >= 2
    assert after_kw.chapters[0].shots[0].actual_source == "external_image"
    assert after_kw.chapters[0].shots[0].locked is True
    assert after_kw.chapters[0].shots[1].actual_source == "external_image"
    assert after_kw.chapters[0].shots[1].locked is True
