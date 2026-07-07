"""Viska – lokal diktering för Windows. Huvudprogram, tray och CLI."""

import argparse
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from collections import deque
from pathlib import Path

import numpy as np

from viska import __version__, gpu, update
from viska.audio import SAMPLE_RATE, Recorder
from viska.config import CONFIG_DIR, CONFIG_PATH, LOG_PATH, load, save
from viska.focus import foreground_app
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
        self.sounds = Sounds(cfg["sounds"])
        self.recorder = Recorder(
            cfg["input_device"], cfg["keep_mic_open"], cfg["preroll_s"]
        )
        self.overlay = Overlay(root, level_source=lambda: self.recorder.level)
        self.history: deque[str] = deque(maxlen=int(cfg["history_size"]))
        self.update_tag: str | None = None
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
        self._session_prompt: str | None = None
        self._session_inject = cfg["inject_mode"]
        # En modelladdning per språk i taget — utan spärren skulle varje
        # tangenttryck i en profilerad app starta ännu en 3 GB-laddning.
        self._loading_langs: set[str] = set()
        self._loading_lock = threading.Lock()
        self._live: LiveWorker | None = None
        self._live_texts: list[str] = []  # fraser som redan skrivits in
        # Tkinter är huvudtrådsbundet; trayn lägger UI-jobb här i stället.
        self._ui_queue: queue.Queue = queue.Queue()
        self._settings: SettingsWindow | None = None
        # En GPU-åtgärd (nedladdning/borttagning) i taget — annars kan ett
        # återöppnat Inställningar-fönster starta en andra nedladdning som
        # raderar och skriver om samma filer som den första.
        self._gpu_busy = threading.Event()
        self.hotkey = PushToTalk(
            cfg["hotkey"], cfg["hold_threshold_s"], self._on_start, self._on_stop
        )
        threading.Thread(
            target=self._load_model, args=(self.language,), daemon=True
        ).start()
        if cfg["check_updates"]:
            update.check_async(__version__, self._on_update_available)
        self._poll_quit()

    def _on_update_available(self, tag: str) -> None:
        self.update_tag = tag
        self.status += f" · ny version {tag} finns"
        self._refresh_tray_menu()

    def _refresh_tray_menu(self) -> None:
        # pystrays Windows-backend bygger menyn EN gång och uppdaterar den
        # bara vid klick — dynamiskt innehåll (historik, uppdateringsrad,
        # status) måste trigga en ombyggnad själv.
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass  # kosmetiskt

    # -- Modell ------------------------------------------------------------------

    def _load_model(self, lang: str, force: bool = False, quiet: bool = False) -> None:
        with self._loading_lock:
            if (not force and lang in self.transcribers) or lang in self._loading_langs:
                return
            self._loading_langs.add(lang)
        try:
            namn = LANG_NAMES[lang]
            if not quiet:
                self.status = f"laddar {namn} modell…"
                self._refresh_tray_menu()
                self.overlay.show(
                    f"Viska: laddar {namn} modell (första gången laddas den ner)…",
                    GRAY,
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
                self._refresh_tray_menu()
                # Overlay bara när ingen inspelning pågår — "tryck F9" mitt
                # i en pågående diktering vore direkt vilseledande.
                if not quiet and not self.recorder.recording:
                    self.overlay.show(
                        f"Viska redo ({t.device.upper()}) – tryck "
                        f"{self.cfg['hotkey'].upper()} och prata",
                        GREEN,
                        auto_hide_ms=3500,
                    )
            except Exception:
                log.exception("Kunde inte ladda modellen för %s", lang)
                self.status = "fel vid modelladdning"
                self._refresh_tray_menu()
                if not quiet:
                    self.sounds.error()
                    self.overlay.show(
                        "Viska: modellen kunde inte laddas – se loggen",
                        RED,
                        auto_hide_ms=8000,
                    )
        finally:
            with self._loading_lock:
                self._loading_langs.discard(lang)

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
            # Profil för appen som ska ta emot texten. Språk byts ALDRIG
            # automatiskt — bara om profilen uttryckligen anger det.
            fg = foreground_app()
            profile = self.cfg["app_profiles"].get(fg, {}) if fg else {}
            lang = profile.get("language") or self.language
            transcriber = self.transcribers.get(lang)
            if transcriber is None and lang != self.language:
                # Profilens språkmodell är inte laddad ännu: kör aktiva
                # språket nu och ladda profilens i bakgrunden till nästa
                # gång (quiet: overlay tillhör inspelningen).
                threading.Thread(
                    target=self._load_model, args=(lang, False, True), daemon=True
                ).start()
                lang = self.language
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
            # Global ordlista + appens egen, kombinerat.
            self._session_prompt = (
                ", ".join(
                    p
                    for p in (
                        self.cfg["initial_prompt"],
                        profile.get("initial_prompt", ""),
                    )
                    if p
                )
                or None
            )
            self._session_inject = profile.get(
                "inject_mode", self.cfg["inject_mode"]
            )
            self.sounds.start()
            self._set_tray_recording(True)
            self.overlay.show("Spelar in", RED, meter=True)
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
                    initial_prompt=self._session_prompt,
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
            inject(text + " ", self._session_inject, self.cfg["restore_clipboard"])
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
            self._set_tray_recording(False)
            live, self._live = self._live, None
            committed = live.stop() if live is not None else 0
            audio = self.recorder.stop()
            if committed:
                audio = audio[committed:]
            # Kopia: skulle ett hängande livepass leva kvar får det inte
            # mutera listan som _finish läser.
            live_texts = list(self._live_texts)
            self._transcribing.set()
            self.overlay.show("Skriver…", BLUE)
            lang = self._session_language
            transcriber = self._session_transcriber
            prompt = self._session_prompt
            inject_mode = self._session_inject
        threading.Thread(
            target=self._finish,
            args=(audio, lang, transcriber, live_texts, prompt, inject_mode),
            daemon=True,
            name="viska-transcribe",
        ).start()

    def _finish(
        self,
        audio: np.ndarray,
        lang: str,
        transcriber: Transcriber,
        live_texts: list[str],
        prompt: str | None,
        inject_mode: str,
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
                text = transcriber.transcribe(audio, lang, initial_prompt=prompt)
            if live_texts or text:
                self.last_text = " ".join([*live_texts, text]).strip()
                self.history.appendleft(self.last_text)
                self._refresh_tray_menu()
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
            inject(text, inject_mode, self.cfg["restore_clipboard"])
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
        self._settings = SettingsWindow(
            self.root, self.cfg, self._apply_settings, gpu_panel=self._gpu_panel()
        )

    def _gpu_panel(self) -> dict:
        if self._gpu_busy.is_set():
            return {"state": "GPU-åtgärd pågår…", "action": None}
        t = self.transcribers.get(self.language)
        if t is not None and t.device == "cuda":
            state = "aktivt (CUDA)"
        elif gpu.has_nvidia_gpu():
            state = "NVIDIA-GPU hittad men GPU-läget är inaktivt"
        else:
            return {"state": "ingen NVIDIA-GPU hittad", "action": None}
        if gpu.downloaded_libs_installed():
            return {
                "state": state,
                "action": "remove",
                "label": "Ta bort GPU-stöd",
                "run": self._remove_gpu_async,
            }
        if t is not None and t.device == "cuda":
            # CUDA körs redan via systemets/pip:ens bibliotek —
            # en nedladdning vore meningslös.
            return {"state": state + " – via systemets CUDA", "action": None}
        if not gpu.gpu_support_present():
            return {
                "state": state,
                "action": "download",
                "label": f"Ladda ner GPU-stöd (~{gpu.DOWNLOAD_SIZE_GB} GB)",
                "run": self._download_gpu_async,
            }
        return {"state": state, "action": None}

    def _download_gpu_async(self, progress, done, error) -> None:
        if self._gpu_busy.is_set():
            error("En GPU-åtgärd pågår redan.")
            return
        self._gpu_busy.set()

        def work():
            try:
                gpu.download_gpu_libs(progress=progress)
                self._load_model(self.language, force=True)
                done("GPU-stöd installerat och modellen omladdad.")
            except Exception as e:
                log.exception("GPU-nedladdningen misslyckades")
                error(f"Misslyckades: {e}")
            finally:
                self._gpu_busy.clear()

        threading.Thread(target=work, daemon=True, name="viska-gpu-dl").start()

    def _remove_gpu_async(self, progress, done, error) -> None:
        if self._gpu_busy.is_set():
            error("En GPU-åtgärd pågår redan.")
            return
        self._gpu_busy.set()

        def work():
            try:
                if gpu.remove_gpu_libs():
                    self._load_model(self.language, force=True)
                    done("GPU-stöd borttaget – kör på CPU.")
                else:
                    done("Tas bort vid nästa start (CUDA används just nu).")
            except Exception as e:
                log.exception("Borttagningen misslyckades")
                error(f"Misslyckades: {e}")
            finally:
                self._gpu_busy.clear()

        threading.Thread(target=work, daemon=True, name="viska-gpu-rm").start()

    def _apply_settings(self, changes: dict):
        """Körs i huvudtråden (Spara-knappen). Returnerar felsträng eller None."""
        # Pågår en diktering avslutas den snyggt först — annars blir
        # sessionen föräldralös när tangenten byts ut (inspelningen skulle
        # aldrig gå att stoppa) och det som sagts hittills ginge förlorat.
        if self.hotkey.force_stop():
            self._on_stop()
        old = self.cfg
        if (
            changes["hotkey"] != old["hotkey"]
            or changes["hold_threshold_s"] != old["hold_threshold_s"]
        ):
            try:
                new_hotkey = None
                self.hotkey.stop()  # avregistrera innan samma/ny tangent tas
                new_hotkey = PushToTalk(
                    changes["hotkey"],
                    changes["hold_threshold_s"],
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
            changes["model"] != old["model"]
            or changes["model_en"] != old["model_en"]
            or changes["device"] != old["device"]
            or changes["compute_type"] != old["compute_type"]
            or changes["beam_size"] != old["beam_size"]
        )
        mic_changed = changes["keep_mic_open"] != old["keep_mic_open"]
        self.cfg.update(changes)
        save(self.cfg)
        self.sounds.enabled = bool(changes["sounds"])
        for t in self.transcribers.values():
            t.initial_prompt = changes["initial_prompt"]
        if mic_changed:
            self.recorder._keep_open = changes["keep_mic_open"]
            threading.Thread(target=self.recorder.restart, daemon=True).start()
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

    def _set_tray_recording(self, recording: bool) -> None:
        if self.icon is not None:
            try:
                self.icon.icon = _make_icon_image(recording)
            except Exception:
                pass  # kosmetiskt — får aldrig störa dikteringen

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


def _make_icon_image(recording: bool = False):
    from PIL import Image, ImageDraw

    color = "#e74c3c" if recording else "#e8eaed"
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((24, 6, 40, 36), radius=8, fill=color)
    d.arc((16, 18, 48, 50), start=0, end=180, fill=color, width=5)
    d.line((32, 50, 32, 57), fill=color, width=5)
    d.line((22, 57, 42, 57), fill=color, width=5)
    return img


def make_tray(app: App):
    import pystray

    def restart_audio(icon, item):
        threading.Thread(target=app.recorder.restart, daemon=True).start()

    def history_items():
        if not app.history:
            yield pystray.MenuItem("(tomt)", None, enabled=False)
            return
        for text in list(app.history):
            label = text if len(text) <= 45 else text[:44] + "…"
            yield pystray.MenuItem(
                label,
                (lambda t: lambda icon, item: copy_to_clipboard(t))(text),
            )

    menu = pystray.Menu(
        pystray.MenuItem(
            lambda item: f"Viska {__version__} – {app.status}", None, enabled=False
        ),
        pystray.MenuItem(
            lambda item: f"Hämta version {app.update_tag}",
            lambda icon, item: os.startfile(update.RELEASES_URL),
            visible=lambda item: app.update_tag is not None,
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
        pystray.MenuItem("Historik – klicka för att kopiera", pystray.Menu(history_items)),
        pystray.MenuItem(
            "Kopiera senaste diktering",
            lambda icon, item: copy_to_clipboard(app.last_text),
        ),
        pystray.MenuItem(
            "Inställningar…", lambda icon, item: app.request_settings()
        ),
        pystray.MenuItem("Byt till ny standardmikrofon", restart_audio),
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
    parser.add_argument(
        "--setup-gpu", action="store_true", help="ladda ner CUDA-biblioteken"
    )
    parser.add_argument(
        "--remove-gpu", action="store_true", help="ta bort nedladdade CUDA-bibliotek"
    )
    args = parser.parse_args()

    _setup_logging()
    gpu.cleanup_pending_removal()  # före första CUDA-laddningen
    first_run = not CONFIG_PATH.exists()
    cfg = load()

    if args.setup_gpu:
        gpu.download_gpu_libs(
            progress=lambda t, f: print(f"\r{t} {f:>4.0%}", end="", flush=True)
        )
        print("\nKlart – GPU-stödet används vid nästa start.")
        return
    if args.remove_gpu:
        if gpu.remove_gpu_libs():
            print("GPU-biblioteken borttagna.")
        else:
            print("Låsta av en körande Viska – tas bort vid nästa start.")
        return

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
    from viska import ui

    ui.refresh_theme()
    if first_run:
        from viska import firstrun

        firstrun.run(root)
    try:
        app = App(root, cfg)
    except HotkeyError as e:
        log.error("%s", e)
        from tkinter import messagebox

        messagebox.showerror("Viska", str(e))
        sys.exit(1)
    app.icon = make_tray(app)
    app._refresh_tray_menu()  # fånga status/uppdatering som hann före trayn
    log.info("Viska %s igång – tangent: %s", __version__, cfg["hotkey"])
    root.mainloop()


if __name__ == "__main__":
    main()
