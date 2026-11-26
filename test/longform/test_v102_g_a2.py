"""V102-G-A2 tests (GA2.4 items (a)-(e)): zero real network/LLM calls."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import config


def _clear_pending_app_updates() -> None:
    try:
        with config._pending_config_lock:
            sec_id = id(config.app)
            for k in [k for k in config._pending_config_updates if k[0] == sec_id]:
                config._pending_config_updates.pop(k, None)
    except Exception:
        pass


def test_ga2_a_unusual_gemini_key_soft_warning_and_server_decides(monkeypatch):
    """GA2.4(a): Non-AIza Gemini key returns configured=True + format_warning, joins chain, Test all calls server."""
    from app.services.longform.llm_adapter import (
        build_provider_failover_chain,
        clear_provider_pause,
        get_llm_readiness,
        get_provider_chain_status,
        test_all_llm_providers,
    )

    clear_provider_pause()
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.app, "llm_provider", "gemini")
    monkeypatch.setitem(config.app, "gemini_api_key", "AQ.FakeUnusualPrefixKey_UnitTest_0001")
    monkeypatch.setitem(config.app, "gemini_model_name", "gemini-3.5-flash-lite")

    readiness = get_llm_readiness("gemini")
    assert readiness["configured"] is True
    assert readiness["is_invalid"] is False
    assert readiness["format_warning"] is True
    assert "AIza" in (readiness.get("warning_message") or "")

    chain = build_provider_failover_chain(primary_provider="gemini", failover_chain=["openrouter"])
    assert chain[0] == "gemini"

    statuses = {s["provider"]: s for s in get_provider_chain_status(chain)}
    assert statuses["gemini"]["status"] == "ready"
    assert statuses["gemini"]["ready"] is True
    assert statuses["gemini"]["format_warning"] is True

    server_calls: list[str] = []

    def _mock_generate(self, prompt, **kwargs):
        server_calls.append(f"{self.provider}:{self.model}")
        return SimpleNamespace(content="OK")

    with patch("app.services.longform.llm_adapter.LLMAdapter.generate", _mock_generate):
        res = test_all_llm_providers(chain=["gemini"])

    assert server_calls == ["gemini:gemini-3.5-flash-lite"]
    assert len(res) == 1
    assert res[0]["ok"] is True
    assert res[0]["status"] == "ok"


def test_ga2_b_server_401_rejected_and_paused_until_key_changed(monkeypatch):
    """GA2.4(b): Server HTTP 401 yields 'key ditolak server' and pauses provider until key changes."""
    from app.services.longform.llm_adapter import (
        LLMAuthError,
        clear_provider_pause,
        get_provider_chain_status,
        get_provider_pause_state,
        test_all_llm_providers,
    )

    clear_provider_pause()
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.app, "llm_provider", "gemini")
    monkeypatch.setitem(config.app, "gemini_api_key", "AQ.RejectedByServerKey_0001")
    monkeypatch.setitem(config.app, "gemini_model_name", "gemini-3.5-flash-lite")

    def _raise_401(self, prompt, **kwargs):
        raise LLMAuthError("HTTP 401 Unauthorized: API key not valid.")

    with patch("app.services.longform.llm_adapter.LLMAdapter.generate", _raise_401):
        res = test_all_llm_providers(chain=["gemini"])

    assert len(res) == 1
    assert res[0]["ok"] is False
    assert res[0]["status"] == "auth_rejected"
    assert "key ditolak server" in res[0]["message"].lower()

    paused = get_provider_pause_state("gemini")
    assert paused is not None
    assert paused["reason"] == "auth_rejected"
    chain_st = {s["provider"]: s for s in get_provider_chain_status(["gemini"])}
    assert chain_st["gemini"]["status"] == "paused_auth"
    assert chain_st["gemini"]["ready"] is False

    # Changing API key immediately clears the auth pause
    monkeypatch.setitem(config.app, "gemini_api_key", "AQ.FreshValidReplacementKey_0002")
    assert get_provider_pause_state("gemini") is None
    clear_provider_pause()


def test_ga2_c_primary_gemini_live_snapshot_and_openrouter_backup(monkeypatch, tmp_path: Path):
    """GA2.4(c): llm_provider='gemini' puts Gemini primary once and preserves OpenRouter in backups."""
    from app.services.longform.config import save_llm_failover_chain
    from app.services.longform.llm_adapter import (
        build_provider_failover_chain,
        clear_provider_pause,
        get_effective_app_config,
        get_provider_chain_status,
    )

    clear_provider_pause()
    _clear_pending_app_updates()
    monkeypatch.setenv("EFS_DATA_DIR", str(tmp_path / "efs_data"))
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "gemini_api_key", "AQ.LivePrimaryGeminiKey_0003")
    monkeypatch.setitem(config.app, "gemini_model_name", "gemini-3.5-flash-lite")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-backup-key-0003")
    monkeypatch.setitem(config.app, "openrouter_model_name", "openrouter/free")

    # Even if saved backup list previously contained 'gemini', primary must never appear twice
    save_llm_failover_chain(["gemini", "openrouter"])

    # Simulate live switch via pending WebUI config update while config.app still holds 'openrouter'
    try:
        config._pending_config_updates[(id(config.app), "llm_provider")] = (
            config.app,
            "llm_provider",
            "gemini",
        )
        eff = get_effective_app_config()
        assert eff["llm_provider"] == "gemini"

        chain = build_provider_failover_chain()
        assert chain == ["gemini", "openrouter"]

        statuses = get_provider_chain_status()
        assert [s["provider"] for s in statuses] == ["gemini", "openrouter"]
        assert statuses[0]["provider"] == "gemini"
        assert statuses[0]["status"] == "ready"
        assert statuses[1]["provider"] == "openrouter"
        assert statuses[1]["status"] == "ready"
    finally:
        _clear_pending_app_updates()

    # Also verify direct config.app['llm_provider'] = 'gemini'
    monkeypatch.setitem(config.app, "llm_provider", "gemini")
    assert build_provider_failover_chain() == ["gemini", "openrouter"]
    clear_provider_pause()


def test_ga2_d_limit_zero_classified_failover_concise_and_model_scoped_pause(monkeypatch, tmp_path: Path):
    """GA2.4(d): Gemini 429 RESOURCE_EXHAUSTED limit: 0 -> model_not_in_free_tier, failover, concise msg, model-scoped pause."""
    from app.services.longform.config import save_llm_failover_chain
    from app.services.longform.llm_adapter import (
        LLMAdapter,
        LLMModelNotInFreeTierError,
        call_llm_with_failover,
        clear_provider_pause,
        format_concise_llm_error,
        get_provider_chain_status,
        get_provider_pause_state,
    )

    clear_provider_pause()
    monkeypatch.setenv("EFS_DATA_DIR", str(tmp_path / "efs_data"))
    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.ui, "language", "id")
    monkeypatch.setitem(config.app, "llm_provider", "gemini")
    monkeypatch.setitem(config.app, "gemini_api_key", "AIzaSyTestFreeTierLimitZero0001")
    monkeypatch.setitem(config.app, "gemini_model_name", "gemini-3.1-pro-preview")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-backup-0002")
    monkeypatch.setitem(config.app, "openrouter_model_name", "openrouter/free")
    save_llm_failover_chain(["openrouter"])

    raw_limit_zero = (
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
        "'You exceeded your current quota. For more information on this error, head to: "
        "https://ai.google.dev/gemini-api/docs/rate-limits.\\n"
        "* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, "
        "limit: 0, model: gemini-3.1-pro-preview', 'status': 'RESOURCE_EXHAUSTED'}}"
    )

    calls: list[tuple[str, str]] = []

    class _FakeGenAIClient:
        def __init__(self, **_kwargs):
            self.models = self

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def generate_content(self, *, model, **_kwargs):
            calls.append(("gemini", model))
            raise RuntimeError(raw_limit_zero)

    class _FakeOpenAICompletions:
        def create(self, *, model, **_kwargs):
            calls.append(("openrouter", model))
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="OK backup", reasoning=None),
                        finish_reason="stop",
                    )
                ]
            )

    monkeypatch.setattr("google.genai.Client", _FakeGenAIClient)
    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_FakeOpenAICompletions())),
    )

    # 1. Direct adapter raises LLMModelNotInFreeTierError immediately (0 retries)
    adapter = LLMAdapter(provider="gemini", model="gemini-3.1-pro-preview", max_retries=3)
    with pytest.raises(LLMModelNotInFreeTierError) as exc_info:
        adapter.generate("hello", force_refresh=True, max_tokens=64)
    assert len(calls) == 1

    concise = format_concise_llm_error(exc_info.value, "gemini")
    assert "Model gemini-3.1-pro-preview tidak tersedia di tier gratis Gemini." in concise
    assert "gemini-3.5-flash-lite" in concise
    assert "https://ai.google.dev" not in concise
    assert "Quota exceeded for metric" not in concise

    # 2. Failover switches immediately to OpenRouter and pauses ONLY gemini+gemini-3.1-pro-preview
    calls.clear()
    resp = call_llm_with_failover("hello", force_refresh=True, max_tokens=64)
    assert resp.provider == "openrouter"
    assert resp.failover_used is True
    assert resp.providers_tried == ["gemini", "openrouter"]
    assert ("gemini", "gemini-3.1-pro-preview") in calls

    paused_pro = get_provider_pause_state("gemini")
    assert paused_pro is not None
    assert paused_pro["reason"] == "model_not_in_free_tier"
    assert paused_pro["model"] == "gemini-3.1-pro-preview"
    st_pro = {s["provider"]: s for s in get_provider_chain_status()}
    assert st_pro["gemini"]["status"] == "model_not_in_free_tier"
    assert st_pro["gemini"]["ready"] is False

    # 3. Switching to Flash model clears pause immediately without changing API key
    monkeypatch.setitem(config.app, "gemini_model_name", "gemini-3.5-flash-lite")
    assert get_provider_pause_state("gemini") is None
    st_flash = {s["provider"]: s for s in get_provider_chain_status()}
    assert st_flash["gemini"]["status"] == "ready"
    assert st_flash["gemini"]["ready"] is True
    clear_provider_pause()


def test_ga2_e_default_gemini_longform_model_not_pro_preview(monkeypatch):
    """GA2.4(e): Empty gemini_model_name in Longform resolves to free-tier Flash model, not *-pro-preview."""
    from app.services.longform.llm_adapter import (
        GEMINI_FREE_DEFAULT_MODEL,
        get_llm_readiness,
        resolve_effective_model,
    )

    monkeypatch.setattr("app.config.config.app", dict(config.app))
    monkeypatch.setitem(config.app, "llm_provider", "gemini")
    monkeypatch.setitem(config.app, "gemini_api_key", "AIzaSyDummyTestKey0001")
    monkeypatch.setitem(config.app, "gemini_model_name", "")

    model_id, used_default = resolve_effective_model("gemini", "")
    assert used_default is True
    assert model_id == GEMINI_FREE_DEFAULT_MODEL
    assert "pro-preview" not in model_id.lower()
    assert "flash" in model_id.lower()

    readiness = get_llm_readiness("gemini")
    assert readiness["model"] == GEMINI_FREE_DEFAULT_MODEL
    assert "pro-preview" not in readiness["model"].lower()

    # Explicit user model choice still honored
    explicit, used_def_explicit = resolve_effective_model("gemini", "gemini-2.5-flash")
    assert explicit == "gemini-2.5-flash"
    assert used_def_explicit is False
