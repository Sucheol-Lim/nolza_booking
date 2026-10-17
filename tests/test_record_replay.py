"""실제 녹화기로 가짜 사이트 흐름을 녹화한 뒤 실제 재생기로 재생한다(README 흐름)."""
from unittest.mock import Mock

from conftest import solve_captcha

from booking_assistant.human import FakePrompter
from booking_assistant.player import Player, RunOptions, RunResult
from booking_assistant.recorder import Recorder
from booking_assistant.recording import Recording
from booking_assistant.runlog import RunLog


def _record(context, site_url):
    r = Recorder(context)
    page = r.start(site_url + "/index.html")
    page.click("text=2026-11-03")
    page.click("text=19:00")
    page.click("#next")
    page.wait_for_url("**/captcha.html*", timeout=10000)
    page.fill("#captcha", "ABCD")              # 녹화 중 사람이 푼 보안문자 — 녹화되면 안 된다
    page.click("#captcha-ok")
    page.wait_for_url("**/info.html", timeout=5000)
    page.fill("#name", "홍길동")
    page.check("text=무통장입금")
    page.keyboard.press("Control+Shift+F")
    page.click("#submit")
    return r.wait_finished()


class _Watcher(FakePrompter):
    """confirm/wait_until 때의 화면과, 그때까지 done.html이 열렸는지 기록한다."""

    def __init__(self, ref, navs, **kw):
        super().__init__(**kw)
        self._ref, self._navs = ref, navs
        self.at_confirm = []          # (url, done.html 열림 여부)
        self.captcha_waits = []       # 보안문자 대기 시작 때 입력칸 값

    def confirm(self, title, body):
        page = self._ref["player"].page
        self.at_confirm.append((page.url, any("done.html" in u for u in self._navs),
                                page.input_value("#name")))
        return super().confirm(title, body)

    def wait_until(self, title, body, done, poll_s=0.5):
        page = self._ref["player"].page
        if "captcha.html" in page.url:
            self.captcha_waits.append(page.input_value("#captcha"))
        return super().wait_until(title, body, done, poll_s)


def test_record_then_replay_confirms_before_submit(_browser, site_url, tmp_path):
    rec_ctx = _browser.new_context()
    try:
        steps = _record(rec_ctx, site_url)
    finally:
        rec_ctx.close()
    assert not any(s.url and "captcha" in s.url for s in steps)
    assert "ABCD" not in [s.value for s in steps]
    assert steps[-1].kind == "final_submit"
    rec = Recording("readme", site_url + "/index.html", steps)

    ctx = _browser.new_context()
    try:
        navs = []
        ctx.on("request", lambda q: navs.append(q.url) if q.is_navigation_request() else None)
        ref = {}

        def on_wait():
            page = ref["player"].page
            if "captcha.html" in page.url:
                solve_captcha(page, "ABCD")

        p = _Watcher(ref, navs, confirms=[True], on_wait=on_wait)
        p.WAIT_TOTAL_S = 10
        player = ref["player"] = Player(ctx, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
        assert player.run(rec, RunOptions({})) is RunResult.SUBMITTED

        assert [c[0] for c in p.calls] == ["wait_until", "confirm"]   # 보안문자(사람) + 최종 확인
        url, done_loaded, name = p.at_confirm[0]
        assert url.endswith("/info.html") and not done_loaded       # 제출 전에 승인을 받았다
        assert name == "홍길동"
        assert p.captcha_waits == [""]                              # 재생기가 보안문자를 넣지 않았다
        assert solve_captcha.count == 1
        assert sum(u.endswith("/index.html") for u in navs) == 1    # 시작 URL은 한 번만 연다
        assert "예약 완료" in player.page.inner_text("body")
    finally:
        ctx.close()
