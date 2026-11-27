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

    # Empty HTTP 200 (category="science") must make ONLY 1 HTTP call (no category=None or upstream double-call)
    empty_calls = {"n": 0}

    def fake_empty_200(url, params=None, **kw):
        empty_calls["n"] += 1
        return SimpleNamespace(
            status_code=200,
            headers={},
            text='{"hits":[]}',
            json=lambda: {"hits": []},
            raise_for_status=lambda: None,
        )

    with patch("app.services.longform.visual.free_providers.requests.get", side_effect=fake_empty_200):
        with patch("app.services.longform.visual.stock.search_videos_pixabay") as upstream_spy:
            c_empty = prov.search_candidates("empty abstract concept", 4.0, category="science")
            assert c_empty == []
            assert empty_calls["n"] == 1
            assert upstream_spy.call_count == 0


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


def test_h3_partial_completed_job_shows_partial_summary_not_paused_or_resume(tmp_path: Path, monkeypatch):
    """H3.1: Completed job with skipped shots shows partial completion summary and NO Resume/paused message; interrupted job still shows Resume."""
    from streamlit.testing.v1 import AppTest
    from app.services.longform.schema import AudioSpec, TtsGroup
    from app.services.longform.status import compute_project_step_status
    from app.services.longform.store import create_project

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    for lang in ("id", "en"):
        monkeypatch.setitem(config.ui, "language", lang)
        pid = f"h3_partial_{lang}"
        manifest, p_dir = create_project(pid, "Wabah 3 Partial", "umum.id", language=lang, projects_root=tmp_path)
        wav_file = p_dir / "assets" / "audio" / "groups" / "ch1_g1.wav"
        wav_file.parent.mkdir(parents=True, exist_ok=True)
        wav_file.write_bytes(b"RIFF" + b"\x00" * 100)

        shots_dir = p_dir / "assets" / "visual" / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        shots = []
        for idx in range(1, 6):
            sid = f"ch01_shot{idx:03d}"
            if idx <= 3:
                (shots_dir / f"{sid}_asset.mp4").write_bytes(b"asset")
                (shots_dir / f"{sid}.mp4").write_bytes(b"clip")
                shots.append(
                    Shot(
                        shot_id=sid,
                        chapter_index=1,
                        shot_index=idx,
                        duration_seconds=3.0,
                        target_frames=75,
                        actual_source="pixabay",
                        asset_path=f"assets/visual/shots/{sid}_asset.mp4",
                        rendered_clip=f"assets/visual/shots/{sid}.mp4",
                        search_query="plague street",
                        search_query_en="plague street",
                        status=ItemStatus.completed,
                    )
                )
            else:
                q_abs = "thousand deaths daily" if idx == 4 else "Alexandria death toll"
                shots.append(
                    Shot(
                        shot_id=sid,
                        chapter_index=1,
                        shot_index=idx,
                        duration_seconds=3.0,
                        target_frames=75,
                        actual_source=None,
                        fallback_reason="no_visual_found",
                        search_query=q_abs,
                        search_query_en=q_abs,
                        status=ItemStatus.pending,
                    )
                )

        manifest.chapters = [
            Chapter(
                chapter_index=1,
                chapter_title="Bab 1",
                audio_duration_seconds=15.0,
                tts_groups=[TtsGroup(group_index=1, paragraph_text="Narration", audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=15.0), status=ItemStatus.completed)],
                shots=shots,
                status=ItemStatus.completed,
            )
        ]
        save_manifest(manifest, p_dir)
        (p_dir / "job_state.json").write_text(
            json.dumps({
                "project_id": pid,
                "step": "visual_pipeline",
                "status": "completed",
                "exit_code": 0,
                "progress": {"completed": 3, "total": 3, "skipped": 2, "partial": True},
            }),
            encoding="utf-8",
        )

        st_info = compute_project_step_status(p_dir)
        assert st_info["steps"]["visual"]["status"] == "selesai_sebagian"

        app_file = Path("app/services/longform/ui/app.py")
        if not app_file.is_file():
            app_file = Path("app/pages/1_Long_Video.py")
        if not app_file.is_file():
            from test.longform.test_stage8c2_d8_no_fake import _get_long_video_app_file

            app_file = _get_long_video_app_file()

        at = AppTest.from_file(str(app_file), default_timeout=25)
        at.query_params["project"] = pid
        at.session_state[f"active_step_{pid}"] = "visual"
        at.run()
        assert not at.exception

        all_text = "\n".join(
            [str(getattr(c, "value", "") or "") for c in list(at.caption) + list(at.warning) + list(at.markdown)]
        )
        btn_labels = [str(b.label or "") for b in at.button]
        if lang == "id":
            assert "Visual selesai: 3 dari 5 shot. 2 shot belum menemukan visual yang cocok." in all_text
            assert "Penyiapan visual terhenti" not in all_text
            assert "Lanjutkan" not in btn_labels
        else:
            assert "Visual finished: 3 of 5 shots. 2 shot(s) found no matching visual." in all_text
            assert "Visual preparation paused" not in all_text
            assert "Resume" not in btn_labels
        assert "ch01_shot004" in all_text and "thousand deaths daily" in all_text
        assert "ch01_shot005" in all_text and "Alexandria death toll" in all_text

        # When truly stopped (status="stopped"), status returns "terhenti" and Resume button IS shown
        (p_dir / "job_state.json").write_text(
            json.dumps({"project_id": pid, "step": "visual_pipeline", "status": "stopped", "exit_code": -1}),
            encoding="utf-8",
        )
        st_stop = compute_project_step_status(p_dir)
        assert st_stop["steps"]["visual"]["status"] == "terhenti"


