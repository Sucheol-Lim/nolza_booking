"""안전 불변식: 최종 제출 버튼은 최종 확인 승인 뒤에만 눌린다(R18) 등."""
import re
from urllib.parse import quote
from unittest.mock import Mock

from booking_assistant.advisor import Proposal
from booking_assistant.human import FakePrompter
from booking_assistant.player import Player, RunOptions, RunResult
from booking_assistant.recording import Recording, Step, Target
from booking_assistant.runlog import RunLog

FINAL = Step("final_submit", Target(role="button", name="예약하기", text="예약하기",
                                    css=["#submit", 'button:has-text("예약하기")']))


def _player(context, tmp_path, prompter, advisor=None):
    return Player(context, prompter, Mock(), RunLog(tmp_path), advisor=advisor, queue_poll_s=0.1)


def _quick_prompter(**kw):
    p = FakePrompter(**kw)
    p.WAIT_TOTAL_S = 1.5
    return p


def _ba_id_of(dom, text):
    for line in dom.splitlines():
        m = re.match(r"\[(\d+)\] <[^>]+> (.*)$", line)
        if m and m.group(2).strip() == text:
            return int(m.group(1))
    raise AssertionError(f"{text!r} not in dom:\n{dom}")


def test_non_final_step_never_clicks_submit_button(context, site_url, tmp_path):
    # 일반 클릭 단계의 선택자가 최종 제출 버튼을 가리켜도 누르지 않고 사람에게 넘긴다.
    rec = Recording("t", f"{site_url}/info.html",
                    [Step("click", Target(css=["#submit"])), FINAL])
    p = _quick_prompter()
    player = _player(context, tmp_path, p)
    assert player.run(rec, RunOptions({})) is RunResult.ABORTED
    assert player.page.url.endswith("/info.html")
    assert [c[0] for c in p.calls] == ["wait_until"]


def test_advisor_cannot_press_final_submit(context, site_url, tmp_path):
    advisor = Mock()
    advisor.propose.side_effect = lambda goal, screenshot, dom: Proposal(
        "click", _ba_id_of(dom, "예약하기"), None, "예약하기를 누릅니다")
    rec = Recording("t", f"{site_url}/info.html",
                    [Step("click", Target(text="없는 버튼")), FINAL])
    p = _quick_prompter(confirms=[True, True, True])
    player = _player(context, tmp_path, p, advisor=advisor)
    assert player.run(rec, RunOptions({})) is RunResult.ABORTED
    assert player.page.url.endswith("/info.html")
    assert "confirm" not in [c[0] for c in p.calls]     # 제출 버튼 제안은 승인 요청조차 하지 않는다


def test_hidden_label_fallback_never_clicks_submit(context, tmp_path):
    html = ("<input type=checkbox id=agree style='display:none'>"
            "<label for=agree>예약하기</label>")
    rec = Recording("t", "data:text/html;charset=utf-8," + quote(html),
                    [Step("click", Target(css=["#agree"])),
                     Step("final_submit", Target(text="예약하기", css=["#go"]))])
    p = _quick_prompter()
    player = _player(context, tmp_path, p)
    assert player.run(rec, RunOptions({})) is RunResult.ABORTED
    assert player.page.evaluate("document.getElementById('agree').checked") is False
