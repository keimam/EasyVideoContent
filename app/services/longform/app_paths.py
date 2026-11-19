"""Unified application & user-data directory resolution for EasyFaceless Studio (Stage 8C.2, D2).

Rules:
- Explicit override: ``EFS_DATA_DIR`` env var always selects the user data directory.
- Source checkout mode (running from git repo with ``pyproject.toml`` + ``.git`` / ``uv.lock``):
  keeps data next to the repository (``<repo>/config.toml``, ``<repo>/storage/``) so existing
  developer workflows (``uv run streamlit run webui/Main.py``) behave identically.
- Installed desktop mode (``EFS_INSTALLED=1``, ``.efs_installed`` marker, or packaged runtime without repo markers):
  writes ALL mutable files (``config.toml``, ``storage/``, ``logs/``, lock files) into
  ``%LOCALAPPDATA%\\EasyFaceless Studio`` on Windows (or platform equivalent via ``platform_utils``),
  leaving the installation directory read-only and cleanly upgradeable.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from app.services.branding.constants import APP_NAME
from app.services.longform import platform_utils


def get_install_root() -> Path:
    """Return the root folder where EasyFaceless Studio code & read-only assets reside."""
    return Path(__file__).resolve().parents[3]


def is_installed_mode() -> bool:
    """Return True when running as an installed package rather than a git source checkout."""
    if os.environ.get("EFS_INSTALLED", "").strip().lower() in ("1", "true", "yes"):
        return True
    root = get_install_root()
    if (root / ".efs_installed").is_file():
        return True
    if os.environ.get("EFS_DATA_DIR", "").strip():
        return True
    # Source checkout is identified by pyproject.toml + (.git or uv.lock)
    has_source_markers = (root / "pyproject.toml").is_file() and (
        (root / ".git").exists() or (root / "uv.lock").is_file()
    )
    return not has_source_markers


def get_data_dir() -> Path:
    """Return canonical root directory for config.toml, storage/, cache/, trash, and logs/."""
    override = os.environ.get("EFS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    compat_home = os.environ.get("MPT_HOME", "").strip()
    if compat_home:
        return Path(compat_home).expanduser().resolve()

    if is_installed_mode():
        return platform_utils.get_user_data_dir(APP_NAME)

    return get_install_root()


def get_config_toml_path() -> Path:
    """Return absolute path to user writable ``config.toml``."""
    return get_data_dir() / "config.toml"


def get_config_example_path() -> Path:
    """Return absolute path to read-only ``config.example.toml`` shipped with app."""
    return get_install_root() / "config.example.toml"


def get_storage_dir(*subparts: str, create: bool = False) -> Path:
    """Return path inside ``<data_dir>/storage[/<subparts...>]``."""
    target = get_data_dir() / "storage"
    for part in subparts:
        if part:
            target = target / str(part)
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def get_longform_projects_dir(*, create: bool = False) -> Path:
    """Return path to ``<data_dir>/storage/longform_projects``."""
    return get_storage_dir("longform_projects", create=create)


def get_custom_presets_dir(*, create: bool = False) -> Path:
    """Return path to ``<data_dir>/storage/presets``."""
    return get_storage_dir("presets", create=create)


def get_longform_cache_dir(*, create: bool = True) -> Path:
    """Return path to ``<data_dir>/storage/longform_cache``."""
    return get_storage_dir("longform_cache", create=create)


def get_logs_dir(*, create: bool = True) -> Path:
    """Return path to ``<data_dir>/logs`` for launcher and server logs."""
    d = get_data_dir() / "logs"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def get_launcher_lock_path() -> Path:
    """Return path to single-instance launcher lock file inside user data dir."""
    return get_data_dir() / ".efs_launcher.lock.json"


def get_bundled_ffmpeg_bin_dir() -> Optional[Path]:
    """Return bundled FFmpeg bin directory if present in installation folder."""
    root = get_install_root()
    for candidate in (
        root / "runtime" / "ffmpeg" / "bin",
        root / "runtime" / "ffmpeg",
        root / "ffmpeg" / "bin",
    ):
        if (candidate / "ffmpeg.exe").is_file() or (candidate / "ffmpeg").is_file():
            return candidate.resolve()
    return None


def ensure_user_data_initialized() -> Path:
    """Ensure user data directories and initial ``config.toml`` exist cleanly (D2).

    In installed mode or when ``EFS_DATA_DIR`` is explicitly set, copies
    ``config.example.toml`` to ``config.toml`` on first run (without API keys)
    if ``config.toml`` does not yet exist. Never overwrites existing ``config.toml``.
    """
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    get_longform_projects_dir(create=True)
    get_custom_presets_dir(create=True)
    get_longform_cache_dir(create=True)
    get_logs_dir(create=True)

    cfg_path = get_config_toml_path()
    example_path = get_config_example_path()
    if is_installed_mode() and not cfg_path.exists() and example_path.is_file():
        fd, tmp_name = tempfile.mkstemp(prefix=".cfg_init_", dir=str(data_dir))
        try:
            with os.fdopen(fd, "wb") as dst, example_path.open("rb") as src:
                shutil.copyfileobj(src, dst)
            os.replace(tmp_name, cfg_path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    return data_dir


def bootstrap_runtime_environment() -> None:
    """Prepare bundled FFmpeg PATH and initialized user data directory on startup."""
    ff_bin = get_bundled_ffmpeg_bin_dir()
    if ff_bin is not None:
        ff_str = str(ff_bin)
        cur_path = os.environ.get("PATH", "")
        parts = cur_path.split(os.pathsep) if cur_path else []
        if ff_str not in parts:
            os.environ["PATH"] = f"{ff_str}{os.pathsep}{cur_path}" if cur_path else ff_str
    if is_installed_mode():
        ensure_user_data_initialized()
