"""Capture Stage 8A.2 UI screenshots matching mockups (8a2_beranda, 8a2_proyek_visual, 8a2_selesai).

Uses local Google Chrome / Microsoft Edge headless via Chrome DevTools Protocol (CDP)
to capture full-render screenshots of Streamlit pages.
"""

from __future__ import annotations

import base64
import json
import os
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


def capture_url(ws_url: str, target_url: str, output_path: Path, wait_sec: float = 6.0):
    ws = websocket.create_connection(ws_url, timeout=15)
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
        raw_res = ""
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


def main():
    browser_exe = find_browser_exe()
    port = 9222
    import shutil
    user_data = REPO_ROOT / ".temp_chrome_profile"
    if user_data.exists():
        shutil.rmtree(user_data, ignore_errors=True)
    user_data.mkdir(exist_ok=True)

    chrome_cmd = [
        browser_exe,
        "--headless=new",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data}",
        "--window-size=1600,1050",
        "--hide-scrollbars",
        "--disable-gpu",
        "--no-first-run",
        "about:blank",
    ]
    proc = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.0)

    try:
        # Get targets from /json
        req = urllib.request.Request(f"http://127.0.0.1:{port}/json")
        with urllib.request.urlopen(req) as resp:
            targets = json.loads(resp.read().decode("utf-8"))
        if not targets:
            req_put = urllib.request.Request(f"http://127.0.0.1:{port}/json/new", method="PUT")
            with urllib.request.urlopen(req_put) as resp:
                target_info = json.loads(resp.read().decode("utf-8"))
        else:
            target_info = targets[0]
        ws_url = target_info["webSocketDebuggerUrl"]

        # 1. 8a2v2_beranda.png
        print("[1/6] Capturing 8a2v2_beranda.png...")
        capture_url(ws_url, "http://localhost:8501/Long_Video?lang=id", SCREENS_DIR / "8a2v2_beranda.png", wait_sec=8.5)

        # 2. 8a2v2_proyek_visual.png
        print("[2/6] Capturing 8a2v2_proyek_visual.png...")
        capture_url(
            ws_url,
            "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=visual&lang=id",
            SCREENS_DIR / "8a2v2_proyek_visual.png",
            wait_sec=8.5,
        )

        # 3. 8a2v2_selesai.png
        print("[3/6] Capturing 8a2v2_selesai.png...")
        capture_url(
            ws_url,
            "http://localhost:8501/Long_Video?project=uji_nokey&step=render&lang=id",
            SCREENS_DIR / "8a2v2_selesai.png",
            wait_sec=8.5,
        )

        print("All Indonesian screenshots captured successfully in docs/branding/screens/!")

        # 4. 8a2v2_beranda_en.png
        print("[4/6] Capturing 8a2v2_beranda_en.png...")
        capture_url(ws_url, "http://localhost:8501/Long_Video?lang=en", SCREENS_DIR / "8a2v2_beranda_en.png", wait_sec=8.5)

        # 5. 8a2v2_proyek_visual_en.png
        print("[5/6] Capturing 8a2v2_proyek_visual_en.png...")
        capture_url(
            ws_url,
            "http://localhost:8501/Long_Video?project=dodge_charger_stage2&step=visual&lang=en",
            SCREENS_DIR / "8a2v2_proyek_visual_en.png",
            wait_sec=8.5,
        )

        # 6. 8a2v2_selesai_en.png
        print("[6/6] Capturing 8a2v2_selesai_en.png...")
        capture_url(
            ws_url,
            "http://localhost:8501/Long_Video?project=uji_nokey&step=render&lang=en",
            SCREENS_DIR / "8a2v2_selesai_en.png",
            wait_sec=8.5,
        )
        print("All English screenshots captured successfully!")

    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
