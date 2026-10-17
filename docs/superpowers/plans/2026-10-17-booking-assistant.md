# 사람 확인형 예약 도우미 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사람이 한 번 녹화한 예약 흐름을 재생하되, CAPTCHA·낯선 화면·마감·최종 제출에서는 사람을 편하게 불러 확인받는 CLI 도구를 만든다.

**Architecture:** Playwright(sync API)로 도우미 전용 Chrome 프로필에 CDP 연결한다. 녹화는 페이지에 주입한 JS가 이벤트를 바인딩으로 보내 YAML `Recording`이 되고, 재생은 `Player`가 단계마다 `detect_state`로 화면 상태를 판별해 대기열 대기·사람 호출·결제 인계를 처리한다. 사람 개입은 `Prompter`(tkinter 확인 창)와 `Alerter`(소리·OS 알림·창 전면화·텔레그램)로 분리해 테스트에서는 가짜로 대체한다.

**Tech Stack:** Python 3.11+, playwright, PyYAML, anthropic, requests, desktop-notifier, pytest

**Spec:** `docs/superpowers/specs/2026-10-17-booking-assistant-design.md`

## Global Constraints

- Windows와 macOS 모두 지원. 명령줄에서 Python으로 직접 실행(패키징 없음). OS 분기는 `sys.platform`으로.
- CAPTCHA·봇 검증 자동 해독/우회 코드 금지. 대기열·속도 제한 우회 금지: 대기 중 `page.reload()`/반복 `goto` 금지, 다중 세션/계정 금지.
- 최종 제출은 항상 `Prompter.confirm` 승인 후에만 클릭. 자동 승인 타임아웃 없음.
- 마감 대안 선택은 항상 사람(`Prompter.choose`). 도우미가 스스로 고르지 않음.
- 결제사 팝업/iframe 안은 녹화·재생하지 않음. 결제 화면 감지 시 사람에게 넘기고 `RunResult.HANDED_TO_PAYMENT`로 종료.
- 카드번호 등 결제 정보는 녹화 파일·로그에 저장 금지(녹화기는 `type=password`, `autocomplete^=cc-` 입력을 기록하지 않음).
- 데이터 위치: `~/.booking-assistant/` 아래 `config.yaml`, `recordings/<name>.yaml`, `chrome-profile/`, `runs/<timestamp>/`.
- 화면 해석은 마스킹 없이 Claude API로 전송(사용자 결정). API 키(`ANTHROPIC_API_KEY`) 없으면 해석 없이 바로 사람 호출. 기본 모델 `claude-opus-5-5`, config `advisor.model`로 변경 가능. 구현자는 SDK 사용 전 `claude-api` 스킬을 읽는다.
- 텔레그램은 config의 `telegram.token`, `telegram.chat_id`가 있을 때만 활성. 알림+스크린샷만, 원격 승인 없음.
- 사용자 대면 문구는 한국어.

## Review Focus

1. 예약 단계 중 새 팝업 창/탭이 열림 → 재생은 가장 최근에 열린 페이지로 따라간다. (Task 8 `test_follows_popup`)
2. 사람이 CAPTCHA를 틀리게 입력해 같은 화면에 머묾 → 재개하지 않고 계속 기다린다. (Task 8 `test_wrong_captcha_keeps_waiting`)
3. 텔레그램/OS 알림 실패(네트워크 끊김, 알림 권한 없음) → 예외 없이 나머지 알림 계속, 실행 지속. (Task 4 `test_alert_survives_channel_failures`)
4. 전용 Chrome이 안 떠 있음 → 스택트레이스 대신 `booking-assistant chrome`을 안내하는 한국어 메시지와 종료 코드 2. (Task 10 `test_run_without_chrome_prints_guidance`)
5. 화면 라벨과 입력값의 공백·줄바꿈 차이(`"19:00 "` vs `"19:00"`, `"11월\n1일"` vs `"11월 1일"`) → 같은 항목으로 찾는다. (Task 2 `test_find_by_text_ignores_whitespace`)

---

### Task 1: 프로젝트 골격 + 녹화 데이터 모델

**Files:**
- Create: `pyproject.toml`, `src/booking_assistant/__init__.py`, `src/booking_assistant/recording.py`, `.gitignore`
- Test: `tests/test_recording.py`

