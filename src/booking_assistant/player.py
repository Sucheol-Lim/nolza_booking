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

FIND_TIMEOUT_S = 3.0          # 정상 화면에서 대상을 기다리는 시간(대기열·보안문자 시간 제외)
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
        if opts.start_at is not None:
            delay = (opts.start_at - datetime.now(opts.start_at.tzinfo)).total_seconds()
            if delay > 0:
                self.log.event("start_wait", f"{opts.start_at.isoformat()}까지 대기")
                sleep(delay)
        if self.page is None:
            self.page = self.context.pages[-1] if self.context.pages else self.context.new_page()
        self.page.goto(rec.start_url)
        self.log.event("start", f"재생 시작: {rec.name}", self.page)

        summary = self._summary(rec, opts)
        for i, step in enumerate(steps):
            following = steps[i + 1] if i + 1 < len(steps) else None
            result = self._run_step(step, following, summary)
            if result is not None:
                return result
        self.log.event("aborted", "녹화에 최종 제출 단계가 없습니다", self.page)
        return RunResult.ABORTED

    def _run_step(self, step: Step, following: Step | None,
                  summary_lines: list[str]) -> RunResult | None:
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
            # 마지막 단계는 사람이 대상을 보이게 해 주면 승인을 받아 직접 누른다.
            if not self._on_target_missing(step, following or step):
                return RunResult.ABORTED
            if step.kind != "final_submit":
                return None

        if step.kind == "final_submit":
            return self._final_submit(found, summary_lines)
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
            found = find(self.page, target, timeout_ms=FIND_SLICE_MS) or self._label_of_hidden_input(target)
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                return None

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

    def _on_target_missing(self, step: Step, following: Step) -> bool:
        """대상을 못 찾음. True면 사람이 처리했으니 진행한다. (Task 9에서 advisor로 확장)"""
        desc = _describe(step.target)
        self.log.event("missing", f"대상을 찾지 못함: {desc}", self.page)
        self.alerter.alert(self.page, "화면 확인 필요", f"'{desc}'을(를) 찾지 못했습니다. 직접 처리해 주세요.")
        start_url = self.page.url

        def done() -> bool:
            if self.page.url != start_url:
                return True
            return following.target is not None and find(self.page, following.target, timeout_ms=0) is not None

        return self.prompter.wait_until(
            "화면 확인 필요",
            f"'{desc}'을(를) 찾지 못했습니다. 브라우저에서 직접 처리해 주시면 이어갑니다.",
            done=done,
        )

    # --- 최종 제출 --------------------------------------------------------

    def _summary(self, rec: Recording, opts: RunOptions) -> list[str]:
        used = list(rec.variables) + [s.var for s in rec.steps if s.var and s.var not in rec.variables]
        return [f"{name}: {opts.values[name]}" for name in used if name in opts.values]

    def _final_submit(self, loc: Locator, summary_lines: list[str]) -> RunResult:
        lines = list(summary_lines)
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


def _describe(target: Target | None) -> str:
    if target is None:
        return "(대상 없음)"
    return target.text or target.name or (target.css[0] if target.css else "(알 수 없음)")
