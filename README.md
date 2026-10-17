# booking-assistant

사람이 최종 확인하는 예약 도우미입니다. 한 번 직접 예약하는 과정을 녹화해 두면, 다음부터는 같은 과정을 자동으로 재생합니다. 대기열은 기다리고, CAPTCHA·결제·최종 제출은 항상 사람이 합니다.

> **사용 전 대상 사이트의 이용약관에서 자동화 허용 여부를 확인할 것.**

## 설치

Python 3.11 이상이 필요합니다.

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
playwright install chromium
```

macOS (zsh):

```zsh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

## 사용법

### 1. 전용 Chrome 실행

```
booking-assistant chrome
```

전용 프로필(`~/.booking-assistant/chrome-profile`)로 Chrome이 열립니다. **이 창에서 대상 사이트에 로그인해 두세요.** 녹화와 재생이 끝날 때까지 이 창을 닫지 마세요. 평소 쓰는 Chrome 프로필은 건드리지 않습니다.

### 2. 녹화

```
booking-assistant record demo --url https://예약사이트.example/
```

평소처럼 예약을 진행하세요.

- `Ctrl+Shift+F`: 다음 클릭을 최종 제출로 지정합니다. 녹화 중에는 실제로 제출되지 않고 막힙니다. 마지막 단계에서 누른 뒤 제출 버튼을 클릭하세요.
- `Ctrl+Shift+S`: 녹화를 중지합니다.

녹화가 끝나면 입력 단계와 클릭 단계가 번호로 표시됩니다. 매번 바뀌는 값(날짜, 시간, 이름 등)은 `1=date`처럼 번호와 변수 이름을 입력해 변수로 지정합니다. 빈 줄을 입력하면 저장합니다. 비밀번호·카드번호 입력은 기록되지 않습니다.

### 3. 재생

Windows (PowerShell):

```powershell
booking-assistant run demo --set date=2026-11-03 --set time=19:00 `
  --set name=김철수 --set phone=010-0000-0000 `
  --candidate "date=2026-11-04,time=19:00" --at "2026-11-01 10:00:00"
```

macOS (zsh):

```zsh
booking-assistant run demo --set date=2026-11-03 --set time=19:00 \
  --set name=김철수 --set phone=010-0000-0000 \
  --candidate "date=2026-11-04,time=19:00" --at "2026-11-01 10:00:00"
```

- `--set 변수=값`: 녹화에서 지정한 변수의 값. 모든 변수에 값이 필요합니다.
- `--candidate "k=v,k=v"`: 원하는 항목이 마감일 때 사람에게 보여줄 대안 후보(여러 번 지정 가능). 대안은 항상 사람이 고릅니다.
- `--at "YYYY-MM-DD HH:MM:SS"`: 이 시각까지 기다렸다가 시작합니다.

종료 코드: 예약 완료/결제 화면 인계 0, 중단 1, Chrome 연결 실패 2.

### 4. 저장된 녹화 목록

```
booking-assistant list
```

## 사람이 개입하는 순간

CAPTCHA, 마감 대안 선택, 결제 화면, 최종 제출 확인에서는 소리와 OS 알림이 나고 창이 뜹니다.

- `Enter`: 승인/확인
- 숫자 키: 대안 선택
- `Esc`: 중단 (제출하지 않음)

최종 제출은 요약 창에서 사람이 승인한 뒤에만 클릭하며, 자동 승인은 없습니다. 결제는 항상 직접 진행합니다.

### 체크박스형 보안문자 위젯 (reCAPTCHA v2, hCaptcha, Turnstile)

이런 위젯은 풀린 뒤에도 화면에 남습니다. 도우미는 위젯이 페이지에 채워 넣는 통과 토큰을 확인해서, 풀리면 창을 닫고 자동으로 이어갑니다.
토큰을 읽을 수 없는 위젯이라 풀었는데도 창이 닫히지 않으면, 대기 창에서 `Enter`(통과했어요)를 누르세요. 그 화면에서는 위젯을 더 이상 보지 않습니다.
글자를 입력하는 보안문자에는 이 `Enter`가 없으며, 실제로 통과해야만 이어갑니다.

## 설정 (선택)

`~/.booking-assistant/config.yaml`:

```yaml
telegram:
  token: "123456:ABC..."
  chat_id: "123456789"
advisor:
  model: claude-opus-5-5
chrome:
  port: 9222
```

### 텔레그램 알림

알림과 스크린샷만 보내며, 원격 승인은 없습니다.

1. 텔레그램에서 `@BotFather`에게 `/newbot`을 보내 봇을 만들고 토큰을 받습니다.
2. 만든 봇에게 아무 메시지나 보냅니다.
3. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열어 `"chat":{"id":...}`의 숫자를 확인합니다.
4. `config.yaml`의 `telegram.token`, `telegram.chat_id`에 넣습니다.

### 화면 해석 (Claude API)

환경 변수 `ANTHROPIC_API_KEY`가 있으면, 예상과 다른 화면에서 Claude가 화면을 해석해 다음 동작을 제안합니다. 화면(스크린샷과 DOM)은 마스킹 없이 Claude API로 전송됩니다. 키가 없으면 해석 없이 바로 사람을 부릅니다.

Windows (PowerShell): `$env:ANTHROPIC_API_KEY = "sk-ant-..."`
macOS (zsh): `export ANTHROPIC_API_KEY="sk-ant-..."`

## 데이터 위치

`~/.booking-assistant/` 아래에 `config.yaml`, `recordings/<이름>.yaml`, `chrome-profile/`, `runs/<시각>/`(이벤트 로그와 스크린샷)이 저장됩니다.

## 직접 확인 (수동 E2E)

키 입력과 CAPTCHA 입력이 필요해 자동 테스트로 확인할 수 없는 부분입니다. 저장소 루트에서 진행하세요.

1. 가짜 사이트 실행: `python -m http.server -d tests/fake_site 8765`
2. 다른 터미널에서 `booking-assistant chrome`
3. `booking-assistant record demo --url http://127.0.0.1:8765/index.html` — 끝에서 `Ctrl+Shift+F`를 누른 뒤 예약하기를 클릭하고, 변수(`date`, `time`, `name`, `phone`)를 지정
4. `booking-assistant run demo --set date=2026-11-03 --set time=19:00 --set name=김철수 --set phone=010-0000-0000`
5. 기대 결과: 대기열 자동 통과, CAPTCHA에서 소리·알림·창 표시, ABCD 입력 후 자동 재개, 최종 요약 창에서 Enter를 누르면 "예약 완료"

## 개발

```
pip install -e .[dev]
pytest
```