**Interfaces:**
- Produces (`booking_assistant.recording`):
  - `@dataclass Target(role: str|None=None, name: str|None=None, text: str|None=None, css: list[str]=[], tag: str|None=None)`
  - `@dataclass Step(kind: Literal["goto","click","fill","select","final_submit"], target: Target|None=None, value: str|None=None, url: str|None=None, var: str|None=None)`
  - `@dataclass Recording(name: str, start_url: str, steps: list[Step], variables: list[str])` with `apply(values: dict[str,str]) -> list[Step]`
  - `save_recording(rec: Recording, path: Path) -> None`, `load_recording(path: Path) -> Recording`
  - `class MissingVariableError(KeyError)`

- [ ] **Step 1: pyproject 작성** — 패키지 `booking-assistant`, src 레이아웃, 의존성 위 Tech Stack, dev extra `pytest`, 콘솔 스크립트 `booking-assistant = booking_assistant.cli:main`. `.gitignore`에 `.venv/ __pycache__/ *.egg-info .pytest_cache/`.
- [ ] **Step 2: 설치** — Run: `python -m venv .venv`, `.venv` 활성화 후 `pip install -e .[dev]`, `playwright install chromium`. Expected: 오류 없이 완료.
- [ ] **Step 3: Write the failing tests**

```python
def test_roundtrip(tmp_path):
    rec = Recording("demo", "http://x/", [
        Step("click", Target(role="button", name="2026-11-01", text="2026-11-01", css=["#d1"]), var="date"),
        Step("fill", Target(css=["#name"]), value="홍길동", var="name"),
        Step("final_submit", Target(text="예약하기", css=["#submit"])),
    ], ["date", "name"])
    save_recording(rec, tmp_path / "demo.yaml")
    assert load_recording(tmp_path / "demo.yaml") == rec
    assert "홍길동" in (tmp_path / "demo.yaml").read_text(encoding="utf-8")  # allow_unicode

def test_apply_substitutes_fill_value_and_click_text():
    steps = rec.apply({"date": "2026-11-03", "name": "김철수"})
    assert steps[0].target.text == "2026-11-03" and steps[0].target.name == "2026-11-03"
    assert steps[0].target.css == []          # 녹화 당시 선택자는 다른 날짜를 가리키므로 버림
    assert steps[1].value == "김철수"
    assert rec.steps[1].value == "홍길동"      # 원본 불변

def test_apply_missing_variable():
    with pytest.raises(MissingVariableError):
        rec.apply({"date": "2026-11-03"})
```

- [ ] **Step 4: Run** `pytest tests/test_recording.py -v` → FAIL (ImportError)
- [ ] **Step 5: Implement** `recording.py` — YAML은 `yaml.safe_dump(..., allow_unicode=True, sort_keys=False)`, 파일은 UTF-8. `apply`는 `copy.deepcopy` 후 치환.
- [ ] **Step 6: Run** `pytest tests/test_recording.py -v` → PASS
- [ ] **Step 7: Commit** `feat: project skeleton and recording model`

---

### Task 2: 가짜 예약 사이트 + 대상 찾기

**Files:**
- Create: `tests/fake_site/index.html`, `queue.html`, `captcha.html`, `notice.html`, `info.html`, `done.html`, `pay.html`, `tests/conftest.py`, `src/booking_assistant/locate.py`
- Test: `tests/test_locate.py`

