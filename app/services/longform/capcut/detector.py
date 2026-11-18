"""Detector module for CapCut Desktop installation, versions, draft directories, and processes."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.longform import platform_utils

TEMPLATE_VERSION = "8.9.0"


def is_capcut_running() -> bool:
    """Check if CapCut Desktop process is currently running."""
    return platform_utils.is_capcut_running()


def detect_capcut_version() -> dict[str, Any]:
    """
    Detect installed CapCut version from ProductInfo.xml / bundle metadata.
    Returns dictionary with version details.
    """
    info = {
        "installed": False,
        "app_ver": None,
        "full_ver": None,
        "apps_dir": None,
        "product_info_path": None,
    }

    product_xml = platform_utils.get_capcut_product_info_path()
    if not product_xml:
        return info

    apps_dir = product_xml.parent
    if product_xml.is_file():
        try:
            tree = ET.parse(product_xml)
            root = tree.getroot()
            appver_el = root.find("appver")
            fullver_el = root.find("full_appver")
            app_ver = appver_el.attrib.get("value") if appver_el is not None else None
            full_ver = fullver_el.attrib.get("value") if fullver_el is not None else None

            info["installed"] = True
            info["app_ver"] = app_ver
            info["full_ver"] = full_ver
            info["apps_dir"] = str(apps_dir)
            info["product_info_path"] = str(product_xml)
        except Exception as exc:
            logger.warning(f"Failed to parse ProductInfo.xml: {exc}")

    return info


def detect_draft_folder() -> Path | None:
    """Detect CapCut user projects draft folder."""
    draft_folder = platform_utils.get_capcut_drafts_dir()
    if draft_folder and draft_folder.is_dir():
        return draft_folder
    return None


def get_capcut_doctor_report() -> dict[str, Any]:
    """Generate comprehensive CapCut doctor diagnostic report."""
    ver_info = detect_capcut_version()
    draft_folder = detect_draft_folder()
    running = is_capcut_running()

    installed = ver_info["installed"]
    app_ver = ver_info["app_ver"]
    full_ver = ver_info["full_ver"]
    template_ver = TEMPLATE_VERSION

    warnings: list[str] = []
    version_match = False

    if not installed:
        warnings.append("CapCut Desktop does not appear to be installed in the standard LocalAppData path.")
    else:
        if app_ver == template_ver:
            version_match = True
        else:
            warnings.append(
                f"Installed CapCut version '{app_ver}' differs from template version '{template_ver}'. "
                f"Draft compatibility cannot be fully guaranteed on unverified versions."
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
        "version_match": version_match,
        "is_running": running,
        "draft_folder": str(draft_folder) if draft_folder else None,
        "apps_dir": ver_info.get("apps_dir"),
        "warnings": warnings,
    }
