"""Central brand identity constants and design tokens for EasyFaceless Studio (B1, B2)."""

from __future__ import annotations

import base64
import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict

# Application Identity (B1)
APP_NAME = "EasyFaceless Studio"
APP_SHORT = "EasyFaceless"
APP_TAGLINE = "STUDIO"
APP_VERSION = "1.0.0"
APP_OWNER = "Naseh Ngulik"
APP_PUBLISHER = APP_OWNER
APP_COPYRIGHT_YEAR = "2026"
APP_SETUP_PREFIX = "EasyFacelessStudio-Setup"
CORE_NAME = "MoneyPrinterTurbo"

# Determine upstream core version dynamically
try:
    import app
    CORE_VERSION = getattr(app, "__version__", "1.3.10")
except Exception:
    CORE_VERSION = "1.3.10"

VERSION_STRING = f"v{APP_VERSION} · inti v{CORE_VERSION}"
UPSTREAM_UPDATE_PREFIX = "Pembaruan inti tersedia: "

# E1 / E2 / E3 External URLs & Release Update Manifest (Single Source)
# Empty placeholder by default: silent no-op for auto-check until production URL is set (E11.4); override via EFS_UPDATE_URL.
UPDATE_MANIFEST_URL = ""
OPENROUTER_KEYS_URL = "https://openrouter.ai/keys"
PIXABAY_API_DOCS_URL = "https://pixabay.com/api/docs/"
COVERR_API_URL = "https://coverr.co/api"
CAPCUT_DOWNLOAD_URL = "https://www.capcut.com/"

# Legal & MIT Attribution (Preserved from MoneyPrinterTurbo)
MIT_ATTRIBUTION = "Dibangun di atas MoneyPrinterTurbo (MIT)"
MIT_THANKS = (
    "EasyFaceless Studio dibangun di atas fondasi MoneyPrinterTurbo yang dibuat oleh Harry "
    "(@harry0703) di bawah Lisensi MIT. Seluruh fungsi inti pembuatan video pendek "
    "tetap dipertahankan dengan penuh rasa hormat kepada pengembang aslinya."
)
MIT_LICENSE_URL = "https://github.com/harry0703/MoneyPrinterTurbo/blob/main/LICENSE"
UPSTREAM_REPO_URL = "https://github.com/harry0703/MoneyPrinterTurbo"

# Paths to Brand Assets
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BRANDING_DIR = PROJECT_ROOT / "resource" / "branding"
LOGO_SVG_PATH = BRANDING_DIR / "logo.svg"
LOGO_PNG_PATH = BRANDING_DIR / "logo.png"
FAVICON_PATH = BRANDING_DIR / "favicon.png"

# Typography Paths (Local offline bundle, served via Streamlit static serving)
FONT_DIR = PROJECT_ROOT / "webui" / "static" / "fonts"
FONT_TTF_PATH = FONT_DIR / "PlusJakartaSans[wght].ttf"
FONT_OFL_PATH = FONT_DIR / "OFL.txt"

# Design Tokens & Palette (B2)
PALETTE = {
    "bg": "#0D1015",
    "sidebar": "#11151C",
    "surface": "#161B23",
    "surface_2": "#1C222C",
    "inset": "#0F1319",
    "border": "#2A3240",
    "border_strong": "#3A4456",
    "text": "#E8ECF2",
    "text_2": "#C3CBD6",
    "muted": "#A3ADBB",
    "dim": "#8C96A5",
    "accent": "#8FA6FF",
    "text_on_accent": "#0D1015",
    "success": "#4ADE9B",
    "warning": "#F5B544",
    "danger_text": "#FFB4A8",
    "danger_border": "#5A3A3A",
    "nav_active": "#1F2633",
}

# Radii & Component Metrics
RADII = {
    "card": "16px",
    "input": "10px",
    "button": "10px",
    "chip": "999px",
}

CONTROL_MIN_HEIGHT = "44px"
MIN_CONTRAST_RATIO = 4.5


def get_logo_svg_content() -> str:
    """Return raw SVG string of the EasyFaceless Studio logo."""
    if LOGO_SVG_PATH.is_file():
        return LOGO_SVG_PATH.read_text(encoding="utf-8").strip()
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="100%" height="100%">'
        '<rect x="32" y="32" width="448" height="448" rx="112" ry="112" fill="#8FA6FF"/>'
        '<circle cx="256" cy="256" r="136" fill="none" stroke="#0D1015" stroke-width="36"/>'
        '<polygon points="232,196 232,316 324,256" fill="#0D1015"/>'
        '</svg>'
    )


def get_logo_data_uri() -> str:
    """Return data URI of the logo SVG for use in <img> tags."""
    svg = get_logo_svg_content()
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


BUILD_META_PATH = Path(__file__).resolve().parent / "_build_meta.json"


@lru_cache(maxsize=1)
def _read_repo_git_head() -> tuple[str, str]:
    try:
        short_res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=2,
        )
        full_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=2,
        )
        short_hash = short_res.stdout.strip() if short_res.returncode == 0 else ""
        full_hash = full_res.stdout.strip() if full_res.returncode == 0 else ""
        if short_hash:
            return (short_hash, full_hash or short_hash)
    except Exception:
        pass
    return ("dev", "dev")


def get_build_info() -> Dict[str, str]:
    """Return embedded installer build metadata or live git HEAD fallback (E12.1, E12.3)."""
    if BUILD_META_PATH.is_file():
        try:
            data = json.loads(BUILD_META_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                short_hash = str(data.get("git_commit") or "").strip() or "unknown"
                full_hash = str(data.get("git_commit_full") or short_hash).strip()
                build_time = str(data.get("build_time") or "").strip() or "unknown"
                build_id = str(data.get("build_id") or "").strip() or f"{short_hash}-{build_time}"
                return {
                    "git_commit": short_hash,
                    "git_commit_full": full_hash,
                    "build_time": build_time,
                    "build_id": build_id,
                    "source": "embedded",
                }
        except Exception:
            pass
    short_hash, full_hash = _read_repo_git_head()
    return {
        "git_commit": short_hash,
        "git_commit_full": full_hash,
        "build_time": "source",
        "build_id": f"{short_hash}-source",
        "source": "git",
    }


def get_build_commit() -> str:
    return get_build_info()["git_commit"]


def get_build_time() -> str:
    return get_build_info()["build_time"]


def get_build_id() -> str:
    return get_build_info()["build_id"]


def get_app_build_signature() -> Dict[str, str]:
    """Return normalized `{app_version, build_id, git_commit, build_time}` for lock & UI (E12.1, E12.3)."""
    info = get_build_info()
    return {
        "app_version": APP_VERSION,
        "build_id": info["build_id"],
        "git_commit": info["git_commit"],
        "build_time": info["build_time"],
    }
