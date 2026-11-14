"""Central brand identity constants and design tokens for EasyFaceless Studio (B1, B2)."""

from __future__ import annotations

import base64
from pathlib import Path

# Application Identity (B1)
APP_NAME = "EasyFaceless Studio"
APP_SHORT = "EasyFaceless"
APP_TAGLINE = "STUDIO"
APP_VERSION = "1.0.0"
CORE_NAME = "MoneyPrinterTurbo"

# Determine upstream core version dynamically
try:
    import app
    CORE_VERSION = getattr(app, "__version__", "1.3.10")
except Exception:
    CORE_VERSION = "1.3.10"

VERSION_STRING = f"v{APP_VERSION} · inti v{CORE_VERSION}"
UPSTREAM_UPDATE_PREFIX = "Pembaruan inti MoneyPrinterTurbo tersedia: "

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

# Typography Paths (Local offline bundle)
FONT_DIR = PROJECT_ROOT / "resource" / "fonts" / "plus-jakarta-sans"
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
