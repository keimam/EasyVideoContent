"""Verify staged Windows installer package hygiene (Stage 8C.2, D5).

Fails non-zero if any forbidden developer/runtime artifact, personal secret/API key,
or developer checkout path is found inside the staging folder.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from pathlib import Path
from typing import List


FORBIDDEN_TOP_OR_NESTED_NAMES = {
    ".git",
    ".venv",
    "storage",
    "_trash",
    "test",
    "tests",
    "dist",
    "build",
    "config.toml",
    ".pytest_cache",
    "__pycache__",
}

FORBIDDEN_SUBPATHS = (
    "docs/branding/screens",
    "storage/longform_projects",
    "storage/longform_cache",
    "storage/presets",
)

FORBIDDEN_FILE_PATTERNS = (
    "*.log",
    "*.backup*",
    "*.bak",
    "*.pyc",
    "*.pyo",
    "*_editable*.pth",
)

SECRET_TOKEN_RE = re.compile(
    r"\b(sk-(?:or-v1-|proj-|ant-)[a-zA-Z0-9_\-]{12,}|AIza[0-9A-Za-z_\-]{20,}|gsk_[0-9A-Za-z_\-]{16,})"
)

TEXT_CHECK_EXTS = {
    ".py",
    ".toml",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".pth",
    ".ini",
    ".cfg",
}

REQUIRED_BUNDLE_PATHS = (
    "EasyFaceless Studio.exe",
    ".efs_installed",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "config.example.toml",
    "cli.py",
    "cli_longform.py",
    "webui/Main.py",
    "runtime/python/python.exe",
    "runtime/python/pythonw.exe",
    "runtime/ffmpeg/bin/ffmpeg.exe",
    "runtime/ffmpeg/bin/ffprobe.exe",
    "licenses/FFMPEG-GPLv3-LICENSE.txt",
    "licenses/FFMPEG-BUILD-INFO.txt",
    "docs/longform/capcut_template_8.9.0/draft_content.json",
    "docs/longform/capcut_template_8.9.0/draft_meta_info.json",
)


def verify_staging_dir(staging_dir: Path, *, check_required: bool = True) -> List[str]:
    """Return list of human-readable verification errors; empty list means clean."""
    root = Path(staging_dir).resolve()
    errors: List[str] = []
    if not root.is_dir():
        return [f"Staging directory does not exist: {root}"]

    if check_required:
        for rel in REQUIRED_BUNDLE_PATHS:
            if not (root / rel).is_file():
                errors.append(f"Missing required bundled file: {rel}")

    for sub in FORBIDDEN_SUBPATHS:
        if (root / sub).exists():
            errors.append(f"Forbidden path present in staging: {sub}")

    personal_dev_path_re = re.compile(r"F:\\MPTLong|IT SUPPORT 2", re.IGNORECASE)

    for item in root.rglob("*"):
        rel = item.relative_to(root)
        rel_posix = rel.as_posix()
        name = item.name
        name_low = name.lower()

        # site-packages legitimately contains subpackages like streamlit/runtime/caching/storage;
        # webui/.streamlit/config.toml is Streamlit theme config (not root user config.toml).
        in_site_packages = "site-packages" in rel.parts or rel_posix.startswith("runtime/python/Lib")
        if name_low == "config.toml" and rel_posix == "webui/.streamlit/config.toml":
            continue
        if name_low in FORBIDDEN_TOP_OR_NESTED_NAMES:
            if (not in_site_packages) or name_low in (".git", "config.toml", "_trash"):
                errors.append(f"Forbidden item present: {rel_posix}")
                continue

        if name_low == "cache" and not in_site_packages:
            errors.append(f"Forbidden cache folder present: {rel_posix}")
            continue

        if item.is_file():
            # Allow Python standard library/package internals where .log/.bak filenames rarely appear? Wait: still check app root/non-site-packages + always forbid config.toml / *.backup*
            for pat in FORBIDDEN_FILE_PATTERNS:
                if fnmatch.fnmatch(name_low, pat):
                    if in_site_packages and pat == "*.pyc":
                        continue
                    errors.append(f"Forbidden file pattern ({pat}): {rel_posix}")
                    break

            if item.suffix.lower() in TEXT_CHECK_EXTS and item.stat().st_size <= 2 * 1024 * 1024:
                try:
                    txt = item.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    txt = ""
                if SECRET_TOKEN_RE.search(txt):
                    errors.append(f"Potential secret/API key string leaked in {rel_posix}")
                if name_low.endswith(".pth") and personal_dev_path_re.search(txt):
                    errors.append(f"Developer machine path leaked in .pth file: {rel_posix}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify EasyFaceless Studio staging folder hygiene.")
    parser.add_argument("--staging", required=True, help="Path to staged package folder")
    parser.add_argument("--skip-required", action="store_true", help="Only check forbidden items & secrets")
    args = parser.parse_args()

    errs = verify_staging_dir(Path(args.staging), check_required=not args.skip_required)
    if errs:
        print(f"[PACKAGE VERIFY FAILED] Found {len(errs)} issue(s):", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"[PACKAGE VERIFY PASSED] Clean staging package verified at: {Path(args.staging).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
