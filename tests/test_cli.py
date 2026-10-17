import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from booking_assistant.browser import ChromeNotReachable, chrome_path
from booking_assistant.cli import main, parse_candidate
from booking_assistant.config import Config, load_config
from booking_assistant.recording import Recording, Step, Target, save_recording

SAMPLE = Recording(
    "demo", "http://127.0.0.1:8765/index.html",
    [Step("fill", Target(css=["#date"]), value="2026-11-01", var="date")],
    ["date"],
)


def _setup(tmp_path, monkeypatch):
    save_recording(SAMPLE, tmp_path / "recordings" / "demo.yaml")
    monkeypatch.setattr("booking_assistant.cli.DEFAULT_HOME", tmp_path)


def test_run_without_chrome_prints_guidance(tmp_path, capsys, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr("booking_assistant.cli.connect", Mock(side_effect=ChromeNotReachable()))
    assert main(["run", "demo", "--set", "date=2026-11-01"]) == 2
    assert "booking-assistant chrome" in capsys.readouterr().out


def test_run_missing_variable_exits_1_before_connecting(tmp_path, capsys, monkeypatch):
    _setup(tmp_path, monkeypatch)
    connect = Mock()
    monkeypatch.setattr("booking_assistant.cli.connect", connect)
    assert main(["run", "demo"]) == 1
    assert "date" in capsys.readouterr().out
    connect.assert_not_called()


def test_list_shows_recordings(tmp_path, capsys, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert main(["list"]) == 0
    assert "demo" in capsys.readouterr().out


def test_parse_candidates():
    assert parse_candidate("date=2026-11-03,time=19:00") == {"date": "2026-11-03", "time": "19:00"}


def test_load_config_defaults(tmp_path):
    assert load_config(tmp_path) == Config(None, None)


def test_load_config_reads_yaml(tmp_path):
    (tmp_path / "config.yaml").write_text(
        "telegram:\n  token: T\n  chat_id: '1'\nadvisor:\n  model: m\nchrome:\n  port: 9333\n",
        encoding="utf-8")
    assert load_config(tmp_path) == Config("T", "1", "m", 9333)


def test_chrome_path_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "exists", lambda self: True)
    assert str(chrome_path()).endswith("Google Chrome")


def test_chrome_path_missing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "exists", lambda self: False)
    with pytest.raises(FileNotFoundError):
        chrome_path()
