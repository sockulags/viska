"""Rätta senaste dikteringen — och låt rättningen bli en regel.

Ordlistan (Whispers initial_prompt) är bara ett tips till modellen och
ignoreras ofta. En ersättningsregel körs efter transkriberingen och slår
därför alltid igenom. Det här fönstret är vägen dit som inte kräver att
man vet vad en regel är: rätta texten som vanligt, så räknas skillnaden
ut och föreslås som regler man kan tacka nej till.

Öppnas från traymenyn och lever bara i huvudtråden (Tk).
"""

import logging
import tkinter as tk
from tkinter import ttk

from viska import ui
from viska.corrections import derive_rules

log = logging.getLogger(__name__)


class CorrectionWindow:
    def __init__(self, root: tk.Tk, text: str, replacements: dict, on_save):
        """text: senaste dikteringen. replacements: reglerna som gäller nu
        (kopia). on_save(regler: dict) anropas med hela den nya uppsättningen."""
        ui.refresh_theme()
        self._original = text
        self._rules = {k: v for k, v in replacements.items() if isinstance(v, str)}
        self._on_save = on_save
        self._order: list[str] = []

        self.win = tk.Toplevel(root)
        self.win.title("Viska – rätta senaste dikteringen")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        pal = ui.setup_window(self.win)
        self._pal = pal

        body = tk.Frame(self.win, bg=pal["window"], padx=14, pady=12)
        body.pack(fill="both", expand=True)

        ui.section(body, "Så här hörde Viska")
        self._hint(
            body,
            "Rätta texten nedan. Orden du ändrar blir förslag på regler som\n"
            "gäller alla kommande dikteringar.",
        )
        self._text = ui.themed_text(body, width=62, height=5, wrap="word")
        self._text.pack(fill="x", pady=(4, 2))
        self._text.insert("1.0", text)
        ttk.Button(
            body, text="Föreslå regler ur rättningen", command=self._derive
        ).pack(anchor="w", pady=(2, 0))

        ui.section(body, "Regler")
        self._hint(
            body, "Hörd fras till vänster, texten som skrivs in till höger."
        )
        picker = tk.Frame(body, bg=pal["window"])
        picker.pack(fill="x", pady=(4, 0))
        self._list = ui.themed_listbox(picker, height=6, exportselection=False)
        self._list.pack(side="left", fill="both", expand=True)
        buttons = tk.Frame(picker, bg=pal["window"])
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Ta bort", width=12, command=self._remove).pack(pady=2)

        adder = tk.Frame(body, bg=pal["window"])
        adder.pack(fill="x", pady=(8, 0))
        self._heard = tk.StringVar()
        self._write = tk.StringVar()
        tk.Label(
            adder, text="Viska hör", bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(adder, textvariable=self._heard, width=18).pack(
            side="left", padx=(6, 6)
        )
        tk.Label(
            adder, text="skriv", bg=pal["window"], fg=pal["text"],
            font=("Segoe UI", 9),
        ).pack(side="left")
        ttk.Entry(adder, textvariable=self._write, width=18).pack(
            side="left", padx=(6, 6)
        )
        ttk.Button(adder, text="Lägg till", command=self._add_manual).pack(side="left")

        self._error = tk.Label(
            body, text="", bg=pal["window"], fg="#e24b4a",
            font=("Segoe UI", 8), anchor="w", justify="left",
        )
        self._error.pack(fill="x", pady=(6, 0))

        footer = tk.Frame(self.win, bg=pal["window"])
        footer.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(footer, text="Avbryt", command=self.win.destroy).pack(side="right")
        ttk.Button(
            footer, text="Spara", command=self._save, style="Accent.TButton"
        ).pack(side="right", padx=(0, 6))

        self._refresh()

    # -- Internt ------------------------------------------------------------------

    def _hint(self, parent, text: str) -> None:
        tk.Label(
            parent, text=text, bg=self._pal["window"], fg=self._pal["muted"],
            font=("Segoe UI", 8), anchor="w", justify="left",
        ).pack(fill="x")

    def _refresh(self) -> None:
        self._list.delete(0, "end")
        self._order = sorted(self._rules, key=str.lower)
        for heard in self._order:
            self._list.insert("end", f"{heard}  →  {self._rules[heard]}")

    def _derive(self) -> None:
        corrected = self._text.get("1.0", "end").strip()
        found = derive_rules(self._original, corrected)
        if not found:
            self._error.config(
                text="Ingen skillnad hittad – ändra texten först, eller lägg "
                "till en regel för hand."
            )
            return
        added = 0
        for heard, write in found:
            if self._rules.get(heard) != write:
                self._rules[heard] = write
                added += 1
        self._refresh()
        self._error.config(
            text=f"{added} regel(er) tillagda nedan – ta bort dem du inte vill ha."
            if added
            else "Reglerna fanns redan."
        )

    def _add_manual(self) -> None:
        heard = self._heard.get().strip()
        write = self._write.get().strip()
        if not heard or not write:
            self._error.config(text="Fyll i både hörd fras och text att skriva.")
            return
        self._rules[heard] = write
        self._heard.set("")
        self._write.set("")
        self._error.config(text="")
        self._refresh()

    def _remove(self) -> None:
        selection = self._list.curselection()
        if not selection or selection[0] >= len(self._order):
            return
        self._rules.pop(self._order[selection[0]], None)
        self._refresh()

    def _save(self) -> None:
        # Ett ifyllt men oadderat par ska inte tyst kastas bort (samma
        # beteende som profil- och mallredigerarna i Inställningar).
        if self._heard.get().strip() and self._write.get().strip():
            self._add_manual()
        try:
            self._on_save(dict(self._rules))
        except Exception:
            log.exception("Kunde inte spara ersättningsreglerna")
            self._error.config(text="Kunde inte spara – se loggen")
            return
        self.win.destroy()
