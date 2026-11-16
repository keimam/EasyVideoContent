"""Tests for Stage 8B.1: Long Video AI Providers Unified with MoneyPrinterTurbo (LLM, TTS, AI Image)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from app.config import config
from app.services.longform import llm_adapter
from app.services.longform.llm_adapter import (
    LLMAdapter,
    LLMModelNotFoundError,
    LLMRateLimitError,
    get_llm_readiness,
    list_llm_models,
    set_active_llm_model,
)
from app.services.longform.schema import (
    AudioSpec,
    Chapter,
    ItemStatus,
    ScriptOutline,
    OutlineChapter,
    Segment,
    Shot,
    TtsGroup,
)
from app.services.longform.script_generator import ScriptGenerator
from app.services.longform.store import create_project, load_manifest, save_manifest
from app.services.longform.tts_adapter import (
    TTSAdapter,
    list_configured_tts_providers,
    provider_has_word_timings,
)
from app.services.longform.ui import jobs
from app.services.longform.ui.components import check_has_llm_key
from app.services.longform.ui.security import check_llm_status
from app.services.longform.visual.ai_image import (
    AIImageProvider,
    estimate_ai_image_usage,
    get_ai_image_readiness,
)
from app.services.longform.voice_service import VoiceService, list_available_voices


def _get_long_video_app_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    return str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")


# ---------------------------------------------------------------------------
# P1: Unified LLM Readiness, Model Catalog, & Resilience
# ---------------------------------------------------------------------------


def test_p1_llm_readiness_openrouter_openai_gemini_and_ollama(monkeypatch):
    """Non-Gemini MPT providers pass readiness without triggering Gemini AIza check."""
    # OpenRouter
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-test1234567890")
    monkeypatch.setitem(config.app, "openrouter_model_name", "openrouter/free")
    r_or = get_llm_readiness()
    assert r_or["configured"] is True
    assert r_or["is_invalid"] is False
    assert r_or["model"] == "openrouter/free"
    assert check_has_llm_key() is True
    assert check_llm_status()["configured"] is True

    # OpenAI
    monkeypatch.setitem(config.app, "llm_provider", "openai")
    monkeypatch.setitem(config.app, "openai_api_key", "sk-live-test1234567890")
    monkeypatch.setitem(config.app, "openai_model_name", "gpt-4o-mini")
    r_oa = get_llm_readiness()
    assert r_oa["configured"] is True
    assert r_oa["is_invalid"] is False
    assert check_has_llm_key() is True

    # Gemini still validates AIza format ONLY when Gemini is active
    monkeypatch.setitem(config.app, "llm_provider", "gemini")
    monkeypatch.setitem(config.app, "gemini_api_key", "bad_gemini_key_1234")
    r_gem_bad = get_llm_readiness()
    assert r_gem_bad["configured"] is False
    assert r_gem_bad["is_invalid"] is True
    assert check_has_llm_key() is False

    monkeypatch.setitem(config.app, "gemini_api_key", "AIzaSyValidKeyForTest1234")
    r_gem_ok = get_llm_readiness()
    assert r_gem_ok["configured"] is True
    assert r_gem_ok["is_invalid"] is False
    assert check_has_llm_key() is True

    # Ollama (keyless local provider)
    monkeypatch.setitem(config.app, "llm_provider", "ollama")
    monkeypatch.setitem(config.app, "ollama_model_name", "qwen3:8b")
    r_ol = get_llm_readiness()
    assert r_ol["configured"] is True
    assert check_has_llm_key() is True


def test_p1_llm_test_connection_and_model_discovery(monkeypatch):
    """Test connection delegates to MPT and /models discovery handles catalogs & fallback."""
    monkeypatch.setattr(
        "app.services.llm.test_connection",
        lambda: (True, "Connected to openrouter", 0.42),
    )
    ok, msg, lat = llm_adapter.test_llm_connection()
    assert ok is True
    assert "openrouter" in msg
    assert lat == pytest.approx(0.42)

    # OpenRouter /models listing
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-test1234567890")
    monkeypatch.setitem(config.app, "openrouter_base_url", "https://openrouter.ai/api/v1")

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "data": [{"id": "openrouter/free"}, {"id": "google/gemini-2.5-flash"}]
    }
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: mock_resp)
    models, err = list_llm_models("openrouter")
    assert err is None
    assert models == ["google/gemini-2.5-flash", "openrouter/free"]

    # Network failure falls back gracefully with sanitized error
    def _fail_get(*args, **kwargs):
        raise RuntimeError("api_key=sk-secret-12345678 network unreachable")

    monkeypatch.setattr("requests.get", _fail_get)
    models_err, err_msg = list_llm_models("openrouter")
    assert models_err == []
    assert err_msg is not None
    assert "sk-secret-12345678" not in err_msg

    # Codex catalog reuse
    monkeypatch.setattr(
        "app.services.codex_models.get_catalog",
        lambda force_refresh=False: SimpleNamespace(models=["gpt-5-codex", "gpt-5.1-codex"]),
    )
    codex_models, codex_err = list_llm_models("openai_codex")
    assert codex_err is None
    assert "gpt-5-codex" in codex_models

    # Saving model writes directly to MPT config.app
    saved_calls = []
    monkeypatch.setattr(config, "save_config", lambda: saved_calls.append(True))
    set_active_llm_model("google/gemini-2.5-flash", "openrouter")
    assert config.app["openrouter_model_name"] == "google/gemini-2.5-flash"
    assert len(saved_calls) == 1


def test_p1_llm_rate_limit_retry_and_model_not_found(tmp_path: Path, monkeypatch):
    """429 retries up to max_retries then succeeds/raises LLMRateLimitError; 404 model fails fast."""
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-test1234567890")
    monkeypatch.setattr("time.sleep", lambda _sec: None)

    # 1. Transient 429 succeeds on 3rd attempt
    calls = {"n": 0}

    def _flaky_429(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return "Error: HTTP 429 Too Many Requests / rate limit exceeded"
        return "Success response"

    monkeypatch.setattr("app.services.llm._generate_response", _flaky_429)
    adapter = LLMAdapter(provider="openrouter", model="openrouter/free", cache_dir=tmp_path / "c1", max_retries=3, retry_delay=0.2)
    resp = adapter.generate("Hello", force_refresh=True)
    assert resp.content == "Success response"
    assert calls["n"] == 3

    # 2. Persistent 429 raises LLMRateLimitError
    monkeypatch.setattr("app.services.llm._generate_response", lambda *a, **kw: "Error: 429 rate_limit reached")
    adapter_rl = LLMAdapter(provider="openrouter", model="openrouter/free", cache_dir=tmp_path / "c2", max_retries=3, retry_delay=0.2)
    with pytest.raises(LLMRateLimitError):
        adapter_rl.generate("Hello", force_refresh=True)

    # 3. Missing/retired model fails immediately without retry loops
    miss_calls = {"n": 0}

    def _missing_model(*args, **kwargs):
        miss_calls["n"] += 1
        return "Error: The model `old-retired-model` does not exist or has been retired."

    monkeypatch.setattr("app.services.llm._generate_response", _missing_model)
    adapter_missing = LLMAdapter(provider="openrouter", model="old-retired-model", cache_dir=tmp_path / "c3", max_retries=3)
    with pytest.raises(LLMModelNotFoundError) as exc_info:
        adapter_missing.generate("Hello", force_refresh=True)
    assert exc_info.value.model == "old-retired-model"
    assert miss_calls["n"] == 1


def test_p1_script_resume_preserves_completed_chapters_on_model_switch(tmp_path: Path, monkeypatch):
    """Completed chapters stay intact after mid-run LLM failure and Resume only writes remaining ones."""
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-test1234567890")
    monkeypatch.setitem(config.app, "openrouter_model_name", "model-alpha")

    _, p_dir = create_project(
        project_id="resume_8b1",
        title="Resume Model Switch",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )

    outline = ScriptOutline(
        topic="Classic V8 Engines",
        target_duration_minutes=6.0,
        words_per_minute=145,
        total_target_words=300,
        opening_hook="An era of iron and thunder.",
        candidate_titles=["Classic V8 Engines", "The Roar of Detroit V8s", "Inside America's V8 Revolution"],
        chapters=[
            OutlineChapter(chapter_index=1, title="Birth of the V8", target_words=150, purpose="Origins"),
            OutlineChapter(chapter_index=2, title="Muscle Era Peak", target_words=150, purpose="Golden years"),
        ],
    )
    (p_dir / "script").mkdir(parents=True, exist_ok=True)
    (p_dir / "script" / "outline.json").write_text(outline.model_dump_json(indent=2), encoding="utf-8")

    # Stub LLM: succeed Ch 1 + summary, fail Ch 2 with retired model
    prompts_seen = []

    def _fail_ch2(prompt, **kwargs):
        prompts_seen.append(prompt)
        if "narrative_summary" in prompt:
            return json.dumps({"narrative_summary": "Ch 1 origins.", "key_facts": ["1964 debut"]})
        if "Chapter Index: 2" in prompt:
            return "Error: model `model-alpha` does not exist"
        return "## Birth of the V8\n\n" + ("Detroit built iconic V8 engines with thunderous torque. " * 12)

    monkeypatch.setattr("app.services.llm._generate_response", _fail_ch2)

    gen1 = ScriptGenerator(project_dir=p_dir, provider="openrouter", model="model-alpha")
    with pytest.raises(LLMModelNotFoundError):
        gen1.write_chapters()

    # Verify Ch 1 was written and saved cleanly
    assert (p_dir / "script" / "chapter_01.md").is_file()
    assert not (p_dir / "script" / "chapter_02.md").is_file()
    status_mid = gen1.get_status()
    assert status_mid["chapters"][0]["status"] == "completed"
    assert status_mid["chapters"][1]["status"] == "pending"

    # Switch to a working model and Resume -> Ch 1 MUST NOT be re-generated!
    resume_prompts = []

    def _succeed_ch2(prompt, **kwargs):
        resume_prompts.append(prompt)
        if "narrative_summary" in prompt:
            return json.dumps({"narrative_summary": "Ch 2 peak.", "key_facts": ["7000 RPM"]})
        if "Chapter Index: 1" in prompt:
            raise AssertionError("Chapter 1 should have been preserved, not regenerated!")
        return "## Muscle Era Peak\n\n" + ("Factory racers dominated drag strips nationwide. " * 12)

    monkeypatch.setattr("app.services.llm._generate_response", _succeed_ch2)
    monkeypatch.setitem(config.app, "openrouter_model_name", "model-beta")

    gen2 = ScriptGenerator(project_dir=p_dir, provider="openrouter", model="model-beta")
    full_path = gen2.write_chapters()
    assert full_path.is_file()
    assert not any("Chapter Index: 1" in p for p in resume_prompts)


# ---------------------------------------------------------------------------
# P2: Unified TTS Providers, Whisper Fallback, & Stale Hash
# ---------------------------------------------------------------------------


def test_p2_tts_configured_providers_and_voice_listing(monkeypatch):
    """Edge is always present; configured MPT TTS providers appear dynamically."""
    monkeypatch.setitem(config.app, "gemini_api_key", "")
    monkeypatch.setitem(config.app, "mimo_api_key", "")
    if isinstance(getattr(config, "azure", None), dict):
        monkeypatch.setitem(config.azure, "speech_key", "")
        monkeypatch.setitem(config.azure, "speech_region", "")

    base_ids = [p["id"] for p in list_configured_tts_providers()]
    assert "edge" in base_ids
    assert provider_has_word_timings("edge") is True
    assert provider_has_word_timings("azure-tts-v2") is True
    assert provider_has_word_timings("gemini-tts") is False

    # Enable Gemini TTS
    monkeypatch.setitem(config.app, "gemini_api_key", "AIzaSyTest12345678")
    ids_with_gem = [p["id"] for p in list_configured_tts_providers()]
    assert "gemini-tts" in ids_with_gem
    gem_voices = list_available_voices(provider="gemini-tts")
    assert any(v.startswith("gemini:") for v in gem_voices)


def test_p2_tts_whisper_fallback_for_non_word_cue_provider(tmp_path: Path, monkeypatch):
    """Providers without word boundaries invoke align_group_segments(..., allow_whisper=True)."""
    monkeypatch.setitem(config.app, "gemini_api_key", "AIzaSyTest12345678")

    class _FakeLegacySubMaker:
        # Upstream non-Edge providers set subs/offset heuristically, NOT cues
        cues = []
        subs = ["Hello world."]
        offset = [(0, 20_000_000)]

    def _fake_upstream_tts(text, voice_name, voice_rate, voice_file, voice_volume=1.0):
        Path(voice_file).write_bytes(b"FAKE_MP3_BYTES" * 20)
        return _FakeLegacySubMaker()

    monkeypatch.setattr("app.services.voice.tts", _fake_upstream_tts)
    monkeypatch.setattr(
        "app.services.longform.tts_adapter.convert_to_pcm_wav",
        lambda input_audio_path, output_wav_path, **kw: Path(output_wav_path).write_bytes(b"RIFF" + b"\x00" * 100),
    )
    monkeypatch.setattr(
        "app.services.longform.tts_adapter.measure_wav_duration_and_samples",
        lambda wav: (2.0, 96000, 48000),
    )

    captured_kwargs = {}

    def _spy_align(segments, group_duration, word_cues, wav_path=None, word_cue_threshold=0.85, allow_whisper=False):
        captured_kwargs["allow_whisper"] = allow_whisper
        captured_kwargs["cues_count"] = len(word_cues)
        seg = segments[0].model_copy(update={"start_offset_seconds": 0.0, "end_offset_seconds": 2.0, "duration_seconds": 2.0})
        return SimpleNamespace(
            method="whisper",
            segments=[seg],
            match_ratio=1.0,
            fallback_reason="No word cues; aligned with Whisper",
            word_timings=[{"word": "Hello", "start": 0.0, "end": 0.5}],
        )

    monkeypatch.setattr("app.services.longform.tts_adapter.align_group_segments", _spy_align)

    adapter = TTSAdapter(provider="gemini-tts", voice_name="gemini:Zephyr-Bright", output_dir=tmp_path)
    grp = TtsGroup(
        group_index=1,
        paragraph_text="Hello world.",
        segments=[Segment(segment_index=1, narration_text="Hello world.")],
    )
    res = adapter.synthesize_group(grp, chapter_index=1)
    assert captured_kwargs["cues_count"] == 0
    assert captured_kwargs["allow_whisper"] is True
    assert res.provider == "gemini-tts"
    assert res.alignment_method == "whisper"


def test_p2_tts_azure_v2_extracts_word_timings_without_whisper(tmp_path: Path, monkeypatch):
    """azure-tts-v2 extracts .subs/.offset word boundaries cleanly without Whisper."""
    monkeypatch.setitem(config.azure, "speech_key", "az-key-1234")
    monkeypatch.setitem(config.azure, "speech_region", "eastus")

    class _FakeAzureV2SubMaker:
        cues = []
        subs = ["Hello", "world"]
        offset = [(0, 4_000_000), (4_500_000, 10_000_000)]  # 100ns ticks

    monkeypatch.setattr(
        "app.services.voice.tts",
        lambda **kw: Path(kw["voice_file"]).write_bytes(b"MP3" * 20) and _FakeAzureV2SubMaker() or _FakeAzureV2SubMaker(),
    )
    monkeypatch.setattr(
        "app.services.longform.tts_adapter.convert_to_pcm_wav",
        lambda input_audio_path, output_wav_path, **kw: Path(output_wav_path).write_bytes(b"RIFF" + b"\x00" * 100),
    )
    monkeypatch.setattr(
        "app.services.longform.tts_adapter.measure_wav_duration_and_samples",
        lambda wav: (1.5, 72000, 48000),
    )

    captured = {}

    def _spy_align(segments, group_duration, word_cues, wav_path=None, word_cue_threshold=0.85, allow_whisper=False):
        captured["cues"] = [(c.word, round(c.start, 2), round(c.end, 2)) for c in word_cues]
        captured["allow_whisper"] = allow_whisper
        seg = segments[0].model_copy(update={"start_offset_seconds": 0.0, "end_offset_seconds": 1.5, "duration_seconds": 1.5})
        return SimpleNamespace(method="word_cues", segments=[seg], match_ratio=1.0, fallback_reason=None, word_timings=[])

    monkeypatch.setattr("app.services.longform.tts_adapter.align_group_segments", _spy_align)

    adapter = TTSAdapter(provider="azure-tts-v2", voice_name="en-US-AndrewNeural-Male", output_dir=tmp_path)
    grp = TtsGroup(
        group_index=1,
        paragraph_text="Hello world",
        segments=[Segment(segment_index=1, narration_text="Hello world")],
    )
    res = adapter.synthesize_group(grp, chapter_index=1)
    assert captured["cues"] == [("Hello", 0.0, 0.4), ("world", 0.45, 1.0)]
    assert captured["allow_whisper"] is False
    assert res.provider == "azure-tts-v2"


def test_p2_voice_service_provider_change_marks_groups_stale(tmp_path: Path):
    """Changing TTS provider invalidates audio_hash and marks groups stale, preserving A->B->A."""
    manifest, p_dir = create_project(
        project_id="voice_hash_8b1",
        title="Voice Hash Test",
        preset_id="automotive.muscle_car",
        projects_root=tmp_path,
    )
    # Synthesize with fake, then simulate completed group on disk
    vs = VoiceService(p_dir)
    m = load_manifest(p_dir)
    wav_rel = "assets/audio/groups/ch1_g1.wav"
    wav_path = p_dir / wav_rel
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    wav_path.write_bytes(b"RIFF" + b"\x00" * 100)

    from app.services.longform.hashing import compute_audio_group_hash

    edge_hash = compute_audio_group_hash(
        paragraph_text="Sample narration.",
        tts_provider="edge",
        voice_name="en-US-ChristopherNeural-Male",
        voice_rate=1.0,
    )
    m.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Ch 1",
            status=ItemStatus.completed,
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="Sample narration.",
                    status=ItemStatus.completed,
                    audio=AudioSpec(
                        file_path=wav_rel,
                        tts_provider="edge",
                        voice_name="en-US-ChristopherNeural-Male",
                        voice_rate=1.0,
                        audio_hash=edge_hash,
                    ),
                )
            ],
        )
    ]
    save_manifest(m, p_dir)

    # Switch to gemini-tts -> marks group stale
    m_after_gem = vs.set_voice("gemini:Zephyr-Bright", 1.0, tts_provider="gemini-tts")
    assert m_after_gem.preset_snapshot["tts_provider"] == "gemini-tts"
    assert m_after_gem.chapters[0].tts_groups[0].status == ItemStatus.stale

    # Switch back to original edge + voice -> hash matches disk again -> restored completed!
    m_restored = vs.set_voice("en-US-ChristopherNeural-Male", 1.0, tts_provider="edge")
    assert m_restored.chapters[0].tts_groups[0].status == ItemStatus.completed


# ---------------------------------------------------------------------------
# P3: Unified AI Image Provider (OpenAI Images & Codex Image)
# ---------------------------------------------------------------------------


def test_p3_ai_image_readiness_codex_and_openai_routing(tmp_path: Path, monkeypatch):
    """AIImageProvider supports both OpenAI Images and Codex Image, plus accurate count estimate."""
    monkeypatch.setitem(config.app, "openai_image_base_url", "")
    monkeypatch.setitem(config.app, "openai_image_model", "")
    monkeypatch.setitem(config.app, "codex_image_model_name", "")
    monkeypatch.setattr("app.services.codex_oauth.account_status", lambda: False)

    assert get_ai_image_readiness()["ready"] is False

    # 1. Codex Image ready when OAuth connected + model configured
    monkeypatch.setattr("app.services.codex_oauth.account_status", lambda: True)
    monkeypatch.setitem(config.app, "codex_image_model_name", "gpt-5-codex")
    r_codex = get_ai_image_readiness()
    assert r_codex["ready"] is True
    assert r_codex["backend"] == "codex_image"
    assert r_codex["backend_label"] == "Codex Image"

    # Mock generate_images_codex
    fake_img = tmp_path / "codex_out.png"
    fake_img.write_bytes(b"\x89PNG\r\n\x1a\nFAKE_PNG")
    monkeypatch.setattr(
        "app.services.longform.visual.ai_image.generate_images_codex",
        lambda **kw: [SimpleNamespace(url=str(fake_img), source_info={"rendition": {"width": 1536, "height": 1024}})],
    )
    ai_prov = AIImageProvider()
    cands = ai_prov.search_candidates("1969 Camaro on highway", target_duration=4.0)
    assert cands[0].provider == "codex_image"
    asset = ai_prov.fetch_asset(cands[0], tmp_path / "dest_codex.png")
    assert asset.provider == "codex_image"
    assert asset.local_path.is_file()

    # 2. Estimator count matches pending shots
    manifest = SimpleNamespace(
        chapters=[
            Chapter(
                chapter_index=1,
                chapter_title="Ch 1",
                shots=[
                    Shot(shot_id="s1", chapter_index=1, shot_index=1, duration_seconds=4.0, target_frames=100),
                    Shot(shot_id="s2", chapter_index=1, shot_index=2, duration_seconds=4.0, target_frames=100),
                ],
            )
        ]
    )
    est = estimate_ai_image_usage(manifest, tmp_path)
    assert est["estimated_count"] == 2
    assert est["has_unit_price"] is False


# ---------------------------------------------------------------------------
# P4: AppTest Bilingual UI Verification (id & en, zero mixed strings)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ui_lang", ["id", "en"])
@pytest.mark.parametrize("prov_id,key_field,key_val,model_field,model_val", [
    ("openrouter", "openrouter_api_key", "sk-or-v1-test1234567890", "openrouter_model_name", "openrouter/free"),
    ("openai", "openai_api_key", "sk-proj-test1234567890", "openai_model_name", "gpt-4o-mini"),
    ("gemini", "gemini_api_key", "AIzaSyValidTestKey1234", "gemini_model_name", "gemini-2.5-flash"),
])
def test_p4_apptest_script_card_bilingual_providers(
    tmp_path: Path, monkeypatch, ui_lang, prov_id, key_field, key_val, model_field, model_val
):
    """Script AI card displays active MPT provider & model cleanly in both ID & EN without Gemini bias."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", ui_lang)
    monkeypatch.setitem(config.app, "llm_provider", prov_id)
    monkeypatch.setitem(config.app, key_field, key_val)
    monkeypatch.setitem(config.app, model_field, model_val)

    pid = f"ui_llm_{prov_id}_{ui_lang}"
    create_project(pid, f"Test {prov_id}", "automotive.muscle_car", projects_root=tmp_path)

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.run(timeout=20)

    assert not at.exception, f"AppTest exception ({ui_lang}/{prov_id}): {[e.value for e in at.exception]}"
    md_text = "\n".join(m.value for m in at.markdown)
    btn_labels = [b.label for b in at.button]

    # Model ID displayed on Script AI card
    assert model_val in md_text, f"Expected model {model_val} in Script AI card markdown"
    assert not any("AIza" in (w.value or "") for w in at.warning), "Valid key should not warn about Gemini AIza"

    if ui_lang == "id":
        assert "Tes koneksi" in btn_labels
        assert "Ganti penyedia/model" in btn_labels
        assert "Penyedia aktif:" in md_text
        assert "Active provider:" not in md_text
    else:
        assert "Test connection" in btn_labels
        assert "Change provider/model" in btn_labels
        assert "Active provider:" in md_text
        assert "Penyedia aktif:" not in md_text


