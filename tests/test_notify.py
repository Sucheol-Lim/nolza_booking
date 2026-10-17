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
