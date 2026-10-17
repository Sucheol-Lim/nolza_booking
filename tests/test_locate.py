from booking_assistant.locate import find, is_available, normalize_text
from booking_assistant.recording import Target


def test_find_by_css_first(browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    assert find(browser_page, Target(css=["#nope", "button:has-text('2026-11-01')"])) is not None


def test_find_by_text_ignores_whitespace(browser_page, site_url):
    browser_page.goto(site_url + "/index.html"); browser_page.click("text=2026-11-01")
    assert find(browser_page, Target(text="21:00")) is not None
    assert normalize_text("11월\n 1일 ") == "11월 1일"


def test_find_by_role_and_name(browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    assert find(browser_page, Target(role="button", name="다음")) is not None


def test_not_found_returns_none(browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    assert find(browser_page, Target(text="2030-01-01"), timeout_ms=300) is None


def test_soldout_not_available(browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    assert not is_available(find(browser_page, Target(text="2026-11-02")))
    assert is_available(find(browser_page, Target(text="2026-11-03")))


def test_next_param_chain_queue_captcha_notice_info(browser_page, site_url):
    browser_page.goto(site_url + "/index.html?next=captcha.html%3Fnext%3Dnotice.html")
    browser_page.click("#next")
    browser_page.wait_for_url("**/captcha.html*", timeout=10000)
    browser_page.fill("#captcha", "ABCD")
    browser_page.click("#captcha-ok")
    browser_page.wait_for_url("**/notice.html", timeout=5000)
    browser_page.click("button:has-text('확인')")
    browser_page.wait_for_url("**/info.html", timeout=5000)


# --- R18: 위치 기반(nth-of-type) 선택자는 녹화된 텍스트/이름으로 확인될 때만 쓴다 ---

CAPTCHA_OK = Target(role="button", name="확인", text="확인",
                    css=["#captcha-ok", 'button:has-text("확인")',
                         "body:nth-of-type(1) > button:nth-of-type(1)"])


def test_positional_css_never_resolves_to_other_button(browser_page, site_url):
    # info.html의 첫 번째 button은 '예약하기' — 녹화된 '확인'과 다르므로 찾지 못해야 한다.
    browser_page.goto(site_url + "/info.html")
    assert find(browser_page, CAPTCHA_OK, timeout_ms=300) is None


def test_css_hit_with_different_text_rejected(browser_page, site_url):
    browser_page.goto(site_url + "/info.html")
    assert find(browser_page, Target(text="다음", css=["#submit"]), timeout_ms=300) is None


def test_positional_css_alone_is_not_identity(browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    t = Target(css=["body:nth-of-type(1) > button:nth-of-type(1)"])
    assert find(browser_page, t, timeout_ms=300) is None


def test_positional_css_confirmed_by_text(browser_page, site_url):
    browser_page.goto(site_url + "/info.html")
    t = Target(text="예약하기", css=["body:nth-of-type(1) > button:nth-of-type(1)"])
    found = find(browser_page, t, timeout_ms=300)
    assert found is not None and found.get_attribute("id") == "submit"


def test_css_hit_confirmed_by_accessible_name(browser_page, site_url):
    browser_page.goto(site_url + "/info.html")
    assert find(browser_page, Target(name="이름", css=["#phone"]), timeout_ms=300) is None
    found = find(browser_page, Target(name="이름", css=["#name"]), timeout_ms=300)
    assert found is not None and found.get_attribute("id") == "name"


def test_shared_name_selector_picks_matching_radio(browser_page, site_url):
    browser_page.goto(site_url + "/info.html")
    found = find(browser_page, Target(role="radio", name="신용카드", css=['[name="pay"]']))
    assert found is not None and found.get_attribute("value") == "card"
