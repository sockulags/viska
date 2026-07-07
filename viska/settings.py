"""Inställningsfönster med flikar: Allmänt (klarspråk för vardagsbruk),
Ordlista (global + per app), Avancerat (råa tekniska värden).

Skapas och används enbart i huvudtråden — trayn begär öppning via App:s
UI-kö. Alla färger går via viska.ui: inget får ärva klassiska
tk-standardfärger (det var så Windows 2000-blandningen uppstod).
"""

import os
import queue
import tkinter as tk
from tkinter import ttk

from viska import autostart, ui
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

_FIELD_W = 30  # enhetlig fältbredd: lugn högerkant i alla flikar


class _ActionPanel:
    """Statuskort + åtgärdsknapp + förlopp för långkörande åtgärder
    (GPU-stöd, poleringsmodell). Callbackarna anropas från arbetstrådar
    och marshallas via kö till huvudtråden."""

    def __init__(self, parent, win, panel: dict):
        self.win = win
        self.panel = panel
        self.queue: queue.Queue = queue.Queue()
        pal = ui.palette()
        kind = "success" if panel.get("kind") == "ok" else "warn"
        ui.status_card(parent, kind, panel["state"], panel.get("body", "")).pack(
            fill="x", pady=(2, 4)
        )
        self.status = tk.Label(
            parent, text="", bg=pal["window"], fg=pal["muted"],
            font=("Segoe UI", 8), anchor="w",
        )
        self.status.pack(fill="x")
        self.progress = ttk.Progressbar(parent, length=300, maximum=1.0)
        if panel.get("action"):
            self.button = ttk.Button(parent, text=panel["label"], command=self._run)
            self.button.pack(anchor="w", pady=(2, 0))
        self._poll()

    def _run(self) -> None:
        self.button.config(state="disabled")
        self.progress.pack(anchor="w", pady=(4, 0))
        self.panel["run"](
            progress=lambda t, f: self.queue.put(("progress", t, f)),
            done=lambda m: self.queue.put(("done", m, 1.0)),
            error=lambda m: self.queue.put(("error", m, 0.0)),
        )

    def _poll(self) -> None:
        try:
            while True:
                kind, text, frac = self.queue.get_nowait()
                self.status.config(text=text)
                if kind == "progress":
                    self.progress["value"] = frac
                else:
                    self.progress.pack_forget()
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(150, self._poll)


