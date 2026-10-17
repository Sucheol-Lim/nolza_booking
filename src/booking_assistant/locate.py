"""녹화된 대상(Target)을 현재 화면에서 다시 찾는다."""
from __future__ import annotations

import re
import time

from playwright.sync_api import Locator, Page

from booking_assistant.recording import Target

_UNAVAILABLE_CLASS = re.compile(r"(^|[\s_-])(soldout|sold-out|disabled)($|[\s_-])|soldout|sold-out")


def normalize_text(s: str) -> str:
    return " ".join(s.split())


# 녹화기(recorder.js)의 텍스트/이름 계산과 같은 규칙으로 요소의 정체를 읽는다.
_IDENTITY_JS = r"""
(el) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const text = clean(el.innerText).slice(0, 80) || null;
  let name = null;
  const aria = el.getAttribute('aria-label');
  const tag = el.tagName.toLowerCase();
  if (aria) name = clean(aria);
  else if (el.labels && el.labels.length) name = clean(el.labels[0].innerText || el.labels[0].textContent);
  else if (tag === 'input' && ['button', 'submit', 'reset'].includes(el.type) && el.value) name = clean(el.value);
  else if (text) name = text;
  else name = clean(el.getAttribute('placeholder') || el.getAttribute('title')
    || el.getAttribute('alt')) || null;
  return { text, name };
}
"""

_MATCHES_CSS_JS = """
(el, selectors) => selectors.some(sel => { try { return el.matches(sel); } catch (e) { return false; } })
"""


def is_positional(selector: str) -> bool:
    """문서 구조상 위치로만 고르는 선택자(nth-of-type 등). 정체 확인 없이는 쓰지 않는다."""
    return ":nth-of-type(" in selector or ":nth-child(" in selector


def _identity(loc: Locator) -> tuple[str | None, str | None]:
    data = loc.evaluate(_IDENTITY_JS)
    return data.get("text"), data.get("name")


def matches_identity(loc: Locator, target: Target) -> bool:
    """요소의 텍스트 또는 접근성 이름이 녹화된 값과 같은지. 녹화된 값이 없으면 확인할 수 없다(False)."""
    if not (target.text or target.name):
        return False
    text, name = _identity(loc)
    return bool((target.text and text == normalize_text(target.text))
                or (target.name and name == normalize_text(target.name)))


def resembles(loc: Locator, target: Target) -> bool:
    """요소가 대상과 같아 보이는지(최종 제출 보호용): 위치 기반이 아닌 CSS가 맞거나 텍스트/이름이 같다."""
    try:
        stable = [c for c in target.css if not is_positional(c)]
        if stable and loc.evaluate(_MATCHES_CSS_JS, stable):
            return True
        return matches_identity(loc, target)
    except Exception:
        return False


def _first_confirmed(loc: Locator, target: Target, need_identity: bool) -> Locator | None:
    """보이는 첫 요소. need_identity면 녹화된 텍스트/이름과 같은 요소만 받는다."""
    try:
        for i in range(loc.count()):
            item = loc.nth(i)
            if item.is_visible() and (not need_identity or matches_identity(item, target)):
                return item
    except Exception:
        return None
    return None


def _by_text(page: Page, text: str) -> Locator | None:
    wanted = normalize_text(text)
    candidates = page.get_by_text(wanted)
    try:
        for i in range(candidates.count()):
            item = candidates.nth(i)
            if item.is_visible() and normalize_text(item.inner_text()) == wanted:
                return item
    except Exception:
        return None
    return None


def _by_css(page: Page, target: Target, selectors: list[str]) -> Locator | None:
    has_identity = bool(target.text or target.name)
    for selector in selectors:
        try:
            found = _first_confirmed(page.locator(selector), target, need_identity=has_identity)
        except Exception:
            found = None
        if found is not None:
            return found
    return None


def _try_once(page: Page, target: Target) -> Locator | None:
    """찾는 순서: 고정 CSS(녹화된 텍스트/이름이 있으면 일치해야 함) → 역할+이름 → 텍스트 →
    위치 기반 CSS(텍스트/이름이 일치할 때만). 위치 기반 선택자만으로는 절대 찾은 것으로 치지 않는다."""
    stable = [c for c in target.css if not is_positional(c)]
    positional = [c for c in target.css if is_positional(c)]
    found = _by_css(page, target, stable)
    if found is not None:
        return found
    if target.role and target.name:
        found = _first_confirmed(
            page.get_by_role(target.role, name=target.name, exact=True),  # type: ignore[arg-type]
            target, need_identity=False)
        if found is not None:
            return found
    if target.text:
        found = _by_text(page, target.text)
        if found is not None:
            return found
    if positional and (target.text or target.name):
        return _by_css(page, target, positional)
    return None


def find(page: Page, target: Target, timeout_ms: int = 3000) -> Locator | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        found = _try_once(page, target)
        if found is not None:
            return found
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(100)


def is_available(loc: Locator) -> bool:
    if loc.is_disabled():
        return False
    if (loc.get_attribute("aria-disabled") or "").lower() == "true":
        return False
    classes = loc.get_attribute("class") or ""
    return not any(_UNAVAILABLE_CLASS.search(c) for c in classes.split())
