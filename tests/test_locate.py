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
