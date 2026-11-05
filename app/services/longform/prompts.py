"""Prompt Template Manager for Longform Video Generation.

Loads templates from resource/longform/prompts/ with user override support
from storage/longform/prompts/ (2.2).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict


def get_resource_prompts_dir() -> Path:
    """Return default builtin prompts directory."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return repo_root / "resource" / "longform" / "prompts"


def get_storage_prompts_dir() -> Path:
    """Return user-customized prompts directory in storage/."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return repo_root / "storage" / "longform" / "prompts"


def load_prompt_template(template_name: str) -> str:
    """Load prompt template, checking user storage first, then falling back to builtin resource."""
    base_name = template_name if template_name.endswith(".md") else f"{template_name}.md"

    storage_path = get_storage_prompts_dir() / base_name
    if storage_path.is_file():
        try:
            content = storage_path.read_text(encoding="utf-8-sig").strip()
            if content:
                return content
        except Exception:
            pass

    resource_path = get_resource_prompts_dir() / base_name
    if resource_path.is_file():
        return resource_path.read_text(encoding="utf-8-sig").strip()

    raise FileNotFoundError(
        f"Prompt template '{base_name}' not found in '{storage_path}' or '{resource_path}'."
    )


def render_prompt(template_name: str, context: Dict[str, Any]) -> str:
    """Render a prompt template with safe placeholder replacement.

    Uses explicit substring replacement instead of str.format to safely allow
    raw JSON braces in prompts without escaping.
    """
    template = load_prompt_template(template_name)
    rendered = template

    for key, value in context.items():
        placeholder = f"{{{key}}}"
        val_str = str(value) if value is not None else ""
        rendered = rendered.replace(placeholder, val_str)

    return rendered
