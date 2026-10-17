from datetime import datetime, timedelta
from unittest.mock import Mock

from conftest import solve_captcha

import booking_assistant.player as player_module
from booking_assistant.human import FakePrompter
from booking_assistant.player import Player, RunOptions, RunResult
from booking_assistant.recording import Recording, Step, Target
from booking_assistant.runlog import RunLog

OPTS = RunOptions(values={"date": "2026-11-03", "time": "19:00",
                          "name": "김철수", "phone": "010-0000-0000"})


def make_recording(site_url, pay="무통장입금", start="index.html"):
    return Recording(
        name="test",
        start_url=f"{site_url}/{start}",
        steps=[
            Step("click", Target(text="2026-11-01"), var="date"),
            Step("click", Target(text="19:00"), var="time"),
            Step("click", Target(role="button", name="다음", text="다음", css=["#next"])),
            Step("fill", Target(css=["#name"]), value="홍길동", var="name"),
            Step("fill", Target(css=["#phone"]), value="010-1111-1111", var="phone"),
            Step("click", Target(text=pay)),
            Step("final_submit", Target(css=["#submit"], text="예약하기")),
        ],
        variables=["date", "time", "name", "phone"],
    )


def captcha_prompter(confirms):
    holder = {}
    p = FakePrompter(confirms=confirms,
                     on_wait=lambda: solve_captcha(holder["player"].page, "ABCD"))
    return p, holder


def test_happy_path_submits(context, site_url, tmp_path):
    p, holder = captcha_prompter([True])
    player = holder["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    assert player.run(make_recording(site_url), OPTS) is RunResult.SUBMITTED
    assert "예약 완료" in player.page.inner_text("body")
    assert p.calls[-1][0] == "confirm"
    assert "김철수" in p.calls[-1][1]
    assert "무통장입금" in p.calls[-1][1]
    assert [c[0] for c in p.calls] == ["wait_until", "confirm"]   # 보안문자 1회 + 최종 확인뿐


def test_queue_never_reloads(context, site_url, tmp_path):
    navs = []
    context.on("request", lambda r: navs.append(r.url) if r.is_navigation_request() else None)
    p, holder = captcha_prompter([True])
    alerter = Mock()
    player = holder["player"] = Player(context, p, alerter, RunLog(tmp_path), queue_poll_s=0.1)
    assert player.run(make_recording(site_url), OPTS) is RunResult.SUBMITTED
    assert sum("queue.html" in u for u in navs) == 1
    queue_alerts = [c for c in alerter.alert.call_args_list if c.args[1] == "대기열 진입"]
    assert len(queue_alerts) == 1


def test_wrong_captcha_keeps_waiting(context, site_url, tmp_path):
    attempts = iter(["ZZZZ", "ABCD"])
    p = FakePrompter(confirms=[True],
                     on_wait=lambda: solve_captcha(player.page, next(attempts, "ABCD")))
    player = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    assert player.run(make_recording(site_url), OPTS) is RunResult.SUBMITTED
    assert solve_captcha.count == 2


def test_final_submit_rejected_aborts(context, site_url, tmp_path):
    p, holder = captcha_prompter([False])
    player = holder["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    assert player.run(make_recording(site_url), OPTS) is RunResult.ABORTED
    assert player.page.url.endswith("/info.html")


def test_card_payment_handed_over(context, site_url, tmp_path):
    p, holder = captcha_prompter([True])
    player = holder["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    result = player.run(make_recording(site_url, pay="신용카드"), OPTS)
    assert result is RunResult.HANDED_TO_PAYMENT
    assert "pay.html" in player.page.url


class _UrlAtConfirm(FakePrompter):
    def __init__(self, player_ref, **kw):
        super().__init__(**kw)
        self._player_ref = player_ref
        self.url_at_confirm = None

    def confirm(self, title, body):
        self.url_at_confirm = self._player_ref["player"].page.url
        return super().confirm(title, body)


def test_follows_popup(context, site_url, tmp_path):
    ref = {}
    p = _UrlAtConfirm(ref, confirms=[True])
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    result = player.run(make_recording(site_url, start="index.html?popup=1"), OPTS)
    assert result is RunResult.SUBMITTED
    assert p.url_at_confirm.endswith("/info.html")
    assert len(context.pages) == 2 and player.page is context.pages[-1]
    assert "예약 완료" in player.page.inner_text("body")


def test_start_at_waits(monkeypatch, context, site_url, tmp_path):
    slept = []
    monkeypatch.setattr(player_module, "sleep", lambda s: slept.append(s))
    navs = []
    context.on("request", lambda r: navs.append(r.url) if r.is_navigation_request() else None)
    p, holder = captcha_prompter([True])
    player = holder["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    opts = RunOptions(values=OPTS.values, start_at=datetime.now() + timedelta(seconds=5))
    assert player.run(make_recording(site_url), opts) is RunResult.SUBMITTED
    assert 4.5 <= sum(slept) <= 5.0
    assert sum("index.html" in u for u in navs) == 1


def _index_or_captcha(player_ref, on_index):
    """보안문자 화면이면 정답을 넣고, index 화면이면 on_index(page)를 부르는 on_wait."""
    def on_wait():
        page = player_ref["player"].page
        if "captcha.html" in page.url:
            solve_captcha(page, "ABCD")
        elif "index.html" in page.url:
            on_index(page)
    return on_wait


def test_missing_target_waits_for_human_and_flags_summary(context, site_url, tmp_path):
    ref, index_calls = {}, []

    def on_index(page):
        index_calls.append(page.url)
        if len(index_calls) >= 2:          # 첫 호출엔 아무것도 하지 않는다(즉시 완료되면 안 됨)
            page.click("text=19:00")
            page.click("#next")

    p = FakePrompter(confirms=[True], on_wait=_index_or_captcha(ref, on_index))
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    opts = RunOptions(values={**OPTS.values, "time": "23:00"})
    assert player.run(make_recording(site_url), opts) is RunResult.SUBMITTED
    assert len(index_calls) >= 2
    summary = p.calls[-1][1]
    assert "직접 처리한 단계" in summary
    assert "time=(직접 선택됨" in summary
    assert "23:00" not in summary
    assert "name=김철수" in summary
    assert "예약 완료" in player.page.inner_text("body")


def test_missing_target_that_appears_is_performed_by_player(context, site_url, tmp_path):
    ref = {}

    def inject(page):
        page.evaluate("""() => {
          if (document.getElementById('late')) return;
          const b = document.createElement('button');
          b.id = 'late'; b.type = 'button'; b.textContent = '23:00';
          b.onclick = () => sessionStorage.setItem('late-clicked', '1');
          document.getElementById('times').appendChild(b);
        }""")

    p = FakePrompter(confirms=[True], on_wait=_index_or_captcha(ref, inject))
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    opts = RunOptions(values={**OPTS.values, "time": "23:00"})
    assert player.run(make_recording(site_url), opts) is RunResult.SUBMITTED
    assert player.page.evaluate("sessionStorage.getItem('late-clicked')") == "1"
    summary = p.calls[-1][1]
    assert "직접 처리한 단계" not in summary
    assert "time=23:00" in summary