**Interfaces:**
- Produces (`tests/conftest.py`): fixture `site_url -> str` (스레드 `http.server`로 `tests/fake_site` 서빙, 세션 범위), fixture `browser_page -> playwright Page` (headless chromium, 테스트마다 새 context), fixture `context` (그 page의 BrowserContext).
- Fake site 동작(이후 테스트가 의존):
  - `index.html`: 날짜 버튼 `2026-11-01`, `2026-11-02`(`class="soldout" disabled`), `2026-11-03`. 날짜 클릭 시 시간 버튼 `19:00`, `21:00 `(뒤 공백, Review Focus 5) 표시. `다음` 버튼 → `queue.html?wait=2`.
  - `queue.html`: "현재 대기순번 N" 표시, `wait`초 후 `location.href = "captcha.html"`(쿼리 `next` 있으면 그 페이지).
  - `captcha.html`: "자동입력 방지 문자" 라벨, `#captcha` 입력, 정답 `ABCD`일 때만 `info.html`로 이동, 오답은 "다시 입력" 표시하고 머묾.
  - `notice.html`: 녹화에 없는 안내 화면, `확인` 버튼 → `info.html`.
  - `info.html`: `#name`, `#phone`, 라디오 `무통장입금`/`신용카드`, `예약하기`(#submit). 무통장 → `done.html`("예약 완료"), 신용카드 → `window.open("pay.html?pg=inicis")`.
  - `index.html?popup=1`: `다음`이 `window.open("queue.html?wait=0&next=info.html")`.
- Produces (`booking_assistant.locate`):
  - `normalize_text(s: str) -> str` — 모든 공백류를 단일 공백으로, 양끝 제거
  - `find(page: Page, target: Target, timeout_ms: int = 3000) -> Locator | None` — 순서: css 후보 각각 → role+name → 정규화 텍스트 일치. 보이는 첫 요소.
  - `is_available(loc: Locator) -> bool` — `disabled`, `aria-disabled="true"`, class에 `soldout|disabled|sold-out` 이면 False

- [ ] **Step 1: 가짜 사이트와 conftest 작성** (위 동작 그대로, 외부 리소스 없음)
- [ ] **Step 2: Write the failing tests**

```python
def test_find_by_css_first(browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    assert find(browser_page, Target(css=["#nope", "button:has-text('2026-11-01')"])) is not None

def test_find_by_text_ignores_whitespace(browser_page, site_url):
    browser_page.goto(site_url + "/index.html"); browser_page.click("text=2026-11-01")
    assert find(browser_page, Target(text="21:00")) is not None
    assert normalize_text("11월\n 1일 ") == "11월 1일"

def test_not_found_returns_none(browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    assert find(browser_page, Target(text="2030-01-01"), timeout_ms=300) is None

def test_soldout_not_available(browser_page, site_url):
    browser_page.goto(site_url + "/index.html")
    assert not is_available(find(browser_page, Target(text="2026-11-02")))
    assert is_available(find(browser_page, Target(text="2026-11-03")))
```

- [ ] **Step 3: Run** `pytest tests/test_locate.py -v` → FAIL
- [ ] **Step 4: Implement** `locate.py`
- [ ] **Step 5: Run** `pytest tests/test_locate.py -v` → PASS
- [ ] **Step 6: Commit** `feat: fake booking site and target locator`

---

### Task 3: 화면 상태 판별

**Files:**
- Create: `src/booking_assistant/detect.py`
- Test: `tests/test_detect.py`

**Interfaces:**
- Consumes: Task 2 fixtures
- Produces: `class PageState(Enum): NORMAL, QUEUE, CAPTCHA, PAYMENT`; `detect_state(page: Page) -> PageState`; 상수 `CAPTCHA_MARKERS`, `QUEUE_MARKERS`, `PAYMENT_URL_MARKERS`
  - CAPTCHA: 본문 텍스트에 `보안문자|자동입력 방지|자동입력방지|captcha|로봇이 아닙니다` 또는 iframe src에 `recaptcha|hcaptcha|turnstile`
  - QUEUE: `대기순번|대기 순번|접속 대기|대기 중입니다|waiting room|queue`
  - PAYMENT: 페이지 URL 또는 iframe src에 `inicis|kcp|tosspayments|nicepay|kakaopay|naverpay|payco|danal|kginicis`
  - 우선순위: PAYMENT > CAPTCHA > QUEUE > NORMAL

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("path,state", [
    ("/index.html", PageState.NORMAL), ("/queue.html?wait=60", PageState.QUEUE),
    ("/captcha.html", PageState.CAPTCHA), ("/pay.html?pg=inicis", PageState.PAYMENT),
    ("/info.html", PageState.NORMAL),
])
def test_detect(browser_page, site_url, path, state):
    browser_page.goto(site_url + path)
    assert detect_state(browser_page) is state
```

- [ ] **Step 2: Run** `pytest tests/test_detect.py -v` → FAIL
- [ ] **Step 3: Implement** `detect.py` (텍스트는 `page.inner_text("body")` 소문자 비교, 페이지가 이동 중이면 NORMAL 반환하고 예외 삼킴)
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: page state detection`

---

### Task 4: 알림 (텔레그램 + 소리 + OS 알림 + 창 전면화)

**Files:**
- Create: `src/booking_assistant/telegram.py`, `src/booking_assistant/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Produces:
  - `TelegramClient(token: str, chat_id: str, session: requests.Session | None = None)`; `send(text: str, photo: bytes | None = None) -> bool` — photo 있으면 `sendPhoto`(caption=text), 없으면 `sendMessage`. timeout 10초. 실패 시 False(예외 없음).
  - `Alerter(telegram: TelegramClient | None = None, sound: bool = True, os_notify: bool = True)`; `alert(page: Page | None, title: str, body: str) -> None` — 순서: `page.bring_to_front()`, 소리(Windows `winsound.MessageBeep`, macOS `afplay /System/Library/Sounds/Glass.aiff`), OS 알림(`desktop-notifier`), 텔레그램(`page.screenshot()` 첨부). 채널별 try/except, 절대 raise 안 함.
  - Alerter 내부 채널은 `_channels: list[Callable[[Page|None,str,str],None]]`로 두어 테스트에서 교체 가능.

- [ ] **Step 1: Write the failing tests**

```python
def test_telegram_send_photo_uses_sendPhoto():
    s = Mock(); s.post.return_value.ok = True
    assert TelegramClient("T", "C", s).send("CAPTCHA", b"png")
    url = s.post.call_args.args[0]
    assert url == "https://api.telegram.org/botT/sendPhoto"
    assert s.post.call_args.kwargs["data"] == {"chat_id": "C", "caption": "CAPTCHA"}

def test_telegram_network_error_returns_false():
    s = Mock(); s.post.side_effect = requests.ConnectionError()
    assert TelegramClient("T", "C", s).send("x") is False

def test_alert_survives_channel_failures():
    calls = []
    a = Alerter(sound=False, os_notify=False)
    a._channels = [Mock(side_effect=RuntimeError()), lambda p, t, b: calls.append(t)]
    a.alert(None, "제목", "본문")
    assert calls == ["제목"]
```

- [ ] **Step 2: Run** `pytest tests/test_notify.py -v` → FAIL
- [ ] **Step 3: Implement** 두 파일
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: 수동 확인(해당 OS에서)** `python -c "from booking_assistant.notify import Alerter; Alerter().alert(None,'테스트','알림 확인')"` → 소리와 OS 알림이 뜸
- [ ] **Step 6: Commit** `feat: alerter with telegram, sound, os notification`

---

### Task 5: 사람 확인 창 + 실행 로그

**Files:**
- Create: `src/booking_assistant/human.py`, `src/booking_assistant/runlog.py`
- Test: `tests/test_human.py`

**Interfaces:**
- Produces (`human.py`):
  - `class Prompter(Protocol)`: `confirm(title: str, body: str) -> bool`; `choose(title: str, options: list[str]) -> int | None` (None=중단); `wait_until(title: str, body: str, done: Callable[[], bool], poll_s: float = 0.5) -> bool` (done()이 True면 창 닫고 True, Esc면 False)
  - `TkPrompter()` — 항상 위(`-topmost`) 작은 창. 키: Enter=승인, 숫자 1–9=선택, Esc=중단. 안내 문구에 키를 표시. `wait_until`은 `root.after(poll_ms)` 루프로 `done()` 확인(메인 스레드에서만 동작; macOS 요구사항).
  - `FakePrompter(confirms: list[bool] = [], choices: list[int|None] = [], on_wait: Callable[[], None] | None = None)` — 호출 기록 `calls: list[tuple[str, str]]` (메서드명, 본문/옵션 문자열); `wait_until`은 먼저 `on_wait()`(사람 동작 흉내)를 호출한 뒤 `done()`이 True가 될 때까지 `poll_s` 간격으로 폴링하고, 1초 안에 안 되면 `on_wait()`를 다시 호출, 총 30초 넘으면 False.
  - 테스트 헬퍼(`tests/conftest.py`에 추가): `solve_captcha(page, text)` — `#captcha`에 입력 후 확인 클릭, 호출 횟수를 `solve_captcha.count`에 누적.
- Produces (`runlog.py`): `RunLog(dir: Path)`; `event(kind: str, message: str, page: Page | None = None) -> None` — `events.jsonl`에 `{"t": iso, "kind", "message", "url", "screenshot"}` 추가, page 있으면 `NNN.png` 저장.

- [ ] **Step 1: Write the failing tests**

```python
def test_fake_prompter_scripted():
    p = FakePrompter(confirms=[True], choices=[1])
    assert p.confirm("최종 제출", "요약") is True
    assert p.choose("대안", ["a", "b"]) == 1
    assert [c[0] for c in p.calls] == ["confirm", "choose"]

def test_fake_wait_until_calls_hook():
    state = {"done": False}
    p = FakePrompter(on_wait=lambda: state.update(done=True))
    assert p.wait_until("CAPTCHA", "입력", lambda: state["done"], poll_s=0.01)

def test_runlog_writes_jsonl(tmp_path):
    log = RunLog(tmp_path); log.event("queue", "대기 시작")
    rec = json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["kind"] == "queue" and rec["message"] == "대기 시작"
```

- [ ] **Step 2: Run** `pytest tests/test_human.py -v` → FAIL
- [ ] **Step 3: Implement** 두 파일
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: 수동 확인** `python -c "from booking_assistant.human import TkPrompter as T; print(T().choose('대안 선택', ['19:00','21:00']))"` → 창이 맨 위에 뜨고 `2` 키로 `1` 출력
- [ ] **Step 6: Commit** `feat: confirmation prompter and run log`

---

### Task 6: 낯선 화면 해석 (Claude API)

**Files:**
- Create: `src/booking_assistant/advisor.py`
- Test: `tests/test_advisor.py`

**Interfaces:**
- Produces:
  - `simplify_dom(page: Page, max_chars: int = 15000) -> str` — 보이는 상호작용 요소(button, a, input, select, textarea, [role=button])에 `data-ba-id="N"` 속성을 부여하고 `[N] <tag> text/label/placeholder/disabled` 줄 목록 반환
  - `@dataclass Proposal(action: Literal["click","fill","wait","ask_human"], ba_id: int|None, value: str|None, reason: str)`
  - `Advisor(client: anthropic.Anthropic, model: str = "claude-opus-5-5")`; `propose(goal: str, screenshot: bytes, dom: str) -> Proposal` — 이미지+DOM+목표를 보내고 JSON 응답 파싱(tool use 또는 구조화 출력 권장). 파싱 실패/API 오류 시 `Proposal("ask_human", None, None, "<오류 요약>")`.
  - 시스템 프롬프트에 고정: CAPTCHA 풀이 금지(CAPTCHA면 `ask_human`), 결제 진행 금지, 확신 없으면 `ask_human`.
  - `apply_proposal(page: Page, p: Proposal) -> None` — `[data-ba-id="N"]` 클릭/입력

- [ ] **Step 1: Write the failing tests**

```python
def test_simplify_dom_tags_buttons(browser_page, site_url):
    browser_page.goto(site_url + "/notice.html")
    dom = simplify_dom(browser_page)
    assert "확인" in dom and browser_page.locator("[data-ba-id]").count() >= 1

def test_propose_parses_click():
    client = fake_client_returning({"action": "click", "ba_id": 0, "value": None, "reason": "안내 닫기"})
    p = Advisor(client).propose("info 페이지로 가기", b"png", "[0] button 확인")
    assert p == Proposal("click", 0, None, "안내 닫기")

def test_propose_api_error_asks_human():
    client = Mock(); client.messages.create.side_effect = Exception("boom")
    assert Advisor(client).propose("g", b"", "").action == "ask_human"

def test_apply_proposal_clicks(browser_page, site_url):
    browser_page.goto(site_url + "/notice.html"); simplify_dom(browser_page)
    apply_proposal(browser_page, Proposal("click", 0, None, ""))
    browser_page.wait_for_url("**/info.html")
```

(`fake_client_returning`는 테스트 파일 내 헬퍼: 선택한 응답 형식에 맞는 Mock 응답을 만든다.)

- [ ] **Step 2: Run** `pytest tests/test_advisor.py -v` → FAIL
- [ ] **Step 3: Implement** `advisor.py`
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: claude-based unknown screen advisor`

---

### Task 7: 녹화기

**Files:**
- Create: `src/booking_assistant/recorder.py`, `src/booking_assistant/recorder.js`
- Test: `tests/test_recorder.py`

**Interfaces:**
- Consumes: `Recording`, `Step`, `Target` (Task 1)
- Produces:
  - `events_to_steps(events: list[dict]) -> list[Step]` — 이벤트 dict: `{"type": "click"|"input"|"change"|"nav"|"final_click", "target": {...Target 필드}, "value": str|None, "url": str|None}`. 같은 대상의 연속 `input`은 마지막 값 하나의 `fill`로 합침. `change`(select)→`select`. `nav`는 직전 단계가 클릭이 아닐 때만 `goto`. `final_click`→`final_submit`.
  - `class Recorder(context: BrowserContext)`; `start(start_url: str) -> Page`; `wait_finished() -> list[Step]` — `final_click` 또는 중지 단축키까지 블록.
  - `recorder.js`: 캡처 단계 리스너. Target 정보(role, 접근 가능한 이름, innerText 앞 80자, css 후보: `#id`, `[name=..]`, `tag:has-text("..")`, nth-of-type 경로). `Ctrl+Shift+F`로 "다음 클릭은 최종 제출" 모드 진입(페이지 우상단 배지 표시), 그 클릭은 `preventDefault()`+`stopImmediatePropagation()` 후 `final_click` 전송. `Ctrl+Shift+S`=녹화 중지. `type=password`와 `autocomplete^="cc-"` 입력은 보내지 않음. `context.add_init_script` + `context.expose_binding("__baRecord")`로 모든 페이지/팝업에 적용.

- [ ] **Step 1: Write the failing tests**

```python
def test_events_merge_inputs():
    ev = [{"type": "input", "target": {"css": ["#name"]}, "value": "홍"},
          {"type": "input", "target": {"css": ["#name"]}, "value": "홍길동"},
          {"type": "final_click", "target": {"text": "예약하기", "css": ["#submit"]}}]
    steps = events_to_steps(ev)
    assert [s.kind for s in steps] == ["fill", "final_submit"] and steps[0].value == "홍길동"

def test_record_fake_site_final_click_blocked(context, browser_page, site_url):
    r = Recorder(context); page = r.start(site_url + "/info.html")
    page.fill("#name", "홍길동"); page.check("text=무통장입금")
    page.keyboard.press("Control+Shift+F"); page.click("#submit")
    steps = r.wait_finished()
    assert steps[-1].kind == "final_submit"
    assert page.url.endswith("/info.html")          # 실제 제출되지 않음

def test_password_not_recorded(context, site_url):
    r = Recorder(context); page = r.start(site_url + "/info.html")
    page.evaluate("document.body.insertAdjacentHTML('beforeend','<input type=password id=pw>')")
    page.fill("#pw", "secret"); page.keyboard.press("Control+Shift+S")
    assert all(s.value != "secret" for s in r.wait_finished())
```

- [ ] **Step 2: Run** `pytest tests/test_recorder.py -v` → FAIL
- [ ] **Step 3: Implement** `recorder.py`, `recorder.js`
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: flow recorder with blocked final submit`

---

### Task 8: 재생기 — 기본 흐름, 대기열, CAPTCHA, 최종 제출, 결제 인계

**Files:**
- Create: `src/booking_assistant/player.py`
- Test: `tests/test_player.py`

**Interfaces:**
- Consumes: Task 1–5 (Task 6은 `advisor=None`으로)
- Produces:
  - `@dataclass RunOptions(values: dict[str,str], candidates: list[dict[str,str]] = [], start_at: datetime | None = None)`
  - `class RunResult(Enum): SUBMITTED, HANDED_TO_PAYMENT, ABORTED`
  - `Player(context: BrowserContext, prompter: Prompter, alerter: Alerter, log: RunLog, advisor: Advisor | None = None, queue_poll_s: float = 1.0)`; `run(rec: Recording, opts: RunOptions) -> RunResult`; 속성 `page` = 현재 활성 페이지(`context.on("page")`로 새 팝업이 열리면 그쪽으로 전환)
- 동작:
  - `start_at` 있으면 그 시각까지 `time.sleep`(로컬 시계), 이후 `start_url`로 한 번 `goto`.
  - 각 단계 전 `detect_state`:
    - QUEUE → 로그+(최초 1회) 텔레그램 "대기열 진입", `queue_poll_s` 간격으로 상태만 재확인(새로고침 금지). 벗어나면 이어서.
    - CAPTCHA → `alerter.alert`, 입력칸(`input:visible` 첫 번째)에 포커스, `prompter.wait_until(..., done=lambda: detect_state(page) is not CAPTCHA)`. False면 ABORTED.
    - PAYMENT → alert "결제는 직접 진행해 주세요", HANDED_TO_PAYMENT 반환.
  - 단계 수행: `find` → 클릭/fill/select. `final_submit`이면 요약(변수 값 + 결제 방식 라디오의 선택 라벨)을 `prompter.confirm`, 승인 시 클릭하고 짧게 대기 후 PAYMENT면 HANDED_TO_PAYMENT 아니면 SUBMITTED, 거절 시 ABORTED.
  - 대상 못 찾음: 이 태스크에서는 alert + `prompter.wait_until`(사람이 직접 처리, done=URL이 바뀌거나 다음 단계 대상이 보임) — Task 9에서 advisor·대안으로 확장.

- [ ] **Step 1: Write the failing tests** (녹화는 테스트 내 헬퍼 `make_recording(site_url)`로 생성: index 날짜(var date)→시간(var time)→다음→[queue]→[captcha]→name(var)→phone(var)→무통장→final_submit)

```python
def test_happy_path_submits(context, site_url, tmp_path):
    p = FakePrompter(confirms=[True], on_wait=lambda: solve_captcha(player.page, "ABCD"))
    player = Player(context, p, Mock(), RunLog(tmp_path), queue_poll_s=0.1)
    assert player.run(make_recording(site_url), OPTS) is RunResult.SUBMITTED
    assert "예약 완료" in player.page.inner_text("body")
    assert "김철수" in p.calls[-1][1]                         # 요약에 예약자 표시

def test_queue_never_reloads(context, site_url, tmp_path):   # 새로고침 금지
    navs = []; ...; context.on("request", lambda r: navs.append(r.url) if r.is_navigation_request() else None)
    player.run(...)
    assert sum("queue.html" in u for u in navs) == 1

def test_wrong_captcha_keeps_waiting(context, site_url, tmp_path):
    attempts = iter(["ZZZZ", "ABCD"])
    p = FakePrompter(confirms=[True], on_wait=lambda: solve_captcha(player.page, next(attempts, "ABCD")))
    assert player.run(...) is RunResult.SUBMITTED
    assert solve_captcha.count == 2

def test_final_submit_rejected_aborts(...):
    FakePrompter(confirms=[False]) → RunResult.ABORTED, page still info.html

def test_card_payment_handed_over(...):
    녹화의 결제 라디오를 신용카드로 바꾼 recording → RunResult.HANDED_TO_PAYMENT

def test_follows_popup(...):
    start_url index.html?popup=1 → player.page.url이 info.html인 상태로 SUBMITTED

def test_start_at_waits(monkeypatch, ...):
    sleep 기록 monkeypatch; start_at=now+5s → sleep 합계 ≈ 5, goto 1회
```

- [ ] **Step 2: Run** `pytest tests/test_player.py -v` → FAIL
- [ ] **Step 3: Implement** `player.py`
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat: replay with queue wait, captcha handoff, confirmed submit`

---

### Task 9: 재생기 — 낯선 화면 해석과 마감 대안

**Files:**
- Modify: `src/booking_assistant/player.py`
- Test: `tests/test_player_recovery.py`

**Interfaces:**
- Consumes: `Advisor`, `simplify_dom`, `apply_proposal` (Task 6), `is_available` (Task 2)
- 동작 추가:
  - 대상 못 찾음 + advisor 있음 → `advisor.propose(goal=f"다음 단계: {step 설명}", ...)`. `ask_human`이면 Task 8 방식. 그 외에는 `prompter.confirm("이렇게 진행할까요?", reason + 대상)` → 승인 시 `apply_proposal` 후 같은 단계 재시도(제안 최대 3회, 이후 사람 호출). 거절 시 사람 호출(`wait_until`).
  - `var` 단계의 대상이 있으나 `is_available` False이거나 못 찾음(마감) → `opts.candidates` 중 현재 화면에서 가능한 것을 라벨로 나열 + 남은 게 없으면 화면의 가능 항목(같은 단계 근처 형제 요소 중 available) 나열 → alert + `prompter.choose`. None이면 ABORTED. 선택한 후보 값으로 `values` 갱신 후, 바뀐 변수가 처음 쓰인 단계부터 재진행(그 단계 이전 URL로 `goto` 1회는 허용 — 녹화의 해당 단계 직전 페이지 URL을 기록해 두고 사용).

- [ ] **Step 1: Write the failing tests**

```python
def test_unknown_notice_screen_with_advisor(...):
    # 녹화엔 없는 notice.html이 captcha 후 끼어듦(captcha.html?next=notice.html)
    advisor = Mock(); advisor.propose.return_value = Proposal("click", 0, None, "안내 확인")
    p = FakePrompter(confirms=[True, True], ...)
    assert run(...) is RunResult.SUBMITTED
    assert p.calls[0] == ("confirm", ANY)                    # 제안을 사람이 승인

def test_advisor_rejected_falls_back_to_human(...):
    confirms=[False, True], on_wait가 notice의 확인을 직접 클릭 → SUBMITTED

def test_soldout_offers_candidates(...):
    values date=2026-11-02(마감), candidates=[{"date":"2026-11-01"},{"date":"2026-11-03"}]
    FakePrompter(choices=[1]) → choose 옵션 == ["2026-11-01", "2026-11-03"], 최종 요약에 2026-11-03

def test_soldout_choice_none_aborts(...):
    choices=[None] → ABORTED, final 클릭 없음

def test_never_chooses_alone(...):
    candidates 1개만 가능해도 prompter.choose가 호출됨
```

- [ ] **Step 2: Run** `pytest tests/test_player_recovery.py -v` → FAIL
- [ ] **Step 3: Implement** 변경
- [ ] **Step 4: Run** `pytest -v` (전체) → PASS
- [ ] **Step 5: Commit** `feat: advisor-assisted recovery and sold-out alternatives`

---

### Task 10: Chrome 연결, CLI, 설정, README

**Files:**
- Create: `src/booking_assistant/browser.py`, `src/booking_assistant/config.py`, `src/booking_assistant/cli.py`, `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `browser.py`: `chrome_path() -> Path` (Windows: `%PROGRAMFILES%`, `%PROGRAMFILES(X86)%`, `%LOCALAPPDATA%`\Google\Chrome\Application\chrome.exe; macOS: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`; 없으면 `FileNotFoundError`), `launch_chrome(profile_dir: Path, port: int = 9222) -> subprocess.Popen` (`--remote-debugging-port`, `--user-data-dir`, `--no-first-run`), `connect(port: int = 9222) -> tuple[Playwright, BrowserContext]` (`connect_over_cdp("http://127.0.0.1:port")`, 첫 context), 실패 시 `ChromeNotReachable`.
  - `config.py`: `@dataclass Config(telegram_token: str|None, telegram_chat_id: str|None, advisor_model: str = "claude-opus-5-5", port: int = 9222)`; `load_config(home: Path = DEFAULT_HOME) -> Config` (파일 없으면 기본값); `DEFAULT_HOME = Path.home() / ".booking-assistant"`.
  - `cli.py`: `main(argv: list[str] | None = None) -> int`
    - `chrome` — 전용 프로필로 Chrome 실행, "이 창에서 대상 사이트에 로그인해 두세요" 안내
    - `record NAME --url URL` — 연결 → 녹화 → fill 단계와 날짜/시간처럼 보이는 클릭 단계를 번호로 보여주고 변수 이름 입력받아 `var` 지정 → 저장
    - `run NAME [--set k=v]... [--candidate "k=v,k=v"]... [--at "YYYY-MM-DD HH:MM:SS"]` — 연결 → `Player(TkPrompter(), Alerter(텔레그램 설정 시), RunLog(runs/<ts>), Advisor 키 있으면)` → 결과를 한국어로 출력. 종료 코드: SUBMITTED/HANDED_TO_PAYMENT 0, ABORTED 1, 연결 실패 2.
    - `list` — 저장된 녹화 목록
