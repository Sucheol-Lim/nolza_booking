"""낯선 화면을 Claude API로 해석해 다음 동작을 제안한다. 제안은 사람이 아닌 코드가 적용하되,
CAPTCHA·결제·대기열 우회는 프롬프트에서 금지하고 불확실하면 사람에게 넘긴다."""
import base64
from dataclasses import dataclass
from typing import Literal

SYSTEM_PROMPT = """너는 사람이 확인하는 예약 도우미의 화면 해석기다. 스크린샷과 번호가 매겨진 화면 요소 목록, 목표를 보고 다음 동작 하나를 propose_action 도구로 제안한다.

규칙:
- CAPTCHA, 보안문자, 봇 검증(체크박스형 포함)은 절대 풀거나 시도하지 말고 ask_human을 반환한다.
- 결제를 절대 진행하지 않는다. 결제 화면이나 결제 버튼이면 ask_human을 반환한다.
- 대기열, 접속 제한, 속도 제한을 우회하지 않는다. 대기 중이면 wait 또는 ask_human을 반환한다(새로고침 제안 금지).
- 마감 등으로 대안을 골라야 하면 스스로 고르지 말고 ask_human을 반환한다.
- 확신이 없으면 ask_human을 반환한다.
- click/fill 에는 화면 요소 목록의 ba_id(정수)를 사용한다. fill 에는 value도 필요하다.
- reason은 한국어로 짧게 쓴다."""

ACTIONS = ("click", "fill", "wait", "ask_human")

PROPOSE_TOOL = {
    "name": "propose_action",
    "description": "화면에서 다음에 할 동작 하나를 제안한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "ba_id": {"type": ["integer", "null"]},
            "value": {"type": ["string", "null"]},
            "reason": {"type": "string"},
        },
        "required": ["action", "reason"],
    },
}

_TAG_JS = r"""
(maxChars) => {
  document.querySelectorAll('[data-ba-id]').forEach(e => e.removeAttribute('data-ba-id'));
  const sel = 'button, a, input, select, textarea, [role=button]';
  const lines = [];
  let n = 0, total = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.type === 'hidden') continue;
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.value || el.getAttribute('aria-label')
      || el.getAttribute('placeholder') || el.getAttribute('title') || '')
      .replace(/\s+/g, ' ').trim().slice(0, 80);
    let line = `[${n}] <${tag}${el.type && tag === 'input' ? ' type=' + el.type : ''}> ${text}`;
    if (el.getAttribute('placeholder') && el.getAttribute('placeholder') !== text)
      line += ` placeholder=${el.getAttribute('placeholder')}`;
    if (el.disabled) line += ' disabled';
    if (total + line.length + 1 > maxChars) break;
    el.setAttribute('data-ba-id', String(n));
    lines.push(line);
    total += line.length + 1;
    n++;
  }
  return lines.join('\n');
}
"""


def simplify_dom(page, max_chars: int = 15000) -> str:
    """보이는 상호작용 요소에 data-ba-id를 새로 부여하고 목록 문자열을 반환한다."""
    return page.evaluate(_TAG_JS, max_chars)


@dataclass
class Proposal:
    action: Literal["click", "fill", "wait", "ask_human"]
    ba_id: int | None
    value: str | None
    reason: str


def _ask_human(reason: str) -> Proposal:
    return Proposal("ask_human", None, None, reason)


class Advisor:
    def __init__(self, client, model: str = "claude-opus-5-5"):
        self.client = client
        self.model = model

    def propose(self, goal: str, screenshot: bytes, dom: str) -> Proposal:
        try:
            b64 = base64.b64encode(screenshot).decode("ascii")
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=[PROPOSE_TOOL],
                tool_choice={"type": "tool", "name": "propose_action"},
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    {"type": "text", "text": f"목표: {goal}\n\n화면 요소:\n{dom}"},
                ]}],
            )
            block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
            if block is None:
                return _ask_human("해석 응답에 제안이 없습니다")
            return self._validate(dict(block.input))
        except Exception as e:  # API 오류·파싱 실패는 모두 사람에게 넘긴다
            return _ask_human(f"화면 해석 실패: {type(e).__name__}: {e}")

    @staticmethod
    def _validate(d: dict) -> Proposal:
        action = d.get("action")
        reason = str(d.get("reason") or "")
        if action not in ACTIONS:
            return _ask_human(f"알 수 없는 제안 동작: {action!r}")
        ba_id, value = d.get("ba_id"), d.get("value")
        if action in ("click", "fill"):
            if not isinstance(ba_id, int) or isinstance(ba_id, bool):
                return _ask_human(f"요소 번호가 올바르지 않습니다: {ba_id!r}")
        if action == "fill" and not isinstance(value, str):
            return _ask_human("입력할 값이 없습니다")
        return Proposal(action, ba_id, value, reason)


def apply_proposal(page, p: Proposal, timeout_ms: float | None = None) -> None:
    """click/fill 제안을 data-ba-id 요소에 적용한다. 그 외 동작은 아무것도 하지 않는다.

    요소가 사라졌으면(오래된 ba_id) timeout_ms(기본: Playwright 기본값) 후 Playwright 오류를 낸다.
    """
    if p.action == "click":
        page.locator(f'[data-ba-id="{p.ba_id}"]').click(timeout=timeout_ms)
    elif p.action == "fill":
        page.locator(f'[data-ba-id="{p.ba_id}"]').fill(p.value or "", timeout=timeout_ms)
