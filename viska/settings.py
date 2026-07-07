"""Inställningsfönster (tkinter). Skapas och används enbart i huvudtråden —
tray-menyn begär öppning via App:s UI-kö.
"""

import queue
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
    def __init__(self, root: tk.Tk, cfg: dict, on_save, gpu_panel: dict | None = None):
        """on_save(ändringar: dict) -> None | str. Returnerad sträng visas
        som fel och fönstret hålls öppet. gpu_panel: {"state", "action",
        "label", "run"} från App._gpu_panel()."""
        self._on_save = on_save
        self._gpu_panel = gpu_panel
        self._gpu_queue: queue.Queue = queue.Queue()
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

        if gpu_panel is not None:
            ttk.Separator(body).grid(
                row=row, column=0, columnspan=2, sticky="we", pady=8
            )
            row += 1
            ttk.Label(body, text="GPU-stöd").grid(row=row, column=0, sticky="nw")
            gpu_frame = ttk.Frame(body)
            gpu_frame.grid(row=row, column=1, sticky="w")
            self._gpu_status = ttk.Label(gpu_frame, text=gpu_panel["state"])
            self._gpu_status.pack(anchor="w")
            self._gpu_progress = ttk.Progressbar(gpu_frame, length=240, maximum=1.0)
            if gpu_panel.get("action"):
                self._gpu_button = ttk.Button(
                    gpu_frame, text=gpu_panel["label"], command=self._run_gpu_action
                )
                self._gpu_button.pack(anchor="w", pady=(4, 0))
            row += 1
            self._poll_gpu()

        self._error = ttk.Label(body, text="", foreground="#c0392b")
        self._error.grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=1, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Avbryt", command=self.win.destroy).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(buttons, text="Spara", command=self._save).pack(side="right")

    # -- GPU-panelen ------------------------------------------------------------

    def _run_gpu_action(self) -> None:
        self._gpu_button.config(state="disabled")
        self._gpu_progress.pack(anchor="w", pady=(4, 0))
        # Callbackarna anropas från arbetstråden — marshallas via kön.
        self._gpu_panel["run"](
            progress=lambda t, f: self._gpu_queue.put(("progress", t, f)),
            done=lambda msg: self._gpu_queue.put(("done", msg, 1.0)),
            error=lambda msg: self._gpu_queue.put(("error", msg, 0.0)),
        )

    def _poll_gpu(self) -> None:
        try:
            while True:
                kind, text, frac = self._gpu_queue.get_nowait()
                if kind == "progress":
                    self._gpu_status.config(text=text)
                    self._gpu_progress["value"] = frac
                else:
                    self._gpu_status.config(text=text)
                    self._gpu_progress.pack_forget()
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(150, self._poll_gpu)

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
