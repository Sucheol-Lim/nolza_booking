"""사용자 설정(~/.booking-assistant/config.yaml)."""
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_HOME = Path.home() / ".booking-assistant"


@dataclass
class Config:
    telegram_token: str | None
    telegram_chat_id: str | None
    advisor_model: str = "claude-opus-5-5"
    port: int = 9222


def load_config(home: Path = DEFAULT_HOME) -> Config:
    path = Path(home) / "config.yaml"
    if not path.exists():
        return Config(None, None)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    telegram = data.get("telegram") or {}
    chat_id = telegram.get("chat_id")
    defaults = Config(None, None)
    return Config(
        telegram_token=telegram.get("token"),
        telegram_chat_id=str(chat_id) if chat_id is not None else None,
        advisor_model=(data.get("advisor") or {}).get("model", defaults.advisor_model),
        port=int((data.get("chrome") or {}).get("port", defaults.port)),
    )
