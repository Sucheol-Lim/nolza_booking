import pytest

from booking_assistant.detect import PageState, detect_state


@pytest.mark.parametrize("path,state", [
    ("/index.html", PageState.NORMAL), ("/queue.html?wait=60", PageState.QUEUE),
    ("/captcha.html", PageState.CAPTCHA), ("/pay.html?pg=inicis", PageState.PAYMENT),
    ("/info.html", PageState.NORMAL),
])
def test_detect(browser_page, site_url, path, state):
    browser_page.goto(site_url + path)
    assert detect_state(browser_page) is state


# --- I3: 보이지 않는 보안문자 배지·문구만으로는 보안문자 화면이 아니다 ---

def _frame(site_url, marker, style):
    return f"<iframe src='{site_url}/done.html?f={marker}' style='{style}'></iframe>"


CHALLENGE = "recaptcha/api2/bframe"
BIG = "width:400px;height:400px;border:0"


@pytest.mark.parametrize("html,state", [
    ("visible", PageState.CAPTCHA),
    ("tiny", PageState.NORMAL),
    ("hidden", PageState.NORMAL),
    ("offscreen", PageState.NORMAL),
    ("badge", PageState.NORMAL),
    ("payment", PageState.PAYMENT),
    ("payment+captcha", PageState.PAYMENT),
])
def test_detect_iframes(browser_page, site_url, html, state):
    frames = {
        "visible": _frame(site_url, CHALLENGE, BIG),
        "tiny": _frame(site_url, CHALLENGE, "width:10px;height:10px"),
        "hidden": _frame(site_url, CHALLENGE, BIG + ";visibility:hidden"),
        "offscreen": _frame(site_url, CHALLENGE, BIG + ";position:absolute;top:-10000px"),
        "badge": _frame(site_url, "recaptcha/api2/anchor?size=invisible",
                        "width:256px;height:60px;position:fixed;bottom:0;right:0"),
        "payment": _frame(site_url, "pg=inicis", BIG),
        "payment+captcha": _frame(site_url, "pg=inicis", BIG) + _frame(site_url, CHALLENGE, BIG),
    }
    browser_page.set_content(f"<h1>예매</h1>{frames[html]}")
    assert detect_state(browser_page) is state


@pytest.mark.parametrize("text,state", [
    ("This site is protected by reCAPTCHA and the Google Privacy Policy", PageState.NORMAL),
    ("Please enter the CAPTCHA below", PageState.CAPTCHA),
    ("CAPTCHA를 입력하세요", PageState.CAPTCHA),
    ("자동입력 방지 문자를 입력하세요", PageState.CAPTCHA),
])
def test_detect_captcha_text(browser_page, text, state):
    browser_page.set_content(f"<p>{text}</p>")
    assert detect_state(browser_page) is state


def test_detect_body_read_has_short_timeout(monkeypatch, browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    seen = {}
    real = browser_page.inner_text

    def spy(selector, **kw):
        seen.update(kw)
        return real(selector, **kw)

    monkeypatch.setattr(browser_page, "inner_text", spy)
    detect_state(browser_page)
    assert seen.get("timeout") == 1000


# --- 풀린 뒤에도 화면에 남는 체크박스형 위젯 ---

def _solve_widget(page):
    page.evaluate("document.querySelector('[name$=\"-response\"]').value = 'token'")


@pytest.mark.parametrize("kind", ["recaptcha", "hcaptcha", "turnstile"])
def test_widget_unsolved_is_captcha(browser_page, site_url, kind):
    browser_page.goto(f"{site_url}/widget.html?kind={kind}")
    assert detect_state(browser_page) is PageState.CAPTCHA


@pytest.mark.parametrize("kind", ["recaptcha", "hcaptcha", "turnstile"])
def test_widget_solved_but_still_visible_is_normal(browser_page, site_url, kind):
    browser_page.goto(f"{site_url}/widget.html?kind={kind}")
    _solve_widget(browser_page)
    assert detect_state(browser_page) is PageState.NORMAL


def test_widget_token_cleared_again_is_captcha(browser_page, site_url):
    browser_page.goto(f"{site_url}/widget.html")
    _solve_widget(browser_page)
    browser_page.evaluate("document.querySelector('[name=\"g-recaptcha-response\"]').value = ''")
    assert detect_state(browser_page) is PageState.CAPTCHA       # 토큰 만료


def test_one_of_two_widgets_solved_is_still_captcha(browser_page, site_url):
    browser_page.goto(f"{site_url}/widget.html")
    browser_page.evaluate("""document.body.insertAdjacentHTML('beforeend',
        '<textarea name="g-recaptcha-response" style="display:none"></textarea>')""")
    _solve_widget(browser_page)                                   # 첫 번째만 채움
    assert detect_state(browser_page) is PageState.CAPTCHA


def test_ignore_widgets_keeps_text_captcha(browser_page, site_url):
    browser_page.goto(f"{site_url}/widget.html?kind=unknown")
    assert detect_state(browser_page, ignore_widgets=True) is PageState.NORMAL
    browser_page.goto(f"{site_url}/captcha.html")
    assert detect_state(browser_page, ignore_widgets=True) is PageState.CAPTCHA