@pytest.mark.parametrize("ui_lang", ["id", "en"])
def test_p4_apptest_visual_ai_image_checkbox_and_estimate(tmp_path: Path, monkeypatch, ui_lang):
    """Visual step unlocks Gambar AI checkbox when MPT AI Image is ready and shows count/cost estimate."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", ui_lang)
    monkeypatch.setitem(config.app, "openai_image_base_url", "https://api.openai.com/v1")
    monkeypatch.setitem(config.app, "openai_image_model", "gpt-image-1")
    monkeypatch.setitem(config.app, "openai_image_api_keys", ["sk-img-test-1234"])

    pid = f"ui_vis_ai_{ui_lang}"
    _, p_dir = create_project(pid, "AI Visual UI Test", "automotive.muscle_car", projects_root=tmp_path)
    m = load_manifest(p_dir)
    wav_rel = "assets/audio/groups/ch1_g1.wav"
    (p_dir / wav_rel).parent.mkdir(parents=True, exist_ok=True)
    (p_dir / wav_rel).write_bytes(b"RIFF" + b"\x00" * 100)
    m.chapters = [
        Chapter(
            chapter_index=1,
            chapter_title="Ch 1",
            status=ItemStatus.completed,
            tts_groups=[
                TtsGroup(
                    group_index=1,
                    paragraph_text="One shot.",
                    status=ItemStatus.completed,
                    audio=AudioSpec(file_path=wav_rel, duration_seconds=5.0),
                    segments=[Segment(segment_index=1, narration_text="One shot.")],
                )
            ],
            shots=[Shot(shot_id="ch01_shot001", chapter_index=1, shot_index=1, duration_seconds=5.0, target_frames=125)],
        )
    ]
    save_manifest(m, p_dir)

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.session_state[f"active_step_{pid}"] = "visual"
    at.run(timeout=20)

    assert not at.exception
    ai_cbs = [cb for cb in at.checkbox if "OpenAI Images" in (cb.label or "")]
    assert len(ai_cbs) == 1, f"Expected enabled AI Images checkbox, got {[c.label for c in at.checkbox]}"
    assert ai_cbs[0].disabled is False

    # Check the AI Images checkbox -> triggers cost & count estimate banner
    ai_cbs[0].check().run(timeout=20)
    info_msgs = " ".join(i.value for i in at.info)
    assert "OpenAI Images" in info_msgs
    if ui_lang == "id":
        assert "Perkiraan Gambar AI" in info_msgs
    else:
        assert "AI Images estimate" in info_msgs


@pytest.mark.parametrize("ui_lang", ["id", "en"])
def test_p4_apptest_voice_step_providers_and_whisper_note(tmp_path: Path, monkeypatch, ui_lang):
    """Voice step renders configured MPT TTS providers and Whisper note for non-word-cue services."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setitem(config.ui, "language", ui_lang)
    monkeypatch.setitem(config.app, "gemini_api_key", "AIzaSyValidTestKey1234")

    pid = f"ui_voice_prov_{ui_lang}"
    _, p_dir = create_project(pid, "Voice Provider UI", "automotive.muscle_car", projects_root=tmp_path)
    m = load_manifest(p_dir)
    m.preset_snapshot["tts_provider"] = "gemini-tts"
    m.preset_snapshot["default_voice"] = "gemini:Zephyr-Bright"
    save_manifest(m, p_dir)

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.session_state[f"active_step_{pid}"] = "voice"
    at.run(timeout=20)

    assert not at.exception
    info_msgs = " ".join(i.value for i in at.info)
    tts_sels = [s for s in at.selectbox if s.key == f"tts_prov_sel_{pid}"]
    assert len(tts_sels) == 1
    assert "Google Gemini TTS" in tts_sels[0].options
    if ui_lang == "id":
        assert "Whisper" in info_msgs
        assert "lebih lama" in info_msgs
    else:
        assert "Whisper" in info_msgs
        assert "takes longer" in info_msgs


