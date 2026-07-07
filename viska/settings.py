"""Inställningsfönster (tkinter). Skapas och används enbart i huvudtråden —
tray-menyn begär öppning via App:s UI-kö.
"""

import tkinter as tk
from tkinter import ttk

from viska.hotkey import HotkeyError, parse_hotkey

SV_MODELS = [
    "auto",
    "KBLab/kb-whisper-large",
    "KBLab/kb-whisper-medium",
    "KBLab/kb-whisper-small",
    "KBLab/kb-whisper-base",
]
EN_MODELS = ["large-v3-turbo", "distil-small.en", "small", "medium.en"]

_LIVE_CHOICES = {"Auto (på med GPU)": "auto", "På": True, "Av": False}


class SettingsWindow:
    def __init__(self, root: tk.Tk, cfg: dict, on_save):
        """on_save(ändringar: dict) -> None | str. Returnerad sträng visas
        som fel och fönstret hålls öppet."""
        self._on_save = on_save
        self.win = tk.Toplevel(root)
        self.win.title("Viska – inställningar")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        body = ttk.Frame(self.win, padding=14)
        body.grid(sticky="nsew")
        row = 0

        ttk.Label(body, text="Språk").grid(row=row, column=0, sticky="w", pady=3)
        self._language = tk.StringVar(value=cfg["language"])
        lang_frame = ttk.Frame(body)
        lang_frame.grid(row=row, column=1, sticky="w")
        ttk.Radiobutton(
            lang_frame, text="Svenska", value="sv", variable=self._language
        ).pack(side="left")
        ttk.Radiobutton(
            lang_frame, text="Engelska", value="en", variable=self._language
        ).pack(side="left", padx=(8, 0))
        row += 1

        ttk.Label(body, text="Modell (svenska)").grid(
            row=row, column=0, sticky="w", pady=3
        )
        self._model = tk.StringVar(value=cfg["model"])
        ttk.Combobox(
            body, textvariable=self._model, values=SV_MODELS, width=32
        ).grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Label(body, text="Modell (engelska)").grid(
            row=row, column=0, sticky="w", pady=3
        )
        self._model_en = tk.StringVar(value=cfg["model_en"])
        ttk.Combobox(
            body, textvariable=self._model_en, values=EN_MODELS, width=32
        ).grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Label(body, text="Tangent").grid(row=row, column=0, sticky="w", pady=3)
        self._hotkey = tk.StringVar(value=cfg["hotkey"])
        ttk.Entry(body, textvariable=self._hotkey, width=20).grid(
            row=row, column=1, sticky="w"
        )
        row += 1

        ttk.Label(body, text="Liveläge").grid(row=row, column=0, sticky="w", pady=3)
        reverse = {v: k for k, v in _LIVE_CHOICES.items()}
        # .get med fallback: handredigerad config kan ha t.ex. "på"/"true".
        current = reverse.get(cfg["live"], reverse["auto"])
        self._live = tk.StringVar(value=current)
        ttk.Combobox(
            body,
            textvariable=self._live,
            values=list(_LIVE_CHOICES),
            state="readonly",
            width=20,
        ).grid(row=row, column=1, sticky="w")
        row += 1

        self._sounds = tk.BooleanVar(value=bool(cfg["sounds"]))
        ttk.Checkbutton(body, text="Ljudsignaler", variable=self._sounds).grid(
            row=row, column=1, sticky="w", pady=3
        )
        row += 1

        ttk.Label(body, text="Ordlista / kontext").grid(
            row=row, column=0, sticky="nw", pady=3
        )
        prompt_frame = ttk.Frame(body)
        prompt_frame.grid(row=row, column=1, sticky="w")
        self._prompt = tk.Text(prompt_frame, width=34, height=4, wrap="word")
        self._prompt.pack()
        self._prompt.insert("1.0", cfg.get("initial_prompt", ""))
        ttk.Label(
            prompt_frame,
            text="Namn och facktermer som ska stavas rätt,\nt.ex. \"Avanza, Smask, Lucas Skog\".",
            foreground="#666666",
        ).pack(anchor="w")
        row += 1

        self._error = ttk.Label(body, text="", foreground="#c0392b")
        self._error.grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=1, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Avbryt", command=self.win.destroy).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(buttons, text="Spara", command=self._save).pack(side="right")

    def values(self) -> dict:
        return {
            "language": self._language.get(),
            "model": self._model.get().strip() or "auto",
            "model_en": self._model_en.get().strip() or "large-v3-turbo",
            "hotkey": self._hotkey.get().strip(),
            "live": _LIVE_CHOICES[self._live.get()],
            "sounds": self._sounds.get(),
            "initial_prompt": self._prompt.get("1.0", "end").strip(),
        }

    def _save(self) -> None:
        changes = self.values()
        try:
            parse_hotkey(changes["hotkey"])
        except HotkeyError as e:
            self._error.config(text=str(e))
            return
        error = self._on_save(changes)
        if error:
            self._error.config(text=error)
            return
        self.win.destroy()
