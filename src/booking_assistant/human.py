"""사람 확인 창. 자동 승인·타임아웃 승인은 없다: 창을 닫거나 Esc를 누르면 거절/중단이다."""
import time
from typing import Callable, Protocol

TK_RETRIES = 5
TK_RETRY_DELAY_S = 0.2


class Prompter(Protocol):
    def confirm(self, title: str, body: str) -> bool: ...

    def choose(self, title: str, options: list[str]) -> int | None: ...

    def wait_until(self, title: str, body: str, done: Callable[[], bool],
                   poll_s: float = 0.5, manual_ok: str | None = None) -> bool:
        """done()이 True가 되면 True, 중단하면 False.

        manual_ok가 주어지면 사람이 Enter로 "끝났다"고 알릴 수 있고, 그때도 True를 반환한다.
        """
        ...


class TkPrompter:
    """항상 위에 뜨는 작은 tkinter 창. 메인 스레드에서만 사용한다(macOS 요구사항)."""

    def _window(self, title: str, body: str, hint: str):
        import tkinter as tk

        # Windows에서 가끔 Tcl 초기화 파일을 순간적으로 못 읽어 Tk()가 실패한다: 몇 번 재시도한다.
        for attempt in range(TK_RETRIES):
            try:
                root = tk.Tk()
                break
            except tk.TclError:
                if attempt == TK_RETRIES - 1:
                    raise
                time.sleep(TK_RETRY_DELAY_S)
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

    @staticmethod
    def _buttons(root, buttons: list[tuple[str, Callable[[], None]]], vertical: bool = False) -> None:
        """클릭용 버튼. Windows는 다른 프로그램이 키보드 포커스를 가져가는 것을 막을 수 있어
        창이 맨 위에 떠도 Enter가 안 먹을 수 있다 — 그때도 클릭 한 번으로 처리할 수 있게 한다."""
        import tkinter as tk

        frame = tk.Frame(root)
        frame.pack(padx=20, pady=(0, 16), fill="x" if vertical else None)
        for label, action in buttons:
            btn = tk.Button(frame, text=label, command=action, padx=12, pady=4)
            if vertical:
                btn.pack(fill="x", pady=2)
            else:
                btn.pack(side="left", padx=4)

    def confirm(self, title: str, body: str) -> bool:
        result = {"value": False}
        root = self._window(title, body, "Enter = 승인    Esc = 중단")

        def finish(value: bool):
            result["value"] = value
            root.destroy()

        self._buttons(root, [("승인 (Enter)", lambda: finish(True)),
                             ("중단 (Esc)", lambda: finish(False))])
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

        self._buttons(root, [(f"{i}. {opt}", lambda i=i: pick(i - 1))
                             for i, opt in enumerate(options, 1)]
                      + [("중단 (Esc)", root.destroy)], vertical=True)
        for i in range(len(options)):
            root.bind(str(i + 1), lambda e, i=i: pick(i))
        root.bind("<Escape>", lambda e: root.destroy())
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.mainloop()
        return result["value"]

    def wait_until(self, title: str, body: str, done: Callable[[], bool],
                   poll_s: float = 0.5, manual_ok: str | None = None) -> bool:
        result = {"value": False}
        hint = "완료되면 자동으로 닫힙니다    Esc = 중단"
        if manual_ok:
            hint = f"완료되면 자동으로 닫힙니다    Enter = {manual_ok}    Esc = 중단"
        root = self._window(title, body, hint)
        poll_ms = max(1, int(poll_s * 1000))

        def signal(_e=None):
            result["value"] = True
            root.destroy()

        buttons = [(f"{manual_ok} (Enter)", signal)] if manual_ok else []
        self._buttons(root, buttons + [("중단 (Esc)", root.destroy)])
        if manual_ok:
            root.bind("<Return>", signal)
            root.bind("<KP_Enter>", signal)

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
                 on_wait: Callable[[], None] | None = None,
                 manual_waits: list[bool] | None = None):
        self._confirms = list(confirms or [])
        self._choices = list(choices or [])
        self._on_wait = on_wait
        # manual_ok를 제공한 wait_until 호출마다 하나씩 꺼내 True면 사람이 Enter를 누른 것으로 본다.
        self._manual_waits = list(manual_waits or [])
        self.calls: list[tuple[str, str]] = []

    def confirm(self, title: str, body: str) -> bool:
        self.calls.append(("confirm", body))
        return self._confirms.pop(0) if self._confirms else False

    def choose(self, title: str, options: list[str]) -> int | None:
        self.calls.append(("choose", "\n".join(options)))
        return self._choices.pop(0) if self._choices else None

    def wait_until(self, title: str, body: str, done: Callable[[], bool],
                   poll_s: float = 0.5, manual_ok: str | None = None) -> bool:
        self.calls.append(("wait_until", body))
        start = time.monotonic()
        last_hook = start
        if self._on_wait:
            self._on_wait()
        if manual_ok and self._manual_waits and self._manual_waits.pop(0):
            return True
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
