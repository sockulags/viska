"""Viska – lokal diktering för Windows. Huvudprogram, tray och CLI."""

import argparse
import logging
import os
import queue
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
from viska.settings import SettingsWindow
from viska.sounds import Sounds
from viska.stream import LiveWorker
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
        self._session_transcriber: Transcriber | None = None
        self._live: LiveWorker | None = None
        self._live_texts: list[str] = []  # fraser som redan skrivits in
        # Tkinter är huvudtrådsbundet; trayn lägger UI-jobb här i stället.
        self._ui_queue: queue.Queue = queue.Queue()
        self._settings: SettingsWindow | None = None
        self.hotkey = PushToTalk(
            cfg["hotkey"], cfg["hold_threshold_s"], self._on_start, self._on_stop
        )
        threading.Thread(
            target=self._load_model, args=(self.language,), daemon=True
        ).start()
        self._poll_quit()

    # -- Modell ------------------------------------------------------------------

    def _load_model(self, lang: str, force: bool = False) -> None:
        if not force and lang in self.transcribers:
            return
        namn = LANG_NAMES[lang]
        self.status = f"laddar {namn} modell…"
        self.overlay.show(
            f"Viska: laddar {namn} modell (första gången laddas den ner)…", GRAY
        )
        try:
            model = self.cfg["model"] if lang == "sv" else self.cfg["model_en"]
            t = Transcriber(
                model,
                self.cfg["device"],
                self.cfg["beam_size"],
                self.cfg["compute_type"],
            )
            t.initial_prompt = self.cfg["initial_prompt"]
            with self._session_lock:
                if force:
                    # Modellbyte: släpp även andra språkets modell så den
                    # laddas om lat med de nya inställningarna. Pågående
                    # _finish behåller sin egen referens.
                    self.transcribers = {lang: t}
                else:
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
            # En referens som hela sessionen använder — dicten kan bytas ut
            # av ett modellbyte i Inställningar medan vi kör.
            transcriber = self.transcribers.get(lang)
            if transcriber is None:
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
            self._session_transcriber = transcriber
            self.sounds.start()
            self.overlay.show(
                f"● Spelar in – släpp eller tryck {self.cfg['hotkey'].upper()} igen",
                RED,
            )
            if self._watchdog is not None:  # ska inte hända, men läck aldrig
                self._watchdog.cancel()
            self._watchdog = threading.Timer(
                self.cfg["max_recording_s"], self._on_max_time
            )
            self._watchdog.daemon = True
            self._watchdog.start()
            self._live_texts = []
            if self._live_enabled(transcriber):
                self._live = LiveWorker(
                    transcriber,
                    self.recorder,
                    lang,
                    on_commit=self._on_live_commit,
                    on_partial=self._on_live_partial,
                    interval_s=self.cfg["live_interval_s"],
                    commit_silence_s=self.cfg["live_commit_silence_s"],
                )
                self._live.start()
            return True

    def _live_enabled(self, transcriber: Transcriber) -> bool:
        live = self.cfg["live"]
        if live == "auto":
            return transcriber.device == "cuda"
        return bool(live)

    def _on_live_commit(self, text: str) -> None:
        self._live_texts.append(text)
        try:
            # Mellanslag mellan fraser oavsett append_space — annars klistras
            # nästa fras fast i den förra.
            inject(
                text + " ", self.cfg["inject_mode"], self.cfg["restore_clipboard"]
            )
        except Exception:
            # Frasen finns kvar i last_text (traymenyn: Kopiera senaste).
            log.exception("Kunde inte skriva in livefrasen")

    def _on_live_partial(self, partial: str) -> None:
        if partial:
            if len(partial) > 70:
                partial = "…" + partial[-70:]
            self.overlay.show(f"● {partial}", RED)

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
            self.sounds.stop()
            live, self._live = self._live, None
            committed = live.stop() if live is not None else 0
            audio = self.recorder.stop()
            if committed:
                audio = audio[committed:]
            # Kopia: skulle ett hängande livepass leva kvar får det inte
            # mutera listan som _finish läser.
            live_texts = list(self._live_texts)
            self._transcribing.set()
            self.overlay.show("… Transkriberar", BLUE)
            lang = self._session_language
            transcriber = self._session_transcriber
        threading.Thread(
            target=self._finish,
            args=(audio, lang, transcriber, live_texts),
            daemon=True,
            name="viska-transcribe",
        ).start()

    def _finish(
        self,
        audio: np.ndarray,
        lang: str,
        transcriber: Transcriber,
        live_texts: list[str],
    ) -> None:
        try:
            if live_texts:
                # Sätts innan sluttranskriberingen: kastar den ett fel ska
                # de redan inskrivna fraserna ändå finnas i "Kopiera
                # senaste diktering".
                self.last_text = " ".join(live_texts).strip()
            text = ""
            too_short = len(audio) < 0.3 * SAMPLE_RATE
            silent = audio.size == 0 or float(np.abs(audio).max()) < 0.003
            if not (too_short or silent) and transcriber is not None:
                text = transcriber.transcribe(audio, lang)
            if live_texts or text:
                self.last_text = " ".join([*live_texts, text]).strip()
            if not text:
                if not live_texts and not (too_short or silent):
                    self.overlay.show(
                        "(inget tal uppfattades)", GRAY, auto_hide_ms=1500
                    )
                else:
                    self.overlay.hide()
                return
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

    # -- Inställningar ---------------------------------------------------------------

    def request_settings(self) -> None:
        """Trådsäker: be huvudtråden öppna inställningsfönstret."""
        self._ui_queue.put(self._open_settings)

    def _open_settings(self) -> None:
        if self._settings is not None and self._settings.win.winfo_exists():
            self._settings.win.lift()
            return
        self._settings = SettingsWindow(self.root, self.cfg, self._apply_settings)

    def _apply_settings(self, changes: dict):
        """Körs i huvudtråden (Spara-knappen). Returnerar felsträng eller None."""
        # Pågår en diktering avslutas den snyggt först — annars blir
        # sessionen föräldralös när tangenten byts ut (inspelningen skulle
        # aldrig gå att stoppa) och det som sagts hittills ginge förlorat.
        if self.hotkey.force_stop():
            self._on_stop()
        old = self.cfg
        if changes["hotkey"] != old["hotkey"]:
            try:
                new_hotkey = None
                self.hotkey.stop()  # avregistrera innan samma/ny tangent tas
                new_hotkey = PushToTalk(
                    changes["hotkey"],
                    old["hold_threshold_s"],
                    self._on_start,
                    self._on_stop,
                )
                self.hotkey = new_hotkey
            except HotkeyError as e:
                if new_hotkey is None:  # återta den gamla tangenten
                    self.hotkey = PushToTalk(
                        old["hotkey"],
                        old["hold_threshold_s"],
                        self._on_start,
                        self._on_stop,
                    )
                return str(e)
        model_changed = (
            changes["model"] != old["model"] or changes["model_en"] != old["model_en"]
        )
        self.cfg.update(changes)
        save(self.cfg)
        self.sounds.enabled = bool(changes["sounds"])
        for t in self.transcribers.values():
            t.initial_prompt = changes["initial_prompt"]
        self.language = changes["language"]
        if model_changed:
            # Byts inte synkront: pågående sessioner behåller sin referens,
            # nya modellen svajpas in under sessionslåset när den är klar.
            threading.Thread(
                target=self._load_model, args=(self.language, True), daemon=True
            ).start()
        elif self.language not in self.transcribers:
            threading.Thread(
                target=self._load_model, args=(self.language,), daemon=True
            ).start()
        log.info("Inställningar sparade")
        return None

    # -- Livscykel -----------------------------------------------------------------

    def quit(self) -> None:
        if self.icon is not None:
            self.icon.stop()
        self.hotkey.stop()
        self._quit.set()

    def _poll_quit(self) -> None:
        if self._quit.is_set():
            self.root.destroy()
            return
        while True:
            try:
                job = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                job()
            except Exception:
                # Ett trasigt UI-jobb får aldrig döda after-kedjan — då
                # slutar både Inställningar och Avsluta att fungera.
                log.exception("Fel i UI-jobb")
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
        pystray.MenuItem(
            "Inställningar…", lambda icon, item: app.request_settings()
        ),
        pystray.MenuItem("Byt till ny standardmikrofon", restart_audio),
        pystray.MenuItem(
            "Öppna config-filen", lambda icon, item: os.startfile(CONFIG_PATH)
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

    if getattr(sys, "frozen", False):  # PyInstaller-exe
        target = Path(sys.executable)
        args = ""
        workdir = target.parent
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        target = pythonw if pythonw.exists() else Path(sys.executable)
        args = "-m viska"
        workdir = Path(__file__).resolve().parents[1]
    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortCut(str(_startup_lnk()))
    lnk.TargetPath = str(target)
    lnk.Arguments = args
    lnk.WorkingDirectory = str(workdir)
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
        t = Transcriber(model, cfg["device"], cfg["beam_size"], cfg["compute_type"])
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
