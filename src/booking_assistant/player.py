"""녹화 재생기: 대기열은 기다리고, 보안문자·결제는 사람에게 넘기고, 최종 제출은 승인 후에만 누른다."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from time import sleep

from playwright.sync_api import BrowserContext, Locator, Page

from booking_assistant.advisor import Advisor
from booking_assistant.detect import PageState, detect_state
from booking_assistant.human import Prompter
from booking_assistant.locate import find
from booking_assistant.notify import Alerter
from booking_assistant.recording import Recording, Step, Target
from booking_assistant.runlog import RunLog

_OWN_VISIBLE = "own_visible"     # 못 찾던 대상이 나타남 → 재생기가 직접 수행
_NEXT_VISIBLE = "next_visible"   # 다음 단계 대상이 새로 나타남 → 사람이 처리함
_MOVED = "moved"                 # 화면(URL/페이지)이 바뀜 → 사람이 처리함

FIND_TIMEOUT_S = 3.0         # 정상 화면에서 대상을 기다리는 시간(대기열·보안문자 시간 제외)
FIND_SLICE_MS = 300           # 상태를 다시 확인하기 전 한 번에 찾는 시간
AFTER_SUBMIT_WAIT_S = 3.0     # 최종 제출 후 이동/팝업을 기다리는 시간

_CHECKED_LABELS_JS = """
() => Array.from(document.querySelectorAll('input[type=radio]:checked')).map(el => {
  const label = (el.labels && el.labels[0]) || el.closest('label');
  return ((label ? label.innerText : el.value) || '').trim();
}).filter(Boolean)
"""


@dataclass
class RunOptions:
    values: dict[str, str]
    candidates: list[dict[str, str]] = field(default_factory=list)
    start_at: datetime | None = None


class RunResult(Enum):
    SUBMITTED = "submitted"
    HANDED_TO_PAYMENT = "handed_to_payment"
    ABORTED = "aborted"


class Player:
    def __init__(self, context: BrowserContext, prompter: Prompter, alerter: Alerter,
                 log: RunLog, advisor: Advisor | None = None, queue_poll_s: float = 1.0):
        self.context = context
        self.prompter = prompter
        self.alerter = alerter
        self.log = log
        self.advisor = advisor
        self.queue_poll_s = queue_poll_s
        self.page: Page | None = None
        self._queue_alerted = False
        self._values: dict[str, str] = {}
        self._manual: list[Step] = []      # 사람이 직접 처리한 단계
        for page in context.pages:
            self._watch(page)
        context.on("page", self._on_new_page)

    # --- 활성 페이지 추적 -------------------------------------------------

    def _on_new_page(self, page: Page) -> None:
        self._watch(page)
        self.page = page

    def _watch(self, page: Page) -> None:
        page.on("close", self._on_page_closed)

    def _on_page_closed(self, page: Page) -> None:
        if page is not self.page:
            return
        still_open = [p for p in self.context.pages if not p.is_closed() and p is not page]
        self.page = still_open[-1] if still_open else None

    # --- 실행 ------------------------------------------------------------

    def run(self, rec: Recording, opts: RunOptions) -> RunResult:
        steps = rec.apply(opts.values)
        self._queue_alerted = False
        self._manual = []
        used = list(rec.variables) + [s.var for s in rec.steps if s.var and s.var not in rec.variables]
        self._values = {name: opts.values[name] for name in used if name in opts.values}
        if opts.start_at is not None:
            delay = (opts.start_at - datetime.now(opts.start_at.tzinfo)).total_seconds()
            if delay > 0:
                self.log.event("start_wait", f"{opts.start_at.isoformat()}까지 대기")
                sleep(delay)
        if self.page is None:
            self.page = self.context.pages[-1] if self.context.pages else self.context.new_page()
        self.page.goto(rec.start_url)
        self.log.event("start", f"재생 시작: {rec.name}", self.page)

        i = 0
        while i < len(steps):
            following = steps[i + 1] if i + 1 < len(steps) else None
            outcome = self._run_step(steps[i], following)
            if isinstance(outcome, RunResult):
                return outcome
            if outcome == _MOVED:
                # 사람이 다른 화면으로 넘어갔다: 대상이 보이는 첫 남은 단계부터 이어간다.
                resume = self._resync(steps, i + 1)
                if isinstance(resume, RunResult):
                    return resume
                self._manual += steps[i + 1:resume]
                i = resume
                continue
            i += 1
        self.log.event("aborted", "녹화에 최종 제출 단계가 없습니다", self.page)
        return RunResult.ABORTED

    def _run_step(self, step: Step, following: Step | None) -> RunResult | str | None:
        """단계 하나. RunResult면 종료, _MOVED면 사람이 화면을 넘김, 그 밖에는 다음 단계로."""
        if step.kind == "goto":
            result = self._settle()[0]
            if result is not None:
                return result
            self.page.goto(step.url)
            return None

        while True:
            found = self._locate(step.target)
            if isinstance(found, RunResult):
                return found
            if found is not None:
                break
            handled = self._on_target_missing(step, following)
            if handled is None:
                return RunResult.ABORTED
            # 대상이 나타났으면 직접 수행한다. 최종 제출은 사람이 대신 처리한 것으로 치지 않고
            # 버튼이 보일 때까지 다시 찾아 승인을 받는다.
            if handled == _OWN_VISIBLE or step.kind == "final_submit":
                continue
            self._manual.append(step)
            return handled

        if step.kind == "final_submit":
            return self._final_submit(found)
        self._perform(step, found)
        self.log.event(step.kind, _describe(step.target))
        return None

    # --- 상태 처리 --------------------------------------------------------

    def _settle(self) -> tuple[RunResult | None, bool]:
        """대기열·보안문자·결제 화면을 처리한다. (결과, 기다린 적 있는지)."""
        waited = False
        while True:
            state = detect_state(self.page)
            if state is PageState.QUEUE:
                self._wait_queue()
            elif state is PageState.CAPTCHA:
                if not self._captcha_handoff():
                    self.log.event("aborted", "보안문자 대기 중 중단", self.page)
                    return RunResult.ABORTED, True
            elif state is PageState.PAYMENT:
                return self._hand_to_payment(), waited
            else:
                return None, waited
            waited = True

    def _wait_queue(self) -> None:
        self.log.event("queue", "대기열 진입 — 새로고침 없이 기다립니다", self.page)
        if not self._queue_alerted:
            self._queue_alerted = True
            self.alerter.alert(self.page, "대기열 진입", "대기열에 들어왔습니다. 자동으로 기다립니다.")
        poll_ms = max(1, int(self.queue_poll_s * 1000))
        while detect_state(self.page) is PageState.QUEUE:
            self.page.wait_for_timeout(poll_ms)
        self.log.event("queue_done", "대기열 통과", self.page)

    def _captcha_handoff(self) -> bool:
        self.log.event("captcha", "보안문자 화면 — 사람에게 넘깁니다", self.page)
        self.alerter.alert(self.page, "보안문자 입력 필요", "보안문자를 직접 입력해 주세요.")
        try:
            self.page.locator("input:visible").first.focus(timeout=1000)
        except Exception:
            pass
        return self.prompter.wait_until(
            "보안문자 입력 필요",
            "브라우저에서 보안문자를 직접 입력해 주세요. 통과하면 자동으로 이어갑니다.",
            done=lambda: detect_state(self.page) is not PageState.CAPTCHA,
        )

    def _hand_to_payment(self) -> RunResult:
        self.log.event("payment", "결제 화면 — 사람에게 넘깁니다", self.page)
        self.alerter.alert(self.page, "결제 화면", "결제는 직접 진행해 주세요.")
        return RunResult.HANDED_TO_PAYMENT

    # --- 대상 찾기와 조작 -------------------------------------------------

    def _locate(self, target: Target) -> Locator | RunResult | None:
        deadline = time.monotonic() + FIND_TIMEOUT_S
        while True:
            result, waited = self._settle()
            if result is not None:
                return result
            if waited:
                deadline = time.monotonic() + FIND_TIMEOUT_S
            found = self._find(target, FIND_SLICE_MS)
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                return None

    def _find(self, target: Target, timeout_ms: int) -> Locator | None:
        return find(self.page, target, timeout_ms=timeout_ms) or self._label_of_hidden_input(target)

    def _resync(self, steps: list[Step], start: int) -> int | RunResult:
        """start 이후 대상이 보이는 첫 단계의 위치. 못 찾으면 start(그 단계에서 다시 사람 호출)."""
        deadline = time.monotonic() + FIND_TIMEOUT_S
        while True:
            result, waited = self._settle()
            if result is not None:
                return result
            if waited:
                deadline = time.monotonic() + FIND_TIMEOUT_S
            for k in range(start, len(steps)):
                target = steps[k].target
                if steps[k].kind == "goto" or (target is not None and self._find(target, 0) is not None):
                    return k
            if time.monotonic() >= deadline:
                return start
            self.page.wait_for_timeout(FIND_SLICE_MS)

    def _label_of_hidden_input(self, target: Target) -> Locator | None:
        """숨겨진 라디오/체크박스는 연결된 라벨을 대신 누른다."""
        for selector in target.css:
            try:
                loc = self.page.locator(selector).first
                if loc.count() == 0 or loc.evaluate(
                        "el => el.tagName !== 'INPUT' || !['radio','checkbox'].includes(el.type)"):
                    continue
                el_id = loc.get_attribute("id")
                labels = [loc.locator("xpath=ancestor::label")]
                if el_id:
                    labels.insert(0, self.page.locator(f'label[for="{el_id}"]'))
                for label in labels:
                    if label.count() and label.first.is_visible():
                        return label.first
            except Exception:
                continue
        return None

    def _perform(self, step: Step, loc: Locator) -> None:
        if step.kind == "fill":
            loc.fill(step.value or "")
        elif step.kind == "select":
            loc.select_option(step.value)
        elif _is_check_input(loc):
            loc.check()
        else:
            loc.click()

    def _on_target_missing(self, step: Step, following: Step | None) -> str | None:
        """대상을 못 찾아 사람에게 넘긴다. (Task 9에서 advisor로 확장)

        _MOVED / _OWN_VISIBLE / _NEXT_VISIBLE 중 하나, 중단이면 None.
        다음 단계 대상은 기다리기 시작할 때 안 보이다가 새로 보여야만 처리 완료로 본다.
        """
        desc = _describe(step.target)
        self.log.event("missing", f"대상을 찾지 못함: {desc}", self.page)
        self.alerter.alert(self.page, "화면 확인 필요", f"'{desc}'을(를) 찾지 못했습니다. 직접 처리해 주세요.")
        start_page, start_url = self.page, self.page.url
        next_target = following.target if following is not None else None
        next_was_visible = next_target is not None and self._find(next_target, 0) is not None
        outcome: dict[str, str] = {}

        def done() -> bool:
            if self.page is not start_page or self.page.url != start_url:
                outcome["v"] = _MOVED
            elif step.target is not None and self._find(step.target, 0) is not None:
                outcome["v"] = _OWN_VISIBLE
            elif (next_target is not None and not next_was_visible
                  and self._find(next_target, 0) is not None):
                outcome["v"] = _NEXT_VISIBLE
            return "v" in outcome

        if not self.prompter.wait_until(
            "화면 확인 필요",
            f"'{desc}'을(를) 찾지 못했습니다. 브라우저에서 직접 처리해 주시면 이어갑니다.",
            done=done,
        ):
            return None
        self.log.event("missing_handled", f"{desc}: {outcome['v']}", self.page)
        return outcome["v"]

    # --- 최종 제출 --------------------------------------------------------

    def _summary(self) -> list[str]:
        manual_vars = {s.var for s in self._manual if s.var}
        lines = [f"{name}=(직접 선택됨 — 화면에서 확인하세요)" if name in manual_vars
                 else f"{name}={value}" for name, value in self._values.items()]
        if self._manual:
            lines.append("직접 처리한 단계: " + ", ".join(_describe_step(s) for s in self._manual))
        return lines

    def _final_submit(self, loc: Locator) -> RunResult:
        lines = self._summary()
        try:
            lines += [f"선택: {label}" for label in self.page.evaluate(_CHECKED_LABELS_JS)]
        except Exception:
            pass
        self.log.event("confirm", "최종 제출 확인 요청", self.page)
        if not self.prompter.confirm("최종 제출 확인", "\n".join(lines)):
            self.log.event("aborted", "최종 제출 거절", self.page)
            return RunResult.ABORTED

        before_page, before_url = self.page, self.page.url
        loc.click()
        deadline = time.monotonic() + AFTER_SUBMIT_WAIT_S
        while time.monotonic() < deadline:
            if self.page is not before_page or before_page.url != before_url:
                break
            self.page.wait_for_timeout(100)
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=int(AFTER_SUBMIT_WAIT_S * 1000))
        except Exception:
            pass
        if detect_state(self.page) is PageState.PAYMENT:
            return self._hand_to_payment()
        self.log.event("submitted", "최종 제출 완료", self.page)
        return RunResult.SUBMITTED


def _is_check_input(loc: Locator) -> bool:
    try:
        return bool(loc.evaluate(
            "el => el.tagName === 'INPUT' && ['radio','checkbox'].includes(el.type)"))
    except Exception:
        return False


def _describe_step(step: Step) -> str:
    # 변수 단계는 의도한 값이 화면과 다를 수 있으니 값 대신 변수 이름으로 적는다.
    return f"{step.var} 선택" if step.var else _describe(step.target)


def _describe(target: Target | None) -> str:
    if target is None:
        return "(대상 없음)"
    return target.text or target.name or (target.css[0] if target.css else "(알 수 없음)")
