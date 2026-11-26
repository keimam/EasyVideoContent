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
