"""Tests for ADDENDUM C8 (C8.1–C8.4): In-app Help viewer, clear AI errors, OpenRouter quota/reasoning control, and request savings."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import config


def _seed_project(root: Path, proj_id: str, lang: str = "id-ID"):
    from app.services.longform.store import create_project

    preset_id = "documentary_id" if lang.lower().startswith("id") else "documentary_en"
    manifest, proj_dir = create_project(
        project_id=proj_id,
        title="Tes C8 Proyek",
        preset_id=preset_id,
        language=lang,
        projects_root=root,
    )
    return manifest, proj_dir


def test_c8_1_help_viewer_renders_markdown_without_relative_md_links(tmp_path: Path, monkeypatch):
    """C8.1: Help viewer renders actual file markdown, handles images, and removes broken relative .md links."""
    from streamlit.testing.v1 import AppTest
    from app.services.branding.help_viewer import get_help_documents, prepare_help_markdown

    repo_root = Path(__file__).resolve().parents[2]

    # 1. README image & relative link processing
    readme_raw = (repo_root / "README.md").read_text(encoding="utf-8")
    blocks, related = prepare_help_markdown("README.md", readme_raw)
    md_text = "\n".join(val for kind, val, _ in blocks if kind == "markdown")
    assert "EasyFaceless Studio" in md_text
    # No unrendered relative .md links should remain in markdown output
    assert not re.search(r"\]\((?!https?://)[^)]+\.md(?:#[^)]*)?\)", md_text)
    assert any(kind == "image" and Path(val).is_file() for kind, val, _ in blocks)
    assert any(rel.endswith("README.md") for rel, _ in related)

    # Docs catalog contains both id & en guides + README + THIRD_PARTY_NOTICES + LICENSE
    id_docs = dict((r, l) for r, l, _ in get_help_documents("id"))
    en_docs = dict((r, l) for r, l, _ in get_help_documents("en"))
    assert "docs/user/id/README.md" in id_docs and "LICENSE" in id_docs and "THIRD_PARTY_NOTICES.md" in id_docs
    assert "docs/user/en/README.md" in en_docs and "LICENSE" in en_docs and "THIRD_PARTY_NOTICES.md" in en_docs

    # 2. Streamlit AppTest renders Help dialog in ID & EN with real document contents
    lv_file = str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    for lang_code, expected_heading in (
        ("id", "Panduan Pengguna EasyFaceless Studio"),
        ("en", "EasyFaceless Studio User Guide"),
    ):
        monkeypatch.setitem(config.ui, "language", lang_code)
        at = AppTest.from_file(lv_file)
        at.session_state["ui_language"] = lang_code
        at.query_params["lang"] = lang_code
        at.run(timeout=20)
        assert not at.exception
        at.button(key="efs_btn_help_longform").click().run(timeout=20)
        assert not at.exception
        dlg_md = "\n".join(str(m.value) for m in at.markdown)
        assert expected_heading in dlg_md
        # Zero broken relative .md markdown links rendered inside the viewer
        assert not re.search(r"\]\((?!https?://)[^)]+\.md(?:#[^)]*)?\)", dlg_md)


def test_c8_2_errors_429_and_openrouter_quota(tmp_path: Path, monkeypatch):
    """C8.2: Differentiate 401 / daily 429 / RPM 429, query /key quota safely, and avoid bare Exit Code: 1."""
    from types import SimpleNamespace as NS
    from app.services.longform.llm_adapter import (
        LLMAdapter,
        LLMAuthError,
        LLMDailyQuotaError,
        LLMRateLimitError,
        fetch_openrouter_key_quota,
    )
    from app.services.longform.ui import components, jobs

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    fake_secret = "sk-or-v1-supersecretkeyvalue9988"
    monkeypatch.setitem(config.app, "openrouter_api_key", fake_secret)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    # 1. 401 Invalid key fails fast with ZERO retries
    calls = {"n": 0}

    def _err_401(*_a, **_kw):
        calls["n"] += 1
        return "Error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}"

    monkeypatch.setattr("app.services.llm._generate_response", _err_401)
    ad = LLMAdapter(provider="openrouter", model="openrouter/free", cache_dir=tmp_path / "c1", max_retries=3)
    with pytest.raises(LLMAuthError):
        ad.generate("hi", force_refresh=True)
    assert calls["n"] == 1

    # 2. 429 Daily quota stops IMMEDIATELY (0 retries!) with LLMDailyQuotaError
    daily_calls = {"n": 0}

    def _err_daily_429(*_a, **_kw):
        daily_calls["n"] += 1
        return "Error: HTTP 429 Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day"

    monkeypatch.setattr("app.services.llm._generate_response", _err_daily_429)
    ad_daily = LLMAdapter(provider="openrouter", model="openrouter/free", cache_dir=tmp_path / "c2", max_retries=3)
    with pytest.raises(LLMDailyQuotaError) as d_exc:
        ad_daily.generate("hi", force_refresh=True)
    assert d_exc.value.is_daily is True
    assert daily_calls["n"] == 1

    # 3. 429 Per-minute reads Retry-After header, sleeps, and succeeds
    sleep_waits: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_waits.append(float(s)))
    monkeypatch.setattr("app.services.llm._generate_response", lambda *a, **kw: "")  # ensure module attr check ignored cleanly
    # Reset _generate_response to real fn so bounded SDK path is tested with response headers
    from app.services import llm as upstream_llm

    monkeypatch.undo()
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", fake_secret)
    monkeypatch.setattr("time.sleep", lambda s: sleep_waits.append(float(s)))

    rpm_calls = {"n": 0}

    class _RateLimitSdkExc(Exception):
        def __init__(self, msg, headers):
            super().__init__(msg)
            self.response = NS(headers=headers)

    class _RpmCompletions:
        def create(self, **_kwargs):
            rpm_calls["n"] += 1
            if rpm_calls["n"] == 1:
                raise _RateLimitSdkExc(
                    "Error code: 429 - Rate limit exceeded: free-models-per-min",
                    {"Retry-After": "4"},
                )
            from openai.types.chat import ChatCompletion, ChatCompletionMessage
            from openai.types.chat.chat_completion import Choice

            return ChatCompletion(
                id="c-rpm",
                choices=[Choice(finish_reason="stop", index=0, message=ChatCompletionMessage(role="assistant", content="RPM OK"))],
                created=0,
                model="openrouter/free",
                object="chat.completion",
            )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: NS(chat=NS(completions=_RpmCompletions())),
    )
    assert upstream_llm._generate_response.__name__ == "_generate_response"
    ad_rpm = LLMAdapter(provider="openrouter", model="openrouter/free", cache_dir=tmp_path / "c3", max_retries=3, retry_delay=1.0)
    rpm_resp = ad_rpm.generate("hi", force_refresh=True)
    assert rpm_resp.content == "RPM OK"
    assert rpm_calls["n"] == 2
    assert any(w >= 4.0 for w in sleep_waits)

    # 4. OpenRouter /key quota check parses response cleanly without leaking key
    captured_auth: list[str] = []

    class _FakeHttpResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "label": "test-key",
                    "limit": 10.0,
                    "limit_remaining": 8.5,
                    "usage": 1.5,
                    "usage_daily": 0.0,
                    "is_free_tier": True,
                    "free_model_daily_requests": {"used": 12, "limit": 50, "remaining": 38},
                }
            }

    def _fake_get(url, headers=None, timeout=10.0):
        captured_auth.append(str((headers or {}).get("Authorization", "")))
        assert url == "https://openrouter.ai/api/v1/key"
        return _FakeHttpResp()

    monkeypatch.setattr("requests.get", _fake_get)
    q_data, q_err = fetch_openrouter_key_quota()
    assert q_err is None
    assert q_data["free_daily_used"] == 12
    assert q_data["free_daily_limit"] == 50
    assert q_data["free_daily_remaining"] == 38
    assert fake_secret not in json.dumps(q_data)
    assert captured_auth == [f"Bearer {fake_secret}"]

    # 5. UI error classification distinguishes empty_reasoning, daily_quota, auth, rate_limit
    assert components._classify_ui_llm_error_kind("[EMPTY_REASONING] finish_reason=length, reasoning_tokens=2048") == "empty_reasoning"
    assert components._classify_ui_llm_error_kind("[DAILY_QUOTA] free-models-per-day reached") == "daily_quota"
    assert components._classify_ui_llm_error_kind("[AUTH_ERROR] HTTP 401 User not found") == "auth"
    assert components._classify_ui_llm_error_kind("[RATE_LIMIT] HTTP 429 per-min") == "rate_limit"

    # 6. cli_longform _handle_script_cli_error records real error into job_state.json + log extraction fallback
    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    _, proj_dir = _seed_project(tmp_path, "proj_err_log", lang="id-ID")
    (proj_dir / "job_state.json").write_text(
        json.dumps({"project_id": "proj_err_log", "status": "running", "step": "script_ai"}),
        encoding="utf-8",
    )
    import cli_longform

    rc = cli_longform._handle_script_cli_error(
        proj_dir,
        "script_ai",
        LLMDailyQuotaError(f"429 free-models-per-day with key {fake_secret}"),
        "Error",
    )
    assert rc == 1
    saved_js = json.loads((proj_dir / "job_state.json").read_text(encoding="utf-8"))
    assert saved_js["status"] == "failed"
    assert "[DAILY_QUOTA]" in saved_js["error_message"]
    assert fake_secret not in saved_js["error_message"]

    # Log extraction fallback recovers real reason if error_message wasn't recorded yet
    log_path = proj_dir / "job.log"
    log_path.write_text("[JOB] Started\n[EMPTY_REASONING] reasoning_tokens=1500\n", encoding="utf-8")
    (proj_dir / "job_state.json").write_text(
        json.dumps({"project_id": "proj_err_log", "status": "failed", "log_file": str(log_path)}),
        encoding="utf-8",
    )
    assert "[EMPTY_REASONING]" in (jobs.extract_last_error_from_logs("proj_err_log") or "")


def test_c8_3_reasoning_control_no_identical_retry_and_free_models(tmp_path: Path, monkeypatch):
    """C8.3: OpenRouter defaults minimize_reasoning=True even for >512 tokens, never loops identical empty calls, and flags reasoning :free models."""
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice
    from app.services.longform.llm_adapter import (
        LLMAdapter,
        LLMEmptyResponseError,
        is_openrouter_reasoning_model,
        list_openrouter_free_models,
    )

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-test-key")

    seen_calls: list[dict] = []

    def _empty_reasoning_resp(model_name: str, usage_rt: int = 2048):
        return NS_completion(content="", finish_reason="length", reasoning_tokens=usage_rt, model=model_name)

    def NS_completion(*, content: str, finish_reason: str, reasoning_tokens: int, model: str):
        return ChatCompletion(
            id="cmp-1",
            choices=[Choice(finish_reason=finish_reason, index=0, message=ChatCompletionMessage(role="assistant", content=content))],
            created=0,
            model=model,
            object="chat.completion",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": reasoning_tokens,
                "total_tokens": 100 + reasoning_tokens,
                "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
            },
        )

    # 1. Chapter call (>512 max_tokens) STILL passes reasoning={"enabled": False} by default for OpenRouter!
    class _ChapterCompletions:
        def create(self, **kwargs):
            seen_calls.append(kwargs)
            return NS_completion(content="Bab 1 narasi lengkap.", finish_reason="stop", reasoning_tokens=0, model="openrouter/free")

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_ChapterCompletions())),
    )
    ad = LLMAdapter(provider="openrouter", model="openrouter/free", cache_dir=tmp_path / "c1")
    resp = ad.generate("Write chapter", max_tokens=1525, force_refresh=True)
    assert resp.content == "Bab 1 narasi lengkap."
    assert seen_calls[-1]["extra_body"] == {"reasoning": {"enabled": False}}

    # 2. Persistent empty reasoning responses escalate max_tokens beyond 2048 AND stop instead of identical repeats
    seen_calls.clear()

    class _AlwaysReasoningEmptyCompletions:
        def create(self, **kwargs):
            seen_calls.append(kwargs)
            return _empty_reasoning_resp("deepseek/deepseek-r1:free", usage_rt=kwargs.get("max_tokens", 1212))

    monkeypatch.setattr("time.sleep", lambda _s: None)
    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_AlwaysReasoningEmptyCompletions())),
    )
    ad_empty = LLMAdapter(provider="openrouter", model="openrouter/free", cache_dir=tmp_path / "c2", max_retries=3)
    with pytest.raises(LLMEmptyResponseError):
        ad_empty.generate("Write chapter", max_tokens=1212, force_refresh=True)

    # Every recorded call must have strictly distinct (max_tokens, extra_body) - zero identical repeats!
    signatures = [(c.get("max_tokens"), json.dumps(c.get("extra_body"), sort_keys=True)) for c in seen_calls]
    assert len(signatures) == len(set(signatures)), f"Duplicate identical retry detected: {signatures}"
    assert max(c.get("max_tokens", 0) for c in seen_calls) > 2048

    # 3. Free models listing flags reasoning vs non-reasoning and sorts non-reasoning first
    class _FakeModelsResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "deepseek/deepseek-r1-0528:free", "supported_parameters": ["reasoning"], "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "google/gemma-3-27b-it:free", "supported_parameters": ["max_tokens"], "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "openrouter/free", "supported_parameters": ["reasoning"], "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "openai/gpt-4o", "supported_parameters": ["max_tokens"], "pricing": {"prompt": "0.005", "completion": "0.015"}},
                ]
            }

    monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeModelsResp())
    free_list, err = list_openrouter_free_models(force_refresh=True)
    assert err is None
    ids = [e["id"] for e in free_list]
    assert "openai/gpt-4o" not in ids
    assert ids[0] == "google/gemma-3-27b-it:free"
    by_id = {e["id"]: e for e in free_list}
    assert by_id["google/gemma-3-27b-it:free"]["is_reasoning"] is False
    assert by_id["deepseek/deepseek-r1-0528:free"]["is_reasoning"] is True
    assert is_openrouter_reasoning_model("qwen/qwq-32b:free") is True


def test_c8_4_max_one_revision_and_batched_ai_keywords_10_to_20(tmp_path: Path, monkeypatch):
    """C8.4: Chapter length revision runs strictly <=1x per chapter, resume skips finished chapters, and AI keywords batch 10-20 shots with cache."""
    from app.services.longform.schema import Chapter, ProjectManifest, ScriptOutline, OutlineChapter, Shot
    from app.services.longform.script_generator import ScriptGenerator
    from app.services.longform.store import load_manifest, save_manifest
    from app.services.longform.visual.ai_keywords import generate_chapter_visual_keywords_ai

    # 1. Verify ScriptGenerator runs at most 1 length revision even when draft triggers lang retry or stays short
    _, proj_dir = _seed_project(tmp_path / "sg_c84", "sg_c84", lang="id-ID")
    sg = ScriptGenerator(project_dir=proj_dir, provider="fake", model="fake")
    outline = ScriptOutline(
        topic="Misteri Palung Mariana",
        target_duration_minutes=4.0,
        words_per_minute=140,
        total_target_words=400,
        candidate_titles=[
            "Misteri Palung Mariana",
            "Menyelami Dasar Samudra Terdalam",
            "Fakta Mengejutkan Zona Abisal",
        ],
        opening_hook="Di kedalaman laut terdapat misteri.",
        chapters=[
            OutlineChapter(chapter_index=1, title="Zona Abisal", purpose="Pintu masuk", key_points=["Tekanan"], target_words=200),
            OutlineChapter(chapter_index=2, title="Palung Terdalam", purpose="Fakta akhir", key_points=["Ekspedisi"], target_words=200),
        ],
        provider="fake",
        model="fake",
    )
    sg.outline_file.write_text(outline.model_dump_json(indent=2), encoding="utf-8")

    call_log: list[str] = []

    def _fake_generate(prompt, fake_kind=None, fake_kwargs=None, force_refresh=False, max_tokens=None, **_kw):
        from app.services.longform.llm_adapter import LLMResponse

        call_log.append(str(fake_kind or "unknown"))
        if fake_kind == "chapter":
            # Deliberately short (8 words) -> triggers 1 revise_length
            return LLMResponse(
                content="## Bab Satu\n\nTeks pendek Bahasa Indonesia tentang palung laut dalam sekali.",
                provider="fake",
                model="fake",
                duration_seconds=0.01,
            )
        if fake_kind == "revise_length":
            # Still short after 1 revision -> must NOT trigger a 2nd revision
            return LLMResponse(
                content="## Bab Satu\n\nRevisi pertama Bahasa Indonesia tentang tekanan air sangat tinggi di dasar samudra.",
                provider="fake",
                model="fake",
                duration_seconds=0.01,
            )
        return LLMResponse(
            content=json.dumps({"narrative_summary": "Ringkasan.", "key_facts": ["Fakta 1"]}),
            provider="fake",
            model="fake",
            duration_seconds=0.01,
        )

    monkeypatch.setattr(sg.adapter, "generate", _fake_generate)
    sg.write_chapters(force_refresh=True)
    # 2 chapters -> at most 1 revise_length per chapter == 2 total revise_length calls
    assert call_log.count("revise_length") <= 2

    # Re-running write_chapters resumes cleanly from completed state with 0 LLM calls
    call_log.clear()
    sg.write_chapters(force_refresh=False)
    assert call_log == []

    # 2. Verify AI visual keywords batch 28 shots into chunks of 15 + 13 (within 10-20 limit) and caches cleanly
    manifest, p_kw = _seed_project(tmp_path / "kw_c84", "kw_c84", lang="id-ID")
    shots = [
        Shot(
            shot_id=f"ch01_shot{i:03d}",
            chapter_index=1,
            shot_index=i,
            segment_indices=[i],
            start_seconds=float((i - 1) * 4),
            end_seconds=float(i * 4),
            duration_seconds=4.0,
            visual_prompt=f"Kapal selam menuruni zona gelap nomor {i} di dasar samudra pasifik.",
            search_query="kapal selam",
        )
        for i in range(1, 29)
    ]
    ch = Chapter(chapter_index=1, chapter_title="Bab 1", shots=shots)
    manifest.chapters = [ch]
    save_manifest(manifest, p_kw)

    monkeypatch.setattr(
        "app.services.longform.visual.ai_keywords.get_llm_readiness",
        lambda *_a, **_kw: {"configured": True, "is_invalid": False, "provider": "openrouter", "model": "google/gemma-3-27b-it:free"},
    )
    batch_sizes: list[int] = []

    def _fake_batch_llm(prompt_str, **_kw):
        # Extract JSON array inside prompt to count shot items per batch
        ids = re.findall(r'"(ch01_shot\d{3})"', prompt_str)
        batch_sizes.append(len(ids))
        resp_map = {sid: f"deep ocean submarine exploration clip {sid}" for sid in ids}
        return json.dumps(resp_map), "openrouter", "google/gemma-3-27b-it:free"

    monkeypatch.setattr(
        "app.services.longform.visual.ai_keywords._invoke_translation_llm",
        _fake_batch_llm,
    )
    res1 = generate_chapter_visual_keywords_ai(
        ch.shots,
        ch,
        preset_snapshot=manifest.preset_snapshot,
        project_lang="id-ID",
        cache_dir=p_kw / "cache",
    )
    assert res1["llm_calls"] == 2
    assert batch_sizes == [15, 13]
    assert res1["updated"] == 28

    # Second run skips already-AI shots with 0 LLM calls
    batch_sizes.clear()
    res2 = generate_chapter_visual_keywords_ai(
        ch.shots,
        ch,
        preset_snapshot=manifest.preset_snapshot,
        project_lang="id-ID",
        cache_dir=p_kw / "cache",
    )
    assert res2["llm_calls"] == 0

    # Freshly re-allocated shots with identical narration hit persistent cache with 0 LLM calls
    fresh_shots = [
        Shot(
            shot_id=f"ch01_shot{i:03d}",
            chapter_index=1,
            shot_index=i,
            segment_indices=[i],
            start_seconds=float((i - 1) * 4),
            end_seconds=float(i * 4),
            duration_seconds=4.0,
            visual_prompt=f"Kapal selam menuruni zona gelap nomor {i} di dasar samudra pasifik.",
            search_query="kapal selam",
        )
        for i in range(1, 29)
    ]
    ch_fresh = Chapter(chapter_index=1, chapter_title="Bab 1", shots=fresh_shots)
    res3 = generate_chapter_visual_keywords_ai(
        ch_fresh.shots,
        ch_fresh,
        preset_snapshot=manifest.preset_snapshot,
        project_lang="id-ID",
        cache_dir=p_kw / "cache",
    )
    assert res3["llm_calls"] == 0
    assert res3["cached"] == 28


def test_c8_2_c8_3_ui_failed_details_quota_and_reasoning_warning(tmp_path: Path, monkeypatch):
    """C8.2+C8.3 UI: Failed Details shows friendly cause (never bare Exit Code: 1), OpenRouter quota button & reasoning warning work."""
    from streamlit.testing.v1 import AppTest

    repo_root = Path(__file__).resolve().parents[2]
    lv_file = str(repo_root / "webui" / "pages" / "1_🎬_Long_Video.py")

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-uitestkey1234567890")
    monkeypatch.setitem(config.app, "openrouter_model_name", "deepseek/deepseek-r1-0528:free")
    monkeypatch.setitem(config.ui, "language", "id")

    monkeypatch.setattr("app.services.longform.store.get_projects_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.longform.ui.components.get_projects_root", lambda: tmp_path)

    _, proj_dir = _seed_project(tmp_path, "ui_c8_proj", lang="id-ID")
    (proj_dir / "job_state.json").write_text(
        json.dumps(
            {
                "project_id": "ui_c8_proj",
                "status": "failed",
                "step": "script_generate",
                "failed_step": "script_ai",
                "exit_code": 1,
                "error_message": "[EMPTY_REASONING] Empty text content (finish_reason=length, reasoning_tokens=2048)",
            }
        ),
        encoding="utf-8",
    )

    class _FakeQuotaResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "is_free_tier": True,
                    "limit": 10.0,
                    "limit_remaining": 10.0,
                    "usage_daily": 0.0,
                    "free_model_daily_requests": {"used": 3, "limit": 50, "remaining": 47},
                }
            }

    monkeypatch.setattr("requests.get", lambda *a, **kw: _FakeQuotaResp())

    at = AppTest.from_file(lv_file)
    at.query_params["lang"] = "id"
    at.query_params["project"] = "ui_c8_proj"
    at.run(timeout=25)
    assert not at.exception

    err_texts = "\n".join(str(e.value) for e in at.error)
    warn_texts = "\n".join(str(w.value) for w in at.warning)
    assert "reasoning" in err_texts.lower()
    assert "Kode Keluar: 1" not in err_texts
    assert "reasoning" in warn_texts.lower()

    quota_btn = at.button(key="btn_or_quota_ui_c8_proj")
    assert quota_btn is not None
    quota_btn.click().run(timeout=25)
    assert not at.exception
    info_texts = "\n".join(str(i.value) for i in at.info)
    assert "47" in info_texts and "50" in info_texts
