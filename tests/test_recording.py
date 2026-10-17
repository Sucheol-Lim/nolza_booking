import pytest

from booking_assistant.recording import (
    MissingVariableError,
    Recording,
    Step,
    Target,
    load_recording,
    save_recording,
)


@pytest.fixture
def rec():
    return Recording("demo", "http://x/", [
        Step("click", Target(role="button", name="2026-11-01", text="2026-11-01", css=["#d1"]), var="date"),
        Step("fill", Target(css=["#name"]), value="홍길동", var="name"),
        Step("final_submit", Target(text="예약하기", css=["#submit"])),
    ], ["date", "name"])


def test_roundtrip(tmp_path, rec):
    save_recording(rec, tmp_path / "demo.yaml")
    assert load_recording(tmp_path / "demo.yaml") == rec
    assert "홍길동" in (tmp_path / "demo.yaml").read_text(encoding="utf-8")  # allow_unicode


def test_apply_substitutes_fill_value_and_click_text(rec):
    steps = rec.apply({"date": "2026-11-03", "name": "김철수"})
    assert steps[0].target.text == "2026-11-03" and steps[0].target.name == "2026-11-03"
    assert steps[0].target.css == []  # 녹화 당시 선택자는 다른 날짜를 가리키므로 버림
    assert steps[1].value == "김철수"
    assert rec.steps[1].value == "홍길동"  # 원본 불변
    assert rec.steps[0].target.css == ["#d1"]


def test_apply_missing_variable(rec):
    with pytest.raises(MissingVariableError):
        rec.apply({"date": "2026-11-03"})
