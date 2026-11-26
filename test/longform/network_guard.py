"""Shared network/LLM test isolation exceptions & counters (V102 GA1.1)."""

from __future__ import annotations

from typing import Dict


class RealLLMCallBlockedInTests(BaseException):
    """Raised whenever an unmocked test attempts to call a real LLM SDK/endpoint."""


class RealNetworkCallBlockedInTests(BaseException):
    """Raised whenever an unmocked test attempts an outbound non-loopback network connection."""


_NET_GUARD_COUNTS: Dict[str, int] = {
    "llm_blocked": 0,
    "http_blocked": 0,
    "real_attempted": 0,
}


def record_llm_blocked() -> None:
    _NET_GUARD_COUNTS["llm_blocked"] += 1


def record_http_blocked() -> None:
    _NET_GUARD_COUNTS["http_blocked"] += 1


def get_test_network_guard_counts() -> Dict[str, int]:
    """Return snapshot of blocked vs real outbound call counters (GA1.1)."""
    return dict(_NET_GUARD_COUNTS)
