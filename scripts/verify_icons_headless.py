"""Headless browser verification for Stage 8A.1 (R1).

Verifies that Streamlit renders with Plus Jakarta Sans for UI text,
while strictly preserving Material Symbols Rounded for icons (no raw text names).
Captures screenshots to docs/branding/screens/ (main.png, longvideo.png, sidebar.png).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
SCREENS_DIR = REPO_ROOT / "docs" / "branding" / "screens"
SCREENS_DIR.mkdir(parents=True, exist_ok=True)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def wait_for_server(url: str, timeout: float = 30.0) -> bool:
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def run_verification():
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    print(f"[1/5] Starting Streamlit server on {base_url}...")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    server_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(REPO_ROOT / "webui" / "Main.py"),
            f"--server.port={port}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
            "--server.enableStaticServing=true",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        if not wait_for_server(base_url, timeout=35.0):
            stdout, stderr = server_proc.communicate(timeout=5)
            raise RuntimeError(f"Streamlit failed to start on {base_url}.\nSTDOUT: {stdout.decode()}\nSTDERR: {stderr.decode()}")
        print("[1/5] Streamlit server is healthy and ready.")

        print("[2/5] Launching headless browser (Edge/Chromium)...")
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="msedge", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)

            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            # --- Page 1: Main.py (Video Pendek) ---
            print(f"[3/5] Navigating to Main.py at {base_url}...")
            page.goto(base_url, wait_until="networkidle")
            # Wait for brand header to ensure full script execution
            page.wait_for_selector('.efs-main-brand', timeout=30000)
            page.wait_for_selector('.efs-sb-header', timeout=30000)
            page.wait_for_timeout(2000)  # Allow fonts and styles to settle

            # Capture main.png and sidebar.png
            main_png = SCREENS_DIR / "main.png"
            page.screenshot(path=str(main_png), full_page=False)
            print(f"  -> Captured {main_png}")

            sidebar_el = page.query_selector('section[data-testid="stSidebar"]')
            sidebar_png = SCREENS_DIR / "sidebar.png"
            if sidebar_el:
                sidebar_el.screenshot(path=str(sidebar_png))
                print(f"  -> Captured {sidebar_png}")
            else:
                page.screenshot(path=str(sidebar_png))
                print(f"  -> Captured fallback {sidebar_png}")

            # Verify computed font-family on all icons in Main page
            print("  -> Checking computed font-family on Main page icon elements...")
            icons = page.query_selector_all('[data-testid="stIconMaterial"], [class*="material-symbols"]')
            print(f"  Found {len(icons)} icon elements on Main page.")
            assert len(icons) > 0, "No icon elements found on Main page!"

            invalid_icon_fonts = []
            for icon in icons:
                ff = icon.evaluate("el => window.getComputedStyle(el).fontFamily")
                text = icon.inner_text().strip()
                if "Material" not in ff:
                    invalid_icon_fonts.append((text, ff))

            if invalid_icon_fonts:
                print(f"  ERROR: Found {len(invalid_icon_fonts)} icons with incorrect font-family:")
                for text, ff in invalid_icon_fonts[:5]:
                    print(f"    Icon '{text}' has font-family: {ff}")
                raise AssertionError(f"Icons do not have Material Symbols font-family: {invalid_icon_fonts[:5]}")
            print(f"  [OK] All {len(icons)} icons on Main page have valid Material Symbols computed font-family.")

            # Check that icon names are NOT visible as plain text outside of icon glyphs (R1-c)
            known_icon_names = [
                "keyboard_double_arrow_left", "movie", "bolt", "category",
                "tune", "cleaning_services", "info", "auto_awesome", "expand_more", "settings"
            ]
            offenders = page.evaluate("""(names) => {
                const results = [];
                const nonIcons = document.querySelectorAll('p, label, h1, h2, h3, h4, span:not([data-testid="stIconMaterial"]):not([class*="material-symbols"])');
                for (const el of nonIcons) {
                    if (el.querySelector('[data-testid="stIconMaterial"], [class*="material-symbols"]')) {
                        continue;
                    }
                    const txt = (el.innerText || '').trim();
                    if (names.includes(txt)) {
                        results.push({ tag: el.tagName, class: el.className, text: txt });
                    }
                }
                return results;
            }""", known_icon_names)
            assert len(offenders) == 0, f"Found plain text elements containing icon ligature names: {offenders}"
            print("  [OK] No icon ligature names found as plain text outside of icon glyphs.")

            # Check UI text font is Plus Jakarta Sans
            h1_ff = page.evaluate("() => { const el = document.querySelector('h1') || document.querySelector('.efs-main-brand__name'); return el ? window.getComputedStyle(el).fontFamily : ''; }")
            print(f"  H1 / Brand computed font-family: {h1_ff}")
            assert "Plus Jakarta Sans" in h1_ff, f"Heading font is not Plus Jakarta Sans: {h1_ff}"

            # --- Page 2: Long Video ---
            lv_url = f"{base_url}/Long_Video"
            print(f"[4/5] Navigating to Long Video at {lv_url}...")
            page.goto(lv_url, wait_until="networkidle")
            page.wait_for_selector('.efs-sb-header', timeout=30000)
            page.wait_for_timeout(2000)

            lv_png = SCREENS_DIR / "longvideo.png"
            page.screenshot(path=str(lv_png), full_page=False)
            print(f"  -> Captured {lv_png}")

            print("  -> Checking computed font-family on Long Video icon elements...")
            lv_icons = page.query_selector_all('[data-testid="stIconMaterial"], [class*="material-symbols"]')
            print(f"  Found {len(lv_icons)} icon elements on Long Video page.")
            assert len(lv_icons) > 0, "No icon elements found on Long Video page!"

            invalid_lv_fonts = []
            for icon in lv_icons:
                ff = icon.evaluate("el => window.getComputedStyle(el).fontFamily")
                text = icon.inner_text().strip()
                if "Material" not in ff:
                    invalid_lv_fonts.append((text, ff))

            if invalid_lv_fonts:
                print(f"  ERROR: Found {len(invalid_lv_fonts)} icons with incorrect font-family:")
                for text, ff in invalid_lv_fonts[:5]:
                    print(f"    Icon '{text}' has font-family: {ff}")
                raise AssertionError(f"Icons do not have Material Symbols font-family: {invalid_lv_fonts[:5]}")
            print(f"  [OK] All {len(lv_icons)} icons on Long Video page have valid Material Symbols computed font-family.")

            lv_offenders = page.evaluate("""(names) => {
                const results = [];
                const nonIcons = document.querySelectorAll('p, label, h1, h2, h3, h4, span:not([data-testid="stIconMaterial"]):not([class*="material-symbols"])');
                for (const el of nonIcons) {
                    if (el.querySelector('[data-testid="stIconMaterial"], [class*="material-symbols"]')) {
                        continue;
                    }
                    const txt = (el.innerText || '').trim();
                    if (names.includes(txt)) {
                        results.push({ tag: el.tagName, class: el.className, text: txt });
                    }
                }
                return results;
            }""", known_icon_names)
            assert len(lv_offenders) == 0, f"Found plain text elements containing icon ligature names on Long Video: {lv_offenders}"
            print("  [OK] No icon ligature names found as plain text on Long Video page.")

            browser.close()

        print("[5/5] Verification complete! All screenshots saved and icons validated.")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()


if __name__ == "__main__":
    run_verification()
