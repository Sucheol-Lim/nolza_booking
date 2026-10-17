"""텔레그램 알림 전송 (알림 + 스크린샷만, 원격 승인 없음)."""
import logging

import requests

log = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, token: str, chat_id: str, session: requests.Session | None = None):
        self._token = token
        self._chat_id = chat_id
        self._session = session or requests.Session()

    def send(self, text: str, photo: bytes | None = None) -> bool:
        """전송 성공 여부를 반환한다. 예외는 던지지 않으며 토큰은 로그에 남기지 않는다."""
        base = f"https://api.telegram.org/bot{self._token}"
        try:
            if photo is not None:
                resp = self._session.post(
                    f"{base}/sendPhoto",
                    data={"chat_id": self._chat_id, "caption": text},
                    files={"photo": ("screen.png", photo, "image/png")},
                    timeout=10,
                )
            else:
                resp = self._session.post(
                    f"{base}/sendMessage",
                    data={"chat_id": self._chat_id, "text": text},
                    timeout=10,
                )
            return bool(resp.ok)
        except Exception as e:  # 메시지에 토큰 URL이 들어 있을 수 있어 타입만 기록
            log.warning("텔레그램 전송 실패: %s", type(e).__name__)
            return False
