from unittest.mock import Mock

import requests

from booking_assistant.notify import Alerter
from booking_assistant.telegram import TelegramClient


def test_telegram_send_photo_uses_sendPhoto():
    s = Mock(); s.post.return_value.ok = True
    assert TelegramClient("T", "C", s).send("CAPTCHA", b"png")
    url = s.post.call_args.args[0]
    assert url == "https://api.telegram.org/botT/sendPhoto"
    assert s.post.call_args.kwargs["data"] == {"chat_id": "C", "caption": "CAPTCHA"}


def test_telegram_send_message_without_photo():
    s = Mock(); s.post.return_value.ok = True
    assert TelegramClient("T", "C", s).send("hi")
    assert s.post.call_args.args[0] == "https://api.telegram.org/botT/sendMessage"
    assert s.post.call_args.kwargs["timeout"] == 10


def test_telegram_network_error_returns_false():
    s = Mock(); s.post.side_effect = requests.ConnectionError()
    assert TelegramClient("T", "C", s).send("x") is False


def test_telegram_failure_does_not_leak_token(caplog):
    s = Mock(); s.post.side_effect = requests.ConnectionError("https://api.telegram.org/botSECRET/x")
    assert TelegramClient("SECRET", "C", s).send("x") is False
    assert "SECRET" not in caplog.text


def test_alert_survives_channel_failures():
    calls = []
    a = Alerter(sound=False, os_notify=False)
    a._channels = [Mock(side_effect=RuntimeError()), lambda p, t, b: calls.append(t)]
    a.alert(None, "제목", "본문")
    assert calls == ["제목"]


def test_alert_brings_page_to_front_and_survives_failure():
    page = Mock(); page.bring_to_front.side_effect = RuntimeError()
    a = Alerter(sound=False, os_notify=False)
    a._channels = []
    a.alert(page, "t", "b")


def test_telegram_channel_attaches_screenshot():
    tg = Mock()
    page = Mock(); page.screenshot.return_value = b"png"
    Alerter(telegram=tg, sound=False, os_notify=False).alert(page, "제목", "본문")
    tg.send.assert_called_once_with("제목\n본문", b"png")


def test_os_notification_works_inside_running_event_loop(monkeypatch):
    """Playwright 동기 API는 메인 스레드에서 asyncio 루프를 돌린다: 그 안에서도 알림이 가야 한다."""
    import asyncio
    import desktop_notifier

    sent = []

    class FakeNotifier:
        def __init__(self, app_name):
            pass

        async def send(self, title, message):
            sent.append((title, message))

    monkeypatch.setattr(desktop_notifier, "DesktopNotifier", FakeNotifier)

    async def inside_loop():
        Alerter._os_notification(None, "보안문자", "직접 입력")

    # 테스트 세션의 Playwright 루프와 겹치지 않게 별도 스레드에서 "루프 안" 상황을 만든다.
    import threading
    t = threading.Thread(target=lambda: asyncio.run(inside_loop()))
    t.start()
    t.join(10)
    assert sent == [("보안문자", "직접 입력")]


def test_os_notification_loop_stays_open_for_callbacks(monkeypatch):
    """Windows 알림 백엔드는 보낸 뒤에도 같은 루프로 콜백을 보낸다: 그 루프가 닫히면 안 된다."""
    import asyncio
    import desktop_notifier

    loops = []

    class FakeNotifier:
        def __init__(self, app_name):
            pass

        async def send(self, title, message):
            loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(desktop_notifier, "DesktopNotifier", FakeNotifier)
    Alerter._os_notification(None, "a", "b")
    Alerter._os_notification(None, "c", "d")
    assert len(loops) == 2 and not loops[0].is_closed() and loops[0] is loops[1]
