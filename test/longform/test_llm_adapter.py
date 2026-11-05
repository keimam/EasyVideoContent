"""Tests for Unified LLM Adapter, Caching, and Template Overrides (2.1, 2.2)."""

import json
from pathlib import Path
import pytest

from app.services.longform.llm_adapter import LLMAdapter, LLMAdapterError, sanitize_error
from app.services.longform.prompts import (
    get_storage_prompts_dir,
    load_prompt_template,
    render_prompt,
)


def test_llm_adapter_fake_and_cache(tmp_path: Path):
    """Verify that fake provider returns deterministic response and uses cache on rerun (2.1)."""
    cache_dir = tmp_path / "llm_cache"
    adapter = LLMAdapter(provider="fake", cache_dir=cache_dir)

    prompt = "Test prompt for fake provider"
    resp1 = adapter.generate(prompt=prompt, fake_kind="chapter")
    assert resp1.provider == "fake"
    assert resp1.cache_hit is False
    assert len(resp1.content) > 0

    # Cache file should exist
    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1

    # Second call must hit cache
    resp2 = adapter.generate(prompt=prompt, fake_kind="chapter")
    assert resp2.cache_hit is True
    assert resp2.content == resp1.content
    assert resp2.duration_seconds == 0.0


def test_sanitize_error_masks_sensitive_keys():
    """Verify error sanitizer redacts API keys and tokens (2.1)."""
    raw_error = "Authentication failed: api_key=sk-proj-1234567890abcdef12345678 for endpoint"
    sanitized = sanitize_error(raw_error)
    assert "sk-proj-1234567890abcdef12345678" not in sanitized
    assert "***REDACTED***" in sanitized


def test_prompt_template_override_from_storage(tmp_path: Path, monkeypatch):
    """Verify prompt templates can be loaded from builtin and overridden from storage/ (2.2)."""
    # 1. Builtin outline.md loads
    builtin_outline = load_prompt_template("outline")
    assert "Video Specifications:" in builtin_outline

    # 2. Mock storage directory with custom override
    storage_prompts = tmp_path / "storage" / "longform" / "prompts"
    storage_prompts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "app.services.longform.prompts.get_storage_prompts_dir",
        lambda: storage_prompts,
    )

    custom_content = "# CUSTOM OUTLINE TEMPLATE OVERRIDE\nTopic: {topic}"
    (storage_prompts / "outline.md").write_text(custom_content, encoding="utf-8")

    loaded_override = load_prompt_template("outline")
    assert "# CUSTOM OUTLINE TEMPLATE OVERRIDE" in loaded_override

    # 3. Test safe rendering with braces
    rendered = render_prompt("outline", {"topic": "Muscle Cars"})
    assert "Topic: Muscle Cars" in rendered


def test_llm_adapter_default_is_not_fake():
    """Verify adapter defaults to config.toml provider and never silently defaults to 'fake' (F1)."""
    adapter = LLMAdapter()
    assert adapter.provider != "fake"
    assert adapter.is_fake is False


def test_llm_adapter_missing_api_key_raises_configuration_error(tmp_path: Path):
    """Verify that unconfigured real provider fails fast with clear LLMConfigurationError (F1)."""
    from app.services.longform.llm_adapter import LLMConfigurationError

    adapter = LLMAdapter(provider="moonshot", cache_dir=tmp_path / "cache")
    with pytest.raises(LLMConfigurationError) as exc_info:
        adapter.generate("Test prompt")
    err_msg = str(exc_info.value)
    assert "moonshot_api_key" in err_msg
    assert "Basic Settings" in err_msg
    assert "--provider fake" in err_msg


def test_llm_adapter_provider_and_model_in_cache_key(tmp_path: Path):
    """Verify that provider and model are part of the cache key, preventing fake cache reuse (F1)."""
    cache_dir = tmp_path / "llm_cache"
    adapter_fake = LLMAdapter(provider="fake", model="model-1", cache_dir=cache_dir)
    adapter_other = LLMAdapter(provider="fake", model="model-2", cache_dir=cache_dir)

    prompt = "Identical prompt text"
    key1 = adapter_fake._compute_cache_key(prompt)
    key2 = adapter_other._compute_cache_key(prompt)
    assert key1 != key2


def test_llm_adapter_summary_fake_generation():
    """Verify fake adapter generates deterministic chapter summaries with key facts (F4)."""
    adapter = LLMAdapter(provider="fake")
    resp = adapter.generate(
        prompt="Please summarize this chapter",
        fake_kind="summary",
        fake_kwargs={
            "chapter_index": 2,
            "chapter_title": "Aero Testing",
            "topic": "Daytona 500",
        },
    )
    assert resp.provider == "fake"
    data = json.loads(resp.content)
    assert "narrative_summary" in data
    assert "Chapter 2 (Aero Testing)" in data["narrative_summary"]
    assert "key_facts" in data
    assert len(data["key_facts"]) >= 2
