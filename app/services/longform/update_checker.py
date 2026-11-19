"""Application version update checker with 24-hour throttle and non-blocking background startup check (Stage 8C.3, E2)."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from app.services.branding.constants import APP_VERSION, UPDATE_MANIFEST_URL
from app.services.longform import app_paths


UPDATE_STATE_FILENAME = ".efs_update_state.json"
DEFAULT_CHECK_INTERVAL_HOURS = 24.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 3.5

_BG_CHECK_LOCK = threading.Lock()
_BG_CHECK_STARTED = False


def get_update_manifest_url() -> str:
    """Return update manifest URL, honoring `EFS_UPDATE_URL` override (including `file:///`)."""
    override = os.environ.get("EFS_UPDATE_URL", "").strip()
    return override or UPDATE_MANIFEST_URL


def get_update_state_path() -> Path:
    """Return path to `.efs_update_state.json` inside `get_data_dir()`."""
    return app_paths.get_data_dir() / UPDATE_STATE_FILENAME


def _default_state() -> Dict[str, Any]:
    return {
        "auto_check_enabled": True,
        "last_checked_at": None,
        "skipped_version": None,
        "latest_manifest": None,
        "last_error": None,
    }


def read_update_state() -> Dict[str, Any]:
    """Read update checker state from `<data_dir>/.efs_update_state.json`."""
    state = _default_state()
    path = get_update_state_path()
    if not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            state.update(raw)
    except Exception as exc:
        logger.debug(f"Could not read update state '{path}': {exc}")
    return state


def write_update_state(state: Dict[str, Any]) -> Path:
    """Atomically persist update checker state into `get_data_dir()`."""
    path = get_update_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".efs_update_state.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(state, fp, indent=2, ensure_ascii=False)
            fp.write("\n")
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return path


def parse_version_tuple(version_str: Optional[str]) -> Tuple[Tuple[int, ...], str]:
    """Parse semver-like version string ('v1.0.1' -> ((1, 0, 1), ''))."""
    raw = str(version_str or "").strip()
    if raw.lower().startswith("v"):
        raw = raw[1:].strip()
    if not raw:
        return ((0, 0, 0), "")
    main_part, _, pre_part = raw.partition("-")
    nums = [int(tok) for tok in re.findall(r"\d+", main_part)]
    while len(nums) < 3:
        nums.append(0)
    return (tuple(nums), pre_part.strip().lower())


def is_newer_version(remote_version: Optional[str], current_version: str = APP_VERSION) -> bool:
    """Return True strictly when `remote_version` is newer than `current_version`."""
    if not remote_version or not str(remote_version).strip():
        return False
    rem_nums, rem_pre = parse_version_tuple(remote_version)
    cur_nums, cur_pre = parse_version_tuple(current_version)
    max_len = max(len(rem_nums), len(cur_nums))
    rem_padded = rem_nums + (0,) * (max_len - len(rem_nums))
    cur_padded = cur_nums + (0,) * (max_len - len(cur_nums))
    if rem_padded != cur_padded:
        return rem_padded > cur_padded
    # Same numeric version: stable ('') > prerelease ('rc1')
    if cur_pre and not rem_pre:
        return True
    return False


def _parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def should_run_auto_check(
    now: Optional[datetime] = None,
    *,
    interval_hours: float = DEFAULT_CHECK_INTERVAL_HOURS,
) -> bool:
    """Return True if automatic checks are enabled and >= 24 hours elapsed since last check."""
    state = read_update_state()
    if not state.get("auto_check_enabled", True):
        return False
    last_dt = _parse_iso_dt(state.get("last_checked_at"))
    if last_dt is None:
        return True
    current_dt = now or datetime.now(timezone.utc)
    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=timezone.utc)
    return (current_dt - last_dt) >= timedelta(hours=float(interval_hours))


def _normalize_manifest_url(url: str) -> str:
    cleaned = str(url or "").strip()
    if not cleaned:
        return UPDATE_MANIFEST_URL
    # Allow bare local file path in EFS_UPDATE_URL if someone passes F:\...\latest.json
    if "://" not in cleaned and Path(cleaned).exists():
        return Path(cleaned).resolve().as_uri()
    return cleaned


def fetch_update_manifest(url: str, *, timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Fetch and validate `latest.json` from HTTP(S) or `file:///` URI cleanly."""
    target_url = _normalize_manifest_url(url)
    req = urllib.request.Request(
        target_url,
        headers={"User-Agent": f"EasyFacelessStudio/{APP_VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
        raw_bytes = resp.read()
    data = json.loads(raw_bytes.decode("utf-8"))
    if not isinstance(data, dict) or not str(data.get("version") or "").strip():
        raise ValueError("Invalid update manifest: missing 'version' field.")
    return {
        "version": str(data.get("version") or "").strip(),
        "released": str(data.get("released") or "").strip(),
        "notes_id": str(data.get("notes_id") or data.get("notes") or "").strip(),
        "notes_en": str(data.get("notes_en") or data.get("notes") or "").strip(),
        "download_url": str(data.get("download_url") or "").strip(),
        "min_supported": str(data.get("min_supported") or "").strip() or None,
    }


def build_update_result(state: Dict[str, Any], *, checked_now: bool = False, error: Optional[str] = None) -> Dict[str, Any]:
    """Construct standardized update status payload from cached/live state."""
    manifest = state.get("latest_manifest") if isinstance(state.get("latest_manifest"), dict) else None
    remote_ver = str((manifest or {}).get("version") or "").strip() or None
    has_update = bool(remote_ver and is_newer_version(remote_ver, APP_VERSION))
    skipped_ver = str(state.get("skipped_version") or "").strip() or None
    is_skipped = bool(has_update and skipped_ver and remote_ver == skipped_ver)

    return {
        "current_version": APP_VERSION,
        "remote_version": remote_ver,
        "has_update": has_update,
        "is_skipped": is_skipped,
        "show_banner": bool(has_update and not is_skipped),
        "manifest": manifest,
        "auto_check_enabled": bool(state.get("auto_check_enabled", True)),
        "last_checked_at": state.get("last_checked_at"),
        "skipped_version": skipped_ver,
        "checked_now": checked_now,
        "error": error,
    }


def check_for_updates(
    *,
    force: bool = False,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    now: Optional[datetime] = None,
    manifest_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Check for a newer EasyFaceless Studio release, obeying 24-hour throttle unless `force=True`."""
    state = read_update_state()
    if not force and not should_run_auto_check(now=now):
        return build_update_result(state, checked_now=False, error=None)

    current_dt = now or datetime.now(timezone.utc)
    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=timezone.utc)

    target_url = manifest_url or get_update_manifest_url()
    try:
        manifest = fetch_update_manifest(target_url, timeout=timeout)
        state["latest_manifest"] = manifest
        state["last_checked_at"] = current_dt.isoformat()
        state["last_error"] = None
        write_update_state(state)
        return build_update_result(state, checked_now=True, error=None)
    except Exception as exc:
        logger.debug(f"App update check skipped/offline ({target_url}): {exc}")
        # Update last_checked_at even on failure when running auto-check to prevent tight retry loops
        state["last_checked_at"] = current_dt.isoformat()
        state["last_error"] = str(exc)
        write_update_state(state)
        return build_update_result(state, checked_now=True, error=str(exc))


def skip_update_version(version: str) -> Dict[str, Any]:
    """Persist `skipped_version` so the banner for that version stays hidden."""
    state = read_update_state()
    state["skipped_version"] = str(version or "").strip() or None
    write_update_state(state)
    return build_update_result(state, checked_now=False)


def set_auto_check_enabled(enabled: bool) -> Dict[str, Any]:
    """Enable or disable automatic startup update checks."""
    state = read_update_state()
    state["auto_check_enabled"] = bool(enabled)
    write_update_state(state)
    return build_update_result(state, checked_now=False)


def get_active_update_banner() -> Optional[Dict[str, Any]]:
    """Return update manifest dict if cached check shows a newer unskipped version."""
    state = read_update_state()
    info = build_update_result(state, checked_now=False)
    if info["show_banner"]:
        return info
    return None


def start_background_update_check(*, timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS) -> bool:
    """Trigger one-time non-blocking background update check per 24h throttle."""
    global _BG_CHECK_STARTED
    if os.environ.get("PYTEST_CURRENT_TEST", "").strip() and not os.environ.get("EFS_UPDATE_URL", "").strip():
        return False
    if not should_run_auto_check():
        return False
    with _BG_CHECK_LOCK:
        if _BG_CHECK_STARTED:
            return False
        _BG_CHECK_STARTED = True

    manifest_url = get_update_manifest_url()
    normalized = _normalize_manifest_url(manifest_url)
    # Local file:/// test manifests resolve in <5ms; run inline so banner shows immediately
    if normalized.lower().startswith("file:"):
        try:
            check_for_updates(force=False, timeout=timeout, manifest_url=manifest_url)
        finally:
            with _BG_CHECK_LOCK:
                _BG_CHECK_STARTED = False
        return True

    def _worker() -> None:
        try:
            check_for_updates(force=False, timeout=timeout, manifest_url=manifest_url)
        except Exception as exc:
            logger.debug(f"Background update worker ignored error: {exc}")
        finally:
            with _BG_CHECK_LOCK:
                _BG_CHECK_STARTED = False

    threading.Thread(target=_worker, name="efs-update-check", daemon=True).start()
    return True
