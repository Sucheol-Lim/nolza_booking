"""녹화 재생기: 대기열은 기다리고, 보안문자·결제는 사람에게 넘기고, 최종 제출은 승인 후에만 누른다."""
from __future__ import annotations

import dataclasses
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from time import sleep

from playwright.sync_api import BrowserContext, Locator, Page
from playwright.sync_api import Error as PlaywrightError

from booking_assistant.advisor import Advisor, Proposal, apply_proposal, simplify_dom
from booking_assistant.detect import PageState, detect_state, is_captcha_url
from booking_assistant.human import Prompter
from booking_assistant.locate import (find, is_available, is_positional, matches_identity,
                                      normalize_text, resembles)
from booking_assistant.notify import Alerter
from booking_assistant.recording import Recording, Step, Target, same_page_url
from booking_assistant.runlog import RunLog

_OWN_VISIBLE = "own_visible"     # 못 찾던 대상이 나타남 → 재생기가 직접 수행
_NEXT_VISIBLE = "next_visible"   # 다음 단계 대상이 새로 나타남 → 사람이 처리함
_MOVED = "moved"                 # 화면(URL/페이지)이 바뀜 → 사람이 처리함

FIND_TIMEOUT_S = 3.0         # 정상 화면에서 대상을 기다리는 시간(대기열·보안문자 시간 제외)
FIND_SLICE_MS = 300           # 상태를 다시 확인하기 전 한 번에 찾는 시간
AFTER_SUBMIT_WAIT_S = 3.0     # 최종 제출 후 이동/팝업을 기다리는 시간
MAX_PROPOSALS = 3             # 한 단계에서 화면 해석 제안을 받는 최대 횟수(이후 사람 호출)
APPLY_TIMEOUT_MS = 5000       # 승인된 제안을 적용할 때 요소를 기다리는 시간
ACTION_TIMEOUT_MS = 5000      # 찾은 요소를 누르거나 입력할 때 기다리는 최대 시간
MAX_ACTION_TRIES = 2          # 동작 오류(요소가 막 다시 그려짐 등) 때 다시 시도하는 횟수(이후 사람)
MAX_CHOICES = 9               # 대안 선택 창에 보여 줄 최대 항목 수
_CAPTCHA_LABEL = re.compile(r"보안문자|자동입력|captcha", re.IGNORECASE)

_CHECKED_LABELS_JS = """
() => Array.from(document.querySelectorAll('input[type=radio]:checked')).map(el => {
  const label = (el.labels && el.labels[0]) || el.closest('label');
  return ((label ? label.innerText : el.value) || '').trim();
}).filter(Boolean)
"""