class SettingsWindow:
    def __init__(
        self,
        root: tk.Tk,
        cfg: dict,
        on_save,
        gpu_panel: dict | None = None,
        polish_panel: dict | None = None,
        polish_ready: bool = False,
    ):
        """on_save(ändringar: dict) -> None | str (fel visas, fönstret kvar).
        Panelerna kommer från App._gpu_panel()/_polish_panel().
        polish_ready: True när poleringen är nedladdad/nåbar. Är den inte det
        visas en hjälptext i stället för flow-lägets kryssrutor — men bara när
        flow faktiskt är avslaget. Är en kvarglömd flow-flagga på (t.ex. efter
        att poleringen tagits bort) visas kryssrutan ändå, så att den går att
        stänga av utan att handredigera config."""
        ui.refresh_theme()
        self._on_save = on_save
        self._polish_ready = polish_ready
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
        self._pal = ui.setup_window(self.win)
        pal = self._pal

        notebook = ttk.Notebook(self.win)
        notebook.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        general = self._tab(notebook, " Allmänt ")
        wordlist = self._tab(notebook, " Ordlista ")
        advanced = self._tab(notebook, " Avancerat ")

        # ---- Allmänt --------------------------------------------------------

        self._language_names = {"sv": "Svenska", "en": "Engelska"}
        self._language = tk.StringVar(
            value=self._language_names.get(cfg["language"], "Svenska")
        )
        holder = self._row(general, "Språk")
        ui.Segmented(
            holder, list(self._language_names.values()), self._language
        ).frame.pack()

        self._hotkey = tk.StringVar(value=cfg["hotkey"])
        holder = self._row(
            general, "Dikteringstangent", hint="Klicka och tryck på tangenten"
        )
        self._hotkey_btn = ttk.Button(
            holder, text=cfg["hotkey"].upper(), width=20, command=self._capture_hotkey
        )
        self._hotkey_btn.pack()

        current_feel = next(
            (
                name
                for name, (i, s) in FEELS.items()
                if abs(i - cfg["live_interval_s"]) < 0.01
                and abs(s - cfg["live_commit_silence_s"]) < 0.01
            ),
            "Egen",
        )
        self._feel = tk.StringVar(value=current_feel)
        feel_options = list(FEELS) + (["Egen"] if current_feel == "Egen" else [])
        holder = self._row(
            general, "Livekänsla", hint="Hur snabbt fraser skrivs in vid paus"
        )
        ui.Segmented(holder, feel_options, self._feel).frame.pack()

        reverse_live = {v: k for k, v in _LIVE_CHOICES.items()}
        self._live = tk.StringVar(
            value=reverse_live.get(cfg["live"], reverse_live["auto"])
        )
        holder = self._row(general, "Liveläge")
        ttk.Combobox(
            holder, textvariable=self._live, values=list(_LIVE_CHOICES),
            state="readonly", width=_FIELD_W - 2,
        ).pack()

        reverse_q = {v: k for k, v in QUALITY.items()}
        current_q = reverse_q.get(cfg["model"], "Egen (se Avancerat)")
        q_values = list(QUALITY) + (
            ["Egen (se Avancerat)"] if current_q not in QUALITY else []
        )
        self._quality = tk.StringVar(value=current_q)
        holder = self._row(general, "Kvalitet (svenska)")
        ttk.Combobox(
            holder, textvariable=self._quality, values=q_values,
            state="readonly", width=_FIELD_W - 2,
        ).pack()

        self._sounds = tk.BooleanVar(value=bool(cfg["sounds"]))
        holder = self._row(general, "Ljudsignaler")
        ttk.Checkbutton(holder, variable=self._sounds).pack()

        # Tillståndet läses från Startup-genvägen, inte config — kan
        # aldrig hamna i osynk med verkligheten.
        self._autostart = tk.BooleanVar(value=autostart.enabled())
        holder = self._row(general, "Starta med Windows")
        ttk.Checkbutton(holder, variable=self._autostart).pack()

        if gpu_panel is not None:
            ui.section(general, "GPU-stöd")
            _ActionPanel(general, self.win, gpu_panel)
        # Flow-läge (global standard). Var:n skapas alltid så att befintlig
        # config bevaras vid spara; kryssrutan visas när poleringen är
        # nedladdad eller när flaggan redan är på (så en kvarglömd flagga går
        # att stänga av), annars en hjälptext i stället för en död kryssruta.
        self._flow = tk.BooleanVar(value=bool(cfg["flow"]))
        if polish_panel is not None:
            ui.section(general, "Polering (AI)")
            self._hint(
                general,
                "Markera text var som helst, tryck tangenten, få den omskriven "
                "lokalt. Uppgift per app ställs in under Ordlista.",
            )
            _ActionPanel(general, self.win, polish_panel)
            # Kryssrutan visas när poleringen är nedladdad ELLER när flaggan
            # redan är på: annars går en kvarglömd flow-flagga (efter t.ex.
            # "Ta bort poleringen") inte att stänga av härifrån.
            if polish_ready or bool(cfg["flow"]):
                holder = self._row(
                    general,
                    "Flow-läge (alla appar)",
                    hint="Polera dikteringen automatiskt innan den skrivs in",
                )
                ttk.Checkbutton(holder, variable=self._flow).pack()
                if not polish_ready:
                    self._hint(
                        general,
                        "Poleringen är inte nedladdad – flow-läget gör inget "
                        "(dikteringen skrivs in som vanligt) tills du laddar "
                        "ner den ovan.",
                    )
            else:
                self._hint(
                    general,
                    "Flow-läge (automatisk polering av dikteringen) blir "
                    "tillgängligt när poleringen laddats ner ovan.",
                )

        # ---- Ordlista -------------------------------------------------------

        ui.section(wordlist, "Global ordlista")
        self._hint(
            wordlist, "Namn och facktermer som ska stavas rätt, t.ex. \"Avanza, Smask\"."
        )
        self._prompt = ui.themed_text(wordlist, width=54, height=3, wrap="word")
        self._prompt.pack(fill="x", pady=(4, 2))
        self._prompt.insert("1.0", cfg["initial_prompt"])

        ui.section(wordlist, "Per app")
        self._hint(
            wordlist, "Extra ordlista och inskrivningssätt när en viss app har fokus."
        )
        picker = tk.Frame(wordlist, bg=pal["window"])
        picker.pack(fill="x", pady=(4, 0))
        self._app_list = ui.themed_listbox(picker, height=4, exportselection=False)
        self._app_list.pack(side="left", fill="both", expand=True)
        self._app_list.bind("<<ListboxSelect>>", self._on_profile_select)
        buttons = tk.Frame(picker, bg=pal["window"])
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Ny…", width=12, command=self._new_profile).pack(
            pady=2
        )
        ttk.Button(
            buttons, text="Ta bort", width=12, command=self._remove_profile
        ).pack(pady=2)

        self._profile_app = tk.StringVar()
        holder = self._row(wordlist, "App (processnamn)")
        self._profile_app_box = ttk.Combobox(
            holder, textvariable=self._profile_app, width=_FIELD_W - 2
        )
        self._profile_app_box.pack()
        holder = self._row(wordlist, "Ordlista för appen")
        self._profile_prompt = ui.themed_text(holder, width=32, height=2, wrap="word")
        self._profile_prompt.pack()
        reverse_inject = {v: k for k, v in _INJECT_CHOICES.items()}
        self._profile_inject = tk.StringVar(value=list(_INJECT_CHOICES)[0])
        holder = self._row(wordlist, "Inskrivning")
        ttk.Combobox(
            holder, textvariable=self._profile_inject,
            values=list(_INJECT_CHOICES), state="readonly", width=_FIELD_W - 2,
        ).pack()
        self._profile_lang = tk.StringVar(value=list(_PROFILE_LANG)[0])
        holder = self._row(wordlist, "Språk i appen", hint="Byts aldrig automatiskt")
        ttk.Combobox(
            holder, textvariable=self._profile_lang,
            values=list(_PROFILE_LANG), state="readonly", width=_FIELD_W - 2,
        ).pack()
        holder = self._row(
            wordlist, "Poleringsprompt", hint="T.ex. \"omforma till artigt mejl\""
        )
        self._profile_polish = ui.themed_text(holder, width=32, height=2, wrap="word")
        self._profile_polish.pack()
        # Flow-läge per app. Var:n skapas alltid (och laddas/sparas) så att en
        # befintlig profil behåller sitt flow-val även när kryssrutan är dold.
        self._profile_flow = tk.BooleanVar(value=False)
        # Visa kryssrutan när poleringen är nedladdad ELLER när någon profil
        # redan har flow på: annars går en kvarglömd flow-flagga i en profil
        # (efter t.ex. "Ta bort poleringen") inte att stänga av härifrån.
        any_profile_flow = any(p.get("flow") for p in self._profiles.values())
        if polish_ready or any_profile_flow:
            holder = self._row(
                wordlist, "Flow-läge", hint="Polera dikteringen automatiskt"
            )
            ttk.Checkbutton(holder, variable=self._profile_flow).pack()
            if not polish_ready:
                self._hint(
                    wordlist,
                    "Poleringen är inte nedladdad – flow-läget gör inget "
                    "tills den laddats ner.",
                )
        else:
            self._hint(
                wordlist,
                "Flow-läge (automatisk polering) blir tillgängligt när "
                "poleringen laddats ner.",
            )
        ttk.Button(
            wordlist, text="Spara profil", command=self._save_profile
        ).pack(anchor="e", pady=(6, 0))
        self._reverse_inject = reverse_inject
        self._refresh_profile_list()

        ui.section(wordlist, "Röstkommandon")
        self._hint(
            wordlist,
            "Säg \"ny rad\" eller \"nytt stycke\" så skrivs radbrytning i stället.\n"
            "Fraserna redigeras under voice_commands i config-filen.",
        )

        ui.section(wordlist, "Poleringsprompt (global)")
        self._hint(
            wordlist,
            "Uppgift när markerad text poleras. Tom = \"skriv om till korrekt, "
            "naturlig text\". Per app: fältet i profilredigeraren ovan.",
        )
        self._polish_prompt = ui.themed_text(wordlist, width=54, height=2, wrap="word")
        self._polish_prompt.pack(fill="x", pady=(4, 2))
        self._polish_prompt.insert("1.0", cfg["polish_prompt"])

        # ---- Avancerat ------------------------------------------------------

        self._adv: dict[str, tk.Variable] = {}

        def adv_entry(label, key, value):
            var = tk.StringVar(value="" if value is None else str(value))
            holder = self._row(advanced, label)
            ttk.Entry(holder, textvariable=var, width=_FIELD_W).pack()
            self._adv[key] = var

        def adv_combo(label, key, value, choices):
            var = tk.StringVar(value="auto" if value is None else str(value))
            holder = self._row(advanced, label)
            ttk.Combobox(
                holder, textvariable=var, values=choices, width=_FIELD_W - 2
            ).pack()
            self._adv[key] = var

        adv_entry("Modell svenska (HF-id)", "model", cfg["model"])
        adv_entry("Modell engelska", "model_en", cfg["model_en"])
        adv_combo("Beräkningsenhet", "device", cfg["device"], ["auto", "cuda", "cpu"])
        adv_combo(
            "Precision", "compute_type", cfg["compute_type"],
            ["auto", "float16", "int8_float16", "int8"],
        )
        adv_entry("Beam size (tomt = auto)", "beam_size", cfg["beam_size"])
        reverse_all_inject = {v: k for k, v in _INJECT_CHOICES.items()}
        self._inject = tk.StringVar(
            value=reverse_all_inject.get(cfg["inject_mode"], list(_INJECT_CHOICES)[0])
        )
        holder = self._row(advanced, "Inskrivning (standard)")
        ttk.Combobox(
            holder, textvariable=self._inject, values=list(_INJECT_CHOICES),
            state="readonly", width=_FIELD_W - 2,
        ).pack()
        adv_entry("Max inspelning (s)", "max_recording_s", cfg["max_recording_s"])
        adv_entry("Hålltröskel (s)", "hold_threshold_s", cfg["hold_threshold_s"])
        adv_entry("Poleringstangent", "polish_hotkey", cfg["polish_hotkey"])
        adv_combo(
            "Poleringsbackend", "polish_backend", cfg["polish_backend"],
            ["builtin", "ollama"],
        )
        adv_entry("Ollama-modell", "polish_ollama_model", cfg["polish_ollama_model"])
        self._restore = tk.BooleanVar(value=bool(cfg["restore_clipboard"]))
        self._append = tk.BooleanVar(value=bool(cfg["append_space"]))
        self._keep_mic = tk.BooleanVar(value=bool(cfg["keep_mic_open"]))
        self._updates = tk.BooleanVar(value=bool(cfg["check_updates"]))
        ui.section(advanced, "Beteende")
        for text, var in (
            ("Återställ klippbordet efter inklistring", self._restore),
            ("Mellanslag efter varje diktering", self._append),
            ("Håll mikrofonströmmen öppen (ingen klippt start)", self._keep_mic),
            ("Sök efter uppdateringar vid start", self._updates),
        ):
            ttk.Checkbutton(advanced, text=text, variable=var).pack(
                anchor="w", pady=1
            )
        files = tk.Frame(advanced, bg=pal["window"])
        files.pack(anchor="w", pady=(10, 0))
        ttk.Button(
            files, text="Öppna config-filen",
            command=lambda: os.startfile(CONFIG_PATH),
        ).pack(side="left")
        ttk.Button(
            files, text="Öppna loggen", command=lambda: os.startfile(LOG_PATH)
        ).pack(side="left", padx=(6, 0))

        # ---- Fot ------------------------------------------------------------

        footer = tk.Frame(self.win, bg=pal["window"])
        footer.pack(fill="x", padx=12, pady=(6, 12))
        self._error = tk.Label(
            footer, text="", bg=pal["window"], fg="#e24b4a",
            font=("Segoe UI", 8), anchor="w", justify="left",
        )
        self._error.pack(side="left")
        ttk.Button(footer, text="Avbryt", command=self.win.destroy).pack(side="right")
        ttk.Button(
            footer, text="Spara", command=self._save, style="Accent.TButton"
        ).pack(side="right", padx=(0, 6))

    # -- Layouthjälpare ------------------------------------------------------------

    def _tab(self, notebook: ttk.Notebook, title: str) -> tk.Frame:
        # tk.Frame med ägd bakgrund — ttk.Frame kan tappa temat och falla
        # tillbaka på klassiskt grått (det var Windows 2000-blandningen).
        frame = tk.Frame(notebook, bg=ui.palette()["window"], padx=14, pady=10)
        notebook.add(frame, text=title)
        return frame

    def _row(self, parent, label: str, hint: str | None = None) -> tk.Frame:
        """Rad med etikett till vänster; returnerar hållaren till höger som
        widgeten ska skapas i (pack med in_ renderar inte pålitligt)."""
        pal = ui.palette()
        frame = tk.Frame(parent, bg=pal["window"])
        frame.pack(fill="x", pady=5)
        left = tk.Frame(frame, bg=pal["window"])
        left.pack(side="left")
        tk.Label(
            left, text=label, bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 9), anchor="w",
        ).pack(anchor="w")
        if hint:
            tk.Label(
                left, text=hint, bg=pal["window"], fg=pal["muted"],
                font=("Segoe UI", 8), anchor="w",
            ).pack(anchor="w")
        holder = tk.Frame(frame, bg=pal["window"])
        holder.pack(side="right")
        return holder

    def _hint(self, parent, text: str) -> None:
        pal = ui.palette()
        tk.Label(
            parent, text=text, bg=pal["window"], fg=pal["muted"],
            font=("Segoe UI", 8), anchor="w", justify="left",
        ).pack(fill="x")

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
        self._profile_polish.delete("1.0", "end")
        self._profile_polish.insert("1.0", profile.get("polish_prompt", ""))
        self._profile_flow.set(bool(profile.get("flow", False)))

    def _new_profile(self) -> None:
        self._selected_app = None
        self._profile_app.set("")
        self._profile_prompt.delete("1.0", "end")
        self._profile_polish.delete("1.0", "end")
        self._profile_inject.set(list(_INJECT_CHOICES)[0])
        self._profile_lang.set(list(_PROFILE_LANG)[0])
        self._profile_flow.set(False)
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
        polish = self._profile_polish.get("1.0", "end").strip()
        if polish:
            profile["polish_prompt"] = polish
        inject = _INJECT_CHOICES[self._profile_inject.get()]
        if inject != "paste":
            profile["inject_mode"] = inject
        lang = _PROFILE_LANG[self._profile_lang.get()]
        if lang:
            profile["language"] = lang
        if self._profile_flow.get():
            profile["flow"] = True
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

    # -- Spara --------------------------------------------------------------------

    def values(self) -> dict:
        lang_reverse = {v: k for k, v in self._language_names.items()}
        changes = {
            "autostart": self._autostart.get(),  # tillstånd, inte config
            "language": lang_reverse.get(self._language.get(), "sv"),
            "hotkey": self._hotkey.get().strip(),
            "live": _LIVE_CHOICES[self._live.get()],
            "sounds": self._sounds.get(),
            "initial_prompt": self._prompt.get("1.0", "end").strip(),
            "polish_prompt": self._polish_prompt.get("1.0", "end").strip(),
            "flow": self._flow.get(),
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
        changes["polish_hotkey"] = self._adv["polish_hotkey"].get().strip() or "f8"
        backend = self._adv["polish_backend"].get().strip()
        changes["polish_backend"] = backend if backend in ("builtin", "ollama") else "builtin"
        changes["polish_ollama_model"] = (
            self._adv["polish_ollama_model"].get().strip() or "qwen3.5:9b"
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
            parse_hotkey(changes["polish_hotkey"])
        except HotkeyError as e:
            self._error.config(text=str(e))
            return
        if changes["hotkey"] == changes["polish_hotkey"]:
            self._error.config(
                text="Diktering och polering kan inte ha samma tangent."
            )
            return
        error = self._on_save(changes)
        if error:
            self._error.config(text=error)
            return
        self.win.destroy()
