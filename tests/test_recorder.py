from booking_assistant.recorder import Recorder, events_to_steps


def test_events_merge_inputs():
    ev = [{"type": "input", "target": {"css": ["#name"]}, "value": "홍"},
          {"type": "input", "target": {"css": ["#name"]}, "value": "홍길동"},
          {"type": "final_click", "target": {"text": "예약하기", "css": ["#submit"]}}]
    steps = events_to_steps(ev)
    assert [s.kind for s in steps] == ["fill", "final_submit"] and steps[0].value == "홍길동"


def test_events_nav_after_click_is_not_goto():
    ev = [{"type": "nav", "url": "http://x/a"},
          {"type": "click", "target": {"css": ["#go"]}},
          {"type": "nav", "url": "http://x/b"},
          {"type": "change", "target": {"css": ["#sel"]}, "value": "2"}]
    steps = events_to_steps(ev)
    assert [s.kind for s in steps] == ["goto", "click", "select"]
    assert steps[0].url == "http://x/a" and steps[2].value == "2"


def test_record_fake_site_final_click_blocked(context, browser_page, site_url):
    r = Recorder(context); page = r.start(site_url + "/info.html")
    page.fill("#name", "홍길동"); page.check("text=무통장입금")
    page.keyboard.press("Control+Shift+F"); page.click("#submit")
    steps = r.wait_finished()
    assert steps[-1].kind == "final_submit"
    assert page.url.endswith("/info.html")          # 실제 제출되지 않음
    assert [s.kind for s in steps] == ["goto", "fill", "click", "final_submit"]
    assert steps[1].value == "홍길동" and "#name" in steps[1].target.css
    assert steps[2].target.name == "무통장입금"


def test_password_not_recorded(context, site_url):
    r = Recorder(context); page = r.start(site_url + "/info.html")
    page.evaluate("document.body.insertAdjacentHTML('beforeend','<input type=password id=pw>')")
    page.fill("#pw", "secret"); page.keyboard.press("Control+Shift+S")
    assert all(s.value != "secret" for s in r.wait_finished())


def test_cc_autocomplete_not_recorded(context, site_url):
    r = Recorder(context); page = r.start(site_url + "/info.html")
    page.evaluate("document.body.insertAdjacentHTML('beforeend','<input type=text autocomplete=cc-number id=cc>')")
    page.fill("#cc", "4111111111111111"); page.keyboard.press("Control+Shift+S")
    assert all(s.value != "4111111111111111" for s in r.wait_finished())
