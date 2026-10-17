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
