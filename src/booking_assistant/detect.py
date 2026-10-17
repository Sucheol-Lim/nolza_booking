"""화면 상태 판별: 대기열, 보안문자, 결제 화면을 구분한다."""
import re
from enum import Enum

from playwright.sync_api import Page


class PageState(Enum):
    NORMAL = "normal"
    QUEUE = "queue"
    CAPTCHA = "captcha"
    PAYMENT = "payment"


# 'captcha'는 낱말로만 센다: "protected by reCAPTCHA" 같은 배지 문구는 보안문자 화면이 아니다.
CAPTCHA_MARKERS = re.compile(r"보안문자|자동입력 ?방지|로봇이 아닙니다|(?<![a-z])captcha(?![a-z])")
CAPTCHA_FRAME_MARKERS = re.compile(r"recaptcha|hcaptcha|turnstile")
QUEUE_MARKERS = re.compile(r"대기순번|대기 순번|접속 대기|대기 중입니다|waiting room|queue")
CAPTCHA_URL_MARKERS = re.compile(r"captcha|turnstile", re.IGNORECASE)
PAYMENT_URL_MARKERS = re.compile(
    r"inicis|kcp|tosspayments|nicepay|kakaopay|naverpay|payco|danal|kginicis"
)


def is_captcha_url(url: str | None) -> bool:
    """보안문자 화면 주소로 보이는지(녹화·재생 모두 이 화면의 입력을 다루지 않는다)."""
    return bool(url) and bool(CAPTCHA_URL_MARKERS.search(url))


MIN_CHALLENGE_PX = 50       # 이보다 작거나 화면 밖·숨김인 보안문자 iframe은 배지로 본다


def _visible_challenge(frame) -> bool:
    """보이는 보안문자 iframe인지. 보이지 않는 reCAPTCHA/Turnstile 배지는 세지 않는다."""
    url = (frame.url or "").lower()
    if not CAPTCHA_FRAME_MARKERS.search(url) or "size=invisible" in url:
        return False
    try:
        el = frame.frame_element()
        if not el.is_visible():
            return False
        box = el.bounding_box()
    except Exception:
        return False
    return (box is not None and box["width"] > MIN_CHALLENGE_PX and box["height"] > MIN_CHALLENGE_PX
            and box["x"] + box["width"] > 0 and box["y"] + box["height"] > 0)


# reCAPTCHA·hCaptcha·Turnstile은 풀리면 페이지의 숨은 응답 칸에 토큰을 채운다.
_WIDGETS_SOLVED_JS = """() => {
  const fields = document.querySelectorAll(
    'textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"],'
    + ' input[name="cf-turnstile-response"]');
  return fields.length > 0 && [...fields].every(f => f.value.trim() !== '');
}"""


def _widgets_solved(page: Page) -> bool:
    """페이지의 모든 체크박스형 위젯이 풀렸는지(토큰 칸이 하나도 없으면 False)."""
    try:
        return bool(page.evaluate(_WIDGETS_SOLVED_JS))
    except Exception:
        return False


def detect_state(page: Page, ignore_widgets: bool = False) -> PageState:
    """현재 화면 상태를 반환한다. 우선순위: PAYMENT > CAPTCHA > QUEUE > NORMAL.

    보안문자 위젯 iframe은 풀린 뒤에도 화면에 남으므로, 토큰이 채워졌으면 보안문자로 보지 않는다.
    ignore_widgets=True면 위젯 iframe은 아예 보지 않는다(사람이 통과했다고 알린 화면).
    글자형 보안문자 문구는 어느 경우에도 확인한다.
    페이지가 이동 중이라 읽기에 실패하면 NORMAL을 반환한다.
    """
    try:
        frames = [f for f in page.frames if f != page.main_frame]
        urls = [page.url.lower()] + [(f.url or "").lower() for f in frames]
        if any(PAYMENT_URL_MARKERS.search(u) for u in urls):
            return PageState.PAYMENT
        text = page.inner_text("body", timeout=1000).lower()
        if CAPTCHA_MARKERS.search(text):
            return PageState.CAPTCHA
        if (not ignore_widgets and any(_visible_challenge(f) for f in frames)
                and not _widgets_solved(page)):
            return PageState.CAPTCHA
        if QUEUE_MARKERS.search(text):
            return PageState.QUEUE
    except Exception:
        return PageState.NORMAL
    return PageState.NORMAL
