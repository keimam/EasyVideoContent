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


def test_a2_3_sixty_percent_cap_and_zero_alternative_fallback():
    """A2.3: 60% cap & 3-consec block Pixabay whenever alternatives exist, and fall back when Pixabay is sole passing source."""
    from app.services.longform.visual.base import VisualCandidate

    def _cand(prov: str, cid: str, score: float) -> VisualCandidate:
        return VisualCandidate(
            candidate_id=cid,
            provider=prov,
            source_type="stock_video" if prov == "pixabay" else "stock_image",
            url=f"https://example.com/{prov}/{cid}",
            local_path=None,
            relevance_score=score,
        )

    order = ["pixabay", "coverr", "wikimedia", "nasa", "openverse"]
    usage = {"pixabay": 13, "coverr": 4, "local": 4}  # 21 assigned so far out of 23 shots
    recent = ["coverr", "coverr", "coverr", "pixabay"]

    # Case 1: 14th Pixabay shot out of 23 (14/23 = 60.87% > 60%) WITH Wikimedia alternative -> MUST pick Wikimedia!
    picked = VisualService._select_candidate_mix_best(
        [_cand("pixabay", "pix_14", 0.96), _cand("wikimedia", "wiki_1", 0.688)],
        providers_order=order,
        source_usage_counts=usage,
        recent_sources=recent,
        total_project_shots=23,
        max_source_share=0.6,
        avoid_consecutive_same_source=True,
    )
    assert picked.provider == "wikimedia"

    # Case 2: 3 consecutive Pixabay shots WITH lower-score Wikimedia alternative -> MUST break streak with Wikimedia!
    picked_streak = VisualService._select_candidate_mix_best(
        [_cand("pixabay", "pix_4", 0.82), _cand("wikimedia", "wiki_2", 0.418)],
        providers_order=order,
        source_usage_counts={"pixabay": 4},
        recent_sources=["pixabay", "pixabay", "pixabay", "pixabay"],
        total_project_shots=23,
        max_source_share=0.6,
        avoid_consecutive_same_source=True,
    )
    assert picked_streak.provider == "wikimedia"

    # Case 3: 17th Pixabay shot out of 23 with ZERO passing alternatives -> MUST still return Pixabay rather than failing!
    picked_sole = VisualService._select_candidate_mix_best(
        [_cand("pixabay", "pix_17", 0.75)],
        providers_order=order,
        source_usage_counts={"pixabay": 16, "coverr": 4, "wikimedia": 2},
        recent_sources=["pixabay", "pixabay", "pixabay"],
        total_project_shots=23,
        max_source_share=0.6,
        avoid_consecutive_same_source=True,
    )
    assert picked_sole.provider == "pixabay"


class _FakeCol:
    def __init__(self, st_parent):
        self._st = st_parent

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def button(self, label, key=None, **kw):
        return self._st.button(label, key=key, **kw)


class _FakeSpinner:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeStreamlit:
    def __init__(self):
        self.session_state = {}
        self.clicked_keys = set()
        self.errors = []
        self.successes = []
        self.selectbox_value = None

    def selectbox(self, label, options=None, index=0, key=None, **kw):
        if self.selectbox_value is not None:
            val = self.selectbox_value
        elif key and key in self.session_state:
            val = self.session_state[key]
        else:
            val = (options or [""])[index]
        if key:
            self.session_state[key] = val
        return val

    def columns(self, n):
        cnt = len(n) if isinstance(n, (list, tuple)) else int(n)
        return [_FakeCol(self) for _ in range(cnt)]

    def button(self, label, key=None, **kw):
        return key in self.clicked_keys

    def spinner(self, text):
        return _FakeSpinner()

    def success(self, msg):
        self.successes.append(str(msg))

    def error(self, msg):
        self.errors.append(str(msg))

    def image(self, *a, **kw):
        pass

    def rerun(self):
        pass


