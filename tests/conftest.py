import functools
import http.server
import threading
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

FAKE_SITE = Path(__file__).parent / "fake_site"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass


@pytest.fixture(scope="session")
def site_url():
    handler = functools.partial(_QuietHandler, directory=str(FAKE_SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def _browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def context(_browser):
    ctx = _browser.new_context()
    yield ctx
    ctx.close()


@pytest.fixture
def browser_page(context):
    return context.new_page()


def solve_captcha(page, text):
    """사람이 CAPTCHA를 푸는 동작을 흉내 내는 테스트 전용 헬퍼."""
    page.fill("#captcha", text)
    page.click("#captcha-ok")
    solve_captcha.count += 1


solve_captcha.count = 0


@pytest.fixture(autouse=True)
def _reset_solve_captcha_count():
    solve_captcha.count = 0
    yield
