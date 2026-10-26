#!/usr/bin/env python3
"""Build/runtime check without a model request or Office/PPT execution."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def main() -> int:
    if os.getuid() != 10000:
        raise RuntimeError("executor must run as UID 10000")
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        raise RuntimeError("this image is verified for ARM64 only")
    skill = Path(os.environ["PPT_MASTER_SKILL_DIR"])
    subprocess.run([sys.executable, str(skill / "scripts/attribution_guard.py")], check=True)
    for name in ("pptx", "xlsxwriter", "lxml.etree", "PIL.Image", "yaml", "mammoth",
                 "pymupdf", "pathops", "uharfbuzz", "flask", "playwright.sync_api"):
        importlib.import_module(name)
    installed = {}
    for line in Path("/opt/ppt-service/build-info/requirements-worker.lock").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f"dependency mismatch for {name}: {actual} != {expected}")
        installed[name] = actual
    font = subprocess.run(
        ["fc-match", "-f", "%{family}", "Noto Sans CJK SC"],
        check=True, capture_output=True, text=True,
    ).stdout
    if "Noto Sans CJK" not in font:
        raise RuntimeError("Noto CJK font unavailable")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 640, "height": 360})
            page.set_content('<html><body style="font-family: Noto Sans CJK SC">'
                             '<h1>中文排版检查</h1><p>Hermes PPT service</p></body></html>')
            page.evaluate("document.fonts.ready")
            if len(page.screenshot(type="png")) < 1000:
                raise RuntimeError("Chromium returned an empty render")
            browser_version = browser.version
        finally:
            browser.close()
    print(json.dumps({"status": "passed", "architecture": platform.machine(),
                      "uid": os.getuid(), "chromium": browser_version,
                      "cjk_font": font, "dependencies": installed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
