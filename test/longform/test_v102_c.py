"""V102-C tests for Visual pipeline optimization (C3-C5), strictly isolated (0 real network/LLM calls)."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.longform.schema import Chapter, ItemStatus, MotionType, ProjectManifest, Shot
from app.services.longform.store import create_project, load_manifest, save_manifest
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    compute_file_content_hash,
)
from app.services.longform.visual.service import (
    MANIFEST_SAVE_BATCH_SIZE,
    MAX_PARALLEL_DOWNLOADS,
    MAX_PARALLEL_SOURCES,
    MAX_SEARCH_PREFETCH_SHOTS,
    VisualService,
    get_effective_search_prefetch_shots,
)


def _seed_visual_project(tmp_path: Path, pid: str, n_shots: int = 8) -> tuple[Path, ProjectManifest]:
    manifest, p_dir = create_project(
        project_id=pid,
        title="Panama Canal Test",
        preset_id="documentary_en",
        projects_root=tmp_path,
    )
    shots = [
        Shot(
            shot_id=f"ch01_shot{i + 1:03d}",
            chapter_index=1,
            shot_index=i + 1,
            visual_prompt=f"Canal excavation scene {i + 1}",
            search_query=f"canal excavation {i + 1}",
            search_query_en=f"canal excavation {i + 1}",
            search_query_source="ai",
            duration_seconds=3.0,
            target_frames=75,
            motion=MotionType.static,
            status=ItemStatus.pending,
        )
        for i in range(n_shots)
    ]
    manifest.chapters = [Chapter(chapter_index=1, chapter_title="Ch 1", shots=shots)]
    manifest.visual_anchor_terms = ["canal"]
    save_manifest(manifest, p_dir)
    return p_dir, manifest


def test_c3_parallel_downloads_bounded_and_sequential_selection_preserved(tmp_path: Path, monkeypatch):
    """Gate (a) + C3: Parallel asset downloads stay <= MAX_PARALLEL_DOWNLOADS, write atomically, and save manifest on main thread."""
    p_dir, manifest = _seed_visual_project(tmp_path, "c3_dl_bound", n_shots=9)
    assert MAX_PARALLEL_DOWNLOADS == 3

    dl_lock = threading.Lock()
    active_dl = {"cur": 0, "peak": 0, "count": 0}
    selection_thread_ids: set[int] = set()
    save_thread_ids: set[int] = set()
    main_tid = threading.get_ident()

    class _BoundedProvider(BaseVisualProvider):
        def __init__(self, prov_name: str) -> None:
            self._name = prov_name

        @property
        def name(self) -> str:
            return self._name

        @property
        def is_fake(self) -> bool:
            return False

        def validate_credentials(self) -> None:
            return None

        def search_candidates(self, query: str, target_duration: float, limit: int = 3, excluded_ids=None, **kwargs):
            selection_thread_ids.add(threading.get_ident())
            return [
                VisualCandidate(
                    candidate_id=f"{self._name}_{abs(hash(query)) % 100000}",
                    provider=self._name,
                    source_type="stock_image",
                    url=f"https://example.test/{self._name}/{abs(hash(query)) % 100000}.jpg",
                    width=1920,
                    height=1080,
                    duration_seconds=target_duration,
                    tags=["canal", "excavation"],
                    relevance_score=0.90 if self._name == "pixabay" else 0.78,
                    title=f"{self._name} canal {query}",
                )
            ]

        def fetch_asset(self, candidate: VisualCandidate, dest_path: Path) -> VisualAsset:
            with dl_lock:
                active_dl["cur"] += 1
                active_dl["count"] += 1
                active_dl["peak"] = max(active_dl["peak"], active_dl["cur"])
            try:
                time.sleep(0.06)
                dest = Path(dest_path).resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.parent / f"{dest.name}.tmp.test"
                tmp.write_bytes(b"FAKEJPEG" * 256)
                os.replace(tmp, dest)
                return VisualAsset(
                    asset_id=candidate.candidate_id,
                    provider=candidate.provider,
                    source_type=candidate.source_type,
                    local_path=dest,
                    content_hash=compute_file_content_hash(dest),
                )
            finally:
                with dl_lock:
                    active_dl["cur"] -= 1

    prov_pix = _BoundedProvider("pixabay")
    prov_wiki = _BoundedProvider("wikimedia")
    svc = VisualService(p_dir)
    monkeypatch.setattr(svc, "get_provider", lambda pn, m=None: prov_pix if pn == "pixabay" else prov_wiki)

    orig_save_timed = svc._save_manifest_timed

    def _track_save(m):
        save_thread_ids.add(threading.get_ident())
        return orig_save_timed(m)

    monkeypatch.setattr(svc, "_save_manifest_timed", _track_save)

    manifest.visual_search_mode = "mix_best"
    out_m = svc.fetch_all(
        manifest=manifest,
        providers_order=["pixabay", "wikimedia"],
        allow_fallback=False,
        translate_keywords=False,
    )

    assert active_dl["count"] == 9
    assert 2 <= active_dl["peak"] <= MAX_PARALLEL_DOWNLOADS
    assert save_thread_ids == {main_tid}
    # Verify Mix Best sequential selection still enforced <=60% single-source limit
    sources = [sh.actual_source for sh in out_m.chapters[0].shots]
    assert all(sh.asset_path and (p_dir / sh.asset_path).is_file() for sh in out_m.chapters[0].shots)
    assert len(set(sources)) == 2
    assert max(sources.count("pixabay"), sources.count("wikimedia")) <= 6


def test_c3_download_circuit_breaker_and_cache_atomic_copy(tmp_path: Path, monkeypatch):
    """C3: B1/B3 cooldown blocks uncached download cleanly and cache.download_or_get copies atomically via tmp."""
    from app.services.longform.visual import free_providers
    from app.services.longform.visual.cache import GlobalMediaCache

    free_providers.reset_provider_circuit_breakers()
    cache = GlobalMediaCache(cache_dir=tmp_path / "gcache")
    url = "https://example.test/media/atomic_clip.jpg"
    cached = cache.get_cache_path_for_url(url)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"CACHEDBYTES" * 128)

    replace_calls: list[tuple[str, str]] = []
    orig_replace = os.replace

    def _spy_replace(src, dst):
        replace_calls.append((Path(src).name, Path(dst).name))
        return orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy_replace)
    dest = tmp_path / "proj" / "shot_asset.jpg"
    out = cache.download_or_get(url, dest)
    assert out == dest.resolve()
    assert dest.is_file()
    assert any(".tmp." in src_n and dst_n == "shot_asset.jpg" for src_n, dst_n in replace_calls)

    # Active cooldown skips uncached download without crashing entire batch
    p_dir, manifest = _seed_visual_project(tmp_path / "p_cd", "c3_cd", n_shots=2)
    free_providers.trip_provider_cooldown("wikimedia", 60.0)
    try:
        svc = VisualService(p_dir)
        wiki_prov = free_providers.WikimediaVisualProvider(cache=svc.cache, credits_manager=svc.credits_manager)
        monkeypatch.setattr(svc, "get_provider", lambda pn, m=None: wiki_prov)
        with patch.object(
            svc,
            "_search_shot_across_providers",
            return_value=(
                [
                    VisualCandidate(
                        candidate_id="wikimedia_999",
                        provider="wikimedia",
                        source_type="stock_image",
                        url="https://upload.wikimedia.org/uncached_999.jpg",
                        tags=["canal"],
                        relevance_score=0.9,
                    )
                ],
                wiki_prov,
                "canal",
                [],
                None,
            ),
        ):
            out_cd = svc.fetch_all(manifest=manifest, providers_order=["wikimedia"], allow_fallback=False, translate_keywords=False)
        for sh in out_cd.chapters[0].shots:
            assert sh.fallback_reason == "no_visual_found"
            assert "circuit breaker" in (sh.no_visual_reason_summary or "").lower()
    finally:
        free_providers.reset_provider_circuit_breakers()


def test_c4_parallel_build_matches_sequential_manifest_and_shares_cpu_threads(tmp_path: Path, monkeypatch):
    """Gate (b) + C4: Parallel build_all produces identical manifest to sequential (mode fake, EFS_DEV=1) and caps -threads."""
    from app.services.longform.visual import service as vis_svc_mod

    monkeypatch.setenv("EFS_DEV", "1")
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    expected_formula = max(1, min(3, 8 // 4))
    assert expected_formula == 2
    assert vis_svc_mod.compute_ffmpeg_threads_per_build(expected_formula, cpu_count=8) == 4
    assert expected_formula * 4 <= 8

    # Seed identical fake projects: one built sequentially (workers=1), one in parallel (workers=2)
    orig_build_clip = vis_svc_mod.ShotClipBuilder.build_shot_clip

    def _build_project(sub: str, workers: int) -> tuple[dict, dict, list[int], int]:
        p_dir, manifest = _seed_visual_project(tmp_path / sub, f"c4_{sub}", n_shots=4)
        svc = VisualService(p_dir)
        out_m = svc.fetch_all(
            manifest=manifest,
            provider_name="fake",
            allow_fallback=True,
            translate_keywords=False,
        )
        thread_args_seen: list[int] = []
        save_tids: set[int] = set()
        main_tid = threading.get_ident()

        def _spy_build_clip(self_b, asset_path, output_path, motion, target_frames):
            if self_b.ffmpeg_threads is not None:
                thread_args_seen.append(int(self_b.ffmpeg_threads))
            return orig_build_clip(self_b, asset_path, output_path, motion, target_frames)

        orig_save = svc._save_manifest_timed

        def _spy_save(m):
            save_tids.add(threading.get_ident())
            return orig_save(m)

        monkeypatch.setattr(vis_svc_mod.ShotClipBuilder, "build_shot_clip", _spy_build_clip)
        monkeypatch.setattr(svc, "_save_manifest_timed", _spy_save)
        monkeypatch.setattr(vis_svc_mod, "MAX_PARALLEL_BUILDS", workers)

        built_m, stats = svc.build_all(out_m)
        assert save_tids == {main_tid}
        summary = {
            "built_count": stats["built_count"],
            "skipped_count": stats["skipped_count"],
            "shots": [
                {
                    "shot_id": s.shot_id,
                    "actual_source": s.actual_source,
                    "source_candidate_id": s.source_candidate_id,
                    "asset_content_hash": s.asset_content_hash,
                    "rendered_clip": s.rendered_clip,
                    "shot_hash": s.shot_hash,
                    "status": s.status.value,
                }
                for s in built_m.chapters[0].shots
            ],
            "ch_frames": built_m.chapters[0].visual_frames,
            "ch_dur": built_m.chapters[0].visual_duration_seconds,
        }
        return summary, stats, thread_args_seen, len(summary["shots"])

    seq_sum, seq_stats, seq_threads, _ = _build_project("seq", 1)
    par_sum, par_stats, par_threads, _ = _build_project("par", 2)

    assert seq_sum["built_count"] == 4
    assert par_sum["built_count"] == 4
    assert seq_sum == par_sum
    assert seq_threads == [8, 8, 8, 8]
    assert par_threads == [4, 4, 4, 4]
    assert par_stats["ffmpeg_threads_per_build"] == 4


def test_c4_single_failed_clip_does_not_abort_others_and_is_recorded_skipped(tmp_path: Path, monkeypatch):
    """Gate (c): If one clip build fails in build_all, sibling clips complete and failed clip is recorded as skipped."""
    from app.services.longform.visual import service as vis_svc_mod

    monkeypatch.setenv("EFS_DEV", "1")
    p_dir, manifest = _seed_visual_project(tmp_path / "fail_iso", "c4_fail_iso", n_shots=3)
    svc = VisualService(p_dir)
    out_m = svc.fetch_all(
        manifest=manifest,
        provider_name="fake",
        allow_fallback=True,
        translate_keywords=False,
    )

    orig_build_clip = vis_svc_mod.ShotClipBuilder.build_shot_clip

    def _failing_shot2(self_b, asset_path, output_path, motion, target_frames):
        if "ch01_shot002" in str(output_path):
            raise RuntimeError("Simulated corrupt media stream on shot 2")
        return orig_build_clip(self_b, asset_path, output_path, motion, target_frames)

    monkeypatch.setattr(vis_svc_mod.ShotClipBuilder, "build_shot_clip", _failing_shot2)
    monkeypatch.setattr(vis_svc_mod, "MAX_PARALLEL_BUILDS", 2)

    final_m, stats = svc.build_all(out_m)
    assert stats["built_count"] == 2
    assert stats["failed_count"] == 1
    assert stats["skipped_count"] == 1
    assert stats["failed_shot_ids"] == ["ch01_shot002"]

    by_id = {s.shot_id: s for s in final_m.chapters[0].shots}
    assert by_id["ch01_shot001"].status == ItemStatus.completed
    assert by_id["ch01_shot003"].status == ItemStatus.completed
    assert by_id["ch01_shot002"].status == ItemStatus.failed
    assert by_id["ch01_shot002"].rendered_clip is None


def test_c5_no_double_transcode_thumbnail_cache_hit_and_hash_skip(tmp_path: Path, monkeypatch):
    """C5: Eliminate libx264 double transcode on trim failure, reuse cached thumbnails, and skip unchanged shot_hash builds."""
    import subprocess
    from app.services.longform.ui.components import get_candidate_thumbnail_path, get_shot_thumbnail_path
    from app.services.longform.visual import builder as builder_mod
    from app.services.longform.visual import service as vis_svc_mod

    # 1. trim_source_video_asset must NOT invoke a secondary libx264 re-encode when stream copy fails
    vid = tmp_path / "weird_source.mp4"
    vid.write_bytes(b"FAKEVIDEO" * 500)
    with monkeypatch.context() as mp:
        mp.setattr(builder_mod, "_get_video_duration", lambda _p: 15.0)
        sub_cmds: list[list[str]] = []

        def _fake_fail_copy(cmd, **_kw):
            sub_cmds.append(list(cmd))
            return type("Res", (), {"returncode": 1, "stderr": "copy failed", "stdout": ""})()

        mp.setattr(subprocess, "run", _fake_fail_copy)
        trimmed = builder_mod.trim_source_video_asset(vid, needed_duration_seconds=4.0)
        assert trimmed is False
        assert len(sub_cmds) == 1
        assert "libx264" not in sub_cmds[0]

    # 2. Candidate thumbnail path without candidate_id is deterministic and hits cache without refetching
    cand = {"url": "https://example.test/media/panama_lock.jpg", "source_type": "stock_image"}
    dl_calls = {"n": 0}

    def _fake_dl_thumb(url: str, dest: Path) -> bool:
        dl_calls["n"] += 1
        dest.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 64)
        return True

    monkeypatch.setattr("app.services.longform.ui.components._download_image_thumbnail", _fake_dl_thumb)
    p1 = get_candidate_thumbnail_path(tmp_path, cand)
    p2 = get_candidate_thumbnail_path(tmp_path, cand)
    assert p1 is not None and p1 == p2
    assert "url_" in p1.name
    assert dl_calls["n"] == 1

    # 3. Shot build skipped (0 build_shot_clip calls) when shot_hash is unchanged, and shot thumbnail hits cache
    monkeypatch.setenv("EFS_DEV", "1")
    p_dir, manifest = _seed_visual_project(tmp_path / "skip_proj", "c5_skip", n_shots=2)
    svc = VisualService(p_dir)
    m_fetched = svc.fetch_all(manifest=manifest, provider_name="fake", allow_fallback=True, translate_keywords=False)
    m_built1, st1 = svc.build_all(m_fetched)
    assert st1["built_count"] == 2
    assert st1["skipped_count"] == 0

    sh0 = m_built1.chapters[0].shots[0]
    t1 = get_shot_thumbnail_path(p_dir, sh0)
    assert t1 is not None and t1.is_file()

    # Second build_all with unchanged hashes must build 0 clips and skip 2 cleanly without deleting cached thumbnails
    build_calls = {"n": 0}
    orig_build_clip = vis_svc_mod.ShotClipBuilder.build_shot_clip

    def _spy_build_clip(self_b, *args, **kwargs):
        build_calls["n"] += 1
        return orig_build_clip(self_b, *args, **kwargs)

    monkeypatch.setattr(vis_svc_mod.ShotClipBuilder, "build_shot_clip", _spy_build_clip)
    _m_built2, st2 = svc.build_all(m_built1)
    assert build_calls["n"] == 0
    assert st2["built_count"] == 0
    assert st2["skipped_count"] == 2
    assert t1.is_file()

    # Second thumbnail call hits cache without running ffmpeg again
    with monkeypatch.context() as mp2:
        mp2.setattr(subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("ffmpeg should not run on cached thumbnail")))
        t2 = get_shot_thumbnail_path(p_dir, sh0)
        assert t2 == t1


def test_ca1_prefetch_fake_selection_identical_to_sequential(tmp_path: Path, monkeypatch):
    """CA1.4(a): Prefetch up to MAX_SEARCH_PREFETCH_SHOTS yields identical fake selection to sequential."""
    import app.services.longform.visual.service as vis_svc_mod

    monkeypatch.setenv("EFS_DEV", "1")
    assert MAX_SEARCH_PREFETCH_SHOTS == 3

    def _run_case(sub_name: str, win_size: int) -> tuple[list[tuple[str | None, str | None, str | None]], int]:
        p_dir, manifest = _seed_visual_project(tmp_path / sub_name, f"ca1_a_{sub_name}", n_shots=8)
        # Include duplicate queries on shots 2 & 3 to exercise sequential window dedup
        manifest.chapters[0].shots[2].search_query_en = manifest.chapters[0].shots[1].search_query_en
        manifest.chapters[0].shots[2].search_query = manifest.chapters[0].shots[1].search_query
        save_manifest(manifest, p_dir)

        svc = VisualService(p_dir)
        active = {"cur": 0, "peak": 0}
        lock = threading.Lock()
        orig_search = svc._search_shot_across_providers

        def _spy_search(*args, **kwargs):
            with lock:
                active["cur"] += 1
                active["peak"] = max(active["peak"], active["cur"])
            try:
                time.sleep(0.02)
                return orig_search(*args, **kwargs)
            finally:
                with lock:
                    active["cur"] -= 1

        with monkeypatch.context() as mp:
            mp.setattr(vis_svc_mod, "get_effective_search_prefetch_shots", lambda profile=None: win_size)
            mp.setattr(svc, "_search_shot_across_providers", _spy_search)
            out_m = svc.fetch_all(manifest=manifest, provider_name="fake", allow_fallback=True, translate_keywords=False)

        picked = [
            (sh.source_candidate_id, sh.actual_source, sh.effective_query)
            for sh in out_m.chapters[0].shots
        ]
        return picked, active["peak"]

    seq_picked, seq_peak = _run_case("seq", 1)
    pref_picked, pref_peak = _run_case("pref", MAX_SEARCH_PREFETCH_SHOTS)

    assert seq_peak == 1
    assert 2 <= pref_peak <= MAX_SEARCH_PREFETCH_SHOTS
    assert pref_picked == seq_picked
    cids = [c[0] for c in pref_picked]
    assert all(c is not None for c in cids)
    assert len(set(cids)) == len(cids)


def test_ca1_prefetch_request_count_per_source_equals_sequential(tmp_path: Path, monkeypatch):
    """CA1.4(b): Prefetch never increases HTTP/search request count per source and caps total threads <= 4*3."""
    import app.services.longform.visual.service as vis_svc_mod
    from app.services.longform.visual.free_providers import reset_provider_circuit_breakers

    reset_provider_circuit_breakers()
    providers = ["wikimedia", "nasa", "openverse", "pexels"]

    class _CountingStub(BaseVisualProvider):
        def __init__(self, prov_name: str, counts: dict[str, int], conc_state: dict[str, int], lock: threading.Lock):
            self._name = prov_name
            self._counts = counts
            self._conc = conc_state
            self._lock = lock

        @property
        def name(self) -> str:
            return self._name

        @property
        def is_fake(self) -> bool:
            return False

        def validate_credentials(self) -> None:
            return None

        def search_candidates(self, query: str, target_duration: float, limit: int = 3, excluded_ids=None, **kwargs):
            with self._lock:
                self._counts[self._name] = self._counts.get(self._name, 0) + 1
                self._conc["cur"] += 1
                self._conc["peak"] = max(self._conc["peak"], self._conc["cur"])
            try:
                time.sleep(0.015)
                h = abs(hash(f"{self._name}:{query}")) % 100000
                return [
                    VisualCandidate(
                        candidate_id=f"{self._name}_{h}_{i}",
                        provider=self._name,
                        source_type="stock_image",
                        url=f"https://example.test/{self._name}/{h}_{i}.jpg",
                        width=1280,
                        height=720,
                        duration_seconds=target_duration,
                        tags=["canal", "excavation"],
                        relevance_score=round(0.92 - i * 0.03, 3),
                        title=f"canal {query} {i}",
                    )
                    for i in range(max(3, limit))
                ]
            finally:
                with self._lock:
                    self._conc["cur"] -= 1

        def fetch_asset(self, candidate: VisualCandidate, dest_path: Path) -> VisualAsset:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"\xff\xd8\xff\xe0CANAL")
            return VisualAsset(
                asset_id=candidate.candidate_id,
                provider=self._name,
                source_type="stock_image",
                local_path=dest_path,
                content_hash=compute_file_content_hash(dest_path),
            )

    def _run_counts(sub_name: str, win_size: int) -> tuple[dict[str, int], int]:
        p_dir, manifest = _seed_visual_project(tmp_path / sub_name, f"ca1_b_{sub_name}", n_shots=7)
        manifest.visual_search_mode = "mix_best"
        manifest.visual_source_order = list(providers)
        save_manifest(manifest, p_dir)

        svc = VisualService(p_dir)
        counts: dict[str, int] = {p: 0 for p in providers}
        conc = {"cur": 0, "peak": 0}
        lock = threading.Lock()
        monkeypatch.setattr(svc, "get_provider", lambda pn, m=None: _CountingStub(pn, counts, conc, lock))
        monkeypatch.setattr(svc, "resolve_provider_waterfall", lambda m, **kw: list(providers))
        with monkeypatch.context() as mp:
            mp.setattr(vis_svc_mod, "get_effective_search_prefetch_shots", lambda profile=None: win_size)
            out_m = svc.fetch_all(manifest=manifest, provider_name="auto", providers_order=list(providers), translate_keywords=False)
        assert all(sh.actual_source is not None for sh in out_m.chapters[0].shots)
        return counts, conc["peak"]

    seq_counts, seq_peak = _run_counts("seq", 1)
    pref_counts, pref_peak = _run_counts("pref", MAX_SEARCH_PREFETCH_SHOTS)

    assert seq_peak <= MAX_PARALLEL_SOURCES
    assert pref_peak <= MAX_PARALLEL_SOURCES * MAX_SEARCH_PREFETCH_SHOTS
    assert pref_counts == seq_counts
    assert all(cnt > 0 for cnt in pref_counts.values())


def test_ca1_prefetch_request_count_covers_pixabay_and_coverr(tmp_path: Path, monkeypatch):
    """CA1.4(b) + H1.5: Sequential vs prefetch HTTP counts match for real Pixabay & Coverr providers, with 24h Pixabay cache."""
    import json
    from types import SimpleNamespace
    from app.config import config as _cfg
    import app.services.longform.visual.service as vis_svc_mod
    from app.services.longform.visual.cache import GlobalMediaCache
    from app.services.longform.visual.coverr_client import reset_coverr_auth_block
    from app.services.longform.visual.free_providers import reset_provider_circuit_breakers
    from app.services.longform.visual.stock import StockVisualProvider

    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    monkeypatch.setitem(_cfg.app, "pixabay_api_keys", ["pix-secret-test-key-1234"])
    monkeypatch.setitem(_cfg.app, "coverr_api_keys", ["cov-secret-test-key-5678"])

    def _run_real_stock_counts(sub_name: str, win_size: int) -> tuple[dict[str, int], dict[str, dict], GlobalMediaCache]:
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()
        p_dir, manifest = _seed_visual_project(tmp_path / sub_name, f"ca1_pixcov_{sub_name}", n_shots=4)
        # Include 2 identical shot queries to test per-cache-key dedup lock under concurrent prefetch
        for idx, sh in enumerate(manifest.chapters[0].shots):
            q = "canal ship harbor" if idx < 2 else f"canal excavation {idx}"
            sh.search_query = q
            sh.search_query_en = q
            sh.search_queries_en = [q]
        manifest.visual_search_mode = "mix_best"
        manifest.visual_source_order = ["pixabay", "coverr"]
        save_manifest(manifest, p_dir)

        cache = GlobalMediaCache(tmp_path / sub_name / "cache")
        svc = VisualService(p_dir)
        svc.cache = cache
        http_counts = {"pixabay": 0, "coverr": 0}
        cnt_lock = threading.Lock()

        class _StreamResp:
            status_code = 200
            headers: dict = {}

            def iter_content(self, chunk_size=65536):
                yield b"\x00\x00\x00\x18ftypmp42"

            def raise_for_status(self):
                return None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def _fake_get(url, params=None, headers=None, **kw):
            u = str(url)
            if "pixabay.com/api/videos" in u:
                with cnt_lock:
                    http_counts["pixabay"] += 1
                q_str = str((params or {}).get("q") or "canal")
                tag_s = ", ".join(["canal", "excavation", "ship", "harbor", q_str])
                payload = {
                    "hits": [
                        {
                            "id": abs(hash(("pix", q_str, k))) % 900000 + 1000,
                            "duration": 8,
                            "tags": tag_s,
                            "user": "PixCreator",
                            "user_id": 42,
                            "pageURL": f"https://pixabay.com/videos/canal-{k}/",
                            "videos": {
                                "medium": {
                                    "url": f"https://cdn.pixabay.test/{abs(hash((q_str, k)))}_{k}.mp4",
                                    "width": 1920,
                                    "height": 1080,
                                    "size": 500000,
                                    "thumbnail": f"https://cdn.pixabay.test/{k}.jpg",
                                }
                            },
                        }
                        for k in range(3)
                    ]
                }
                return SimpleNamespace(
                    status_code=200,
                    headers={"X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "95", "X-RateLimit-Reset": "60"},
                    text=json.dumps(payload),
                    json=lambda: payload,
                    raise_for_status=lambda: None,
                )
            if "api.coverr.co/videos" in u:
                with cnt_lock:
                    http_counts["coverr"] += 1
                q_str = str((params or {}).get("query") or "canal")
                payload = {
                    "hits": [
                        {
                            "id": f"cov_{abs(hash(('cov', q_str, k))) % 900000}",
                            "duration": 8.0,
                            "max_width": 1920,
                            "max_height": 1080,
                            "is_vertical": False,
                            "title": f"canal excavation ship harbor {q_str}",
                            "canonical_url": f"https://coverr.co/videos/canal-{k}",
                            "creator": {"name": "CoverrArtist", "url": "https://coverr.co/creators/a"},
                            "urls": {"mp4_download": f"https://cdn.coverr.test/{abs(hash((q_str, k)))}_{k}.mp4"},
                        }
                        for k in range(2)
                    ]
                }
                return SimpleNamespace(
                    status_code=200,
                    headers={},
                    text=json.dumps(payload),
                    json=lambda: payload,
                    raise_for_status=lambda: None,
                )
            if u.endswith(".mp4"):
                return _StreamResp()
            raise AssertionError(f"Unexpected HTTP URL: {url}")

        with monkeypatch.context() as mp:
            mp.setattr("requests.get", _fake_get)
            mp.setattr(vis_svc_mod, "get_effective_search_prefetch_shots", lambda profile=None: win_size)
            mp.setattr(StockVisualProvider, "PIXABAY_MIN_INTERVAL_SEC", 0.01)
            with patch("app.services.longform.visual.service.ShotClipBuilder") as bld_cls:
                bld_cls.return_value.build_shot_clip.side_effect = lambda asset_path, out_path, **kw: (
                    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                    or Path(out_path).write_bytes(b"clip")
                    or Path(out_path)
                )
                svc.fetch_all(manifest=manifest, provider_name="auto", providers_order=["pixabay", "coverr"], translate_keywords=False)

        metrics = dict(getattr(svc, "_last_fetch_metrics", {}) or {})
        return http_counts, metrics.get("search_per_source", {}), cache

    seq_counts, seq_metrics, seq_cache = _run_real_stock_counts("seq_pixcov", 1)
    pref_counts, pref_metrics, _pref_cache = _run_real_stock_counts("pref_pixcov", MAX_SEARCH_PREFETCH_SHOTS)

    assert pref_counts == seq_counts
    assert pref_counts["pixabay"] > 0 and pref_counts["coverr"] > 0
    assert seq_metrics["pixabay"]["http_requests"] <= seq_metrics["pixabay"]["tier_attempts"]
    assert pref_metrics["pixabay"]["http_requests"] <= pref_metrics["pixabay"]["tier_attempts"]
    assert seq_metrics["pixabay"]["cache_hits"] >= 1
    assert pref_metrics["pixabay"]["cache_hits"] >= 1
    # Verify Pixabay search cache never leaks API key in filename or JSON contents
    pix_cache_files = list((seq_cache.cache_dir / "api_search").glob("pixabay_*.json"))
    assert pix_cache_files
    for cf in pix_cache_files:
        assert "pix-secret-test-key-1234" not in cf.name
        assert "pix-secret-test-key-1234" not in cf.read_text(encoding="utf-8")


def test_ca1_cooled_down_source_gets_zero_prefetch_requests(tmp_path: Path, monkeypatch):
    """CA1.4(c): Source in cooldown (before or during prefetch window) receives 0 new prefetch calls."""
    import app.services.longform.visual.service as vis_svc_mod
    from app.services.longform.visual.free_providers import (
        reset_provider_circuit_breakers,
        trip_provider_cooldown,
    )

    reset_provider_circuit_breakers()
    trip_provider_cooldown("wikimedia", 60.0)
    try:
        p_dir, manifest = _seed_visual_project(tmp_path / "cd_pref", "ca1_c", n_shots=6)
        manifest.visual_search_mode = "mix_best"
        manifest.visual_source_order = ["wikimedia", "nasa"]
        save_manifest(manifest, p_dir)

        svc = VisualService(p_dir)
        calls: dict[str, int] = {"wikimedia": 0, "nasa": 0}
        lock = threading.Lock()

        class _CooldownSpy(BaseVisualProvider):
            def __init__(self, pn: str) -> None:
                self._name = pn

            @property
            def name(self) -> str:
                return self._name

            @property
            def is_fake(self) -> bool:
                return False

            def validate_credentials(self) -> None:
                return None

            def search_candidates(self, query: str, target_duration: float, limit: int = 3, **kwargs):
                with lock:
                    calls[self._name] = calls.get(self._name, 0) + 1
                h = abs(hash(f"{self._name}:{query}")) % 100000
                return [
                    VisualCandidate(
                        candidate_id=f"{self._name}_{h}_{i}",
                        provider=self._name,
                        source_type="stock_image",
                        url=f"https://example.test/{self._name}/{h}_{i}.jpg",
                        width=1280,
                        height=720,
                        duration_seconds=target_duration,
                        tags=["canal"],
                        relevance_score=0.90,
                        title=f"canal {query}",
                    )
                    for i in range(max(2, limit))
                ]

            def fetch_asset(self, candidate: VisualCandidate, dest_path: Path) -> VisualAsset:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(b"\xff\xd8\xff\xe0NASA")
                return VisualAsset(
                    asset_id=candidate.candidate_id,
                    provider=self._name,
                    source_type="stock_image",
                    local_path=dest_path,
                    content_hash=compute_file_content_hash(dest_path),
                )

        monkeypatch.setattr(svc, "get_provider", lambda pn, m=None: _CooldownSpy(pn))
        with monkeypatch.context() as mp:
            mp.setattr(vis_svc_mod, "get_effective_search_prefetch_shots", lambda profile=None: 3)
            out_m = svc.fetch_all(manifest=manifest, provider_name="auto", providers_order=["wikimedia", "nasa"], translate_keywords=False)

        assert calls["wikimedia"] == 0
        assert calls["nasa"] > 0
        assert all(sh.actual_source == "nasa" for sh in out_m.chapters[0].shots)
    finally:
        reset_provider_circuit_breakers()


def test_ca1_network_profile_lambat_disables_prefetch_window(tmp_path: Path, monkeypatch):
    """CA1.4(d): network_profile='lambat' clamps search prefetch window to 1 (no outer shot overlap)."""
    import app.services.longform.config as lf_cfg

    monkeypatch.setenv("EFS_DEV", "1")
    with monkeypatch.context() as mp:
        mp.setattr(lf_cfg, "get_network_profile_settings", lambda profile=None: {"profile": "lambat"})
        assert get_effective_search_prefetch_shots() == 1
    with monkeypatch.context() as mp:
        mp.setattr(lf_cfg, "get_network_profile_settings", lambda profile=None: {"profile": "sedang"})
        assert get_effective_search_prefetch_shots() == MAX_SEARCH_PREFETCH_SHOTS
    with monkeypatch.context() as mp:
        mp.setattr(lf_cfg, "get_network_profile_settings", lambda profile=None: {"profile": "cepat"})
        assert get_effective_search_prefetch_shots() == MAX_SEARCH_PREFETCH_SHOTS

    p_dir, manifest = _seed_visual_project(tmp_path / "lambat_proj", "ca1_d", n_shots=5)
    svc = VisualService(p_dir)
    conc = {"cur": 0, "peak": 0}
    lock = threading.Lock()
    orig_search = svc._search_shot_across_providers

    def _spy_outer(*args, **kwargs):
        with lock:
            conc["cur"] += 1
            conc["peak"] = max(conc["peak"], conc["cur"])
        try:
            time.sleep(0.02)
            return orig_search(*args, **kwargs)
        finally:
            with lock:
                conc["cur"] -= 1

    with monkeypatch.context() as mp:
        mp.setattr(lf_cfg, "get_network_profile_settings", lambda profile=None: {"profile": "lambat"})
        mp.setattr(svc, "_search_shot_across_providers", _spy_outer)
        svc.fetch_all(manifest=manifest, provider_name="fake", allow_fallback=True, translate_keywords=False)

    assert conc["peak"] == 1


def test_ca1_manifest_consistent_and_batched_on_mid_fetch_cancel(tmp_path: Path, monkeypatch):
    """CA1.4(e): Manifest saves are batched (< N shots) yet flush all completed shots on mid-fetch stop."""
    import app.services.longform.store as store_mod
    import app.services.longform.visual.service as vis_svc_mod

    monkeypatch.setenv("EFS_DEV", "1")
    assert MANIFEST_SAVE_BATCH_SIZE == 5

    # 1. Normal 7-shot run batches saves strictly fewer than 7 times while persisting all 7 shots
    p_dir, manifest = _seed_visual_project(tmp_path / "batch_ok", "ca1_e_ok", n_shots=7)
    svc = VisualService(p_dir)
    save_calls = {"n": 0}
    orig_save = vis_svc_mod.save_manifest

    def _spy_save(m, pd):
        save_calls["n"] += 1
        return orig_save(m, pd)

    monkeypatch.setattr(vis_svc_mod, "save_manifest", _spy_save)
    svc.fetch_all(manifest=manifest, provider_name="fake", allow_fallback=True, translate_keywords=False)
    assert 1 <= save_calls["n"] <= 3
    reloaded = load_manifest(p_dir)
    assert all(sh.asset_path for sh in reloaded.chapters[0].shots)

    # 2. Mid-fetch cancel before batch_size (after shot #3) still flushes all 3 completed shots to disk
    p_stop, m_stop = _seed_visual_project(tmp_path / "batch_stop", "ca1_e_stop", n_shots=7)
    svc_stop = VisualService(p_stop)
    stop_flag = {"triggered": False}
    dl_done = {"n": 0}
    dl_lock = threading.Lock()
    orig_get_prov = svc_stop.get_provider

    class _CancelSpyProvider(BaseVisualProvider):
        def __init__(self, inner: BaseVisualProvider) -> None:
            self._inner = inner

        @property
        def name(self) -> str:
            return self._inner.name

        @property
        def is_fake(self) -> bool:
            return self._inner.is_fake

        def validate_credentials(self) -> None:
            return self._inner.validate_credentials()

        def search_candidates(self, *args, **kwargs):
            return self._inner.search_candidates(*args, **kwargs)

        def fetch_asset(self, candidate: VisualCandidate, dest_path: Path) -> VisualAsset:
            asset = self._inner.fetch_asset(candidate, dest_path)
            with dl_lock:
                dl_done["n"] += 1
                if dl_done["n"] >= 3 and not stop_flag["triggered"]:
                    stop_flag["triggered"] = True
                    store_mod.request_job_stop(p_stop)
            return asset

    monkeypatch.setattr(svc_stop, "get_provider", lambda pn, m=None: _CancelSpyProvider(orig_get_prov(pn, m)))
    # Force sequential download completion visibility by capping parallel workers to 1 so stop hits cleanly at 3 < batch_size(5)
    with monkeypatch.context() as mp:
        mp.setattr(vis_svc_mod, "MAX_PARALLEL_DOWNLOADS", 1)
        svc_stop.fetch_all(manifest=m_stop, provider_name="fake", allow_fallback=True, translate_keywords=False)

    reloaded_stop = load_manifest(p_stop)
    persisted_assets = [sh for sh in reloaded_stop.chapters[0].shots if sh.asset_path]
    assert len(persisted_assets) == 3
    for sh in persisted_assets:
        assert (p_stop / sh.asset_path).is_file()



