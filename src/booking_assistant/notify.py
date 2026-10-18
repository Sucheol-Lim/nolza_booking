"""사람을 부르는 알림: 창 전면화, 소리, OS 알림, 텔레그램. 어떤 채널이 실패해도 예외를 던지지 않는다."""
import asyncio
import logging
import subprocess
import sys
import threading
from typing import Callable, Optional

from playwright.sync_api import Page

from booking_assistant.telegram import TelegramClient

log = logging.getLogger(__name__)

Channel = Callable[[Optional[Page], str, str], None]

OS_NOTIFY_TIMEOUT_S = 5.0     # 알림이 늦어져도 재생을 이보다 오래 붙잡지 않는다

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _notify_loop() -> asyncio.AbstractEventLoop:
    """OS 알림 전용 이벤트 루프(데몬 스레드에서 계속 돈다)."""
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="os-notify", daemon=True).start()
        return _loop


class Alerter:
    def __init__(self, telegram: TelegramClient | None = None, sound: bool = True,
                 os_notify: bool = True):
        self._telegram = telegram
        self._channels: list[Channel] = []
        if sound:
            self._channels.append(self._play_sound)
        if os_notify:
            self._channels.append(self._os_notification)
        if telegram is not None:
            self._channels.append(self._send_telegram)

    def alert(self, page: Page | None, title: str, body: str) -> None:
        if page is not None:
            try:
                page.bring_to_front()
            except Exception as e:
                log.warning("창 전면화 실패: %s", type(e).__name__)
        for channel in self._channels:
            try:
                channel(page, title, body)
            except Exception as e:
                log.warning("알림 채널 실패: %s", type(e).__name__)

    @staticmethod
    def _play_sound(page: Page | None, title: str, body: str) -> None:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep()
        elif sys.platform == "darwin":
            subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)

    @staticmethod
    def _os_notification(page: Page | None, title: str, body: str) -> None:
        # Playwright 동기 API가 메인 스레드에서 asyncio 루프를 돌리고 있어 그 안에서는 루프를
        # 또 돌릴 수 없다(DesktopNotifierSync가 실패함). 알림 전용 스레드의 루프에서 보낸다.
        # Windows 백엔드는 보낸 뒤에도 그 루프로 콜백을 보내므로 루프는 닫지 않고 계속 둔다.
        import asyncio

        import desktop_notifier

        async def send() -> None:
            notifier = desktop_notifier.DesktopNotifier(app_name="booking-assistant")
            await notifier.send(title=title, message=body)

        asyncio.run_coroutine_threadsafe(send(), _notify_loop()).result(OS_NOTIFY_TIMEOUT_S)

    def _send_telegram(self, page: Page | None, title: str, body: str) -> None:
        photo = None
        if page is not None:
            try:
                photo = page.screenshot()
            except Exception as e:
                log.warning("스크린샷 실패: %s", type(e).__name__)
        self._telegram.send(f"{title}\n{body}", photo)
