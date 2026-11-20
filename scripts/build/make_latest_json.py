"""Generate and validate `packaging/release/latest.json` for EasyFaceless Studio releases (R1.2, R1.7)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.branding.constants import APP_VERSION

DEFAULT_OUTPUT_PATH = REPO_ROOT / "packaging" / "release" / "latest.json"
DEFAULT_NOTES_DIR = REPO_ROOT / "packaging" / "release" / "notes"
REQUIRED_FIELDS = ("version", "released", "notes_id", "notes_en", "download_url")

# R1.7: Member-facing update banner notes must never contain internal milestone/task codes.
INTERNAL_NOTE_PATTERN = re.compile(
    r"(?:\b(?:tahap|stage|addendum)\b|\bR\d+(?:\.\d+)?\b|\b[ED]\d+(?:\.\d+)?\b)",
    re.IGNORECASE,
)


def validate_member_release_note(text: str, *, label: str) -> str:
    """Validate member-facing release note text is non-empty and free of internal IDs/stages (R1.7)."""
    cleaned = " ".join(line.strip() for line in str(text or "").splitlines() if line.strip()).strip()
    if not cleaned:
        raise ValueError(f"Release note '{label}' must not be empty.")
    match = INTERNAL_NOTE_PATTERN.search(cleaned)
    if match:
        raise ValueError(
            f"Release note '{label}' contains internal reference '{match.group(0)}'; "
            "use plain member-friendly language only."
        )
    return cleaned


def load_member_release_notes(
    version: str,
    *,
    notes_dir: Path = DEFAULT_NOTES_DIR,
) -> Tuple[str, str]:
    """Load and validate `<version>.id.md` and `<version>.en.md` from `notes_dir` (R1.7)."""
    ver = str(version or "").strip()
    base = Path(notes_dir)
    id_file = base / f"{ver}.id.md"
    en_file = base / f"{ver}.en.md"
    if not id_file.is_file():
        raise FileNotFoundError(f"Missing Indonesian member release notes file for v{ver}: {id_file}")
    if not en_file.is_file():
        raise FileNotFoundError(f"Missing English member release notes file for v{ver}: {en_file}")

    notes_id = validate_member_release_note(id_file.read_text(encoding="utf-8"), label=id_file.name)
    notes_en = validate_member_release_note(en_file.read_text(encoding="utf-8"), label=en_file.name)
    return notes_id, notes_en


def build_latest_manifest_payload(
    *,
    download_url: str,
    version: Optional[str] = None,
    released: Optional[str] = None,
    notes_id: Optional[str] = None,
    notes_en: Optional[str] = None,
    min_supported: Optional[str] = None,
    notes_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Construct release manifest payload from `constants.py`, member notes files, and `--download-url`."""
    ver = str(version or APP_VERSION).strip()
    if not ver:
        raise ValueError("Application version must not be empty.")
    dl = str(download_url or "").strip()
    if not dl:
        raise ValueError("--download-url must not be empty.")
    rel_date = str(released or date.today().isoformat()).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", rel_date):
        raise ValueError(f"Invalid release date '{rel_date}' (expected YYYY-MM-DD).")

    resolved_notes_dir = Path(notes_dir) if notes_dir else DEFAULT_NOTES_DIR
    if notes_id is None or notes_en is None:
        loaded_id, loaded_en = load_member_release_notes(ver, notes_dir=resolved_notes_dir)
        final_id = validate_member_release_note(notes_id, label="notes_id") if notes_id is not None else loaded_id
        final_en = validate_member_release_note(notes_en, label="notes_en") if notes_en is not None else loaded_en
    else:
        final_id = validate_member_release_note(notes_id, label="notes_id")
        final_en = validate_member_release_note(notes_en, label="notes_en")

    payload: Dict[str, Any] = {
        "version": ver,
        "released": rel_date,
        "notes_id": final_id,
        "notes_en": final_en,
        "download_url": dl,
    }
    if min_supported and str(min_supported).strip():
        payload["min_supported"] = str(min_supported).strip()
    return payload


def validate_latest_json_file(path: Path, expected_version: str = APP_VERSION) -> Dict[str, Any]:
    """Validate that `path` is UTF-8 without BOM, valid JSON dict, free of internal jargon, and matches `expected_version`."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"latest.json file does not exist: {target}")
    raw = target.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"{target} contains a UTF-8 BOM; must be plain UTF-8 without BOM.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid JSON in {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Manifest root in {target} must be a JSON object.")
    for field in REQUIRED_FIELDS:
        if not str(data.get(field) or "").strip():
            raise ValueError(f"Missing or empty required field '{field}' in {target}.")
    validate_member_release_note(str(data["notes_id"]), label="notes_id")
    validate_member_release_note(str(data["notes_en"]), label="notes_en")
    if str(data["version"]).strip() != str(expected_version).strip():
        raise ValueError(
            f"Version mismatch in {target}: got '{data['version']}', expected '{expected_version}'."
        )
    return data


def write_latest_json(
    *,
    download_url: str,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    released: Optional[str] = None,
    notes_id: Optional[str] = None,
    notes_en: Optional[str] = None,
    min_supported: Optional[str] = None,
    notes_dir: Path = DEFAULT_NOTES_DIR,
) -> Path:
    """Write and validate `latest.json` as UTF-8 (without BOM)."""
    payload = build_latest_manifest_payload(
        download_url=download_url,
        version=APP_VERSION,
        released=released,
        notes_id=notes_id,
        notes_en=notes_en,
        min_supported=min_supported,
        notes_dir=notes_dir,
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    out.write_bytes(content.encode("utf-8"))
    validate_latest_json_file(out, expected_version=APP_VERSION)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and validate EasyFaceless Studio latest.json manifest.")
    parser.add_argument("--download-url", required=False, help="Public Google Drive or installer download URL")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Output JSON file path")
    parser.add_argument("--notes-dir", default=str(DEFAULT_NOTES_DIR), help="Directory containing <version>.id.md and <version>.en.md")
    parser.add_argument("--released", default=None, help="Release date YYYY-MM-DD (default: today)")
    parser.add_argument("--notes-id", default=None, help="Optional Indonesian notes override")
    parser.add_argument("--notes-en", default=None, help="Optional English notes override")
    parser.add_argument("--min-supported", default=None, help="Optional minimum supported version string")
    parser.add_argument("--validate-only", action="store_true", help="Only validate an existing latest.json file")
    args = parser.parse_args(argv)

    out_path = Path(args.output)
    try:
        if args.validate_only:
            data = validate_latest_json_file(out_path, expected_version=APP_VERSION)
            print(f"[LATEST.JSON VALID] {out_path} (version={data['version']})")
            return 0
        if not args.download_url:
            parser.error("--download-url is required unless --validate-only is specified")
        written = write_latest_json(
            download_url=args.download_url,
            output_path=out_path,
            released=args.released,
            notes_id=args.notes_id,
            notes_en=args.notes_en,
            min_supported=args.min_supported,
            notes_dir=Path(args.notes_dir),
        )
        print(f"[LATEST.JSON WRITTEN & VALIDATED] {written} (version={APP_VERSION})")
        return 0
    except Exception as exc:
        print(f"[LATEST.JSON ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
