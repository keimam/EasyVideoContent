"""Visual module for longform production engine (Stage 4)."""

from app.services.longform.visual.ai_image import AIImageProvider
from app.services.longform.visual.base import (
    BaseVisualProvider,
    VisualAsset,
    VisualCandidate,
    VisualConfigurationError,
    VisualError,
    compute_file_content_hash,
)
from app.services.longform.visual.builder import (
    ShotClipBuilder,
    verify_clip_stream_parameters,
)
from app.services.longform.visual.cache import GlobalMediaCache
from app.services.longform.visual.contact_sheet import generate_chapter_contact_sheet
from app.services.longform.visual.credits import CreditsManager
from app.services.longform.visual.fake import FakeVisualProvider
from app.services.longform.visual.local import LocalLibraryManager, LocalVisualProvider
from app.services.longform.visual.service import VisualService
from app.services.longform.visual.stock import StockVisualProvider

__all__ = [
    "AIImageProvider",
    "BaseVisualProvider",
    "CreditsManager",
    "FakeVisualProvider",
    "GlobalMediaCache",
    "LocalLibraryManager",
    "LocalVisualProvider",
    "ShotClipBuilder",
    "StockVisualProvider",
    "VisualAsset",
    "VisualCandidate",
    "VisualConfigurationError",
    "VisualError",
    "VisualService",
    "compute_file_content_hash",
    "generate_chapter_contact_sheet",
    "verify_clip_stream_parameters",
]
