"""Liten alltid-överst-remsa nertill på skärmen som visar vad som händer.

Två egenheter att känna till:

- Tkinter kräver att allt GUI-arbete sker i huvudtråden, men inspelning
  och transkribering händer i andra trådar. Alla uppdateringar går därför
  via en kö som huvudtråden tömmer med jämna mellanrum.

- Fönstret får WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW: skulle remsan ta
  fokus när den visas hamnar den inklistrade texten i fel fönster.
"""

import ctypes
import queue
import tkinter as tk

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

_POLL_MS = 50

RED = "#c0392b"
BLUE = "#2980b9"
GREEN = "#27ae60"
GRAY = "#566573"


class Overlay:
    def __init__(self, root: tk.Tk):
        self._root = root
        self._queue = queue.Queue()
        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.withdraw()
        self._label = tk.Label(
            self._win,
            font=("Segoe UI", 11, "bold"),
            fg="white",
            bg=RED,
            padx=16,
            pady=7,
        )
        self._label.pack()
        self._hide_job = None
        self._poll()

    # -- Trådsäkra metoder (kan anropas varifrån som helst) --------------------

    def show(self, text: str, color: str = RED, auto_hide_ms: int | None = None):
        self._queue.put(("show", text, color, auto_hide_ms))

    def hide(self) -> None:
        self._queue.put(("hide", None, None, None))

    # -- Körs i huvudtråden -----------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                action, text, color, auto_hide_ms = self._queue.get_nowait()
                if action == "show":
                    self._show(text, color, auto_hide_ms)
                else:
                    self._win.withdraw()
        except queue.Empty:
            pass
        self._root.after(_POLL_MS, self._poll)

    def _set_noactivate(self) -> None:
        hwnd = user32.GetParent(self._win.winfo_id()) or self._win.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        )

    def _show(self, text: str, color: str, auto_hide_ms: int | None) -> None:
        if self._hide_job is not None:
            self._root.after_cancel(self._hide_job)
            self._hide_job = None
        self._label.config(text=text, bg=color)
        self._win.update_idletasks()
        self._set_noactivate()
        w = self._win.winfo_reqwidth()
        h = self._win.winfo_reqheight()
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        self._win.geometry(f"+{(sw - w) // 2}+{sh - h - 80}")
        self._win.deiconify()
        if auto_hide_ms:
            self._hide_job = self._root.after(auto_hide_ms, self._win.withdraw)
