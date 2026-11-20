"""Detector module for CapCut Desktop installation, versions, draft directories, and processes (E3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from loguru import logger

from app.services.longform import platform_utils
from app.services.longform.config import load_longform_config

TEMPLATE_VERSION = "8.9.0"


def is_capcut_running() -> bool:
    """Check if CapCut Desktop process is currently running."""
    return platform_utils.is_capcut_running()


def parse_capcut_version_xml(xml_path: Path) -> dict[str, Any]:
    """Parse CapCut `ProductInfo.xml` cleanly into version metadata dict (E3)."""
    try:
        return platform_utils.parse_capcut_product_info_xml(Path(xml_path))
    except Exception as exc:
        logger.warning(f"Failed to parse ProductInfo.xml '{xml_path}': {exc}")
        return {
            "installed": False,
            "app_ver": None,
            "full_ver": None,
            "apps_dir": None,
            "product_info_path": str(xml_path),
        }


def is_capcut_version_tested(
    installed_ver: Optional[str],
    tested_versions: Optional[Sequence[str]] = None,
) -> bool:
    """Return True if `installed_ver` matches any tested CapCut Desktop version from config."""
    ver = str(installed_ver or "").strip()
    if not ver:
        return False
    allowed = tuple(tested_versions) if tested_versions is not None else load_longform_config().capcut_tested_versions
    ver_norm = ver.lower()
    for item in allowed:
        target = str(item or "").strip().lower()
        if not target:
            continue
        if ver_norm == target or ver_norm.startswith(f"{target}."):
            return True
    return False


def detect_capcut_version(product_info_path: Optional[Path] = None) -> dict[str, Any]:
    """Detect installed CapCut version via OS abstraction layer (platform_utils)."""
    info: dict[str, Any] = {
        "installed": False,
        "app_ver": None,
        "full_ver": None,
        "apps_dir": None,
        "product_info_path": None,
        "unsupported_os_reason": None,
    }
    try:
        raw = platform_utils.get_capcut_version_info(product_info_path=product_info_path)
        info.update(raw)
    except NotImplementedError as exc:
        info["unsupported_os_reason"] = str(exc)
    except Exception as exc:
        logger.warning(f"Failed detecting CapCut version: {exc}")
    return info


def detect_draft_folder() -> Path | None:
    """Detect CapCut user projects draft folder, preferring any manual folder override."""
    try:
        from app.services.longform.first_run import get_custom_capcut_drafts_dir

        override_dir = get_custom_capcut_drafts_dir()
        if override_dir and override_dir.is_dir():
            return override_dir
    except Exception:
        pass

    draft_folder = platform_utils.get_capcut_drafts_dir()
    if draft_folder and draft_folder.is_dir():
        return draft_folder
    return None


def get_capcut_doctor_report() -> dict[str, Any]:
    """Generate comprehensive CapCut doctor diagnostic report including tested status (E3)."""
    cfg = load_longform_config()
    ver_info = detect_capcut_version()
    draft_folder = detect_draft_folder()
    running = is_capcut_running()

    installed = bool(ver_info.get("installed"))
    app_ver = ver_info.get("app_ver")
    full_ver = ver_info.get("full_ver")
    tested_versions = list(cfg.capcut_tested_versions or (TEMPLATE_VERSION,))
    template_ver = tested_versions[0] if tested_versions else TEMPLATE_VERSION
    unsupported_reason = ver_info.get("unsupported_os_reason")

    warnings: list[str] = []
    version_tested = is_capcut_version_tested(app_ver, tested_versions)
    version_match = version_tested

    if unsupported_reason:
        status_kind = "unsupported_os"
        warnings.append(str(unsupported_reason))
    elif not installed:
        status_kind = "not_installed"
        warnings.append("CapCut Desktop does not appear to be installed in the standard LocalAppData path.")
    elif version_tested:
        status_kind = "tested"
    else:
        status_kind = "untested"
        warnings.append(
            f"Installed CapCut version '{app_ver or 'unknown'}' has not been tested against {', '.join(tested_versions)}. "
            "Draft export is still allowed; please inspect the draft in CapCut."
        )

    if running:
        warnings.append("CapCut is currently running. Installing drafts while CapCut is active is blocked.")

    if draft_folder is None:
        warnings.append("CapCut draft directory (com.lveditor.draft) could not be located.")

    return {
        "installed": installed,
        "installed_version": app_ver,
        "full_version": full_ver,
        "template_version": template_ver,
        "tested_versions": tested_versions,
        "version_match": version_match,
        "version_tested": version_tested,
        "status_kind": status_kind,
        "unsupported_os_reason": unsupported_reason,
        "download_url": cfg.capcut_download_url,
        "is_running": running,
        "draft_folder": str(draft_folder) if draft_folder else None,
        "apps_dir": ver_info.get("apps_dir"),
        "warnings": warnings,
    }
