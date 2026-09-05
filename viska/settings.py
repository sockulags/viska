"""Inställningsfönster med flikar: Allmänt (klarspråk för vardagsbruk),
Ordlista (global + per app), Polering (poleringsmotor, global standard,
mallbibliotek och flow) och Avancerat (råa tekniska värden).

Skapas och används enbart i huvudtråden — trayn begär öppning via App:s
UI-kö. Alla färger går via viska.ui: inget får ärva klassiska
tk-standardfärger (det var så Windows 2000-blandningen uppstod).
"""

import os
import queue
import tkinter as tk
from tkinter import ttk

from viska import autostart, focus, llm, metrics, ui
from viska.config import CONFIG_PATH, LOG_PATH
from viska.hotkey import HotkeyError, is_mouse, parse_hotkey

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
# Mikrofonläge: "Vid diktering" är förvalet — mikrofonikonen i aktivitetsfältet
# ska bara lysa när Viska faktiskt lyssnar.
_MIC_CHOICES = {
    "Vid diktering": "on_demand",
    "Alltid på": "always",
    "Stäng direkt": "closed",
}
# Musknappar som går att binda dikteringen till. Vänster och höger saknas
# medvetet — de behövs för att kunna använda datorn.
_MOUSE_BUTTONS = {
    "Mus 4 (tumknapp bakåt)": "mouse4",
    "Mus 5 (tumknapp framåt)": "mouse5",
    "Hjulklick": "mouse3",
}
_INJECT_CHOICES = {"Klistra in (standard)": "paste", "Skriv tecken för tecken": "type"}
_PROFILE_LANG = {"Samma som aktivt": None, "Svenska": "sv", "Engelska": "en"}

