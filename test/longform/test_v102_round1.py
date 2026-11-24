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


def test_b3_network_profile_lambat_timeout_15_and_threshold_3_plus_default_normal(tmp_path: Path, monkeypatch):
    """B3: Missing key defaults to 'normal'; profile 'lambat' passes timeout=15 to requests.get and trips cooldown on 3rd failure."""
    import requests
    from app.services.longform import app_paths, config as lf_cfg
    from app.services.longform.visual.base import VisualError
    from app.services.longform.visual.cache import GlobalMediaCache
    from app.services.longform.visual.coverr_client import reset_coverr_auth_block, search_coverr_videos
    from app.services.longform.visual.free_providers import (
        cached_json_get,
        reset_provider_circuit_breakers,
    )

    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    data_dir = tmp_path / "b3_data"
    monkeypatch.setattr(app_paths, "get_data_dir", lambda create=False: data_dir)

    # 1. Missing key defaults to 'normal'
    legacy_cfg = tmp_path / "legacy_longform.toml"
    legacy_cfg.write_text('[cache]\nmax_size_gb = 10.0\n', encoding="utf-8")
    monkeypatch.setattr(lf_cfg, "get_config_file_path", lambda: legacy_cfg)
    assert lf_cfg.get_network_profile_settings(reload=True) == {
        "profile": "normal",
        "timeout": 6,
        "failure_threshold": 2,
        "cooldown_seconds": 120,
    }

    # 2. Saving 'lambat' persists and preserves base config sections
    user_cfg = data_dir / "config" / "longform.toml"
    monkeypatch.setattr(lf_cfg, "get_config_file_path", lambda: user_cfg if user_cfg.is_file() else legacy_cfg)
    saved_prof = lf_cfg.save_network_profile("lambat")
    assert saved_prof == "lambat"
    assert lf_cfg.get_network_profile_settings(reload=True) == {
        "profile": "lambat",
        "timeout": 15,
        "failure_threshold": 3,
        "cooldown_seconds": 60,
    }
    assert lf_cfg.load_longform_config().capcut_tested_versions == ("8.9.0", "9.7.1")

    seen_timeouts = []

    def _always_timeout(url, *a, **kw):
        seen_timeouts.append(kw.get("timeout"))
        raise requests.exceptions.ReadTimeout("slow network")

    monkeypatch.setattr(requests, "get", _always_timeout)
    monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-test-key"])

    cache = GlobalMediaCache(tmp_path / "b3_cache")
    try:
        # 3. Free provider uses timeout=15 and stays active through failures 1 & 2, trips on 3rd
        for idx in range(3):
            try:
                cached_json_get(
                    "openverse",
                    "https://api.openverse.org/v1/images/",
                    params={"q": f"term{idx}"},
                    cache=cache,
                    min_interval_sec=0,
                )
                assert False
            except VisualError:
                pass
        assert seen_timeouts == [15, 15, 15]

        # 4th call must hit cooldown (no requests.get call)
        try:
            cached_json_get(
                "openverse",
                "https://api.openverse.org/v1/images/",
                params={"q": "term3"},
                cache=cache,
                min_interval_sec=0,
            )
            assert False
        except VisualError as exc:
            assert "cooldown active" in str(exc).lower()
        assert len(seen_timeouts) == 3

        # 4. Coverr (B1) also uses B3 timeout=15
        try:
            search_coverr_videos("snow", 3)
            assert False
        except VisualError:
            pass
        assert seen_timeouts[-1] == 15
    finally:
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()
        lf_cfg._CONFIG_CACHE = None


def test_b4_max_parallel_sources_caps_thread_pool_at_4(tmp_path: Path, monkeypatch):
    """B4: With 6 active providers in mix_best mode, ThreadPoolExecutor is created with max_workers == 4."""
    from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
    import app.services.longform.visual.service as svc_mod
    from app.services.longform.visual.base import BaseVisualProvider, VisualAsset, VisualCandidate

    assert svc_mod.MAX_PARALLEL_SOURCES == 4

    observed_workers = []

    class _RecordingExecutor(RealThreadPoolExecutor):
        def __init__(self, max_workers=None, *a, **kw):
            observed_workers.append(max_workers)
            super().__init__(max_workers=max_workers, *a, **kw)

    class _StubProvider(BaseVisualProvider):
        def __init__(self, pname: str) -> None:
            self._pname = pname

        @property
        def name(self) -> str:
            return self._pname

        @property
        def is_fake(self) -> bool:
            return False

        def validate_credentials(self) -> None:
            return None

        def search_candidates(self, query, target_duration, limit=3, excluded_ids=None, **kwargs):
            return [
                VisualCandidate(
                    candidate_id=f"{self._pname}_1",
                    provider=self._pname,
                    source_type="stock_image",
                    url=f"https://example.com/{self._pname}_1.jpg",
                    local_path=None,
                    tags=["snow", "forest"],
                    relevance_score=0.75,
                )
            ]

        def fetch_asset(self, candidate, dest_path):
            raise NotImplementedError

    monkeypatch.setattr(svc_mod, "ThreadPoolExecutor", _RecordingExecutor)

    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        duration_seconds=3.0,
        target_frames=75,
        visual_prompt="snow forest",
        search_query="snow forest",
        search_query_en="snow forest",
        search_query_source="ai",
    )
    manifest = ProjectManifest(
        project_id="b4_proj",
        title="B4 Pool Cap",
        preset_id="umum.en",
        preset_snapshot={"min_relevance_score": 0.35},
        chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=[shot])],
        visual_search_mode="mix_best",
    )
    svc = VisualService(tmp_path / "b4_proj")
    monkeypatch.setattr(svc, "get_provider", lambda pname, _m=None: _StubProvider(pname))

    svc._search_shot_across_providers(
        shot,
        manifest,
        providers_order=["pixabay", "coverr", "pexels", "wikimedia", "nasa", "openverse"],
        search_mode="mix_best",
    )
    assert observed_workers == [4]


