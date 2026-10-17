import re
from unittest.mock import ANY, Mock

from conftest import solve_captcha
from test_player import OPTS, make_recording

import booking_assistant.player as player_module
from booking_assistant.advisor import Proposal
from booking_assistant.human import FakePrompter
from booking_assistant.player import Player, RunOptions, RunResult
from booking_assistant.runlog import RunLog

NOTICE_START = "index.html?next=captcha.html%3Fnext%3Dnotice.html"   # 보안문자 뒤에 안내 화면이 끼어듦


def _ba_id_of(dom, text):
    for line in dom.splitlines():
        m = re.match(r"\[(\d+)\] <[^>]+> (.*)$", line)
        if m and m.group(2).strip() == text:
            return int(m.group(1))
    raise AssertionError(f"{text!r} not in dom:\n{dom}")


def _click_notice_advisor():
    advisor = Mock()
    advisor.propose.side_effect = lambda goal, screenshot, dom: Proposal(
        "click", _ba_id_of(dom, "확인"), None, "안내 확인")
    return advisor


def _captcha_or_notice(ref):
    """보안문자는 사람이 풀고, 안내 화면이면 사람이 확인을 누르는 on_wait."""
    def on_wait():
        page = ref["player"].page
        if "captcha.html" in page.url:
            solve_captcha(page, "ABCD")
        elif "notice.html" in page.url:
            page.click("button:has-text('확인')")
    return on_wait


def _captcha_only(ref):
    def on_wait():
        page = ref["player"].page
        if "captcha.html" in page.url:
            solve_captcha(page, "ABCD")
    return on_wait


def test_unknown_notice_screen_with_advisor(context, site_url, tmp_path):
    ref = {}
    advisor = _click_notice_advisor()
    p = FakePrompter(confirms=[True, True], on_wait=_captcha_only(ref))
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), advisor=advisor,
                                    queue_poll_s=0.1)
    assert player.run(make_recording(site_url, start=NOTICE_START), OPTS) is RunResult.SUBMITTED
    # 보안문자는 사람이 풀고(wait_until), 그다음 첫 확인이 제안 승인이다.
    assert p.calls[0][0] == "wait_until"
    assert p.calls[1] == ("confirm", ANY)
    assert "안내 확인" in p.calls[1][1] and "확인" in p.calls[1][1].split("대상:")[1]
    assert [c[0] for c in p.calls] == ["wait_until", "confirm", "confirm"]
    assert advisor.propose.call_count == 1
    goal = advisor.propose.call_args.kwargs["goal"]
    assert goal.startswith("다음 단계:") and "#name" in goal
    assert "name=김철수" in p.calls[-1][1]
    assert "직접 처리한 단계" not in p.calls[-1][1]
    assert "예약 완료" in player.page.inner_text("body")


def test_advisor_rejected_falls_back_to_human(context, site_url, tmp_path):
    ref = {}
    advisor = _click_notice_advisor()
    p = FakePrompter(confirms=[False, True], on_wait=_captcha_or_notice(ref))
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), advisor=advisor,
                                    queue_poll_s=0.1)
    assert player.run(make_recording(site_url, start=NOTICE_START), OPTS) is RunResult.SUBMITTED
    assert [c[0] for c in p.calls] == ["wait_until", "confirm", "wait_until", "confirm"]
    assert "예약 완료" in player.page.inner_text("body")


def test_stale_proposal_falls_back_to_human(monkeypatch, context, site_url, tmp_path):
    monkeypatch.setattr(player_module, "APPLY_TIMEOUT_MS", 300)
    ref = {}
    advisor = Mock()
    advisor.propose.return_value = Proposal("click", 999, None, "사라진 요소")   # 화면에 없는 ba_id
    p = FakePrompter(confirms=[True, True], on_wait=_captcha_or_notice(ref))
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), advisor=advisor,
                                    queue_poll_s=0.1)
    assert player.run(make_recording(site_url, start=NOTICE_START), OPTS) is RunResult.SUBMITTED
    assert [c[0] for c in p.calls] == ["wait_until", "confirm", "wait_until", "confirm"]
    assert advisor.propose.call_count == 1


def test_advisor_gives_up_after_three_proposals(context, site_url, tmp_path):
    ref = {}
    advisor = Mock()
    advisor.propose.return_value = Proposal("wait", None, None, "잠시 기다림")   # 화면이 안 바뀜
    p = FakePrompter(confirms=[True, True, True, True], on_wait=_captcha_or_notice(ref))
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), advisor=advisor,
                                    queue_poll_s=0.1)
    assert player.run(make_recording(site_url, start=NOTICE_START), OPTS) is RunResult.SUBMITTED
    assert advisor.propose.call_count == 3
    assert [c[0] for c in p.calls] == ["wait_until", "confirm", "confirm", "confirm",
                                       "wait_until", "confirm"]


