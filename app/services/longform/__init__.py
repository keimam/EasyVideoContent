"""Longform Video Service Package.

Dedicated pipeline and project models for producing 10-20 minute structured videos
with presets, smart caching, resume capability, and atomic persistence.
"""

try:
    from app.services.branding.constants import APP_VERSION as __version__
except Exception:
    __version__ = "1.0.0"
