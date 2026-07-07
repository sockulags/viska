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

from viska import ui

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

_POLL_MS = 50
_BARS = 5
# Tonvalsmenyn stängs normalt med Esc, klick på ett val eller klick utanför.
# Skulle den mot förmodan inte få tangentfokus (fokus/klick-utanför slutar då
# stänga den) städas den bort av den här säkerhetstimern så inget topmost-
# fönster kan bli kvarhängande.
_TONE_MENU_TIMEOUT_MS = 6000

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


class ToneMenu:
    """Liten meny nära statuspillen för val av poleringston vid F8-dubbeltryck.

    Samma trådmönster som Overlay: tkinter är huvudtrådsbundet, så begäran
    läggs i en kö som huvudtråden tömmer — menyn skapas och stängs alltid i
    huvudtråden. Till skillnad från pillen TAR menyn tangentfokus (den ska
    fånga Esc och klick utanför); anroparen återställer själv fokus till
    målfönstret innan markeringen läses. Valet gäller bara den poleringen och
    levereras via on_choose(style)."""

    def __init__(self, root: tk.Tk):
        self._root = root
        self._queue: queue.Queue = queue.Queue()
        self._win: tk.Toplevel | None = None
        self._on_choose = None
        self._timeout_job = None
        self._poll()

    # -- Trådsäkra metoder ------------------------------------------------------

    def show(self, options, on_choose) -> None:
        """options: lista av (etikett, style, is_default). style None = appens
        standardton. on_choose(style) anropas i huvudtråden vid val. Trådsäker
        — kan anropas från hotkey-tråden."""
        self._queue.put((options, on_choose))

    def close(self) -> None:
        self._queue.put((None, None))

    # -- Körs i huvudtråden -----------------------------------------------------

    def _poll(self) -> None:
        try:
            while True:
                options, on_choose = self._queue.get_nowait()
                if options is None:
                    self._close()
                else:
                    self._open(options, on_choose)
        except queue.Empty:
            pass
        self._root.after(_POLL_MS, self._poll)

    def _open(self, options, on_choose) -> None:
        self._close()
        self._on_choose = on_choose
        pal = ui.palette()
        win = tk.Toplevel(self._root)
        self._win = win
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        # Tunn kant runt panelen: en yttre ram i kantfärg med panelen 1 px in.
        outer = tk.Frame(win, bg=pal["border"])
        outer.pack(fill="both", expand=True)
        frame = tk.Frame(outer, bg=pal["surface"], padx=4, pady=4)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(
            frame,
            text="Polera med ton",
            bg=pal["surface"],
            fg=pal["muted"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
            padx=10,
            pady=2,
        ).pack(fill="x", pady=(2, 6))
        for label, style, is_default in options:
            base_bg = pal["surface"]
            base_fg = pal["accent"] if is_default else pal["text"]
            row = tk.Label(
                frame,
                text=("● " + label) if is_default else ("    " + label),
                bg=base_bg,
                fg=base_fg,
                font=("Segoe UI", 10, "bold" if is_default else "normal"),
                anchor="w",
                padx=12,
                pady=6,
                cursor="hand2",
            )
            row.pack(fill="x")
            row.bind(
                "<Enter>",
                lambda _e, r=row: r.config(bg=pal["accent"], fg=pal["accent_fg"]),
            )
            row.bind(
                "<Leave>",
                lambda _e, r=row, b=base_bg, f=base_fg: r.config(bg=b, fg=f),
            )
            row.bind("<Button-1>", lambda _e, s=style: self._choose(s))
        win.update_idletasks()
        w = win.winfo_reqwidth()
        h = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        # Centrerad, strax ovanför pillen (som ligger på sh - h - 80).
        win.geometry(f"+{(sw - w) // 2}+{sh - h - 130}")
        _round_corners(user32.GetParent(win.winfo_id()) or win.winfo_id())
        win.bind("<Escape>", lambda _e: self._close())
        # Klick utanför menyn (den tappar då fokus) stänger den.
        win.bind("<FocusOut>", lambda _e: self._close())
        win.deiconify()
        win.lift()
        win.focus_force()
        self._timeout_job = self._root.after(_TONE_MENU_TIMEOUT_MS, self._close)

    def _choose(self, style) -> None:
        cb = self._on_choose
        self._close()
        if cb is not None:
            cb(style)

    def _close(self) -> None:
        job, self._timeout_job = self._timeout_job, None
        if job is not None:
            try:
                self._root.after_cancel(job)
            except Exception:
                pass
        win, self._win = self._win, None
        self._on_choose = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
