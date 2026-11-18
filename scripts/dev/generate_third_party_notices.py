"""Generate THIRD_PARTY_NOTICES.md from installed Python distributions and build assets (C4)."""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import subprocess
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "THIRD_PARTY_NOTICES.md"


def _detect_license_from_meta(dist: importlib_metadata.Distribution) -> str:
    meta = dist.metadata
    lic_expr = (meta.get("License-Expression") or "").strip()
    if lic_expr:
        return lic_expr
    lic = (meta.get("License") or "").strip()
    if lic and lic.upper() not in ("UNKNOWN", "NONE") and "\n" not in lic and len(lic) <= 60:
        return lic
    classifiers = [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    if classifiers:
        parts = [c.split(" :: ")[-1].strip() for c in classifiers]
        return "; ".join(dict.fromkeys(parts))
    if lic and lic.upper() not in ("UNKNOWN", "NONE"):
        first_line = lic.splitlines()[0].strip()
        if len(first_line) <= 60:
            return first_line
        return f"{first_line[:57]}..."
    return "See package metadata"


def _collect_python_packages() -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []
    seen = set()
    for dist in importlib_metadata.distributions():
        name = (dist.metadata.get("Name") or dist._path.name).strip()  # type: ignore[attr-defined]
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        rows.append((name, dist.version, _detect_license_from_meta(dist)))
    rows.sort(key=lambda r: r[0].lower())
    return rows


def _detect_ffmpeg_info() -> Tuple[str, str]:
    version_line = "FFmpeg (installed in PATH)"
    license_note = "GPLv3 (or later)"
    try:
        v_res = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        if v_res.returncode == 0 and v_res.stdout.strip():
            version_line = v_res.stdout.splitlines()[0].strip()
    except Exception:
        pass
    try:
        l_res = subprocess.run(["ffmpeg", "-L"], capture_output=True, text=True, timeout=5)
        out_txt = l_res.stdout or ""
        if "GNU General Public License" in out_txt:
            license_note = "GNU General Public License v3+ (GPLv3)" if "version 3" in out_txt else "GNU GPL"
        elif "GNU Lesser General Public License" in out_txt:
            license_note = "GNU Lesser General Public License (LGPL)"
    except Exception:
        pass
    return version_line, license_note


def build_markdown() -> str:
    pkgs = _collect_python_packages()
    ffmpeg_ver, ffmpeg_lic = _detect_ffmpeg_info()

    lines: List[str] = [
        "# Third-Party Notices & Attributions",
        "",
        "EasyFaceless Studio dibangun di atas komponen open-source dan layanan pihak ketiga berikut.",
        "Dokumen ini dihasilkan secara otomatis untuk bagian dependensi Python oleh [`scripts/dev/generate_third_party_notices.py`](scripts/dev/generate_third_party_notices.py).",
        "",
        "---",
        "",
        "## 1. Proyek Upstream",
        "",
        "- **MoneyPrinterTurbo** — oleh Harry (`@harry0703`), dilisensikan di bawah **MIT License** ([LICENSE](LICENSE), https://github.com/harry0703/MoneyPrinterTurbo).",
        "",
        "---",
        "",
        "## 2. Media, Font, & Integrasi Eksternal",
        "",
        "### FFmpeg",
        f"- **Build terdeteksi saat pembuatan:** `{ffmpeg_ver}`",
        f"- **Lisensi:** {ffmpeg_lic}. EasyFaceless Studio memanggil biner eksternal `ffmpeg`/`ffprobe` sebagai subproses terpisah dan tidak mendistribusikan ulang kode sumber FFmpeg di repositori ini.",
        "",
        "### Tipografi: Plus Jakarta Sans",
        "- **Berkas:** `webui/static/fonts/PlusJakartaSans[wght].ttf`",
        "- **Lisensi:** **SIL Open Font License 1.1 (OFL-1.1)** — teks lengkap tersedia pada [`webui/static/fonts/OFL.txt`](webui/static/fonts/OFL.txt).",
        "",
        "### Sumber Footage Stok (Pixabay, Pexels, Coverr)",
        "- Aset video/gambar yang diunduh oleh pengguna tunduk pada ketentuan lisensi dan syarat layanan masing-masing penyedia:",
        "  - **Pixabay** — https://pixabay.com/service/license-summary/",
        "  - **Pexels** — https://www.pexels.com/license/",
        "  - **Coverr** — https://coverr.co/license",
        "- Pengguna bertanggung jawab memeriksa atribusi, batasan komersial, serta kelayakan merek dagang aset sebelum menerbitkan video.",
        "",
        "### Microsoft Edge TTS (`edge-tts`)",
        "- Fitur suara gratis default memanfaatkan layanan Read Aloud Microsoft Edge secara **tidak resmi** melalui pustaka `edge-tts`.",
        "- Layanan tidak memiliki SLA publik; ketersediaan, pembatasan laju (*rate limit*), dan perubahan protokol sepenuhnya berada di bawah kendali Microsoft.",
        "",
        "### CapCut Desktop (Ekspor Draft)",
        "- Fitur ekspor draft CapCut bersifat independen dan **tidak berafiliasi dengan atau didukung oleh ByteDance / CapCut**.",
        "- Format berkas draft internal (`draft_content.json` / `draft_meta_info.json`) tidak terdokumentasi secara resmi dan dapat berubah pada versi CapCut berikutnya (teruji pada CapCut Desktop **8.9.0**).",
        "",
        "### Penyedia AI Opsional (LLM, TTS, Gambar AI, Video AI)",
        "- Koneksi ke OpenRouter, OpenAI, Anthropic, Google Gemini, DeepSeek, WaveSpeed, SiliconFlow, ElevenLabs, Azure Speech, dan penyedia lainnya menggunakan API key pengguna sendiri dan tunduk pada ketentuan layanan & tarif masing-masing penyedia.",
        "",
        "---",
        "",
        f"## 3. Dependensi Python Terpasang ({len(pkgs)} paket)",
        "",
        "| Paket | Versi | Lisensi |",
        "|---|---|---|",
    ]
    for name, ver, lic in pkgs:
        safe_lic = lic.replace("|", "\\|")
        lines.append(f"| `{name}` | `{ver}` | {safe_lic} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    content = build_markdown()
    OUT_PATH.write_text(content, encoding="utf-8")
    print(f"Generated {OUT_PATH} ({len(content)} bytes)")


if __name__ == "__main__":
    main()
