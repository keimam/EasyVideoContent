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

    # Gemini key validation (E5)
    if "gemini" in provider_id or "google" in provider_id:
        if not raw_key.startswith("AIza"):
            return {
                "configured": False,
                "provider": provider_id,
                "requires_key": True,
                "key_name": key_name,
                "masked_key": mask_api_key(raw_key),
                "api_key_url": prov_def.api_key_url,
                "is_invalid": True,
                "message": "Key Gemini tampaknya tidak valid (bukan dari Google AI Studio)",
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
