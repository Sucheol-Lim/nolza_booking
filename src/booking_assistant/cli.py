"""booking-assistant 명령줄."""
import argparse
import os
from datetime import datetime
from pathlib import Path

from .advisor import Advisor
from .browser import ChromeNotReachable, connect, launch_chrome
from .config import DEFAULT_HOME, load_config
from .human import TkPrompter
from .notify import Alerter
from .player import Player, RunOptions, RunResult
from .recorder import Recorder
from .recording import (MissingVariableError, Recording, load_recording,
                        save_recording)
from .runlog import RunLog
from .telegram import TelegramClient

CHROME_GUIDANCE = ("전용 Chrome에 연결하지 못했습니다. 먼저 `booking-assistant chrome`을 "
                   "실행하고, 열린 Chrome 창에서 대상 사이트에 로그인해 두세요.")


def parse_candidate(s: str) -> dict[str, str]:
    values = {}
    for item in (i for i in s.split(",") if i.strip()):
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"--candidate는 k=v,k=v 형식이어야 합니다: {s}")
        values[key.strip()] = value.strip()
    return values


def _parse_set(items: list[str]) -> dict[str, str]:
    values = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--set은 k=v 형식이어야 합니다: {item}")
        values[key.strip()] = value
    return values


def _recording_path(name: str) -> Path:
    return DEFAULT_HOME / "recordings" / f"{name}.yaml"


def _cmd_chrome(args) -> int:
    config = load_config(DEFAULT_HOME)
    try:
        launch_chrome(DEFAULT_HOME / "chrome-profile", config.port)
    except FileNotFoundError as e:
        print(f"{e}. Chrome을 설치해 주세요.")
        return 1
    print("전용 Chrome을 실행했습니다. 이 창에서 대상 사이트에 로그인해 두세요.")
    print("예약이 끝날 때까지 이 Chrome 창을 닫지 마세요.")
    return 0


def _designate_variables(rec: Recording) -> None:
    numbered = [s for s in rec.steps if s.kind in ("fill", "click")]
    for n, step in enumerate(numbered, 1):
        if step.kind == "fill":
            print(f"{n}. 입력: {step.value}")
        else:
            t = step.target
            print(f"{n}. 클릭: {(t.text or t.name or '') if t else ''}")
    while True:
        line = input("변수로 지정할 단계 번호와 이름 (예: 1=date). 빈 줄이면 끝:").strip()
        if not line:
            return
        num, sep, name = line.partition("=")
        name = name.strip()
        if not (sep and num.strip().isdigit() and name
                and 1 <= int(num) <= len(numbered)):
            print("형식이 올바르지 않습니다. 예: 1=date")
            continue
        numbered[int(num) - 1].var = name
        if name not in rec.variables:
            rec.variables.append(name)


def _cmd_record(args) -> int:
    config = load_config(DEFAULT_HOME)
    try:
        pw, context = connect(config.port)
    except ChromeNotReachable:
        print(CHROME_GUIDANCE)
        return 2
    try:
        recorder = Recorder(context)
        recorder.start(args.url)
        print("녹화 중입니다. 평소처럼 예약을 진행하세요. "
              "Ctrl+Shift+F: 다음 클릭을 최종 제출로 지정, Ctrl+Shift+S: 녹화 중지")
        steps = recorder.wait_finished()
    finally:
        pw.stop()
    rec = Recording(args.name, args.url, steps)
    _designate_variables(rec)
    save_recording(rec, _recording_path(args.name))
    print(f"저장했습니다: {_recording_path(args.name)}")
    return 0


def _make_advisor(model: str) -> Advisor | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return Advisor(anthropic.Anthropic(), model)


def _cmd_run(args) -> int:
    path = _recording_path(args.name)
    if not path.exists():
        print(f"녹화를 찾을 수 없습니다: {args.name}")
        return 1
    rec = load_recording(path)
    try:
        values = _parse_set(args.set)
        candidates = [parse_candidate(c) for c in args.candidate]
        start_at = datetime.strptime(args.at, "%Y-%m-%d %H:%M:%S") if args.at else None
    except ValueError as e:
        print(f"옵션 형식이 올바르지 않습니다: {e}")
        return 1
    try:
        rec.apply(values)
    except MissingVariableError as e:
        print(f"값이 필요한 변수가 있습니다: {e}. --set 변수=값 으로 지정해 주세요.")
        return 1
    config = load_config(DEFAULT_HOME)
    try:
        pw, context = connect(config.port)
    except ChromeNotReachable:
        print(CHROME_GUIDANCE)
        return 2
    try:
        telegram = (TelegramClient(config.telegram_token, config.telegram_chat_id)
                    if config.telegram_token and config.telegram_chat_id else None)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        player = Player(context, TkPrompter(), Alerter(telegram),
                        RunLog(DEFAULT_HOME / "runs" / stamp),
                        advisor=_make_advisor(config.advisor_model))
        result = player.run(rec, RunOptions(values, candidates, start_at))
    finally:
        pw.stop()
    messages = {
        RunResult.SUBMITTED: "예약 완료",
        RunResult.HANDED_TO_PAYMENT: "결제 화면입니다. 결제는 직접 진행해 주세요.",
        RunResult.ABORTED: "중단되었습니다. 제출하지 않았습니다.",
    }
    print(messages[result])
    return 1 if result is RunResult.ABORTED else 0


def _cmd_list(args) -> int:
    folder = DEFAULT_HOME / "recordings"
    names = sorted(p.stem for p in folder.glob("*.yaml")) if folder.exists() else []
    if not names:
        print("저장된 녹화가 없습니다.")
    for name in names:
        print(name)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="booking-assistant",
                                     description="사람이 최종 확인하는 예약 도우미")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("chrome", help="전용 프로필로 Chrome 실행").set_defaults(func=_cmd_chrome)
    p = sub.add_parser("record", help="예약 과정 녹화")
    p.add_argument("name")
    p.add_argument("--url", required=True)
    p.set_defaults(func=_cmd_record)
    p = sub.add_parser("run", help="녹화 재생")
    p.add_argument("name")
    p.add_argument("--set", action="append", default=[], metavar="k=v")
    p.add_argument("--candidate", action="append", default=[], metavar="k=v,k=v")
    p.add_argument("--at", metavar="YYYY-MM-DD HH:MM:SS")
    p.set_defaults(func=_cmd_run)
    sub.add_parser("list", help="저장된 녹화 목록").set_defaults(func=_cmd_list)
    args = parser.parse_args(argv)
    return args.func(args)
