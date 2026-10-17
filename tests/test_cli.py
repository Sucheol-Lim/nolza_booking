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


def _rec():
    return Recording("r", "http://x", [
        Step("fill", Target(css=["#a"]), value="2026-11-01"),
        Step("click", Target(text="19:00")),
        Step("goto", url="http://x/y"),
    ])


def _script(monkeypatch, lines):
    it = iter(lines)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def test_designate_valid_line(monkeypatch, capsys):
    from booking_assistant.cli import _designate_variables
    rec = _rec()
    _script(monkeypatch, ["1=date", ""])
    _designate_variables(rec)
    assert rec.steps[0].var == "date"
    assert rec.variables == ["date"]
    assert "2026-11-01" in capsys.readouterr().out


def test_designate_bad_lines_reprompt_with_message(monkeypatch, capsys):
    from booking_assistant.cli import _designate_variables
    rec = _rec()
    _script(monkeypatch, ["abc", "x=date", "1=", "2=time", ""])
    _designate_variables(rec)
    assert rec.variables == ["time"]
    assert rec.steps[0].var is None and rec.steps[1].var == "time"
    assert capsys.readouterr().out.count("형식이 올바르지 않습니다") == 3


def test_designate_out_of_range_ignored(monkeypatch, capsys):
    from booking_assistant.cli import _designate_variables
    rec = _rec()
    _script(monkeypatch, ["0=a", "3=b", "99=c", ""])
    _designate_variables(rec)
    assert rec.variables == []
    assert all(s.var is None for s in rec.steps)
    assert "형식이 올바르지 않습니다" in capsys.readouterr().out


def test_designate_duplicate_name_listed_once(monkeypatch):
    from booking_assistant.cli import _designate_variables
    rec = _rec()
    _script(monkeypatch, ["1=date", "2=date", ""])
    _designate_variables(rec)
    assert rec.variables == ["date"]
    assert rec.steps[0].var == rec.steps[1].var == "date"


def test_designate_empty_line_ends_immediately(monkeypatch):
    from booking_assistant.cli import _designate_variables
    rec = _rec()
    _script(monkeypatch, [""])
    _designate_variables(rec)
    assert rec.variables == []


def test_record_command_saves_yaml_with_var(tmp_path, monkeypatch, capsys):
    from booking_assistant.recording import load_recording
    monkeypatch.setattr("booking_assistant.cli.DEFAULT_HOME", tmp_path)
    pw = Mock()
    monkeypatch.setattr("booking_assistant.cli.connect", Mock(return_value=(pw, Mock())))
    steps = [Step("fill", Target(css=["#a"]), value="2026-11-01"),
             Step("final_submit", Target(text="예약하기"))]
    recorder = Mock()
    recorder.wait_finished.return_value = steps
    monkeypatch.setattr("booking_assistant.cli.Recorder", Mock(return_value=recorder))
    _script(monkeypatch, ["1=date", ""])
    assert main(["record", "demo", "--url", "http://x/"]) == 0
    saved = load_recording(tmp_path / "recordings" / "demo.yaml")
    assert saved.start_url == "http://x/"
    assert saved.variables == ["date"]
    assert saved.steps[0].var == "date"
    recorder.start.assert_called_once_with("http://x/")
    pw.stop.assert_called_once()


def test_run_malformed_candidate_exits_1_before_connecting(tmp_path, capsys, monkeypatch):
    _setup(tmp_path, monkeypatch)
    connect = Mock()
    monkeypatch.setattr("booking_assistant.cli.connect", connect)
    assert main(["run", "demo", "--set", "date=2026-11-01", "--candidate", "date"]) == 1
    assert "--candidate" in capsys.readouterr().out
    connect.assert_not_called()
