"""사람 확인 창. 자동 승인·타임아웃 승인은 없다: 창을 닫거나 Esc를 누르면 거절/중단이다."""
import time
from typing import Callable, Protocol


class Prompter(Protocol):
    def confirm(self, title: str, body: str) -> bool: ...

    def choose(self, title: str, options: list[str]) -> int | None: ...

    def wait_until(self, title: str, body: str, done: Callable[[], bool],
                   poll_s: float = 0.5) -> bool: ...


class TkPrompter:
    """항상 위에 뜨는 작은 tkinter 창. 메인 스레드에서만 사용한다(macOS 요구사항)."""

    def _window(self, title: str, body: str, hint: str):
        import tkinter as tk

        root = tk.Tk()
        root.title(title)
        root.attributes("-topmost", True)
        tk.Label(root, text=title, font=("", 13, "bold")).pack(padx=20, pady=(16, 4))
        tk.Label(root, text=body, justify="left", wraplength=420).pack(padx=20, pady=4)
        tk.Label(root, text=hint, fg="#555555").pack(padx=20, pady=(8, 16))
        root.update_idletasks()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 3
        root.geometry(f"+{x}+{y}")
        root.lift()
        root.focus_force()
        return root

    def confirm(self, title: str, body: str) -> bool:
        result = {"value": False}
        root = self._window(title, body, "Enter = 승인    Esc = 중단")

        def finish(value: bool):
            result["value"] = value
            root.destroy()

        root.bind("<Return>", lambda e: finish(True))
        root.bind("<KP_Enter>", lambda e: finish(True))
        root.bind("<Escape>", lambda e: finish(False))
        root.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        root.mainloop()
        return result["value"]

    def choose(self, title: str, options: list[str]) -> int | None:
        options = options[:9]
        body = "\n".join(f"{i}. {opt}" for i, opt in enumerate(options, 1))
        result: dict = {"value": None}
        root = self._window(title, body, f"숫자 1-{len(options)} = 선택    Esc = 중단")

        def pick(i: int):
            result["value"] = i
            root.destroy()

        for i in range(len(options)):
            root.bind(str(i + 1), lambda e, i=i: pick(i))
        root.bind("<Escape>", lambda e: root.destroy())
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.mainloop()
        return result["value"]

    def wait_until(self, title: str, body: str, done: Callable[[], bool],
                   poll_s: float = 0.5) -> bool:
        result = {"value": False}
        root = self._window(title, body, "완료되면 자동으로 닫힙니다    Esc = 중단")
        poll_ms = max(1, int(poll_s * 1000))

        def poll():
            try:
                finished = done()
            except Exception:    # 화면 이동 중 읽기 실패 등: 아직 안 끝난 것으로 보고 계속 확인한다
                finished = False
            if finished:
                result["value"] = True
                root.destroy()
            else:
                root.after(poll_ms, poll)

        root.bind("<Escape>", lambda e: root.destroy())
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.after(poll_ms, poll)
        root.mainloop()
        return result["value"]


class FakePrompter:
    """테스트용. calls는 (메서드명, 본문 또는 "\n".join(옵션)) 튜플 목록."""

    WAIT_RETRY_S = 1.0
    WAIT_TOTAL_S = 30.0

    def __init__(self, confirms: list[bool] | None = None,
                 choices: list[int | None] | None = None,
                 on_wait: Callable[[], None] | None = None):
        self._confirms = list(confirms or [])
        self._choices = list(choices or [])
        self._on_wait = on_wait
        self.calls: list[tuple[str, str]] = []

    def confirm(self, title: str, body: str) -> bool:
        self.calls.append(("confirm", body))
        return self._confirms.pop(0) if self._confirms else False

    def choose(self, title: str, options: list[str]) -> int | None:
        self.calls.append(("choose", "\n".join(options)))
        return self._choices.pop(0) if self._choices else None

    def wait_until(self, title: str, body: str, done: Callable[[], bool],
                   poll_s: float = 0.5) -> bool:
        self.calls.append(("wait_until", body))
        start = time.monotonic()
        last_hook = start
        if self._on_wait:
            self._on_wait()
        while True:
            if done():
                return True
            now = time.monotonic()
            if now - start > self.WAIT_TOTAL_S:
                return False
            if self._on_wait and now - last_hook >= self.WAIT_RETRY_S:
                self._on_wait()
                last_hook = now
            time.sleep(poll_s)
