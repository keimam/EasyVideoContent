"""V102-G-A1 verification tests (GA1.1 - GA1.4)."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
import requests

from app.config import config
from app.services.longform.llm_adapter import LLMAdapter
from test.longform.network_guard import (
    RealLLMCallBlockedInTests,
    RealNetworkCallBlockedInTests,
    get_test_network_guard_counts,
)


def test_ga1_1_blocks_real_llm_sdk_and_outbound_http_even_with_fake_keys(monkeypatch, tmp_path: Path):
    """GA1.1: Unmocked LLM SDK/adapter and non-loopback HTTP calls are blocked without sending real requests."""
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-fakekeythatmustneverleave0000")

    before = get_test_network_guard_counts()
    adapter = LLMAdapter(provider="openrouter", model="openrouter/free", cache_dir=tmp_path / "cache", max_retries=1)
    with pytest.raises(RealLLMCallBlockedInTests):
        adapter.generate("Say hi", force_refresh=True)

    with pytest.raises(RealNetworkCallBlockedInTests):
        requests.get("https://openrouter.ai/api/v1/key", timeout=2)

    with pytest.raises(RealNetworkCallBlockedInTests):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("93.184.216.34", 80))
        finally:
            s.close()

    after = get_test_network_guard_counts()
    assert after["llm_blocked"] > before["llm_blocked"]
    assert after["http_blocked"] >= before["http_blocked"] + 2
    assert after["real_attempted"] == 0


def test_ga1_2_update_keywords_degrades_gracefully_on_daily_quota_without_leaking_user_id_or_url(
    monkeypatch, tmp_path: Path
):
    """GA1.2: Exhausted daily quota degrades to extraction with concise summary & no raw OpenRouter user_id/URL."""
    from unittest.mock import patch

    from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, Shot
    from app.services.longform.visual.service import VisualService

    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "sk-or-v1-dummytestkey00001111")

    proj_dir = tmp_path / "ga1_2_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    shots = [
        Shot(
            shot_id="ch01_shot001",
            chapter_index=1,
            shot_index=1,
            duration_seconds=4.0,
            visual_prompt="A lone traveler walks through deep pine woods in winter.",
            search_query="traveler pine woods",
            status=ItemStatus.pending,
        ),
        Shot(
            shot_id="ch01_shot002",
            chapter_index=1,
            shot_index=2,
            duration_seconds=4.0,
            visual_prompt="Lantern glow reflects on frozen timber gate.",
            search_query="lantern frozen gate",
            status=ItemStatus.pending,
        ),
    ]
    ch = Chapter(chapter_index=1, chapter_title="Ch 1", shots=shots)
    manifest = ProjectManifest(
        project_id="ga1_2_proj",
        title="Winter Forest",
        topic="Folklore",
        preset_id="mystery_folklore",
        preset_snapshot={"preset_id": "mystery_folklore", "name": "Mystery & Folklore"},
        chapters=[ch],
    )

    raw_or_429 = (
        "Error: Error code: 429 - {'error': {'message': 'Rate limit exceeded: free-models-per-day. "
        "Add 10 credits to unlock 1000 free model requests per day', "
        "'metadata': {'headers': {'X-RateLimit-Reset': '1795737600000'}, "
        "'user_id': 'user_secret_abc_999'}}, 'more': 'https://openrouter.ai/docs/limits'}"
    )
    with patch("app.services.llm._generate_response", return_value=raw_or_429):
        svc = VisualService(proj_dir)
        out_m, stats = svc.update_keywords_with_ai(manifest, force=True, mark_stale=True)

    assert stats["fallback_used"] is True
    assert stats["updated"] >= 1
    for sh in out_m.chapters[0].shots:
        assert sh.search_query_source == "extraction"
        assert sh.search_query_reason == "ai_quota_exceeded"
        assert sh.search_query_en

    joined_warnings = " | ".join(stats.get("warnings") or [])
    assert "kuota harian habis" in joined_warnings.lower()
    assert "OpenRouter" in joined_warnings
    assert "user_secret_abc_999" not in joined_warnings
    assert "https://openrouter.ai" not in joined_warnings


def test_ga1_3_flashback_variant_strips_relative_age_and_carried_objects():
    """GA1.3: Derived child variant removes adult/relative age words and transient props (doll, amulet)."""
    from app.services.longform.schema import VisualBible, VisualBibleCharacter
    from app.services.longform.visual.visual_bible import STYLE_MODE_BASE_TEXTS, ensure_flashback_character_variants

    vb = VisualBible(
        world="16th-century Slavic Russia",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted autumn tones",
        pov_character_id="vasilisa_16",
        characters=[
            VisualBibleCharacter(
                id="vasilisa_16",
                name="Vasilisa",
                aliases=["Vasilisa"],
                role="protagonist",
                description="teenage girl, dark hair in two braids, red kaftan with embroidered sash, bronze amulet doll given by her dead mother",
            )
        ],
    )
    out_vb, changed = ensure_flashback_character_variants(vb, "Your mother died when you were eight.")
    assert changed is True
    variants = [c for c in out_vb.characters if c.variant_of == "vasilisa_16"]
    assert len(variants) == 1
    assert variants[0].description == "8-year-old girl, dark hair in two braids, red kaftan with embroidered sash"

    # Also test other relative-age terms ("young woman, early 20s, elderly, late teens") & carries/holds
    vb2 = VisualBible(
        world="19th-century village",
        style_mode="realistic",
        style=STYLE_MODE_BASE_TEXTS["realistic"],
        palette_lighting="muted slate tones",
        characters=[
            VisualBibleCharacter(
                id="marta",
                name="Marta",
                aliases=["Marta"],
                role="protagonist",
                description="young woman, early 20s, braided auburn hair, wool shawl, carries a wooden doll and glowing lantern",
            )
        ],
    )
    out_vb2, changed2 = ensure_flashback_character_variants(vb2, "When Marta was eight years old, she hid by the gate.")
    assert changed2 is True
    v2 = next(c for c in out_vb2.characters if c.variant_of == "marta")
    low2 = v2.description.lower()
    assert low2.startswith("8-year-old girl")
    assert "young woman" not in low2 and "early 20s" not in low2
    assert "doll" not in low2 and "lantern" not in low2
    assert "braided auburn hair" in low2 and "wool shawl" in low2


def test_ga1_4_invalid_key_format_vs_server_auth_rejected_and_ui_copy(monkeypatch):
    """GA1.4 + GA2.1: Format mismatch is soft format_warning; Uji semua calls server for all keyed providers."""
    from unittest.mock import patch

    from app.services.longform.llm_adapter import (
        clear_provider_pause,
        get_llm_readiness,
        get_provider_chain_status,
        test_all_llm_providers as run_test_all,
    )
    from app.services.longform.ui.i18n import EN_TRANSLATIONS, ID_TRANSLATIONS

    clear_provider_pause()
    # 1. Unusual Gemini prefix -> soft format_warning only, still configured & in chain (GA2.1)
    monkeypatch.setitem(config.app, "llm_provider", "openrouter")
    monkeypatch.setitem(config.app, "openrouter_api_key", "custom-valid-provider-token-without-sk-prefix")
    monkeypatch.setitem(config.app, "gemini_api_key", "not-a-gemini-key")

    # Lightweight format heuristic must NOT reject a valid server key for non-Gemini OR Gemini providers
    assert get_llm_readiness("openrouter")["configured"] is True
    gem_r = get_llm_readiness("gemini")
    assert gem_r["configured"] is True
    assert gem_r["is_invalid"] is False
    assert gem_r["format_warning"] is True

    statuses = {s["provider"]: s for s in get_provider_chain_status(["openrouter", "gemini"])}
    assert statuses["gemini"]["status"] == "ready"
    assert statuses["gemini"]["format_warning"] is True
    assert "settings_failover_format_warn_badge" in ID_TRANSLATIONS
    assert "settings_failover_format_warn_badge" in EN_TRANSLATIONS

    # 2. Uji semua calls server even for unusual-prefix Gemini (ok -> ✅) vs server 401 (openrouter -> auth_rejected + paused)
    def _fake_ping(pid, timeout=12.0):
        if pid == "gemini":
            return True, "OK", 0.15
        return False, "HTTP 401 Unauthorized: User not found", 0.12

    with patch("app.services.longform.llm_adapter.test_llm_provider", side_effect=_fake_ping):
        results = {r["provider"]: r for r in run_test_all(["openrouter", "gemini"])}

    assert results["gemini"]["ok"] is True
    assert results["gemini"]["status"] == "ok"
    assert results["openrouter"]["status"] == "auth_rejected"
    assert "401/403" in results["openrouter"]["message"]
    clear_provider_pause()
