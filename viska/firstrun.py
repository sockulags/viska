"""Förstagångsdialog: upptäcker NVIDIA-GPU och erbjuder att ladda ner
GPU-stödet direkt. Körs modalt i huvudtråden innan appen startar;
nedladdningen sker i en arbetstråd med förlopp via kö + after-poll.
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from viska import gpu, ui


class FirstRunDialog:
    def __init__(self, root: tk.Tk):
        self._offer_gpu = gpu.has_nvidia_gpu() and not gpu.gpu_support_present()
        self._queue: queue.Queue = queue.Queue()
        self._cancelled = False
        self._downloading = False

        ui.refresh_theme()
        self.win = tk.Toplevel(root)
        self.win.title("Välkommen till Viska")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        ui.setup_window(self.win)

        body = ttk.Frame(self.win, padding=16)
        body.grid(sticky="nsew")
        ttk.Label(
            body,
            text="Viska – lokal diktering. Allt körs på din dator;\n"
            "inget ljud eller text lämnar den.",
            justify="left",
        ).grid(sticky="w", pady=(0, 10))

        name = gpu.gpu_name()
        if self._offer_gpu:
            ttk.Label(body, text=f"NVIDIA-GPU hittad: {name}").grid(sticky="w")
            self._choice = tk.StringVar(value="gpu")
            ttk.Radiobutton(
                body,
                text=f"GPU-läge – ladda ner CUDA-bibliotek (~{gpu.DOWNLOAD_SIZE_GB} GB).\n"
                "Snabbast, bäst modell och livetranskribering. (Rekommenderas)",
                value="gpu",
                variable=self._choice,
            ).grid(sticky="w", pady=(6, 2))
            ttk.Radiobutton(
                body,
                text="CPU-läge – fungerar direkt utan nedladdning.\n"
                "Går att ändra i Inställningar senare.",
                value="cpu",
                variable=self._choice,
            ).grid(sticky="w", pady=2)
        else:
            info = (
                f"NVIDIA-GPU hittad: {name} – GPU-läget används."
                if gpu.has_nvidia_gpu()
                else "Ingen NVIDIA-GPU hittades – Viska kör på CPU\n"
                "med en mindre (fortfarande väldigt bra) svensk modell."
            )
            ttk.Label(body, text=info, justify="left").grid(sticky="w")
            self._choice = tk.StringVar(value="cpu")

        self._progress = ttk.Progressbar(body, length=340, maximum=1.0)
        self._status = ttk.Label(body, text="")
        self._progress.grid(sticky="we", pady=(12, 2))
        self._status.grid(sticky="w")
        self._progress.grid_remove()

        self._button = ttk.Button(body, text="Starta Viska", command=self._go)
        self._button.grid(sticky="e", pady=(12, 0))

        self._poll(root)

    def _poll(self, root) -> None:
        try:
            while True:
                kind, text, frac = self._queue.get_nowait()
                if kind == "progress":
                    self._progress["value"] = frac
                    self._status.config(text=text)
                elif kind == "done":
                    self.win.destroy()
                    return
                else:  # fel
                    if self._cancelled:
                        # Användaren ville stänga mitt i — fullfölj det.
                        self.win.destroy()
                        return
                    self._status.config(
                        text=text + "\nGPU-stöd går att lägga till senare i Inställningar."
                    )
                    # Knappen ska nu göra vad den säger: starta appen —
                    # inte försöka ladda ner igen.
                    self._button.config(
                        state="normal",
                        text="Starta Viska ändå",
                        command=self.win.destroy,
                    )
                    self._downloading = False
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            root.after(100, self._poll, root)

    def _go(self) -> None:
        if self._choice.get() != "gpu" or gpu.gpu_support_present():
            self.win.destroy()
            return
        if self._downloading:
            return
        self._cancelled = False  # nytt försök = ren avbrottsflagga
        self._downloading = True
        self._button.config(state="disabled")
        self._progress.grid()
        threading.Thread(target=self._download, daemon=True).start()

    def _download(self) -> None:
        try:
            gpu.download_gpu_libs(
                progress=lambda t, f: self._queue.put(("progress", t, f)),
                cancelled=lambda: self._cancelled,
            )
            self._queue.put(("done", "", 1.0))
        except Exception as e:
            self._queue.put(("error", f"Nedladdningen misslyckades: {e}", 0.0))

    def _close(self) -> None:
        self._cancelled = True
        if not self._downloading:
            self.win.destroy()


def run(root: tk.Tk) -> None:
    """Visar dialogen modalt; återvänder när den stängts."""
    dialog = FirstRunDialog(root)
    root.wait_window(dialog.win)
