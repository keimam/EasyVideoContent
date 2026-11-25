"""Tests for V102 Addendum 1 (A1.1 Coverr 403 rate_limited vs auth_blocked, A1.2 network_profile UI)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, Segment, Shot, TtsGroup
from app.services.longform.store import create_project, save_manifest
from app.services.longform.visual.service import VisualService


def _write_local_img(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), (20, 40, 80)).save(path, format="JPEG")


def _make_coverr_ok_resp(video_id: str = "cv1", title: str = "mountain forest mist") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.text = "{}"
    resp.json.return_value = {
        "hits": [
            {
                "id": video_id,
                "duration": 10,
                "max_width": 1920,
                "max_height": 1080,
                "is_vertical": False,
                "title": title,
                "tags": ["mountain", "forest", "mist"],
                "thumbnail": f"https://cdn.coverr.co/t/{video_id}.jpg",
                "urls": {"mp4_download": f"https://cdn.coverr.co/v/{video_id}.mp4"},
            }
        ]
    }
    return resp


def test_a1_1a_coverr_403_first_request_is_auth_blocked_zero_followup(monkeypatch):
    """A1.1(a): HTTP 403 before any 200 for this key immediately triggers auth_blocked with 0 follow-up requests."""
    import requests
    from app.services.longform.visual.base import VisualError, reset_io_telemetry, snapshot_io_telemetry
    from app.services.longform.visual.coverr_client import (
        CoverrAuthBlockedError,
        get_coverr_auth_status,
        reset_coverr_auth_block,
        search_coverr_videos,
    )
    from app.services.longform.visual.free_providers import reset_provider_circuit_breakers

    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    reset_io_telemetry()
    monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-badkey-secret-001"])

    http_calls: list[str] = []

    def _fake_get(url, *a, **kw):
        http_calls.append(str(url))
        resp = MagicMock()
        resp.status_code = 403
        resp.headers = {}
        resp.text = "Invalid API key Bearer cov-badkey-secret-001 denied"
        return resp

    monkeypatch.setattr(requests, "get", _fake_get)
    try:
        try:
            search_coverr_videos("mountain", 2)
            raise AssertionError("Expected CoverrAuthBlockedError on first 403")
        except CoverrAuthBlockedError:
            pass

        try:
            search_coverr_videos("ocean", 2)
            raise AssertionError("Expected CoverrAuthBlockedError on follow-up call")
        except CoverrAuthBlockedError:
            pass

        assert len(http_calls) == 1
        st = get_coverr_auth_status()
        assert st["auth_blocked"] is True
        assert st["rate_limited"] is False
        assert "cov-badkey-secret-001" not in str(st["error"])

        snap = snapshot_io_telemetry()["provider_status_events"]["coverr"]
        assert snap["auth_blocked"] is True
        assert snap["rate_limited"] is False
        assert "Invalid API key" in str(snap["last_error"])
        assert "cov-badkey-secret-001" not in str(snap["last_error"])

        # 401 anytime -> auth_blocked even after prior HTTP 200
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()
        reset_io_telemetry()
        monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-revoked-secret-002"])
        seq = [_make_coverr_ok_resp("v_ok"), 401]
        seq_calls = [0]

        def _fake_401(url, *a, **kw):
            idx = seq_calls[0]
            seq_calls[0] += 1
            if idx == 0:
                return seq[0]
            r = MagicMock()
            r.status_code = 401
            r.headers = {}
            r.text = "Token revoked Bearer cov-revoked-secret-002"
            return r

        monkeypatch.setattr(requests, "get", _fake_401)
        assert len(search_coverr_videos("forest", 2)) == 1
        try:
            search_coverr_videos("river", 2)
            raise AssertionError("Expected CoverrAuthBlockedError on 401 after 200")
        except CoverrAuthBlockedError:
            pass
        assert get_coverr_auth_status()["auth_blocked"] is True
        assert get_coverr_auth_status()["rate_limited"] is False
    finally:
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()
        reset_io_telemetry()


def test_a1_1b_coverr_200_200_then_403_rate_limited_cooldown_recovery_and_3x_escalation(
    tmp_path: Path, monkeypatch
):
    """A1.1(b): 200, 200, then 403 records rate_limited (not auth_blocked), resumes after cooldown, and escalates on 3 consecutive 403s."""
    import requests
    from loguru import logger
    from app.services.longform import app_paths, config as lf_cfg
    from app.services.longform.visual.base import VisualError, reset_io_telemetry
    from app.services.longform.visual.coverr_client import (
        get_coverr_auth_status,
        reset_coverr_auth_block,
        search_coverr_videos,
    )
    from app.services.longform.visual.free_providers import (
        check_provider_cooldown,
        reset_provider_circuit_breakers,
    )

    reset_coverr_auth_block()
    reset_provider_circuit_breakers()
    reset_io_telemetry()

    data_dir = tmp_path / "a11_data"
    monkeypatch.setattr(app_paths, "get_data_dir", lambda create=False: data_dir)
    lf_cfg._CONFIG_CACHE = None
    monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-validkey-secret-999"])
    monkeypatch.setenv("EFS_DEV", "1")

    fake_now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    long_403_body = (
        '{"error":"Hourly Demo limit reached for token Bearer cov-validkey-secret-999; '
        + ("detail_pad_" * 20)
        + '"}'
    )
    responses = [
        _make_coverr_ok_resp("v1"),
        _make_coverr_ok_resp("v2"),
        403,  # strike 1 -> cooldown 120s
        403,  # strike 2 (after cooldown) -> cooldown 120s
        403,  # strike 3 (after cooldown) -> run_paused for remainder of run
    ]
    call_idx = [0]

    def _fake_get(url, *a, **kw):
        idx = call_idx[0]
        call_idx[0] += 1
        item = responses[idx] if idx < len(responses) else 403
        if isinstance(item, int):
            r = MagicMock()
            r.status_code = item
            r.headers = {}
            r.text = long_403_body
            return r
        return item

    monkeypatch.setattr(requests, "get", _fake_get)

    captured_logs: list[str] = []
    sink_id = logger.add(lambda m: captured_logs.append(str(m)), level="WARNING")
    try:
        # 2x HTTP 200 -> establishes key success history
        assert len(search_coverr_videos("forest", 2)) == 1
        assert len(search_coverr_videos("mist", 2)) == 1
        assert get_coverr_auth_status()["has_succeeded"] is True

        # 3rd HTTP request returns 403 -> rate_limited (NOT auth_blocked), trips B3 normal cooldown (120s)
        try:
            search_coverr_videos("river", 2)
            raise AssertionError("Expected VisualError on 403 rate_limited")
        except VisualError:
            pass

        st1 = get_coverr_auth_status()
        assert st1["auth_blocked"] is False
        assert st1["rate_limited"] is True
        assert st1["run_paused"] is False
        assert st1["consecutive_403"] == 1
        assert 115.0 <= check_provider_cooldown("coverr") <= 121.0
        assert len(str(st1["error"])) <= 200
        assert "cov-validkey-secret-999" not in str(st1["error"])
        assert any("rate_limited" in msg and "Hourly Demo limit reached" in msg for msg in captured_logs)
        assert all("cov-validkey-secret-999" not in msg for msg in captured_logs)

        # Before cooldown expires: Coverr MUST NOT issue an HTTP request
        fake_now[0] += 60.0
        try:
            search_coverr_videos("cloud", 2)
        except VisualError:
            pass
        assert call_idx[0] == 3

        # After cooldown expires (120s): Coverr IS called again!
        fake_now[0] += 65.0
        assert check_provider_cooldown("coverr") == 0.0
        try:
            search_coverr_videos("ocean", 2)  # strike 2 -> 403
        except VisualError:
            pass
        assert call_idx[0] == 4
        assert get_coverr_auth_status()["consecutive_403"] == 2
        assert get_coverr_auth_status()["run_paused"] is False

        # After 2nd cooldown expires -> 3rd consecutive 403 -> run_paused for remainder of run
        fake_now[0] += 125.0
        try:
            search_coverr_videos("valley", 2)  # strike 3 -> 403
        except VisualError:
            pass
        assert call_idx[0] == 5
        st3 = get_coverr_auth_status()
        assert st3["auth_blocked"] is False
        assert st3["rate_limited"] is True
        assert st3["run_paused"] is True

        # Even if cooldown time elapses again, run_paused blocks HTTP calls for remainder of run
        fake_now[0] += 500.0
        try:
            search_coverr_videos("lake", 2)
        except VisualError:
            pass
        assert call_idx[0] == 5

        # Changing API key resets run_paused/has_succeeded/rate_limited
        monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-brandnew-key-777"])
        st_new = get_coverr_auth_status()
        assert st_new["run_paused"] is False
        assert st_new["rate_limited"] is False
        assert st_new["has_succeeded"] is False
        assert st_new["auth_blocked"] is False

        # Now test VisualService.fetch_all persistence: 200 -> 403 stores rate_limited & <=200 redacted last_error
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()
        reset_io_telemetry()
        monkeypatch.setitem(config.app, "coverr_api_keys", ["cov-validkey-secret-999"])
        svc_calls = [0]

        def _svc_get(url, *a, **kw):
            u = str(url)
            if "coverr" in u:
                idx = svc_calls[0]
                svc_calls[0] += 1
                if idx == 0:
                    return _make_coverr_ok_resp("s1", title="snow forest")
                r = MagicMock()
                r.status_code = 403
                r.headers = {}
                r.text = long_403_body
                return r
            raise AssertionError(f"Unexpected HTTP call: {url}")

        monkeypatch.setattr(requests, "get", _svc_get)
        proj_dir = tmp_path / "a11_proj"
        _write_local_img(proj_dir / "assets" / "visual" / "library" / "snow_winter_forest.jpg")
        shots = [
            Shot(
                shot_id=f"ch01_shot00{i}",
                chapter_index=1,
                shot_index=i,
                duration_seconds=3.0,
                target_frames=75,
                visual_prompt="snow mountain forest",
                search_query="snow mountain forest",
                search_query_en="snow mountain forest",
                search_query_source="ai",
            )
            for i in range(1, 3)
        ]
        manifest = ProjectManifest(
            project_id="a11_proj",
            title="A1.1 Rate Limited",
            preset_id="umum.en",
            preset_snapshot={"min_relevance_score": 0.35},
            chapters=[Chapter(chapter_index=1, chapter_title="Ch1", shots=shots)],
            visual_anchor_terms=["snow", "forest"],
            visual_search_mode="mix_best",
        )
        save_manifest(manifest, proj_dir)
        svc = VisualService(proj_dir)
        svc.fetch_all(manifest, providers_order=["coverr", "local"], translate_keywords=False)

        metrics = json.loads((proj_dir / "visual_metrics.json").read_text(encoding="utf-8"))
        cov_st = metrics["source_status"]["coverr"]
        assert cov_st["auth_blocked"] is False
        assert cov_st["rate_limited"] is True
        assert cov_st["ok"] is False
        assert cov_st["last_error"] is not None
        assert 120 < len(cov_st["last_error"]) <= 200
        assert "Hourly Demo limit reached" in cov_st["last_error"]
        assert "cov-validkey-secret-999" not in cov_st["last_error"]
    finally:
        logger.remove(sink_id)
        lf_cfg._CONFIG_CACHE = None
        reset_coverr_auth_block()
        reset_provider_circuit_breakers()
        reset_io_telemetry()


def test_a1_1c_visual_ui_distinguishes_auth_blocked_and_rate_limited_messages(tmp_path: Path, monkeypatch):
    """A1.1(c): Visual UI renders distinct warnings for Coverr auth_blocked vs rate_limited in ID and EN."""
    proj_root = tmp_path / "projects"
    proj_root.mkdir()
    app_file = str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)

    for pid, cov_status in [
        ("proj_auth_blk", {"ok": False, "cooldown": 0, "auth_blocked": True, "rate_limited": False, "timeout": 0, "last_error": "HTTP 403: Invalid key"}),
        ("proj_rate_lim", {"ok": False, "cooldown": 1, "auth_blocked": False, "rate_limited": True, "timeout": 0, "last_error": "HTTP 403: Demo limit"}),
    ]:
        manifest, p_dir = create_project(pid, pid, "umum.id", language="id-ID", projects_root=proj_root)
        manifest.chapters = [
            Chapter(
                chapter_index=1,
                chapter_title="Ch1",
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
                        visual_prompt="forest",
                        search_query="forest",
                        search_query_en="forest",
                        status=ItemStatus.pending,
                    )
                ],
                status=ItemStatus.completed,
            )
        ]
        save_manifest(manifest, p_dir)
        (p_dir / "visual_metrics.json").write_text(
            json.dumps({
                "project_id": pid,
                "total_seconds": 10.0,
                "source_status": {"coverr": cov_status},
            }),
            encoding="utf-8",
        )

    # Indonesian
    monkeypatch.setitem(config.ui, "language", "id")
    at_auth_id = AppTest.from_file(app_file, default_timeout=20)
    at_auth_id.query_params["project"] = "proj_auth_blk"
    at_auth_id.query_params["step"] = "visual"
    at_auth_id.query_params["lang"] = "id"
    at_auth_id.run()
    assert not at_auth_id.exception
    warn_auth_id = "\n".join(str(w.value) for w in at_auth_id.warning)
    assert "Coverr dilewati: API key ditolak (403). Cek key di API & Penyedia AI." in warn_auth_id

    at_rl_id = AppTest.from_file(app_file, default_timeout=20)
    at_rl_id.query_params["project"] = "proj_rate_lim"
    at_rl_id.query_params["step"] = "visual"
    at_rl_id.query_params["lang"] = "id"
    at_rl_id.run()
    assert not at_rl_id.exception
    warn_rl_id = "\n".join(str(w.value) for w in at_rl_id.warning)
    assert "Coverr dijeda sementara: batas pemakaian (403) tercapai." in warn_rl_id
    assert "API key ditolak" not in warn_rl_id

    # English
    monkeypatch.setitem(config.ui, "language", "en")
    at_auth_en = AppTest.from_file(app_file, default_timeout=20)
    at_auth_en.query_params["project"] = "proj_auth_blk"
    at_auth_en.query_params["step"] = "visual"
    at_auth_en.query_params["lang"] = "en"
    at_auth_en.run()
    assert not at_auth_en.exception
    warn_auth_en = "\n".join(str(w.value) for w in at_auth_en.warning)
    assert "Coverr skipped: API key rejected (403). Check your key in AI & API Providers." in warn_auth_en

    at_rl_en = AppTest.from_file(app_file, default_timeout=20)
    at_rl_en.query_params["project"] = "proj_rate_lim"
    at_rl_en.query_params["step"] = "visual"
    at_rl_en.query_params["lang"] = "en"
    at_rl_en.run()
    assert not at_rl_en.exception
    warn_rl_en = "\n".join(str(w.value) for w in at_rl_en.warning)
    assert "Coverr temporarily paused: usage limit (403) reached." in warn_rl_en
    assert "API key rejected" not in warn_rl_en


def test_a1_2_settings_studio_network_profile_control_saves_lambat_timeout_15(tmp_path: Path, monkeypatch):
    """A1.2 (B3.2): Pengaturan Studio renders settings_network_profile_label and selecting 'lambat' calls save_network_profile('lambat') -> timeout == 15."""
    from app.services.longform import app_paths, config as lf_cfg, first_run
    from app.services.longform.ui.i18n import t

    data_dir = (tmp_path / "a12_data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EFS_DATA_DIR", str(data_dir))
    monkeypatch.setattr(app_paths, "get_data_dir", lambda create=False: data_dir)
    monkeypatch.setattr("app.services.longform.update_checker.start_background_update_check", lambda *a, **kw: False)
    monkeypatch.setattr("app.services.version_checker.get_available_update", lambda *a, **kw: None)
    lf_cfg._CONFIG_CACHE = None
    first_run.mark_setup_completed(skipped=False)
    monkeypatch.setitem(config.ui, "language", "id")

    saved_calls: list[str] = []
    orig_save = lf_cfg.save_network_profile

    def _spy_save(profile: str, *, config_path: Path | None = None) -> str:
        saved_calls.append(profile)
        return orig_save(profile, config_path=config_path)

    monkeypatch.setattr(lf_cfg, "save_network_profile", _spy_save)

    main_file = str(Path(__file__).resolve().parents[2] / "webui" / "Main.py")
    at_main = AppTest.from_file(main_file, default_timeout=40)
    at_main.run(timeout=40)
    assert not at_main.exception

    # Open Settings dialog / Pengaturan Umum -> Pengaturan Studio
    at_main.button(key="open_settings_dialog_button").click().run(timeout=30)
    assert not at_main.exception

    np_boxes = [sb for sb in at_main.selectbox if sb.key == "general_settings_network_profile_select"]
    assert len(np_boxes) == 1
    assert np_boxes[0].label == t("settings_network_profile_label")
    assert np_boxes[0].value == "normal"
    assert lf_cfg.get_network_profile_settings()["timeout"] == 6

    np_boxes[0].set_value("lambat").run(timeout=30)
    assert not at_main.exception
    assert "lambat" in saved_calls
    assert lf_cfg.get_network_profile_settings()["timeout"] == 15
    assert lf_cfg.get_network_profile_settings()["profile"] == "lambat"
    assert [sb for sb in at_main.selectbox if sb.key == "general_settings_network_profile_select"][0].value == "lambat"

