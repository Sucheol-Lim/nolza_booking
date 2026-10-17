"""사람을 부르는 알림: 창 전면화, 소리, OS 알림, 텔레그램. 어떤 채널이 실패해도 예외를 던지지 않는다."""
import logging
import subprocess
import sys
from typing import Callable, Optional

from playwright.sync_api import Page

from booking_assistant.telegram import TelegramClient

log = logging.getLogger(__name__)

Channel = Callable[[Optional[Page], str, str], None]


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
        from desktop_notifier import DesktopNotifierSync
        DesktopNotifierSync(app_name="booking-assistant").send(title=title, message=body)

    def _send_telegram(self, page: Page | None, title: str, body: str) -> None:
        photo = None
        if page is not None:
            try:
                photo = page.screenshot()
            except Exception as e:
                log.warning("스크린샷 실패: %s", type(e).__name__)
        self._telegram.send(f"{title}\n{body}", photo)
