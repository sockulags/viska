"""Viska – lokal diktering för Windows. Huvudprogram, tray och CLI."""

import argparse
import logging
import os
import sys
import threading
import tkinter as tk
from pathlib import Path

import numpy as np

from viska import __version__
from viska.audio import SAMPLE_RATE, Recorder
from viska.config import CONFIG_DIR, CONFIG_PATH, LOG_PATH, load, save
from viska.hotkey import HotkeyError, PushToTalk
from viska.inject import copy_to_clipboard, inject
from viska.overlay import BLUE, GRAY, GREEN, RED, Overlay
from viska.sounds import Sounds
from viska.transcribe import Transcriber

log = logging.getLogger("viska")

LANG_NAMES = {"sv": "svensk", "en": "engelsk"}


class App:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.cfg = cfg
        self.root = root
        self.overlay = Overlay(root)
        self.sounds = Sounds(cfg["sounds"])
        self.recorder = Recorder(
            cfg["input_device"], cfg["keep_mic_open"], cfg["preroll_s"]
        )
        self.transcribers: dict[str, Transcriber] = {}
        self.language = cfg["language"] if cfg["language"] in LANG_NAMES else "sv"
        self.last_text = ""
        self.status = "startar…"
        self.icon = None  # sätts av make_tray
        self._transcribing = threading.Event()
        self._watchdog = None
        self._quit = threading.Event()
        # Serialiserar start/stopp av en dikteringssession (hotkey-trådarna
        # och maxtidstimern) och fryser vilket språk sessionen hade.
        self._session_lock = threading.Lock()
        self._session_language = self.language
        self.hotkey = PushToTalk(
            cfg["hotkey"], cfg["hold_threshold_s"], self._on_start, self._on_stop
        )
        threading.Thread(
            target=self._load_model, args=(self.language,), daemon=True
        ).start()
        self._poll_quit()

    # -- Modell ------------------------------------------------------------------

    def _load_model(self, lang: str) -> None:
        if lang in self.transcribers:
            return
        namn = LANG_NAMES[lang]
        self.status = f"laddar {namn} modell…"
        self.overlay.show(
            f"Viska: laddar {namn} modell (första gången laddas den ner)…", GRAY
        )
        try:
            model = self.cfg["model"] if lang == "sv" else self.cfg["model_en"]
            t = Transcriber(model, self.cfg["device"], self.cfg["beam_size"])
            self.transcribers[lang] = t
            self.status = f"redo – {t.model_name} ({t.device})"
            self.overlay.show(
                f"Viska redo ({t.device.upper()}) – tryck "
                f"{self.cfg['hotkey'].upper()} och prata",
                GREEN,
                auto_hide_ms=3500,
            )
        except Exception:
            log.exception("Kunde inte ladda modellen för %s", lang)
            self.status = "fel vid modelladdning"
            self.sounds.error()
            self.overlay.show(
                "Viska: modellen kunde inte laddas – se loggen", RED, auto_hide_ms=8000
            )

    def set_language(self, lang: str) -> None:
        self.language = lang
        self.cfg["language"] = lang
        save(self.cfg)
        if lang not in self.transcribers:
            threading.Thread(
                target=self._load_model, args=(lang,), daemon=True
            ).start()

    # -- Diktering (anropas från hotkey-trådarna) ---------------------------------

    def _on_start(self) -> bool:
        """Returnerar True om en inspelning startade (PushToTalk-kontraktet)."""
        with self._session_lock:
            lang = self.language
            if self.transcribers.get(lang) is None:
                self.overlay.show(
                    "Viska: modellen laddas fortfarande…", GRAY, auto_hide_ms=1500
                )
                return False
            if self._transcribing.is_set():
                self.overlay.show(
                    "Viska: vänta – förra dikteringen skrivs ut…",
                    GRAY,
                    auto_hide_ms=1200,
                )
                return False
            try:
                self.recorder.start()
            except Exception:
                log.exception("Kunde inte starta inspelningen")
                self.sounds.error()
                self.overlay.show(
                    "Viska: mikrofonfel – se loggen", RED, auto_hide_ms=4000
                )
                return False
            self._session_language = lang
            self.sounds.start()
            self.overlay.show(
                f"● Spelar in – släpp eller tryck {self.cfg['hotkey'].upper()} igen",
                RED,
            )
            self._watchdog = threading.Timer(
                self.cfg["max_recording_s"], self._on_max_time
            )
            self._watchdog.daemon = True
            self._watchdog.start()
            return True

    def _on_max_time(self) -> None:
        # force_stop() avgör atomiskt vem som äger stoppet — bara om vi tog
        # en aktiv session får vi anropa _on_stop, annars stoppade
        # användaren precis själv.
        if self.hotkey.force_stop():
            log.info("Maxtid nådd (%s s) – stoppar", self.cfg["max_recording_s"])
            self._on_stop()

    def _on_stop(self) -> None:
        with self._session_lock:
            watchdog, self._watchdog = self._watchdog, None
            if watchdog is not None:
                watchdog.cancel()
            audio = self.recorder.stop()
            self.sounds.stop()
            self._transcribing.set()
            self.overlay.show("… Transkriberar", BLUE)
            lang = self._session_language
        threading.Thread(
            target=self._finish,
            args=(audio, lang),
            daemon=True,
            name="viska-transcribe",
        ).start()

    def _finish(self, audio: np.ndarray, lang: str) -> None:
        try:
            too_short = len(audio) < 0.3 * SAMPLE_RATE
            silent = audio.size == 0 or float(np.abs(audio).max()) < 0.003
            if too_short or silent:
                self.overlay.hide()
                return
            transcriber = self.transcribers.get(lang)
            if transcriber is None:  # ska inte kunna hända: fanns vid start
                self.overlay.hide()
                return
            text = transcriber.transcribe(audio, lang)
            if not text:
                self.overlay.show("(inget tal uppfattades)", GRAY, auto_hide_ms=1500)
                return
            self.last_text = text
            if self.cfg["append_space"]:
                text += " "
            inject(text, self.cfg["inject_mode"], self.cfg["restore_clipboard"])
            self.overlay.hide()
        except Exception:
            log.exception("Transkriberingen misslyckades")
            self.sounds.error()
            self.overlay.show(
                "Viska: fel vid transkribering – se loggen", RED, auto_hide_ms=4000
            )
        finally:
            self._transcribing.clear()

    # -- Livscykel -----------------------------------------------------------------

    def quit(self) -> None:
        if self.icon is not None:
            self.icon.stop()
        self.hotkey.stop()
        self._quit.set()

    def _poll_quit(self) -> None:
        if self._quit.is_set():
            self.root.destroy()
        else:
            self.root.after(200, self._poll_quit)


