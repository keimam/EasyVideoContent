"""Tests for Stage 8B.2 AI features (F1 Relevance, F2 Translation, F3 AI Video, F4 Polish)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services.longform.config import load_ai_video_pricing
from app.services.longform.hashing import compute_audio_group_hash
from app.services.longform.llm_adapter import LLMResponse
from app.services.longform.schema import Chapter, ItemStatus, Shot, TtsGroup
from app.services.longform.shot import compute_shot_hash
from app.services.longform.store import create_project, load_manifest, save_manifest
from app.services.longform.visual.ai_relevance import (
    check_project_relevance,
    check_shot_relevance,
    get_vision_readiness,
)
from app.services.longform.visual.ai_video import (
    build_ai_video_prompt,
    estimate_ai_video_cost,
    generate_ai_video_for_needs_review,
    generate_ai_video_for_shot,
    list_configured_ai_video_providers,
    plan_needs_review_ai_video_batch,
    round_up_supported_duration,
)
from app.services.longform.visual.base import compute_file_content_hash
from app.services.longform.visual.keyword_translator import translate_chapter_queries_to_english
from app.services.longform.visual.service import VisualService


def _get_long_video_app_file() -> str:
    return str(Path(__file__).resolve().parents[2] / "webui" / "pages" / "1_🎬_Long_Video.py")


def _make_sample_jpg(path: Path, color=(40, 120, 200)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (640, 360), color=color)
    img.save(path, format="JPEG")
    return path


def _make_sample_mp4(path: Path, duration: float = 4.0, color: str = "blue") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=640x360:r=25:d={duration}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"ffmpeg failed: {res.stderr}"
    return path


def _seed_project_with_shots(
    tmp_path: Path,
    pid: str,
    *,
    lang: str = "id-ID",
    queries: list[str] | None = None,
    needs_review: bool = False,
) -> tuple[Path, list[Shot]]:
    _, p_dir = create_project(
        project_id=pid,
        title="Test Project",
        preset_id="automotive.muscle_car",
        language=lang,
        projects_root=tmp_path,
    )
    m = load_manifest(p_dir)
    q_list = queries or ["mobil balap klasik di jalanan malam", "mesin v8 bertenaga tinggi"]
    shots: list[Shot] = []
    for idx, q in enumerate(q_list, start=1):
        sid = f"ch01_shot{idx:03d}"
        asset_rel = f"assets/visual/shots/{sid}_asset.jpg"
        _make_sample_jpg(p_dir / asset_rel, color=(30 * idx, 90, 180))
        ahash = compute_file_content_hash(p_dir / asset_rel)
        shash = compute_shot_hash(
            shot_id=sid,
            duration_seconds=3.2,
            target_frames=80,
            motion="zoom_in",
            asset_content_hash=ahash,
            fps=25,
            requested_provider="pixabay",
            actual_provider="pixabay",
        )
        shots.append(
            Shot(
                shot_id=sid,
                chapter_index=1,
                shot_index=idx,
                segment_indices=[idx],
                duration_seconds=3.2,
                target_frames=80,
                requested_source="pixabay",
                actual_source="pixabay",
                visual_prompt=q,
                search_query=q,
                asset_path=asset_rel,
                asset_content_hash=ahash,
                shot_hash=shash,
                needs_review=needs_review,
                status=ItemStatus.completed,
            )
        )
    m.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Narasi paragraf satu.",
                    audio={"audio_hash": compute_audio_group_hash("Narasi paragraf satu."), "file_path": "audio/g1.wav"},
                    segments=[{"segment_index": i, "narration_text": q_list[i - 1]} for i in range(1, len(q_list) + 1)],
                )
            ],
            shots=shots,
        )
    ]
    save_manifest(m, p_dir)
    return p_dir, shots


# ---------------------------------------------------------------------------
# F1: AI Relevance Check (Vision Capability, 429 Retry, Cache, Needs Review)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,cfg_key,model_name,expected_vision",
    [
        ("openai", "openai_model_name", "gpt-4o-mini", True),
        ("openrouter", "openrouter_model_name", "qwen/qwen2.5-vl-72b-instruct", True),
        ("gemini", "gemini_model_name", "gemini-2.5-flash", True),
        ("deepseek", "deepseek_model_name", "deepseek-chat", False),
        ("openrouter", "openrouter_model_name", "openrouter/free", False),
    ],
)
def test_f1_vision_model_detection(monkeypatch, provider, cfg_key, model_name, expected_vision):
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setitem(config.app, "llm_provider", provider)
    monkeypatch.setitem(config.app, f"{provider}_api_key", "AIzaDummyKeyForTestCheck_123456" if provider == "gemini" else "sk-test-key-1234567890")
    monkeypatch.setitem(config.app, cfg_key, model_name)
    monkeypatch.setitem(config.app, "llm_vision_override", "")

    info = get_vision_readiness(provider)
    assert info["ready"] is True
    assert info["vision_supported"] is expected_vision


def test_f1_cache_per_asset_hash_429_retry_and_needs_review_preserves_asset(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setitem(config.app, "llm_provider", "openai")
    monkeypatch.setitem(config.app, "openai_api_key", "sk-test-openai-vision-key")
    monkeypatch.setitem(config.app, "openai_model_name", "gpt-4o-mini")
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)

    p_dir, _ = _seed_project_with_shots(tmp_path / "proj", "f1_proj", needs_review=False)
    m = load_manifest(p_dir)
    shot = m.chapters[0].shots[0]
    orig_asset_path = shot.asset_path
    orig_asset_hash = shot.asset_content_hash
    orig_shot_hash = shot.shot_hash

    calls = 0

    def _flaky_transport(img_bytes: bytes, prompt_text: str) -> str:
        nonlocal calls
        assert len(img_bytes) > 50
        calls += 1
        if calls == 1:
            raise RuntimeError("HTTP 429 rate limit exceeded")
        # Return >15 words to ensure strict truncation to <=15 words
        return json.dumps({
            "relevan": "tidak",
            "alasan": "Gambar menunjukkan langit biru kosong bukan mobil klasik berotot di lintasan balap malam hari yang dramatis",
        })

    cache_dir = tmp_path / "cache"
    res1 = check_shot_relevance(
        shot, m.chapters[0], p_dir, m, cache_dir=cache_dir, transport_fn=_flaky_transport
    )
    assert calls == 2  # 429 retried cleanly
    assert res1["cached"] is False
    assert shot.ai_relevance_status == "tidak"
    assert len(shot.ai_relevance_reason.split()) <= 15
    assert shot.needs_review is True
    # Asset & hash completely untouched!
    assert shot.asset_path == orig_asset_path
    assert shot.asset_content_hash == orig_asset_hash
    assert shot.shot_hash == orig_shot_hash

    # Second check on shot 1 hits asset-hash cache without calling model again
    res1_cached = check_shot_relevance(
        shot, m.chapters[0], p_dir, m, cache_dir=cache_dir, transport_fn=_flaky_transport
    )
    assert calls == 2
    assert res1_cached["cached"] is True

    # Project run checks uncached shot 2 (calls=3) while hitting cache for shot 1 (cached>=1)
    res2 = check_project_relevance(m, p_dir, cache_dir=cache_dir, transport_fn=_flaky_transport)
    assert calls == 3
    assert res2["cached"] >= 1


def test_f1_ui_toggle_default_off_disabled_non_vision_and_recheck(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", "id")

    p_dir, _ = _seed_project_with_shots(tmp_path, "f1_ui_proj")

    # 1. Non-vision model -> toggle disabled + message
    monkeypatch.setitem(config.app, "llm_provider", "deepseek")
    monkeypatch.setitem(config.app, "deepseek_api_key", "sk-ds-test-12345678")
    monkeypatch.setitem(config.app, "deepseek_model_name", "deepseek-chat")

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = "f1_ui_proj"
    at.session_state["active_step_f1_ui_proj"] = "visual"
    at.run(timeout=25)
    assert not at.exception
    cb_rel = [c for c in at.checkbox if c.key == "cb_ai_rel_f1_ui_proj"]
    assert len(cb_rel) == 1
    assert cb_rel[0].value is False
    assert cb_rel[0].disabled is True
    assert any("tidak mendukung gambar" in cap.value for cap in at.caption)

    # 2. Vision model -> toggle enabled (default False), checking shows estimate; recheck button works
    monkeypatch.setitem(config.app, "llm_provider", "openai")
    monkeypatch.setitem(config.app, "openai_api_key", "sk-openai-vision-test-123")
    monkeypatch.setitem(config.app, "openai_model_name", "gpt-4o-mini")

    at2 = AppTest.from_file(_get_long_video_app_file())
    at2.query_params["project"] = "f1_ui_proj"
    at2.session_state["active_step_f1_ui_proj"] = "visual"
    at2.run(timeout=25)
    cb_rel2 = [c for c in at2.checkbox if c.key == "cb_ai_rel_f1_ui_proj"]
    assert cb_rel2[0].disabled is False
    assert cb_rel2[0].value is False

    cb_rel2[0].check().run(timeout=25)
    assert any("Cek relevansi AI" in info.value for info in at2.info)

    # Mock single-shot recheck button
    def _fake_recheck(shot, chapter, project_dir, manifest, **_kw):
        shot.ai_relevance_status = "ragu"
        shot.ai_relevance_reason = "Objek sedikit jauh"
        shot.needs_review = True
        return {"verdict": {"relevan": "ragu", "alasan": "Objek sedikit jauh"}, "cached": False}

    monkeypatch.setattr("app.services.longform.visual.ai_relevance.check_shot_relevance", _fake_recheck)
    recheck_btns = [b for b in at2.button if (b.key or "").startswith("btn_recheck_ai_f1_ui_proj_")]
    assert len(recheck_btns) == 1
    recheck_btns[0].click().run(timeout=25)
    assert not at2.exception
    assert any("Ragu" in w.value and "Objek sedikit jauh" in w.value for w in at2.warning)


# ---------------------------------------------------------------------------
# F2: Batch English Translation per Chapter, Cache, Fallback, Old Hash Intact
# ---------------------------------------------------------------------------


def test_f2_batch_translation_per_chapter_cache_and_fallback(tmp_path: Path, monkeypatch):
    p_dir, shots = _seed_project_with_shots(
        tmp_path / "proj",
        "f2_batch",
        lang="id-ID",
        queries=["warung soto tradisional ramai pembeli", "koki memasak mie goreng panas"],
    )
    m = load_manifest(p_dir)

    llm_calls = 0

    class _MockAdapter:
        def generate(self, prompt: str):
            nonlocal llm_calls
            llm_calls += 1
            return LLMResponse(
                content=json.dumps({
                    "ch01_shot001": "traditional indonesian street food stall",
                    "ch01_shot002": "chef cooking hot fried noodles",
                }),
                provider="openrouter",
                model="mock-llm",
                duration_seconds=0.1,
            )

    cache_dir = tmp_path / "cache"
    res1 = translate_chapter_queries_to_english(
        shots,
        preset_snapshot=m.preset_snapshot,
        project_lang="id-ID",
        cache_dir=cache_dir,
        llm_adapter=_MockAdapter(),
    )
    assert llm_calls == 1  # Batched in 1 call per chapter!
    assert res1["translated"] == 2
    assert shots[0].search_query_en == "traditional indonesian street food stall"
    assert shots[1].search_query_en == "chef cooking hot fried noodles"

    # Reset in-memory field on shots, call again -> hits cache (0 LLM calls)
    for s in shots:
        s.search_query_en = None
    res2 = translate_chapter_queries_to_english(
        shots,
        preset_snapshot=m.preset_snapshot,
        project_lang="id-ID",
        cache_dir=cache_dir,
        llm_adapter=_MockAdapter(),
    )
    assert llm_calls == 1
    assert res2["cached"] == 2
    assert shots[0].search_query_en == "traditional indonesian street food stall"

    # Fallback when LLM not ready -> preserves original keywords cleanly with mild warning
    monkeypatch.setattr(
        "app.services.longform.visual.keyword_translator.get_llm_readiness",
        lambda *_a, **_kw: {"ready": False},
    )
    s_fresh = Shot(shot_id="ch02_shot001", chapter_index=2, shot_index=1, search_query="pasar malam ramai")
    res_fb = translate_chapter_queries_to_english(
        [s_fresh],
        project_lang="id-ID",
        cache_dir=tmp_path / "empty_cache",
        llm_adapter=None,
    )
    assert res_fb["translated"] == 0
    assert res_fb["warning"] is not None
    assert s_fresh.search_query == "pasar malam ramai"


def test_f2_preserves_old_locked_built_shot_hash_during_fetch_all(tmp_path: Path, monkeypatch):
    p_dir, shots = _seed_project_with_shots(tmp_path / "proj", "f2_hash", lang="id-ID")
    m = load_manifest(p_dir)
    # Lock shot 1 with pre-built hash; keep shot 2 unlocked & needing fetch
    shots[0].locked = True
    locked_hash_before = shots[0].shot_hash
    shots[1].asset_path = None
    shots[1].asset_content_hash = None
    shots[1].shot_hash = None
    save_manifest(m, p_dir)

    monkeypatch.setattr(
        "app.services.longform.visual.keyword_translator.get_llm_readiness",
        lambda *_a, **_kw: {"ready": False},
    )
    svc = VisualService(p_dir)
    m_after = svc.fetch_all(m, provider_name="fake", allow_fallback=True, translate_keywords=True)
    assert m_after.chapters[0].shots[0].shot_hash == locked_hash_before
    assert m_after.chapters[0].shots[0].search_query_en is None


def test_f2_ui_shows_original_and_english_keywords_and_prefills_search_again(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", "id")

    p_dir, _ = _seed_project_with_shots(tmp_path, "f2_ui", lang="id-ID", queries=["nasi goreng panas"])
    m = load_manifest(p_dir)
    m.chapters[0].shots[0].search_query_en = "hot indonesian fried rice"
    save_manifest(m, p_dir)

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = "f2_ui"
    at.session_state["active_step_f2_ui"] = "visual"
    at.run(timeout=25)

    assert not at.exception
    md_text = "\n".join(x.value for x in at.markdown)
    assert "nasi goreng panas" in md_text
    assert "hot indonesian fried rice" in md_text
    srch_inputs = [ti for ti in at.text_input if ti.key == "txt_srch_f2_ui_ch01_shot001"]
    assert len(srch_inputs) == 1
    assert srch_inputs[0].value == "hot indonesian fried rice"


# ---------------------------------------------------------------------------
# F3: AI Video per Shot, Pricing TOML, Confirmation Dialog, Budget Cap, Single Rebuild
# ---------------------------------------------------------------------------


def test_f3_provider_priority_duration_rounding_and_cost_table(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setitem(config.app, "ofox_api_key", "ofox-test-key")
    monkeypatch.setitem(config.app, "wavespeed_api_keys", ["ws-test-key"])

    providers = list_configured_ai_video_providers()
    assert len(providers) >= 2
    assert providers[0]["provider_id"] == "wavespeed"  # WaveSpeed prioritized first!

    # Duration rounding UP to smallest supported >= shot duration
    assert round_up_supported_duration(3.2, [4, 5, 6, 8, 10, 12, 15]) == 4
    assert round_up_supported_duration(6.8, [5, 10]) == 10
    assert round_up_supported_duration(20.0, [4, 5, 8, 12]) == 12

    # Editable config/longform.toml pricing lookup + source URL/date + unknown fallback
    default_pricing = load_ai_video_pricing()
    assert default_pricing["updated_at"] == "2026-11-17"
    assert len(default_pricing["models"]) >= 12
    for row_id, row_meta in default_pricing["models"].items():
        assert row_meta["per_second"] > 0, f"Invalid price for {row_id}"
        assert str(row_meta.get("url", "")).startswith("https://"), f"Missing source URL for {row_id}"
        assert row_meta.get("checked_at"), f"Missing checked_at for {row_id}"

    # Corrected Seedance 2.0 Fast ($0.100/s) and Wan 2.2 Ultra Fast ($0.010/s)
    known_cost = estimate_ai_video_cost("bytedance/seedance-2.0-fast/text-to-video", 5, "720p")
    assert known_cost["known"] is True
    assert known_cost["estimated_usd"] == pytest.approx(0.50, abs=1e-4)
    assert known_cost["url"].startswith("https://")
    wan_fast = estimate_ai_video_cost("wavespeed-ai/wan-2.2/t2v-480p-ultra-fast", 4, "480p")
    assert wan_fast["known"] is True
    assert wan_fast["estimated_usd"] == pytest.approx(0.04, abs=1e-4)

    custom_toml = tmp_path / "custom_longform.toml"
    custom_toml.write_text(
        '[ai_video_pricing]\nupdated_at = "2026-12-01"\n[ai_video_pricing.models]\n"my-custom-model" = 0.12\n',
        encoding="utf-8",
    )
    pricing = load_ai_video_pricing(custom_toml)
    assert pricing["updated_at"] == "2026-12-01"
    est_custom = estimate_ai_video_cost("my-custom-model", 4, "720p", config_path=custom_toml)
    assert est_custom["known"] is True
    assert est_custom["estimated_usd"] == pytest.approx(0.48, abs=1e-4)

    unknown = estimate_ai_video_cost("brand-new-unknown-model", 5, "720p", config_path=custom_toml)
    assert unknown["known"] is False
    assert unknown["estimated_usd"] is None


def test_f3_single_shot_generation_locks_and_rebuilds_only_target_clip(tmp_path: Path):
    p_dir, shots = _seed_project_with_shots(tmp_path / "proj", "f3_single", lang="en-US", needs_review=True)
    m = load_manifest(p_dir)
    # Pre-build shot 2 clip so we verify its file & hash are NOT touched!
    shot2_clip = p_dir / "assets/visual/shots/ch01_shot002.mp4"
    _make_sample_mp4(shot2_clip, duration=3.2, color="green")
    shots[1].rendered_clip = "assets/visual/shots/ch01_shot002.mp4"
    shot2_hash_before = shots[1].shot_hash
    save_manifest(m, p_dir)
    shot2_mtime_before = shot2_clip.stat().st_mtime_ns

    gen_src_mp4 = tmp_path / "mock_ai_vid.mp4"
    _make_sample_mp4(gen_src_mp4, duration=4.0, color="red")

    def _mock_video_gen(*, dest: Path, **_kw) -> Path:
        shutil.copyfile(gen_src_mp4, dest)
        return dest

    updated = generate_ai_video_for_shot(
        project_dir=p_dir,
        manifest=m,
        shot_id="ch01_shot001",
        provider_id="wavespeed",
        model_id="bytedance/seedance-2.0-fast/text-to-video",
        prompt="classic muscle car roaring down neon highway",
        resolution="720p",
        duration_seconds=4,
        rebuild=True,
        video_generator_fn=_mock_video_gen,
    )

    assert updated.locked is True
    assert updated.needs_review is False
    assert updated.actual_source == "AI Video · WaveSpeed · bytedance/seedance-2.0-fast/text-to-video"
    assert (p_dir / updated.rendered_clip).is_file()
    # Verify shot 2 was NOT rebuilt
    m_reloaded = load_manifest(p_dir)
    assert m_reloaded.chapters[0].shots[1].shot_hash == shot2_hash_before
    assert shot2_clip.stat().st_mtime_ns == shot2_mtime_before


def test_f3_bulk_needs_review_enforces_budget_cap_and_never_runs_automatically(tmp_path: Path):
    p_dir, _ = _seed_project_with_shots(
        tmp_path / "proj",
        "f3_budget",
        queries=["scene one", "scene two", "scene three"],
        needs_review=True,
    )
    m = load_manifest(p_dir)
    gen_src_mp4 = tmp_path / "mock_batch.mp4"
    _make_sample_mp4(gen_src_mp4, duration=4.0, color="purple")

    calls = 0

    def _mock_video_gen(*, dest: Path, **_kw) -> Path:
        nonlocal calls
        calls += 1
        shutil.copyfile(gen_src_mp4, dest)
        return dest

    # Each 4s clip at 720p with seedance-2.0-fast costs 4 * 0.100 = $0.40
    # Cap at $0.85 -> allows 2 shots ($0.80) and MUST stop before 3rd shot ($1.20 > $0.85)!
    res = generate_ai_video_for_needs_review(
        project_dir=p_dir,
        manifest=m,
        provider_id="wavespeed",
        model_id="bytedance/seedance-2.0-fast/text-to-video",
        resolution="720p",
        max_budget_usd=0.85,
        video_generator_fn=_mock_video_gen,
    )
    assert calls == 2
    assert res["generated"] == 2
    assert res["skipped_budget"] == 1
    assert res["spent_usd"] <= 0.85

    # Ensure standard run_pipeline NEVER automatically calls AI video
    plan_only = plan_needs_review_ai_video_batch(m, "wavespeed", "bytedance/seedance-2.0-fast/text-to-video", "720p")
    assert plan_only["count"] == 1


@pytest.mark.parametrize("ui_lang", ["id", "en"])
def test_f3_ui_tab_ai_video_requires_confirmation_dialog_and_launches_job(tmp_path: Path, monkeypatch, ui_lang):
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", ui_lang)
    monkeypatch.setitem(config.app, "wavespeed_api_keys", ["ws-test-ui-key"])

    started_cmds: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "app.services.longform.ui.components._start_job_safe",
        lambda pid, step, cmd, **_kw: started_cmds.append((step, cmd)) or True,
    )

    pid = f"f3_ui_{ui_lang}"
    _seed_project_with_shots(tmp_path, pid, needs_review=True)

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.session_state[f"active_step_{pid}"] = "visual"
    at.run(timeout=25)
    assert not at.exception

    gen_btns = [b for b in at.button if (b.key or "").startswith(f"btn_gen_aiv_{pid}_")]
    assert len(gen_btns) == 1
    gen_btns[0].click().run(timeout=25)
    assert not at.exception
    # No paid job launched yet! Confirmation dialog state must be active instead.
    assert len(started_cmds) == 0
    assert isinstance(at.session_state[f"ai_video_confirm_single_{pid}"], dict)

    # Confirm button in dialog launches job
    conf_btns = [b for b in at.button if b.key == f"btn_dlg_conf_aiv_{pid}"]
    assert len(conf_btns) == 1
    conf_btns[0].click().run(timeout=25)
    assert len(started_cmds) == 1
    assert started_cmds[0][0] == "visual_ai_video"
    assert "ai-video" in started_cmds[0][1]


# ---------------------------------------------------------------------------
# F4: Theme hides anchor links + Shortened AI Image Checkbox with Tooltip
# ---------------------------------------------------------------------------


def test_f4_theme_hides_anchor_links_and_short_ai_image_checkbox(tmp_path: Path, monkeypatch):
    from app.services.branding.theme import get_global_css

    css = get_global_css()
    assert '[data-testid="stHeaderActionElements"]' in css
    assert "display: none !important" in css

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.app, "openai_image_api_key", "")
    monkeypatch.setitem(config.app, "codex_image_enabled", False)

    for ui_lang, expected_lbl, expected_help in [
        ("id", "Gambar AI — belum diatur", "Pengaturan sumber materi"),
        ("en", "AI Images — not configured", "Material Source Settings"),
    ]:
        monkeypatch.setitem(config.ui, "language", ui_lang)
        pid = f"f4_chk_{ui_lang}"
        create_project(pid, "F4 Test", "automotive.muscle_car", projects_root=tmp_path)
        at = AppTest.from_file(_get_long_video_app_file())
        at.query_params["project"] = pid
        at.session_state[f"active_step_{pid}"] = "visual"
        at.run(timeout=25)
        assert not at.exception
        cb = [c for c in at.checkbox if c.key == f"cb_prov_ai_{pid}"]
        assert len(cb) == 1
        assert cb[0].label == expected_lbl
        assert expected_help in (cb[0].help or "")


# ---------------------------------------------------------------------------
# F5: max_tokens Limits, 402/404 Zero-Retry + Friendly UI, Empty Model Fallback,
#     Free/Paid Badge, Pricing Source URL/Disclaimer, Host config.toml Isolation
# ---------------------------------------------------------------------------


def test_f5_max_tokens_per_call_type_never_unbounded(tmp_path: Path, monkeypatch):
    from app.services.longform.llm_adapter import (
        LLMAdapter,
        MAX_TOKENS_TEST_CONNECTION,
        MAX_TOKENS_VISION_RELEVANCE,
        compute_script_max_tokens,
        test_llm_connection,
    )
    from app.services.longform.script_generator import ScriptGenerator
    from app.services.longform.visual import ai_relevance, keyword_translator

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-test-key")
    monkeypatch.setitem(config.app, "openrouter_model_name", "openrouter/free")

    # 1. Verify ScriptGenerator compute_script_max_tokens bounds per call type
    assert compute_script_max_tokens("outline") == 800
    assert compute_script_max_tokens("outline", target_words=1000) == 1050
    assert compute_script_max_tokens("summary") == 320
    assert compute_script_max_tokens("chapter", target_words=450) == 1525
    assert compute_script_max_tokens("revise", target_words=500) == 1650

    # 2. Verify test_llm_connection passes max_tokens <= 16 via OpenAI-compatible SDK
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice

    def _mk_chat_resp(txt: str) -> ChatCompletion:
        return ChatCompletion(
            id="chatcmpl-f5-test",
            choices=[Choice(finish_reason="stop", index=0, message=ChatCompletionMessage(role="assistant", content=txt))],
            created=0,
            model="openrouter/free",
            object="chat.completion",
        )

    recorded_sdk_calls: list[dict] = []

    class _FakeCompletions:
        def create(self, **kwargs):
            recorded_sdk_calls.append(kwargs)
            return _mk_chat_resp("ok")

    class _FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    ok_conn, conn_msg, _lat = test_llm_connection()
    assert ok_conn is True
    assert recorded_sdk_calls[-1]["max_tokens"] == MAX_TOKENS_TEST_CONNECTION
    assert recorded_sdk_calls[-1]["max_tokens"] == 256
    assert recorded_sdk_calls[-1]["extra_body"] == {"reasoning": {"enabled": False}}

    # 3. Verify LLMAdapter.generate passes explicit max_tokens to SDK and records it
    adapter = LLMAdapter(provider="openrouter", model="openrouter/free")
    resp = adapter.generate("Hello", max_tokens=compute_script_max_tokens("outline", target_words=1000))
    assert resp.max_tokens_requested == 1050
    assert recorded_sdk_calls[-1]["max_tokens"] == 1050
    assert recorded_sdk_calls[-1]["max_tokens"] != 65535

    # 4. Verify ScriptGenerator passes bounded max_tokens for outline, chapter, revise_length, summary end-to-end
    sg_dir, _ = _seed_project_with_shots(tmp_path / "sg_proj", "sg_max", lang="en-US", queries=["muscle car"])
    recorded_sdk_calls.clear()
    seen_script_kinds: list[tuple[str, int | None]] = []

    class _FakeScriptCompletions:
        def create(self, **kwargs):
            recorded_sdk_calls.append(kwargs)
            prompt_txt = str(kwargs["messages"][0]["content"])
            mt = kwargs.get("max_tokens")
            if "candidate_titles" in prompt_txt or '"chapters"' in prompt_txt:
                seen_script_kinds.append(("outline", mt))
                body = json.dumps({
                    "topic": "Classic Muscle Cars",
                    "target_duration_minutes": 5.0,
                    "words_per_minute": 140,
                    "total_target_words": 200,
                    "candidate_titles": [
                        "Classic Muscle Cars: How Detroit Built Icons",
                        "Roaring V8 History of American Streets",
                        "Golden Era of Muscle Car Legends",
                    ],
                    "opening_hook": "In nineteen sixty four America discovered affordable horsepower on the open road.",
                    "chapters": [
                        {
                            "chapter_index": 1,
                            "title": "Birth of the V8 Legend",
                            "purpose": "Explain how Detroit engineers sparked the muscle car revolution.",
                            "key_points": ["Pontiac GTO", "Street performance"],
                            "target_words": 200,
                        }
                    ],
                })
            elif "narrative_summary" in prompt_txt:
                seen_script_kinds.append(("summary", mt))
                body = json.dumps({
                    "narrative_summary": "Pontiac pioneered the intermediate V8 formula in nineteen sixty four.",
                    "key_facts": ["Pontiac GTO debuted in 1964"],
                })
            elif "revise" in prompt_txt.lower() or "current_words" in prompt_txt.lower() or "condense" in prompt_txt.lower() or "expand" in prompt_txt.lower():
                seen_script_kinds.append(("revise_length", mt))
                words_200 = " ".join(["history"] * 200)
                body = f"## Birth of the V8 Legend\n\n{words_200}."
            else:
                seen_script_kinds.append(("chapter", mt))
                body = "## Birth of the V8 Legend\n\nShort English draft to trigger length revision."
            return _mk_chat_resp(body)

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_FakeScriptCompletions())),
    )
    sg = ScriptGenerator(project_dir=sg_dir, provider="openrouter", model="openrouter/free")
    sg.generate_outline(topic="Classic Muscle Cars", target_duration_minutes=5.0, force_refresh=True)
    sg.write_chapters(force_refresh=True)

    kinds_dict = dict(seen_script_kinds)
    assert set(["outline", "chapter", "revise_length", "summary"]).issubset(kinds_dict.keys())
    for k_name, mt_val in seen_script_kinds:
        assert isinstance(mt_val, int) and 0 < mt_val < 65535, f"Unbounded max_tokens for {k_name}: {mt_val}"
    assert kinds_dict["summary"] == compute_script_max_tokens("summary")
    assert kinds_dict["chapter"] == compute_script_max_tokens("chapter", 200)
    assert kinds_dict["revise_length"] == compute_script_max_tokens("revise_length", 200)

    # 5. Verify keyword_translator uses proportional max_tokens (no 65535)
    recorded_sdk_calls.clear()
    p_dir, shots_kw = _seed_project_with_shots(
        tmp_path / "kw_proj", "kw_max", lang="id-ID", queries=["mobil otot", "bengkel retro"]
    )
    m_kw = load_manifest(p_dir)

    class _FakeKwCompletions:
        def create(self, **kwargs):
            recorded_sdk_calls.append(kwargs)
            return _mk_chat_resp('{"ch01_shot001": "classic muscle car", "ch01_shot002": "retro garage"}')

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_FakeKwCompletions())),
    )
    kw_out = keyword_translator.translate_chapter_queries_to_english(
        shots=shots_kw,
        project_lang="id-ID",
        cache_dir=p_dir / "cache",
    )
    assert kw_out["translated"] == 2
    assert recorded_sdk_calls[-1]["max_tokens"] == 320

    # 6. Verify ai_relevance uses MAX_TOKENS_VISION_RELEVANCE == 256 + OpenRouter reasoning minimization
    recorded_sdk_calls.clear()
    monkeypatch.setitem(config.app, "openrouter_model_name", "google/gemini-2.5-flash")

    class _FakeVisionCompletions:
        def create(self, **kwargs):
            recorded_sdk_calls.append(kwargs)
            return _mk_chat_resp('{"relevan": "ya", "alasan": "clear match"}')

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_FakeVisionCompletions())),
    )
    rel = ai_relevance.check_shot_relevance(
        shots_kw[0],
        m_kw.chapters[0],
        p_dir,
        m_kw,
        force_refresh=True,
        cache_dir=p_dir / "cache",
    )
    assert rel["verdict"]["relevan"] == "ya"
    assert recorded_sdk_calls[-1]["max_tokens"] == MAX_TOKENS_VISION_RELEVANCE
    assert recorded_sdk_calls[-1]["max_tokens"] == 256


def test_f5_402_credit_error_zero_retries_and_friendly_ui(tmp_path: Path, monkeypatch):
    from app.services.longform.llm_adapter import LLMAdapter, LLMCreditError
    from app.services.longform.visual import ai_relevance, keyword_translator

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-test-key")
    monkeypatch.setitem(config.app, "openrouter_model_name", "google/gemini-2.5-flash")
    monkeypatch.setitem(config.ui, "language", "id")

    err_402_msg = (
        "Error code: 402 - {'error': {'message': 'This request requires more credits, or fewer max_tokens. "
        "You requested up to 65535 tokens, but can only afford 15659.', 'code': 402}}"
    )
    call_counter = {"n": 0}

    class _Raise402Completions:
        def create(self, **_kwargs):
            call_counter["n"] += 1
            raise RuntimeError(err_402_msg)

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_Raise402Completions())),
    )

    # 1. LLMAdapter.generate raises LLMCreditError with ZERO retries (call_count == 1)
    adapter = LLMAdapter(provider="openrouter", model="google/gemini-2.5-flash")
    with pytest.raises(LLMCreditError):
        adapter.generate("hello", max_tokens=1200)
    assert call_counter["n"] == 1

    # 2. keyword_translator does NOT retry on 402
    call_counter["n"] = 0
    p_dir, shots_402 = _seed_project_with_shots(tmp_path / "kw402", "kw402", lang="id-ID", queries=["mobil antik"])
    m_402 = load_manifest(p_dir)
    kw_res = keyword_translator.translate_chapter_queries_to_english(
        shots=shots_402,
        project_lang="id-ID",
        cache_dir=p_dir / "cache",
    )
    assert call_counter["n"] == 1
    assert kw_res["warning"] and "402" in kw_res["warning"]

    # 3. ai_relevance does NOT retry on 402
    call_counter["n"] = 0
    with pytest.raises(LLMCreditError):
        ai_relevance.check_shot_relevance(
            shots_402[0],
            m_402.chapters[0],
            p_dir,
            m_402,
            force_refresh=True,
            cache_dir=p_dir / "cache",
        )
    assert call_counter["n"] == 1

    # 4. AppTest: "Tes Koneksi" shows friendly Indonesian 402 message + openrouter/free hint + Settings button + Detail expander
    pid = "f5_ui_402"
    create_project(pid, "F5 402 Test", "automotive.muscle_car", projects_root=tmp_path)
    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.session_state[f"active_step_{pid}"] = "script"
    at.run(timeout=25)
    assert not at.exception

    # Verify Paid badge shown on Tulis dengan AI card for google/gemini-2.5-flash
    all_md = "\n".join(m.value for m in at.markdown)
    all_cap = "\n".join(c.value for c in at.caption)
    assert "google/gemini-2.5-flash" in (all_md + all_cap)
    assert "(Berbayar)" in (all_md + all_cap)

    test_btns = [b for b in at.button if b.key == f"btn_test_llm_{pid}"]
    assert len(test_btns) == 1
    test_btns[0].click().run(timeout=25)
    assert not at.exception
    warn_text = "\n".join(w.value for w in at.warning)
    err_text = "\n".join(e.value for e in at.error)
    combined_alerts = warn_text + "\n" + err_text
    assert "Kredit penyedia tidak cukup" in combined_alerts
    assert "openrouter/free" in combined_alerts
    assert any(b.key == f"btn_llm_set_conn_{pid}" for b in at.button)


def test_f5_empty_openrouter_model_uses_openrouter_free_and_404_unavailable_for_free(tmp_path: Path, monkeypatch):
    from app.models.llm_provider import get_llm_provider
    from app.services.longform.llm_adapter import (
        LLMAdapter,
        LLMModelNotFoundError,
        OPENROUTER_SAFE_DEFAULT_MODEL,
        get_llm_readiness,
        resolve_model_billing_tier,
    )

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-test-key")
    monkeypatch.setitem(config.app, "openrouter_model_name", "")
    monkeypatch.setitem(config.ui, "language", "en")

    # Upstream provider default remains untouched (minimax/minimax-m3:free)!
    assert get_llm_provider("openrouter").default_model == "minimax/minimax-m3:free"

    # Long Video resolves empty OpenRouter model to openrouter/free with Free billing tier
    readiness = get_llm_readiness()
    assert readiness["configured"] is True
    assert readiness["model"] == OPENROUTER_SAFE_DEFAULT_MODEL
    assert readiness["used_safe_default"] is True
    assert readiness["billing_tier"] == "free"
    assert resolve_model_billing_tier("openrouter", "openrouter/free") == "free"
    assert resolve_model_billing_tier("openrouter", "google/gemini-2.5-flash") == "paid"

    # Verify AppTest shows openrouter/free (Free) on both Script card & Visual relevance toggle
    pid = "f5_empty_or"
    create_project(pid, "F5 Empty OR", "automotive.muscle_car", projects_root=tmp_path)
    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.session_state[f"active_step_{pid}"] = "script"
    at.run(timeout=25)
    assert not at.exception
    txt_script = "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    assert "openrouter/free (Free)" in txt_script

    at.session_state[f"active_step_{pid}"] = "visual"
    at.run(timeout=25)
    assert not at.exception
    txt_visual = "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption])
    assert "openrouter/free (Free)" in txt_visual

    # Verify HTTP 404 "unavailable for free" raises LLMModelNotFoundError with ZERO retries
    calls = {"n": 0}

    class _Raise404Completions:
        def create(self, **_kwargs):
            calls["n"] += 1
            raise RuntimeError(
                "Error code: 404 - {'error': {'message': 'This model is unavailable for free. "
                "The paid version is available now - use this slug instead: minimax/minimax-m3', 'code': 404}}"
            )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_Raise404Completions())),
    )
    adapter = LLMAdapter(provider="openrouter", model="minimax/minimax-m3:free")
    with pytest.raises(LLMModelNotFoundError):
        adapter.generate("hello", max_tokens=100)
    assert calls["n"] == 1


def test_f5_host_config_toml_untouched_by_tests(tmp_path: Path, monkeypatch):
    import hashlib
    from app.services.longform.llm_adapter import set_active_llm_model

    root_cfg = Path(__file__).resolve().parents[2] / "config.toml"
    before_bytes = root_cfg.read_bytes() if root_cfg.is_file() else b""
    before_hash = hashlib.sha256(before_bytes).hexdigest()

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    # Attempt mutations that internally call config.try_save_config() / save_ui_language()
    set_active_llm_model("openrouter", "google/gemini-2.5-flash")
    pid = "f5_iso"
    create_project(pid, "Isolation Check", "automotive.muscle_car", projects_root=tmp_path)
    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.run(timeout=25)
    assert not at.exception

    after_bytes = root_cfg.read_bytes() if root_cfg.is_file() else b""
    after_hash = hashlib.sha256(after_bytes).hexdigest()
    assert after_hash == before_hash


# ---------------------------------------------------------------------------
# F6: Reasoning-safe max_tokens, length/reasoning 1x retry, HTTP OK empty warning
# ---------------------------------------------------------------------------


def _mk_chat_completion_with_meta(
    content: str | None,
    *,
    finish_reason: str = "stop",
    reasoning_tokens: int = 0,
    reasoning_text: str | None = None,
):
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice
    from openai.types.completion_usage import CompletionTokensDetails, CompletionUsage

    msg_kwargs = {"role": "assistant", "content": content}
    msg = ChatCompletionMessage(**msg_kwargs)
    if reasoning_text is not None:
        object.__setattr__(msg, "reasoning", reasoning_text)
    usage = CompletionUsage(
        completion_tokens=max(reasoning_tokens, 1),
        prompt_tokens=20,
        total_tokens=20 + max(reasoning_tokens, 1),
        completion_tokens_details=CompletionTokensDetails(reasoning_tokens=reasoning_tokens),
    )
    return ChatCompletion(
        id="chatcmpl-f6-test",
        choices=[Choice(finish_reason=finish_reason, index=0, message=msg)],
        created=0,
        model="openrouter/free",
        object="chat.completion",
        usage=usage,
    )


def test_f6_connection_test_retries_length_reasoning_with_larger_max_tokens(monkeypatch):
    from app.services.longform.llm_adapter import MAX_TOKENS_TEST_CONNECTION, test_llm_connection

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-test-key")
    monkeypatch.setitem(config.app, "openrouter_model_name", "openrouter/free")

    calls: list[dict] = []

    class _ReasoningRetryCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                # 1st attempt: HTTP 200, finish_reason="length", all tokens consumed in reasoning -> content=None
                return _mk_chat_completion_with_meta(
                    None,
                    finish_reason="length",
                    reasoning_tokens=MAX_TOKENS_TEST_CONNECTION,
                    reasoning_text="Step 1: user wants OK...",
                )
            # 2nd attempt (escalated max_tokens=512): returns "OK"
            return _mk_chat_completion_with_meta("OK", finish_reason="stop", reasoning_tokens=40)

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_ReasoningRetryCompletions())),
    )

    ok_conn, msg, _lat = test_llm_connection()
    assert ok_conn is True
    assert msg == "OK"
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 256
    assert calls[1]["max_tokens"] == 512
    assert calls[0]["extra_body"] == {"reasoning": {"enabled": False}}
    assert calls[0]["messages"][0]["content"] == "Reply with exactly: OK"


@pytest.mark.parametrize("ui_lang,expected_warn", [
    ("id", "Terhubung, tetapi model tidak mengembalikan teks — coba lagi atau pilih model lain."),
    ("en", "Connected, but the model returned no text — try again or choose another model."),
])
def test_f6_permanent_empty_http_ok_shows_connected_empty_warning_not_failed(
    tmp_path: Path, monkeypatch, ui_lang: str, expected_warn: str
):
    from app.services.longform.llm_adapter import test_llm_connection

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-test-key")
    monkeypatch.setitem(config.app, "openrouter_model_name", "openrouter/free")
    monkeypatch.setitem(config.ui, "language", ui_lang)

    calls: list[dict] = []

    class _AlwaysEmptyReasoningCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _mk_chat_completion_with_meta(
                None,
                finish_reason="length",
                reasoning_tokens=kwargs.get("max_tokens", 256),
                reasoning_text="Long internal monologue...",
            )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_AlwaysEmptyReasoningCompletions())),
    )

    # Backend test confirms 1x retry (256 -> 512) then HTTP-OK empty tag
    ok_conn, raw_msg, _lat = test_llm_connection()
    assert ok_conn is False
    assert "[EMPTY_AFTER_HTTP_OK]" in raw_msg
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 256
    assert calls[1]["max_tokens"] == 512

    # AppTest UI verifies warning banner ("Terhubung, tetapi..." / "Connected, but..."), NOT "Tes koneksi gagal" error!
    pid = f"f6_empty_{ui_lang}"
    create_project(pid, "F6 Empty Test", "automotive.muscle_car", projects_root=tmp_path)
    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.session_state[f"active_step_{pid}"] = "script"
    at.run(timeout=25)
    assert not at.exception

    btn = [b for b in at.button if b.key == f"btn_test_llm_{pid}"]
    assert len(btn) == 1
    btn[0].click().run(timeout=25)
    assert not at.exception

    warn_text = "\n".join(w.value for w in at.warning)
    err_text = "\n".join(e.value for e in at.error)
    assert expected_warn in warn_text
    assert "Tes koneksi gagal" not in err_text
    assert "Connection test failed" not in err_text
    assert any(b.key == f"btn_llm_set_conn_{pid}" for b in at.button)


def test_f6_ai_relevance_and_keyword_translator_escalate_on_length_reasoning(tmp_path: Path, monkeypatch):
    from app.services.longform.visual import ai_relevance, keyword_translator

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-test-key")
    monkeypatch.setitem(config.app, "openrouter_model_name", "google/gemini-2.5-flash")

    p_dir, shots = _seed_project_with_shots(tmp_path / "f6_vis", "f6_vis", lang="id-ID", queries=["mobil sport"])
    m = load_manifest(p_dir)

    # 1. Vision relevance: empty+length at 256 -> retries 1x at 512 and succeeds
    vis_calls: list[dict] = []

    class _VisEscalationCompletions:
        def create(self, **kwargs):
            vis_calls.append(kwargs)
            if len(vis_calls) == 1:
                return _mk_chat_completion_with_meta(
                    "<think>analyzing car frame...</think>",
                    finish_reason="length",
                    reasoning_tokens=256,
                )
            return _mk_chat_completion_with_meta('{"relevan": "ya", "alasan": "sports car"}', finish_reason="stop")

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_VisEscalationCompletions())),
    )
    rel = ai_relevance.check_shot_relevance(
        shots[0], m.chapters[0], p_dir, m, force_refresh=True, cache_dir=p_dir / "cache"
    )
    assert rel["verdict"]["relevan"] == "ya"
    assert [c["max_tokens"] for c in vis_calls] == [256, 512]
    assert vis_calls[0]["extra_body"] == {"reasoning": {"enabled": False}}

    # 2. Keyword translation: empty+length at 320 -> retries 1x at 640 and succeeds
    kw_calls: list[dict] = []

    class _KwEscalationCompletions:
        def create(self, **kwargs):
            kw_calls.append(kwargs)
            if len(kw_calls) == 1:
                return _mk_chat_completion_with_meta(
                    None,
                    finish_reason="length",
                    reasoning_tokens=320,
                    reasoning_text="translating...",
                )
            return _mk_chat_completion_with_meta('{"ch01_shot001": "sports car"}', finish_reason="stop")

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_KwEscalationCompletions())),
    )
    kw_out = keyword_translator.translate_chapter_queries_to_english(
        shots=shots, project_lang="id-ID", cache_dir=p_dir / "cache", force=True
    )
    assert kw_out["translated"] == 1
    assert shots[0].search_query_en == "sports car"
    assert [c["max_tokens"] for c in kw_calls] == [320, 640]


# ---------------------------------------------------------------------------
# F8: AI Visual Keywords per Chapter (3-6 English words, Distinct Split Shots,
#     Origin Tagging, Refresh Stale Only Unlocked, Fallback without LLM)
# ---------------------------------------------------------------------------


def _seed_street_food_same_sentence_project(tmp_path: Path, pid: str = "f8_sf"):
    from app.services.longform.schema import AudioSpec, Segment, TtsGroup, VisualSpec

    _, p_dir = create_project(
        project_id=pid,
        title="Kuliner Jalanan Malam Indonesia",
        preset_id="umum.id",
        language="id-ID",
        projects_root=tmp_path,
    )
    m = load_manifest(p_dir)
    same_sentence = (
        "Di tengah bisingnya klakson kota dan cahaya lampu neon yang mulai menyala, "
        "aroma panggangan arang dan tumisan bawang putih di pinggir jalan seolah jadi magnet yang mustahil ditolak."
    )
    seg1 = Segment(
        segment_index=1,
        narration_text="Suasana malam mulai terasa hidup di sudut pasar lama.",
        visual=VisualSpec(search_keywords="night market crowd", prompt="night market"),
    )
    seg2 = Segment(segment_index=2, narration_text=same_sentence)
    seg3 = Segment(segment_index=3, narration_text="Asap sate ayam mengepul di atas bara api merah menyala.")
    seg4 = Segment(segment_index=4, narration_text="Pembeli antre di depan gerobak nasi goreng pinggir jalan.")

    ch = Chapter(
        chapter_index=1,
        chapter_title="Denyut Kuliner Malam",
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text=" ".join(s.narration_text for s in (seg1, seg2, seg3, seg4)),
                audio=AudioSpec(file_path="assets/audio/groups/ch1_g1.wav", duration_seconds=20.0),
                segments=[seg1, seg2, seg3, seg4],
            )
        ],
        shots=[
            Shot(
                shot_id="ch01_shot001",
                chapter_index=1,
                shot_index=1,
                segment_indices=[1],
                duration_seconds=4.0,
                target_frames=100,
                search_query="night market crowd",
                search_query_source="tag",
                status=ItemStatus.completed,
            ),
            Shot(
                shot_id="ch01_shot002",
                chapter_index=1,
                shot_index=2,
                segment_indices=[2],
                duration_seconds=4.5,
                target_frames=112,
                search_query="tengah bisingnya klakson kota",
                search_query_source="extraction",
                status=ItemStatus.completed,
            ),
            Shot(
                shot_id="ch01_shot003",
                chapter_index=1,
                shot_index=3,
                segment_indices=[2],  # Same narration sentence as Shot #2!
                duration_seconds=4.5,
                target_frames=113,
                search_query="tengah bisingnya klakson kota",
                search_query_source="extraction",
                status=ItemStatus.completed,
            ),
            Shot(
                shot_id="ch01_shot004",
                chapter_index=1,
                shot_index=4,
                segment_indices=[3],
                duration_seconds=3.5,
                target_frames=87,
                search_query="satay grill smoke",
                search_query_en="satay grill smoke",
                search_query_source="extraction",
                locked=True,  # Locked shot MUST NOT change!
                status=ItemStatus.completed,
            ),
            Shot(
                shot_id="ch01_shot005",
                chapter_index=1,
                shot_index=5,
                segment_indices=[4],
                duration_seconds=3.5,
                target_frames=88,
                search_query="pembeli antre gerobak",
                search_query_source="extraction",
                status=ItemStatus.completed,
            ),
        ],
    )
    for idx, sh in enumerate(ch.shots, start=1):
        rel = f"assets/visual/shots/{sh.shot_id}_asset.jpg"
        _make_sample_jpg(p_dir / rel, color=(40 * idx, 80, 140))
        ahash = compute_file_content_hash(p_dir / rel)
        sh.asset_path = rel
        sh.asset_content_hash = ahash
        sh.requested_source = "pixabay"
        sh.actual_source = "pixabay"
        sh.rendered_clip = f"assets/visual/shots/{sh.shot_id}.mp4"
        sh.shot_hash = compute_shot_hash(
            shot_id=sh.shot_id,
            duration_seconds=sh.duration_seconds,
            target_frames=sh.target_frames,
            motion="zoom_in",
            asset_content_hash=ahash,
            fps=25,
            requested_provider="pixabay",
            actual_provider="pixabay",
        )
    m.chapters = [ch]
    save_manifest(m, p_dir)
    return p_dir, m


def test_f8_ai_keywords_different_for_same_sentence_preserves_tag_and_locked(tmp_path: Path, monkeypatch):
    from app.services.longform.visual.ai_keywords import generate_chapter_visual_keywords_ai

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-test-key")
    monkeypatch.setitem(config.app, "openrouter_model_name", "openrouter/free")

    p_dir, m = _seed_street_food_same_sentence_project(tmp_path / "p1", "f8_same")
    ch = m.chapters[0]

    # Mock LLM: even if LLM tries returning identical query for shot 2 and shot 3, guard still differentiates,
    # and verify normal distinct JSON batch works cleanly too.
    llm_calls = 0

    class _MockAdapter:
        def generate(self, prompt: str, **_kw):
            nonlocal llm_calls
            llm_calls += 1
            return LLMResponse(
                content=json.dumps({
                    "ch01_shot002": "night city traffic neon lights",
                    "ch01_shot003": "street food charcoal grill garlic",
                    "ch01_shot005": "fried rice street cart night",
                }),
                provider="openrouter",
                model="openrouter/free",
                duration_seconds=0.1,
            )

    res = generate_chapter_visual_keywords_ai(
        ch.shots,
        ch,
        preset_snapshot=m.preset_snapshot,
        project_lang="id-ID",
        cache_dir=tmp_path / "cache_f8",
        llm_adapter=_MockAdapter(),
        force=True,
    )
    assert res["llm_calls"] == 1
    assert res["skipped_locked"] == 1
    assert res["tag_preserved"] == 1
    assert ch.shots[0].search_query_source == "tag"
    assert ch.shots[0].search_query == "night market crowd"
    assert ch.shots[1].search_query_en == "night city traffic neon lights"
    assert ch.shots[2].search_query_en == "street food charcoal grill garlic"
    assert ch.shots[1].search_query_en != ch.shots[2].search_query_en
    assert ch.shots[1].search_query_source == "ai"
    assert ch.shots[2].search_query_source == "ai"
    assert ch.shots[3].search_query_en == "satay grill smoke"
    assert ch.shots[3].search_query_source == "extraction"
    assert 3 <= len(ch.shots[1].search_query_en.split()) <= 6
    assert 3 <= len(ch.shots[2].search_query_en.split()) <= 6

    # Also test collision-guard when LLM returns identical query for both split shots of same sentence
    class _DupAdapter:
        def generate(self, prompt: str, **_kw):
            return LLMResponse(
                content=json.dumps({
                    "ch01_shot002": "night city neon street",
                    "ch01_shot003": "night city neon street",
                    "ch01_shot005": "street food cart night",
                }),
                provider="openrouter",
                model="openrouter/free",
                duration_seconds=0.1,
            )

    generate_chapter_visual_keywords_ai(
        ch.shots,
        ch,
        preset_snapshot=m.preset_snapshot,
        project_lang="id-ID",
        cache_dir=tmp_path / "cache_dup",
        llm_adapter=_DupAdapter(),
        force=True,
    )
    assert ch.shots[1].search_query_en != ch.shots[2].search_query_en


def test_f8_fallback_without_llm_and_mark_stale_refetches_only_unlocked(tmp_path: Path, monkeypatch):
    from app.services.longform.status import compute_project_step_status

    p_dir, m = _seed_street_food_same_sentence_project(tmp_path / "p2", "f8_fb")
    locked_hash_before = m.chapters[0].shots[3].shot_hash

    # 1. Fallback when LLM not configured -> extractor used with mild warning
    monkeypatch.setattr(
        "app.services.longform.visual.ai_keywords.get_llm_readiness",
        lambda *_a, **_kw: {"configured": False, "is_invalid": True},
    )
    svc = VisualService(p_dir)
    _, fb_stats = svc.update_keywords_with_ai(force=True, mark_stale=True)
    assert fb_stats["fallback_used"] is True
    assert fb_stats["warnings"]
    assert fb_stats["llm_calls"] == 0

    # 2. Now run with mocked LLM -> updates unlocked shots, marks stale ('perlu_diperbarui'), leaves locked untouched!
    monkeypatch.setattr(
        "app.services.longform.visual.ai_keywords.get_llm_readiness",
        lambda *_a, **_kw: {"configured": True, "is_invalid": False},
    )

    class _UpdateAdapter:
        def generate(self, prompt: str, **_kw):
            return LLMResponse(
                content=json.dumps({
                    "ch01_shot002": "neon city traffic night",
                    "ch01_shot003": "charcoal street food grill",
                    "ch01_shot005": "indonesian fried rice stall",
                }),
                provider="openrouter",
                model="openrouter/free",
                duration_seconds=0.1,
            )

    m_upd, upd_stats = svc.update_keywords_with_ai(force=True, mark_stale=True, llm_adapter=_UpdateAdapter())
    assert upd_stats["stale_marked"] >= 3
    assert m_upd.chapters[0].shots[1].status == ItemStatus.stale
    assert m_upd.chapters[0].shots[2].status == ItemStatus.stale
    assert m_upd.chapters[0].shots[3].locked is True
    assert m_upd.chapters[0].shots[3].shot_hash == locked_hash_before
    assert m_upd.chapters[0].shots[3].status == ItemStatus.completed

    st_info = compute_project_step_status(p_dir)
    assert st_info["steps"]["visual"]["status"] == "perlu_diperbarui"

    # 3. fetch_all(provider='fake') re-fetches ONLY stale unlocked shots and skips locked shot!
    svc.fetch_all(m_upd, provider_name="fake", allow_fallback=True, translate_keywords=False)
    m_after = load_manifest(p_dir)
    assert m_after.chapters[0].shots[3].shot_hash == locked_hash_before
    assert m_after.chapters[0].shots[3].actual_source == "pixabay"  # Locked shot untouched!
    assert m_after.chapters[0].shots[1].status == ItemStatus.pending  # Stale shot was re-fetched!


@pytest.mark.parametrize("ui_lang", ["id", "en"])
def test_f8_ui_shows_en_keyword_source_origin_and_update_button(tmp_path: Path, monkeypatch, ui_lang: str):
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", ui_lang)

    p_dir, m = _seed_street_food_same_sentence_project(tmp_path, f"f8_ui_{ui_lang}")
    m.chapters[0].shots[0].search_query_en = "night market street food"
    m.chapters[0].shots[0].search_query_source = "ai"
    save_manifest(m, p_dir)

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = f"f8_ui_{ui_lang}"
    at.session_state[f"active_step_f8_ui_{ui_lang}"] = "visual"
    at.run(timeout=25)
    assert not at.exception

    md_all = "\n".join(x.value for x in at.markdown)
    assert "night market street food" in md_all
    if ui_lang == "id":
        assert "Kata kunci pencarian (EN):" in md_all
        assert "Asal:" in md_all and "AI" in md_all
    else:
        assert "Search keyword (EN):" in md_all
        assert "Origin:" in md_all and "AI" in md_all
    assert any(b.key == f"btn_run_ai_kw_f8_ui_{ui_lang}" for b in at.button)


# ---------------------------------------------------------------------------
# F9: AI Video Provider Selection Dropdown + Supported Providers Fallback
# ---------------------------------------------------------------------------


def test_f9_ai_video_provider_dropdown_and_unsupported_list(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setattr("app.services.longform.config.get_llm_failover_chain", lambda **_kw: [])
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", "id")

    pid = "f9_vid_prov"
    _seed_project_with_shots(tmp_path, pid, needs_review=True)

    # 1. Zero providers configured -> lists all 8 MPT-supported providers + button Pengaturan Video AI
    for key_clear in (
        "wavespeed_api_keys", "ofox_api_key", "volcengine_seedance_api_key",
        "metaso_minimax_api_key", "infistar_api_key", "jalapeno_api_key",
        "muapi_api_key", "loomloom_api_key",
    ):
        monkeypatch.setitem(config.app, key_clear, "")

    at0 = AppTest.from_file(_get_long_video_app_file())
    at0.query_params["project"] = pid
    at0.session_state[f"active_step_{pid}"] = "visual"
    at0.run(timeout=25)
    assert not at0.exception
    md0 = "\n".join(x.value for x in at0.markdown)
    assert "WaveSpeed" in md0 and "OFox AI Video" in md0 and "Volcano Engine Seedance" in md0
    cfg_btns = [b for b in at0.button if b.key == f"btn_cfg_aiv_{pid}_ch01_shot001"]
    assert len(cfg_btns) == 1
    cfg_btns[0].click().run(timeout=25)
    assert at0.session_state.get("settings_material_group") == "ai_video"

    # 2. Multiple configured providers -> WaveSpeed first, OFox & Volcano selectable, switching updates models!
    monkeypatch.setitem(config.app, "ofox_api_key", "ofox-test-key")
    monkeypatch.setitem(config.app, "wavespeed_api_keys", ["ws-test-key"])
    monkeypatch.setitem(config.app, "volcengine_seedance_api_key", "volc-test-key")

    at1 = AppTest.from_file(_get_long_video_app_file())
    at1.query_params["project"] = pid
    at1.session_state[f"active_step_{pid}"] = "visual"
    at1.run(timeout=25)
    assert not at1.exception

    prov_sb = [s for s in at1.selectbox if s.key == f"sel_aiv_prov_{pid}_ch01_shot001"]
    assert len(prov_sb) == 1
    assert prov_sb[0].options[0] == "WaveSpeed"
    assert "OFox AI Video" in prov_sb[0].options
    assert "Volcano Engine Seedance" in prov_sb[0].options

    # Switch to OFox -> model dropdown updates to OFox models!
    prov_sb[0].select("OFox AI Video").run(timeout=25)
    ofox_model_sb = [s for s in at1.selectbox if s.key == f"sel_aiv_model_{pid}_ch01_shot001_ofox"]
    assert len(ofox_model_sb) == 1
    assert "bytedance/seedance-2.0-fast" in ofox_model_sb[0].options


# ---------------------------------------------------------------------------
# F7: Safe Project Delete (_trash/), Restore, Permanent Delete, Empty Trash, CLI & UI
# ---------------------------------------------------------------------------


def test_f7_soft_delete_restore_perm_delete_capcut_and_niche_guard(tmp_path: Path, monkeypatch):
    from app.services.longform.presets import (
        create_custom_niche_hierarchy,
        delete_custom_preset,
        get_preset_directories,
    )
    from app.services.longform.store import ProjectLock, ProjectLockedError
    from app.services.longform.trash import (
        empty_trash,
        list_trashed_projects,
        permanently_delete_trashed_project,
        restore_trashed_project,
        soft_delete_project,
    )
    from app.services.longform.ui.components import get_project_summaries

    real_presets_res, _ = get_preset_directories()
    projects_root = tmp_path / "projects"
    presets_sto = tmp_path / "sto_presets"
    capcut_root = tmp_path / "capcut_drafts"
    global_cache = tmp_path / "global_cache"
    for d in (projects_root, presets_sto, capcut_root, global_cache):
        d.mkdir(parents=True, exist_ok=True)
    # Seed a file in global cache to confirm cache is untouched
    cache_probe = global_cache / "pixabay_keep.mp4"
    cache_probe.write_bytes(b"keep-me")

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: projects_root)
    monkeypatch.setattr("app.services.longform.trash.get_projects_root", lambda: projects_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: projects_root)
    monkeypatch.setattr("app.services.longform.presets.get_preset_directories", lambda: (real_presets_res, presets_sto))
    monkeypatch.setattr("app.services.longform.trash.detect_draft_folder", lambda: capcut_root)

    # Create custom preset & 2 projects + CapCut drafts
    preset_id, _ = create_custom_niche_hierarchy(niche_name="F7 Test Cuisine", language="id-ID")
    _, p1_dir = create_project("f7_p1", "Kuliner Nusantara", preset_id, projects_root=projects_root)
    _, p2_dir = create_project("f7_p2", "Kuliner Kedua", preset_id, projects_root=projects_root)
    d1 = capcut_root / "EFS_f7_p1_20261117"
    d1.mkdir(parents=True)
    (d1 / "draft_content.json").write_text("{}", encoding="utf-8")
    d2 = capcut_root / "MPT_f7_p2_20261117"
    d2.mkdir(parents=True)

    # 1. Reject deletion while ProjectLock active!
    with ProjectLock(p1_dir):
        with pytest.raises(ProjectLockedError):
            soft_delete_project("f7_p1")

    # 2. Reject deletion while running job active!
    monkeypatch.setattr(
        "app.services.longform.ui.jobs.get_job_state",
        lambda pid: {"status": "running", "step": "render", "pid": os.getpid()} if pid == "f7_p1" else None,
    )
    with pytest.raises(ProjectLockedError):
        soft_delete_project("f7_p1")
    monkeypatch.setattr("app.services.longform.ui.jobs.get_job_state", lambda _pid: None)

    # 3. Soft delete WITHOUT CapCut -> moved to _trash, hidden from summaries, EFS_f7_p1_* preserved!
    del_res = soft_delete_project("f7_p1", delete_capcut_drafts=False)
    assert not p1_dir.exists()
    assert Path(del_res["trash_path"]).is_dir()
    assert d1.is_dir()  # Preserved!
    assert cache_probe.is_file()
    assert [p["project_id"] for p in get_project_summaries()] == ["f7_p2"]
    assert len(list_trashed_projects()) == 1

    # Restore brings p1 back cleanly
    rest = restore_trashed_project("f7_p1")
    assert Path(rest["restored_path"]).is_dir()
    assert set(p["project_id"] for p in get_project_summaries()) == {"f7_p1", "f7_p2"}
    assert len(list_trashed_projects()) == 0

    # 4. Soft delete BOTH projects -> trashed projects DO NOT block deleting custom niche!
    soft_delete_project("f7_p1", delete_capcut_drafts=True)
    assert not d1.exists()  # Checked: deleted ONLY p1's CapCut draft!
    assert d2.is_dir()      # p2's draft untouched!
    soft_delete_project("f7_p2", delete_capcut_drafts=False)
    delete_custom_preset(preset_id)  # Must succeed even though p1 & p2 in _trash reference it!

    # 5. Permanent delete single vs empty_trash
    permanently_delete_trashed_project("f7_p1")
    assert [t["project_id"] for t in list_trashed_projects()] == ["f7_p2"]
    empty_res = empty_trash()
    assert empty_res["deleted_count"] == 1
    assert list_trashed_projects() == []


def test_f7_cli_project_delete_restore_and_trash(tmp_path: Path, monkeypatch):
    import sys
    import cli_longform

    projects_root = tmp_path / "cli_proj"
    projects_root.mkdir(parents=True)
    capcut_root = tmp_path / "cli_cc"
    capcut_root.mkdir(parents=True)
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: projects_root)
    monkeypatch.setattr("app.services.longform.trash.get_projects_root", lambda: projects_root)
    monkeypatch.setattr("cli_longform.get_project_dir", lambda pid: projects_root / pid)
    monkeypatch.setattr("app.services.longform.trash.detect_draft_folder", lambda: capcut_root)

    create_project("cli_del_1", "CLI Delete Demo", "automotive.muscle_car", projects_root=projects_root)
    draft_dir = capcut_root / "EFS_cli_del_1_demo"
    draft_dir.mkdir(parents=True)

    monkeypatch.setattr(sys, "argv", ["cli_longform.py", "project", "delete", "cli_del_1"])
    assert cli_longform.main() == 0
    assert draft_dir.is_dir()  # Default without --with-capcut preserves draft!

    monkeypatch.setattr(sys, "argv", ["cli_longform.py", "trash", "list"])
    assert cli_longform.main() == 0

    monkeypatch.setattr(sys, "argv", ["cli_longform.py", "project", "restore", "cli_del_1"])
    assert cli_longform.main() == 0
    assert (projects_root / "cli_del_1" / "manifest.json").is_file()

    monkeypatch.setattr(sys, "argv", ["cli_longform.py", "project", "delete", "cli_del_1", "--with-capcut"])
    assert cli_longform.main() == 0
    assert not draft_dir.exists()

    monkeypatch.setattr(sys, "argv", ["cli_longform.py", "trash", "empty"])
    assert cli_longform.main() == 0


def test_f7_ui_delete_dialog_and_home_trash_filter(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.trash.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", "id")

    capcut_root = tmp_path / "cc_ui"
    capcut_root.mkdir(parents=True)
    monkeypatch.setattr("app.services.longform.trash.detect_draft_folder", lambda: capcut_root)
    (capcut_root / "EFS_f7_ui_demo_1").mkdir()

    proj_root = tmp_path / "projects_ui"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.trash.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    create_project("f7_ui_demo", "Proyek Hapus UI", "automotive.muscle_car", projects_root=proj_root)

    # 1. Open Home -> click "Hapus proyek" on card popover -> modal opens
    at = AppTest.from_file(_get_long_video_app_file())
    at.run(timeout=25)
    assert not at.exception
    del_btn = [b for b in at.button if b.key == "btn_del_card_f7_ui_demo"]
    assert len(del_btn) == 1
    del_btn[0].click().run(timeout=25)
    assert not at.exception

    # Confirm checkbox default OFF; form submit validates mismatch safely without disabling button (F10)
    chk_cc = [c for c in at.checkbox if c.key == "chk_del_capcut_f7_ui_demo"]
    assert len(chk_cc) == 1 and chk_cc[0].value is False
    conf_btns = [b for b in at.button if "form_del_proj_f7_ui_demo" in (b.key or "")]
    assert len(conf_btns) == 1
    assert conf_btns[0].disabled is False

    # Empty confirm click -> shows validation warning/error and keeps project intact
    conf_btns[0].click().run(timeout=25)
    assert not at.exception
    assert (proj_root / "f7_ui_demo").exists()
    assert any("f7_ui_demo" in str(e.value) for e in at.error)

    # Typing exact ID + single click -> moves immediately to Trash
    txt_in = [ti for ti in at.text_input if ti.key == "txt_del_confirm_f7_ui_demo"][0]
    txt_in.input("f7_ui_demo")
    conf_btn = [b for b in at.button if "form_del_proj_f7_ui_demo" in (b.key or "")][0]
    conf_btn.click().run(timeout=25)
    assert not at.exception
    assert not (proj_root / "f7_ui_demo").exists()
    assert (capcut_root / "EFS_f7_ui_demo_1").is_dir()

    # 2. Jump to Trash filter pill -> restore button visible -> click Restore -> back in Home!
    at.session_state["home_open_trash_requested"] = True
    at.run(timeout=25)
    assert not at.exception
    rest_btns = [b for b in at.button if (b.key or "").startswith("btn_restore_f7_ui_demo_")]
    assert len(rest_btns) == 1
    rest_btns[0].click().run(timeout=25)
    assert not at.exception
    assert (proj_root / "f7_ui_demo" / "manifest.json").is_file()


# ---------------------------------------------------------------------------
# ADDENDUM F10 — Reliable Delete, Stop Running Job, & Locked File Recovery
# ---------------------------------------------------------------------------


def test_f10_stop_running_job_move_restore_and_locked_file_retry(tmp_path: Path, monkeypatch):
    import cli_longform
    from app.services.longform.trash import (
        ProjectTrashError,
        get_project_delete_preview,
        restore_trashed_project,
        soft_delete_project,
    )

    proj_root = tmp_path / "projects_f10"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.trash.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.jobs.get_project_dir", lambda pid: proj_root / pid)
    monkeypatch.setitem(config.ui, "language", "id")

    # 1. Completed project deletes cleanly
    _, p_done = create_project("f10_done", "Proyek Selesai F10", "automotive.muscle_car", projects_root=proj_root)
    (p_done / "exports" / "final.mp4").parent.mkdir(parents=True, exist_ok=True)
    (p_done / "exports" / "final.mp4").write_bytes(b"\x00" * 2048)
    del_done = soft_delete_project("f10_done", stop_running_job=True)
    assert not p_done.exists()
    assert Path(del_done["trash_path"]).is_dir()

    # 2. Running dummy subprocess project: UI warns (non-blocking), stops job, moves to trash, restore marks stopped
    _, p_run = create_project("f10_run", "Proyek Berjalan F10", "automotive.muscle_car", projects_root=proj_root)
    import sys

    dummy_proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (p_run / ".lock").write_text(f"pid={dummy_proc.pid}\nhost=test", encoding="utf-8")
        (p_run / "job_state.json").write_text(
            json.dumps(
                {
                    "project_id": "f10_run",
                    "step": "audio_synth",
                    "status": "running",
                    "pid": dummy_proc.pid,
                    "started_at": "2026-11-17T10:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        preview = get_project_delete_preview("f10_run")
        assert preview["is_locked_or_running"] is True
        assert preview["running_step"] == "audio_synth"

        # UI delete dialog shows warning AND keeps confirm button enabled
        at = AppTest.from_file(_get_long_video_app_file())
        at.session_state["confirm_delete_project_id"] = "f10_run"
        at.run(timeout=25)
        assert not at.exception
        assert any("audio_synth" in str(w.value) for w in at.warning)
        conf_btns = [b for b in at.button if "form_del_proj_f10_run" in (b.key or "")]
        assert len(conf_btns) == 1 and conf_btns[0].disabled is False

        progress_steps = []
        del_run = soft_delete_project(
            "f10_run",
            stop_running_job=True,
            max_wait_seconds=10.0,
            progress_cb=lambda frac, msg: progress_steps.append((frac, msg)),
        )
        assert del_run["job_stopped"] is True
        assert len(progress_steps) >= 2
        assert dummy_proc.poll() is not None
        assert not p_run.exists()
        trashed_dir = Path(del_run["trash_path"])
        assert (trashed_dir / "manifest.json").is_file()
        trashed_js = json.loads((trashed_dir / "job_state.json").read_text(encoding="utf-8"))
        assert trashed_js["status"] == "stopped"

        # Restore -> active job_state has "stopped" ("terhenti") and no .lock remains
        res_rest = restore_trashed_project("f10_run")
        restored_dir = Path(res_rest["restored_path"])
        assert (restored_dir / "manifest.json").is_file()
        assert not (restored_dir / ".lock").exists()
        restored_js = json.loads((restored_dir / "job_state.json").read_text(encoding="utf-8"))
        assert restored_js["status"] == "stopped"
    finally:
        if dummy_proc.poll() is None:
            dummy_proc.kill()
            dummy_proc.wait(timeout=5)

    # 3. Simulated locked file: transient PermissionError succeeds on retry; persistent lock raises ProjectTrashError + rollback
    _, p_lock = create_project("f10_lock", "Proyek Terkunci F10", "automotive.muscle_car", projects_root=proj_root)
    locked_sample = p_lock / "visuals" / "busy.mp4"
    locked_sample.parent.mkdir(parents=True, exist_ok=True)
    locked_sample.write_bytes(b"video-bytes")

    real_move = shutil.move
    attempts = {"n": 0}

    def _flaky_move(src, dst, *args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise PermissionError("WinError 32 transient lock")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr("app.services.longform.trash.shutil.move", _flaky_move)
    res_retry = soft_delete_project("f10_lock")
    assert attempts["n"] == 3
    assert Path(res_retry["trash_path"]).is_dir()

    restore_trashed_project("f10_lock")
    assert (p_lock / "visuals" / "busy.mp4").is_file()

    def _always_locked_move(src, dst, *args, **kwargs):
        raise PermissionError(f"WinError 32 locked: {locked_sample}")

    monkeypatch.setattr("app.services.longform.trash.shutil.move", _always_locked_move)
    real_unlink = Path.unlink

    def _fail_unlink_busy(self, *args, **kwargs):
        if self.name == "busy.mp4":
            raise PermissionError(f"WinError 32 cannot unlink {self}")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_unlink_busy)
    with pytest.raises(ProjectTrashError) as exc_info:
        soft_delete_project("f10_lock")
    assert any(p.replace("\\", "/") == "visuals/busy.mp4" for p in exc_info.value.locked_files)
    # Rolled back partial trash dir and kept original intact
    assert (p_lock / "manifest.json").is_file()
    trash_entries = [x for x in (proj_root / "_trash").iterdir() if x.name.startswith("f10_lock_")]
    assert trash_entries == []


# ---------------------------------------------------------------------------
# ADDENDUM F11 — Chapter Heading Guard, Repair Utility, Honest Keyword UI
# ---------------------------------------------------------------------------


def test_f11_heading_parser_and_fix_headings(tmp_path: Path, monkeypatch):
    import sys
    import cli_longform
    from app.services.longform.presets import get_preset
    from app.services.longform.script_generator import ScriptGenerator
    from app.services.longform.script_import import (
        detect_leaked_chapter_headings,
        fix_project_headings,
        parse_markdown_script,
    )
    from app.services.longform.status import compute_project_step_status

    md_tricky = """#Judul Video Rahasia
