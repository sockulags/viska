"""Inställningsfönster med flikar: Allmänt (klarspråk för vardagsbruk),
Ordlista (global + per app), Avancerat (råa tekniska värden).
Skapas och används enbart i huvudtråden — trayn begär öppning via App:s
UI-kö.
"""

import os
import queue
import tkinter as tk
from tkinter import ttk

from viska.config import CONFIG_PATH, LOG_PATH
from viska.focus import visible_apps
from viska.hotkey import HotkeyError, parse_hotkey

# Livekänsla: (passintervall, tystnadskrav). Kortare = ivrigare inskrivning.
FEELS = {
    "Lugn": (0.5, 0.6),
    "Normal": (0.4, 0.45),
    "Snabb": (0.3, 0.35),
}

QUALITY = {
    "Auto – bäst för din dator": "auto",
    "Bäst kvalitet (kräver GPU)": "KBLab/kb-whisper-large",
    "Snabbast (mindre modell)": "KBLab/kb-whisper-small",
}

_LIVE_CHOICES = {"Auto (på med GPU)": "auto", "På": True, "Av": False}
_INJECT_CHOICES = {"Klistra in (standard)": "paste", "Skriv tecken för tecken": "type"}
_PROFILE_LANG = {"Samma som aktivt": None, "Svenska": "sv", "Engelska": "en"}

# Tkinter-keysym → vårt tangentnamn (hotkey.parse_hotkey-format).
_KEYSYMS = {
    **{f"F{i}": f"f{i}" for i in range(1, 25)},
    "space": "space",
    "Scroll_Lock": "scroll lock",
    "Pause": "pause",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Prior": "page up",
    "Next": "page down",
}
_MODIFIER_KEYSYMS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R",
    "Alt_L", "Alt_R", "Win_L", "Win_R", "Super_L", "Super_R", "Caps_Lock",
}


def _themed_text(parent, **kwargs) -> tk.Text:
    """tk.Text följer inte ttk-temat — hämta fältfärgerna från stilen så
    att ordlistorna inte lyser vita i mörkt läge."""
    style = ttk.Style(parent)
    bg = style.lookup("TEntry", "fieldbackground") or "white"
    fg = style.lookup("TEntry", "foreground") or "black"
    return tk.Text(
        parent, bg=bg, fg=fg, insertbackground=fg,
        relief="flat", highlightthickness=1,
        highlightbackground=style.lookup("TEntry", "bordercolor") or "#888888",
        **kwargs,
    )


def _row(parent, label: str, hint: str | None = None):
    frame = ttk.Frame(parent)
    frame.pack(fill="x", pady=6)
    left = ttk.Frame(frame)
    left.pack(side="left", fill="x")
    ttk.Label(left, text=label).pack(anchor="w")
    if hint:
        ttk.Label(left, text=hint, foreground="#888888", font=("Segoe UI", 8)).pack(
            anchor="w"
        )
    holder = ttk.Frame(frame)
    holder.pack(side="right")
    return holder


