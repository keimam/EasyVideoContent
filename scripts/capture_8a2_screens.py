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
    user_data = REPO_ROOT / ".temp_chrome_profile"
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

        # 1. 8a2_beranda.png
        print("[1/3] Capturing 8a2_beranda.png...")
        capture_url(ws_url, "http://localhost:8501/Long_Video", SCREENS_DIR / "8a2_beranda.png", wait_sec=7.0)

        # 2. 8a2_proyek_visual.png
        print("[2/3] Capturing 8a2_proyek_visual.png...")
        capture_url(
            ws_url,
            "http://localhost:8501/Long_Video?project=dodge_charger_stage2",
            SCREENS_DIR / "8a2_proyek_visual.png",
            wait_sec=7.0,
        )

        # 3. 8a2_selesai.png
        print("[3/3] Capturing 8a2_selesai.png...")
        capture_url(
            ws_url,
            "http://localhost:8501/Long_Video?project=uji_nokey",
            SCREENS_DIR / "8a2_selesai.png",
            wait_sec=7.0,
        )

        print("All Indonesian screenshots captured successfully in docs/branding/screens/!")

        # Now capture English versions (L5)
        print("\nSwitching to English UI for EN screenshots...")
        import toml
        cfg_path = REPO_ROOT / "config.toml"
        cfg_data = toml.load(cfg_path)
        orig_ui = dict(cfg_data.get("ui", {}))
        try:
            cfg_data.setdefault("ui", {})["language"] = "en"
            with open(cfg_path, "w", encoding="utf-8") as f:
                toml.dump(cfg_data, f)
            time.sleep(2.0)

            print("[4/6] Capturing 8a2_beranda_en.png...")
            capture_url(ws_url, "http://localhost:8501/Long_Video", SCREENS_DIR / "8a2_beranda_en.png", wait_sec=7.0)

            print("[5/6] Capturing 8a2_proyek_visual_en.png...")
            capture_url(
                ws_url,
                "http://localhost:8501/Long_Video?project=dodge_charger_stage2",
                SCREENS_DIR / "8a2_proyek_visual_en.png",
                wait_sec=7.0,
            )

            print("[6/6] Capturing 8a2_selesai_en.png...")
            capture_url(
                ws_url,
                "http://localhost:8501/Long_Video?project=uji_nokey",
                SCREENS_DIR / "8a2_selesai_en.png",
                wait_sec=7.0,
            )
            print("All English screenshots captured successfully!")
        finally:
            # Restore original config
            cfg_data["ui"] = orig_ui
            with open(cfg_path, "w", encoding="utf-8") as f:
                toml.dump(cfg_data, f)
            print("Restored original config.toml.")

    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
