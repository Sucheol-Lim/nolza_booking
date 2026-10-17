"""실행 로그: events.jsonl(UTF-8)에 한 줄씩 추가하고 NNN.png 스크린샷을 저장한다."""
import json
import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page

log = logging.getLogger(__name__)


class RunLog:
    def __init__(self, dir: Path):
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._n = 0

    def event(self, kind: str, message: str, page: Page | None = None) -> None:
        url = None
        shot = None
        if page is not None:
            try:
                url = page.url
            except Exception:
                log.warning("현재 URL을 읽지 못했습니다", exc_info=True)
            self._n += 1
            name = f"{self._n:03d}.png"
            try:
                page.screenshot(path=str(self.dir / name))
                shot = name
            except Exception:
                log.warning("스크린샷 저장 실패", exc_info=True)
        rec = {"t": datetime.now().isoformat(timespec="seconds"), "kind": kind,
               "message": message, "url": url, "screenshot": shot}
        with open(self.dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
