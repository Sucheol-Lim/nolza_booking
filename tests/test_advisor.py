from types import SimpleNamespace
from unittest.mock import Mock

from booking_assistant.advisor import (
    SYSTEM_PROMPT,
    Advisor,
    Proposal,
    apply_proposal,
    simplify_dom,
)


def fake_client_returning(d):
    client = Mock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name="propose_action", input=d)]
    )
    return client


def test_simplify_dom_tags_buttons(browser_page, site_url):
    browser_page.goto(site_url + "/notice.html")
    dom = simplify_dom(browser_page)
    assert "확인" in dom and browser_page.locator("[data-ba-id]").count() >= 1


def test_simplify_dom_is_idempotent(browser_page, site_url):
    browser_page.goto(site_url + "/notice.html")
    first = simplify_dom(browser_page)
    second = simplify_dom(browser_page)
    assert first == second
    ids = browser_page.eval_on_selector_all(
        "[data-ba-id]", "els => els.map(e => e.getAttribute('data-ba-id'))"
    )
    assert len(ids) == len(set(ids))


def test_propose_parses_click():
    client = fake_client_returning({"action": "click", "ba_id": 0, "value": None, "reason": "안내 닫기"})
    p = Advisor(client).propose("info 페이지로 가기", b"png", "[0] button 확인")
    assert p == Proposal("click", 0, None, "안내 닫기")
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["tool_choice"] == {"type": "tool", "name": "propose_action"}


def test_propose_api_error_asks_human():
    client = Mock()
    client.messages.create.side_effect = Exception("boom")
    p = Advisor(client).propose("g", b"", "")
    assert p.action == "ask_human" and "boom" in p.reason


def test_propose_invalid_action_asks_human():
    client = fake_client_returning({"action": "pay", "ba_id": 1, "value": None, "reason": "x"})
    assert Advisor(client).propose("g", b"", "").action == "ask_human"


def test_propose_click_without_id_asks_human():
    client = fake_client_returning({"action": "click", "ba_id": None, "value": None, "reason": "x"})
    assert Advisor(client).propose("g", b"", "").action == "ask_human"


def test_propose_no_tool_use_asks_human():
    client = Mock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hi")]
    )
    assert Advisor(client).propose("g", b"", "").action == "ask_human"


def test_system_prompt_forbids_captcha_and_payment():
    assert "CAPTCHA" in SYSTEM_PROMPT and "ask_human" in SYSTEM_PROMPT
    assert "결제" in SYSTEM_PROMPT and "대기열" in SYSTEM_PROMPT


def test_apply_proposal_clicks(browser_page, site_url):
    browser_page.goto(site_url + "/notice.html")
    simplify_dom(browser_page)
    apply_proposal(browser_page, Proposal("click", 0, None, ""))
    browser_page.wait_for_url("**/info.html")
