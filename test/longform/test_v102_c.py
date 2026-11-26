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
