"""Inställningsfönstret: en sidomeny med fem sidor.

Uppdelningen följer vad man faktiskt kommer hit för att göra:

- Diktering: mikrofon, tangenter, språk och livebeteende.
- Polering: motor, anslutning, standardstil, mallar och en testyta.
- App-profiler: hur enskilda program avviker från det globala.
- Ord och ersättningar: ordlista och rättningsregler.
- Inställningar: autostart, integritet, modeller och felsökning.

App-profilerna låg tidigare inklämda under Ordlista trots att de styr språk,
inskrivningssätt, polering och flow — nu är de en egen sida där varje fält
visar vad som ärvs och vad som är överstyrt.

Två saker att känna till:

- ETT sparande. Tidigare fanns "Spara mall" och "Spara profil" bredvid
  fönstrets "Spara", och det gick inte att se vilken som gällde vad. Nu
  skrivs redigeringarna in i arbetskopian så fort man byter markering eller
  lämnar fältet, och fotens Spara är det enda som rör config-filen. Har man
  osparade ändringar syns det i foten och man varnas innan fönstret stängs.

- Ingenting som kan ta tid får köras på GUI-tråden. Nåbarhetskoller och
  testpoleringar går i egna trådar och levererar via en kö som huvudtråden
  tömmer — en nedsläckt poleringstjänst ska inte kunna frysa fönstret.

Skapas och används enbart i huvudtråden — trayn begär öppning via App:s
UI-kö. Alla färger går via viska.ui: inget får ärva klassiska
tk-standardfärger (det var så Windows 2000-blandningen uppstod).
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from viska import autostart, codex, focus, llm, metrics, ui
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
_MIC_CHOICES = {
    "Vid diktering": "on_demand",
    "Alltid på": "always",
    "Stäng direkt": "closed",
}
_INJECT_CHOICES = {"Klistra in (standard)": "paste", "Skriv tecken för tecken": "type"}

# Musknappar som går att binda dikteringen till. Vänster och höger saknas
# medvetet — de behövs för att kunna använda datorn.
_MOUSE_BUTTONS = {
    "Mus 4 (tumknapp bakåt)": "mouse4",
    "Mus 5 (tumknapp framåt)": "mouse5",
    "Hjulklick": "mouse3",
}

# Poleringsmotorn i klarspråk. Låg den under Avancerat som "builtin/ollama/
# http" var den i praktiken osynlig för den som inte redan visste vad den
# hette — och för en dator utan GPU är den valet som avgör om poleringen
# fungerar alls.
_ENGINE_CHOICES = {
    "Inbyggd modell (kräver GPU)": "builtin",
    "Codex (använder din inloggning)": "codex",
    "Ollama på den här datorn": "ollama",
    "Egen tjänst (moln eller lokal server)": "http",
}
_CODEX_EFFORT = ("low", "medium", "high")

# Poleringsväljarnas specialval (utöver mallnamnen i biblioteket).
_GLOBAL_STANDARD = "Ärv global standard"
_OWN_TEXT = "Egen text"
_INHERIT = "Ärv global"

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

_FIELD_W = 30  # enhetlig fältbredd: lugn högerkant på alla sidor
_PAGES = (
    "Diktering",
    "Polering",
    "App-profiler",
    "Ord och ersättningar",
    "Inställningar",
)


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


class _ScrollPage:
    """En sida som kan bli längre än fönstret. Canvas + inre ram, med hjulet
    kopplat bara medan pekaren är över sidan (annars skrollar alla sidor
    samtidigt och listboxar slutar fungera)."""

    def __init__(self, parent):
        pal = ui.palette()
        self.outer = tk.Frame(parent, bg=pal["window"])
        self._canvas = tk.Canvas(
            self.outer, bg=pal["window"], highlightthickness=0, borderwidth=0
        )
        self._bar = ttk.Scrollbar(
            self.outer, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._bar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self.frame = tk.Frame(self._canvas, bg=pal["window"], padx=18, pady=12)
        self._window = self._canvas.create_window(
            (0, 0), window=self.frame, anchor="nw"
        )
        self.frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self.outer.bind("<Enter>", lambda _e: self._bind_wheel())
        self.outer.bind("<Leave>", lambda _e: self._unbind_wheel())

    def _on_frame_configure(self, _event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        needed = self.frame.winfo_reqheight() > self._canvas.winfo_height()
        if needed and not self._bar.winfo_ismapped():
            self._bar.pack(side="right", fill="y")
        elif not needed and self._bar.winfo_ismapped():
            self._bar.pack_forget()

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self) -> None:
        self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        if self.frame.winfo_reqheight() <= self._canvas.winfo_height():
            return
        self._canvas.yview_scroll(-1 * (event.delta // 120), "units")


class SettingsWindow:
    def __init__(
        self,
        root: tk.Tk,
        cfg: dict,
        on_save,
        gpu_panel: dict | None = None,
        polish_panel: dict | None = None,
        polish_ready: bool = False,
        polish_fn=None,
    ):
        """on_save(ändringar: dict) -> None | str (fel visas, fönstret kvar).
        Panelerna kommer från App._gpu_panel()/_polish_panel().
        polish_ready: True när poleringen är nedladdad/nåbar.
        polish_fn(cfg, text, style) -> text är appens egen poleringsväg,
        som testknappen använder."""
        ui.refresh_theme()
        self._on_save = on_save
        self._polish_ready = polish_ready
        self._cfg = cfg
        # polish_fn(cfg, text, style) -> text. Appens egen poleringsväg, så
        # att testknappen provar exakt det som kommer att köras och kan
        # återanvända en redan varm motor.
        self._polish_fn = polish_fn
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
        self._dirty = False
        self._closing = False
        # Sant medan fönstret själv fyller i fält (sidbyten, listval,
        # arv-etiketter). Utan flaggan räknades varje sådan uppdatering som
        # en ändring, och "Osparade ändringar" tändes av att man bara tittade.
        self._loading = False
        self._vars: list[tk.Variable] = []
        self._texts: list[tk.Text] = []
        self._remote_widgets: dict[str, dict] = {}
        self._remote_queue: queue.Queue = queue.Queue()
        # Räknare som gör att ett avbrutet testsvar aldrig kan hamna i rutan.
        self._test_token = 0

        self.win = tk.Toplevel(root)
        self.win.title("Viska – inställningar")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._pal = ui.setup_window(self.win)
        pal = self._pal
        # Storleken sätts i skärmens egen skala i stället för hårda pixlar, så
        # att fönstret följer med när Windows förstorar texten.
        unit = max(1.0, self.win.winfo_fpixels("1i") / 96)
        self.win.geometry(f"{int(940 * unit)}x{int(660 * unit)}")
        self.win.minsize(int(760 * unit), int(520 * unit))

        # Arbetskopia av mallbiblioteket, rensad från trasiga poster.
        self._templates = [dict(t) for t in llm.polish_templates(cfg)]
        self._selected_template: str | None = None
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
        # Skapas tidigt: både Polering och App-profiler läser den globala
        # flow-flaggan för att kunna visa vad en profil ärver.
        self._flow = self._var(tk.BooleanVar, value=bool(cfg["flow"]))
        self._inject = self._var(
            tk.StringVar,
            value={v: k for k, v in _INJECT_CHOICES.items()}.get(
                cfg["inject_mode"], list(_INJECT_CHOICES)[0]
            ),
        )
        self._language_names = {"sv": "Svenska", "en": "Engelska"}
        self._language = self._var(
            tk.StringVar, value=self._language_names.get(cfg["language"], "Svenska")
        )

        body = tk.Frame(self.win, bg=pal["window"])
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        self._content = tk.Frame(body, bg=pal["window"])
        self._content.pack(side="left", fill="both", expand=True)

        self._pages: dict[str, _ScrollPage] = {}
        for name in _PAGES:
            self._pages[name] = _ScrollPage(self._content)
        self._build_dictation(self._pages["Diktering"].frame, cfg)
        self._build_polishing(
            self._pages["Polering"].frame, cfg, polish_panel, polish_ready
        )
        self._build_profiles(self._pages["App-profiler"].frame, cfg)
        self._build_words(self._pages["Ord och ersättningar"].frame, cfg)
        self._build_advanced(self._pages["Inställningar"].frame, cfg, gpu_panel)

        self._build_footer()
        self._select_page(_PAGES[0])
        self._watch_changes()
        self._poll_remote()

    # -- Ram och navigering --------------------------------------------------------

    def _var(self, kind, **kwargs):
        variable = kind(**kwargs)
        self._vars.append(variable)
        return variable

    def _build_sidebar(self, parent) -> None:
        pal = self._pal
        bar = tk.Frame(parent, bg=pal["surface"], width=190)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        tk.Label(
            bar, text="VISKA", bg=pal["surface"], fg=pal["muted"],
            font=("Segoe UI", 9, "bold"), anchor="w", padx=16, pady=14,
        ).pack(fill="x")
        self._nav_items: dict[str, tk.Label] = {}
        for name in _PAGES:
            item = tk.Label(
                bar, text=name, bg=pal["surface"], fg=pal["text"],
                font=("Segoe UI", 10), anchor="w", padx=16, pady=9, cursor="hand2",
            )
            item.pack(fill="x")
            item.bind("<Button-1>", lambda _e, n=name: self._select_page(n))
            self._nav_items[name] = item

    def _select_page(self, name: str) -> None:
        pal = self._pal
        for page_name, page in self._pages.items():
            if page_name == name:
                page.outer.pack(fill="both", expand=True)
            else:
                page.outer.pack_forget()
        for item_name, item in self._nav_items.items():
            selected = item_name == name
            item.config(
                bg=pal["accent"] if selected else pal["surface"],
                fg=pal["accent_fg"] if selected else pal["text"],
                font=("Segoe UI", 10, "bold" if selected else "normal"),
            )
        self._current_page = name
        if name == "Polering":
            self._show_engine_block()
        elif name == "App-profiler":
            self._refresh_inherit_labels()

    def _build_footer(self) -> None:
        pal = self._pal
        footer = tk.Frame(self.win, bg=pal["window"])
        footer.pack(fill="x", padx=18, pady=(6, 14))
        self._error = tk.Label(
            footer, text="", bg=pal["window"], fg="#e24b4a",
            font=("Segoe UI", 8), anchor="w", justify="left", wraplength=460,
        )
        self._error.pack(side="left")
        ttk.Button(footer, text="Avbryt", command=self._on_close).pack(side="right")
        ttk.Button(
            footer, text="Spara", command=self._save, style="Accent.TButton"
        ).pack(side="right", padx=(0, 6))
        self._dirty_label = tk.Label(
            footer, text="", bg=pal["window"], fg=pal["muted"],
            font=("Segoe UI", 8), anchor="e",
        )
        self._dirty_label.pack(side="right", padx=(0, 12))

    # -- Osparade ändringar --------------------------------------------------------

    def _watch_changes(self) -> None:
        """Kopplas in EFTER att sidorna byggts, annars räknas uppbyggnaden
        själv som ändringar."""
        for variable in self._vars:
            variable.trace_add("write", lambda *_a: self._mark_dirty())
        for text in self._texts:
            text.edit_modified(False)
            text.bind("<<Modified>>", self._on_text_modified, add="+")

    def _on_text_modified(self, event) -> None:
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._mark_dirty()

    def _mark_dirty(self) -> None:
        if self._closing or self._loading:
            return
        self._dirty = True
        self._dirty_label.config(text="Osparade ändringar")

    def _on_close(self) -> None:
        if not self._dirty:
            self._closing = True
            self.win.destroy()
            return
        answer = messagebox.askyesnocancel(
            "Osparade ändringar",
            "Du har ändringar som inte är sparade. Spara dem?",
            parent=self.win,
        )
        if answer is None:
            return  # avbryt stängningen
        if answer:
            self._save()
            return
        self._closing = True
        self.win.destroy()

    # -- Sida: Diktering -----------------------------------------------------------

    def _build_dictation(self, parent, cfg) -> None:
        self._heading(parent, "Diktering", "Håll tangenten, prata, släpp.")

        holder = self._row(parent, "Språk")
        ui.Segmented(
            holder, list(self._language_names.values()), self._language
        ).frame.pack()

        self._hotkey = self._var(tk.StringVar, value=cfg["hotkey"])
        holder = self._row(
            parent,
            "Dikteringstangent",
            hint="Klicka och tryck på tangenten, eller välj en musknapp",
        )
        self._hotkey_btn = ttk.Button(
            holder, text=cfg["hotkey"].upper(), width=18, command=self._capture_hotkey
        )
        self._hotkey_btn.pack(side="left")
        ttk.Button(holder, text="Mus…", width=6, command=self._mouse_menu).pack(
            side="left", padx=(4, 0)
        )

        reverse_mic = {v: k for k, v in _MIC_CHOICES.items()}
        self._mic_mode = self._var(
            tk.StringVar, value=reverse_mic.get(cfg["mic_mode"], "Vid diktering")
        )
        holder = self._row(
            parent, "Mikrofon", hint="När mikrofonikonen lyser i aktivitetsfältet"
        )
        ui.Segmented(holder, list(_MIC_CHOICES), self._mic_mode).frame.pack()

        reverse_live = {v: k for k, v in _LIVE_CHOICES.items()}
        self._live = self._var(
            tk.StringVar, value=reverse_live.get(cfg["live"], reverse_live["auto"])
        )
        holder = self._row(
            parent,
            "Visa text medan jag talar",
            hint="Färdiga fraser skrivs in vid pauserna",
        )
        ttk.Combobox(
            holder, textvariable=self._live, values=list(_LIVE_CHOICES),
            state="readonly", width=_FIELD_W - 2,
        ).pack()

        current_feel = next(
            (
                name
                for name, (i, s) in FEELS.items()
                if abs(i - cfg["live_interval_s"]) < 0.01
                and abs(s - cfg["live_commit_silence_s"]) < 0.01
            ),
            "Egen",
        )
        self._feel = self._var(tk.StringVar, value=current_feel)
        feel_options = list(FEELS) + (["Egen"] if current_feel == "Egen" else [])
        holder = self._row(
            parent, "Livekänsla", hint="Hur snabbt fraser skrivs in vid paus"
        )
        ui.Segmented(holder, feel_options, self._feel).frame.pack()

        reverse_q = {v: k for k, v in QUALITY.items()}
        current_q = reverse_q.get(cfg["model"], "Egen (se Inställningar)")
        q_values = list(QUALITY) + (
            ["Egen (se Inställningar)"] if current_q not in QUALITY else []
        )
        self._quality = self._var(tk.StringVar, value=current_q)
        holder = self._row(parent, "Kvalitet (svenska)")
        ttk.Combobox(
            holder, textvariable=self._quality, values=q_values,
            state="readonly", width=_FIELD_W - 2,
        ).pack()

        self._sounds = self._var(tk.BooleanVar, value=bool(cfg["sounds"]))
        holder = self._row(parent, "Ljudsignaler")
        ttk.Checkbutton(holder, variable=self._sounds).pack()

        ui.section(parent, "Mikrofonen")
        self._mic_info = tk.Label(
            parent, text="", bg=self._pal["window"], fg=self._pal["muted"],
            font=("Segoe UI", 8), anchor="w", justify="left",
        )
        self._mic_info.pack(fill="x")
        ttk.Button(
            parent, text="Kontrollera mikrofonen", command=self._refresh_mic_info
        ).pack(anchor="w", pady=(4, 0))
        self._refresh_mic_info()

    def _refresh_mic_info(self) -> None:
        """Vilken mikrofon Windows skulle välja just nu. Ren avläsning i en
        egen tråd — PortAudio kan ta hundratals millisekunder på sig."""

        def work():
            try:
                import sounddevice as sd

                default = sd.query_devices(kind="input")
                text = f"Standardmikrofon: {default['name']}"
            except Exception as e:  # noqa: BLE001
                text = f"Ingen mikrofon hittades ({e})"
            self._remote_queue.put(("mic", "text", text))

        self._mic_info.config(text="Läser enheter…")
        threading.Thread(target=work, daemon=True, name="viska-micinfo").start()

    # -- Sida: Polering ------------------------------------------------------------

    def _build_polishing(self, parent, cfg, polish_panel, polish_ready) -> None:
        pal = self._pal
        self._heading(
            parent,
            "Polering",
            "Markera text och tryck poleringstangenten, eller låt hela "
            "dikteringar poleras automatiskt.",
        )

        reverse_engine = {v: k for k, v in _ENGINE_CHOICES.items()}
        self._engine = self._var(
            tk.StringVar,
            value=reverse_engine.get(cfg["polish_backend"], list(_ENGINE_CHOICES)[0]),
        )
        holder = self._row(parent, "Motor", hint="Var omskrivningen körs")
        engine_combo = ttk.Combobox(
            holder, textvariable=self._engine, values=list(_ENGINE_CHOICES),
            state="readonly", width=_FIELD_W - 2,
        )
        engine_combo.pack()
        engine_combo.bind("<<ComboboxSelected>>", lambda _e: self._show_engine_block())

        # Motorns egna fält hör ihop med väljaren och måste ligga DIREKT under
        # den. Utan en egen behållare hamnade de sist på sidan, eftersom pack()
        # lägger till i slutet och resten av sidan byggs efter det här.
        self._engine_area = tk.Frame(parent, bg=pal["window"])
        self._engine_area.pack(fill="x")
        parent = self._engine_area
        self._engine_blocks: dict[str, tk.Frame] = {}

        block = tk.Frame(parent, bg=pal["window"])
        self._engine_blocks["builtin"] = block
        if polish_panel is not None:
            _ActionPanel(block, self.win, polish_panel)

        block = tk.Frame(parent, bg=pal["window"])
        self._engine_blocks["codex"] = block
        self._codex_model = self._var(
            tk.StringVar, value=cfg.get("polish_codex_model", "gpt-5.6-luna")
        )
        holder = self._row(block, "Modell", hint="Måste finnas i din Codex-katalog")
        ttk.Entry(holder, textvariable=self._codex_model, width=_FIELD_W).pack()
        self._codex_effort = self._var(
            tk.StringVar, value=cfg.get("polish_codex_effort", "low")
        )
        holder = self._row(
            block, "Tankenivå", hint="low är snabbast och räcker för omskrivning"
        )
        ttk.Combobox(
            holder, textvariable=self._codex_effort, values=list(_CODEX_EFFORT),
            state="readonly", width=_FIELD_W - 2,
        ).pack()
        ui.status_card(
            block,
            "warn",
            "Texten lämnar datorn",
            "Omskrivningen görs av OpenAI via din Codex-inloggning, och räknas "
            "mot dess kvot. Allt annat i Viska stannar lokalt.",
        ).pack(fill="x", pady=(8, 2))
        self._build_remote_tools(block, "codex")

        block = tk.Frame(parent, bg=pal["window"])
        self._engine_blocks["ollama"] = block
        self._ollama_model = self._var(tk.StringVar, value=cfg["polish_ollama_model"])
        holder = self._row(block, "Modell i Ollama")
        ttk.Entry(holder, textvariable=self._ollama_model, width=_FIELD_W).pack()
        self._ollama_url = self._var(tk.StringVar, value=cfg["polish_ollama_url"])
        holder = self._row(block, "Ollamas adress")
        ttk.Entry(holder, textvariable=self._ollama_url, width=_FIELD_W).pack()
        self._build_remote_tools(block, "ollama")

        block = tk.Frame(parent, bg=pal["window"])
        self._engine_blocks["http"] = block
        self._http_url = self._var(tk.StringVar, value=cfg["polish_http_url"])
        holder = self._row(block, "Tjänstens adress")
        ttk.Entry(holder, textvariable=self._http_url, width=_FIELD_W).pack()
        self._http_model = self._var(tk.StringVar, value=cfg["polish_http_model"])
        holder = self._row(block, "Modell i tjänsten")
        ttk.Entry(holder, textvariable=self._http_model, width=_FIELD_W).pack()
        self._http_token = self._var(
            tk.StringVar, value=str(cfg.get("polish_http_token") or "")
        )
        holder = self._row(block, "Nyckel", hint="Tom = ingen Authorization-header")
        ttk.Entry(
            holder, textvariable=self._http_token, width=_FIELD_W, show="•"
        ).pack()
        ui.status_card(
            block,
            "warn",
            "Texten lämnar datorn",
            "Med den här motorn skickas det som ska poleras till adressen "
            "ovan. Allt annat i Viska stannar lokalt.",
        ).pack(fill="x", pady=(8, 2))
        self._build_remote_tools(block, "http")

        # -- Standardstil ------------------------------------------------------
        parent = self._engine_area.master
        ui.section(parent, "Standardstil")
        self._hint(
            parent,
            "Uppgiften som gäller överallt utom där en app har en egen "
            "(se App-profiler).",
        )
        names = self._template_names()
        stored = (cfg.get("polish_template") or "").strip()
        initial = stored if stored in names else _OWN_TEXT
        self._global_polish_choice = self._var(tk.StringVar, value=initial)
        gbox = tk.Frame(parent, bg=pal["window"])
        gbox.pack(fill="x", pady=(2, 0))
        self._global_polish_combo = ttk.Combobox(
            gbox, textvariable=self._global_polish_choice,
            values=[_OWN_TEXT] + names, state="readonly", width=_FIELD_W - 2,
        )
        self._global_polish_combo.pack(anchor="w")
        self._global_polish_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._toggle_global_polish_text()
        )
        self._polish_prompt = self._text(gbox, height=3)
        self._polish_prompt.insert("1.0", cfg["polish_prompt"])
        self._toggle_global_polish_text()  # visar textfältet bara för Egen text

        holder = self._row(
            parent,
            "Polera automatiskt",
            hint="Hela dikteringen poleras innan den skrivs in",
        )
        ttk.Checkbutton(holder, variable=self._flow).pack()
        # Ingen extra "poleringen är inte redo"-text här: motorblocket ovanför
        # vet sanningen och uppdaterar sig själv. Två källor sa emot varandra.

        # -- Mallbibliotek -----------------------------------------------------
        ui.section(parent, "Stilar")
        self._hint(
            parent,
            "Mallar som standardstilen och app-profilerna pekar på. Ändras en "
            "mall slår det igenom överallt där den används. Redigeringen "
            "sparas med fönstrets Spara.",
        )
        picker = tk.Frame(parent, bg=pal["window"])
        picker.pack(fill="x", pady=(4, 0))
        self._template_list = ui.themed_listbox(
            picker, height=6, exportselection=False
        )
        self._template_list.pack(side="left", fill="both", expand=True)
        self._template_list.bind("<<ListboxSelect>>", self._on_template_select)
        tbtns = tk.Frame(picker, bg=pal["window"])
        tbtns.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(tbtns, text="Ny stil", width=12, command=self._new_template).pack(
            pady=2
        )
        ttk.Button(
            tbtns, text="Ta bort", width=12, command=self._remove_template
        ).pack(pady=2)
        self._template_name = self._var(tk.StringVar)
        holder = self._row(parent, "Namn")
        self._template_name_entry = ttk.Entry(
            holder, textvariable=self._template_name, width=_FIELD_W
        )
        self._template_name_entry.pack()
        self._template_name_entry.bind("<FocusOut>", lambda _e: self._commit_template())
        self._hint(parent, "Uppgift till modellen")
        self._template_prompt = self._text(parent, height=6)
        self._template_prompt.bind("<FocusOut>", lambda _e: self._commit_template())
        self._refresh_template_list()

        # -- Testyta -----------------------------------------------------------
        ui.section(parent, "Testa")
        self._hint(
            parent,
            "Kör en mening genom motorn och stilen som är vald här — även om "
            "de inte är sparade än. Resultatet stannar i rutan: det skrivs "
            "inte in någonstans och rör inte klippbordet.",
        )
        self._test_sample = self._text(parent, height=3)
        self._test_sample.insert("1.0", cfg.get("polish_test_text", ""))
        test_row = tk.Frame(parent, bg=pal["window"])
        test_row.pack(fill="x", pady=(4, 0))
        self._test_button = ttk.Button(
            test_row, text="Testa polering", command=self._run_test
        )
        self._test_button.pack(side="left")
        self._test_cancel = ttk.Button(
            test_row, text="Avbryt test", command=self._cancel_test, state="disabled"
        )
        self._test_cancel.pack(side="left", padx=(6, 0))
        self._test_time = tk.Label(
            test_row, text="", bg=pal["window"], fg=pal["muted"],
            font=("Segoe UI", 8),
        )
        self._test_time.pack(side="left", padx=(10, 0))
        self._test_result = self._text(parent, height=5, editable=False)
        # Blocken packas redan här, inte först när sidan besöks: motorns status
        # ska vara känd även för den som sparar utan att gå hit.
        self._show_engine_block()

    # -- Sida: App-profiler --------------------------------------------------------

    def _build_profiles(self, parent, cfg) -> None:
        pal = self._pal
        self._heading(
            parent,
            "App-profiler",
            "Hur ett enskilt program avviker från det globala. Fält som står "
            "på \"Ärv global\" följer inställningarna på de andra sidorna.",
        )
        picker = tk.Frame(parent, bg=pal["window"])
        picker.pack(fill="x")
        self._app_list = ui.themed_listbox(picker, height=6, exportselection=False)
        self._app_list.pack(side="left", fill="both", expand=True)
        self._app_list.bind("<<ListboxSelect>>", self._on_profile_select)
        buttons = tk.Frame(picker, bg=pal["window"])
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(
            buttons, text="Ny profil", width=12, command=self._new_profile
        ).pack(pady=2)
        ttk.Button(
            buttons, text="Ta bort", width=12, command=self._remove_profile
        ).pack(pady=2)

        self._profile_app = self._var(tk.StringVar)
        holder = self._row(parent, "Program", hint="Processnamn, t.ex. code.exe")
        self._profile_app_box = ttk.Combobox(
            holder, textvariable=self._profile_app, width=_FIELD_W - 2
        )
        self._profile_app_box.pack()
        self._profile_app_box.bind("<FocusOut>", lambda _e: self._commit_profile())

        self._hint(parent, "Extra ordlista för appen")
        self._profile_prompt = self._text(parent, height=3)
        self._profile_prompt.bind("<FocusOut>", lambda _e: self._commit_profile())

        self._profile_lang = self._var(tk.StringVar, value=_INHERIT)
        holder = self._row(parent, "Språk", hint="Byts aldrig automatiskt")
        self._profile_lang_combo = ttk.Combobox(
            holder, textvariable=self._profile_lang, state="readonly",
            width=_FIELD_W - 2,
        )
        self._profile_lang_combo.pack()

        self._profile_inject = self._var(tk.StringVar, value=_INHERIT)
        holder = self._row(parent, "Inskrivning")
        self._profile_inject_combo = ttk.Combobox(
            holder, textvariable=self._profile_inject, state="readonly",
            width=_FIELD_W - 2,
        )
        self._profile_inject_combo.pack()

        self._profile_polish_choice = self._var(tk.StringVar, value=_GLOBAL_STANDARD)
        holder = self._row(parent, "Poleringsstil")
        self._profile_polish_combo = ttk.Combobox(
            holder, textvariable=self._profile_polish_choice, state="readonly",
            width=_FIELD_W - 2,
        )
        self._profile_polish_combo.pack()
        self._profile_polish_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._toggle_profile_polish_text()
        )
        self._profile_polish = self._text(parent, height=3)

        self._profile_flow = self._var(tk.StringVar, value=_INHERIT)
        holder = self._row(parent, "Polera automatiskt")
        self._profile_flow_combo = ttk.Combobox(
            holder, textvariable=self._profile_flow, state="readonly",
            width=_FIELD_W - 2,
        )
        self._profile_flow_combo.pack()

        ui.section(parent, "Översikt")
        self._polish_overview = tk.Label(
            parent, text="", bg=pal["window"], fg=pal["muted"],
            font=("Segoe UI", 8), anchor="w", justify="left",
        )
        self._polish_overview.pack(fill="x")
        self._refresh_inherit_labels()
        self._refresh_profile_list()
        self._set_profile_editor(None)

    def _refresh_inherit_labels(self) -> None:
        """Väljarna visar vad som ärvs, med det globala värdet i klartext —
        annars går det inte att se vad "ärv" faktiskt betyder just nu."""
        keep_lang = self._profile_lang.get()
        keep_inject = self._profile_inject.get()
        keep_flow = self._profile_flow.get()
        # Kort form av det globala värdet: hela etiketten hade gett
        # "Ärv global (Klistra in (standard))", parentes i parentes.
        inject_short = {
            "paste": "klistra in", "type": "tecken för tecken"
        }[_INJECT_CHOICES[self._inject.get()]]
        was_loading, self._loading = self._loading, True
        try:
            self._profile_lang_combo.config(
                values=[
                    f"{_INHERIT} ({self._language.get().lower()})",
                    "Svenska",
                    "Engelska",
                ]
            )
            self._profile_inject_combo.config(
                values=[f"{_INHERIT} ({inject_short})"] + list(_INJECT_CHOICES)
            )
            self._profile_polish_combo.config(
                values=[_GLOBAL_STANDARD] + self._template_names() + [_OWN_TEXT]
            )
            self._profile_flow_combo.config(
                values=[
                    f"{_INHERIT} ({'på' if self._flow.get() else 'av'})", "På", "Av"
                ]
            )
            # Behåll valet men följ med i den nya arv-etiketten.
            for var, keep, combo in (
                (self._profile_lang, keep_lang, self._profile_lang_combo),
                (self._profile_inject, keep_inject, self._profile_inject_combo),
                (self._profile_flow, keep_flow, self._profile_flow_combo),
            ):
                if self._inherit_value(keep):
                    var.set(combo.cget("values")[0])
                else:
                    var.set(keep)
        finally:
            self._loading = was_loading

    @staticmethod
    def _inherit_value(combo_value: str) -> bool:
        return str(combo_value).startswith(_INHERIT)

    # -- Sida: Ord och ersättningar ------------------------------------------------

    def _build_words(self, parent, cfg) -> None:
        pal = self._pal
        self._heading(
            parent, "Ord och ersättningar", "Namn och facktermer som ska bli rätt."
        )
        ui.section(parent, "Ordlista")
        self._hint(
            parent,
            "Ett tips till modellen om hur ord stavas, t.ex. \"Avanza, Smask\". "
            "Den följs inte alltid.",
        )
        self._prompt = self._text(parent, height=3)
        self._prompt.insert("1.0", cfg["initial_prompt"])

        ui.section(parent, "Ersättningsregler")
        self._hint(
            parent,
            "Körs efter transkriberingen och slår alltid igenom. Fylls enklast "
            "på via \"Rätta senaste dikteringen…\" i traymenyn.",
        )
        repl_box = tk.Frame(parent, bg=pal["window"])
        repl_box.pack(fill="x", pady=(4, 0))
        self._repl_list = ui.themed_listbox(repl_box, height=6, exportselection=False)
        self._repl_list.pack(side="left", fill="both", expand=True)
        repl_buttons = tk.Frame(repl_box, bg=pal["window"])
        repl_buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(
            repl_buttons, text="Ta bort", width=12, command=self._remove_replacement
        ).pack(pady=2)
        repl_add = tk.Frame(parent, bg=pal["window"])
        repl_add.pack(fill="x", pady=(6, 0))
        self._repl_heard = self._var(tk.StringVar)
        self._repl_write = self._var(tk.StringVar)
        tk.Label(
            repl_add, text="Hör", bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(repl_add, textvariable=self._repl_heard, width=18).pack(
            side="left", padx=(6, 6)
        )
        tk.Label(
            repl_add, text="skriv", bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(repl_add, textvariable=self._repl_write, width=18).pack(
            side="left", padx=(6, 6)
        )
        ttk.Button(repl_add, text="Lägg till", command=self._add_replacement).pack(
            side="left"
        )
        self._refresh_replacements()

        ui.section(parent, "Röstkommandon")
        self._hint(
            parent,
            "Säg \"ny rad\" eller \"nytt stycke\" så skrivs radbrytning i stället.\n"
            "Fraserna redigeras under voice_commands i config-filen.",
        )

    # -- Sida: Inställningar -------------------------------------------------------

    def _build_advanced(self, parent, cfg, gpu_panel) -> None:
        pal = self._pal
        self._heading(parent, "Inställningar", "Start, integritet och modeller.")

        # Tillståndet läses från Startup-genvägen, inte config — kan
        # aldrig hamna i osynk med verkligheten.
        self._autostart = self._var(tk.BooleanVar, value=autostart.enabled())
        holder = self._row(parent, "Starta med Windows")
        ttk.Checkbutton(holder, variable=self._autostart).pack()

        self._adv: dict[str, tk.Variable] = {}

        def adv_entry(label, key, value, hint=None):
            var = self._var(tk.StringVar, value="" if value is None else str(value))
            holder = self._row(parent, label, hint=hint)
            ttk.Entry(holder, textvariable=var, width=_FIELD_W).pack()
            self._adv[key] = var

        def adv_combo(label, key, value, choices):
            var = self._var(
                tk.StringVar, value="auto" if value is None else str(value)
            )
            holder = self._row(parent, label)
            ttk.Combobox(
                holder, textvariable=var, values=choices, width=_FIELD_W - 2
            ).pack()
            self._adv[key] = var

        ui.section(parent, "Tangenter")
        adv_entry("Poleringstangent", "polish_hotkey", cfg["polish_hotkey"])
        adv_entry(
            "Tonvalsmenyns tangent", "polish_menu_hotkey", cfg["polish_menu_hotkey"],
            hint="Tom = ingen menytangent",
        )
        self._double_tap = self._var(
            tk.BooleanVar, value=bool(cfg["polish_double_tap"])
        )
        holder = self._row(
            parent,
            "Tonvalsmeny på dubbeltryck",
            hint="Fördröjer varje polering ~0,4 s",
        )
        ttk.Checkbutton(holder, variable=self._double_tap).pack()
        adv_entry("Hålltröskel (s)", "hold_threshold_s", cfg["hold_threshold_s"])

        ui.section(parent, "Integritet och beteende")
        self._restore = self._var(tk.BooleanVar, value=bool(cfg["restore_clipboard"]))
        self._append = self._var(tk.BooleanVar, value=bool(cfg["append_space"]))
        self._updates = self._var(tk.BooleanVar, value=bool(cfg["check_updates"]))
        self._history_persist = self._var(
            tk.BooleanVar, value=bool(cfg["history_persist"])
        )
        for text, var in (
            ("Återställ klippbordet efter inklistring", self._restore),
            ("Mellanslag efter varje diktering", self._append),
            ("Sök efter uppdateringar vid start", self._updates),
            ("Spara historik på disk (annars bara i minnet)", self._history_persist),
        ):
            ttk.Checkbutton(parent, text=text, variable=var).pack(anchor="w", pady=1)
        holder = self._row(parent, "Inskrivning (standard)")
        ttk.Combobox(
            holder, textvariable=self._inject, values=list(_INJECT_CHOICES),
            state="readonly", width=_FIELD_W - 2,
        ).pack()
        adv_entry(
            "Stäng mikrofonen efter (s)", "mic_idle_close_s", cfg["mic_idle_close_s"]
        )
        adv_entry("Max inspelning (s)", "max_recording_s", cfg["max_recording_s"])

        ui.section(parent, "Modeller")
        adv_entry("Modell svenska (HF-id)", "model", cfg["model"])
        adv_entry("Modell engelska", "model_en", cfg["model_en"])
        adv_combo("Beräkningsenhet", "device", cfg["device"], ["auto", "cuda", "cpu"])
        adv_combo(
            "Precision", "compute_type", cfg["compute_type"],
            ["auto", "float16", "int8_float16", "int8"],
        )
        adv_entry("Beam size (tomt = auto)", "beam_size", cfg["beam_size"])

        if gpu_panel is not None:
            ui.section(parent, "GPU-stöd")
            _ActionPanel(parent, self.win, gpu_panel)

        ui.section(parent, "Uppdateringar")
        self._github_token = self._var(
            tk.StringVar, value=str(cfg.get("github_token", "") or "")
        )
        holder = self._row(
            parent, "GitHub-token", hint="Krävs för uppdaterare mot privat repo"
        )
        ttk.Entry(
            holder, textvariable=self._github_token, width=_FIELD_W, show="•"
        ).pack()

        ui.section(parent, "Felsökning")
        self._hint(
            parent,
            metrics.summary()
            or "Inga dikteringar mätta än – siffrorna dyker upp efter första.",
        )
        files = tk.Frame(parent, bg=pal["window"])
        files.pack(anchor="w", pady=(8, 0))
        ttk.Button(
            files, text="Öppna config-filen", command=lambda: os.startfile(CONFIG_PATH)
        ).pack(side="left")
        ttk.Button(
            files, text="Öppna loggen", command=lambda: os.startfile(LOG_PATH)
        ).pack(side="left", padx=(6, 0))

    # -- Layouthjälpare ------------------------------------------------------------

    def _heading(self, parent, title: str, body: str) -> None:
        pal = self._pal
        tk.Label(
            parent, text=title, bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 15, "bold"), anchor="w",
        ).pack(fill="x")
        tk.Label(
            parent, text=body, bg=pal["window"], fg=pal["muted"],
            font=("Segoe UI", 9), anchor="w", justify="left", wraplength=560,
        ).pack(fill="x", pady=(2, 10))

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
            font=("Segoe UI", 8), anchor="w", justify="left", wraplength=560,
        ).pack(fill="x")

    def _text(self, parent, height: int, editable: bool = True) -> tk.Text:
        """Textfält som fyller bredden — promptar behöver plats, och en
        hårdkodad teckenbredd lämnade en tom remsa till höger."""
        widget = ui.themed_text(parent, height=height, wrap="word")
        widget.pack(fill="x", pady=(4, 2))
        if editable:
            self._texts.append(widget)
        else:
            widget.config(state="disabled")
        return widget

    def _set_text(self, widget: tk.Text, value: str) -> None:
        state = str(widget.cget("state"))
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.edit_modified(False)
        widget.config(state=state)

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
            menu.add_command(label=label, command=lambda k=key: self._set_hotkey(k))
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

    # -- Poleringsmotorns block ----------------------------------------------------

    def _show_engine_block(self) -> None:
        """Visar bara den valda motorns inställningar. Blocken byggs en gång
        och packas om — att bygga om sidan vid varje byte hade tappat allt
        annat man hunnit skriva i den."""
        chosen = _ENGINE_CHOICES.get(self._engine.get(), "builtin")
        for name, block in self._engine_blocks.items():
            if name == chosen:
                block.pack(fill="x", pady=(2, 0))
            else:
                block.pack_forget()
        if chosen in ("ollama", "http", "codex"):
            self._probe_remote(chosen)

    def _build_remote_tools(self, parent, backend: str) -> None:
        """Anslutningsstatus som uppdateras utan att blockera GUI-tråden."""
        pal = self._pal
        status = tk.Label(
            parent, text="Kontrollerar anslutningen…", bg=pal["window"],
            fg=pal["muted"], font=("Segoe UI", 8), anchor="w", justify="left",
            wraplength=520,
        )
        status.pack(fill="x", pady=(6, 0))
        ttk.Button(
            parent, text="Kontrollera igen",
            command=lambda: self._probe_remote(backend),
        ).pack(anchor="w", pady=(4, 0))
        self._remote_widgets[backend] = {"status": status}

    # -- Nåbarhet och testpolering i bakgrunden ------------------------------------

    def _engine_cfg(self) -> dict:
        """Config som den skulle se ut om man sparade nu — så att statuskoll
        och test använder adressen man just skrivit in, inte den sparade."""
        return {
            **self._cfg,
            "polish_backend": _ENGINE_CHOICES.get(self._engine.get(), "builtin"),
            "polish_codex_model": self._codex_model.get().strip(),
            "polish_codex_effort": self._codex_effort.get(),
            "polish_ollama_model": self._ollama_model.get().strip(),
            "polish_ollama_url": self._ollama_url.get().strip(),
            "polish_http_url": self._http_url.get().strip(),
            "polish_http_model": self._http_model.get().strip(),
            "polish_http_token": self._http_token.get().strip(),
        }

    def _probe_remote(self, backend: str) -> None:
        widgets = self._remote_widgets.get(backend)
        if widgets is None:
            return
        widgets["status"].config(text="Kontrollerar…")
        cfg = {**self._engine_cfg(), "polish_backend": backend}

        def work():
            if backend == "codex":
                # Codex vet själv varför den inte är redo (saknas, inte
                # inloggad) — den förklaringen är mer värd än ett "svarar inte".
                self._remote_queue.put((backend, "status", codex.status()[1]))
                return
            try:
                ok = llm.remote_reachable(cfg)
            except Exception:
                ok = False
            name = llm.remote_label(cfg)
            model = llm.remote_model(cfg) or "(ingen modell vald)"
            text = (
                f"Ansluten till {name} – modell {model}"
                if ok
                else f"{name} svarar inte. Kontrollera adressen, och att "
                "tjänsten är igång."
            )
            self._remote_queue.put((backend, "status", text))

        threading.Thread(target=work, daemon=True, name="viska-probe").start()

    def _current_style(self) -> str:
        """Poleringsuppgiften som testet ska använda: den som står i editorn
        just nu, inte den som är sparad."""
        self._commit_template()
        cfg = {
            **self._engine_cfg(),
            "polish_templates": [dict(t) for t in self._templates],
            "polish_template": (
                self._global_polish_choice.get()
                if self._global_polish_choice.get() in self._template_names()
                else ""
            ),
            "polish_prompt": self._polish_prompt.get("1.0", "end").strip(),
        }
        return llm.resolve_polish_prompt(cfg)

    def _run_test(self) -> None:
        text = self._test_sample.get("1.0", "end").strip()
        if not text:
            self._set_text(self._test_result, "Skriv en mening att testa med först.")
            return
        backend = _ENGINE_CHOICES.get(self._engine.get(), "builtin")
        cfg = {**self._engine_cfg(), "polish_backend": backend}
        style = self._current_style()
        self._test_token += 1
        token = self._test_token
        self._test_button.config(state="disabled")
        self._test_cancel.config(state="normal")
        self._test_time.config(text="")
        self._set_text(self._test_result, "Polerar…")

        def work():
            t0 = time.perf_counter()
            try:
                if self._polish_fn is not None:
                    out = self._polish_fn(cfg, text, style)
                elif llm.is_remote(cfg):
                    out = llm.polish_remote(cfg, text, style, "sv")
                else:
                    out = llm.Polisher(cfg).polish(text, style, "sv")
                seconds = f"{time.perf_counter() - t0:.1f}".replace(".", ",")
                self._remote_queue.put(("test", "done", (token, out, seconds)))
            except Exception as e:  # noqa: BLE001
                self._remote_queue.put(
                    ("test", "error", (token, codex.friendly_error(e), ""))
                )

        threading.Thread(target=work, daemon=True, name="viska-polishtest").start()

    def _cancel_test(self) -> None:
        """Avbryter visningen, inte anropet: en förfrågan som redan är ute går
        inte att ta tillbaka, men dess svar ska inte dyka upp i rutan efter
        att man tryckt avbryt."""
        self._test_token += 1
        self._test_button.config(state="normal")
        self._test_cancel.config(state="disabled")
        self._set_text(self._test_result, "Testet avbrutet.")
        self._test_time.config(text="")

    def _finish_test(self, kind: str, payload) -> None:
        token, text, seconds = payload
        if token != self._test_token:
            return  # avbrutet, eller ersatt av ett nyare test
        self._test_button.config(state="normal")
        self._test_cancel.config(state="disabled")
        if kind == "error":
            self._set_text(self._test_result, f"Testet misslyckades: {text}")
            return
        self._set_text(self._test_result, text)
        self._test_time.config(text=f"Svarstid {seconds} s")

    def _poll_remote(self) -> None:
        try:
            while True:
                key, kind, payload = self._remote_queue.get_nowait()
                if key == "test":
                    self._finish_test(kind, payload)
                elif key == "mic":
                    self._mic_info.config(text=payload)
                else:
                    widgets = self._remote_widgets.get(key)
                    if widgets is not None and kind in widgets:
                        widgets[kind].config(text=payload)
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(150, self._poll_remote)

    # -- App-profiler --------------------------------------------------------------

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
        self._commit_profile()  # spara det man höll på med innan bytet
        self._set_profile_editor(self._app_order[selection[0]])

    def _set_profile_editor(self, app: str | None) -> None:
        was_loading, self._loading = self._loading, True
        try:
            self._fill_profile_editor(app)
        finally:
            self._loading = was_loading

    def _fill_profile_editor(self, app: str | None) -> None:
        self._selected_app = app
        profile = self._profiles.get(app or "", {})
        self._profile_app.set(app or "")
        self._set_text(self._profile_prompt, profile.get("initial_prompt", ""))
        self._refresh_inherit_labels()
        lang = profile.get("language")
        self._profile_lang.set(
            {"sv": "Svenska", "en": "Engelska"}.get(
                lang, self._profile_lang_combo.cget("values")[0]
            )
        )
        reverse_inject = {v: k for k, v in _INJECT_CHOICES.items()}
        inject = profile.get("inject_mode")
        self._profile_inject.set(
            reverse_inject.get(inject, self._profile_inject_combo.cget("values")[0])
        )
        if profile.get("polish_template"):
            self._profile_polish_choice.set(profile["polish_template"])
        elif profile.get("polish_prompt"):
            self._profile_polish_choice.set(_OWN_TEXT)
        else:
            self._profile_polish_choice.set(_GLOBAL_STANDARD)
        self._set_text(self._profile_polish, profile.get("polish_prompt", ""))
        self._toggle_profile_polish_text()
        if "flow" in profile:
            self._profile_flow.set("På" if profile["flow"] else "Av")
        else:
            self._profile_flow.set(self._profile_flow_combo.cget("values")[0])

    def _commit_profile(self) -> None:
        """Skriver redigeringsfältens innehåll till arbetskopian. Körs vid
        listbyte, vid fokusskifte och innan Spara — det finns ingen egen
        "spara profil"-knapp längre."""
        app = self._profile_app.get().strip().lower()
        if not app:
            return
        profile = {}
        prompt = self._profile_prompt.get("1.0", "end").strip()
        if prompt:
            profile["initial_prompt"] = prompt
        # Poleringsval: skriv exakt en nyckel (eller ingen). Ärv global =
        # ingen nyckel (bakåtkompatibelt). Egen text = fritext i polish_prompt.
        # En mall = polish_template.
        choice = self._profile_polish_choice.get()
        if choice == _OWN_TEXT:
            polish = self._profile_polish.get("1.0", "end").strip()
            if polish:
                profile["polish_prompt"] = polish
        elif choice != _GLOBAL_STANDARD and choice in self._template_names():
            profile["polish_template"] = choice
        if not self._inherit_value(self._profile_inject.get()):
            profile["inject_mode"] = _INJECT_CHOICES[self._profile_inject.get()]
        if not self._inherit_value(self._profile_lang.get()):
            profile["language"] = (
                "sv" if self._profile_lang.get() == "Svenska" else "en"
            )
        # Flow: bara ett uttryckligt val skrivs. "Ärv global" utelämnar
        # nyckeln, vilket är exakt vad arv betyder i configen — och gör att
        # ett "av" går att uttrycka även när det globala står på.
        if not self._inherit_value(self._profile_flow.get()):
            profile["flow"] = self._profile_flow.get() == "På"
        if self._selected_app and self._selected_app != app:
            self._profiles.pop(self._selected_app, None)
        self._profiles[app] = profile
        self._selected_app = app
        self._refresh_profile_list()

    def _new_profile(self) -> None:
        self._commit_profile()
        proc = self._choose_running_app()
        if not proc:
            return
        self._profiles.setdefault(proc.lower(), {})
        self._refresh_profile_list()
        self._set_profile_editor(proc.lower())
        self._mark_dirty()

    def _choose_running_app(self) -> str | None:
        """Liten väljare med körande program. Returnerar processnamnet."""
        pal = self._pal
        picker = tk.Toplevel(self.win)
        picker.title("Välj program")
        picker.transient(self.win)
        picker.grab_set()
        ui.setup_window(picker)
        frame = tk.Frame(picker, bg=pal["window"], padx=14, pady=12)
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame, text="Program som körs nu", bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 9), anchor="w",
        ).pack(fill="x")
        listbox = ui.themed_listbox(frame, height=12, exportselection=False)
        listbox.pack(fill="both", expand=True, pady=(6, 6))
        apps = focus.running_apps()
        for proc, name in apps:
            listbox.insert("end", f"{name}  ({proc})")
        manual = tk.StringVar()
        entry_row = tk.Frame(frame, bg=pal["window"])
        entry_row.pack(fill="x")
        tk.Label(
            entry_row, text="eller skriv processnamn:", bg=pal["window"],
            fg=pal["muted"], font=("Segoe UI", 8),
        ).pack(side="left")
        ttk.Entry(entry_row, textvariable=manual, width=22).pack(
            side="left", padx=(6, 0)
        )
        chosen: dict[str, str] = {}

        def use(proc: str) -> None:
            chosen["proc"] = proc
            picker.destroy()

        def confirm() -> None:
            selection = listbox.curselection()
            if selection:
                use(apps[selection[0]][0])
            elif manual.get().strip():
                use(manual.get().strip())

        listbox.bind("<Double-Button-1>", lambda _e: confirm())
        buttons = tk.Frame(frame, bg=pal["window"])
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Avbryt", command=picker.destroy).pack(side="right")
        ttk.Button(
            buttons, text="Välj", command=confirm, style="Accent.TButton"
        ).pack(side="right", padx=(0, 6))
        picker.wait_window()
        return chosen.get("proc")

    def _remove_profile(self) -> None:
        if self._selected_app:
            self._profiles.pop(self._selected_app, None)
        self._selected_app = None
        self._refresh_profile_list()
        self._set_profile_editor(None)
        self._mark_dirty()

    def _toggle_profile_polish_text(self) -> None:
        if self._profile_polish_choice.get() == _OWN_TEXT:
            self._profile_polish.pack(fill="x", pady=(4, 2))
        else:
            self._profile_polish.pack_forget()

    def _refresh_polish_overview(self) -> None:
        rows = []
        for app in sorted(self._profiles):
            profile = self._profiles[app]
            bits = []
            if profile.get("polish_template"):
                bits.append(f"stil: {profile['polish_template']}")
            elif profile.get("polish_prompt"):
                bits.append("stil: egen text")
            if "flow" in profile:
                bits.append(
                    "polera automatiskt: " + ("på" if profile["flow"] else "av")
                )
            if profile.get("language"):
                bits.append(f"språk: {profile['language']}")
            if profile.get("inject_mode"):
                bits.append(f"inskrivning: {profile['inject_mode']}")
            if profile.get("initial_prompt"):
                bits.append("egen ordlista")
            rows.append(
                f"{focus.display_name(app)}: " + (", ".join(bits) or "ärver allt")
            )
        self._polish_overview.config(
            text="\n".join(rows) or "Inga profiler ännu – lägg till en ovan."
        )

    # -- Mallbiblioteket -----------------------------------------------------------

    def _template_names(self) -> list[str]:
        return [t["namn"] for t in self._templates]

    def _toggle_global_polish_text(self) -> None:
        if self._global_polish_choice.get() == _OWN_TEXT:
            self._polish_prompt.pack(fill="x", pady=(4, 2))
        else:
            self._polish_prompt.pack_forget()

    def _refresh_polish_choices(self) -> None:
        names = self._template_names()
        current = self._global_polish_choice.get()
        self._global_polish_combo.config(values=[_OWN_TEXT] + names)
        if current not in names and current != _OWN_TEXT:
            self._global_polish_choice.set(_OWN_TEXT)
            self._toggle_global_polish_text()
        if hasattr(self, "_profile_polish_combo"):
            self._refresh_inherit_labels()

    def _refresh_template_list(self) -> None:
        self._template_list.delete(0, "end")
        for template in self._templates:
            self._template_list.insert("end", template["namn"])
        self._refresh_polish_choices()

    def _on_template_select(self, _event) -> None:
        selection = self._template_list.curselection()
        if not selection or selection[0] >= len(self._templates):
            return
        index = selection[0]
        self._commit_template()  # spara det man höll på med innan bytet
        if index >= len(self._templates):
            return
        template = self._templates[index]
        was_loading, self._loading = self._loading, True
        try:
            self._selected_template = template["namn"]
            self._template_name.set(template["namn"])
            self._set_text(self._template_prompt, template["prompt"])
        finally:
            self._loading = was_loading

    def _new_template(self) -> None:
        self._commit_template()
        self._selected_template = None
        self._template_name.set("Ny stil")
        self._set_text(self._template_prompt, "")
        self._template_name_entry.focus_set()

    def _commit_template(self) -> None:
        """Skriver editorn till arbetskopian. Ett namnbyte följer med till
        alla som pekar på mallen — annars hade en omdöpt stil tyst blivit
        "Egen text" för varje profil som använde den."""
        name = self._template_name.get().strip()
        if not name:
            return
        prompt = self._template_prompt.get("1.0", "end").strip()
        old = self._selected_template
        if old is not None and old != name:
            if any(t["namn"] == name for t in self._templates if t["namn"] != old):
                self._error.config(text=f"Det finns redan en stil som heter {name}.")
                return
            for template in self._templates:
                if template["namn"] == old:
                    template["namn"] = name
                    template["prompt"] = prompt
            self._rename_template_refs(old, name)
        elif any(t["namn"] == name for t in self._templates):
            for template in self._templates:
                if template["namn"] == name:
                    template["prompt"] = prompt
        else:
            self._templates.append({"namn": name, "prompt": prompt})
        self._selected_template = name
        self._error.config(text="")
        self._refresh_template_list()

    def _remove_template(self) -> None:
        selection = self._template_list.curselection()
        if not selection or selection[0] >= len(self._templates):
            return
        removed = self._templates.pop(selection[0])["namn"]
        if self._selected_template == removed:
            self._selected_template = None
            self._template_name.set("")
            self._set_text(self._template_prompt, "")
        self._refresh_template_list()
        self._mark_dirty()

    def _rename_template_refs(self, old: str, new: str) -> None:
        if self._global_polish_choice.get() == old:
            self._global_polish_choice.set(new)
        for profile in self._profiles.values():
            if profile.get("polish_template") == old:
                profile["polish_template"] = new
        if self._profile_polish_choice.get() == old:
            self._profile_polish_choice.set(new)

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
        self._mark_dirty()

    def _remove_replacement(self) -> None:
        selection = self._repl_list.curselection()
        if not selection or selection[0] >= len(self._repl_order):
            return
        self._replacements.pop(self._repl_order[selection[0]], None)
        self._refresh_replacements()
        self._mark_dirty()

    # -- Spara ---------------------------------------------------------------------

    def _commit_editors(self) -> None:
        """Allt som står i en redigerare räknas som gjort. Det var den gamla
        oklarheten: en ifylld mall eller profil kastades om man inte råkade
        trycka på rätt av två sparaknappar."""
        self._commit_template()
        self._commit_profile()
        if self._repl_heard.get().strip() and self._repl_write.get().strip():
            self._add_replacement()

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
            # Standardstilen pekar på en mall, eller "" = Egen text.
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
            "polish_backend": _ENGINE_CHOICES.get(self._engine.get(), "builtin"),
            "polish_codex_model": self._codex_model.get().strip() or "gpt-5.6-luna",
            "polish_codex_effort": (
                self._codex_effort.get() if self._codex_effort.get() in _CODEX_EFFORT
                else "low"
            ),
            "polish_ollama_model": self._ollama_model.get().strip() or "qwen3.5:9b",
            "polish_ollama_url": (
                self._ollama_url.get().strip() or "http://localhost:11434"
            ),
            "polish_http_url": (
                self._http_url.get().strip()
                or "http://127.0.0.1:8756/v1/chat/completions"
            ),
            "polish_http_model": self._http_model.get().strip() or "gpt-5.6-luna",
            "polish_http_token": self._http_token.get().strip(),
            "polish_double_tap": self._double_tap.get(),
            "polish_test_text": self._test_sample.get("1.0", "end").strip(),
            "replacements": dict(self._replacements),
            "check_updates": self._updates.get(),
            "history_persist": self._history_persist.get(),
            "github_token": self._github_token.get().strip(),
        }
        if self._feel.get() in FEELS:
            interval, silence = FEELS[self._feel.get()]
            changes["live_interval_s"] = interval
            changes["live_commit_silence_s"] = silence
        # Modellfältet under Inställningar vinner bara om det uttryckligen
        # ÄNDRATS — annars gäller kvalitetsvalet på Diktering-sidan (en
        # förenkling av samma sak).
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
        changes["polish_menu_hotkey"] = self._adv["polish_menu_hotkey"].get().strip()
        return changes

    def _save(self) -> None:
        self._commit_editors()
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
            self._error.config(text="Poleringstangenten kan inte vara en musknapp.")
            return
        menu_key = changes["polish_menu_hotkey"]
        if menu_key:
            try:
                parse_hotkey(menu_key)
            except HotkeyError as e:
                self._error.config(text=str(e))
                return
            if is_mouse(menu_key):
                self._error.config(text="Tonvalsmenyn kan inte ligga på en musknapp.")
                return
        bindings = [changes["hotkey"], changes["polish_hotkey"]] + (
            [menu_key] if menu_key else []
        )
        if len(set(bindings)) != len(bindings):
            self._error.config(
                text="Diktering, polering och tonvalsmeny måste ha olika tangenter."
            )
            return
        error = self._on_save(changes)
        if error:
            self._error.config(text=error)
            return
        self._closing = True
        self.win.destroy()
