"""녹화 데이터 모델과 YAML 저장/불러오기."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import yaml

StepKind = Literal["goto", "click", "fill", "select", "final_submit"]


class MissingVariableError(KeyError):
    """apply()에 필요한 변수 값이 주어지지 않았을 때."""


@dataclass
class Target:
    role: str | None = None
    name: str | None = None
    text: str | None = None
    css: list[str] = field(default_factory=list)
    tag: str | None = None


@dataclass
class Step:
    kind: StepKind
    target: Target | None = None
    value: str | None = None
    url: str | None = None
    var: str | None = None


@dataclass
class Recording:
    name: str
    start_url: str
    steps: list[Step] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)

    def apply(self, values: dict[str, str]) -> list[Step]:
        """변수 값을 치환한 단계 사본을 돌려준다. 원본은 바뀌지 않는다."""
        missing = [v for v in self.variables if v not in values]
        missing += [s.var for s in self.steps
                    if s.var and s.var not in values and s.var not in missing]
        if missing:
            raise MissingVariableError(", ".join(missing))
        steps = copy.deepcopy(self.steps)
        for step in steps:
            if step.var is None:
                continue
            value = values[step.var]
            if step.kind in ("fill", "select"):
                step.value = value
            elif step.target is not None:
                # 녹화 당시 선택자는 다른 값을 가리키므로 버리고 텍스트로 찾는다.
                step.target.text = value
                step.target.name = value
                step.target.css = []
        return steps


def save_recording(rec: Recording, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(asdict(rec), allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")


def load_recording(path: Path) -> Recording:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    steps = []
    for s in data.get("steps", []):
        target = s.get("target")
        steps.append(Step(
            kind=s["kind"],
            target=Target(**target) if target is not None else None,
            value=s.get("value"),
            url=s.get("url"),
            var=s.get("var"),
        ))
    return Recording(data["name"], data["start_url"], steps, data.get("variables", []))
