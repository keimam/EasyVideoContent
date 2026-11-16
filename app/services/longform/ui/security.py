"""Security and credential masking utilities for Long Video WebUI (B6).

Ensures API keys are masked in the UI and never exposed in logs,
and provides safe path handling on Windows.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import config
from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider


def mask_api_key(key: Optional[str]) -> str:
    """Mask sensitive API keys for display (e.g. '****abcd') (B6, E5).

    Returns '****' followed by the last 4 characters only.
    """
    if not key or not isinstance(key, str):
        return "(belum diatur)"

    k = key.strip()
    if len(k) <= 6:
        return "****"
    return f"****{k[-4:]}"


def check_llm_status() -> Dict[str, Any]:
    """Check active LLM provider configuration and credential status via unified LLM adapter."""
    from app.services.longform.llm_adapter import get_llm_readiness

    readiness = get_llm_readiness()
    provider_id = readiness["provider"]
    prov_def = get_llm_provider(provider_id)
    key_name = prov_def.config_key("api_key") if (prov_def and prov_def.requires_api_key) else None
    requires_key = bool(readiness.get("requires_api_key", True))

    return {
        "configured": bool(readiness.get("configured", False)),
        "provider": provider_id,
        "provider_label": readiness.get("provider_label", provider_id),
        "model": readiness.get("model", ""),
        "adapter": readiness.get("adapter", ""),
        "requires_key": requires_key,
        "key_name": key_name,
        "masked_key": readiness.get("masked_key") or ("(tidak memerlukan API key)" if not requires_key else None),
        "api_key_url": prov_def.api_key_url if prov_def else None,
        "is_invalid": bool(readiness.get("is_invalid", False)),
        "message": readiness.get("message", ""),
        "configured_providers": readiness.get("configured_providers", ()),
    }


def safe_windows_path(path: str | Path) -> str:
    """Format path string safely for Windows environments with spaces (B6)."""
    p = Path(path).resolve()
    return str(p)
