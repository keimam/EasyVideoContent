"""Generate and validate `packaging/release/latest.json` for EasyFaceless Studio releases (R1.2)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.branding.constants import APP_VERSION

DEFAULT_OUTPUT_PATH = REPO_ROOT / "packaging" / "release" / "latest.json"
DEFAULT_CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
REQUIRED_FIELDS = ("version", "released", "notes_id", "notes_en", "download_url")


def extract_changelog_bullets(changelog_path: Path, version: str, *, max_items: int = 5) -> List[str]:
    """Extract concise release summary lines for `## [<version>]` from `CHANGELOG.md`."""
    if not changelog_path.is_file():
        return []
    text = changelog_path.read_text(encoding="utf-8")
    header_pat = re.compile(rf"^##\s+\[{re.escape(version)}\]", re.MULTILINE)
    m = header_pat.search(text)
    if not m:
        return []

    rest = text[m.end() :]
    next_h2 = re.search(r"^##\s+", rest, re.MULTILINE)
    section = rest[: next_h2.start()] if next_h2 else rest

    bullets: List[str] = []
    for raw_ln in section.splitlines():
        ln = raw_ln.strip()
        if ln.startswith("### "):
            heading = ln[4:].strip()
            if heading:
                bullets.append(heading)
        elif ln.startswith("- **") and ":**" in ln:
            title_part = ln[2:].split(":**", 1)[0].replace("**", "").replace("`", "").strip()
            if title_part:
                bullets.append(title_part)
        if len(bullets) >= max_items:
            break
    return bullets


def build_latest_manifest_payload(
    *,
    download_url: str,
    version: Optional[str] = None,
    released: Optional[str] = None,
    notes_id: Optional[str] = None,
    notes_en: Optional[str] = None,
    min_supported: Optional[str] = None,
    changelog_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Construct release manifest payload from `constants.py`, `CHANGELOG.md`, and `--download-url`."""
    ver = str(version or APP_VERSION).strip()
    if not ver:
        raise ValueError("Application version must not be empty.")
    dl = str(download_url or "").strip()
    if not dl:
        raise ValueError("--download-url must not be empty.")
    rel_date = str(released or date.today().isoformat()).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", rel_date):
        raise ValueError(f"Invalid release date '{rel_date}' (expected YYYY-MM-DD).")

    cl_path = Path(changelog_path) if changelog_path else DEFAULT_CHANGELOG_PATH
    bullets = extract_changelog_bullets(cl_path, ver)

    if not notes_id:
        if bullets:
            notes_id = f"Rilis EasyFaceless Studio v{ver}: " + "; ".join(bullets) + "."
        else:
            notes_id = f"Rilis resmi EasyFaceless Studio v{ver}."
    if not notes_en:
        notes_en = (
            f"EasyFaceless Studio v{ver} release: first-run setup wizard, update checker, "
            "CapCut version detection, keyless stock footage sources, and Windows desktop installer."
        )

    payload: Dict[str, Any] = {
        "version": ver,
        "released": rel_date,
        "notes_id": str(notes_id).strip(),
        "notes_en": str(notes_en).strip(),
        "download_url": dl,
    }
    if min_supported and str(min_supported).strip():
        payload["min_supported"] = str(min_supported).strip()
    return payload


def validate_latest_json_file(path: Path, expected_version: str = APP_VERSION) -> Dict[str, Any]:
    """Validate that `path` is UTF-8 without BOM, valid JSON dict, has required fields, and matches `expected_version`."""
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
    changelog_path: Path = DEFAULT_CHANGELOG_PATH,
) -> Path:
    """Write and validate `latest.json` as UTF-8 (without BOM)."""
    payload = build_latest_manifest_payload(
        download_url=download_url,
        version=APP_VERSION,
        released=released,
        notes_id=notes_id,
        notes_en=notes_en,
        min_supported=min_supported,
        changelog_path=changelog_path,
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
        )
        print(f"[LATEST.JSON WRITTEN & VALIDATED] {written} (version={APP_VERSION})")
        return 0
    except Exception as exc:
        print(f"[LATEST.JSON ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
