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
    # 시작 URL로 가는 첫 이동은 재생기가 이미 하므로 단계로 남기지 않는다(I1).
    assert [s.kind for s in steps] == ["fill", "click", "final_submit"]
    assert steps[0].value == "홍길동" and "#name" in steps[0].target.css
    assert steps[1].target.name == "무통장입금"


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


# --- C2: 보안문자 화면의 입력·클릭은 녹화하지 않는다 ---

def test_captcha_page_not_recorded(context, site_url):
    r = Recorder(context); page = r.start(site_url + "/captcha.html?next=info.html")
    page.fill("#captcha", "ABCD"); page.click("#captcha-ok")
    page.wait_for_url("**/info.html", timeout=5000)
    page.fill("#name", "홍길동"); page.keyboard.press("Control+Shift+S")
    steps = r.wait_finished()
    assert "ABCD" not in [s.value for s in steps]
    assert not any(s.target and s.target.text == "확인" for s in steps)
    assert [s.kind for s in steps if s.kind != "goto"] == ["fill"]
    assert steps[-1].value == "홍길동" and steps[-1].url.endswith("/info.html")


def test_events_from_captcha_url_dropped():
    r = Recorder(None)
    r._on_event(None, {"type": "input", "target": {"css": ["#c"]}, "value": "ABCD",
                       "url": "http://x/captcha.html"})
    r._on_event(None, {"type": "click", "target": {"css": ["#ok"]}, "url": "http://x/a",
                       "captcha": True})
    r._on_event(None, {"type": "click", "target": {"css": ["#go"]}, "url": "http://x/a"})
    assert [e["target"]["css"] for e in r._events] == [["#go"]]


def test_steps_keep_recorded_url():
    ev = [{"type": "click", "target": {"css": ["#go"]}, "url": "http://x/a"},
          {"type": "input", "target": {"css": ["#n"]}, "value": "v", "url": "http://x/b"}]
    assert [s.url for s in events_to_steps(ev)] == ["http://x/a", "http://x/b"]


def test_leading_goto_to_start_url_dropped():
    ev = [{"type": "nav", "url": "http://x/a/"}, {"type": "nav", "url": "http://x/b"},
          {"type": "click", "target": {"css": ["#go"]}, "url": "http://x/b"}]
    assert [s.kind for s in events_to_steps(ev, start_url="http://x/a")] == ["goto", "click"]
    assert events_to_steps(ev, start_url="http://x/a")[0].url == "http://x/b"
    assert [s.kind for s in events_to_steps(ev)] == ["goto", "goto", "click"]


# --- C3: 결제 화면과 카드 정보 입력칸은 녹화하지 않는다 ---

CARD = "4111111111111111"


def test_payment_page_plain_input_not_recorded(context, site_url):
    r = Recorder(context); page = r.start(site_url + "/pay.html?pg=inicis")
    page.evaluate("document.body.insertAdjacentHTML('beforeend','<input type=text id=num>')")
    page.fill("#num", CARD); page.click("#num")
    page.close()                                 # 결제 화면에서는 단축키도 받지 않는다
    steps = r.wait_finished()
    assert all(s.value != CARD for s in steps)
    assert not any(s.url and "pay.html" in s.url for s in steps)


def test_card_like_fields_not_recorded(context, site_url):
    r = Recorder(context); page = r.start(site_url + "/info.html")
    page.evaluate("""document.body.insertAdjacentHTML('beforeend',
        '<input type=text name=cardNo><input type=text id=cvc><input type=text id=user-pin>')""")
    page.fill("[name=cardNo]", CARD); page.fill("#cvc", "123"); page.fill("#user-pin", "0000")
    page.fill("#name", "홍길동"); page.keyboard.press("Control+Shift+S")
    values = [s.value for s in r.wait_finished()]
    assert CARD not in values and "123" not in values and "0000" not in values
    assert "홍길동" in values


def test_events_from_payment_url_or_card_field_dropped():
    r = Recorder(None)
    r._on_event(None, {"type": "input", "target": {"css": ["#num"]}, "value": CARD,
                       "url": "https://mobile.inicis.com/pay"})
    r._on_event(None, {"type": "input", "target": {"css": ['[name="card_no"]']}, "value": CARD,
                       "url": "http://x/a"})
    r._on_event(None, {"type": "input", "target": {"css": ["#name"]}, "value": "홍길동",
                       "url": "http://x/a"})
    assert [e["value"] for e in r._events] == ["홍길동"]
