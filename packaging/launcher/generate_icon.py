"""Generate multi-size Windows .ico file from EasyFaceless Studio PNG logo (D3, D4)."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def build_ico(src_png: Path, dst_ico: Path) -> Path:
    src = Path(src_png).resolve()
    dst = Path(dst_ico).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Source PNG not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert("RGBA")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(dst, format="ICO", sizes=sizes)
    return dst


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Build app.ico from logo.png")
    parser.add_argument("--src", default=str(repo_root / "resource" / "branding" / "logo.png"))
    parser.add_argument("--dst", default=str(repo_root / "resource" / "branding" / "app.ico"))
    args = parser.parse_args()
    out = build_ico(Path(args.src), Path(args.dst))
    print(f"Generated icon: {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
