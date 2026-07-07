"""Gemensamt UI-lager: palett, fönsteruppsättning och egna kontroller.

sv-ttk stylar bara ttk-widgets — Toplevel-bakgrunder, tk.Text/Listbox
och titelraden är klassisk tk/Win32 och måste sättas explicit, annars
blir resultatet en skev blandning av tema och Windows 2000. Allt som
rör färger går därför genom paletten här, ingen widget får ärva
standardfärger.
"""

import ctypes
import logging
import tkinter as tk
from tkinter import ttk

log = logging.getLogger(__name__)

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

_LIGHT = {
    "window": "#fafafa",
    "surface": "#f0f0f0",
    "field_bg": "#ffffff",
    "field_fg": "#1a1a1a",
    "border": "#d0d0d0",
    "text": "#1a1a1a",
    "muted": "#6b6b6b",
    "accent": "#005fb8",
    "accent_fg": "#ffffff",
    "chip_bg": "#e8e8e8",
    "chip_fg": "#444444",
    "success_bg": "#e1f5ee",
    "success_fg": "#085041",
    "warn_bg": "#faeeda",
    "warn_fg": "#633806",
}
_DARK = {
    "window": "#1c1c1c",
    "surface": "#242424",
    "field_bg": "#2b2b2b",
    "field_fg": "#fafafa",
    "border": "#404040",
    "text": "#fafafa",
    "muted": "#9a9a9a",
    "accent": "#60cdff",
    "accent_fg": "#1a1a1a",
    "chip_bg": "#333333",
    "chip_fg": "#cccccc",
    "success_bg": "#0f3d30",
    "success_fg": "#9fe1cb",
    "warn_bg": "#41300f",
    "warn_fg": "#fac775",
}


def refresh_theme() -> None:
    """Läs Windows appläge NU och applicera sv-ttk därefter — anropas vid
    varje fönsteröppning så att ett temabyte i Windows plockas upp."""
    try:
        import darkdetect
        import sv_ttk

        theme = (darkdetect.theme() or "light").lower()
        if sv_ttk.get_theme() != theme:
            sv_ttk.set_theme(theme)
        # sv-ttk lämnar vissa widgetbakgrunder olösta (checkbuttons får
        # klassisk grå platta bakom texten) — sätt dem explicit ur paletten.
        pal = _DARK if theme == "dark" else _LIGHT
        style = ttk.Style()
        for name in ("TCheckbutton", "TRadiobutton", "TLabel"):
            style.configure(
                name, background=pal["window"], foreground=pal["text"]
            )
    except Exception:
        log.debug("Kunde inte applicera temat", exc_info=True)


def palette() -> dict:
    try:
        import sv_ttk

        return _DARK if sv_ttk.get_theme() == "dark" else _LIGHT
    except Exception:
        return _LIGHT


def setup_window(win: tk.Toplevel | tk.Tk) -> dict:
    """Bakgrund ur paletten + mörk titelrad + rundade hörn. Returnerar
    paletten så anroparen kan färga sina icke-ttk-widgets."""
    pal = palette()
    win.configure(bg=pal["window"])
    win.update_idletasks()
    try:
        hwnd = win.winfo_id()
        parent = ctypes.windll.user32.GetParent(hwnd)
        hwnd = parent or hwnd
        dark = ctypes.c_int(1 if pal is _DARK else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark), 4
        )
        pref = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), 4
        )
    except Exception:
        pass  # äldre Windows
    return pal


def themed_text(parent, **kwargs) -> tk.Text:
    pal = palette()
    return tk.Text(
        parent,
        bg=pal["field_bg"],
        fg=pal["field_fg"],
        insertbackground=pal["field_fg"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=pal["border"],
        highlightcolor=pal["accent"],
        font=("Segoe UI", 9),
        **kwargs,
    )


def themed_listbox(parent, **kwargs) -> tk.Listbox:
    pal = palette()
    return tk.Listbox(
        parent,
        bg=pal["field_bg"],
        fg=pal["field_fg"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=pal["border"],
        highlightcolor=pal["accent"],
        selectbackground=pal["accent"],
        selectforeground=pal["accent_fg"],
        font=("Segoe UI", 9),
        **kwargs,
    )


class Segmented:
    """Segmenterad kontroll (t.ex. Lugn | Normal | Snabb): ihopsatta
    chips där den valda är accentfylld. Egna tk.Label-chips i stället
    för ttk-knappar — då äger vi färgerna fullt ut."""

    def __init__(self, parent, options: list[str], variable: tk.StringVar):
        self.variable = variable
        pal = palette()
        self._pal = pal
        self.frame = tk.Frame(parent, bg=pal["window"])
        self._chips: dict[str, tk.Label] = {}
        for i, option in enumerate(options):
            chip = tk.Label(
                self.frame,
                text=option,
                font=("Segoe UI", 9),
                padx=14,
                pady=5,
                cursor="hand2",
            )
            chip.grid(row=0, column=i, padx=(0 if i == 0 else 2, 0))
            chip.bind("<Button-1>", lambda _e, o=option: self._select(o))
            self._chips[option] = chip
        variable.trace_add("write", lambda *_: self._paint())
        self._paint()

    def _select(self, option: str) -> None:
        self.variable.set(option)

    def _paint(self) -> None:
        current = self.variable.get()
        for option, chip in self._chips.items():
            if option == current:
                chip.config(bg=self._pal["accent"], fg=self._pal["accent_fg"])
            else:
                chip.config(bg=self._pal["chip_bg"], fg=self._pal["chip_fg"])


def status_card(parent, kind: str, title: str, body: str) -> tk.Frame:
    """Färgat statuskort (kind: "success" eller "warn")."""
    pal = palette()
    bg = pal[f"{kind}_bg"]
    fg = pal[f"{kind}_fg"]
    card = tk.Frame(parent, bg=bg, padx=12, pady=8)
    dot = tk.Label(card, text="●", bg=bg, fg=fg, font=("Segoe UI", 11))
    dot.pack(side="left", padx=(0, 8))
    texts = tk.Frame(card, bg=bg)
    texts.pack(side="left", fill="x", expand=True)
    tk.Label(
        texts, text=title, bg=bg, fg=fg, font=("Segoe UI", 9, "bold"), anchor="w"
    ).pack(fill="x")
    if body:
        tk.Label(
            texts, text=body, bg=bg, fg=fg, font=("Segoe UI", 8), anchor="w"
        ).pack(fill="x")
    return card


def section(parent, title: str) -> None:
    pal = palette()
    tk.Label(
        parent,
        text=title.upper(),
        font=("Segoe UI", 8, "bold"),
        fg=pal["muted"],
        bg=pal["window"],
        anchor="w",
    ).pack(fill="x", pady=(14, 2))
