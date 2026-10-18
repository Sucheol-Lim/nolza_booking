import pytest
import json
from unittest.mock import Mock

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


class _FakeRoot:
    """tkinter 창 대신: after 콜백을 mainloop에서 차례로 실행한다."""

    def __init__(self):
        self.pending, self.destroyed = [], False

    def bind(self, *a): pass
    def protocol(self, *a): pass
    def after(self, ms, fn): self.pending.append(fn)
    def destroy(self): self.destroyed = True

    def mainloop(self):
        while self.pending and not self.destroyed:
            self.pending.pop(0)()


def test_tk_wait_until_survives_done_error(monkeypatch):
    from booking_assistant.human import TkPrompter
    root = _FakeRoot()
    monkeypatch.setattr(TkPrompter, "_window", lambda self, *a: root)
    monkeypatch.setattr(TkPrompter, "_buttons", staticmethod(lambda *a, **k: None))
    calls = iter([RuntimeError("페이지 이동 중"), False, True])

    def done():
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    assert TkPrompter().wait_until("t", "b", done, poll_s=0.01) is True
    assert root.destroyed


def test_fake_wait_until_manual_signal_only_when_offered():
    p = FakePrompter(manual_waits=[True, True])
    assert p.wait_until("t", "b", lambda: False, poll_s=0.01, manual_ok="통과했어요") is True
    p2 = FakePrompter(manual_waits=[True])
    p2.WAIT_TOTAL_S = 0.05
    assert p2.wait_until("t", "b", lambda: False, poll_s=0.01) is False     # 제공 안 했으면 무시


def test_runlog_screenshot_has_short_timeout(tmp_path):
    """뒤쪽 탭 등에서 스크린샷이 멈춰도 진행이 30초씩 막히지 않게 짧게 기다린다."""
    page = Mock()
    page.url = "http://x/"
    RunLog(tmp_path).event("captcha", "보안문자", page)
    assert 0 < page.screenshot.call_args.kwargs["timeout"] <= 5000


# --- 확인 창 버튼: 창이 키보드 포커스를 못 받아도(Windows 포그라운드 잠금) 클릭으로 처리 ---

def _press_button_later(monkeypatch, label):
    """창이 뜨면 label로 시작하는 버튼을 누른다. 화면이 없으면 건너뛴다."""
    import tkinter
    from booking_assistant.human import TkPrompter
    orig = TkPrompter._window
    seen = []

    def patched(self, *a):
        root = orig(self, *a)

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, tkinter.Button):
                    seen.append(c.cget("text"))
                yield c
                yield from walk(c)

        def press():
            for w in walk(root):
                if isinstance(w, tkinter.Button) and w.cget("text").startswith(label):
                    w.invoke()
                    return
            root.destroy()     # 버튼이 없으면 창을 닫아 테스트가 멈추지 않게 한다

        root.after(150, press)
        return root

    monkeypatch.setattr(TkPrompter, "_window", patched)
    return seen


@pytest.mark.parametrize("label,expected", [("승인", True), ("중단", False)])
def test_tk_confirm_buttons(monkeypatch, label, expected):
    from booking_assistant.human import TkPrompter
    _press_button_later(monkeypatch, label)
    assert TkPrompter().confirm("최종 제출 확인", "요약") is expected


def test_tk_choose_buttons(monkeypatch):
    from booking_assistant.human import TkPrompter
    _press_button_later(monkeypatch, "2.")
    assert TkPrompter().choose("대안", ["19:00", "21:00"]) == 1


def test_tk_wait_until_passed_button_only_when_offered(monkeypatch):
    from booking_assistant.human import TkPrompter
    seen = _press_button_later(monkeypatch, "통과했어요")
    assert TkPrompter().wait_until("보안문자", "b", lambda: False, manual_ok="통과했어요") is True
    seen.clear()
    _press_button_later(monkeypatch, "통과했어요")
    assert TkPrompter().wait_until("보안문자", "b", lambda: False) is False   # 버튼 없음 → 닫힘
    assert not any(s.startswith("통과했어요") for s in seen)


def test_tk_window_retries_transient_tcl_error(monkeypatch):
    """Windows에서 가끔 init.tcl을 못 읽어 Tk()가 실패한다: 재시도해서 창을 띄워야 한다."""
    import tkinter
    from booking_assistant import human

    real_tk = tkinter.Tk
    attempts = []

    def flaky_tk(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise tkinter.TclError("Can't find a usable init.tcl")
        return real_tk(*a, **k)

    monkeypatch.setattr(tkinter, "Tk", flaky_tk)
    monkeypatch.setattr(human, "TK_RETRY_DELAY_S", 0.01)
    _press_button_later(monkeypatch, "승인")
    assert human.TkPrompter().confirm("최종 제출 확인", "요약") is True
    assert len(attempts) == 2