#Bab 1: Panggilan Lapar dan Legenda Kaki Lima
Malam selalu membuka aroma arang dan rempah jalanan.

##Bab 2: Harmoni Rempah Tanpa Batas
Ketika wajan besi menyentuh bara panas, suara desis langsung menggoda selera.

Chapter 3: Rising Pressure Inside the Kitchen
Every detail of heat control shapes the broth texture.

Bab 4 - Titik Balik Sang Pedagang
Dalam sekejap antrean panjang mulai memadati trotoar sempit.

[ch:5]
Hidangan terakhir disajikan hangat bersama sambal racikan turun temurun.

#Bab 6: Penutup Malam yang HangatAkhirnya lampu gerobak padam perlahan menjelang fajar.
"""
    chapters, _doc_title = parse_markdown_script(md_tricky, preset=get_preset("umum.id"))
    assert len(chapters) == 6
    assert chapters[0].chapter_title == "Panggilan Lapar dan Legenda Kaki Lima"
    assert chapters[1].chapter_title == "Harmoni Rempah Tanpa Batas"
    assert chapters[2].chapter_title == "Rising Pressure Inside the Kitchen"
    assert chapters[3].chapter_title == "Titik Balik Sang Pedagang"
    assert chapters[4].chapter_index == 5
    assert chapters[5].chapter_title == "Penutup Malam yang Hangat"
    assert "Akhirnya lampu gerobak padam perlahan" in chapters[5].tts_groups[0].paragraph_text

    for ch in chapters:
        for grp in ch.tts_groups:
            assert not grp.paragraph_text.lstrip().lower().startswith(("#bab", "bab ", "chapter ", "[ch:"))

    # ScriptGenerator helper strips #Bab / Chapter headings too
    h_split, b_split = ScriptGenerator._extract_heading_and_body("#Bab 1: Panggilan Lapar\nMalam selalu ramai.")
    assert h_split == "Panggilan Lapar"
    assert b_split == "Malam selalu ramai."

    # Build a synthetic copy matching real corrupted projects (kulineran + street food style)
    proj_root = tmp_path / "projects_f11"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    monkeypatch.setitem(config.ui, "language", "id")

    manifest, p_dir = create_project("f11_fix", "Kulineran Uji", "umum.id", projects_root=proj_root)
    audio_ch1 = p_dir / "audio" / "ch1_grp1.mp3"
    audio_ch1.parent.mkdir(parents=True, exist_ok=True)
    audio_ch1.write_bytes(b"fake-mp3")

    from app.services.longform.schema import AudioSpec, Segment

    ch1 = Chapter(
        chapter_index=1,
        chapter_title="Panggilan Lapar dan Legenda Kaki Lima",
        status=ItemStatus.completed,
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="#Bab 1: Panggilan Lapar dan Legenda Kaki Lima Malam selalu membuka aroma arang.",
                audio=AudioSpec(file_path="audio/ch1_grp1.mp3"),
                status=ItemStatus.completed,
                segments=[
                    Segment(
                        segment_index=1,
                        narration_text="#Bab 1: Panggilan Lapar dan Legenda Kaki Lima Malam selalu membuka aroma arang.",
                    )
                ],
            ),
        ],
        shots=[
            Shot(
                shot_id="s_1_1",
                chapter_index=1,
                shot_index=1,
                segment_indices=[1],
                search_query="bab panggilan lapar legenda",
                search_query_source="extraction",
            )
        ],
    )
    manifest.chapters = [ch1]
    save_manifest(manifest, p_dir)

    hits = detect_leaked_chapter_headings(manifest)
    assert len(hits) == 1
    assert hits[0]["chapter_index"] == 1

    # Verify Naskah UI displays warning banner + repair button and repairs cleanly
    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = "f11_fix"
    at.session_state["active_step_f11_fix"] = "script"
    at.run(timeout=25)
    assert not at.exception
    assert any("judul bab" in str(w.value).lower() for w in at.warning)
    fix_btns = [b for b in at.button if b.key == "btn_fix_headings_f11_fix"]
    assert len(fix_btns) == 1
    fix_btns[0].click().run(timeout=25)
    assert not at.exception

    fixed_manifest = load_manifest(p_dir)
    assert detect_leaked_chapter_headings(fixed_manifest) == []
    fixed_grp = fixed_manifest.chapters[0].tts_groups[0]
    assert fixed_grp.paragraph_text == "Malam selalu membuka aroma arang."
    assert fixed_grp.segments[0].narration_text == "Malam selalu membuka aroma arang."
    assert fixed_grp.status == ItemStatus.stale
    assert "bab" not in (fixed_manifest.chapters[0].shots[0].search_query or "").lower().split()

    steps = compute_project_step_status(p_dir)
    assert steps["steps"]["voice"]["status"] == "perlu_diperbarui"

    # CLI project fix-headings works cleanly as well
    ch2 = Chapter(
        chapter_index=2,
        chapter_title="The Midnight Queue",
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Bab 2: The Midnight Queue Lanterns glow over warm broth.",
                status=ItemStatus.pending,
                segments=[
                    Segment(
                        segment_index=1,
                        narration_text="Bab 2: The Midnight Queue Lanterns glow over warm broth.",
                    )
                ],
            )
        ],
    )
    fixed_manifest.chapters.append(ch2)
    save_manifest(fixed_manifest, p_dir)
    monkeypatch.setattr(cli_longform, "get_project_dir", lambda pid: proj_root / pid)
    monkeypatch.setattr(sys, "argv", ["cli_longform.py", "project", "fix-headings", "f11_fix"])
    assert cli_longform.main() == 0
    cli_fixed = load_manifest(p_dir)
    assert cli_fixed.chapters[1].tts_groups[0].paragraph_text == "Lanterns glow over warm broth."


def test_f11_honest_keyword_labels_visible_fallback_and_vision_disabled_hint(tmp_path: Path, monkeypatch):
    from app.services.longform.visual.ai_keywords import generate_chapter_visual_keywords_ai

    proj_root = tmp_path / "projects_f11_vis"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    monkeypatch.setitem(config.ui, "language", "id")

    manifest, p_dir = create_project(
        "f11_vis_ui",
        "Kaki Lima Nusantara",
        "umum.id",
        language="id-ID",
        projects_root=proj_root,
    )
    shot = Shot(
        shot_id="s_1_1",
        chapter_index=1,
        shot_index=1,
        segment_indices=[1],
        search_query="aroma panggangan arang jalanan",
        search_query_en=None,
        search_query_source="extraction",
        search_query_reason=None,
    )
    ch = Chapter(
        chapter_index=1,
        chapter_title="Suasana Malam",
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Aroma panggangan arang dan bawang putih memenuhi pinggir jalan malam.",
            )
        ],
        shots=[shot],
    )
    ch.tts_groups[0].segments = [
        __import__("app.services.longform.schema", fromlist=["Segment"]).Segment(
            segment_index=1,
            narration_text="Aroma panggangan arang dan bawang putih memenuhi pinggir jalan malam.",
        )
    ]
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    # 1. No-LLM fallback records search_query_reason='llm_not_ready' and leaves search_query_en=None
    monkeypatch.setattr(
        "app.services.longform.visual.ai_keywords.get_llm_readiness",
        lambda *a, **k: {"ready": False, "reason": "no_key"},
    )
    res_kw = generate_chapter_visual_keywords_ai(
        [shot],
        ch,
        project_lang="id-ID",
        cache_dir=tmp_path / "kw_cache",
        force=True,
    )
    assert res_kw["fallback_used"] is True
    assert shot.search_query_en is None
    assert shot.search_query_source == "extraction"
    assert shot.search_query_reason == "llm_not_ready"
    save_manifest(manifest, p_dir)

    # 2. Visual UI with non-vision LLM & non-EN extracted query: honest label + reason + disabled tooltip/hint/settings
    monkeypatch.setattr(
        "app.services.longform.ui.components.check_llm_status",
        lambda: {"configured": True, "provider": "openrouter", "model": "openrouter/free", "masked_key": "****9999", "is_invalid": False},
    )
    monkeypatch.setattr(
        "app.services.longform.visual.ai_relevance.get_vision_readiness",
        lambda *a, **k: {"vision_supported": False, "provider": "openrouter", "model": "openrouter/free", "reason": "text_only_model"},
    )
    monkeypatch.setattr(
        "app.services.longform.visual.ai_video.list_configured_ai_video_providers",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "app.services.longform.visual.ai_image.get_ai_image_readiness",
        lambda *a, **k: {"ready": False, "provider": "", "model": ""},
    )

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = "f11_vis_ui"
    at.session_state["active_step_f11_vis_ui"] = "visual"
    at.run(timeout=25)
    assert not at.exception

    md_all = "\n".join(str(m.value) for m in at.markdown)
    cap_all = "\n".join(str(c.value) for c in at.caption)
    assert "Kata kunci pencarian (id-ID):" in md_all
    assert "Kata kunci pencarian (EN):" not in md_all
    assert "LLM belum siap" in md_all
    assert "vision" in cap_all.lower()

    recheck_btn = [b for b in at.button if b.key == "btn_recheck_ai_f11_vis_ui_s_1_1"][0]
    assert recheck_btn.disabled is True
    assert bool(recheck_btn.help)
    cfg_btns = [b for b in at.button if b.key == "btn_recheck_cfg_f11_vis_ui_s_1_1"]
    assert len(cfg_btns) == 1

    # 3. Clicking "Perbarui kata kunci dengan AI" when LLM not ready persists visible warning after rerun
    monkeypatch.setattr(
        "app.services.longform.visual.service.VisualService.update_keywords_with_ai",
        lambda self, **kw: (
            manifest,
            {
                "updated": 1,
                "stale_marked": 0,
                "fallback_used": True,
                "warnings": ["Ch 1: LLM belum siap — memakai ekstraksi lokal"],
            },
        ),
    )
    run_kw_btn = [b for b in at.button if b.key == "btn_run_ai_kw_f11_vis_ui"][0]
    run_kw_btn.click().run(timeout=25)
    assert not at.exception
    assert any("LLM belum siap" in str(w.value) for w in at.warning)


# ---------------------------------------------------------------------------
# ADDENDUM F12 — Concrete Visual Keywords, Project Anchors/Avoids, Multi-Query
# ---------------------------------------------------------------------------


def test_f12_concrete_keywords_and_abstract_sentence_anchoring(tmp_path: Path, monkeypatch):
    from app.services.longform.schema import Segment
    from app.services.longform.visual.ai_keywords import (
        ABSTRACT_NON_VISUAL_WORDS,
        ensure_project_visual_anchors,
        update_project_visual_keywords_ai,
    )

    proj_root = tmp_path / "projects_f12"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    monkeypatch.setitem(config.ui, "language", "id")

    manifest, p_dir = create_project(
        "f12_kuliner",
        "Kulineran Malam",
        "umum.id",
        language="id-ID",
        projects_root=proj_root,
    )
    manifest.topic = "Kuliner Kaki Lima Indonesia Malam Hari"
    sh1 = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        segment_indices=[1],
        search_query="malam cara unik membangunkan lapar",
    )
    sh2 = Shot(
        shot_id="ch01_shot002",
        chapter_index=1,
        shot_index=2,
        segment_indices=[2],
        search_query="warung tenda sederhana bangku plastik",
    )
    ch1 = Chapter(
        chapter_index=1,
        chapter_title="Panggilan Lapar Kaki Lima",
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text=(
                    "Malam selalu punya cara unik untuk membangunkan rasa lapar yang sebenarnya tidak direncanakan. "
                    "Di warung tenda sederhana dengan bangku plastik seadanya, hidangan hangat tersaji cepat."
                ),
                segments=[
                    Segment(
                        segment_index=1,
                        narration_text="Malam selalu punya cara unik untuk membangunkan rasa lapar yang sebenarnya tidak direncanakan.",
                    ),
                    Segment(
                        segment_index=2,
                        narration_text="Di warung tenda sederhana dengan bangku plastik seadanya, hidangan hangat tersaji cepat.",
                    ),
                ],
            )
        ],
        shots=[sh1, sh2],
    )
    manifest.chapters = [ch1]
    save_manifest(manifest, p_dir)

    prompts_seen = []

    class _MockLLM:
        def generate(self, prompt: str, **_kw):
            prompts_seen.append(prompt)
            if '"anchor_terms"' in prompt:
                return LLMResponse(
                    content=json.dumps({
                        "anchor_terms": ["food", "street food", "vendor", "cooking", "stall", "night market"],
                        "avoid_terms": ["animal", "wildlife", "pet", "panda", "cartoon", "garbage"],
                        "visual_summary": "Indonesian night street food vendors cooking at glowing roadside stalls.",
                    }),
                    provider="fake",
                    model="fake-vision",
                    duration_seconds=0.1,
                )
            # Simulate weak LLM outputting an abstract query on shot #1 alongside concrete alternatives on shot #2
            return LLMResponse(
                content=json.dumps({
                    "ch01_shot001": ["unplanned hunger night", "night street food stall crowd", "vendor grilling skewers smoke"],
                    "ch01_shot002": ["street food tent stall diners", "night market food tables crowd", "vendor serving hot soup bowl"],
                }),
                provider="fake",
                model="fake-llm",
                duration_seconds=0.1,
            )

    cache_dir = tmp_path / "f12_cache"
    res = update_project_visual_keywords_ai(
        manifest,
        force=True,
        cache_dir=cache_dir,
        llm_adapter=_MockLLM(),
    )
    save_manifest(manifest, p_dir)

    # 1. Anchor & avoid terms persisted once and reused on cached call
    assert "street food" in manifest.visual_anchor_terms
    assert "animal" in manifest.visual_avoid_terms
    assert manifest.visual_summary
    calls_before = len(prompts_seen)
    res_cached_anchor = ensure_project_visual_anchors(manifest, cache_dir=cache_dir, llm_adapter=_MockLLM(), force=False)
    assert len(prompts_seen) == calls_before
    assert res_cached_anchor["source"] == "manifest"

    # 2. Batch prompt included project topic, chapter title, visual_summary, and anchors
    batch_prompt = prompts_seen[-1]
    assert "Kuliner Kaki Lima Indonesia Malam Hari" in batch_prompt
    assert "Panggilan Lapar Kaki Lima" in batch_prompt
    assert "street food" in batch_prompt

    # 3. Abstract query sanitized & replaced with concrete themed query; 2-3 alternatives saved
    assert len(sh1.search_queries_en) >= 2
    assert len(sh2.search_queries_en) >= 2
    for q_text in sh1.search_queries_en + sh2.search_queries_en:
        words_low = set(q_text.lower().split())
        assert not (words_low & ABSTRACT_NON_VISUAL_WORDS), f"Abstract word leaked into query: {q_text}"
    assert "hunger" not in (sh1.search_query_en or "").lower()
    assert "unplanned" not in (sh1.search_query_en or "").lower()
    assert res["updated"] == 2

    # 4. UI "Pengaturan Lanjutan Proyek (Jangkar Visual)" edits & persists anchor/avoid terms cleanly
    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = "f12_kuliner"
    at.session_state["active_step_f12_kuliner"] = "visual"
    at.run(timeout=25)
    assert not at.exception
    anc_inp = [ti for ti in at.text_input if ti.key == "txt_proj_anchors_f12_kuliner"][0]
    avd_inp = [ti for ti in at.text_input if ti.key == "txt_proj_avoids_f12_kuliner"][0]
    anc_inp.input("food, street food, noodles, wok")
    avd_inp.input("animal, panda, plastic bottle, garbage")
    save_anc_btn = [b for b in at.button if b.key == "btn_save_proj_anchors_f12_kuliner"][0]
    save_anc_btn.click().run(timeout=25)
    assert not at.exception
    reloaded = load_manifest(p_dir)
    assert reloaded.visual_anchor_terms == ["food", "street food", "noodles", "wok"]
    assert reloaded.visual_avoid_terms == ["animal", "panda", "plastic bottle", "garbage"]


def test_f12_anchor_avoid_stock_filter_and_alternative_query_fallback(tmp_path: Path, monkeypatch):
    from app.services.longform.schema import Segment
    from app.services.longform.visual.base import VisualCandidate
    from app.services.longform.visual.stock import StockVisualProvider, score_candidate_relevance

    anchors = ["food", "street food", "vendor", "cooking", "stall", "night market"]
    avoids = ["animal", "wildlife", "pet", "panda", "cartoon", "garbage", "bottle"]

    # 1. Red panda candidate must be rejected immediately by avoid_terms + missing anchors
    ok_panda, score_panda, reason_panda = score_candidate_relevance(
        candidate_tags=["red panda", "animal", "mammal", "nature", "eating"],
        search_query="unplanned hunger night",
        anchor_terms=anchors,
        avoid_terms=avoids,
    )
    assert ok_panda is False
    assert score_panda == 0.0
    assert "avoid" in reason_panda.lower()

    # 2. Plastic bottle candidate with 0 matching anchor_terms rejected with low score
    ok_bottle, score_bottle, reason_bottle = score_candidate_relevance(
        candidate_tags=["plastic", "ground", "litter", "urban", "street"],
        search_query="simple street plastic",
        anchor_terms=anchors,
        avoid_terms=avoids,
    )
    assert ok_bottle is False
    assert score_bottle < 0.35
    assert "anchor" in reason_bottle.lower()

    # 3. Genuine street food candidate passes with high score
    ok_food, score_food, _reason_food = score_candidate_relevance(
        candidate_tags=["street food", "vendor", "cooking", "night market", "noodles"],
        search_query="night street food vendor cooking",
        anchor_terms=anchors,
        avoid_terms=avoids,
    )
    assert ok_food is True
    assert score_food >= 0.70

    # 4. End-to-end VisualService.fetch_all tries alternative query #2 when query #1 only returns rejected animals
    proj_root = tmp_path / "projects_f12_fetch"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)

    manifest, p_dir = create_project(
        "f12_fetch",
        "Kuliner Malam",
        "umum.id",
        language="id-ID",
        projects_root=proj_root,
    )
    manifest.visual_anchor_terms = anchors
    manifest.visual_avoid_terms = avoids
    shot = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        segment_indices=[1],
        duration_seconds=3.0,
        target_frames=75,
        requested_source="stock",
        search_query="malam bangunkan rasa lapar",
        search_query_en="night city sidewalk lights",
        search_queries_en=[
            "night city sidewalk lights",
            "night street food vendor cooking",
            "street food stall sizzling wok",
        ],
        search_query_source="ai",
    )
    ch = Chapter(
        chapter_index=1,
        chapter_title="Pembukaan",
        tts_groups=[
            TtsGroup(
                group_index=1,
                paragraph_text="Malam selalu punya cara membangunkan rasa lapar.",
                segments=[Segment(segment_index=1, narration_text="Malam selalu punya cara membangunkan rasa lapar.")],
            )
        ],
        shots=[shot],
    )
    manifest.chapters = [ch]
    save_manifest(manifest, p_dir)

    sample_mp4 = _make_sample_mp4(tmp_path / "good_street_food.mp4", duration=4.0, color="orange")
    bad_mp4 = _make_sample_mp4(tmp_path / "bad_panda.mp4", duration=4.0, color="red")

    class _FakePixabayApiProvider(StockVisualProvider):
        tried_queries: list[str] = []

        def validate_credentials(self) -> None:
            return None

        def search_candidates(
            self,
            query: str,
            target_duration: float,
            limit: int = 3,
            excluded_ids=None,
            required_subject_terms=None,
            blocked_terms=None,
            visual_keywords=None,
            negative_terms=None,
            anchor_terms=None,
            avoid_terms=None,
            min_relevance_score=None,
            category=None,
            **kwargs,
        ):
            self.tried_queries.append(query)
            if "sidewalk" in query.lower():
                raw_pool = [
                    ("pixabay_panda", ["red panda", "animal", "wildlife"], str(bad_mp4)),
                ]
            elif "vendor cooking" in query.lower():
                raw_pool = [
                    ("pixabay_panda_2", ["red panda", "animal"], str(bad_mp4)),
                    ("pixabay_food_ok", ["street food", "vendor", "cooking", "night market"], str(sample_mp4)),
                ]
            else:
                raw_pool = []

            kept = []
            for cid, tags, src in raw_pool:
                ok, sc, _ = score_candidate_relevance(
                    candidate_tags=tags,
                    search_query=query,
                    visual_keywords=visual_keywords,
                    required_subject_terms=required_subject_terms,
                    blocked_terms=blocked_terms,
                    negative_terms=negative_terms,
                    anchor_terms=anchor_terms,
                    avoid_terms=avoid_terms,
                    min_relevance_score=min_relevance_score,
                )
                if ok:
                    kept.append(
                        VisualCandidate(
                            candidate_id=cid,
                            provider="pixabay",
                            source_type="stock_video",
                            url=src,
                            duration_seconds=4.0,
                            width=1920,
                            height=1080,
                            tags=tags,
                            relevance_score=sc,
                        )
                    )
            return kept[:limit]

        def fetch_asset(self, candidate: VisualCandidate, dest_path: Path):
            from app.services.longform.visual.base import VisualAsset, compute_file_content_hash

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate.url, dest_path)
            return VisualAsset(
                asset_id=candidate.candidate_id,
                provider=candidate.provider,
                source_type=candidate.source_type,
                local_path=dest_path,
                content_hash=compute_file_content_hash(dest_path),
                width=1920,
                height=1080,
                duration_seconds=4.0,
            )

    fake_prov = _FakePixabayApiProvider("stock")
    vis_svc = VisualService(p_dir)
    monkeypatch.setattr(vis_svc, "get_provider", lambda *_a, **_kw: fake_prov)

    updated_m = vis_svc.fetch_all(manifest, provider_name="stock", allow_fallback=False, translate_keywords=False)
    updated_sh = updated_m.chapters[0].shots[0]
    assert "night city sidewalk lights" in fake_prov.tried_queries
    assert updated_sh.effective_query == "night street food vendor cooking"
    assert "street food" in updated_sh.tags
    assert "animal" not in " ".join(updated_sh.tags).lower()
    assert updated_sh.asset_path and (p_dir / updated_sh.asset_path).is_file()


# ---------------------------------------------------------------------------
# ADDENDUM F13 — Re-fetch on Changed Keywords, Short Query Escalation, Cand Filter
# ---------------------------------------------------------------------------


def test_f13_refetch_on_keyword_change_short_query_escalation_cand_filter_and_locked_preserved(
    tmp_path: Path, monkeypatch
):
    from app.services.longform.schema import Segment
    from app.services.longform.visual.base import VisualAsset, VisualCandidate
    from app.services.longform.visual.stock import (
        StockVisualProvider,
        filter_candidates_by_anchors_and_avoids,
        score_candidate_relevance,
    )

    proj_root = tmp_path / "projects_f13"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    monkeypatch.setitem(config.ui, "language", "id")

    manifest, p_dir = create_project(
        "f13_refetch",
        "Kulineran F13",
        "umum.id",
        language="id-ID",
        projects_root=proj_root,
    )
    manifest.visual_anchor_terms = ["street food", "vendor", "cooking", "stall", "night market"]
    manifest.visual_avoid_terms = ["animal", "wildlife", "pet", "panda", "cartoon"]

    old_panda_mp4 = _make_sample_mp4(p_dir / "assets/visual/shots/ch01_shot001_asset.mp4", duration=3.5, color="red")
    locked_mp4 = _make_sample_mp4(p_dir / "assets/visual/shots/ch01_shot002_asset.mp4", duration=3.5, color="green")
    fresh_food_mp4 = _make_sample_mp4(tmp_path / "fresh_street_food.mp4", duration=3.5, color="blue")

    old_cands = [
        {"candidate_id": "pixabay_90290", "provider": "pixabay", "tags": ["red panda", "animal", "wildlife"], "relevance_score": 0.37},
        {"candidate_id": "pixabay_90291", "provider": "pixabay", "tags": ["cheetah", "wildlife", "animal"], "relevance_score": 0.37},
        {"candidate_id": "pixabay_90292", "provider": "pixabay", "tags": ["hedgehog", "pet", "animal"], "relevance_score": 0.37},
    ]

    # Case A: Filter candidates rejects avoid_terms (cheetah/hedgehog/panda) and keeps valid food candidates
    mixed_cands = old_cands + [
        {"candidate_id": "pixabay_24602", "provider": "pixabay", "tags": ["street food", "vendor", "night market"], "relevance_score": 0.72},
    ]
    filtered_cands = filter_candidates_by_anchors_and_avoids(
        mixed_cands,
        anchor_terms=manifest.visual_anchor_terms,
        avoid_terms=manifest.visual_avoid_terms,
    )
    assert [c["candidate_id"] for c in filtered_cands] == ["pixabay_24602"]

    sh1 = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        segment_indices=[1],
        duration_seconds=3.5,
        target_frames=87,
        requested_source="pixabay",
        actual_source="pixabay",
        search_query="malam membangunkan lapar",
        search_query_en="unplanned hunger night",
        effective_query="unplanned hunger night",
        asset_path="assets/visual/shots/ch01_shot001_asset.mp4",
        asset_content_hash=compute_file_content_hash(old_panda_mp4),
        tags=["red panda", "animal", "wildlife"],
        relevance_score=0.37,
        candidates=list(old_cands),
        status=ItemStatus.completed,
    )
    sh2_locked = Shot(
        shot_id="ch01_shot002",
        chapter_index=1,
        shot_index=2,
        segment_indices=[2],
        duration_seconds=3.5,
        target_frames=87,
        requested_source="pixabay",
        actual_source="pixabay",
        search_query="warung tenda malam",
        search_query_en="night street food stall",
        effective_query="night street food stall",
        asset_path="assets/visual/shots/ch01_shot002_asset.mp4",
        asset_content_hash=compute_file_content_hash(locked_mp4),
        shot_hash="locked-hash-f13",
        locked=True,
        tags=["street food", "stall", "night"],
        relevance_score=0.75,
        status=ItemStatus.completed,
    )
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Pembukaan",
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Malam membuka rasa lapar. Di warung tenda makanan tersaji.",
                    segments=[
                        Segment(segment_index=1, narration_text="Malam membuka rasa lapar."),
                        Segment(segment_index=2, narration_text="Di warung tenda makanan tersaji."),
                    ],
                )
            ],
            shots=[sh1, sh2_locked],
        )
    ]
    save_manifest(manifest, p_dir)

    # Case B: Updating keywords marks unlocked shot stale and IMMEDIATELY clears old animal candidates/tags, leaving locked intact
    class _F13MockLLM:
        def generate(self, prompt: str, **_kw):
            return LLMResponse(
                content=json.dumps({
                    "ch01_shot001": [
                        "night street food stall crowd",
                        "vendor cooking noodles wok stall",
                        "street food night market crowd",
                    ]
                }),
                provider="fake",
                model="fake-llm",
                duration_seconds=0.1,
            )

    vis_svc = VisualService(p_dir)
    monkeypatch.setattr(
        "app.services.longform.visual.ai_keywords.get_llm_readiness",
        lambda *_a, **_kw: {"configured": True, "is_invalid": False},
    )
    manifest, _ = vis_svc.update_keywords_with_ai(manifest, force=True, mark_stale=True, llm_adapter=_F13MockLLM())
    assert sh1.status == ItemStatus.stale
    assert sh1.candidates == []
    assert sh1.tags == []
    assert sh1.effective_query is None
    assert sh2_locked.locked is True
    assert sh2_locked.shot_hash == "locked-hash-f13"
    assert sh2_locked.status == ItemStatus.completed

    # Case C: Even if status was left 'completed' with old effective_query, fetch_all MUST detect outdated effective_query and re-fetch!
    sh1.status = ItemStatus.completed
    sh1.effective_query = "unplanned hunger night"

    class _ShortQueryEscalationProvider(StockVisualProvider):
        tried: list[str] = []

        def validate_credentials(self) -> None:
            return None

        def search_candidates(
            self,
            query: str,
            target_duration: float,
            limit: int = 3,
            excluded_ids=None,
            required_subject_terms=None,
            blocked_terms=None,
            visual_keywords=None,
            negative_terms=None,
            anchor_terms=None,
            avoid_terms=None,
            min_relevance_score=None,
            category=None,
            **kwargs,
        ):
            self.tried.append(query)
            # Simulate 5-word queries returning 0 results on Pixabay, while shortened 3-word "street food night" returns 3 hits
            if len(query.split()) > 3:
                return []
            if query.lower() == "street food night":
                return [
                    VisualCandidate(
                        candidate_id=f"pixabay_sf_{idx}",
                        provider="pixabay",
                        source_type="stock_video",
                        url=str(fresh_food_mp4),
                        duration_seconds=3.5,
                        width=1920,
                        height=1080,
                        tags=["street food", "vendor", "night market"],
                        relevance_score=0.72 - idx * 0.02,
                    )
                    for idx in range(1, 4)
                ]
            return []

        def fetch_asset(self, candidate: VisualCandidate, dest_path: Path):
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate.url, dest_path)
            return VisualAsset(
                asset_id=candidate.candidate_id,
                provider=candidate.provider,
                source_type=candidate.source_type,
                local_path=dest_path,
                content_hash=compute_file_content_hash(dest_path),
                width=1920,
                height=1080,
                duration_seconds=3.5,
            )

    esc_prov = _ShortQueryEscalationProvider("pixabay")
    monkeypatch.setattr(vis_svc, "get_provider", lambda *_a, **_kw: esc_prov)

    fetched_m = vis_svc.fetch_all(manifest, provider_name="pixabay", allow_fallback=False, translate_keywords=False)
    save_manifest(fetched_m, p_dir)

    assert "night street food stall crowd" in esc_prov.tried
    assert "street food night" in esc_prov.tried
    assert sh1.effective_query == "street food night"
    assert len(sh1.candidates) == 3
    assert sh1.tags == ["street food", "vendor", "night market"]
    assert sh2_locked.shot_hash == "locked-hash-f13"
    assert sh2_locked.effective_query == "night street food stall"

    # Case D: Detail panel shows "Ditemukan dengan: street food night" and excludes animal candidates
    monkeypatch.setattr(
        "app.services.longform.visual.ai_relevance.get_vision_readiness",
        lambda *a, **k: {"vision_supported": False, "provider": "openrouter", "model": "openrouter/free", "reason": "text_only_model"},
    )
    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = "f13_refetch"
    at.session_state["active_step_f13_refetch"] = "visual"
    at.run(timeout=25)
    assert not at.exception
    md_text = "\n".join(str(m.value) for m in at.markdown)
    assert "Ditemukan dengan:" in md_text
    assert "street food night" in md_text


# ---------------------------------------------------------------------------
# ADDENDUM F14 — Single Visual Source of Truth, Hash-Keyed Thumbnails, Rebuild Badge
# ---------------------------------------------------------------------------


def test_f14_thumbnail_detail_single_source_hash_keyed_and_needs_rebuild_badges(tmp_path: Path, monkeypatch):
    from app.services.longform.schema import Segment
    from app.services.longform.ui.components import get_shot_thumbnail_path
    from app.services.longform.visual.base import resolve_shot_visual_source

    proj_root = tmp_path / "projects_f14"
    proj_root.mkdir()
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: proj_root)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: proj_root)
    monkeypatch.setitem(config.ui, "language", "id")

    manifest, p_dir = create_project(
        "f14_thumb_sync",
        "Sinkron Thumbnail F14",
        "umum.id",
        language="id-ID",
        projects_root=proj_root,
    )

    blue_asset = _make_sample_mp4(p_dir / "assets/visual/shots/ch01_shot001_asset.mp4", duration=3.0, color="blue")
    blue_clip = _make_sample_mp4(p_dir / "assets/visual/shots/ch01_shot001.mp4", duration=3.0, color="blue")
    green_upload = _make_sample_mp4(tmp_path / "green_new.mp4", duration=3.0, color="green")
    orange_asset = _make_sample_mp4(p_dir / "assets/visual/shots/ch01_shot002_asset.mp4", duration=3.0, color="orange")

    # Seed a stale legacy unkeyed RED thumbnail simulating Shot #2 in kulineran!
    thumb_dir = p_dir / "assets/visual/thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    stale_legacy_thumb = _make_sample_jpg(thumb_dir / "ch01_shot001.jpg", color=(220, 20, 20))
    os.utime(stale_legacy_thumb, (1000.0, 1000.0))

    blue_asset_hash = compute_file_content_hash(blue_asset)
    sh1 = Shot(
        shot_id="ch01_shot001",
        chapter_index=1,
        shot_index=1,
        segment_indices=[1],
        duration_seconds=3.0,
        target_frames=75,
        requested_source="pixabay",
        actual_source="pixabay",
        search_query="blue night scene",
        asset_path="assets/visual/shots/ch01_shot001_asset.mp4",
        asset_content_hash=blue_asset_hash,
        rendered_clip="assets/visual/shots/ch01_shot001.mp4",
        shot_hash=compute_shot_hash(
            shot_id="ch01_shot001",
            duration_seconds=3.0,
            target_frames=75,
            motion="zoom_in",
            asset_content_hash=blue_asset_hash,
            fps=25,
            requested_provider="pixabay",
            actual_provider="pixabay",
        ),
        status=ItemStatus.completed,
    )
    sh2_not_built = Shot(
        shot_id="ch01_shot002",
        chapter_index=1,
        shot_index=2,
        segment_indices=[2],
        duration_seconds=3.0,
        target_frames=75,
        requested_source="pixabay",
        actual_source="pixabay",
        search_query="orange food stall",
        asset_path="assets/visual/shots/ch01_shot002_asset.mp4",
        asset_content_hash=compute_file_content_hash(orange_asset),
        rendered_clip=None,
        status=ItemStatus.pending,
    )
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Visual Sync",
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Adegan pertama biru. Adegan kedua jingga belum disusun.",
                    segments=[
                        Segment(segment_index=1, narration_text="Adegan pertama biru."),
                        Segment(segment_index=2, narration_text="Adegan kedua jingga belum disusun."),
                    ],
                )
            ],
            shots=[sh1, sh2_not_built],
        )
    ]
    save_manifest(manifest, p_dir)

    # 1. Hash-keyed thumbnail rejects stale unkeyed red file and extracts BLUE frame from rendered_clip
    t1_blue = get_shot_thumbnail_path(p_dir, sh1)
    assert t1_blue is not None and t1_blue.is_file()
    assert not stale_legacy_thumb.exists()
    assert t1_blue.name.startswith("ch01_shot001_")
    with Image.open(t1_blue) as im:
        r, g, b = im.getpixel((im.width // 2, im.height // 2))
        assert b > r + 80, f"Expected blue clip thumbnail, got RGB={(r, g, b)}"

    # 2. Replace asset WITHOUT rebuild -> status stale, resolve_shot_visual_source flags needs_rebuild=True
    vis_svc = VisualService(p_dir)
    vis_svc.replace_shot(manifest, "ch01_shot001", file_path=green_upload, rebuild=False)
    save_manifest(manifest, p_dir)
    assert sh1.status == ItemStatus.stale

    v_state_1 = resolve_shot_visual_source(p_dir, sh1)
    assert v_state_1["source_kind"] == "clip"
    assert v_state_1["needs_rebuild"] is True
    assert v_state_1["is_needs_update"] is True
    v_state_2 = resolve_shot_visual_source(p_dir, sh2_not_built)
    assert v_state_2["source_kind"] == "asset"
    assert v_state_2["not_built"] is True

    # 3. UI shows "Perlu diperbarui · 1" chip, "Perlu disusun ulang" badge on card + detail, and "Belum disusun" on shot #2
    monkeypatch.setattr(
        "app.services.longform.visual.ai_relevance.get_vision_readiness",
        lambda *a, **k: {"vision_supported": False, "provider": "openrouter", "model": "openrouter/free", "reason": "text_only_model"},
    )
    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = "f14_thumb_sync"
    at.session_state["active_step_f14_thumb_sync"] = "visual"
    at.run(timeout=25)
    assert not at.exception
    caps = "\n".join(str(c.value) for c in at.caption)
    warns = "\n".join(str(w.value) for w in at.warning)
    assert "Perlu disusun ulang" in caps
    assert "Belum disusun" in caps
    assert "Perlu disusun ulang" in warns

    # 4. Rebuild Shot #1 -> rendered_clip turns green, hash-keyed thumbnail regenerates with GREEN pixel & cleans old hash!
    vis_svc.replace_shot(manifest, "ch01_shot001", file_path=green_upload, rebuild=True)
    save_manifest(manifest, p_dir)
    t1_green = get_shot_thumbnail_path(p_dir, sh1)
    assert t1_green is not None and t1_green.is_file()
    assert t1_green != t1_blue
    assert not t1_blue.exists()
    with Image.open(t1_green) as im:
        r2, g2, b2 = im.getpixel((im.width // 2, im.height // 2))
        assert g2 > b2 + 60, f"Expected regenerated green clip thumbnail, got RGB={(r2, g2, b2)}"


def test_f15_partial_visual_voice_render_progress_accuracy_and_indeterminate(
    tmp_path: Path, monkeypatch
):
    """F15: Partial runs count only target units starting from 0%, track skipped valid/locked count, and only hit 100% on completion."""
    from datetime import datetime
    from unittest.mock import patch
    from app.services.longform.hashing import compute_chapter_hash
    from app.services.longform.render.service import RenderService
    from app.services.longform.schema import Chapter, ItemStatus, Shot, TtsGroup
    from app.services.longform.store import create_project, save_manifest, update_job_phase
    from app.services.longform.ui import jobs
    from app.services.longform.visual.service import VisualService
    from app.services.longform.voice_service import VoiceService

    # --- 1. Visual partial pipeline (16 valid/locked + 3 stale/changed = 19 total) ---
    manifest, p_dir = create_project(
        "f15_vis_proj",
        "F15 Visual Partial Test",
        "automotive.muscle_car",
        projects_root=tmp_path,
    )
    shots = [
        Shot(
            shot_id=f"ch01_shot{i:03d}",
            chapter_index=1,
            shot_index=i,
            segment_indices=[i],
            search_query="classic muscle car",
            search_query_en="classic muscle car",
            search_query_source="ai",
            duration_seconds=2.0,
            target_frames=50,
            status=ItemStatus.pending,
        )
        for i in range(1, 20)
    ]
    manifest.chapters = [Chapter(chapter_index=1, chapter_title="Bab 1", shots=shots)]
    save_manifest(manifest, p_dir)

    vis_svc = VisualService(p_dir)
    manifest, _ = vis_svc.run_pipeline(manifest, provider_name="fake", allow_fallback=True)
    # Lock shot #1 and verify all 19 completed
    manifest.chapters[0].shots[0].locked = True
    assert all(sh.rendered_clip and (p_dir / sh.rendered_clip).is_file() for sh in manifest.chapters[0].shots)
    # Invalidate ONLY 3 shots (#17, #18, #19) for fetch + build
    for idx in (16, 17, 18):
        sh = manifest.chapters[0].shots[idx]
        sh.asset_content_hash = None
        sh.effective_query = "old query"
        sh.status = ItemStatus.stale
    save_manifest(manifest, p_dir)

    (p_dir / "job_state.json").write_text(
        json.dumps(
            {
                "project_id": "f15_vis_proj",
                "step": "visual_pipeline",
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "progress": {
                    "phase": "Mempersiapkan proses…",
                    "phase_key": "prog_preparing",
                    "phase_args": {},
                    "completed": 0,
                    "total": 0,
                    "skipped": 0,
                    "percentage": 0.0,
                    "is_indeterminate": True,
                    "partial": True,
                },
            }
        ),
        encoding="utf-8",
    )

    vis_snapshots = []

    def _spy_vis_update(proj_dir_or_id, phase, **kwargs):
        update_job_phase(proj_dir_or_id, phase, **kwargs)
        with patch("app.services.longform.ui.jobs.get_project_dir", return_value=p_dir):
            vis_snapshots.append(jobs.get_project_progress("f15_vis_proj", "visual_pipeline"))

    monkeypatch.setattr("app.services.longform.visual.service.update_job_phase", _spy_vis_update)

    with patch("app.services.longform.ui.jobs.get_project_dir", return_value=p_dir):
        init_prog = jobs.get_project_progress("f15_vis_proj", "visual_pipeline")
        assert init_prog["percentage"] == 0.0
        assert init_prog["is_indeterminate"] is True

    manifest, _ = vis_svc.run_pipeline(manifest, provider_name="fake", allow_fallback=True)

    assert len(vis_snapshots) >= 8
    # First snapshot must be 0/3 (0.0%), NOT ~99%, with 16 skipped!
    assert vis_snapshots[0]["completed"] == 0
    assert vis_snapshots[0]["total"] == 3
    assert vis_snapshots[0]["skipped"] == 16
    assert vis_snapshots[0]["percentage"] == 0.0
    assert all(s["skipped"] == 16 for s in vis_snapshots)
    assert all(s["percentage"] < 100.0 for s in vis_snapshots)
    assert vis_snapshots[-1]["completed"] == 3
    assert vis_snapshots[-1]["total"] == 3
    assert vis_snapshots[-1]["percentage"] == 99.0

    # Completion transitions cleanly to 100%
    st_done = json.loads((p_dir / "job_state.json").read_text(encoding="utf-8"))
    st_done["status"] = "completed"
    st_done["exit_code"] = 0
    (p_dir / "job_state.json").write_text(json.dumps(st_done), encoding="utf-8")
    with patch("app.services.longform.ui.jobs.get_project_dir", return_value=p_dir):
        done_prog = jobs.get_project_progress("f15_vis_proj", "visual_pipeline")
        assert done_prog["percentage"] == 100.0
        assert done_prog["phase"] == "Selesai"

    # Case B: All assets valid, ONLY 3 shots need build (e.g. shot_hash invalidated / pending rebuild) -> must start at 0% (never jump to 45%!)
    for idx in (16, 17, 18):
        manifest.chapters[0].shots[idx].shot_hash = None
        manifest.chapters[0].shots[idx].status = ItemStatus.pending
    save_manifest(manifest, p_dir)
    (p_dir / "job_state.json").write_text(
        json.dumps(
            {
                "project_id": "f15_vis_proj",
                "step": "visual_pipeline",
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "progress": {"phase": "Mempersiapkan proses…", "completed": 0, "total": 0, "skipped": 0, "percentage": 0.0, "is_indeterminate": True, "partial": True},
            }
        ),
        encoding="utf-8",
    )
    vis_snapshots.clear()
    manifest, _ = vis_svc.run_pipeline(manifest, provider_name="fake", allow_fallback=True)
    assert vis_snapshots[0]["completed"] == 0
    assert vis_snapshots[0]["total"] == 3
    assert vis_snapshots[0]["skipped"] == 16
    assert vis_snapshots[0]["percentage"] == 0.0
    assert vis_snapshots[1]["completed"] == 1
    assert vis_snapshots[1]["percentage"] == 33.0
    assert vis_snapshots[-1]["completed"] == 3
    assert vis_snapshots[-1]["percentage"] == 99.0

    # --- 2. Voice partial synthesis (3 valid + 2 stale = 5 groups) ---
    m_voice, p_voice = create_project("f15_voice_proj", "F15 Voice Test", "automotive.muscle_car", projects_root=tmp_path)
    m_voice.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            tts_groups=[
                TtsGroup(group_index=i, paragraph_text=f"Paragraph number {i} for voice synthesis.")
                for i in range(1, 6)
            ],
        )
    ]
    save_manifest(m_voice, p_voice)
    v_svc = VoiceService(p_voice)
    m_voice = v_svc.synthesize_all(provider="fake", concurrency=1)
    # Invalidate 2 groups (#4 and #5)
    m_voice.chapters[0].tts_groups[3].audio.audio_hash = "stale_hash"
    m_voice.chapters[0].tts_groups[3].status = ItemStatus.stale
    m_voice.chapters[0].tts_groups[4].audio.audio_hash = "stale_hash"
    m_voice.chapters[0].tts_groups[4].status = ItemStatus.stale
    save_manifest(m_voice, p_voice)

    (p_voice / "job_state.json").write_text(
        json.dumps(
            {
                "project_id": "f15_voice_proj",
                "step": "audio_synthesize",
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "progress": {"phase": "Mempersiapkan proses…", "completed": 0, "total": 0, "skipped": 0, "percentage": 0.0, "is_indeterminate": True, "partial": True},
            }
        ),
        encoding="utf-8",
    )
    voice_snapshots = []

    def _spy_voice_update(proj_dir_or_id, phase, **kwargs):
        update_job_phase(proj_dir_or_id, phase, **kwargs)
        with patch("app.services.longform.ui.jobs.get_project_dir", return_value=p_voice):
            voice_snapshots.append(jobs.get_project_progress("f15_voice_proj", "audio_synthesize"))

    monkeypatch.setattr("app.services.longform.voice_service.update_job_phase", _spy_voice_update)
    m_voice = v_svc.synthesize_all(provider="fake", concurrency=1)

    assert len(voice_snapshots) >= 4
    assert voice_snapshots[0]["completed"] == 0
    assert voice_snapshots[0]["total"] == 2
    assert voice_snapshots[0]["skipped"] == 3
    assert voice_snapshots[0]["percentage"] == 0.0
    assert voice_snapshots[1]["completed"] == 1
    assert voice_snapshots[1]["percentage"] == 47.5
    assert voice_snapshots[-1]["is_indeterminate"] is True
    assert all(s["percentage"] < 100.0 for s in voice_snapshots)

    # --- 3. Render partial run (2 valid skipped + 1 chapter rendered = 3 total chapters) ---
    m_rnd, p_rnd = create_project("f15_rnd_proj", "F15 Render Test", "automotive.muscle_car", projects_root=tmp_path)
    chapters_dir = p_rnd / "assets" / "visual" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    preset_snap = m_rnd.preset_snapshot or {}
    rnd_chapters = []
    for idx in (1, 2, 3):
        out_rel = f"assets/visual/chapters/chapter_{idx:02d}.mp4"
        ch_obj = Chapter(
            chapter_index=idx,
            chapter_title=f"Bab {idx}",
            status=ItemStatus.completed if idx in (1, 2) else ItemStatus.pending,
            output_file=out_rel if idx in (1, 2) else None,
        )
        if idx in (1, 2):
            (p_rnd / out_rel).write_bytes(b"mp4")
            ch_obj.chapter_hash = compute_chapter_hash(
                chapter=ch_obj,
                encoder_settings=m_rnd.encoder_settings,
                subtitle_style=preset_snap.get("subtitle_style", {}),
                preset_snapshot=preset_snap,
                pipeline_version=m_rnd.pipeline_version,
                sub_niche_focus=m_rnd.sub_niche_focus or "",
            )
        rnd_chapters.append(ch_obj)
    m_rnd.chapters = rnd_chapters
    save_manifest(m_rnd, p_rnd)

    (p_rnd / "job_state.json").write_text(
        json.dumps(
            {
                "project_id": "f15_rnd_proj",
                "step": "render_all",
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "progress": {"phase": "Mempersiapkan proses…", "completed": 0, "total": 0, "skipped": 0, "percentage": 0.0, "is_indeterminate": True, "partial": True},
            }
        ),
        encoding="utf-8",
    )
    rnd_svc = RenderService(p_rnd)
    rnd_snapshots = []

    def _spy_rnd_update(proj_dir_or_id, phase, **kwargs):
        update_job_phase(proj_dir_or_id, phase, **kwargs)
        with patch("app.services.longform.ui.jobs.get_project_dir", return_value=p_rnd):
            rnd_snapshots.append(jobs.get_project_progress("f15_rnd_proj", "render_all"))

    monkeypatch.setattr("app.services.longform.render.service.update_job_phase", _spy_rnd_update)
    monkeypatch.setattr("app.services.longform.render.final_concat.update_job_phase", _spy_rnd_update)
    monkeypatch.setattr(
        rnd_svc.chapter_renderer,
        "render_chapter",
        lambda manifest_arg, chapter_index, force=False, _progress_slot=None: (
            p_rnd / f"assets/visual/chapters/chapter_{chapter_index:02d}.mp4",
            {"chapter_index": chapter_index, "elapsed_seconds": 0.1},
        ),
    )

    def _fake_concat(manifest, no_bgm=False):
        from app.services.longform.render.final_concat import update_job_phase as fc_upd

        fc_upd(p_rnd, phase="Menggabungkan video & audio final…", percentage=93.0, is_indeterminate=True)
        out_f = p_rnd / "output" / "final_video.mp4"
        out_f.parent.mkdir(parents=True, exist_ok=True)
        out_f.write_bytes(b"final")
        return out_f, {"status": "completed"}

    monkeypatch.setattr(rnd_svc.final_concatenator, "concatenate_final_video", _fake_concat)
    rnd_svc.render_all(m_rnd)

    assert len(rnd_snapshots) >= 3
    assert rnd_snapshots[0]["completed"] == 0
    assert rnd_snapshots[0]["total"] == 1
    assert rnd_snapshots[0]["skipped"] == 2
    assert rnd_snapshots[0]["percentage"] == 0.0
    assert rnd_snapshots[1]["completed"] == 1
    assert rnd_snapshots[1]["total"] == 1
    assert rnd_snapshots[1]["percentage"] == 80.0
    assert rnd_snapshots[-1]["is_indeterminate"] is True
    assert rnd_snapshots[-1]["percentage"] <= 95.0
    assert rnd_snapshots[-1]["skipped"] == 2


def test_f15_ui_progress_monitor_displays_partial_and_skipped_id_en(tmp_path: Path, monkeypatch):
    """F15: WebUI job monitor shows scoped target progress and skipped valid/locked count in ID and EN."""
    from datetime import datetime
    from streamlit.testing.v1 import AppTest
    from app.services.longform.schema import Chapter, Shot
    from app.services.longform.store import create_project, save_manifest

    manifest, p_dir = create_project("f15_ui_prog", "F15 UI Prog", "automotive.muscle_car", projects_root=tmp_path)
    manifest.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Bab 1",
            shots=[
                Shot(
                    shot_id=f"ch01_shot{i:03d}",
                    chapter_index=1,
                    shot_index=i,
                    segment_indices=[i],
                    search_query="muscle car",
                    duration_seconds=2.0,
                    target_frames=50,
                )
                for i in range(1, 20)
            ],
        )
    ]
    save_manifest(manifest, p_dir)
    (p_dir / "job_state.json").write_text(
        json.dumps(
            {
                "project_id": "f15_ui_prog",
                "step": "visual_pipeline",
                "status": "running",
                "pid": os.getpid(),
                "started_at": datetime.now().isoformat(),
                "progress": {
                    "phase": "Mencari footage (1/3 shot)…",
                    "phase_key": "prog_visual_fetch",
                    "phase_args": {"done": 1, "total": 3},
                    "completed": 1,
                    "total": 3,
                    "skipped": 16,
                    "percentage": 15.0,
                    "unit_label": "shot",
                    "is_indeterminate": False,
                    "partial": True,
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.jobs.get_project_dir", lambda pid: p_dir if pid == "f15_ui_prog" else tmp_path / pid)
    monkeypatch.setattr("app.services.longform.ui.jobs.is_pid_alive", lambda pid: True)

    def _run_monitor_script(lang_code: str):
        script_path = tmp_path / f"mon_{lang_code}.py"
        script_path.write_text(
            "import streamlit as st\n"
            f"st.session_state['ui_language'] = {lang_code!r}\n"
            "from app.services.longform.ui.components import render_job_monitor_widget\n"
            "render_job_monitor_widget('f15_ui_prog')\n",
            encoding="utf-8",
        )
        at = AppTest.from_file(str(script_path))
        at.run(timeout=20)
        assert not at.exception
        return "\n".join(str(getattr(el, "text", "") or getattr(el, "value", "")) for el in at.get("progress"))

    id_prog_text = _run_monitor_script("id")
    assert "1/3 (15.0%)" in id_prog_text
    assert "dilewati: 16 (sudah valid/terkunci)" in id_prog_text
    assert "99.0%" not in id_prog_text

    en_prog_text = _run_monitor_script("en")
    assert "Fetching footage (1/3 shots)" in en_prog_text
    assert "1/3 (15.0%)" in en_prog_text
    assert "skipped: 16 (already valid/locked)" in en_prog_text