# 대상 근처(부모, 조부모)에서 같은 태그의 보이는 요소 텍스트
_SIBLING_TEXTS_JS = r"""
(el) => {
  const tag = el.tagName;
  const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  let box = el.parentElement;
  for (let depth = 0; box && depth < 2; depth++, box = box.parentElement) {
    const items = Array.from(box.getElementsByTagName(tag)).filter(visible);
    if (items.length >= 2)
      return items.map(e => (e.innerText || e.value || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
  }
  return [];
}
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


@dataclass
class _Choice:
    """사람이 고른 마감 대안: 새로 쓸 변수 값들."""
    values: dict[str, str]


class _FinalGuard(Exception):
    """최종 제출 버튼으로 보이는 요소를 최종 확인 없이 조작하려 함."""


def _final_target(steps: list[Step]) -> Target | None:
    return next((s.target for s in steps if s.kind == "final_submit"), None)


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
        self._candidates: list[dict[str, str]] = []
        self._final: Target | None = None    # 녹화의 최종 제출 대상: 그 밖의 단계에서는 절대 누르지 않는다
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
        values = dict(opts.values)
        steps = rec.apply(values)
        self._final = _final_target(steps)
        self._queue_alerted = False
        self._manual = []
        self._candidates = list(opts.candidates)
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

        url_before: dict[int, str] = {}    # 단계를 수행하기 직전 페이지 URL
        i = 0
        if steps and steps[0].kind == "goto" and (same_page_url(steps[0].url, rec.start_url)
                                                  or same_page_url(steps[0].url, self.page.url)):
            i = 1      # 시작 화면은 방금 열었다: 다시 열면 대기 순번을 잃을 수 있다
        while i < len(steps):
            following = steps[i + 1] if i + 1 < len(steps) else None
            url_before[i] = self.page.url
            outcome = self._run_step(steps[i], following, rec.steps[i])
            if isinstance(outcome, RunResult):
                return outcome
            if isinstance(outcome, _Choice):
                steps, i = self._apply_choice(rec, values, steps, i, outcome, url_before)
                continue
            if outcome == _MOVED:
                # 사람이 다른 화면으로 넘어갔다: 현재 단계부터 대상이 보이는 첫 단계에서 이어간다.
                resume = self._resync(steps, i)
                if isinstance(resume, RunResult):
                    return resume
                if resume == i:
                    # 현재 단계 대상이 새 화면에 있다(예: 끼어든 안내 화면을 사람이 넘김) — 직접 수행한다.
                    self._manual = [m for m in self._manual if m is not steps[i]]
                else:
                    self._manual += steps[i + 1:resume]
                i = resume
                continue
            i += 1
        self.log.event("aborted", "녹화에 최종 제출 단계가 없습니다", self.page)
        return RunResult.ABORTED

    def _apply_choice(self, rec: Recording, values: dict[str, str], steps: list[Step], i: int,
                      choice: _Choice, url_before: dict[int, str]) -> tuple[list[Step], int]:
        """고른 대안 값을 반영하고, 바뀐 변수가 처음 쓰인 단계부터 다시 진행할 위치를 돌려준다."""
        changed = {k for k, v in choice.values.items() if values.get(k) != v}
        values.update(choice.values)
        self._values.update(choice.values)
        new_steps = rec.apply(values)
        self._final = _final_target(new_steps)
        j = next((k for k in range(i + 1) if new_steps[k].var in changed), i)
        # 다시 진행할 단계는 더 이상 사람이 처리한 단계가 아니다. 나머지는 새 단계 목록을 가리키게 한다.
        self._manual = [new_steps[k] for k in range(j)
                        if any(m is steps[k] for m in self._manual)]
        url = url_before.get(j)
        if j < i and url and url != self.page.url:
            # 그 단계는 이전 페이지에 있었다: 그 단계 직전 URL로 한 번만 이동한다.
            self.log.event("rewind", f"{_describe_step(new_steps[j])}부터 다시 진행: {url}", self.page)
            self.page.goto(url)
        return new_steps, j

    def _run_step(self, step: Step, following: Step | None,
                  recorded: Step) -> RunResult | _Choice | str | None:
        """단계 하나. RunResult면 종료, _Choice면 사람이 마감 대안을 고름, _MOVED면 사람이 화면을
        넘김, 그 밖에는 다음 단계로. recorded는 변수 치환 전 녹화 단계."""
        if step.kind != "goto" and is_captcha_url(step.url):
            # 보안문자는 재생기가 절대 입력하지 않는다: 사람이 풀도록 _settle이 넘긴다.
            self.log.event("captcha_step_skipped", f"보안문자 화면 단계는 건너뜀: {_describe(step.target)}")
            return None
        if step.kind == "goto":
            result = self._settle()[0]
            if result is not None:
                return result
            self.page.goto(step.url)
            return None

        proposals = 0
        failures = 0
        while True:
            found = self._locate(step)
            if isinstance(found, RunResult):
                return found
            unavailable = found is not None and self._is_choice(step) and not _available(found)
            if found is not None and not unavailable:
                if step.kind == "final_submit":
                    result = self._final_submit(found)
                    if result is not None:
                        return result
                    continue      # 사람이 아직 제출하지 않음: 버튼을 다시 찾아 다시 승인을 받는다
                try:
                    self._perform(step, found)
                except _FinalGuard:
                    self.log.event("final_guard",
                                   f"최종 제출 버튼으로 보여 누르지 않음: {_describe(step.target)}", self.page)
                except PlaywrightError as e:
                    failures += 1
                    self.log.event("action_failed",
                                   f"{_describe(step.target)}: {type(e).__name__}", self.page)
                    if failures < MAX_ACTION_TRIES:
                        continue
                    return self._on_action_failed(step)
                else:
                    self.log.event(step.kind, _describe(step.target))
                    return None
            elif self._is_choice(step):
                # 고를 값이 없거나 마감: 사람이 대안을 고른다. 대안이 없으면 마감이면 사람 호출,
                # 대상을 못 찾았으면(낯선 화면일 수 있음) 아래 화면 해석으로 넘어간다.
                choice = self._offer_alternatives(step, recorded, found)
                if choice is not None:
                    return choice
            if (found is None and self.advisor is not None and step.kind != "final_submit"
                    and proposals < MAX_PROPOSALS):
                # 최종 제출 버튼은 해석 제안으로 누르지 않는다(최종 확인을 거쳐야 함).
                proposals += 1
                if self._apply_advice(step):
                    continue
            handled = self._on_target_missing(step, following, unavailable)
            if handled is None:
                return RunResult.ABORTED
            if step.kind == "final_submit" and handled == _MOVED:
                # 사람이 브라우저에서 직접 제출했을 수 있다.
                result = self._after_manual_submit()
                if result is not None:
                    return result
            # 대상이 나타났으면 직접 수행한다. 최종 제출은 사람이 대신 처리한 것으로 치지 않고
            # 버튼이 보일 때까지 다시 찾아 승인을 받는다.
            if handled == _OWN_VISIBLE or step.kind == "final_submit":
                continue
            self._manual.append(step)
            return handled

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
        self._focus_captcha_input()
        return self.prompter.wait_until(
            "보안문자 입력 필요",
            "브라우저에서 보안문자를 직접 입력해 주세요. 통과하면 자동으로 이어갑니다.",
            done=lambda: detect_state(self.page) is not PageState.CAPTCHA,
        )

    def _focus_captcha_input(self) -> None:
        """사람이 바로 입력하도록 보안문자 입력칸에 커서를 둔다(검색창 등 다른 입력칸보다 우선)."""
        candidates = [
            self.page.get_by_label(_CAPTCHA_LABEL),
            self.page.locator("input[id*=captcha i]:visible, input[name*=captcha i]:visible"),
            self.page.locator("input:visible"),
        ]
        for loc in candidates:
            try:
                if loc.count():
                    loc.first.focus(timeout=1000)
                    return
            except Exception:
                continue

    def _hand_to_payment(self) -> RunResult:
        self.log.event("payment", "결제 화면 — 사람에게 넘깁니다", self.page)
        self.alerter.alert(self.page, "결제 화면", "결제는 직접 진행해 주세요.")
        return RunResult.HANDED_TO_PAYMENT

    # --- 대상 찾기와 조작 -------------------------------------------------

    def _locate(self, step: Step) -> Locator | RunResult | None:
        deadline = time.monotonic() + FIND_TIMEOUT_S
        while True:
            result, waited = self._settle()
            if result is not None:
                return result
            if waited:
                deadline = time.monotonic() + FIND_TIMEOUT_S
            found = self._find(step.target, FIND_SLICE_MS, final_ok=step.kind == "final_submit")
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                return None

    def _find(self, target: Target, timeout_ms: int, final_ok: bool = False) -> Locator | None:
        """대상을 찾는다. final_ok가 아니면 최종 제출 버튼으로 보이는 요소는 찾은 것으로 치지 않는다."""
        found = find(self.page, target, timeout_ms=timeout_ms) or self._label_of_hidden_input(target)
        if found is not None and not final_ok and self._is_final_element(found):
            return None
        return found

    def _is_final_element(self, loc: Locator) -> bool:
        return self._final is not None and resembles(loc, self._final)

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
                if steps[k].kind == "goto" or (target is not None and self._find(
                        target, 0, final_ok=steps[k].kind == "final_submit") is not None):
                    return k
            if time.monotonic() >= deadline:
                return start
            self.page.wait_for_timeout(FIND_SLICE_MS)

    def _label_of_hidden_input(self, target: Target) -> Locator | None:
        """숨겨진 라디오/체크박스는 연결된 라벨을 대신 누른다.

        녹화된 텍스트/이름이 있으면 그와 같은 입력만 받는다(같은 name을 쓰는 다른 선택지를 누르지 않음).
        위치 기반 선택자는 그 확인이 있을 때만 쓴다."""
        has_identity = bool(target.text or target.name)
        for selector in target.css:
            if is_positional(selector) and not has_identity:
                continue
            try:
                matches = self.page.locator(selector)
                for n in range(matches.count()):
                    loc = matches.nth(n)
                    if loc.evaluate(
                            "el => el.tagName !== 'INPUT' || !['radio','checkbox'].includes(el.type)"):
                        continue
                    if has_identity and not matches_identity(loc, target):
                        continue
                    el_id = loc.get_attribute("id")
                    labels = [loc.locator("xpath=ancestor::label")]
                    if el_id:
                        labels.insert(0, self.page.locator(f"label[for={_css_string(el_id)}]"))
                    for label in labels:
                        if label.count() and label.first.is_visible():
                            return label.first
            except Exception:
                continue
        return None

    def _perform(self, step: Step, loc: Locator) -> None:
        if self._is_final_element(loc):      # 최종 제출 보호: _find가 걸러도 한 번 더 확인한다
            raise _FinalGuard()
        if step.kind == "fill":
            loc.fill(step.value or "", timeout=ACTION_TIMEOUT_MS)
        elif step.kind == "select":
            loc.select_option(step.value, timeout=ACTION_TIMEOUT_MS)
        elif _is_check_input(loc):
            loc.check(timeout=ACTION_TIMEOUT_MS)
        else:
            loc.click(timeout=ACTION_TIMEOUT_MS)

    # --- 마감 대안과 화면 해석 ---------------------------------------------

    @staticmethod
    def _is_choice(step: Step) -> bool:
        """값으로 화면 항목을 고르는 단계(날짜·시간 등). 마감이면 사람이 대안을 고른다."""
        return step.var is not None and step.kind == "click" and step.target is not None

    def _ready(self, step: Step, loc: Locator | None) -> bool:
        return loc is not None and (not self._is_choice(step) or _available(loc))

    def _value_available(self, step: Step, value: str) -> bool:
        target = dataclasses.replace(step.target, text=value, name=value, css=[])
        return self._ready(step, self._find(target, 0))

    def _candidate_label(self, var: str, cand: dict[str, str]) -> str:
        others = [f"{k}={v}" for k, v in cand.items() if k != var and self._values.get(k) != v]
        return f"{cand[var]} ({', '.join(others)})" if others else cand[var]

    def _sibling_texts(self, anchor: Locator | None) -> list[str]:
        if anchor is None:
            return []
        try:
            return [normalize_text(t) for t in anchor.evaluate(_SIBLING_TEXTS_JS)]
        except Exception:
            return []

    def _offer_alternatives(self, step: Step, recorded: Step,
                            found: Locator | None) -> _Choice | RunResult | None:
        """마감 대안을 사람이 고르게 한다. 스스로 고르지 않는다: 가능한 대안이 하나여도 묻는다.

        _Choice(선택함), RunResult.ABORTED(선택 취소), None(대안 없음 → 사람 호출).
        """
        var, current = step.var, step.target.text or ""
        options: list[tuple[str, dict[str, str]]] = []
        for cand in self._candidates:
            value = cand.get(var)
            if value is None or not self._value_available(step, value):
                continue
            label = self._candidate_label(var, cand)
            if label not in [o[0] for o in options]:
                options.append((label, dict(cand)))
        if not options:
            # 후보가 없으면 마감된 대상(또는 녹화 당시 대상) 근처의 가능한 같은 종류 항목을 보여 준다.
            anchor = found
            if anchor is None and recorded.target is not None:
                anchor = self._find(recorded.target, 0)
            for text in self._sibling_texts(anchor):
                if (text != normalize_text(current) and text not in [o[0] for o in options]
                        and self._value_available(step, text)):
                    options.append((text, {var: text}))
        if not options:
            return None
        options = options[:MAX_CHOICES]

        self.log.event("soldout", f"{var}={current} 선택 불가 — 대안 {len(options)}개", self.page)
        self.alerter.alert(self.page, "마감 — 대안 선택 필요",
                           f"'{current}'을(를) 선택할 수 없습니다. 대안을 골라 주세요.")
        idx = self.prompter.choose("마감된 항목 대안 선택", [label for label, _ in options])
        if idx is None or not 0 <= idx < len(options):
            self.log.event("aborted", "마감 대안 선택 취소", self.page)
            return RunResult.ABORTED
        label, values = options[idx]
        self.log.event("alternative", f"{var}: {label} 선택", self.page)
        return _Choice(values)

    def _apply_advice(self, step: Step) -> bool:
        """화면 해석 제안을 사람이 승인하면 적용한다. 적용했으면 True(같은 단계 재시도)."""
        try:
            dom = simplify_dom(self.page)
            shot = self.page.screenshot()
        except Exception as e:
            self.log.event("advisor_failed", f"화면 정보 수집 실패: {type(e).__name__}", self.page)
            return False
        proposal = self.advisor.propose(goal=_goal(step), screenshot=shot, dom=dom)
        self.log.event("advisor", f"{proposal.action} {proposal.ba_id}: {proposal.reason}", self.page)
        if proposal.action == "ask_human":
            return False
        if self._proposal_hits_final(proposal):
            self.log.event("final_guard", "제안 대상이 최종 제출 버튼이라 적용하지 않음", self.page)
            return False
        self.alerter.alert(self.page, "제안 확인 필요", f"낯선 화면입니다. 제안: {proposal.reason}")
        if not self.prompter.confirm("이렇게 진행할까요?",
                                     f"{proposal.reason}\n대상: {_proposal_target(proposal, dom)}"):
            self.log.event("advisor_rejected", "제안 거절", self.page)
            return False
        if self._proposal_hits_final(proposal):     # 승인하는 사이 화면이 바뀌었을 수 있다
            self.log.event("final_guard", "제안 대상이 최종 제출 버튼이라 적용하지 않음", self.page)
            return False
        try:
            apply_proposal(self.page, proposal, timeout_ms=APPLY_TIMEOUT_MS)
        except Exception as e:   # 화면이 바뀌어 ba_id가 사라짐 등
            self.log.event("advisor_failed", f"제안 적용 실패: {type(e).__name__}", self.page)
            return False
        return True

    def _proposal_hits_final(self, proposal: Proposal) -> bool:
        if proposal.action not in ("click", "fill"):
            return False
        loc = self.page.locator(f'[data-ba-id="{proposal.ba_id}"]')
        try:
            return any(self._is_final_element(loc.nth(n)) for n in range(loc.count()))
        except Exception:
            return True     # 확인할 수 없으면 적용하지 않는다

    # --- 사람 호출 ---------------------------------------------------------

    def _on_target_missing(self, step: Step, following: Step | None,
                           unavailable: bool = False) -> str | None:
        """대상을 못 찾았거나(또는 마감인데 대안이 없어) 사람에게 넘긴다.

        _MOVED / _OWN_VISIBLE / _NEXT_VISIBLE 중 하나, 중단이면 None.
        다음 단계 대상은 기다리기 시작할 때 안 보이다가 새로 보여야만 처리 완료로 본다.
        """
        desc = _describe(step.target)
        problem = "선택할 수 없고 대안도 없습니다" if unavailable else "찾지 못했습니다"
        self.log.event("missing", f"대상을 {problem}: {desc}", self.page)
        self.alerter.alert(self.page, "화면 확인 필요", f"'{desc}'을(를) {problem}. 직접 처리해 주세요.")
        start_page, start_url = self.page, self.page.url
        next_target = following.target if following is not None else None
        next_final = following is not None and following.kind == "final_submit"
        own_final = step.kind == "final_submit"
        next_was_visible = (next_target is not None
                            and self._find(next_target, 0, final_ok=next_final) is not None)
        outcome: dict[str, str] = {}

        def done() -> bool:
            if self.page is not start_page or self.page.url != start_url:
                outcome["v"] = _MOVED
            elif step.target is not None and self._ready(
                    step, self._find(step.target, 0, final_ok=own_final)):
                outcome["v"] = _OWN_VISIBLE
            elif (next_target is not None and not next_was_visible
                  and self._find(next_target, 0, final_ok=next_final) is not None):
                outcome["v"] = _NEXT_VISIBLE
            return "v" in outcome

        if not self.prompter.wait_until(
            "화면 확인 필요",
            f"'{desc}'을(를) {problem}. 브라우저에서 직접 처리해 주시면 이어갑니다.",
            done=done,
        ):
            return None
        self.log.event("missing_handled", f"{desc}: {outcome['v']}", self.page)
        return outcome["v"]

    def _on_action_failed(self, step: Step) -> str | RunResult:
        """찾은 대상을 자동으로 조작하지 못함(가려짐·막 사라짐 등): 사람이 직접 처리하고 승인한다.

        바뀌지 않는 화면에서는 끝났는지 알 수 없으므로 기다리지 않고 승인으로 받는다."""
        desc = _describe(step.target)
        self.alerter.alert(self.page, "직접 처리 필요",
                           f"'{desc}'을(를) 자동으로 처리하지 못했습니다. 직접 처리해 주세요.")
        start_page, start_url = self.page, self.page.url
        if not self.prompter.confirm(
                "직접 처리 확인",
                f"'{desc}' {_KIND_KO.get(step.kind, step.kind)}을(를) 자동으로 하지 못했습니다.\n"
                "브라우저에서 직접 처리한 뒤 승인하면 다음 단계로 이어갑니다. (Esc = 중단)"):
            self.log.event("aborted", f"직접 처리 거절: {desc}", self.page)
            return RunResult.ABORTED
        self._manual.append(step)
        moved = self.page is not start_page or self.page.url != start_url
        self.log.event("missing_handled", f"{desc}: 직접 처리", self.page)
        return _MOVED if moved else _NEXT_VISIBLE

    # --- 최종 제출 --------------------------------------------------------

    def _summary(self) -> list[str]:
        manual_vars = {s.var for s in self._manual if s.var}
        lines = [f"{name}=(직접 선택됨 — 화면에서 확인하세요)" if name in manual_vars
                 else f"{name}={value}" for name, value in self._values.items()]
        if self._manual:
            lines.append("직접 처리한 단계: " + ", ".join(_describe_step(s) for s in self._manual))
        return lines

    def _final_submit(self, loc: Locator) -> RunResult | None:
        """승인을 받고 최종 제출 버튼을 누른다. None이면 아직 제출되지 않음(다시 찾아 승인)."""
        lines = self._summary()
        try:
            lines += [f"선택: {label}" for label in self.page.evaluate(_CHECKED_LABELS_JS)]
        except Exception:
            pass
        button = _element_text(loc) or _describe(self._final)
        lines.append(f"누를 버튼: {button}")
        self.log.event("confirm", "최종 제출 확인 요청", self.page)
        if not self.prompter.confirm("최종 제출 확인", "\n".join(lines)):
            self.log.event("aborted", "최종 제출 거절", self.page)
            return RunResult.ABORTED

        before_page, before_url = self.page, self.page.url
        if not self._click_final(loc):
            return self._final_click_failed()
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

    def _click_final(self, loc: Locator) -> bool:
        """승인받은 최종 제출 버튼을 누른다. 실패하면 버튼을 다시 찾아 한 번 더 누른다."""
        for attempt in range(MAX_ACTION_TRIES):
            try:
                loc.click(timeout=ACTION_TIMEOUT_MS)
                return True
            except PlaywrightError as e:
                self.log.event("action_failed", f"최종 제출 클릭 실패: {type(e).__name__}", self.page)
            if attempt + 1 < MAX_ACTION_TRIES:
                again = self._find(self._final, FIND_SLICE_MS, final_ok=True) if self._final else None
                if again is None:
                    return False
                loc = again
        return False

    def _final_click_failed(self) -> RunResult | None:
        """최종 제출 버튼을 누르지 못함: 사람이 직접 누르도록 알리고 화면이 바뀔 때까지 기다린다."""
        msg = "최종 제출 버튼을 누르지 못했습니다. 브라우저에서 직접 눌러 주세요."
        self.alerter.alert(self.page, "최종 제출 실패", msg)
        start_page, start_url = self.page, self.page.url
        if not self.prompter.wait_until(
                "최종 제출 실패", msg + " 화면이 바뀌면 이어갑니다.",
                done=lambda: self.page is not start_page or self.page.url != start_url):
            self.log.event("aborted", "최종 제출 실패 후 중단", self.page)
            return RunResult.ABORTED
        return self._after_manual_submit()

    def _after_manual_submit(self) -> RunResult | None:
        """최종 제출 단계에서 사람이 화면을 넘김: 결제 화면이면 넘기고, 아니면 직접 제출했는지 묻는다.

        None이면 아직 제출하지 않음(계속 진행)."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=int(AFTER_SUBMIT_WAIT_S * 1000))
        except Exception:
            pass
        if detect_state(self.page) is PageState.PAYMENT:
            return self._hand_to_payment()
        if self.prompter.confirm("직접 제출 확인",
                                 "화면이 바뀌었습니다. 예약을 브라우저에서 직접 제출하셨나요?\n"
                                 "승인 = 제출함(종료)    Esc = 아직 안 함(계속)"):
            self.log.event("submitted", "사람이 직접 최종 제출함", self.page)
            return RunResult.SUBMITTED
        self.log.event("manual_submit_denied", "직접 제출하지 않음 — 계속 진행", self.page)
        return None