# -- Tray ----------------------------------------------------------------------------


def _make_icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((24, 6, 40, 36), radius=8, fill="#e8eaed")
    d.arc((16, 18, 48, 50), start=0, end=180, fill="#e8eaed", width=5)
    d.line((32, 50, 32, 57), fill="#e8eaed", width=5)
    d.line((22, 57, 42, 57), fill="#e8eaed", width=5)
    return img


def make_tray(app: App):
    import pystray

    def restart_audio(icon, item):
        threading.Thread(target=app.recorder.restart, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda item: f"Viska {__version__} – {app.status}", None, enabled=False
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Svenska",
            lambda icon, item: app.set_language("sv"),
            checked=lambda item: app.language == "sv",
            radio=True,
        ),
        pystray.MenuItem(
            "Engelska",
            lambda icon, item: app.set_language("en"),
            checked=lambda item: app.language == "en",
            radio=True,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Kopiera senaste diktering",
            lambda icon, item: copy_to_clipboard(app.last_text),
        ),
        pystray.MenuItem("Byt till ny standardmikrofon", restart_audio),
        pystray.MenuItem(
            "Öppna inställningar", lambda icon, item: os.startfile(CONFIG_PATH)
        ),
        pystray.MenuItem("Öppna logg", lambda icon, item: os.startfile(LOG_PATH)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Avsluta", lambda icon, item: app.quit()),
    )
    icon = pystray.Icon("viska", _make_icon_image(), "Viska – lokal diktering", menu)
    icon.run_detached()
    return icon


# -- Autostart -------------------------------------------------------------------------


def _startup_lnk() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / "Viska.lnk"
    )


def install_autostart() -> None:
    import win32com.client

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortCut(str(_startup_lnk()))
    lnk.TargetPath = str(exe)
    lnk.Arguments = "-m viska"
    lnk.WorkingDirectory = str(Path(__file__).resolve().parents[1])
    lnk.Description = "Viska – lokal diktering"
    lnk.Save()
    print(f"Autostart installerad: {_startup_lnk()}")


def uninstall_autostart() -> None:
    lnk = _startup_lnk()
    if lnk.exists():
        lnk.unlink()
        print("Autostart borttagen.")
    else:
        print("Ingen autostart-genväg hittades.")


# -- CLI ----------------------------------------------------------------------------------


def _setup_logging() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="viska",
        description="Lokal diktering för Windows – tryck en tangent, prata, "
        "texten skrivs där markören är. Inget lämnar datorn.",
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="lista ljudenheter och avsluta"
    )
    parser.add_argument(
        "--transcribe", metavar="FIL", help="transkribera en ljudfil och skriv ut"
    )
    parser.add_argument(
        "--language", default=None, help="språk för --transcribe (default: config)"
    )
    parser.add_argument(
        "--check", action="store_true", help="ladda modellen och rapportera status"
    )
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--uninstall-autostart", action="store_true")
    args = parser.parse_args()

    _setup_logging()
    cfg = load()

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    if args.install_autostart:
        install_autostart()
        return
    if args.uninstall_autostart:
        uninstall_autostart()
        return
    if args.check or args.transcribe:
        lang = args.language or cfg["language"]
        model = cfg["model_en"] if lang == "en" else cfg["model"]
        t = Transcriber(model, cfg["device"], cfg["beam_size"])
        print(f"Modell:  {t.model_name}")
        print(f"Enhet:   {t.device}")
        print(f"Config:  {CONFIG_PATH}")
        if args.transcribe:
            text = t.transcribe(args.transcribe, lang)
            print(f"Text:    {text}")
        return

    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root, cfg)
    except HotkeyError as e:
        log.error("%s", e)
        from tkinter import messagebox

        messagebox.showerror("Viska", str(e))
        sys.exit(1)
    app.icon = make_tray(app)
    log.info("Viska %s igång – tangent: %s", __version__, cfg["hotkey"])
    root.mainloop()


if __name__ == "__main__":
    main()