def _soldout_run(context, site_url, tmp_path, values, candidates, choices, confirms=(True,)):
    ref = {}
    p = FakePrompter(confirms=list(confirms), choices=choices, on_wait=_captcha_only(ref))
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    opts = RunOptions(values={**OPTS.values, **values}, candidates=candidates)
    return player, p, player.run(make_recording(site_url), opts)


def test_soldout_offers_candidates(context, site_url, tmp_path):
    player, p, result = _soldout_run(
        context, site_url, tmp_path, {"date": "2026-11-02"},
        [{"date": "2026-11-01"}, {"date": "2026-11-03"}], choices=[1])
    assert result is RunResult.SUBMITTED
    assert p.calls[0] == ("choose", "2026-11-01\n2026-11-03")
    summary = p.calls[-1][1]
    assert "date=2026-11-03" in summary
    assert "2026-11-02" not in summary
    assert "직접 처리한 단계" not in summary


def test_soldout_choice_none_aborts(context, site_url, tmp_path):
    player, p, result = _soldout_run(
        context, site_url, tmp_path, {"date": "2026-11-02"},
        [{"date": "2026-11-01"}, {"date": "2026-11-03"}], choices=[None])
    assert result is RunResult.ABORTED
    assert [c[0] for c in p.calls] == ["choose"]          # 최종 확인도, 최종 클릭도 없음
    assert player.page.url.endswith("/index.html")


def test_never_chooses_alone(context, site_url, tmp_path):
    # 후보 중 가능한 것이 하나뿐이어도 사람이 고른다.
    player, p, result = _soldout_run(
        context, site_url, tmp_path, {"date": "2026-11-02"},
        [{"date": "2026-11-02"}, {"date": "2026-11-03"}], choices=[0])
    assert result is RunResult.SUBMITTED
    assert p.calls[0] == ("choose", "2026-11-03")
    assert "date=2026-11-03" in p.calls[-1][1]


def test_missing_value_without_candidates_offers_siblings(context, site_url, tmp_path):
    # 화면에 없는 날짜 + 후보 없음 → 녹화 당시 대상 근처의 가능한 형제 항목을 나열한다.
    player, p, result = _soldout_run(
        context, site_url, tmp_path, {"date": "2026-11-05"}, [], choices=[0])
    assert result is RunResult.SUBMITTED
    assert p.calls[0] == ("choose", "2026-11-01\n2026-11-03")
    assert "date=2026-11-01" in p.calls[-1][1]


def test_candidate_changes_earlier_var_reruns_from_that_step(context, site_url, tmp_path):
    # 시간이 마감 → 날짜까지 바꾸는 후보를 고르면 날짜 단계부터 다시 진행한다(같은 화면이라 goto 없음).
    navs = []
    context.on("request", lambda r: navs.append(r.url) if r.is_navigation_request() else None)
    player, p, result = _soldout_run(
        context, site_url, tmp_path, {"date": "2026-11-03", "time": "23:00"},
        [{"date": "2026-11-01", "time": "21:00"}], choices=[0])
    assert result is RunResult.SUBMITTED
    assert p.calls[0] == ("choose", "21:00 (date=2026-11-01)")
    summary = p.calls[-1][1]
    assert "date=2026-11-01" in summary and "time=21:00" in summary
    assert sum("index.html" in u for u in navs) == 1


def test_choice_changing_earlier_page_var_goes_back_once(context, site_url, tmp_path):
    # 결제수단(info 화면)이 마감 → 날짜(index 화면)까지 바꾸는 후보를 고르면
    # 날짜 단계 직전 URL로 한 번만 돌아가 다시 진행한다.
    navs = []
    context.on("request", lambda r: navs.append(r.url) if r.is_navigation_request() else None)
    rec = make_recording(site_url)
    rec.steps[5].var = "pay"
    ref = {}
    p = FakePrompter(confirms=[True], choices=[0], on_wait=_captcha_only(ref))
    player = ref["player"] = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    opts = RunOptions(values={**OPTS.values, "pay": "계좌이체"},
                      candidates=[{"date": "2026-11-01", "pay": "무통장입금"}])
    assert player.run(rec, opts) is RunResult.SUBMITTED
    assert [c[0] for c in p.calls] == ["wait_until", "choose", "wait_until", "confirm"]
    assert p.calls[1] == ("choose", "무통장입금 (date=2026-11-01)")
    summary = p.calls[-1][1]
    assert "date=2026-11-01" in summary and "pay=무통장입금" in summary
    assert sum("index.html" in u for u in navs) == 2
    assert "예약 완료" in player.page.inner_text("body")