class SettingsWindow:
    def __init__(self, root: tk.Tk, cfg: dict, on_save, gpu_panel: dict | None = None):
        """on_save(ändringar: dict) -> None | str (fel visas, fönstret kvar).
        gpu_panel: {"state", "action", "label", "run"} från App._gpu_panel()."""
        self._on_save = on_save
        self._gpu_panel = gpu_panel
        self._gpu_queue: queue.Queue = queue.Queue()
        self._orig_model = cfg["model"]
        # Tål handredigerad config: hoppa över trasiga profilposter.
        self._profiles = {
            k.lower(): dict(v)
            for k, v in cfg["app_profiles"].items()
            if isinstance(v, dict)
        }
        self._selected_app: str | None = None
        self._capturing = False

        self.win = tk.Toplevel(root)
        self.win.title("Viska – inställningar")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        notebook = ttk.Notebook(self.win)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        general = ttk.Frame(notebook, padding=12)
        wordlist = ttk.Frame(notebook, padding=12)
        advanced = ttk.Frame(notebook, padding=12)
        notebook.add(general, text=" Allmänt ")
        notebook.add(wordlist, text=" Ordlista ")
        notebook.add(advanced, text=" Avancerat ")

        # ---- Allmänt --------------------------------------------------------

        holder = _row(general, "Språk")
        self._language = tk.StringVar(value=cfg["language"])
        ttk.Radiobutton(
            holder, text="Svenska", value="sv", variable=self._language
        ).pack(side="left")
        ttk.Radiobutton(
            holder, text="Engelska", value="en", variable=self._language
        ).pack(side="left", padx=(8, 0))

        holder = _row(general, "Dikteringstangent", "Klicka och tryck på tangenten")
        self._hotkey = tk.StringVar(value=cfg["hotkey"])
        self._hotkey_btn = ttk.Button(
            holder, text=cfg["hotkey"].upper(), width=18, command=self._capture_hotkey
        )
        self._hotkey_btn.pack()

        holder = _row(general, "Livekänsla", "Hur snabbt fraser skrivs in vid paus")
        current_feel = next(
            (
                name
                for name, (i, s) in FEELS.items()
                if abs(i - cfg["live_interval_s"]) < 0.01
                and abs(s - cfg["live_commit_silence_s"]) < 0.01
            ),
            "Egen",
        )
        feel_values = list(FEELS) + (["Egen"] if current_feel == "Egen" else [])
        self._feel = tk.StringVar(value=current_feel)
        ttk.Combobox(
            holder, textvariable=self._feel, values=feel_values,
            state="readonly", width=16,
        ).pack()

        holder = _row(general, "Liveläge")
        reverse_live = {v: k for k, v in _LIVE_CHOICES.items()}
        self._live = tk.StringVar(
            value=reverse_live.get(cfg["live"], reverse_live["auto"])
        )
        ttk.Combobox(
            holder, textvariable=self._live, values=list(_LIVE_CHOICES),
            state="readonly", width=20,
        ).pack()

        holder = _row(general, "Kvalitet (svenska)")
        reverse_q = {v: k for k, v in QUALITY.items()}
        current_q = reverse_q.get(cfg["model"], "Egen (se Avancerat)")
        q_values = list(QUALITY) + (
            ["Egen (se Avancerat)"] if current_q not in QUALITY else []
        )
        self._quality = tk.StringVar(value=current_q)
        ttk.Combobox(
            holder, textvariable=self._quality, values=q_values,
            state="readonly", width=26,
        ).pack()

        self._sounds = tk.BooleanVar(value=bool(cfg["sounds"]))
        ttk.Checkbutton(general, text="Ljudsignaler", variable=self._sounds).pack(
            anchor="w", pady=6
        )

        if gpu_panel is not None:
            ttk.Separator(general).pack(fill="x", pady=8)
            gpu_frame = ttk.Frame(general)
            gpu_frame.pack(fill="x")
            ttk.Label(gpu_frame, text="GPU-stöd", font=("Segoe UI", 9, "bold")).pack(
                anchor="w"
            )
            self._gpu_status = ttk.Label(gpu_frame, text=gpu_panel["state"])
            self._gpu_status.pack(anchor="w")
            self._gpu_progress = ttk.Progressbar(gpu_frame, length=280, maximum=1.0)
            if gpu_panel.get("action"):
                self._gpu_button = ttk.Button(
                    gpu_frame, text=gpu_panel["label"], command=self._run_gpu_action
                )
                self._gpu_button.pack(anchor="w", pady=(4, 0))
            self._poll_gpu()

        # ---- Ordlista -------------------------------------------------------

        ttk.Label(wordlist, text="Global ordlista").pack(anchor="w")
        ttk.Label(
            wordlist,
            text="Namn och facktermer som ska stavas rätt, t.ex. \"Avanza, Smask\".",
            foreground="#888888", font=("Segoe UI", 8),
        ).pack(anchor="w")
        self._prompt = _themed_text(wordlist, width=52, height=3, wrap="word")
        self._prompt.pack(fill="x", pady=(4, 10))
        self._prompt.insert("1.0", cfg["initial_prompt"])

        ttk.Separator(wordlist).pack(fill="x", pady=4)
        ttk.Label(wordlist, text="Per app").pack(anchor="w")
        ttk.Label(
            wordlist,
            text="Extra ordlista och inskrivningssätt när en viss app har fokus.",
            foreground="#888888", font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 4))

        picker = ttk.Frame(wordlist)
        picker.pack(fill="x")
        self._app_list = tk.Listbox(picker, height=4, exportselection=False)
        self._app_list.pack(side="left", fill="both", expand=True)
        self._app_list.bind("<<ListboxSelect>>", self._on_profile_select)
        buttons = ttk.Frame(picker)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Ny…", width=12, command=self._new_profile).pack(
            pady=2
        )
        ttk.Button(
            buttons, text="Ta bort", width=12, command=self._remove_profile
        ).pack(pady=2)

        editor = ttk.Frame(wordlist)
        editor.pack(fill="x", pady=(8, 0))
        holder = _row(editor, "App (processnamn)")
        self._profile_app = tk.StringVar()
        self._profile_app_box = ttk.Combobox(
            holder, textvariable=self._profile_app, width=28
        )
        self._profile_app_box.pack()
        holder = _row(editor, "Ordlista för appen")
        self._profile_prompt = _themed_text(holder, width=34, height=2, wrap="word")
        self._profile_prompt.pack()
        holder = _row(editor, "Inskrivning")
        reverse_inject = {v: k for k, v in _INJECT_CHOICES.items()}
        self._profile_inject = tk.StringVar(value=list(_INJECT_CHOICES)[0])
        ttk.Combobox(
            holder, textvariable=self._profile_inject,
            values=list(_INJECT_CHOICES), state="readonly", width=26,
        ).pack()
        holder = _row(editor, "Språk i appen", "Byts aldrig automatiskt")
        self._profile_lang = tk.StringVar(value=list(_PROFILE_LANG)[0])
        ttk.Combobox(
            holder, textvariable=self._profile_lang,
            values=list(_PROFILE_LANG), state="readonly", width=26,
        ).pack()
        ttk.Button(
            editor, text="Spara profil", command=self._save_profile
        ).pack(anchor="e", pady=(4, 0))
        self._reverse_inject = reverse_inject
        self._refresh_profile_list()

        # ---- Avancerat ------------------------------------------------------

        self._adv: dict[str, tk.Variable] = {}

        def adv_entry(label, key, value, width=34):
            holder = _row(advanced, label)
            var = tk.StringVar(value="" if value is None else str(value))
            ttk.Entry(holder, textvariable=var, width=width).pack()
            self._adv[key] = var

        def adv_combo(label, key, value, choices):
            holder = _row(advanced, label)
            var = tk.StringVar(value="auto" if value is None else str(value))
            ttk.Combobox(
                holder, textvariable=var, values=choices, width=32
            ).pack()
            self._adv[key] = var

        adv_entry("Modell svenska (HF-id)", "model", cfg["model"])
        adv_entry("Modell engelska", "model_en", cfg["model_en"])
        adv_combo("Beräkningsenhet", "device", cfg["device"], ["auto", "cuda", "cpu"])
        adv_combo(
            "Precision", "compute_type", cfg["compute_type"],
            ["auto", "float16", "int8_float16", "int8"],
        )
        adv_entry("Beam size (tomt = auto)", "beam_size", cfg["beam_size"], width=8)
        holder = _row(advanced, "Inskrivning (standard)")
        reverse_all_inject = {v: k for k, v in _INJECT_CHOICES.items()}
        self._inject = tk.StringVar(
            value=reverse_all_inject.get(
                cfg["inject_mode"], list(_INJECT_CHOICES)[0]
            )
        )
        ttk.Combobox(
            holder, textvariable=self._inject, values=list(_INJECT_CHOICES),
            state="readonly", width=26,
        ).pack()
        adv_entry("Max inspelning (s)", "max_recording_s", cfg["max_recording_s"], 8)
        adv_entry(
            "Hålltröskel (s)", "hold_threshold_s", cfg["hold_threshold_s"], 8
        )
        self._restore = tk.BooleanVar(value=bool(cfg["restore_clipboard"]))
        self._append = tk.BooleanVar(value=bool(cfg["append_space"]))
        self._keep_mic = tk.BooleanVar(value=bool(cfg["keep_mic_open"]))
        self._updates = tk.BooleanVar(value=bool(cfg["check_updates"]))
        for text, var in (
            ("Återställ klippbordet efter inklistring", self._restore),
            ("Mellanslag efter varje diktering", self._append),
            ("Håll mikrofonströmmen öppen (ingen klippt start)", self._keep_mic),
            ("Sök efter uppdateringar vid start", self._updates),
        ):
            ttk.Checkbutton(advanced, text=text, variable=var).pack(anchor="w")
        files = ttk.Frame(advanced)
        files.pack(anchor="w", pady=(8, 0))
        ttk.Button(
            files, text="Öppna config-filen",
            command=lambda: os.startfile(CONFIG_PATH),
        ).pack(side="left")
        ttk.Button(
            files, text="Öppna loggen", command=lambda: os.startfile(LOG_PATH)
        ).pack(side="left", padx=(6, 0))

        # ---- Fot ------------------------------------------------------------

        footer = ttk.Frame(self.win, padding=(10, 6, 10, 10))
        footer.pack(fill="x")
        self._error = ttk.Label(footer, text="", foreground="#c0392b")
        self._error.pack(side="left")
        ttk.Button(footer, text="Avbryt", command=self.win.destroy).pack(side="right")
        ttk.Button(footer, text="Spara", command=self._save, style="Accent.TButton").pack(
            side="right", padx=(0, 6)
        )

    # -- Tangentfångst -----------------------------------------------------------
    # Bindningen sitter på KNAPPEN, inte fönstret: en fönsterbindning hade
    # även fångat tangenter man skriver i ordlistefälten och tyst gjort
    # första bokstaven till global dikteringstangent. Klick någon
    # annanstans (fokus lämnar knappen) eller Esc avbryter fångsten.
    # Obs: den NUVARANDE tangenten går inte att fånga — den konsumeras av
    # den aktiva registreringen — men att behålla den kräver ju ingen ändring.

    def _capture_hotkey(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self._hotkey_btn.config(text="Tryck tangent… (Esc avbryter)")
        self._hotkey_btn.bind("<Key>", self._on_capture_key)
        self._hotkey_btn.bind("<FocusOut>", self._cancel_capture)
        self._hotkey_btn.focus_set()

    def _cancel_capture(self, _event=None) -> None:
        if not self._capturing:
            return
        self._capturing = False
        self._hotkey_btn.unbind("<Key>")
        self._hotkey_btn.unbind("<FocusOut>")
        self._hotkey_btn.config(text=self._hotkey.get().upper())

    def _on_capture_key(self, event) -> str:
        if event.keysym == "Escape":
            self._cancel_capture()
            return "break"
        if event.keysym in _MODIFIER_KEYSYMS:
            return "break"
        key = _KEYSYMS.get(event.keysym)
        if key is None and len(event.keysym) == 1 and event.keysym.isalnum():
            key = event.keysym.lower()
        if key is None:
            self._hotkey_btn.config(text="Ogiltig – försök igen (Esc avbryter)")
            return "break"
        mods = []
        if event.state & 0x0004:
            mods.append("ctrl")
        if event.state & 0x20000:
            mods.append("alt")
        if event.state & 0x0001 and len(key) > 1:
            mods.append("shift")  # shift+bokstav vore bara versal — hoppa
        hotkey = "+".join(mods + [key])
        self._hotkey.set(hotkey)
        self._capturing = False
        self._hotkey_btn.unbind("<Key>")
        self._hotkey_btn.unbind("<FocusOut>")
        self._hotkey_btn.config(text=hotkey.upper())
        return "break"

    # -- Per-app-profiler ----------------------------------------------------------

    def _refresh_profile_list(self) -> None:
        self._app_list.delete(0, "end")
        for app in sorted(self._profiles):
            self._app_list.insert("end", app)

    def _on_profile_select(self, _event) -> None:
        selection = self._app_list.curselection()
        if not selection:
            return
        app = self._app_list.get(selection[0])
        self._selected_app = app
        profile = self._profiles[app]
        self._profile_app.set(app)
        self._profile_prompt.delete("1.0", "end")
        self._profile_prompt.insert("1.0", profile.get("initial_prompt", ""))
        self._profile_inject.set(
            self._reverse_inject.get(
                profile.get("inject_mode", "paste"), list(_INJECT_CHOICES)[0]
            )
        )
        lang = profile.get("language")
        self._profile_lang.set(
            {None: "Samma som aktivt", "sv": "Svenska", "en": "Engelska"}.get(
                lang, "Samma som aktivt"
            )
        )

    def _new_profile(self) -> None:
        self._selected_app = None
        self._profile_app.set("")
        self._profile_prompt.delete("1.0", "end")
        self._profile_inject.set(list(_INJECT_CHOICES)[0])
        self._profile_lang.set(list(_PROFILE_LANG)[0])
        self._profile_app_box.config(values=visible_apps())
        self._profile_app_box.focus_set()

    def _save_profile(self) -> None:
        app = self._profile_app.get().strip().lower()
        if not app:
            self._error.config(text="Ange appens processnamn (t.ex. code.exe)")
            return
        profile = {}
        prompt = self._profile_prompt.get("1.0", "end").strip()
        if prompt:
            profile["initial_prompt"] = prompt
        inject = _INJECT_CHOICES[self._profile_inject.get()]
        if inject != "paste":
            profile["inject_mode"] = inject
        lang = _PROFILE_LANG[self._profile_lang.get()]
        if lang:
            profile["language"] = lang
        if self._selected_app and self._selected_app != app:
            self._profiles.pop(self._selected_app, None)
        self._profiles[app] = profile
        self._selected_app = app
        self._error.config(text="")
        self._refresh_profile_list()

    def _remove_profile(self) -> None:
        selection = self._app_list.curselection()
        if selection:
            self._profiles.pop(self._app_list.get(selection[0]), None)
            self._refresh_profile_list()

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
                self._gpu_status.config(text=text)
                if kind == "progress":
                    self._gpu_progress["value"] = frac
                else:
                    self._gpu_progress.pack_forget()
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(150, self._poll_gpu)

    # -- Spara --------------------------------------------------------------------

    def values(self) -> dict:
        changes = {
            "language": self._language.get(),
            "hotkey": self._hotkey.get().strip(),
            "live": _LIVE_CHOICES[self._live.get()],
            "sounds": self._sounds.get(),
            "initial_prompt": self._prompt.get("1.0", "end").strip(),
            "app_profiles": dict(self._profiles),
            "inject_mode": _INJECT_CHOICES[self._inject.get()],
            "restore_clipboard": self._restore.get(),
            "append_space": self._append.get(),
            "keep_mic_open": self._keep_mic.get(),
            "check_updates": self._updates.get(),
        }
        if self._feel.get() in FEELS:
            interval, silence = FEELS[self._feel.get()]
            changes["live_interval_s"] = interval
            changes["live_commit_silence_s"] = silence
        # Avancerat vinner bara om fältet uttryckligen ÄNDRATS — annars
        # gäller kvalitetsvalet i Allmänt (en förenkling av samma sak).
        adv_model = self._adv["model"].get().strip()
        if adv_model and adv_model != self._orig_model:
            changes["model"] = adv_model
        elif self._quality.get() in QUALITY:
            changes["model"] = QUALITY[self._quality.get()]
        else:
            changes["model"] = self._orig_model
        changes["model_en"] = self._adv["model_en"].get().strip() or "large-v3-turbo"
        changes["device"] = self._adv["device"].get().strip() or "auto"
        compute = self._adv["compute_type"].get().strip()
        changes["compute_type"] = None if compute in ("", "auto") else compute
        beam = self._adv["beam_size"].get().strip()
        changes["beam_size"] = int(beam) if beam.isdigit() else None
        changes["max_recording_s"] = int(
            float(self._adv["max_recording_s"].get() or 300)
        )
        changes["hold_threshold_s"] = float(
            self._adv["hold_threshold_s"].get().replace(",", ".") or 0.35
        )
        return changes

    def _save(self) -> None:
        # Har användaren fyllt i profilredigeraren utan att klicka "Spara
        # profil" ska det inte tyst kastas bort.
        if self._profile_app.get().strip():
            self._save_profile()
        try:
            changes = self.values()
        except ValueError as e:
            self._error.config(text=f"Ogiltigt värde: {e}")
            return
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
