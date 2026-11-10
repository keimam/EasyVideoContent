"""Project Store and Manifest Persistence for Longform Video Generation.

Handles project directory creation, atomic manifest writes with Windows retry logic (K5),
file staging for local visual assets (K7), and single-process project locking with
stale lock takeover (K5).
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from loguru import logger

from pydantic import ValidationError

from app.services.longform.config import load_longform_config
from app.services.longform.hashing import (
    compute_audio_group_hash,
    compute_chapter_hash,
    compute_visual_segment_hash,
    compute_preset_snapshot_hash,
    hash_file,
)
from app.services.longform.presets import get_preset, get_preset_inheritance_chain
from app.services.longform.schema import Chapter, ItemStatus, ProjectManifest, Segment, TtsGroup


class ProjectStoreError(Exception):
    """Base exception for project store operations."""
    pass


class ProjectLockedError(ProjectStoreError):
    """Raised when an operation cannot proceed because the project is locked by another active process."""
    pass


class ProjectNotFoundError(ProjectStoreError):
    """Raised when a requested project directory or manifest does not exist."""
    pass


def is_pid_alive(pid: int) -> bool:
    """Check whether a given PID is still running.

    Safe for Windows (uses Win32 OpenProcess/GetExitCodeProcess) and POSIX.
    """
    if pid <= 0:
        return False

    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


class ProjectLock:
    """Advisory file lock for longform project directory (K5).

    Creates a '.lock' file containing PID, hostname, and timestamp.
    If a lock already exists, verifies if the process is alive. Stale locks
    from deceased processes are automatically reclaimed.
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir).resolve()
        self.lock_path = self.project_dir / ".lock"
        self._acquired = False

    def acquire(self) -> None:
        """Acquire the lock or raise ProjectLockedError."""
        self.project_dir.mkdir(parents=True, exist_ok=True)

        if self.lock_path.is_file():
            lock_info = self._read_lock_file()
            pid = lock_info.get("pid")
            created_at = lock_info.get("created_at", "unknown")

            if pid and isinstance(pid, int) and is_pid_alive(pid):
                raise ProjectLockedError(
                    f"Project '{self.project_dir.name}' is currently locked by PID {pid} "
                    f"(since {created_at}). Another active process is working on this project."
                )
            else:
                logger.warning(
                    f"Stale lock found on project '{self.project_dir.name}' (PID: {pid}). "
                    f"Process is dead; breaking and reclaiming lock."
                )
                try:
                    self.lock_path.unlink()
                except Exception as exc:
                    logger.warning(f"Could not remove stale lock file: {exc}")

        # Write lock file
        info = {
            "pid": os.getpid(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
        }
        try:
            with open(self.lock_path, "w", encoding="utf-8") as fp:
                json.dump(info, fp, indent=2)
            self._acquired = True
        except Exception as exc:
            raise ProjectStoreError(f"Failed to create lock file at {self.lock_path}: {exc}") from exc

    def release(self) -> None:
        """Release the lock by deleting the .lock file."""
        if self._acquired and self.lock_path.is_file():
            try:
                # Only delete if it belongs to our PID
                lock_info = self._read_lock_file()
                if lock_info.get("pid") == os.getpid():
                    self.lock_path.unlink()
            except Exception as exc:
                logger.warning(f"Failed to release lock file '{self.lock_path}': {exc}")
            finally:
                self._acquired = False

    def _read_lock_file(self) -> dict[str, Any]:
        try:
            with open(self.lock_path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            return {}

    def __enter__(self) -> "ProjectLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


STOP_MARKER_FILENAME = ".stop_requested"


def request_job_stop(project_dir: str | Path) -> Path:
    """Write an advisory stop marker file in the project directory (D2)."""
    p_dir = Path(project_dir).resolve()
    p_dir.mkdir(parents=True, exist_ok=True)
    stop_file = p_dir / STOP_MARKER_FILENAME
    stop_file.write_text(f"stop_requested_at: {datetime.now().isoformat()}\n", encoding="utf-8")
    return stop_file


def is_stop_requested(project_dir: str | Path) -> bool:
    """Check if a stop has been requested for this project (D2)."""
    p_dir = Path(project_dir).resolve()
    return (p_dir / STOP_MARKER_FILENAME).is_file()


def clear_stop_request(project_dir: str | Path) -> None:
    """Remove any advisory stop marker file in the project directory (D2)."""
    p_dir = Path(project_dir).resolve()
    stop_file = p_dir / STOP_MARKER_FILENAME
    if stop_file.is_file():
        stop_file.unlink(missing_ok=True)


def get_projects_root() -> Path:
    """Return the absolute path to storage/longform_projects/."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    return repo_root / "storage" / "longform_projects"


def get_project_dir(project_id: str) -> Path:
    """Return absolute path for a specific project ID."""
    return get_projects_root() / project_id


def save_manifest(manifest: ProjectManifest, project_dir: str | Path) -> Path:
    """Atomically save manifest.json with fsync and Windows retry logic (K5)."""
    p_dir = Path(project_dir).resolve()
    target_path = p_dir / "manifest.json"
    temp_path = p_dir / f"manifest.json.tmp.{uuid.uuid4().hex}"

    payload = manifest.model_dump_json(indent=2).encode("utf-8")

    # 1. Write to temporary file with flush & fsync
    try:
        with open(temp_path, "wb") as fp:
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise ProjectStoreError(f"Failed to write temporary manifest '{temp_path}': {exc}") from exc

    # 2. Atomic replacement with retries for Windows PermissionError
    config = load_longform_config()
    retries = max(1, config.atomic_retry_count)
    delay = max(0.01, config.atomic_retry_delay_seconds)

    for attempt in range(retries):
        try:
            os.replace(temp_path, target_path)
            return target_path
        except PermissionError as exc:
            if attempt == retries - 1:
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)
                raise ProjectStoreError(
                    f"Failed to atomically replace manifest at '{target_path}' "
                    f"after {retries} retries due to PermissionError: {exc}"
                ) from exc
            time.sleep(delay * (attempt + 1))
        except Exception as exc:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise ProjectStoreError(f"Unexpected error replacing manifest '{target_path}': {exc}") from exc

    return target_path


def load_manifest(project_dir: str | Path) -> ProjectManifest:
    """Load and validate manifest.json from project directory.

    BOM-safe (utf-8-sig). Detailed line/column on JSON syntax error (A2).
    Field-level validation error reporting. Keeps paragraph_text synchronized (A1).
    Does NOT overwrite file on error.
    """
    p_dir = Path(project_dir).resolve()
    manifest_path = p_dir / "manifest.json"

    if not manifest_path.is_file():
        raise ProjectNotFoundError(f"Manifest file not found at '{manifest_path}'")

    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as fp:
            data = json.load(fp)
    except json.JSONDecodeError as exc:
        raise ProjectStoreError(
            f"Invalid JSON syntax in manifest '{manifest_path}' at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    except Exception as exc:
        raise ProjectStoreError(f"Failed to read manifest JSON at '{manifest_path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ProjectStoreError(
            f"Manifest file '{manifest_path}' must contain a JSON object at root, got {type(data).__name__}"
        )

    try:
        manifest = ProjectManifest.model_validate(data)
        # Synchronize paragraph_text with segments (A1) and clean chapter title prefixes (E1)
        from app.services.longform.script_import import clean_chapter_title
        for ch in manifest.chapters:
            if ch.chapter_title:
                ch.chapter_title = clean_chapter_title(ch.chapter_title, fallback_index=ch.chapter_index)
            for g in ch.tts_groups:
                if g.segments:
                    g.paragraph_text = g.get_effective_paragraph_text()
        return manifest
    except ValidationError as exc:
        errors = [
            f"Field '{' -> '.join(str(p) for p in err.get('loc', []))}': {err.get('msg')}"
            for err in exc.errors()
        ]
        details = "\n  - " + "\n  - ".join(errors)
        raise ProjectStoreError(f"Manifest validation failed for '{manifest_path}':{details}") from exc


def create_project(
    project_id: str,
    title: str,
    preset_id: str,
    script_markdown: Optional[str] = None,
    aspect_ratio: str = "16:9",
    sub_niche_focus: Optional[str] = None,
    topic: Optional[str] = None,
    target_duration_minutes: Optional[int] = None,
    language: Optional[str] = None,
    projects_root: Optional[Path] = None,
) -> Tuple[ProjectManifest, Path]:
    """Create a new longform project folder with directory structure and manifest.

    Copies preset snapshot and inheritance chain into manifest (K2, B2, B3, D7).
    """
    clean_id = project_id.strip()
    if not clean_id or not clean_id.replace("_", "").replace("-", "").isalnum():
        raise ValueError(
            f"Invalid project_id '{project_id}'. Must contain alphanumeric, underscore, or hyphen."
        )

    base_root = Path(projects_root).resolve() if projects_root else get_projects_root()
    project_dir = base_root / clean_id
    if project_dir.exists() and (project_dir / "manifest.json").exists():
        raise FileExistsError(f"Project '{clean_id}' already exists at {project_dir}")

    # Create directory tree
    subdirs = [
        project_dir / "assets" / "visual" / "raw",
        project_dir / "assets" / "visual" / "clips",
        project_dir / "assets" / "audio" / "groups",
        project_dir / "assets" / "chapters",
        project_dir / "output",
    ]
    for d in subdirs:
        d.mkdir(parents=True, exist_ok=True)

    # Resolve generic "umum" to language-specific preset (H1)
    if preset_id == "umum":
        preset_id = "umum.en" if (language and language.startswith("en")) else "umum.id"

    # Fetch preset & snapshot
    preset = get_preset(preset_id)
    preset_snapshot = preset.model_dump()
    preset_chain = get_preset_inheritance_chain(preset_id)

    # Create chapters from script if provided
    chapters: list[Chapter] = []
    if script_markdown:
        from app.services.longform.script_import import parse_markdown_script
        chapters, _ = parse_markdown_script(script_markdown, preset)

    manifest = ProjectManifest(
        pipeline_version="1.0.0",
        schema_version="1.0.0",
        project_id=clean_id,
        title=title,
        topic=topic.strip() if topic else None,
        target_duration_minutes=target_duration_minutes,
        aspect_ratio=aspect_ratio,
        preset_id=preset_id,
        preset_inheritance_chain=preset_chain,
        preset_snapshot=preset_snapshot,
        sub_niche_focus=sub_niche_focus.strip() if sub_niche_focus else None,
        chapters=chapters,
        status=ItemStatus.pending,
    )

    with ProjectLock(project_dir):
        save_manifest(manifest, project_dir)

    return manifest, project_dir


def update_project_script(
    project_dir: str | Path,
    new_script_markdown: str,
) -> dict[str, Any]:
    """Re-import script into an existing project, preserving unaffected units (A4).

    Hierarchical position-based matching:
    - Matches chapters by chapter_index (1-based).
    - Matches TTS groups by group_index (1-based).
    - Matches segments by segment_index within group (1-based).
    - Unchanged segments preserve rendered_clip, visual_hash, and completed status.
    - Unchanged groups preserve audio file_path, audio_hash, and completed status.
    - Modified items are marked as stale (or pending if not yet rendered).
    - Returns structured summary of modifications.
    """
    p_dir = Path(project_dir).resolve()

    with ProjectLock(p_dir):
        manifest = load_manifest(p_dir)
        preset = get_preset(manifest.preset_id)

        from app.services.longform.script_import import parse_markdown_script
        new_chapters, _ = parse_markdown_script(new_script_markdown, preset)

        stats = {
            "chapters_unchanged": 0,
            "chapters_modified": 0,
            "chapters_added": 0,
            "chapters_removed": 0,
            "groups_unchanged": 0,
            "groups_modified": 0,
            "groups_added": 0,
            "groups_removed": 0,
            "segments_unchanged": 0,
            "segments_modified": 0,
            "segments_added": 0,
            "segments_removed": 0,
        }

        old_chapters_map = {c.chapter_index: c for c in manifest.chapters}
        new_chapters_map = {c.chapter_index: c for c in new_chapters}
        all_ch_indices = sorted(set(old_chapters_map.keys()) | set(new_chapters_map.keys()))

        updated_chapters: list[Chapter] = []

        for ch_idx in all_ch_indices:
            if ch_idx in old_chapters_map and ch_idx not in new_chapters_map:
                # Chapter removed
                old_ch = old_chapters_map[ch_idx]
                stats["chapters_removed"] += 1
                stats["groups_removed"] += len(old_ch.tts_groups)
                stats["segments_removed"] += sum(len(g.segments) for g in old_ch.tts_groups)
                continue

            if ch_idx not in old_chapters_map and ch_idx in new_chapters_map:
                # Chapter added
                new_ch = new_chapters_map[ch_idx]
                stats["chapters_added"] += 1
                stats["groups_added"] += len(new_ch.tts_groups)
                stats["segments_added"] += sum(len(g.segments) for g in new_ch.tts_groups)
                updated_chapters.append(new_ch)
                continue

            # Chapter exists in both old and new
            old_ch = old_chapters_map[ch_idx]
            new_ch = new_chapters_map[ch_idx]

            ch_modified = (old_ch.chapter_title.strip() != new_ch.chapter_title.strip())

            old_groups_map = {g.group_index: g for g in old_ch.tts_groups}
            new_groups_map = {g.group_index: g for g in new_ch.tts_groups}
            all_g_indices = sorted(set(old_groups_map.keys()) | set(new_groups_map.keys()))

            updated_groups: list[TtsGroup] = []

            for g_idx in all_g_indices:
                if g_idx in old_groups_map and g_idx not in new_groups_map:
                    # Group removed
                    old_g = old_groups_map[g_idx]
                    stats["groups_removed"] += 1
                    stats["segments_removed"] += len(old_g.segments)
                    ch_modified = True
                    continue

                if g_idx not in old_groups_map and g_idx in new_groups_map:
                    # Group added
                    new_g = new_groups_map[g_idx]
                    stats["groups_added"] += 1
                    stats["segments_added"] += len(new_g.segments)
                    updated_groups.append(new_g)
                    ch_modified = True
                    continue

                # Group exists in both
                old_g = old_groups_map[g_idx]
                new_g = new_groups_map[g_idx]

                g_modified = False

                old_segs_map = {s.segment_index: s for s in old_g.segments}
                new_segs_map = {s.segment_index: s for s in new_g.segments}
                all_s_indices = sorted(set(old_segs_map.keys()) | set(new_segs_map.keys()))

                updated_segments: list[Segment] = []

                for s_idx in all_s_indices:
                    if s_idx in old_segs_map and s_idx not in new_segs_map:
                        stats["segments_removed"] += 1
                        g_modified = True
                        continue

                    if s_idx not in old_segs_map and s_idx in new_segs_map:
                        new_s = new_segs_map[s_idx]
                        stats["segments_added"] += 1
                        updated_segments.append(new_s)
                        g_modified = True
                        continue

                    # Segment exists in both
                    old_s = old_segs_map[s_idx]
                    new_s = new_segs_map[s_idx]

                    text_same = (old_s.narration_text.strip() == new_s.narration_text.strip())
                    prompt_same = (old_s.visual.prompt.strip() == new_s.visual.prompt.strip())
                    motion_same = (old_s.visual.motion == new_s.visual.motion)
                    source_same = (old_s.visual.type == new_s.visual.type)

                    if text_same and prompt_same and motion_same and source_same:
                        stats["segments_unchanged"] += 1
                        # Preserve existing rendered asset and status
                        new_s.visual.rendered_clip = old_s.visual.rendered_clip
                        new_s.visual.visual_hash = old_s.visual.visual_hash
                        new_s.visual.source_file = old_s.visual.source_file
                        new_s.visual.source_content_hash = old_s.visual.source_content_hash
                        new_s.duration_seconds = old_s.duration_seconds
                        new_s.status = old_s.status
                    else:
                        stats["segments_modified"] += 1
                        g_modified = True
                        # Preserve old hash so planner detects mismatch with new expected hash
                        new_s.visual.rendered_clip = old_s.visual.rendered_clip
                        new_s.visual.visual_hash = old_s.visual.visual_hash
                        new_s.status = ItemStatus.stale if old_s.visual.rendered_clip else ItemStatus.pending

                    updated_segments.append(new_s)

                new_g.segments = updated_segments
                new_g.paragraph_text = new_g.get_effective_paragraph_text()

                if not g_modified:
                    stats["groups_unchanged"] += 1
                    new_g.audio.file_path = old_g.audio.file_path
                    new_g.audio.audio_hash = old_g.audio.audio_hash
                    new_g.status = old_g.status
                else:
                    stats["groups_modified"] += 1
                    ch_modified = True
                    new_g.audio.file_path = old_g.audio.file_path
                    # Preserve old audio hash so planner detects mismatch with new expected text
                    new_g.audio.audio_hash = old_g.audio.audio_hash
                    new_g.status = ItemStatus.stale if old_g.audio.file_path else ItemStatus.pending

                updated_groups.append(new_g)

            new_ch.tts_groups = updated_groups

            if not ch_modified:
                stats["chapters_unchanged"] += 1
                new_ch.output_file = old_ch.output_file
                new_ch.chapter_hash = old_ch.chapter_hash
                new_ch.status = old_ch.status
            else:
                stats["chapters_modified"] += 1
                new_ch.output_file = old_ch.output_file
                new_ch.status = ItemStatus.stale if old_ch.output_file else ItemStatus.pending

            updated_chapters.append(new_ch)

        manifest.chapters = updated_chapters
        if stats["chapters_modified"] > 0 or stats["chapters_added"] > 0 or stats["chapters_removed"] > 0:
            manifest.status = ItemStatus.stale if manifest.final_video_file else ItemStatus.pending

        save_manifest(manifest, p_dir)

    return stats


def refresh_project_preset(project_dir: str | Path) -> Tuple[ProjectManifest, dict[str, Any]]:
    """Refresh project preset snapshot from current preset definitions on disk (B2).

    Updates preset_snapshot and preset_inheritance_chain.
    Affected chapters will be flagged as stale by compute_plan.
    """
    p_dir = Path(project_dir).resolve()

    with ProjectLock(p_dir):
        manifest = load_manifest(p_dir)
        old_chain = list(manifest.preset_inheritance_chain)
        old_snapshot = dict(manifest.preset_snapshot)

        latest_preset = get_preset(manifest.preset_id)

        # Guard: check language compatibility between project and target preset (H1)
        from app.services.longform.presets import PresetValidationError
        proj_lang = (manifest.preset_snapshot or {}).get("language")
        preset_lang = getattr(latest_preset, "language", None)
        if proj_lang and preset_lang:
            proj_norm = "en" if str(proj_lang).lower().startswith("en") else ("id" if str(proj_lang).lower().startswith("id") else "")
            preset_norm = "en" if str(preset_lang).lower().startswith("en") else ("id" if str(preset_lang).lower().startswith("id") else "")
            if proj_norm and preset_norm and proj_norm != preset_norm:
                raise PresetValidationError(
                    f"Tidak dapat memperbarui preset: perbedaan bahasa terdeteksi (proyek: '{proj_lang}' vs preset: '{preset_lang}'). "
                    f"Perubahan bahasa narasi harus dilakukan secara eksplisit terpisah."
                )

        latest_chain = get_preset_inheritance_chain(manifest.preset_id)
        latest_snapshot = latest_preset.model_dump()

        manifest.preset_snapshot = latest_snapshot
        manifest.preset_inheritance_chain = latest_chain
        # Reset completed status to stale if render exists
        if manifest.final_video_file:
            manifest.status = ItemStatus.stale

        save_manifest(manifest, p_dir)

        old_hash = compute_preset_snapshot_hash(old_snapshot)
        new_hash = compute_preset_snapshot_hash(latest_snapshot)
        details = {
            "preset_id": manifest.preset_id,
            "old_chain": old_chain,
            "new_chain": latest_chain,
            "preset_inheritance_chain": latest_chain,
            "old_snapshot_hash": old_hash,
            "new_snapshot_hash": new_hash,
            "snapshot_updated": (old_snapshot != latest_snapshot),
        }
        return manifest, details


def stage_local_visual_file(project_dir: str | Path, source_path: str | Path) -> Tuple[str, str]:
    """Copy a local visual file into assets/visual/raw/ and return (relative_path, content_hash) (K7).

    Guarantees project portability and stable content hashing.
    """
    p_dir = Path(project_dir).resolve()
    src = Path(source_path).resolve()

    if not src.is_file():
        raise FileNotFoundError(f"Local visual source file not found: {src}")

    content_hash = hash_file(src)
    ext = src.suffix.lower()
    # Unique safe filename preserving extension
    safe_name = f"{content_hash[:16]}_{src.stem[:32]}{ext}"
    dest_dir = p_dir / "assets" / "visual" / "raw"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / safe_name

    # Only copy if destination does not already exist
    if not dest_file.exists():
        shutil.copy2(src, dest_file)

    rel_path = f"assets/visual/raw/{safe_name}"
    return rel_path, content_hash


def migrate_umum_presets_and_projects() -> List[str]:
    """Migrate legacy 'umum' preset references to 'umum.id' / 'umum.en' and restore Tokyo project (H1, H2).

    Returns a list of log messages detailing all migrations performed.
    """
    from app.services.longform.presets import (
        get_preset,
        get_preset_directories,
        get_preset_inheritance_chain,
    )

    logs: List[str] = []
    _, storage_dir = get_preset_directories()

    # 1. Migrate custom presets in storage/presets/
    if storage_dir.is_dir():
        for f in sorted(storage_dir.glob("*.json")):
            if f.name.endswith(".override.json"):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("extends") == "umum":
                    p_lang = data.get("language", "")
                    new_ext = "umum.en" if str(p_lang).startswith("en") else "umum.id"
                    data["extends"] = new_ext
                    f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                    msg = f"[MIGRASI PRESET H1] Preset kustom '{data.get('id', f.stem)}': extends diubah dari 'umum' ke '{new_ext}'"
                    logger.info(msg)
                    logs.append(msg)
            except Exception as exc:
                logger.warning(f"Error checking preset file {f}: {exc}")

    # 2. Migrate projects in storage/longform_projects/
    root = get_projects_root()
    if root.is_dir():
        for p_dir in sorted(root.iterdir()):
            if not p_dir.is_dir() or not (p_dir / "manifest.json").is_file():
                continue
            try:
                manifest = load_manifest(p_dir)

                # H2: Restore history_of_street_food_in_tokyo
                if manifest.project_id == "history_of_street_food_in_tokyo" and manifest.sub_niche_focus is None and manifest.preset_id == "custom.kuliner.street_food_jepang":
                    old_pid = manifest.preset_id
                    manifest.preset_id = "umum.id"
                    manifest.preset_inheritance_chain = ["documentary_id", "umum.id"]
                    manifest.preset_snapshot = get_preset("umum.id").model_dump()
                    save_manifest(manifest, p_dir)
                    msg = f"[PEMULIHAN PROYEK H2] Proyek '{manifest.title}' ({manifest.project_id}): preset dipulihkan dari '{old_pid}' ke 'umum.id'"
                    logger.info(msg)
                    logs.append(msg)
                    continue

                # H1: Migrate projects with preset_id == "umum"
                if manifest.preset_id == "umum":
                    old_pid = manifest.preset_id
                    proj_lang = getattr(manifest, "language", None) or (manifest.preset_snapshot or {}).get("language", "")
                    target_pid = "umum.en" if str(proj_lang).startswith("en") else "umum.id"
                    manifest.preset_id = target_pid
                    target_preset = get_preset(target_pid)
                    manifest.preset_inheritance_chain = get_preset_inheritance_chain(target_pid)
                    manifest.preset_snapshot = target_preset.model_dump()
                    save_manifest(manifest, p_dir)
                    msg = f"[MIGRASI PROYEK H1] Proyek '{manifest.title}' ({manifest.project_id}): preset_id diubah dari '{old_pid}' ke '{target_pid}'"
                    logger.info(msg)
                    logs.append(msg)
                    continue

                # Clean inheritance chain containing "umum"
                chain = list(getattr(manifest, "preset_inheritance_chain", []) or [])
                if "umum" in chain:
                    proj_lang = getattr(manifest, "language", None) or (manifest.preset_snapshot or {}).get("language", "")
                    replacement = "umum.en" if str(proj_lang).startswith("en") else "umum.id"
                    new_chain = [replacement if x == "umum" else x for x in chain]
                    manifest.preset_inheritance_chain = new_chain
                    save_manifest(manifest, p_dir)
                    msg = f"[MIGRASI CHAIN H1] Proyek '{manifest.title}' ({manifest.project_id}): 'umum' diganti '{replacement}' pada inheritance chain"
                    logger.info(msg)
                    logs.append(msg)

            except Exception as exc:
                logger.warning(f"Error migrating project at {p_dir}: {exc}")

    return logs