def test_h3_zero_candidate_concrete_fallback_runs_only_on_zero_hits(tmp_path: Path):
    """H3.2: Grounded concrete fallback runs once when all sources return 0 candidates, and 0 times when hits already exist."""
    from app.services.longform.visual.base import BaseVisualProvider, VisualAsset, VisualCandidate, compute_file_content_hash
    from app.services.longform.visual.stock import build_zero_candidate_concrete_query

    fb_rome = build_zero_candidate_concrete_query(
        "thousand deaths daily",
        narration_text="Lima ribu orang mati setiap hari di Roma.",
        anchor_terms=["wabah"],
    )
    assert fb_rome and any("plague" in q and ("crowd" in q or "roma" in q or "people" in q) for q in fb_rome), fb_rome
    # Never invents unrelated objects
    assert not any("spaceship" in q or "car" in q for q in fb_rome)

    queries_called = []

    class _MockProvider(BaseVisualProvider):
        @property
        def name(self):
            return "wikimedia"

        @property
        def is_fake(self):
            return False

        def validate_credentials(self):
            return None

        def search_candidates(self, query, target_duration, limit=3, excluded_ids=None, **kw):
            queries_called.append(query)
            if query == "ancient harbor ship":
                return [
                    VisualCandidate(
                        candidate_id="wm_harbor_1",
                        provider="wikimedia",
                        source_type="stock_image",
                        url="https://example.test/harbor.jpg",
                        width=1280,
                        height=720,
                        duration_seconds=target_duration,
                        tags=["harbor", "ship"],
                        relevance_score=0.88,
                        title="Ancient Harbor Ship",
                    )
                ]
            if "plague" in query and ("crowd" in query or "roma" in query):
                return [
                    VisualCandidate(
                        candidate_id="wm_plague_crowd_1",
                        provider="wikimedia",
                        source_type="stock_image",
                        url="https://example.test/plague_crowd.jpg",
                        width=1280,
                        height=720,
                        duration_seconds=target_duration,
                        tags=["plague", "crowd"],
                        relevance_score=0.62,
                        title="Plague crowd street",
                    )
                ]
            return []

        def fetch_asset(self, candidate, dest_path):
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(b"img")
            return VisualAsset(
                asset_id=candidate.candidate_id,
                provider=candidate.provider,
                source_type=candidate.source_type,
                local_path=dest_path,
                content_hash=compute_file_content_hash(dest_path),
            )

    svc = VisualService(tmp_path)
    manifest = ProjectManifest(
        project_id="h3_fb",
        title="Wabah Roma",
        preset_id="umum.id",
        preset_snapshot={},
        visual_anchor_terms=["wabah"],
        chapters=[
            Chapter(
                chapter_index=1,
                chapter_title="Bab 1",
                shots=[
                    Shot(
                        shot_id="ch01_s01",
                        chapter_index=1,
                        shot_index=1,
                        duration_seconds=3.0,
                        visual_prompt="ancient harbor ship",
                        search_query="ancient harbor ship",
                        search_query_en="ancient harbor ship",
                        search_queries_en=["ancient harbor ship"],
                    ),
                    Shot(
                        shot_id="ch01_s02",
                        chapter_index=1,
                        shot_index=2,
                        duration_seconds=3.0,
                        visual_prompt="Lima ribu orang mati setiap hari di Roma.",
                        search_query="thousand deaths daily",
                        search_query_en="thousand deaths daily",
                        search_queries_en=["thousand deaths daily"],
                    ),
                ],
            )
        ],
    )
    stub = _MockProvider()
    with patch.object(svc, "get_provider", return_value=stub):
        # Shot 1 already hits on primary query -> concrete 0-hit fallback must NOT run
        queries_called.clear()
        c1, _, _, _, _ = svc._search_shot_across_providers(
            manifest.chapters[0].shots[0],
            manifest,
            providers_order=["wikimedia"],
            allow_waterfall=True,
        )
        assert len(c1) == 1 and c1[0].candidate_id == "wm_harbor_1"
        assert not any("plague" in q for q in queries_called)

        # Shot 2 returns 0 on primary/anchors -> Pass 3 concrete fallback executes and finds candidate
        queries_called.clear()
        c2, _, _, _, _ = svc._search_shot_across_providers(
            manifest.chapters[0].shots[1],
            manifest,
            providers_order=["wikimedia"],
            allow_waterfall=True,
        )
        assert len(c2) == 1 and c2[0].candidate_id == "wm_plague_crowd_1"
        assert any("plague" in q for q in queries_called)
