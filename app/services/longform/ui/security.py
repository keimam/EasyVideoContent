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
    """Mask sensitive API keys for display (e.g. 'pk_****abcd', 'sk-****1234') (B6).

    Never exposes full key content.
    """
    if not key or not isinstance(key, str):
        return "(belum diatur)"

    k = key.strip()
    if len(k) <= 6:
        return "****"

    # Check for common prefixes like pk_..., sk-..., etc.
    if "_" in k[:6]:
        prefix = k.split("_", 1)[0] + "_"
        suffix = k[-4:]
        return f"{prefix}****{suffix}"
    elif "-" in k[:6]:
        prefix = k.split("-", 1)[0] + "-"
        suffix = k[-4:]
        return f"{prefix}****{suffix}"
    elif len(k) > 10:
        return f"{k[:3]}****{k[-4:]}"
    else:
        return f"{k[:2]}****{k[-2:]}"


def check_llm_status() -> Dict[str, Any]:
    """Check active LLM provider configuration and credential status."""
    provider_id = (config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID).lower().strip()
    prov_def = get_llm_provider(provider_id)

    if not prov_def:
        return {
            "configured": False,
            "provider": provider_id,
            "requires_key": True,
            "key_name": None,
            "masked_key": None,
            "message": f"Provider '{provider_id}' tidak terdaftar dalam sistem.",
        }

    if not prov_def.requires_api_key:
        label = getattr(prov_def, "default_label", prov_def.provider_id)
        return {
            "configured": True,
            "provider": provider_id,
            "requires_key": False,
            "key_name": None,
            "masked_key": "(tidak memerlukan API key)",
            "message": f"Provider '{label}' aktif (lokal/tanpa kunci).",
        }

    key_name = prov_def.config_key("api_key")
    raw_key = (config.app.get(key_name) or "").strip()

    if not raw_key:
        return {
            "configured": False,
            "provider": provider_id,
            "requires_key": True,
            "key_name": key_name,
            "masked_key": None,
            "api_key_url": prov_def.api_key_url,
            "message": f"Kunci API '{key_name}' belum dikonfigurasi di config.toml.",
        }

    label = getattr(prov_def, "default_label", prov_def.provider_id)
    return {
        "configured": True,
        "provider": provider_id,
        "requires_key": True,
        "key_name": key_name,
        "masked_key": mask_api_key(raw_key),
        "message": f"Provider '{label}' siap.",
    }


def safe_windows_path(path: str | Path) -> str:
    """Format path string safely for Windows environments with spaces (B6)."""
    p = Path(path).resolve()
    return str(p)