def test_b5_per_preset_api_key_switch_preserves_and_isolates_keys(monkeypatch):
    """B5(a): Together key -> switch to DeepInfra -> DeepInfra key box empty -> switch back -> Together key returns."""
    from app.services.longform.visual.image_presets import render_openai_image_preset_controls

    class _DummyCol:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _MockSt:
        def __init__(self):
            self.session_state = {}
            self.clicked = set()
            self.sel_value = "together"

        def selectbox(self, label, options=None, index=0, key=None, **kw):
            if key:
                self.session_state[key] = self.sel_value
            return self.sel_value

        def columns(self, n):
            cnt = len(n) if isinstance(n, (list, tuple)) else int(n)
            return [_DummyCol() for _ in range(cnt)]

        def button(self, label, key=None, disabled=False, **kw):
            return (not disabled) and (key in self.clicked)

        def spinner(self, *_a):
            return _DummyCol()

        def success(self, *_a):
            pass

        def error(self, *_a):
            pass

        def image(self, *_a, **_kw):
            pass

        def rerun(self):
            pass

    app_s = {
        "openai_image_base_url": "https://api.together.xyz/v1",
        "openai_image_model": "black-forest-labs/FLUX.1-schnell",
        "openai_image_size": "1344x768",
        "openai_image_api_keys": ["tgp-secret-together-111"],
    }
    monkeypatch.setitem(config.app, "openai_image_base_url", app_s["openai_image_base_url"])
    monkeypatch.setitem(config.app, "openai_image_api_keys", list(app_s["openai_image_api_keys"]))
    monkeypatch.delitem(config.app, "openai_image_api_keys_by_preset", raising=False)

    st_ui = _MockSt()
    st_ui.session_state["openai_image_base_url_input"] = "https://api.together.xyz/v1"
    st_ui.session_state["openai_image_api_keys_input"] = "tgp-secret-together-111"

    # 1. Switch to DeepInfra and apply preset -> DeepInfra starts empty, Together key preserved in dict
    st_ui.sel_value = "deepinfra"
    st_ui.clicked = {"btn_apply_openai_image_preset"}
    render_openai_image_preset_controls(st_module=st_ui, app_settings=app_s)

    assert app_s["openai_image_base_url"] == "https://api.deepinfra.com/v1/openai"
    assert app_s["openai_image_api_keys"] == []
    assert st_ui.session_state["openai_image_api_keys_input"] == ""
    assert app_s["openai_image_api_keys_by_preset"]["together"] == "tgp-secret-together-111"

    # 2. Switch back to Together and apply preset -> Together key returns cleanly
    st_ui.sel_value = "together"
    st_ui.clicked = {"btn_apply_openai_image_preset"}
    render_openai_image_preset_controls(st_module=st_ui, app_settings=app_s)

    assert app_s["openai_image_base_url"] == "https://api.together.xyz/v1"
    assert app_s["openai_image_api_keys"] == ["tgp-secret-together-111"]
    assert st_ui.session_state["openai_image_api_keys_input"] == "tgp-secret-together-111"


def test_b5_legacy_single_key_migration_without_dict():
    """B5(b): Legacy config without openai_image_api_keys_by_preset migrates key to inferred preset with zero loss."""
    from app.services.longform.visual.image_presets import migrate_openai_image_api_keys_by_preset

    legacy = {
        "openai_image_base_url": "https://api.deepinfra.com/v1/openai",
        "openai_image_api_keys": ["di-legacy-key-777"],
    }
    rt = dict(legacy)
    saved = {}
    migrated = migrate_openai_image_api_keys_by_preset(
        legacy,
        save_fn=lambda k, v: saved.setdefault(k, v),
        runtime_cfg=rt,
    )
    assert migrated == {"deepinfra": "di-legacy-key-777"}
    assert legacy["openai_image_api_keys_by_preset"] == {"deepinfra": "di-legacy-key-777"}
    assert rt["openai_image_api_keys_by_preset"] == {"deepinfra": "di-legacy-key-777"}
    assert saved["openai_image_api_keys_by_preset"] == {"deepinfra": "di-legacy-key-777"}


def test_b5_redact_api_secrets_masks_key_in_single_image_test(monkeypatch):
    """B5(c): redact_api_secrets and test_single_openai_image mask preset API keys on 401/403 failures."""
    import requests
    from app.services.longform.visual.image_presets import (
        redact_api_secrets,
        test_single_openai_image,
    )

    secret = "tgp-secret-mask-4455"
    assert secret not in redact_api_secrets(f"Invalid Bearer {secret} key {secret}", secret)

    def _fake_post(*a, **kw):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = f"Unauthorized token {secret}"
        return resp

    monkeypatch.setattr(requests, "post", _fake_post)
    res = test_single_openai_image(
        base_url="https://api.together.xyz/v1",
        api_key=secret,
        model="black-forest-labs/FLUX.1-schnell",
        lang="id",
    )
    assert res["ok"] is False
    assert res["category"] == "auth"
    assert secret not in res["message"]
