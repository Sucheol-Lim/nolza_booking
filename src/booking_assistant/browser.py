"""전용 Chrome 실행과 CDP 연결."""
import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import BrowserContext, Playwright, sync_playwright


class ChromeNotReachable(Exception):
    """전용 Chrome에 연결하지 못했을 때."""


def chrome_path() -> Path:
    if sys.platform == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    elif sys.platform == "win32":
        candidates = [
            Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
            if (base := os.environ.get(var))
        ]
    else:
        candidates = []
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("Chrome 실행 파일을 찾지 못했습니다")


def launch_chrome(profile_dir: Path, port: int = 9222) -> subprocess.Popen:
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([
        str(chrome_path()),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
    ])


def connect(port: int = 9222) -> tuple[Playwright, BrowserContext]:
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        return pw, browser.contexts[0]
    except Exception as e:
        pw.stop()
        raise ChromeNotReachable(
            "전용 Chrome에 연결하지 못했습니다. 먼저 `booking-assistant chrome`으로 "
            "Chrome을 실행해 주세요.") from e
