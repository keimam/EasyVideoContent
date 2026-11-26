"""Pytest fixtures for longform video test suite (Stage 7B.2 Z4)."""

from __future__ import annotations

import functools
import socket
import urllib.parse
from pathlib import Path

import pytest

from test.longform.network_guard import (
    _NET_GUARD_COUNTS,
    RealLLMCallBlockedInTests,
    RealNetworkCallBlockedInTests,
    get_test_network_guard_counts,
    record_http_blocked,
    record_llm_blocked,
)


def _is_loopback_host(host: str | None) -> bool:
    h = str(host or "").strip().lower().strip("[]")
    return h in ("", "localhost", "127.0.0.1", "::1")


def _url_is_loopback(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
        if parsed.scheme in ("file", "data", "blob", ""):
            return True
        return _is_loopback_host(parsed.hostname)
    except Exception:
        return False


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_network: allow real outbound network connections in this test (GA1.1).",
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    total_blocked = _NET_GUARD_COUNTS["llm_blocked"] + _NET_GUARD_COUNTS["http_blocked"]
    terminalreporter.write_line(
        f"real LLM/HTTP calls blocked: {total_blocked}, attempted: {_NET_GUARD_COUNTS['real_attempted']}"
    )


@pytest.fixture(autouse=True)
def block_real_llm_and_network_calls(request, tmp_path: Path):
    """Block all unmocked real LLM & outbound non-loopback HTTP/socket calls across tests (GA1.1)."""
    if "allow_network" in request.keywords:
        yield
        return

    import httpx
    import openai
    import requests.adapters
    import urllib.request
    from google import genai

    from app.config import config as app_cfg
    from app.services import llm as upstream_llm
    from app.services import version_checker as upstream_ver
    from app.services.longform import config as lf_cfg
    from app.services.longform.llm_adapter import LLMAdapter, clear_provider_pause

    # Separate MonkeyPatch instance so tests calling monkeypatch.undo() cannot strip this guard
    guard_mp = pytest.MonkeyPatch()

    # Prevent Main.py background version check thread from hitting api.github.com
    guard_mp.setattr(upstream_ver, "get_available_update", lambda *_a, **_kw: None)
    guard_mp.setattr(
        upstream_ver,
        "poll_available_update",
        lambda *_a, **_kw: upstream_ver.UpdateCheckSnapshot(complete=True),
    )

    orig_generate = upstream_llm._generate_response
    orig_bounded_sdk = LLMAdapter._call_bounded_sdk
    orig_openai_cls = openai.OpenAI
    orig_azure_cls = openai.AzureOpenAI
    orig_genai_cls = genai.Client
    orig_http_adapter_send = requests.adapters.HTTPAdapter.send
    orig_httpx_transport_handle = httpx.HTTPTransport.handle_request
    orig_urllib_open = urllib.request.OpenerDirector.open
    orig_socket_connect = socket.socket.connect

    # Optional test mode for Condition (2): blank/dummy ONLY LLM keys via isolated temp copy outside tmp_path
    import os
    import tempfile
    test_cfg_mode = os.environ.get("EFS_TEST_CONFIG_MODE", "").strip().lower()
    _STOCK_KEY_FIELDS = {"pixabay_api_keys", "pexels_api_keys", "coverr_api_keys", "unsplash_api_keys"}
    if test_cfg_mode in ("fake_keys", "empty_keys"):
        from app.models.llm_provider import LLM_PROVIDER_REGISTRY
        llm_key_fields = {p.config_key("api_key") for p in LLM_PROVIDER_REGISTRY}
        with tempfile.TemporaryDirectory() as isolated_dir:
            temp_cfg_path = Path(isolated_dir) / "isolated_config.toml"
            if test_cfg_mode == "fake_keys":
                temp_cfg_path.write_text(
                    '[app]\nllm_provider = "openrouter"\nopenrouter_api_key = "sk-or-v1-dummyisolatedkey0000"\n'
                    'gemini_api_key = "AIzaSyDummyIsolatedKey0000000000000"\ngrok_api_key = ""\ndeepseek_api_key = ""\n',
                    encoding="utf-8",
                )
            else:
                temp_cfg_path.write_text('[app]\nllm_provider = "openrouter"\nopenrouter_api_key = ""\n', encoding="utf-8")

        for k in list(app_cfg.app.keys()):
            if k in _STOCK_KEY_FIELDS:
                continue
            if k in llm_key_fields or (k.endswith("_api_key") and "image" not in k and "tts" not in k and "speech" not in k):
                guard_mp.setitem(app_cfg.app, k, "")
        if test_cfg_mode == "fake_keys":
            guard_mp.setitem(app_cfg.app, "openrouter_api_key", "sk-or-v1-dummyisolatedkey0000")
            guard_mp.setitem(app_cfg.app, "gemini_api_key", "AIzaSyDummyIsolatedKey0000000000000")

    # Force safe defaults: EFS_DEV=1, fake LLM provider, empty failover chain, isolated pause state
    guard_mp.setenv("EFS_DEV", "1")
    guard_mp.setenv("EFS_LLM_PAUSE_STATE_PATH", str(tmp_path / "test_llm_pauses.json"))
    if request.node.name != "test_llm_adapter_default_is_not_fake":
        guard_mp.setitem(app_cfg.app, "llm_provider", "fake")
    elif not str(app_cfg.app.get("llm_provider") or "").strip():
        guard_mp.setitem(app_cfg.app, "llm_provider", "openrouter")
    clear_provider_pause()

    lf_cfg._CONFIG_CACHE = None
    failover_override_active = {"saved": False}
    orig_get_failover = lf_cfg.get_llm_failover_chain
    orig_save_failover = lf_cfg.save_llm_failover_chain

    def _isolated_save_failover(*args, **kwargs):
        failover_override_active["saved"] = True
        return orig_save_failover(*args, **kwargs)

    def _isolated_get_failover(*, reload: bool = False):
        if not failover_override_active["saved"] and not os.environ.get("EFS_DATA_DIR", "").strip():
            return []
        return orig_get_failover(reload=reload)

    guard_mp.setattr(lf_cfg, "save_llm_failover_chain", _isolated_save_failover)
    guard_mp.setattr(lf_cfg, "get_llm_failover_chain", _isolated_get_failover)

    @functools.wraps(orig_generate)
    def _blocked_upstream_generate(*args, **kwargs):
        record_llm_blocked()
        raise RealLLMCallBlockedInTests(
            "Real upstream LLM call blocked in tests (mock app.services.llm._generate_response or use provider='fake')."
        )

    def _sdk_client_is_mocked(provider_id: str = "") -> bool:
        pid = (provider_id or "").strip().lower()
        if pid in ("gemini", "google"):
            return genai.Client is not orig_genai_cls
        if pid == "azure":
            return openai.AzureOpenAI is not orig_azure_cls
        return openai.OpenAI is not orig_openai_cls

    def _guarded_bounded_sdk(self, *args, **kwargs):
        if _sdk_client_is_mocked(getattr(self, "provider", "")):
            return orig_bounded_sdk(self, *args, **kwargs)
        record_llm_blocked()
        raise RealLLMCallBlockedInTests(
            f"Real LLM SDK call blocked for provider '{getattr(self, 'provider', '')}' in tests."
        )

    orig_openai_init = orig_openai_cls.__init__
    orig_azure_init = orig_azure_cls.__init__
    orig_genai_init = orig_genai_cls.__init__

    def _blocked_openai_init(self, *args, **kwargs):
        record_llm_blocked()
        raise RealLLMCallBlockedInTests("Real openai.OpenAI client instantiation blocked in tests.")

    def _blocked_azure_init(self, *args, **kwargs):
        record_llm_blocked()
        raise RealLLMCallBlockedInTests("Real openai.AzureOpenAI client instantiation blocked in tests.")

    def _blocked_genai_init(self, *args, **kwargs):
        record_llm_blocked()
        raise RealLLMCallBlockedInTests("Real google.genai.Client instantiation blocked in tests.")

    def _guarded_http_adapter_send(self, req, *args, **kwargs):
        url = getattr(req, "url", "")
        if _url_is_loopback(url):
            return orig_http_adapter_send(self, req, *args, **kwargs)
        record_http_blocked()
        raise RealNetworkCallBlockedInTests(f"Real outbound HTTP call blocked in tests: {url}")

    def _guarded_httpx_handle(self, req, *args, **kwargs):
        url = str(getattr(req, "url", ""))
        if _url_is_loopback(url):
            return orig_httpx_transport_handle(self, req, *args, **kwargs)
        record_http_blocked()
        raise RealNetworkCallBlockedInTests(f"Real outbound httpx call blocked in tests: {url}")

    def _guarded_urllib_open(self, fullurl, *args, **kwargs):
        url = fullurl.full_url if hasattr(fullurl, "full_url") else str(fullurl)
        if _url_is_loopback(url):
            return orig_urllib_open(self, fullurl, *args, **kwargs)
        record_http_blocked()
        raise RealNetworkCallBlockedInTests(f"Real outbound urllib call blocked in tests: {url}")

    def _guarded_socket_connect(sock_self, address, *args, **kwargs):
        try:
            family = getattr(sock_self, "family", socket.AF_INET)
            if family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address:
                host = str(address[0] or "")
                if not _is_loopback_host(host):
                    record_http_blocked()
                    raise RealNetworkCallBlockedInTests(f"Real outbound socket connection blocked in tests: {address}")
        except RealNetworkCallBlockedInTests:
            raise
        except Exception:
            pass
        return orig_socket_connect(sock_self, address, *args, **kwargs)

    guard_mp.setattr(upstream_llm, "_generate_response", _blocked_upstream_generate)
    guard_mp.setattr(LLMAdapter, "_call_bounded_sdk", _guarded_bounded_sdk)
    guard_mp.setattr(orig_openai_cls, "__init__", _blocked_openai_init)
    guard_mp.setattr(orig_azure_cls, "__init__", _blocked_azure_init)
    guard_mp.setattr(orig_genai_cls, "__init__", _blocked_genai_init)
    guard_mp.setattr(requests.adapters.HTTPAdapter, "send", _guarded_http_adapter_send)
    guard_mp.setattr(httpx.HTTPTransport, "handle_request", _guarded_httpx_handle)
    guard_mp.setattr(urllib.request.OpenerDirector, "open", _guarded_urllib_open)
    guard_mp.setattr(socket.socket, "connect", _guarded_socket_connect)

    try:
        yield
    finally:
        guard_mp.undo()
        clear_provider_pause()


@pytest.fixture(autouse=True)
def mock_capcut_not_running(monkeypatch, request):
    """Ensure CapCut is treated as NOT running by default across all longform tests,
    unless explicitly testing CapCut running detection.
    """
    test_name = request.node.name
    # Don't mock if test specifically checks running CapCut
    if "running_capcut" in test_name or "capcut_running" in test_name:
        return
    if "real_capcut" in request.keywords or "allow_real_capcut" in request.keywords:
        return

    monkeypatch.setattr("app.services.longform.capcut.detector.is_capcut_running", lambda: False)
    monkeypatch.setattr("app.services.longform.capcut.service.is_capcut_running", lambda: False)


@pytest.fixture(autouse=True)
def default_ui_language_id(monkeypatch):
    """Isolate tests from host config.toml language setting by defaulting to 'id' unless overridden."""
    from app.config import config

    monkeypatch.setitem(config.ui, "language", "id")


@pytest.fixture(autouse=True)
def isolate_host_config_toml(monkeypatch):
    """Prevent any test or AppTest from mutating or saving to the host user's config.toml."""
    import copy
    from app.config import config

    orig_app = copy.deepcopy(config.app)
    orig_ui = copy.deepcopy(config.ui)

    monkeypatch.setattr(config, "save_config", lambda *a, **kw: None)
    monkeypatch.setattr(config, "try_save_config", lambda *a, **kw: True)
    monkeypatch.setattr(config, "_schedule_deferred_config_flush", lambda *a, **kw: None)
    monkeypatch.setattr(config, "_run_deferred_config_flush", lambda *a, **kw: None)

    try:
        yield
    finally:
        config.app.clear()
        config.app.update(orig_app)
        config.ui.clear()
        config.ui.update(orig_ui)


@pytest.fixture(autouse=True)
def default_setup_completed_for_legacy_tests(monkeypatch, request):
    """Treat first-run setup as completed for legacy UI/branding tests that do not test the Wizard."""
    name = request.node.name.lower()
    if any(tok in name for tok in ("wizard", "first_run", "onboarding_tour", "_e1_", "_e2_", "_e5_", "_e8_", "_e9_")):
        return
    monkeypatch.setattr("app.services.longform.first_run.is_setup_completed", lambda: True)


@pytest.fixture(autouse=True)
def reset_longform_config_cache_between_tests():
    """Ensure LongformConfig global cache never leaks custom/slow profiles across tests."""
    from app.services.longform import config as lf_cfg

    lf_cfg._CONFIG_CACHE = None
    yield
    lf_cfg._CONFIG_CACHE = None
