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
    MAX_PARALLEL_DOWNLOADS,
    VisualService,
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


