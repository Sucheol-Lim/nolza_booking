"""화면 상태 판별: 대기열, 보안문자, 결제 화면을 구분한다."""
import re
from enum import Enum

from playwright.sync_api import Page


class PageState(Enum):
    NORMAL = "normal"
    QUEUE = "queue"
    CAPTCHA = "captcha"
    PAYMENT = "payment"


CAPTCHA_MARKERS = re.compile(r"보안문자|자동입력 방지|자동입력방지|captcha|로봇이 아닙니다")
CAPTCHA_FRAME_MARKERS = re.compile(r"recaptcha|hcaptcha|turnstile")
QUEUE_MARKERS = re.compile(r"대기순번|대기 순번|접속 대기|대기 중입니다|waiting room|queue")
CAPTCHA_URL_MARKERS = re.compile(r"captcha|turnstile", re.IGNORECASE)
PAYMENT_URL_MARKERS = re.compile(
    r"inicis|kcp|tosspayments|nicepay|kakaopay|naverpay|payco|danal|kginicis"
)


def is_captcha_url(url: str | None) -> bool:
    """보안문자 화면 주소로 보이는지(녹화·재생 모두 이 화면의 입력을 다루지 않는다)."""
    return bool(url) and bool(CAPTCHA_URL_MARKERS.search(url))


def detect_state(page: Page) -> PageState:
    """현재 화면 상태를 반환한다. 우선순위: PAYMENT > CAPTCHA > QUEUE > NORMAL.

    페이지가 이동 중이라 읽기에 실패하면 NORMAL을 반환한다.
    """
    try:
        urls = [page.url.lower()]
        urls += [(f.url or "").lower() for f in page.frames if f != page.main_frame]
        iframe_srcs = urls[1:]
        if any(PAYMENT_URL_MARKERS.search(u) for u in urls):
            return PageState.PAYMENT
        text = page.inner_text("body").lower()
        if CAPTCHA_MARKERS.search(text) or any(
            CAPTCHA_FRAME_MARKERS.search(u) for u in iframe_srcs
        ):
            return PageState.CAPTCHA
        if QUEUE_MARKERS.search(text):
            return PageState.QUEUE
    except Exception:
        return PageState.NORMAL
    return PageState.NORMAL
