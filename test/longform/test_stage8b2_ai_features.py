"""Tests for Stage 8B.2 AI features (F1 Relevance, F2 Translation, F3 AI Video, F4 Polish)."""

from __future__ import annotations

import json
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
    monkeypatch.setitem(config.app, "llm_provider", provider)
    monkeypatch.setitem(config.app, f"{provider}_api_key", "AIzaDummyKeyForTestCheck_123456" if provider == "gemini" else "sk-test-key-1234567890")
    monkeypatch.setitem(config.app, cfg_key, model_name)
    monkeypatch.setitem(config.app, "llm_vision_override", "")

    info = get_vision_readiness(provider)
    assert info["ready"] is True
    assert info["vision_supported"] is expected_vision


def test_f1_cache_per_asset_hash_429_retry_and_needs_review_preserves_asset(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.config.config.app", dict(config.app))
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
    assert recorded_sdk_calls[-1]["extra_body"] == {"reasoning": {"effort": "minimal", "exclude": True}}

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
    assert calls[0]["extra_body"] == {"reasoning": {"effort": "minimal", "exclude": True}}
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
    assert vis_calls[0]["extra_body"] == {"reasoning": {"effort": "minimal", "exclude": True}}

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


