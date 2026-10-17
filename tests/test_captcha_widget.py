"""풀린 뒤에도 화면에 남는 체크박스형 보안문자 위젯(reCAPTCHA v2·hCaptcha·Turnstile)."""
from unittest.mock import Mock

from conftest import solve_captcha
from test_player import OPTS, make_recording

from booking_assistant.human import FakePrompter
from booking_assistant.player import Player, RunOptions, RunResult
from booking_assistant.recorder import Recorder
from booking_assistant.recording import Recording, Step, Target
from booking_assistant.runlog import RunLog

OPTS_NAME = RunOptions(values={"name": "김철수"})


def widget_recording(site_url, kind="recaptcha"):
    return Recording(
        name="widget",
        start_url=f"{site_url}/widget.html?kind={kind}",
        steps=[
            Step("fill", Target(css=["#name"]), value="홍길동", var="name"),
            Step("final_submit", Target(css=["#submit"], text="예약하기")),
        ],
        variables=["name"],
    )


def _player(context, prompter, tmp_path):
    return Player(context, prompter, Mock(), RunLog(tmp_path), queue_poll_s=0.1)


def _waits(p):
    return [c for c in p.calls if c[0] == "wait_until"]


def test_solved_widget_resumes_automatically(context, site_url, tmp_path):
    holder = {}
    p = FakePrompter(confirms=[True], on_wait=lambda: holder["pl"].page.evaluate(
        "document.querySelector('[name=\"g-recaptcha-response\"]').value = 'token'"))
    pl = holder["pl"] = _player(context, p, tmp_path)
    assert pl.run(widget_recording(site_url), OPTS_NAME) is RunResult.SUBMITTED
    assert len(_waits(p)) == 1                       # 위젯이 남아 있어도 다시 부르지 않는다
    confirm_body = [c for c in p.calls if c[0] == "confirm"][0][1]
    assert "name=김철수" in confirm_body and "직접 처리한 단계" not in confirm_body


def test_unknown_widget_human_says_passed(context, site_url, tmp_path):
    p = FakePrompter(confirms=[True], manual_waits=[True])
    pl = _player(context, p, tmp_path)
    assert pl.run(widget_recording(site_url, kind="unknown"), OPTS_NAME) is RunResult.SUBMITTED
    assert len(_waits(p)) == 1


def test_unknown_widget_without_signal_keeps_waiting_until_abort(context, site_url, tmp_path, monkeypatch):
    monkeypatch.setattr(FakePrompter, "WAIT_TOTAL_S", 0.5)
    p = FakePrompter(confirms=[True])
    pl = _player(context, p, tmp_path)
    assert pl.run(widget_recording(site_url, kind="unknown"), OPTS_NAME) is RunResult.ABORTED
    assert not [c for c in p.calls if c[0] == "confirm"]


def test_text_captcha_cannot_be_skipped_by_passed_signal(context, site_url, tmp_path):
    holder = {}
    p = FakePrompter(confirms=[True], manual_waits=[True],
                     on_wait=lambda: solve_captcha(holder["pl"].page, "ABCD"))
    pl = holder["pl"] = _player(context, p, tmp_path)
    assert pl.run(make_recording(site_url), OPTS) is RunResult.SUBMITTED
    assert len(_waits(p)) == 1                       # "통과했어요"는 글자형 보안문자에 제공되지 않는다
    assert solve_captcha.count == 1


def test_recording_on_widget_page_keeps_form_steps(context, site_url):
    r = Recorder(context)
    page = r.start(site_url + "/widget.html")
    page.fill("#name", "홍길동")
    page.keyboard.press("Control+Shift+F")
    page.click("#submit")
    steps = r.wait_finished()
    assert [s.kind for s in steps] == ["fill", "final_submit"]
    assert steps[0].value == "홍길동"
