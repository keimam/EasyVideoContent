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
    server_log = open(SCREENS_DIR / "streamlit_server.log", "w", encoding="utf-8")
    venv_py = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    streamlit_py = str(venv_py) if venv_py.is_file() else sys.executable
    server_proc = subprocess.Popen(
        [
            streamlit_py,
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
        stdout=server_log,
        stderr=server_log,
    )

    try:
        if not wait_for_server(base_url, timeout=35.0):
            server_log.flush()
            raise RuntimeError(f"Streamlit failed to start on {base_url}.")
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
            page.wait_for_selector('.efs-sb-header', timeout=45000)
            page.wait_for_selector('.efs-main-brand', timeout=45000)
            page.wait_for_timeout(2000)  # Allow fonts and styles to settle

            # Dismiss onboarding tour if present so sidebar is completely unobstructed
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                popover_close = page.query_selector('.driver-popover-close-btn, button[aria-label="Close"]')
                if popover_close:
                    popover_close.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

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

            known_icon_names = [
                "keyboard_double_arrow_left", "movie", "bolt", "category",
                "tune", "cleaning_services", "info", "auto_awesome", "expand_more",
                "settings", "psychology", "image", "video_library", "key",
                "refresh", "folder_open", "delete_sweep", "visibility", "network_check",
            ]

            def assert_icons_valid(scope_label: str, root_selector: str = "document", require_icons: bool = True):
                check_res = page.evaluate("""({names, rootSel}) => {
                    const root = rootSel === 'document' ? document : document.querySelector(rootSel);
                    if (!root) return {error: `Missing container: ${rootSel}`, count: 0, badFonts: [], plainLeaks: []};
                    const iconSel = '[data-testid="stIconMaterial"], [class*="material-symbols"], span[role="img"][aria-label$=" icon"], span[style*="Material Symbols"]';
                    const icons = Array.from(root.querySelectorAll(iconSel));
                    const badFonts = [];
                    for (const el of icons) {
                        const ff = window.getComputedStyle(el).fontFamily || '';
                        if (!ff.includes('Material')) {
                            badFonts.push({text: (el.innerText || el.textContent || '').trim(), ff, tag: el.tagName, role: el.getAttribute('role')});
                        }
                    }
                    const plainLeaks = [];
                    const nonIcons = root.querySelectorAll('p, label, h1, h2, h3, h4, span:not([data-testid="stIconMaterial"]):not([class*="material-symbols"]):not([role="img"][aria-label$=" icon"]):not([style*="Material Symbols"])');
                    for (const el of nonIcons) {
                        if (el.querySelector(iconSel)) continue;
                        const directText = Array.from(el.childNodes)
                            .filter(n => n.nodeType === 3)
                            .map(n => n.textContent.trim())
                            .join(' ')
                            .trim();
                        for (const name of names) {
                            if (directText === name || directText.startsWith(name + ' ')) {
                                plainLeaks.push({tag: el.tagName, class: el.className, text: directText});
                            }
                        }
                    }
                    return {error: null, count: icons.length, badFonts, plainLeaks};
                }""", {"names": known_icon_names, "rootSel": root_selector})
                assert not check_res["error"], f"[{scope_label}] {check_res['error']}"
                if require_icons:
                    assert check_res["count"] > 0, f"[{scope_label}] No icon elements found!"
                assert not check_res["badFonts"], f"[{scope_label}] Icons with non-Material font-family: {check_res['badFonts'][:5]}"
                assert not check_res["plainLeaks"], f"[{scope_label}] Ligature names leaked as plain text: {check_res['plainLeaks'][:5]}"
                print(f"  [OK] [{scope_label}] {check_res['count']} icons valid, 0 ligature leaks.")

            # Verify computed font-family on all icons in Main page
            print("  -> Checking computed font-family on Main page icon elements...")
            assert_icons_valid("Main Page")

            # Check UI text font is Plus Jakarta Sans
            h1_ff = page.evaluate("() => { const el = document.querySelector('h1') || document.querySelector('.efs-main-brand__name'); return el ? window.getComputedStyle(el).fontFamily : ''; }")
            print(f"  H1 / Brand computed font-family: {h1_ff}")
            assert "Plus Jakarta Sans" in h1_ff, f"Heading font is not Plus Jakarta Sans: {h1_ff}"

            # --- Page 2: Long Video (?lang=id and ?lang=en) + Same-Page Dialog Verification (B3) ---
            for ui_lang in ("id", "en"):
                lv_url = f"{base_url}/Long_Video?lang={ui_lang}"
                print(f"[4/5] Navigating to Long Video ({ui_lang}) at {lv_url}...")
                page.goto(lv_url, wait_until="networkidle")
                page.wait_for_selector('.efs-sb-header', timeout=30000)
                page.wait_for_timeout(1500)

                if ui_lang == "id":
                    lv_png = SCREENS_DIR / "longvideo.png"
                    page.screenshot(path=str(lv_png), full_page=False)
                    print(f"  -> Captured {lv_png}")

                assert_icons_valid(f"Long Video ({ui_lang})")

                # Open MPT Settings right on Long Video via sidebar API & AI Providers button
                page.mouse.move(900, 10)
                api_btn = page.locator('[data-testid="stSidebar"] button').filter(has_text="API").first
                api_btn.scroll_into_view_if_needed()
                api_btn.evaluate("el => el.click()")
                dlg = page.locator('[role="dialog"], [data-testid="stDialog"]').first
                dlg.wait_for(state="visible", timeout=25000)
                page.wait_for_timeout(1200)
                assert "/Long_Video" in page.url, f"Settings dialog unexpectedly switched away from Long_Video: {page.url}"

                # Click through every tab in Settings dialog and validate icons
                tabs = dlg.locator('[role="tab"]')
                tab_count = tabs.count()
                assert tab_count >= 5, f"Expected all Settings tabs, got {tab_count}"
                for idx in range(tab_count):
                    tabs.nth(idx).click(force=True)
                    page.wait_for_timeout(900)
                    if idx == 0:
                        dlg.locator("h3").first.wait_for(state="visible", timeout=15000)
                        page.wait_for_timeout(500)
                        shot_path = SCREENS_DIR / f"8b1_settings_general_{ui_lang}.png"
                        page.screenshot(path=str(shot_path), full_page=False)
                        print(f"  -> Captured {shot_path}")
                    assert_icons_valid(f"Settings Dialog ({ui_lang}) Tab {idx}", root_selector='[role="dialog"], [data-testid="stDialog"]', require_icons=(idx == 0))

                # Close Settings dialog cleanly via Close button and wait for backdrop removal
                close_btn = dlg.locator('button[aria-label="Close"]').first
                if close_btn.count() > 0:
                    close_btn.click()
                else:
                    page.keyboard.press("Escape")
                dlg.wait_for(state="detached", timeout=15000)
                page.wait_for_timeout(400)
                assert "/Long_Video" in page.url

                # Verify other sidebar dialogs (Kelola Niche, Cache & Tentang) on Long Video in ID
                if ui_lang == "id":
                    for btn_text, dlg_label in [("Niche", "Kelola Niche"), ("Cache", "Cache"), ("Tentang", "Tentang")]:
                        page.mouse.move(900, 10)
                        page.wait_for_timeout(200)
                        btn_loc = page.locator('[data-testid="stSidebar"] button', has_text=btn_text).first
                        btn_loc.scroll_into_view_if_needed()
                        btn_loc.evaluate("el => el.click()")
                        d2 = page.locator('[role="dialog"], [data-testid="stDialog"]').first
                        d2.wait_for(state="visible", timeout=20000)
                        page.wait_for_timeout(800)
                        assert_icons_valid(f"Dialog {dlg_label}", root_selector='[role="dialog"], [data-testid="stDialog"]', require_icons=False)
                        d2_close = d2.locator('button[aria-label="Close"]').first
                        if d2_close.count() > 0:
                            d2_close.click()
                        else:
                            page.keyboard.press("Escape")
                        d2.wait_for(state="detached", timeout=15000)
                        page.wait_for_timeout(400)

            browser.close()

        print("[5/5] Verification complete! All screenshots saved and icons validated.")

    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        try:
            server_log.close()
        except Exception:
            pass


if __name__ == "__main__":
    run_verification()