def _element_text(loc: Locator) -> str:
    try:
        return normalize_text(loc.inner_text(timeout=1000) or loc.get_attribute("value") or "")
    except Exception:
        return ""


def _css_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _is_check_input(loc: Locator) -> bool:
    try:
        return bool(loc.evaluate(
            "el => el.tagName === 'INPUT' && ['radio','checkbox'].includes(el.type)"))
    except Exception:
        return False


def _available(loc: Locator) -> bool:
    try:
        return is_available(loc)
    except Exception:   # 요소가 막 사라짐: 다시 찾을 때 드러난다
        return True


_KIND_KO = {"click": "누르기", "fill": "입력", "select": "선택", "final_submit": "최종 제출"}


def _goal(step: Step) -> str:
    t = step.target or Target()
    parts = []
    if t.text:
        parts.append(f"텍스트 '{t.text}'")
    elif t.name:
        parts.append(f"이름 '{t.name}'")
    if t.css:
        parts.append("선택자 " + ", ".join(t.css))
    goal = f"다음 단계: {' / '.join(parts) or '(대상 정보 없음)'} {_KIND_KO.get(step.kind, step.kind)}"
    if step.value:
        goal += f" (값: {step.value})"
    return goal + ". 이 대상이 지금 화면에 없습니다. 이 대상이 나오는 화면으로 넘어가는 동작을 제안하세요."


def _proposal_target(p: Proposal, dom: str) -> str:
    if p.action not in ("click", "fill"):
        return "(없음 — 잠시 기다림)" if p.action == "wait" else "(없음)"
    prefix = f"[{p.ba_id}] "
    line = next((ln[len(prefix):] for ln in dom.splitlines() if ln.startswith(prefix)),
                f"요소 {p.ba_id}(화면 목록에 없음)")
    return f"{line} ← '{p.value}' 입력" if p.action == "fill" else line


def _describe_step(step: Step) -> str:
    # 변수 단계는 의도한 값이 화면과 다를 수 있으니 값 대신 변수 이름으로 적는다.
    return f"{step.var} 선택" if step.var else _describe(step.target)


def _describe(target: Target | None) -> str:
    if target is None:
        return "(대상 없음)"
    return target.text or target.name or (target.css[0] if target.css else "(알 수 없음)")
