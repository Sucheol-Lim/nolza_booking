"""사용자의 예약 흐름을 페이지 안 JS로 캡처해 Step 목록으로 만든다."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from playwright.sync_api import BrowserContext, Page

from booking_assistant.recording import Step, Target

BINDING_NAME = "__baRecord"
_TARGET_FIELDS = {f.name for f in fields(Target)}


def _target(data: dict | None) -> Target | None:
    if not data:
        return None
    return Target(**{k: v for k, v in data.items() if k in _TARGET_FIELDS})


def events_to_steps(events: list[dict]) -> list[Step]:
    steps: list[Step] = []
    for ev in events:
        kind = ev.get("type")
        target = _target(ev.get("target"))
        if kind == "input":
            last = steps[-1] if steps else None
            if (last and last.kind == "fill" and last.target and target
                    and last.target.css == target.css):
                last.value = ev.get("value")
            else:
                steps.append(Step("fill", target, value=ev.get("value")))
        elif kind == "click":
            steps.append(Step("click", target))
        elif kind == "change":
            steps.append(Step("select", target, value=ev.get("value")))
        elif kind == "nav":
            if not steps or steps[-1].kind != "click":
                steps.append(Step("goto", url=ev.get("url")))
        elif kind == "final_click":
            steps.append(Step("final_submit", target))
    return steps


class Recorder:
    def __init__(self, context: BrowserContext):
        self.context = context
        self._events: list[dict] = []
        self._finished = False
        self._pages: list[Page] = []

    def _on_event(self, source, payload: dict) -> None:
        if payload.get("type") == "stop":
            self._finished = True
            return
        self._events.append(payload)
        if payload.get("type") == "final_click":
            self._finished = True

    def start(self, start_url: str) -> Page:
        # init script는 이후 생성되는 문서에만 적용되므로 등록 후 새 페이지를 연다.
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
        return events_to_steps(self._events)
