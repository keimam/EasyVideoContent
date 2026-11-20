"""Shared system specification thresholds and live diagnostic checker (Stage 8C.3, E1/E4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.longform import app_paths, platform_utils


# Calibrated single-source system thresholds shared by Wizard Step 2, README, and docs/user (E4).
SYSTEM_SPEC_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "ram_gb": {
        "min": 7.5,          # 8 GB physical RAM nominal (~7.5+ GiB reported by OS)
        "recommended": 15.0, # 16 GB physical RAM nominal
    },
    "cpu_cores": {
        "min": 4.0,          # 4 logical threads / 4 cores minimum
        "recommended": 8.0,  # 8+ logical threads / 6-8 cores recommended
    },
    "disk_free_gb": {
        "min": 15.0,         # 15 GB minimum free disk space (~2.3 GB/project + ~0.9 GB cache)
        "recommended": 20.0, # 20+ GB SSD recommended for multiple projects + stock cache
    },
}


def rate_numeric_spec(
    value: float,
    *,
    minimum: float,
    recommended: float,
) -> str:
    """Return 'green' (>= recommended), 'yellow' (>= minimum), or 'red' (< minimum)."""
    val = float(value)
    if val >= float(recommended):
        return "green"
    if val >= float(minimum):
        return "yellow"
    return "red"


def evaluate_system_specs(
    data_dir: Optional[Path] = None,
    *,
    include_internet: bool = True,
) -> Dict[str, Any]:
    """Measure host specs & dependencies and score green/yellow/red against shared thresholds."""
    from app.services.longform.capcut.detector import get_capcut_doctor_report

    target_dir = Path(data_dir).resolve() if data_dir else app_paths.get_data_dir()

    ram_gb = platform_utils.get_system_ram_gb()
    cpu_info = platform_utils.get_cpu_info()
    disk_free_gb = platform_utils.get_free_disk_gb(target_dir)
    ffmpeg_info = platform_utils.get_ffmpeg_status()
    internet_ok = platform_utils.check_internet_connectivity() if include_internet else True
    capcut_report = get_capcut_doctor_report()

    ram_status = rate_numeric_spec(
        ram_gb,
        minimum=SYSTEM_SPEC_THRESHOLDS["ram_gb"]["min"],
        recommended=SYSTEM_SPEC_THRESHOLDS["ram_gb"]["recommended"],
    )
    cpu_logical = int(cpu_info.get("logical_cores") or 1)
    cpu_physical = int(cpu_info.get("physical_cores") or cpu_logical)
    cpu_status = rate_numeric_spec(
        float(cpu_logical),
        minimum=SYSTEM_SPEC_THRESHOLDS["cpu_cores"]["min"],
        recommended=SYSTEM_SPEC_THRESHOLDS["cpu_cores"]["recommended"],
    )
    disk_status = rate_numeric_spec(
        disk_free_gb,
        minimum=SYSTEM_SPEC_THRESHOLDS["disk_free_gb"]["min"],
        recommended=SYSTEM_SPEC_THRESHOLDS["disk_free_gb"]["recommended"],
    )
    ffmpeg_status = "green" if ffmpeg_info.get("available") else "red"
    internet_status = "green" if internet_ok else "yellow"

    cc_kind = capcut_report.get("status_kind", "not_installed")
    if cc_kind == "tested":
        capcut_status = "green"
    elif cc_kind in ("untested", "not_installed"):
        capcut_status = "yellow"
    else:
        capcut_status = "yellow"

    checks: List[Dict[str, Any]] = [
        {
            "key": "ram",
            "status": ram_status,
            "value": ram_gb,
            "min": SYSTEM_SPEC_THRESHOLDS["ram_gb"]["min"],
            "recommended": SYSTEM_SPEC_THRESHOLDS["ram_gb"]["recommended"],
        },
        {
            "key": "cpu",
            "status": cpu_status,
            "value": cpu_logical,
            "physical": cpu_physical,
            "model": cpu_info.get("model") or "",
            "min": int(SYSTEM_SPEC_THRESHOLDS["cpu_cores"]["min"]),
            "recommended": int(SYSTEM_SPEC_THRESHOLDS["cpu_cores"]["recommended"]),
        },
        {
            "key": "disk",
            "status": disk_status,
            "value": disk_free_gb,
            "path": str(target_dir),
            "min": SYSTEM_SPEC_THRESHOLDS["disk_free_gb"]["min"],
            "recommended": SYSTEM_SPEC_THRESHOLDS["disk_free_gb"]["recommended"],
        },
        {
            "key": "ffmpeg",
            "status": ffmpeg_status,
            "available": bool(ffmpeg_info.get("available")),
            "path": ffmpeg_info.get("path"),
            "version": ffmpeg_info.get("version"),
        },
        {
            "key": "internet",
            "status": internet_status,
            "available": bool(internet_ok),
        },
        {
            "key": "capcut",
            "status": capcut_status,
            "report": capcut_report,
        },
    ]

    overall = "green"
    if any(c["status"] == "red" for c in checks):
        overall = "red"
    elif any(c["status"] == "yellow" for c in checks):
        overall = "yellow"

    return {
        "overall": overall,
        "data_dir": str(target_dir),
        "ram_gb": ram_gb,
        "cpu": cpu_info,
        "disk_free_gb": disk_free_gb,
        "ffmpeg": ffmpeg_info,
        "internet_ok": bool(internet_ok),
        "capcut": capcut_report,
        "checks": checks,
    }