- README: 설치(venv, `pip install -e .`, `playwright install chromium`), Windows/macOS 명령 예시, 텔레그램 봇 만들기(@BotFather, chat id 확인), `ANTHROPIC_API_KEY`, 녹화 단축키(Ctrl+Shift+F 최종 제출 지정, Ctrl+Shift+S 중지), 개입 키(Enter/숫자/Esc), "사용 전 대상 사이트의 이용약관에서 자동화 허용 여부를 확인할 것".

- [ ] **Step 1: Write the failing tests**

```python
def test_run_without_chrome_prints_guidance(tmp_path, capsys, monkeypatch):
    save_recording(sample, tmp_path / "recordings" / "demo.yaml")
    monkeypatch.setattr("booking_assistant.cli.DEFAULT_HOME", tmp_path)
    monkeypatch.setattr("booking_assistant.cli.connect", Mock(side_effect=ChromeNotReachable()))
    assert main(["run", "demo", "--set", "date=2026-11-01"]) == 2
    assert "booking-assistant chrome" in capsys.readouterr().out

def test_parse_candidates():
    assert parse_candidate("date=2026-11-03,time=19:00") == {"date": "2026-11-03", "time": "19:00"}

def test_load_config_defaults(tmp_path):
    assert load_config(tmp_path) == Config(None, None)

def test_chrome_path_mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin"); monkeypatch.setattr(Path, "exists", lambda self: True)
    assert str(chrome_path()).endswith("Google Chrome")
```

- [ ] **Step 2: Run** `pytest tests/test_cli.py -v` → FAIL
- [ ] **Step 3: Implement** 세 모듈과 README (`parse_candidate(s: str) -> dict[str,str]`는 cli.py)
- [ ] **Step 4: Run** `pytest -v` → 전체 PASS
- [ ] **Step 5: 수동 E2E** — `python -m http.server -d tests/fake_site 8765` 실행 후 `booking-assistant chrome` → `booking-assistant record demo --url http://127.0.0.1:8765/index.html`(끝에서 Ctrl+Shift+F 후 예약하기) → `booking-assistant run demo --set date=2026-11-03 --set time=19:00 --set name=김철수 --set phone=010-0000-0000`. Expected: 대기열 자동 통과, CAPTCHA에서 소리·알림·창 뜸, ABCD 입력 후 자동 재개, 최종 요약 창에서 Enter → "예약 완료".
- [ ] **Step 6: Commit** `feat: chrome connection, cli and readme`