def test_a2_4_test_single_image_uses_unsaved_form_state_handles_401_no_secret_leak(monkeypatch):
    """A2.4(a): Unsaved form URL + model + fake key in session_state sends request and handles 401 without key leak."""
    from unittest.mock import MagicMock
    import requests
    from app.config import config as app_cfg
    from app.services.longform.visual.image_presets import render_openai_image_preset_controls

    monkeypatch.setitem(app_cfg.app, "openai_image_base_url", "")
    monkeypatch.setitem(app_cfg.app, "openai_image_model", "")
    monkeypatch.setitem(app_cfg.app, "openai_image_api_keys", [])
    monkeypatch.setitem(app_cfg.app, "openai_image_size", "")

    st_mock = _FakeStreamlit()
    fake_secret = "sk-fake-secret-998877"
    st_mock.session_state["openai_image_base_url_input"] = "https://api.deepinfra.com/v1/openai"
    st_mock.session_state["openai_image_model_input"] = "black-forest-labs/FLUX-1-schnell"
    st_mock.session_state["openai_image_api_keys_input"] = fake_secret
    st_mock.session_state["openai_image_size_input"] = "1344x768"
    st_mock.clicked_keys = {"btn_test_single_image_settings"}

    seen = {}

    def _fake_post(url, json=None, headers=None, **kw):
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = dict(headers or {})
        resp = MagicMock()
        resp.status_code = 401
        resp.text = f"Unauthorized for Bearer {fake_secret}"
        return resp

    monkeypatch.setattr(requests, "post", _fake_post)

    render_openai_image_preset_controls(st_module=st_mock, app_settings={})

    assert seen["url"] == "https://api.deepinfra.com/v1/openai/images/generations"
    assert seen["json"]["model"] == "black-forest-labs/FLUX-1-schnell"
    assert seen["headers"].get("Authorization") == f"Bearer {fake_secret}"
    res = st_mock.session_state.get("_openai_img_test_result")
    assert isinstance(res, dict) and res["ok"] is False
    assert res["category"] == "auth"
    assert fake_secret not in res["message"]
    assert st_mock.errors and fake_secret not in st_mock.errors[0]


def test_a2_4_test_single_image_empty_url_returns_config_error(monkeypatch):
    """A2.4(b): Empty URL in both form and saved config yields clean missing-URL error without crashing."""
    from app.config import config as app_cfg
    from app.services.longform.visual.image_presets import render_openai_image_preset_controls

    monkeypatch.setitem(app_cfg.app, "openai_image_base_url", "")
    monkeypatch.setitem(app_cfg.app, "openai_image_model", "flux-model")
    monkeypatch.setitem(app_cfg.app, "openai_image_api_keys", [])

    st_mock = _FakeStreamlit()
    st_mock.session_state["openai_image_base_url_input"] = ""
    st_mock.session_state["openai_image_model_input"] = "flux-model"
    st_mock.clicked_keys = {"btn_test_single_image_settings"}

    render_openai_image_preset_controls(st_module=st_mock, app_settings={})

    res = st_mock.session_state.get("_openai_img_test_result")
    assert isinstance(res, dict) and res["ok"] is False
    assert res["category"] == "config"
    assert "URL" in res["message"] or "url" in res["message"].lower()


def test_a2_4_changing_preset_clears_stale_test_result(monkeypatch):
    """A2.4(c): Changing preset, URL, key, or model clears stale _openai_img_test_result from session_state."""
    from app.config import config as app_cfg
    from app.services.longform.visual.image_presets import render_openai_image_preset_controls

    monkeypatch.setitem(app_cfg.app, "openai_image_base_url", "https://api.deepinfra.com/v1/openai")
    monkeypatch.setitem(app_cfg.app, "openai_image_model", "black-forest-labs/FLUX-1-schnell")
    monkeypatch.setitem(app_cfg.app, "openai_image_api_keys", ["sk-fake-1"])

    st_mock = _FakeStreamlit()
    st_mock.selectbox_value = "deepinfra"
    render_openai_image_preset_controls(st_module=st_mock, app_settings=dict(app_cfg.app))
    st_mock.session_state["_openai_img_test_result"] = {"ok": False, "message": "Old DeepInfra error"}

    # Switch preset -> stale result must be cleared automatically
    st_mock.selectbox_value = "together"
    render_openai_image_preset_controls(st_module=st_mock, app_settings=dict(app_cfg.app))
    assert "_openai_img_test_result" not in st_mock.session_state



