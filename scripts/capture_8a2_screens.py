"""Capture Stage 8A.2 UI screenshots matching mockups (8a2_beranda, 8a2_proyek_visual, 8a2_selesai)
and verify that no major layout blocks overlap across screen widths (1280px & 1920px) (V6).

Uses local Google Chrome / Microsoft Edge headless via Chrome DevTools Protocol (CDP).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

REPO_ROOT = Path(__file__).resolve().parents[1]
SCREENS_DIR = REPO_ROOT / "docs" / "branding" / "screens"
SCREENS_DIR.mkdir(parents=True, exist_ok=True)

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_browser_exe() -> str:
    for p in CHROME_PATHS:
        if os.path.isfile(p):
            return p
    raise RuntimeError("Neither Chrome nor Edge found on system.")


def capture_url(ws_url: str, target_url: str, output_path: Path, wait_sec: float = 8.5):
    ws = websocket.create_connection(ws_url, timeout=20)
    try:
        # Enable Page domain
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        ws.recv()

        # Navigate
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": target_url}}))
        ws.recv()

        # Wait for Streamlit rendering and websocket sync
        time.sleep(wait_sec)

        # Capture screenshot
        ws.send(json.dumps({"id": 3, "method": "Page.captureScreenshot", "params": {"format": "png"}}))
        while True:
            msg = ws.recv()
            data = json.loads(msg)
            if data.get("id") == 3:
                b64_data = data["result"]["data"]
                output_path.write_bytes(base64.b64decode(b64_data))
                print(f"Captured: {output_path} ({output_path.stat().st_size} bytes)")
                break
    finally:
        ws.close()


def verify_no_block_overlaps(ws_url: str, target_url: str, viewport_width: int, wait_sec: float = 7.0):
    """Verify that no two major UI layout blocks overlap vertically at the given viewport width (V6)."""
    ws = websocket.create_connection(ws_url, timeout=20)
    try:
        # Enable domains
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        ws.recv()

        # Set device metrics override for requested width
        ws.send(json.dumps({
            "id": 2,
            "method": "Emulation.setDeviceMetricsOverride",
            "params": {
                "width": viewport_width,
                "height": 1050,
                "deviceScaleFactor": 1,
                "mobile": False,
            }
        }))
        ws.recv()

        # Navigate
        ws.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {"url": target_url}}))
        ws.recv()

        time.sleep(wait_sec)

        # JS code to inspect bounding boxes of all major layout blocks
        js_code = """
        (async () => {
            // Wait for Streamlit page to settle
            for (let i = 0; i < 40; i++) {
                if (document.querySelectorAll('.efs-stepper-card').length >= 5) break;
                await new Promise(r => setTimeout(r, 250));
            }

            const selectors = [
                '[data-testid="stHorizontalBlock"]:has(.efs-stepper-card)',
                '.efs-job-banner',
                '.efs-success-banner',
                '[data-testid="stHorizontalBlock"]:has(.efs-metric-tile)',
                '[data-testid="stExpander"]',
                '.efs-card',
            ];

            const elements = Array.from(document.querySelectorAll(selectors.join(', ')));
            const blocks = [];
            elements.forEach((el, idx) => {
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0 || r.bottom <= 0) return;
                const text = (el.innerText || '').trim().replace(/\\s+/g, ' ').substring(0, 35);
                blocks.push({
                    idx,
                    el,
                    tag: el.tagName,
                    text,
                    top: Math.round(r.top),
                    bottom: Math.round(r.bottom),
                    left: Math.round(r.left),
                    right: Math.round(r.right),
                    width: Math.round(r.width),
                    height: Math.round(r.height),
                });
            });

            // Check overlaps among vertically stacked elements
            const collisions = [];
            const gaps = [];
            for (let i = 0; i < blocks.length; i++) {
                for (let j = i + 1; j < blocks.length; j++) {
                    const b1 = blocks[i];
                    const b2 = blocks[j];
                    // Skip if one element contains the other (parent-child relationship)
                    if (b1.el.contains(b2.el) || b2.el.contains(b1.el)) continue;

                    // Overlap in horizontal span (meaning they are vertically stacked peers, not side-by-side columns)
                    const hOverlap = Math.min(b1.right, b2.right) - Math.max(b1.left, b2.left);
                    if (hOverlap > 40) {
                        const higher = b1.top <= b2.top ? b1 : b2;
                        const lower = b1.top <= b2.top ? b2 : b1;
                        const vGap = lower.top - higher.bottom;
                        if (vGap < 0) {
                            collisions.push({
                                higher: higher.text,
                                lower: lower.text,
                                overlapPx: -vGap,
                                higherBottom: higher.bottom,
                                lowerTop: lower.top,
                            });
                        } else {
                            gaps.push({
                                higher: higher.text,
                                lower: lower.text,
                                gapPx: vGap,
                            });
                        }
                    }
                }
            }
            // Check specifically the metric tiles row to subsequent expander gap (V6)
            let metricToExpanderGap = null;
            const metricBlock = document.querySelector('[data-testid="stHorizontalBlock"]:has(.efs-metric-tile)');
            if (metricBlock) {
                const mr = metricBlock.getBoundingClientRect();
                const allExpanders = Array.from(document.querySelectorAll('[data-testid="stExpander"]'));
                const subsequentExpander = allExpanders.find(el => el.getBoundingClientRect().top >= mr.bottom - 5);
                if (subsequentExpander) {
                    const er = subsequentExpander.getBoundingClientRect();
                    metricToExpanderGap = Math.round(er.top - mr.bottom);
                }
            }

            return {
                blockCount: blocks.length,
                collisions,
                metricToExpanderGap,
                gaps: gaps.slice(0, 10).map(g => ({ higher: g.higher, lower: g.lower, gapPx: g.gapPx }))
            };
        })()
        """

        ws.send(json.dumps({
            "id": 4,
            "method": "Runtime.evaluate",
            "params": {"expression": js_code, "awaitPromise": True, "returnByValue": True}
        }))
        while True:
            msg = ws.recv()
            data = json.loads(msg)
            if data.get("id") == 4:
                res = data["result"]["result"]["value"]
                print(f"[V6 BoundingBox Check @ {viewport_width}px] Blocks found: {res['blockCount']}, Collisions: {len(res['collisions'])}")
                if res.get("metricToExpanderGap") is not None:
                    gap = res["metricToExpanderGap"]
                    print(f"  📏 Metric tiles -> Expander gap: {gap}px (target: 16-24px)")
                    if not (16 <= gap <= 24):
                        raise AssertionError(f"Metric tiles -> Expander gap {gap}px is outside target range 16-24px!")
                if res["collisions"]:
                    for col in res["collisions"]:
                        print(f"  ❌ COLLISION: '{col['higher']}' overlaps with '{col['lower']}' by {col['overlapPx']}px!")
                    raise AssertionError(f"Layout blocks collision detected at {viewport_width}px: {res['collisions']}")
                else:
                    print(f"  ✅ Verified: ZERO collisions among major layout blocks at {viewport_width}px.")
                break
    finally:
        ws.close()


def main():
    browser_exe = find_browser_exe()
    port = 9222
    user_data = REPO_ROOT / ".temp_chrome_profile"
    if user_data.exists():
        shutil.rmtree(user_data, ignore_errors=True)
    user_data.mkdir(exist_ok=True)

    chrome_cmd = [
        browser_exe,
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
        "--window-size=1600,1280",
        "--hide-scrollbars",
        "--disable-gpu",
        "--no-first-run",
        "about:blank",
    ]
    proc = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)

    try:
        # Get targets from /json with retry
        targets = None
        for _ in range(15):
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/json")
                with urllib.request.urlopen(req) as resp:
                    targets = json.loads(resp.read().decode("utf-8"))
                if targets:
                    break
            except Exception:
                time.sleep(0.5)

        if not targets:
            req_put = urllib.request.Request(f"http://127.0.0.1:{port}/json/new", method="PUT")
            with urllib.request.urlopen(req_put) as resp:
                targets = [json.loads(resp.read().decode("utf-8"))]

        target_info = targets[0]
        ws_url = target_info["webSocketDebuggerUrl"]

        # --- V6 Automated Bounding Box Collision Check at 1280px and 1920px ---
        print("\n--- Running V6 Automated Bounding Box Checks ---")
        visual_url = "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=visual&lang=id"
        completed_url = "http://localhost:8501/Long_Video?project=uji_nokey&step=render&lang=id"

        print("[Check 1/4] Visual step @ 1280px:")
        verify_no_block_overlaps(ws_url, visual_url, viewport_width=1280)

        print("[Check 2/4] Visual step @ 1920px:")
        verify_no_block_overlaps(ws_url, visual_url, viewport_width=1920)

        print("[Check 3/4] Completed view @ 1280px:")
        verify_no_block_overlaps(ws_url, completed_url, viewport_width=1280)

        print("[Check 4/4] Completed view @ 1920px:")
        verify_no_block_overlaps(ws_url, completed_url, viewport_width=1920)
        print("--- All V6 Bounding Box Checks Passed! ---\n")

        # --- Capture High-Res V7 Screenshots (ID & EN for Beranda + all 5 steps + Completed) ---
        targets_v3 = [
            ("8a2v3_beranda.png", "http://localhost:8501/Long_Video?lang=id"),
            ("8a2v3_beranda_en.png", "http://localhost:8501/Long_Video?lang=en"),
            ("8a2v3_naskah.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=script&lang=id"),
            ("8a2v3_naskah_en.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=script&lang=en"),
            ("8a2v3_suara.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=voice&lang=id"),
            ("8a2v3_suara_en.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=voice&lang=en"),
            ("8a2v3_visual.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=visual&lang=id"),
            ("8a2v3_visual_en.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=visual&lang=en"),
            ("8a2v3_render.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=render&lang=id"),
            ("8a2v3_render_en.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=render&lang=en"),
            ("8a2v3_capcut.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=capcut&lang=id"),
            ("8a2v3_capcut_en.png", "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=capcut&lang=en"),
            ("8a2v3_selesai.png", "http://localhost:8501/Long_Video?project=uji_nokey&step=render&lang=id"),
            ("8a2v3_selesai_en.png", "http://localhost:8501/Long_Video?project=uji_nokey&step=render&lang=en"),
        ]
        total_v3 = len(targets_v3)
        for idx, (fname, url) in enumerate(targets_v3, start=1):
            print(f"[{idx}/{total_v3}] Capturing {fname}...")
            capture_url(ws_url, url, SCREENS_DIR / fname, wait_sec=7.5)
        print("All V7 (ID & EN) screenshots captured successfully in docs/branding/screens/!")

    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