# Poleringsväljarnas specialval (utöver mallnamnen i biblioteket). En app-profil
# kan ärva den globala standarden; den globala standarden själv kan inte det, så
# "Global standard" finns bara i per-app-väljaren.
_GLOBAL_STANDARD = "Global standard"
_OWN_TEXT = "Egen text"

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
        # Processnamn i listboxens ordning: listan visar visningsnamn (riktiga
        # programnamn) men config nycklas på processnamn, så markeringens index
        # slås upp här i stället för via listboxens text.
        self._app_order: list[str] = []
        self._capturing = False

        self.win = tk.Toplevel(root)
        self.win.title("Viska – inställningar")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self._pal = ui.setup_window(self.win)
        pal = self._pal

        # Arbetskopia av mallbiblioteket, rensad från trasiga poster. Både den
        # globala väljaren och per-app-väljaren läser namnen härifrån, och
        # bibliotekseditorn nedan muterar listan — allt sparas via values().
        self._templates = [dict(t) for t in llm.polish_templates(cfg)]
        self._selected_template: str | None = None
        # Skapas här (delas av Polering- och Ordlista-flikarna) i stället för i
        # en flik, så per-app-editorns ärv-logik alltid har den globala flaggan.
        self._flow = tk.BooleanVar(value=bool(cfg["flow"]))

        notebook = ttk.Notebook(self.win)
        notebook.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        # Ordningen på add-anropen sätter flikordningen: Polering ligger mellan
        # Ordlista och Avancerat.
        general = self._tab(notebook, " Allmänt ")
        wordlist = self._tab(notebook, " Ordlista ")
        polishing = self._tab(notebook, " Polering ")
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
            general,
            "Dikteringstangent",
            hint="Klicka och tryck på tangenten, eller välj en musknapp",
        )
        self._hotkey_btn = ttk.Button(
            holder, text=cfg["hotkey"].upper(), width=20, command=self._capture_hotkey
        )
        self._hotkey_btn.pack(side="left")
        ttk.Button(holder, text="Mus…", width=6, command=self._mouse_menu).pack(
            side="left", padx=(4, 0)
        )

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

        reverse_mic = {v: k for k, v in _MIC_CHOICES.items()}
        self._mic_mode = tk.StringVar(
            value=reverse_mic.get(cfg["mic_mode"], "Vid diktering")
        )
        holder = self._row(
            general, "Mikrofon", hint="När mikrofonikonen lyser i aktivitetsfältet"
        )
        ui.Segmented(holder, list(_MIC_CHOICES), self._mic_mode).frame.pack()

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

        # ---- Polering -------------------------------------------------------
        # Allt poleringsrelaterat samlat: motorstatus/nedladdning, global
        # standard, mallbibliotek, globalt flow och en översikt.
        self._build_polishing_tab(polishing, cfg, polish_panel, polish_ready)

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
        # Poleringsuppgift för appen: Global standard (ärv), en mall ur
        # biblioteket eller Egen text (fritext, dagens polish_prompt). Combobox
        # + textfält som bara visas för Egen text. Samma bibliotek som den
        # globala väljaren i Polering-fliken.
        polish_box = tk.Frame(wordlist, bg=pal["window"])
        polish_box.pack(fill="x")
        self._profile_polish_choice = tk.StringVar(value=_GLOBAL_STANDARD)
        holder = self._row(
            polish_box, "Polering", hint="Uppgift när appens text poleras"
        )
        self._profile_polish_combo = ttk.Combobox(
            holder,
            textvariable=self._profile_polish_choice,
            values=[_GLOBAL_STANDARD] + self._template_names() + [_OWN_TEXT],
            state="readonly",
            width=_FIELD_W - 2,
        )
        self._profile_polish_combo.pack()
        self._profile_polish_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._toggle_profile_polish_text()
        )
        self._profile_polish = ui.themed_text(
            polish_box, width=54, height=2, wrap="word"
        )
        # Packas först när Egen text väljs (_toggle_profile_polish_text).
        # Flow-läge per app. Var:n skapas alltid (och laddas/sparas) så att en
        # befintlig profil behåller sitt flow-val även när kryssrutan är dold.
        # Startvärdet är den globala standarden: en profil utan eget val ärver
        # den och sparas utan egen flow-nyckel (se _save_profile).
        self._profile_flow = tk.BooleanVar(value=bool(cfg["flow"]))
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

        ui.section(wordlist, "Ersättningsregler")
        self._hint(
            wordlist,
            "Ord Whisper hör fel skrivs alltid rätt. Fylls enklast på via\n"
            "\"Rätta senaste dikteringen…\" i traymenyn.",
        )
        stored_replacements = cfg["replacements"]
        self._replacements = (
            {
                k: v
                for k, v in stored_replacements.items()
                if isinstance(k, str) and isinstance(v, str)
            }
            if isinstance(stored_replacements, dict)
            else {}
        )
        self._repl_order: list[str] = []
        repl_box = tk.Frame(wordlist, bg=pal["window"])
        repl_box.pack(fill="x", pady=(4, 0))
        self._repl_list = ui.themed_listbox(repl_box, height=4, exportselection=False)
        self._repl_list.pack(side="left", fill="both", expand=True)
        repl_buttons = tk.Frame(repl_box, bg=pal["window"])
        repl_buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(
            repl_buttons, text="Ta bort", width=12, command=self._remove_replacement
        ).pack(pady=2)
        repl_add = tk.Frame(wordlist, bg=pal["window"])
        repl_add.pack(fill="x", pady=(6, 0))
        self._repl_heard = tk.StringVar()
        self._repl_write = tk.StringVar()
        tk.Label(
            repl_add, text="Hör", bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(repl_add, textvariable=self._repl_heard, width=16).pack(
            side="left", padx=(6, 6)
        )
        tk.Label(
            repl_add, text="skriv", bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(repl_add, textvariable=self._repl_write, width=16).pack(
            side="left", padx=(6, 6)
        )
        ttk.Button(repl_add, text="Lägg till", command=self._add_replacement).pack(
            side="left"
        )
        self._refresh_replacements()

        ui.section(wordlist, "Röstkommandon")
        self._hint(
            wordlist,
            "Säg \"ny rad\" eller \"nytt stycke\" så skrivs radbrytning i stället.\n"
            "Fraserna redigeras under voice_commands i config-filen.",
        )

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
        adv_entry(
            "Stäng mikrofonen efter (s)", "mic_idle_close_s", cfg["mic_idle_close_s"]
        )
        adv_combo(
            "Poleringsbackend", "polish_backend", cfg["polish_backend"],
            ["builtin", "ollama", "http"],
        )
        adv_entry("Ollama-modell", "polish_ollama_model", cfg["polish_ollama_model"])
        adv_entry("Poleringstjänstens adress", "polish_http_url", cfg["polish_http_url"])
        adv_entry("Modell i tjänsten", "polish_http_model", cfg["polish_http_model"])
        self._http_token = tk.StringVar(value=str(cfg.get("polish_http_token", "") or ""))
        holder = self._row(
            advanced, "Nyckel till tjänsten", hint="Tom = ingen Authorization-header"
        )
        ttk.Entry(
            holder, textvariable=self._http_token, width=_FIELD_W, show="•"
        ).pack()
        self._hint(
            advanced,
            "Backend \"http\" skickar texten till adressen ovan — den lämnar\n"
            "alltså datorn. Avsedd för maskiner utan lokal poleringsmodell.",
        )
        self._restore = tk.BooleanVar(value=bool(cfg["restore_clipboard"]))
        self._append = tk.BooleanVar(value=bool(cfg["append_space"]))
        self._updates = tk.BooleanVar(value=bool(cfg["check_updates"]))
        self._history_persist = tk.BooleanVar(value=bool(cfg["history_persist"]))
        ui.section(advanced, "Beteende")
        for text, var in (
            ("Återställ klippbordet efter inklistring", self._restore),
            ("Mellanslag efter varje diktering", self._append),
            ("Sök efter uppdateringar vid start", self._updates),
            ("Spara historik på disk (annars bara i minnet)", self._history_persist),
        ):
            ttk.Checkbutton(advanced, text=text, variable=var).pack(
                anchor="w", pady=1
            )
        # GitHub-token för den inbyggda uppdateraren mot ett privat repo.
        # Lösenordsmaskerad; tom som standard (anonymt, funkar för publika repon).
        self._github_token = tk.StringVar(value=str(cfg.get("github_token", "") or ""))
        holder = self._row(
            advanced,
            "GitHub-token",
            hint="Krävs för inbyggd uppdaterare mot privat repo",
        )
        ttk.Entry(
            holder, textvariable=self._github_token, width=_FIELD_W, show="•"
        ).pack()
        ui.section(advanced, "Prestanda")
        self._hint(
            advanced,
            metrics.summary()
            or "Inga dikteringar mätta än – siffrorna dyker upp efter första.",
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

    def _mouse_menu(self) -> None:
        """Musknappar går inte att fånga i tkinter på ett tillförlitligt sätt
        (Windows-Tk levererar inte tumknapparna), så de väljs ur en lista."""
        self._cancel_capture()
        menu = tk.Menu(self.win, tearoff=0)
        for label, key in _MOUSE_BUTTONS.items():
            menu.add_command(
                label=label, command=lambda k=key: self._set_hotkey(k)
            )
        try:
            menu.tk_popup(
                self._hotkey_btn.winfo_rootx(),
                self._hotkey_btn.winfo_rooty() + self._hotkey_btn.winfo_height(),
            )
        finally:
            menu.grab_release()

    def _set_hotkey(self, key: str) -> None:
        self._hotkey.set(key)
        self._hotkey_btn.config(text=key.upper())

    # -- Ersättningsregler ---------------------------------------------------------

    def _refresh_replacements(self) -> None:
        self._repl_list.delete(0, "end")
        self._repl_order = sorted(self._replacements, key=str.lower)
        for heard in self._repl_order:
            self._repl_list.insert("end", f"{heard}  →  {self._replacements[heard]}")

    def _add_replacement(self) -> None:
        heard = self._repl_heard.get().strip()
        write = self._repl_write.get().strip()
        if not heard or not write:
            self._error.config(text="Fyll i både hörd fras och text att skriva.")
            return
        self._replacements[heard] = write
        self._repl_heard.set("")
        self._repl_write.set("")
        self._error.config(text="")
        self._refresh_replacements()

    def _remove_replacement(self) -> None:
        selection = self._repl_list.curselection()
        if not selection or selection[0] >= len(self._repl_order):
            return
        self._replacements.pop(self._repl_order[selection[0]], None)
        self._refresh_replacements()

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
        focus.running_apps()  # värm namncachen så listan visar programnamn
        self._app_list.delete(0, "end")
        self._app_order = sorted(self._profiles)
        for app in self._app_order:
            self._app_list.insert("end", focus.display_name(app))
        self._refresh_polish_overview()

    def _on_profile_select(self, _event) -> None:
        selection = self._app_list.curselection()
        if not selection or selection[0] >= len(self._app_order):
            return
        app = self._app_order[selection[0]]
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
        # Poleringsval: mall (om den finns kvar), Egen text (fritext) eller
        # Global standard (ärv). En pekare till en raderad mall visas som
        # Global standard — samma effektiva beteende som upplösningen ger.
        tmpl = (profile.get("polish_template") or "").strip()
        self._profile_polish.delete("1.0", "end")
        if tmpl and tmpl in self._template_names():
            self._profile_polish_choice.set(tmpl)
        elif profile.get("polish_prompt"):
            self._profile_polish_choice.set(_OWN_TEXT)
            self._profile_polish.insert("1.0", profile["polish_prompt"])
        else:
            self._profile_polish_choice.set(_GLOBAL_STANDARD)
        self._toggle_profile_polish_text()
        # Visa det effektiva läget: en profil utan eget flow-val ärver den
        # globala standarden (samma väg som _on_start väljer). Då speglar
        # kryssrutan vad som faktiskt händer, och att bocka bort den blir ett
        # äkta "av"-override i stället för bara ett borttaget "på".
        self._profile_flow.set(bool(profile.get("flow", self._flow.get())))

    def _new_profile(self) -> None:
        # Öppna väljaren för körande program (riktiga namn) + fritext. Avbryts
        # den rörs editorn inte alls; annars fyller det valda processnamnet i
        # en tom profileditor.
        proc = self._choose_running_app()
        if proc is None:
            return
        self._selected_app = None
        self._app_list.selection_clear(0, "end")
        self._profile_app.set(proc)
        self._profile_prompt.delete("1.0", "end")
        self._profile_polish.delete("1.0", "end")
        self._profile_polish_choice.set(_GLOBAL_STANDARD)
        self._toggle_profile_polish_text()
        self._profile_inject.set(list(_INJECT_CHOICES)[0])
        self._profile_lang.set(list(_PROFILE_LANG)[0])
        # Ny profil ärver den globala standarden tills användaren avviker.
        self._profile_flow.set(bool(self._flow.get()))
        self._profile_app_box.config(values=focus.visible_apps())
        self._profile_prompt.focus_set()

    def _choose_running_app(self) -> str | None:
        """Modal väljare för en ny app-profil: körande program med synliga
        fönster (riktiga namn) plus ett fritextfält för manuellt processnamn.
        Returnerar valt processnamn (gemener) eller None om väljaren avbröts.
        Följer fönstermönstret här (ui.setup_window, temafärger) och körs i
        huvudtråden — öppnas från Ny…-knappens command."""
        pal = self._pal
        apps = focus.running_apps()  # [(processnamn, visningsnamn)], värmer cachen
        dlg = tk.Toplevel(self.win)
        dlg.title("Välj app")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        ui.setup_window(dlg)
        dlg.transient(self.win)
        body = tk.Frame(dlg, bg=pal["window"], padx=14, pady=12)
        body.pack(fill="both", expand=True)
        self._hint(
            body,
            "Välj ett program som körs, eller skriv processnamnet själv "
            "(t.ex. code.exe).",
        )
        listbox = ui.themed_listbox(body, height=8, exportselection=False)
        listbox.pack(fill="both", expand=True, pady=(6, 6))
        for _proc, display in apps:
            listbox.insert("end", display)

        manual = tk.StringVar()
        holder = self._row(body, "Processnamn")
        ttk.Entry(holder, textvariable=manual, width=_FIELD_W).pack()

        result: dict[str, str | None] = {"proc": None}

        def use(proc: str) -> None:
            result["proc"] = proc
            dlg.destroy()

        def confirm() -> None:
            # Fritextfältet vinner: det fylls med processnamnet vid markering
            # men kan skrivas över för hand.
            typed = manual.get().strip().lower()
            if typed:
                use(typed)
                return
            sel = listbox.curselection()
            if sel and sel[0] < len(apps):
                use(apps[sel[0]][0])

        def on_select(_e) -> None:
            sel = listbox.curselection()
            if sel and sel[0] < len(apps):
                manual.set(apps[sel[0]][0])

        listbox.bind("<<ListboxSelect>>", on_select)
        listbox.bind("<Double-Button-1>", lambda _e: confirm())

        footer = tk.Frame(body, bg=pal["window"])
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="Avbryt", command=dlg.destroy).pack(side="right")
        ttk.Button(
            footer, text="Välj", command=confirm, style="Accent.TButton"
        ).pack(side="right", padx=(0, 6))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.bind("<Return>", lambda _e: confirm())

        dlg.update_idletasks()
        w = dlg.winfo_reqwidth()
        h = dlg.winfo_reqheight()
        px, py = self.win.winfo_rootx(), self.win.winfo_rooty()
        pw, ph = self.win.winfo_width(), self.win.winfo_height()
        dlg.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")
        dlg.grab_set()
        listbox.focus_set()
        self.win.wait_window(dlg)
        return result["proc"]

    def _save_profile(self) -> None:
        app = self._profile_app.get().strip().lower()
        if not app:
            self._error.config(text="Ange appens processnamn (t.ex. code.exe)")
            return
        profile = {}
        prompt = self._profile_prompt.get("1.0", "end").strip()
        if prompt:
            profile["initial_prompt"] = prompt
        # Poleringsval: skriv exakt en nyckel (eller ingen). Global standard =
        # ingen nyckel (ärver global, bakåtkompatibelt). Egen text = fritext i
        # polish_prompt. En mall = polish_template. Tom Egen text faller till
        # Global standard.
        choice = self._profile_polish_choice.get()
        if choice == _OWN_TEXT:
            polish = self._profile_polish.get("1.0", "end").strip()
            if polish:
                profile["polish_prompt"] = polish
        elif choice != _GLOBAL_STANDARD:
            profile["polish_template"] = choice
        inject = _INJECT_CHOICES[self._profile_inject.get()]
        if inject != "paste":
            profile["inject_mode"] = inject
        lang = _PROFILE_LANG[self._profile_lang.get()]
        if lang:
            profile["language"] = lang
        # Flow: skriv ett explicit override bara när profilen avviker från den
        # globala standarden – matchar den standarden utelämnas nyckeln så
        # profilen fortsätter ärva den (åt båda hållen). Utan avvikelsetestet
        # gick ett "av"-override inte att uttrycka: en avbockad ruta skrev
        # ingenting och profilen ärvde ett globalt "på" ändå, och en handskriven
        # flow: false föll tyst bort när profilen sparades om.
        if self._profile_flow.get() != bool(self._flow.get()):
            profile["flow"] = self._profile_flow.get()
        if self._selected_app and self._selected_app != app:
            self._profiles.pop(self._selected_app, None)
        self._profiles[app] = profile
        self._selected_app = app
        self._error.config(text="")
        self._refresh_profile_list()

    def _remove_profile(self) -> None:
        selection = self._app_list.curselection()
        if selection and selection[0] < len(self._app_order):
            self._profiles.pop(self._app_order[selection[0]], None)
            self._refresh_profile_list()

    # -- Polering-fliken -----------------------------------------------------------

    def _template_names(self) -> list[str]:
        return [t["namn"] for t in self._templates]

    def _build_polishing_tab(self, parent, cfg, polish_panel, polish_ready) -> None:
        """Samlar allt poleringsrelaterat: motorstatus/nedladdning, global
        standard (mall/Egen text), globalt flow, mallbiblioteket och en
        översikt över appar med egen polering."""
        pal = self._pal

        # -- Motor: status, nedladdning, "Ta bort poleringen" ------------------
        if polish_panel is not None:
            ui.section(parent, "Poleringsmotor")
            self._hint(
                parent,
                "Markera text var som helst, tryck tangenten, få den omskriven "
                "lokalt. Toner för en enskild polering väljs med dubbeltryck.",
            )
            _ActionPanel(parent, self.win, polish_panel)

        # -- Global standard: mall eller Egen text -----------------------------
        ui.section(parent, "Global standard")
        self._hint(
            parent,
            "Poleringsuppgiften som gäller överallt utom där en app har en egen "
            "(under Ordlista). Välj en mall ur biblioteket eller Egen text.",
        )
        names = self._template_names()
        stored = (cfg.get("polish_template") or "").strip()
        initial = stored if stored in names else _OWN_TEXT
        self._global_polish_choice = tk.StringVar(value=initial)
        gbox = tk.Frame(parent, bg=pal["window"])
        gbox.pack(fill="x", pady=(2, 0))
        self._global_polish_combo = ttk.Combobox(
            gbox,
            textvariable=self._global_polish_choice,
            values=[_OWN_TEXT] + names,
            state="readonly",
            width=_FIELD_W - 2,
        )
        self._global_polish_combo.pack(anchor="w")
        self._global_polish_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._toggle_global_polish_text()
        )
        self._polish_prompt = ui.themed_text(gbox, width=54, height=2, wrap="word")
        self._polish_prompt.insert("1.0", cfg["polish_prompt"])
        self._toggle_global_polish_text()  # visar textfältet bara för Egen text

        # -- Flow-läge (global) -----------------------------------------------
        # Kryssrutan visas när poleringen är nedladdad ELLER när flaggan redan
        # är på (så en kvarglömd flagga går att stänga av), annars en hjälptext.
        ui.section(parent, "Flow-läge")
        if polish_ready or bool(cfg["flow"]):
            holder = self._row(
                parent,
                "Flow-läge (alla appar)",
                hint="Polera dikteringen automatiskt innan den skrivs in",
            )
            ttk.Checkbutton(holder, variable=self._flow).pack()
            if not polish_ready:
                self._hint(
                    parent,
                    "Poleringen är inte nedladdad – flow-läget gör inget "
                    "(dikteringen skrivs in som vanligt) tills du laddar ner "
                    "den ovan.",
                )
        else:
            self._hint(
                parent,
                "Flow-läge (automatisk polering av dikteringen) blir "
                "tillgängligt när poleringen laddats ner ovan.",
            )

        # -- Mallbibliotek -----------------------------------------------------
        ui.section(parent, "Mallbibliotek")
        self._hint(
            parent,
            "Mallar som global standard och app-profiler pekar på. Ändras en "
            "mall slår det igenom överallt där den används.",
        )
        picker = tk.Frame(parent, bg=pal["window"])
        picker.pack(fill="x", pady=(4, 0))
        self._template_list = ui.themed_listbox(
            picker, height=5, exportselection=False
        )
        self._template_list.pack(side="left", fill="both", expand=True)
        self._template_list.bind("<<ListboxSelect>>", self._on_template_select)
        tbtns = tk.Frame(picker, bg=pal["window"])
        tbtns.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(tbtns, text="Ny…", width=12, command=self._new_template).pack(
            pady=2
        )
        ttk.Button(
            tbtns, text="Ta bort", width=12, command=self._remove_template
        ).pack(pady=2)
        self._template_name = tk.StringVar()
        holder = self._row(parent, "Mallens namn")
        self._template_name_entry = ttk.Entry(
            holder, textvariable=self._template_name, width=_FIELD_W
        )
        self._template_name_entry.pack()
        holder = self._row(parent, "Mallens prompt")
        self._template_prompt = ui.themed_text(
            holder, width=32, height=3, wrap="word"
        )
        self._template_prompt.pack()
        ttk.Button(
            parent, text="Spara mall", command=self._save_template
        ).pack(anchor="e", pady=(6, 0))
        self._refresh_template_list()

        # -- Översikt: appar med egen polering/flow ----------------------------
        ui.section(parent, "Appar med egen polering")
        self._polish_overview = tk.Label(
            parent, text="", bg=pal["window"], fg=pal["muted"],
            font=("Segoe UI", 8), anchor="w", justify="left",
        )
        self._polish_overview.pack(fill="x")
        self._refresh_polish_overview()

    def _toggle_global_polish_text(self) -> None:
        if self._global_polish_choice.get() == _OWN_TEXT:
            self._polish_prompt.pack(fill="x", pady=(4, 2))
        else:
            self._polish_prompt.pack_forget()

    def _toggle_profile_polish_text(self) -> None:
        if self._profile_polish_choice.get() == _OWN_TEXT:
            self._profile_polish.pack(fill="x", pady=(2, 0))
        else:
            self._profile_polish.pack_forget()

    def _refresh_polish_choices(self) -> None:
        """Uppdaterar båda poleringsväljarnas mallnamn efter en biblioteks-
        ändring och lagar val som pekar på en mall som just försvann."""
        names = self._template_names()
        self._global_polish_combo.config(values=[_OWN_TEXT] + names)
        if self._global_polish_choice.get() not in ([_OWN_TEXT] + names):
            self._global_polish_choice.set(_OWN_TEXT)
        self._toggle_global_polish_text()
        self._profile_polish_combo.config(
            values=[_GLOBAL_STANDARD] + names + [_OWN_TEXT]
        )
        if self._profile_polish_choice.get() not in (
            [_GLOBAL_STANDARD, _OWN_TEXT] + names
        ):
            self._profile_polish_choice.set(_GLOBAL_STANDARD)
        self._toggle_profile_polish_text()

    def _refresh_template_list(self) -> None:
        self._template_list.delete(0, "end")
        for name in self._template_names():
            self._template_list.insert("end", name)

    def _on_template_select(self, _event) -> None:
        selection = self._template_list.curselection()
        if not selection:
            return
        name = self._template_list.get(selection[0])
        tmpl = next((t for t in self._templates if t["namn"] == name), None)
        if tmpl is None:
            return
        self._selected_template = name
        self._template_name.set(tmpl["namn"])
        self._template_prompt.delete("1.0", "end")
        self._template_prompt.insert("1.0", tmpl.get("prompt", ""))

    def _new_template(self) -> None:
        self._selected_template = None
        self._template_name.set("")
        self._template_prompt.delete("1.0", "end")
        self._template_name_entry.focus_set()

    def _save_template(self) -> None:
        name = self._template_name.get().strip()
        if not name:
            self._error.config(text="Ge mallen ett namn.")
            return
        if name in (_GLOBAL_STANDARD, _OWN_TEXT):
            self._error.config(
                text=f"\"{name}\" är reserverat – välj ett annat namn."
            )
            return
        if name in self._template_names() and name != self._selected_template:
            self._error.config(text=f"En mall med namnet \"{name}\" finns redan.")
            return
        prompt = self._template_prompt.get("1.0", "end").strip()
        old = self._selected_template
        if old is not None:
            for t in self._templates:
                if t["namn"] == old:
                    t["namn"] = name
                    t["prompt"] = prompt
                    break
            if old != name:
                self._rename_template_refs(old, name)
        else:
            self._templates.append({"namn": name, "prompt": prompt})
        self._selected_template = name
        self._error.config(text="")
        self._refresh_template_list()
        self._refresh_polish_choices()
        self._refresh_polish_overview()

    def _remove_template(self) -> None:
        selection = self._template_list.curselection()
        if not selection:
            return
        name = self._template_list.get(selection[0])
        self._templates = [t for t in self._templates if t["namn"] != name]
        if self._selected_template == name:
            self._selected_template = None
            self._template_name.set("")
            self._template_prompt.delete("1.0", "end")
        self._refresh_template_list()
        self._refresh_polish_choices()
        self._refresh_polish_overview()

    def _rename_template_refs(self, old: str, new: str) -> None:
        """Följ med pekare vid namnbyte så de inte tyst faller tillbaka på
        global standard: den globala väljaren, öppen profil och alla sparade
        profiler som pekade på den gamla mallen."""
        if self._global_polish_choice.get() == old:
            self._global_polish_choice.set(new)
        if self._profile_polish_choice.get() == old:
            self._profile_polish_choice.set(new)
        for prof in self._profiles.values():
            if prof.get("polish_template") == old:
                prof["polish_template"] = new

    def _refresh_polish_overview(self) -> None:
        if not hasattr(self, "_polish_overview"):
            return
        names = self._template_names()
        lines = []
        for app in sorted(self._profiles):
            prof = self._profiles[app]
            parts = []
            tmpl = (prof.get("polish_template") or "").strip()
            if tmpl:
                parts.append(
                    f"mall: {tmpl}" if tmpl in names else "mall saknas → global"
                )
            elif prof.get("polish_prompt"):
                parts.append("egen text")
            if "flow" in prof:
                parts.append("flow på" if prof.get("flow") else "flow av")
            if parts:
                lines.append(f"{focus.display_name(app)} – {' · '.join(parts)}")
        self._polish_overview.config(
            text="\n".join(lines) if lines else "Inga appar har egen polering ännu."
        )

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
            # polish_prompt är den globala Egen text-fritexten; den sparas alltid
            # (även när en mall är vald) så texten finns kvar om man byter
            # tillbaka. Upplösningen använder den bara när polish_template är tom.
            "polish_prompt": self._polish_prompt.get("1.0", "end").strip(),
            "polish_templates": [dict(t) for t in self._templates],
            # Global standard pekar på en mall, eller "" = Egen text.
            "polish_template": (
                self._global_polish_choice.get()
                if self._global_polish_choice.get() in self._template_names()
                else ""
            ),
            "flow": self._flow.get(),
            "app_profiles": dict(self._profiles),
            "inject_mode": _INJECT_CHOICES[self._inject.get()],
            "restore_clipboard": self._restore.get(),
            "append_space": self._append.get(),
            "mic_mode": _MIC_CHOICES[self._mic_mode.get()],
            "replacements": dict(self._replacements),
            "check_updates": self._updates.get(),
            "history_persist": self._history_persist.get(),
            "github_token": self._github_token.get().strip(),
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
        changes["mic_idle_close_s"] = float(
            self._adv["mic_idle_close_s"].get().replace(",", ".") or 20
        )
        changes["polish_hotkey"] = self._adv["polish_hotkey"].get().strip() or "f8"
        backend = self._adv["polish_backend"].get().strip()
        changes["polish_backend"] = (
            backend if backend in ("builtin", "ollama", "http") else "builtin"
        )
        changes["polish_ollama_model"] = (
            self._adv["polish_ollama_model"].get().strip() or "qwen3.5:9b"
        )
        changes["polish_http_url"] = (
            self._adv["polish_http_url"].get().strip()
            or "http://127.0.0.1:8756/v1/chat/completions"
        )
        changes["polish_http_model"] = (
            self._adv["polish_http_model"].get().strip() or "gpt-5.4-mini"
        )
        changes["polish_http_token"] = self._http_token.get().strip()
        return changes

    def _save(self) -> None:
        # Har användaren fyllt i profil- eller mallredigeraren utan att klicka
        # "Spara" i respektive ruta ska det inte tyst kastas bort.
        if self._profile_app.get().strip():
            self._save_profile()
        if self._template_name.get().strip():
            self._save_template()
        if self._repl_heard.get().strip() and self._repl_write.get().strip():
            self._add_replacement()
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
        if is_mouse(changes["polish_hotkey"]):
            self._error.config(
                text="Poleringstangenten kan inte vara en musknapp."
            )
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
