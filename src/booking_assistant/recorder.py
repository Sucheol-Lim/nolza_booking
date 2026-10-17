"""사용자의 예약 흐름을 페이지 안 JS로 캡처해 Step 목록으로 만든다."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from booking_assistant.detect import is_captcha_url
from booking_assistant.recording import Step, Target, same_page_url

BINDING_NAME = "__baRecord"
_TARGET_FIELDS = {f.name for f in fields(Target)}
_ACTION_EVENTS = ("click", "input", "change", "nav")


def _target(data: dict | None) -> Target | None:
    if not data:
        return None
    return Target(**{k: v for k, v in data.items() if k in _TARGET_FIELDS})


def events_to_steps(events: list[dict], start_url: str | None = None) -> list[Step]:
    """이벤트를 단계로 바꾼다. 첫 단계가 start_url로 가는 이동이면 빼 둔다(재생기가 이미 연다)."""
    steps: list[Step] = []
    for ev in events:
        kind = ev.get("type")
        target = _target(ev.get("target"))
        url = ev.get("url")
        if kind == "input":
            last = steps[-1] if steps else None
            if (last and last.kind == "fill" and last.target and target
                    and last.target.css == target.css):
                last.value = ev.get("value")
            else:
                steps.append(Step("fill", target, value=ev.get("value"), url=url))
        elif kind == "click":
            steps.append(Step("click", target, url=url))
        elif kind == "change":
            steps.append(Step("select", target, value=ev.get("value"), url=url))
        elif kind == "nav":
            if not steps or steps[-1].kind != "click":
                steps.append(Step("goto", url=url))
        elif kind == "final_click":
            steps.append(Step("final_submit", target, url=url))
    if steps and steps[0].kind == "goto" and same_page_url(steps[0].url, start_url):
        steps.pop(0)
    return steps


class Recorder:
    def __init__(self, context: BrowserContext):
        self.context = context
        self._events: list[dict] = []
        self._finished = False
        self._pages: list[Page] = []
        self._start_url: str | None = None

    def _on_event(self, source, payload: dict) -> None:
        if payload.get("type") == "stop":
            self._finished = True
            return
        if payload.get("type") in _ACTION_EVENTS and (
                payload.get("captcha") or is_captcha_url(payload.get("url"))):
            return      # 보안문자 화면의 입력·클릭은 녹화하지 않는다(재생 때 사람이 푼다)
        self._events.append(payload)
        if payload.get("type") == "final_click":
            self._finished = True

    def start(self, start_url: str) -> Page:
        # init script는 이후 생성되는 문서에만 적용되므로 등록 후 새 페이지를 연다.
        self._start_url = start_url
        script = Path(__file__).with_name("recorder.js").read_text(encoding="utf-8")
        self.context.expose_binding(BINDING_NAME, self._on_event)
        self.context.add_init_script(script)
        self.context.on("page", lambda p: self._pages.append(p))
        page = self.context.new_page()
        page.goto(start_url)
        return page

    def wait_finished(self) -> list[Step]:
        """final_click 또는 중지 단축키까지 이벤트를 처리하며 기다린다."""
        while not self._finished:
            open_pages = [p for p in self._pages if not p.is_closed()]
            if not open_pages:
                break
            try:
                open_pages[-1].wait_for_timeout(100)
            except Exception:  # 대기 중 페이지가 닫힘 → 다음 루프에서 다른 페이지로
                continue
        return events_to_steps(self._events, self._start_url)