@pytest.mark.parametrize("ui_lang", ["id", "en"])
def test_p4_apptest_failed_job_model_not_found_banner(tmp_path: Path, monkeypatch, ui_lang):
    """Failed LLM job with [MODEL_NOT_FOUND] displays localized message + Change provider button."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.jobs.get_project_dir", lambda pid: tmp_path / pid)
    monkeypatch.setitem(config.ui, "language", ui_lang)

    pid = f"ui_fail_model_{ui_lang}"
    _, p_dir = create_project(pid, "Model Error UI", "automotive.muscle_car", projects_root=tmp_path)
    (p_dir / "script").mkdir(parents=True, exist_ok=True)
    (p_dir / "script" / "chapter_01.md").write_text("## Bab 1\n\nTeks selesai.", encoding="utf-8")

    (p_dir / "job_state.json").write_text(
        json.dumps(
            {
                "project_id": pid,
                "step": "script_generate",
                "status": "failed",
                "failed_step": "script_generate",
                "error_message": "[MODEL_NOT_FOUND] Model=old-gpt-3 | model does not exist",
            }
        ),
        encoding="utf-8",
    )

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.run(timeout=20)

    assert not at.exception
    err_msgs = " ".join(e.value for e in at.error)
    info_msgs = " ".join(i.value for i in at.info)
    btn_labels = [b.label for b in at.button]
    assert "old-gpt-3" in err_msgs
    if ui_lang == "id":
        assert "tidak tersedia, pilih model lain" in err_msgs
        assert "Ganti penyedia/model" in btn_labels
        assert "tetap aman tersimpan" in info_msgs
    else:
        assert "is unavailable" in err_msgs
        assert "Change provider/model" in btn_labels
        assert "preserved" in info_msgs


# ---------------------------------------------------------------------------
# B1 Addendum: Canonical Job Command Builder, Pre-Popen Validation & Live Fake Job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cli_args", [
    ("script", "outline", "demo_pid", "--topic", "Test", "--write"),
    ("script", "write", "demo_pid"),
    ("audio", "synth", "demo_pid"),
    ("visual", "pipeline", "demo_pid", "--allow-fallback"),
    ("render", "all", "demo_pid"),
    ("capcut", "export", "demo_pid", "--install"),
    ("pipeline", "auto", "demo_pid"),
])
def test_b1_all_job_commands_use_canonical_builder_and_valid_paths(cli_args):
    """Every job type uses active venv Python and an existing absolute cli_longform.py path."""
    import sys

    cmd = jobs.build_longform_cli_command(*cli_args)
    assert cmd[0] == sys.executable
    assert Path(cmd[0]).is_file(), f"Interpreter missing: {cmd[0]}"
    assert Path(cmd[1]).is_absolute(), f"CLI path must be absolute: {cmd[1]}"
    assert Path(cmd[1]).name == "cli_longform.py"
    assert Path(cmd[1]).is_file(), f"CLI script missing: {cmd[1]}"
    assert tuple(cmd[2:]) == cli_args


def test_b1_start_job_validates_missing_executable_and_script_before_popen(tmp_path: Path, monkeypatch):
    """Missing executable or CLI script raises JobLaunchError cleanly before Popen, and UI catches it safely."""
    import shutil
    from app.services.longform.ui.jobs import JobLaunchError

    pid = f"b1_validate_{tmp_path.name}"
    _, p_dir = create_project(pid, "Validate Launch", "automotive.muscle_car")
    try:
        popen_called = []
        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: popen_called.append(True))

        # 1. Missing executable (e.g. bare 'script' subcommand without python)
        with pytest.raises(JobLaunchError, match="not found"):
            jobs.start_job(pid, "script_outline", ["nonexistent_executable_b1", "cli_longform.py"])
        assert popen_called == []

        # 2. Missing CLI script
        import sys
        with pytest.raises(JobLaunchError, match="CLI script not found"):
            jobs.start_job(pid, "script_outline", [sys.executable, "nonexistent_cli_script_b1.py"])
        assert popen_called == []

        # 3. _start_job_safe handles failure with friendly st.error + Detail expander (no page crash)
        from app.services.longform.ui import components

        errors_shown = []
        expanders_shown = []
        monkeypatch.setattr("streamlit.error", lambda msg: errors_shown.append(msg))
        monkeypatch.setattr("streamlit.code", lambda txt, language="text": expanders_shown.append(txt))

        class _DummyCtx:
            def __enter__(self): return self
            def __exit__(self, *a): pass

        monkeypatch.setattr("streamlit.expander", lambda *a, **kw: _DummyCtx())
        ok = components._start_job_safe(pid, "script_outline", ["nonexistent_executable_b1"])
        assert ok is False
        assert len(errors_shown) == 1
        assert any("nonexistent_executable_b1" in x for x in expanders_shown)
    finally:
        shutil.rmtree(p_dir, ignore_errors=True)


def test_b1_connection_test_no_trailing_colon_when_empty(tmp_path: Path, monkeypatch):
    """Empty connection test reply shows 'Koneksi berhasil (2.30 dtk)' without trailing colon."""
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.test_llm_connection", lambda: (True, "", 2.30))
    monkeypatch.setitem(config.ui, "language", "id")
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-test1234567890")
    monkeypatch.setitem(config.app, "openrouter_model_name", "openrouter/free")

    pid = "b1_conn_colon"
    create_project(pid, "Colon Test", "automotive.muscle_car", projects_root=tmp_path)

    at = AppTest.from_file(_get_long_video_app_file())
    at.query_params["project"] = pid
    at.run(timeout=20)
    assert not at.exception

    btn_test = [b for b in at.button if b.key == f"btn_test_llm_{pid}"][0]
    btn_test.click().run(timeout=20)
    assert not at.exception
    succ_msgs = [s.value for s in at.success]
    assert any("Koneksi berhasil (2.30 dtk)" in m for m in succ_msgs)
    assert not any(m.strip().endswith(":") for m in succ_msgs)


def test_b1_end_to_end_fake_script_background_job():
    """Launch real background script outline --write job with fake provider and poll to completion."""
    import shutil
    import time

    pid = "b1_e2e_fake_script_job"
    p_dir = jobs.get_project_dir(pid)
    if p_dir.exists():
        shutil.rmtree(p_dir, ignore_errors=True)

    try:
        create_project(pid, "E2E Background Script Test", "automotive.muscle_car")
        cmd = jobs.build_longform_cli_command(
            "script", "outline", pid, "--topic", "Classic Muscle Cars", "--minutes", "6", "--write", "--provider", "fake"
        )
        state = jobs.start_job(pid, "script_outline", cmd)
        assert state["status"] == "running"

        t0 = time.time()
        cur = jobs.get_job_state(pid)
        while cur and cur.get("status") == "running" and (time.time() - t0) < 30.0:
            time.sleep(0.25)
            cur = jobs.get_job_state(pid)

        assert cur is not None
        assert cur["status"] == "completed", f"Job failed: {cur}, logs={jobs.get_job_logs(pid, 30)}"
        assert cur["exit_code"] == 0
        assert (p_dir / "script" / "outline.json").is_file()
        assert (p_dir / "script" / "full_script.md").is_file()
        assert "[FAKE]" in (p_dir / "script" / "full_script.md").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(p_dir, ignore_errors=True)


