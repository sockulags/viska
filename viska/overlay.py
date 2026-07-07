"""Statuspill nertill på skärmen: rundade hörn (Windows 11 DWM), levande
ljudnivåmätare under inspelning och förhandsvisning av pågående fras.

Två egenheter att känna till:

- Tkinter kräver att allt GUI-arbete sker i huvudtråden, men inspelning
  och transkribering händer i andra trådar. Alla uppdateringar går därför
  via en kö som huvudtråden tömmer med jämna mellanrum.

- Fönstret får WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW: skulle pillen ta
  fokus när den visas hamnar den inklistrade texten i fel fönster.
"""

import ctypes
import queue
import tkinter as tk

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

_POLL_MS = 50
_BARS = 5

RED = "#c0392b"
BLUE = "#2980b9"
GREEN = "#27ae60"
GRAY = "#566573"


def _round_corners(hwnd: int) -> None:
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(ctypes.c_int(DWMWCP_ROUND)),
            ctypes.sizeof(ctypes.c_int),
        )
    except Exception:
        pass  # äldre Windows: fyrkantiga hörn, fullt funktionellt


class Overlay:
    def __init__(self, root: tk.Tk, level_source=None):
        """level_source() -> float RMS 0..~0.3, läses under inspelning."""
        self._root = root
        self._level_source = level_source
        self._queue = queue.Queue()
        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.withdraw()
        self._frame = tk.Frame(self._win, bg=RED)
        self._frame.pack(fill="both", expand=True)
        self._meter = tk.Canvas(
            self._frame, width=26, height=18, bg=RED, highlightthickness=0
        )
        self._label = tk.Label(
            self._frame,
            font=("Segoe UI", 11, "bold"),
            fg="white",
            bg=RED,
            padx=6,
            pady=8,
        )
        self._label.pack(side="left", padx=(14, 12))
        self._recording = False
        self._hide_job = None
        self._poll()

    # -- Trådsäkra metoder (kan anropas varifrån som helst) --------------------

    def show(
        self,
        text: str,
        color: str = RED,
        auto_hide_ms: int | None = None,
        meter: bool = False,
    ):
        self._queue.put(("show", text, color, auto_hide_ms, meter))

    def hide(self) -> None:
        self._queue.put(("hide", None, None, None, False))

    # -- Körs i huvudtråden -----------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                action, text, color, auto_hide_ms, meter = self._queue.get_nowait()
                if action == "show":
                    self._show(text, color, auto_hide_ms, meter)
                else:
                    self._recording = False
                    self._win.withdraw()
        except queue.Empty:
            pass
        if self._recording:
            self._draw_meter()
        self._root.after(_POLL_MS, self._poll)

    def _set_window_styles(self) -> None:
        hwnd = user32.GetParent(self._win.winfo_id()) or self._win.winfo_id()
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
        )
        _round_corners(hwnd)

    def _show(
        self, text: str, color: str, auto_hide_ms: int | None, meter: bool
    ) -> None:
        if self._hide_job is not None:
            self._root.after_cancel(self._hide_job)
            self._hide_job = None
        for w in (self._frame, self._meter, self._label):
            w.config(bg=color)
        self._label.config(text=text)
        self._recording = meter and self._level_source is not None
        if self._recording:
            self._meter.pack(side="left", padx=(0, 14))
            self._draw_meter()
        else:
            self._meter.pack_forget()
        self._win.update_idletasks()
        self._set_window_styles()
        w = self._win.winfo_reqwidth()
        h = self._win.winfo_reqheight()
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        self._win.geometry(f"+{(sw - w) // 2}+{sh - h - 80}")
        self._win.deiconify()
        if auto_hide_ms:
            self._hide_job = self._root.after(auto_hide_ms, self._win.withdraw)

    def _draw_meter(self) -> None:
        try:
            level = float(self._level_source())
        except Exception:
            level = 0.0
        # Tal ligger runt RMS 0,02–0,15; logliknande skala känns naturligare.
        norm = min(1.0, (level / 0.12) ** 0.5)
        self._meter.delete("all")
        for i in range(_BARS):
            # Mittstapeln högst; nivån styr hur många staplar som "tänds".
            shape = 1.0 - abs(i - (_BARS - 1) / 2) / _BARS
            h = 4 + 12 * norm * shape
            x = 2 + i * 5
            self._meter.create_rectangle(
                x, 18 - h, x + 3, 18, fill="white", outline=""
            )
