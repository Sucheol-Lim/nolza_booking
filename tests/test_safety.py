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


# --- I2b: 사람이 브라우저에서 직접 최종 제출한 경우 ---

MISSING_FINAL = Step("final_submit", Target(text="제출", css=["#nope"]))


def _human_on_info(ref, action):
    def on_wait():
        page = ref["player"].page
        if page.url.endswith("/info.html"):
            action(page)
    return on_wait


def test_manual_final_submit_recognized(context, site_url, tmp_path):
    ref = {}
    p = _quick_prompter(confirms=[True], on_wait=_human_on_info(ref, lambda pg: pg.click("#submit")))
    player = ref["player"] = _player(context, tmp_path, p)
    rec = Recording("t", f"{site_url}/info.html", [MISSING_FINAL])
    assert player.run(rec, RunOptions({})) is RunResult.SUBMITTED
    assert [c[0] for c in p.calls] == ["wait_until", "confirm"]
    assert "직접 제출하셨나요?" in p.calls[1][1]


def test_manual_final_submit_into_payment_hands_over(context, site_url, tmp_path):
    ref = {}

    def pay_by_card(page):
        page.check("text=신용카드")
        page.click("#submit")

    p = _quick_prompter(on_wait=_human_on_info(ref, pay_by_card))
    player = ref["player"] = _player(context, tmp_path, p)
    rec = Recording("t", f"{site_url}/info.html", [MISSING_FINAL])
    assert player.run(rec, RunOptions({})) is RunResult.HANDED_TO_PAYMENT
    assert [c[0] for c in p.calls] == ["wait_until"]


def test_manual_final_submit_denied_keeps_waiting(context, site_url, tmp_path):
    ref = {}
    p = _quick_prompter(confirms=[False], on_wait=_human_on_info(ref, lambda pg: pg.click("#submit")))
    player = ref["player"] = _player(context, tmp_path, p)
    rec = Recording("t", f"{site_url}/info.html", [MISSING_FINAL])
    assert player.run(rec, RunOptions({})) is RunResult.ABORTED
    assert [c[0] for c in p.calls] == ["wait_until", "confirm", "wait_until"]


# --- I4: Playwright 동작 오류는 실행을 죽이지 않고 사람에게 넘긴다 ---

VEIL_JS = """document.addEventListener('DOMContentLoaded', () => {
  if (!location.pathname.endsWith('/info.html')) return;
  const d = document.createElement('div');
  d.id = 'veil'; d.style.cssText = 'position:fixed;inset:0;z-index:9999';
  document.body.appendChild(d);
});"""


class _HumanFixesOnConfirm(FakePrompter):
    """'직접 처리' 확인 창이 뜨면 사람이 가림막을 걷고 무통장입금을 고른 것으로 친다."""

    def __init__(self, ref, **kw):
        super().__init__(**kw)
        self._ref = ref

    def confirm(self, title, body):
        if title == "직접 처리 확인":
            page = self._ref["player"].page
            page.evaluate("document.getElementById('veil').remove()")
            page.check("text=무통장입금")
        return super().confirm(title, body)


def test_action_error_routes_to_human(monkeypatch, context, site_url, tmp_path):
    import booking_assistant.player as player_module
    monkeypatch.setattr(player_module, "ACTION_TIMEOUT_MS", 300, raising=False)
    context.add_init_script(VEIL_JS)
    ref = {}
    p = _HumanFixesOnConfirm(ref, confirms=[True, True])
    alerter = Mock()
    player = ref["player"] = Player(context, p, alerter, RunLog(tmp_path), queue_poll_s=0.1)
    rec = Recording("t", f"{site_url}/info.html",
                    [Step("fill", Target(css=["#name"]), value="홍길동"),
                     Step("click", Target(text="무통장입금")), FINAL])
    assert player.run(rec, RunOptions({})) is RunResult.SUBMITTED
    assert [c[0] for c in p.calls] == ["confirm", "confirm"]
    assert "직접 처리한 단계" in p.calls[-1][1]
    assert "예약 완료" in player.page.inner_text("body")
    assert any(c.args[1] == "직접 처리 필요" for c in alerter.alert.call_args_list)


def test_final_click_error_alerts_and_waits_for_human(monkeypatch, context, site_url, tmp_path):
    import booking_assistant.player as player_module
    monkeypatch.setattr(player_module, "ACTION_TIMEOUT_MS", 300, raising=False)
    context.add_init_script(VEIL_JS)
    ref = {}

    def human_submits(page):
        page.evaluate("document.getElementById('veil') && document.getElementById('veil').remove()")
        page.click("#submit")

    p = _quick_prompter(confirms=[True, True], on_wait=_human_on_info(ref, human_submits))
    alerter = Mock()
    player = ref["player"] = Player(context, p, alerter, RunLog(tmp_path), queue_poll_s=0.1)
    rec = Recording("t", f"{site_url}/info.html",
                    [Step("fill", Target(css=["#name"]), value="홍길동"), FINAL])
    assert player.run(rec, RunOptions({})) is RunResult.SUBMITTED
    assert [c[0] for c in p.calls] == ["confirm", "wait_until", "confirm"]
    assert "직접 제출하셨나요?" in p.calls[2][1]
    assert any(c.args[1] == "최종 제출 실패" for c in alerter.alert.call_args_list)


def test_captcha_handoff_focuses_captcha_input_not_search_box(context, tmp_path):
    html = ("<input id=search placeholder=검색><p>보안문자 확인</p>"
            "<label for=c>자동입력 방지 문자</label><input id=c>")
    seen = {}
    p = FakePrompter(on_wait=lambda: seen.setdefault(
        "focus", player.page.evaluate("document.activeElement.id")))
    p.WAIT_TOTAL_S = 0.5
    player = _player(context, tmp_path, p)
    player.page = context.new_page()
    player.page.goto("data:text/html;charset=utf-8," + quote(html))
    player._captcha_handoff()
    assert seen["focus"] == "c"
