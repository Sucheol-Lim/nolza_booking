"""녹화된 대상(Target)을 현재 화면에서 다시 찾는다."""
from __future__ import annotations

import re
import time

from playwright.sync_api import Locator, Page

from booking_assistant.recording import Target

_UNAVAILABLE_CLASS = re.compile(r"(^|[\s_-])(soldout|sold-out|disabled)($|[\s_-])|soldout|sold-out")


def normalize_text(s: str) -> str:
    return " ".join(s.split())


def _first_visible(loc: Locator) -> Locator | None:
    try:
        for i in range(loc.count()):
            item = loc.nth(i)
            if item.is_visible():
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


def _try_once(page: Page, target: Target) -> Locator | None:
    for selector in target.css:
        try:
            found = _first_visible(page.locator(selector))
        except Exception:
            found = None
        if found is not None:
            return found
    if target.role and target.name:
        found = _first_visible(page.get_by_role(target.role, name=target.name, exact=True))  # type: ignore[arg-type]
        if found is not None:
            return found
    if target.text:
        return _by_text(page, target.text)
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
