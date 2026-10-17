import json

from booking_assistant.human import FakePrompter
from booking_assistant.runlog import RunLog
from conftest import solve_captcha


def test_fake_prompter_scripted():
    p = FakePrompter(confirms=[True], choices=[1])
    assert p.confirm("최종 제출", "요약") is True
    assert p.choose("대안", ["a", "b"]) == 1
    assert [c[0] for c in p.calls] == ["confirm", "choose"]
    assert p.calls[1] == ("choose", "a\nb")


def test_fake_wait_until_calls_hook():
    state = {"done": False}
    p = FakePrompter(on_wait=lambda: state.update(done=True))
    assert p.wait_until("CAPTCHA", "입력", lambda: state["done"], poll_s=0.01)
    assert p.calls == [("wait_until", "입력")]


def test_fake_wait_until_gives_up_after_timeout(monkeypatch):
    import booking_assistant.human as human
    clock = {"t": 0.0}
    monkeypatch.setattr(human.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(human.time, "sleep", lambda s: clock.update(t=clock["t"] + s))
    p = FakePrompter()
    assert p.wait_until("t", "b", lambda: False, poll_s=0.5) is False


def test_runlog_writes_jsonl(tmp_path):
    log = RunLog(tmp_path)
    log.event("queue", "대기 시작")
    rec = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["kind"] == "queue" and rec["message"] == "대기 시작"
    assert rec["screenshot"] is None and rec["url"] is None and rec["t"]


def test_runlog_saves_screenshot(tmp_path, browser_page, site_url):
    browser_page.goto(f"{site_url}/index.html")
    log = RunLog(tmp_path)
    log.event("a", "x", browser_page)
    log.event("b", "y", browser_page)
    recs = [json.loads(l) for l in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["screenshot"] for r in recs] == ["001.png", "002.png"]
    assert (tmp_path / "001.png").exists() and recs[0]["url"].startswith(site_url)


def test_runlog_screenshot_failure_does_not_raise(tmp_path):
    class Broken:
        url = "http://x"
        def screenshot(self, **kw):
            raise RuntimeError("closed")
    RunLog(tmp_path).event("k", "m", Broken())
    rec = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["screenshot"] is None


def test_solve_captcha_helper_counts(browser_page, site_url):
    browser_page.goto(f"{site_url}/captcha.html")
    solve_captcha(browser_page, "abc")
    assert solve_captcha.count == 1
