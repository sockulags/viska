"""Viska – lokal diktering för Windows. Huvudprogram, tray och CLI."""

import argparse
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import urllib.error
from pathlib import Path

from viska import __version__, autostart, codex, gpu, llm, mics, update
from viska.config import (
    CONFIG_DIR,
    CONFIG_PATH,
    LOG_PATH,
    delete_history,
    load,
    save,
    save_history,
)
from viska.engine import DictationEngine, Feedback
from viska.focus import (
    focus_window,
    foreground_app,
    foreground_is_terminal,
    foreground_window,
)
from viska.correct import CorrectionWindow
from viska.hotkey import ActionKey, HotkeyError, create_push_to_talk
from viska.inject import (
    copy_to_clipboard,
    grab_selection,
    inject,
    restore_original_clipboard,
)
from viska.overlay import BLUE, GRAY, GREEN, RED, Overlay, ToneMenu
from viska.polishing import Polishing
from viska.settings import SettingsWindow
from viska.transcribe import Transcriber

log = logging.getLogger("viska")

# Poleringstangenten: två tryck inom det här fönstret öppnar tonvalsmenyn i
# stället för att polera direkt. Ett enkeltryck fördröjs så länge (och bara så
# länge) så trycken kan skiljas åt — ingen annan extra fördröjning.
POLISH_DOUBLE_TAP_S = 0.4

class TkFeedback(Feedback):
    """Gör statusruta och trayikon av dikteringsmotorns meddelanden."""

    def __init__(self, app: "App"):
        self.app = app

    @property
    def overlay(self) -> Overlay:
        return self.app.overlay

    def status(self, text: str) -> None:
        self.app.status = text
        self.app._refresh_tray_menu()

    def history_changed(self) -> None:
        self.app._refresh_tray_menu()

    def recording(self, active: bool) -> None:
        self.app._set_tray_recording(active)

    def model_loading(self, language: str) -> None:
        self.overlay.show(
            f"Viska: laddar {language} modell (första gången laddas den ner)…",
            GRAY,
        )

    def model_ready(self, device: str) -> None:
        self.overlay.show(
            f"Viska redo ({device.upper()}) – tryck "
            f"{self.app.cfg['hotkey'].upper()} och prata",
            GREEN,
            auto_hide_ms=3500,
        )

    def model_failed(self) -> None:
        self.overlay.show(
            "Viska: modellen kunde inte laddas – se loggen", RED, auto_hide_ms=8000
        )

    def not_ready(self) -> None:
        self.overlay.show(
            "Viska: modellen laddas fortfarande…", GRAY, auto_hide_ms=1500
        )

    def busy(self) -> None:
        self.overlay.show(
            "Viska: vänta – förra dikteringen skrivs ut…", GRAY, auto_hide_ms=1200
        )

    def mic_failed(self) -> None:
        self.overlay.show("Viska: mikrofonfel – se loggen", RED, auto_hide_ms=4000)

    def connecting(self) -> None:
        self.overlay.show("Ansluter headsetets mikrofon …", GRAY)

    def listening(self) -> None:
        self.overlay.show("Spelar in", RED, meter=True, timer=True)

    def partial(self, text: str) -> None:
        if len(text) > 70:
            text = "…" + text[-70:]
        self.overlay.show(f"● {text}", RED)

    def transcribing(self) -> None:
        self.overlay.show("Transkriberar", BLUE)

    def nothing_heard(self) -> None:
        self.overlay.show("(inget tal uppfattades)", GRAY, auto_hide_ms=1500)

    def idle(self) -> None:
        self.overlay.hide()

    def failed(self) -> None:
        self.overlay.show(
            "Viska: fel vid transkribering – se loggen", RED, auto_hide_ms=4000
        )

    def polishing(self, insert_raw, cancel) -> None:
        self.overlay.show(
            "Polerar",
            BLUE,
            actions=[("Infoga råtext", insert_raw), ("Avbryt", cancel)],
            timer=True,
        )

    def inserting(self) -> None:
        self.overlay.show("Skriver in", BLUE)

    def flow_done(self, message: str) -> None:
        self.overlay.show(message, GREEN, auto_hide_ms=1600)

    def notice(self, message: str) -> None:
        self.overlay.show(message, GRAY, auto_hide_ms=2500)


class App:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.cfg = cfg
        self.root = root
        self.icon = None  # sätts av make_tray
        self.status = "startar…"
        self.polishing = Polishing(cfg)
        self.engine = DictationEngine(cfg, TkFeedback(self), self.polishing)
        self.engine.take_over_key = (
            lambda: self.hotkey is not None and self.hotkey.force_stop()
        )
        self.overlay = Overlay(root, level_source=lambda: self.recorder.level)
        self.tone_menu = ToneMenu(root)
        self.update_tag: str | None = None
        self.update_release: update.Release | None = None
        # Spärr mot att den inbyggda uppdateraren startas flera gånger (dubbel-
        # klick på hämta-raden, bekräftelsedialog redan öppen).
        self._updating = threading.Event()
        self._quit = threading.Event()
        # Tkinter är huvudtrådsbundet; trayn lägger UI-jobb här i stället.
        self._ui_queue: queue.Queue = queue.Queue()
        self._settings: SettingsWindow | None = None
        # En GPU-åtgärd (nedladdning/borttagning) i taget — annars kan ett
        # återöppnat Inställningar-fönster starta en andra nedladdning som
        # raderar och skriver om samma filer som den första.
        self._gpu_busy = threading.Event()
        self._llm_busy = threading.Event()
        # Tangentkonflikter (annat program äger tangenten) ska aldrig hindra
        # appen från att starta — men trayikonen finns inte förrän main()
        # kört klart make_tray(), så notiserna köas här och flushas därifrån.
        self._startup_notices: list[str] = []
        # PushToTalk eller MouseTrigger beroende på bindningen (se
        # create_push_to_talk); båda har samma håll/växla-kontrakt.
        self.hotkey = None
        self.polish_key = None
        self.polish_menu_key = None
        self._polishing = threading.Event()
        # Dikteringstangenten, poleringstangenten och tonvalsmenyns tangent
        # registreras i ett svep. En upptagen tangent får aldrig hindra
        # appen från att starta — notisen köas till trayikonen finns.
        for notice in self._bind_keys(cfg, keep_on_error=True):
            log.warning("Tangenten kunde inte registreras: %s", notice)
            self._startup_notices.append(notice)
        self.engine.load_model_async(self.language)
        if cfg["check_updates"]:
            update.check_async(
                __version__, self._on_update_available, token=cfg.get("github_token", "")
            )
        self._poll_quit()

    # Motorns tillstånd, läst av trayn, inställningarna och F8-poleringen.
    @property
    def recorder(self):
        return self.engine.recorder

    @property
    def sounds(self):
        return self.engine.sounds

    @property
    def history(self):
        return self.engine.history

    @property
    def transcribers(self):
        return self.engine.transcribers

    @property
    def language(self) -> str:
        return self.engine.language

    @property
    def last_text(self) -> str:
        return self.engine.last_text

    def set_language(self, lang: str) -> None:
        self.engine.set_language(lang)

    # -- Tangentbindningar ---------------------------------------------------------

    def _stop_keys(self) -> None:
        """Släpper alla registreringar. Måste ske innan nya sätts: annars går
        det inte att byta plats på två tangenter."""
        for attr in ("hotkey", "polish_key", "polish_menu_key"):
            key = getattr(self, attr, None)
            if key is not None:
                key.stop()
            setattr(self, attr, None)

    def _bind_keys(self, cfg: dict, keep_on_error: bool = False) -> list[str]:
        """Registrerar de tre tangenterna enligt cfg. Returnerar felmeddelanden
        för dem som inte gick att ta.

        keep_on_error=True (vid start) behåller det som lyckades och rapporterar
        resten; anroparen som byter tangenter vill i stället rulla tillbaka och
        skickar därför False, kollar returlistan och återställer själv.

        Tonvalsmenyn har en egen tangent i stället för dubbeltryck på
        poleringstangenten: ett dubbeltrycksfönster måste fördröja varje
        enkeltryck lika länge för att kunna skilja dem åt, vilket sköt upp all
        polering 0,4 s. Dubbeltrycket finns kvar som opt-in (polish_double_tap)
        och slår då automatiskt av menytangenten — annars fanns två vägar till
        samma meny och den ena kostade latens.
        """
        errors: list[str] = []
        double_tap = bool(cfg.get("polish_double_tap"))
        try:
            self.hotkey = create_push_to_talk(
                cfg["hotkey"],
                cfg["hold_threshold_s"],
                self.engine.start,
                self.engine.stop,
            )
        except HotkeyError as e:
            errors.append(str(e))
            if not keep_on_error:
                return errors
        try:
            self.polish_key = ActionKey(
                cfg["polish_hotkey"],
                self._on_polish_key,
                on_double=self._on_polish_menu if double_tap else None,
                double_tap_s=POLISH_DOUBLE_TAP_S,
            )
        except HotkeyError as e:
            # Polering är ett tillval — appen ska fungera ändå.
            errors.append(str(e))
            if not keep_on_error:
                return errors
        menu_key = (cfg.get("polish_menu_hotkey") or "").strip()
        if menu_key and not double_tap:
            try:
                self.polish_menu_key = ActionKey(menu_key, self._on_polish_menu)
            except HotkeyError as e:
                errors.append(str(e))
                if not keep_on_error:
                    return errors
        return errors

    def flush_startup_notices(self) -> None:
        """Visar traynotiser för tangenter som inte gick att registrera vid
        start. Kan inte ske i __init__ — trayikonen skapas först efter att
        App(...) returnerat (se main())."""
        if self.icon is None:
            return
        for msg in self._startup_notices:
            try:
                self.icon.notify(msg, "Viska")
            except Exception:
                pass  # tray-notiser är kosmetiska, felet finns redan i loggen
        self._startup_notices.clear()

    def _on_update_available(self, release: "update.Release") -> None:
        # Idempotent: en omkontroll (t.ex. efter att en token satts i
        # Inställningar) får inte lägga till statussuffixet en gång till.
        if self.update_release is not None and self.update_release.tag == release.tag:
            return
        self.update_release = release
        self.update_tag = release.tag
        self.status += f" · ny version {release.tag} finns"
        self._refresh_tray_menu()

    # -- Inbyggd uppdaterare (hämta-raden i traymenyn) ------------------------------

    def _on_update_clicked(self) -> None:
        """Klick på hämta-raden. Körs på pystray-tråden. I dev-läge behålls
        dagens beteende (öppna releases-sidan); i fryst exe-läge lämnas
        bekräftelsedialogen till huvudtråden (tkinter är huvudtrådsbundet)."""
        if not getattr(sys, "frozen", False):
            self._open_releases_page()
            return
        self._ui_queue.put(self._confirm_update)

    def _confirm_update(self) -> None:
        """Huvudtråden: bekräfta och starta nedladdningen i bakgrunden. Finns
        ingen matchande zip-asset öppnas releases-sidan direkt (ingen omstart
        att bekräfta)."""
        if self._updating.is_set():
            return
        release = self.update_release
        if release is None:
            self._open_releases_page()
            return
        install_dir = Path(sys.executable).parent
        prefer_gpu = (install_dir / "_internal" / "nvidia").is_dir()
        asset = update.select_asset(release, prefer_gpu)
        if asset is None:
            log.info(
                "Ingen matchande zip-asset för %s – öppnar releases-sidan",
                release.tag,
            )
            self._open_releases_page()
            return
        # Sätt spärren INNAN dialogen: en modal messagebox kör en egen
        # händelseloop, så after-pollen kan hinna köa ett andra _confirm_update
        # och öppna en andra dialog utan spärren. Släpps igen vid Nej.
        self._updating.set()
        from tkinter import messagebox

        ok = messagebox.askyesno(
            "Viska",
            f"Uppdatera till {release.tag}? Viska startas om.",
            parent=self.root,
        )
        if not ok:
            self._updating.clear()
            return
        threading.Thread(
            target=self._run_update,
            args=(release, asset),
            daemon=True,
            name="viska-update-install",
        ).start()

    def _run_update(self, release: "update.Release", asset: dict) -> None:
        """Bakgrundstråd: ladda ner, packa upp, validera, skriv och starta
        uppdaterings-baten, och avsluta appen för omstart. Varje steg före
        bat-starten är rent additivt (temp-filer) — misslyckas något rörs
        installationen aldrig och vi faller tillbaka på releases-sidan."""
        try:
            install_dir = Path(sys.executable).parent
            # Sanity: rör inget om vi inte känner igen installationskatalogen.
            if not (install_dir / "Viska.exe").is_file():
                raise RuntimeError(
                    f"Installationskatalogen saknar Viska.exe: {install_dir}"
                )
            work = update.fresh_workdir()
            zip_path = work / asset["name"]
            self.overlay.show(f"Hämtar {release.tag}…", BLUE)
            last_pct = [-1]

            def progress(frac: float) -> None:
                # Töm inte overlay-kön i onödan: uppdatera bara vid ny procent.
                pct = int(frac * 100)
                if pct != last_pct[0]:
                    last_pct[0] = pct
                    self.overlay.show(f"Hämtar {release.tag}… {pct} %", BLUE)

            try:
                update.download_asset(
                    asset,
                    zip_path,
                    token=self.cfg.get("github_token", ""),
                    progress=progress,
                )
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404):
                    log.info(
                        "Uppdatering: asset-nedladdning nekades (HTTP %s) – "
                        "privat repo utan giltig token? Faller tillbaka på "
                        "releases-sidan.",
                        e.code,
                    )
                    self._update_fallback()
                    return
                raise
            self.overlay.show("Packar upp uppdateringen…", BLUE)
            app_dir = update.extract_update(zip_path, work / "extracted")
            if app_dir is None:
                raise RuntimeError("Uppackningen saknade Viska.exe eller _internal/")
            bat = update.write_updater_bat(os.getpid(), app_dir, install_dir, work)
            update.launch_updater(bat)
            log.info(
                "Uppdatering till %s startad – avslutar Viska för omstart",
                release.tag,
            )
            self.overlay.show(
                f"Installerar {release.tag} – Viska startas om…", GREEN
            )
            # Avsluta via huvudtråden (samma väg som Avsluta-menyvalet). Baten
            # väntar på att vår PID försvinner innan den rör installationen.
            self._ui_queue.put(self.quit)
        except Exception:
            log.exception(
                "Den inbyggda uppdateringen misslyckades – faller tillbaka "
                "på releases-sidan"
            )
            self._update_fallback()

    def _update_fallback(self) -> None:
        """Släpp spärren och öppna releases-sidan i webbläsaren, precis som
        det icke frysta läget och äldre versioner gör."""
        self._updating.clear()
        self.overlay.show(
            "Uppdateringen misslyckades – öppnar hämtningssidan",
            GRAY,
            auto_hide_ms=5000,
        )
        self._open_releases_page()

    def _open_releases_page(self) -> None:
        try:
            os.startfile(update.RELEASES_URL)
        except Exception:
            log.exception("Kunde inte öppna releases-sidan")

    def _refresh_tray_menu(self) -> None:
        # pystrays Windows-backend bygger menyn EN gång och uppdaterar den
        # bara vid klick — dynamiskt innehåll (historik, uppdateringsrad,
        # status) måste trigga en ombyggnad själv.
        if self.icon is not None:
            try:
                self.icon.update_menu()
            except Exception:
                pass  # kosmetiskt

    # -- Rätta senaste dikteringen (bygger ersättningsregler) -----------------------

    def request_correction(self) -> None:
        """Trayn kör i egen tråd; Tk måste öppnas från huvudtråden."""
        self._ui_queue.put(self._open_correction)

    def _open_correction(self) -> None:
        if not self.last_text:
            self.overlay.show(
                "Viska: ingen diktering att rätta än", GRAY, auto_hide_ms=2000
            )
            return
        CorrectionWindow(
            self.root, self.last_text, dict(self.cfg["replacements"]),
            on_save=self._save_replacements,
        )

    def _save_replacements(self, replacements: dict) -> None:
        self.cfg["replacements"] = replacements
        save(self.cfg)
        log.info("Ersättningsregler sparade (%d st)", len(replacements))

    # -- Inställningar ---------------------------------------------------------------

    def request_settings(self) -> None:
        """Trådsäker: be huvudtråden öppna inställningsfönstret."""
        self._ui_queue.put(self._open_settings)

    def _open_settings(self) -> None:
        if self._settings is not None and self._settings.win.winfo_exists():
            self._settings.win.lift()
            return
        # Ingen nåbarhetskoll här. Den är ett HTTP-anrop med 2 s timeout, och
        # på huvudtråden innebar det att en avstängd poleringsserver frös hela
        # inställningsfönstret innan det ens visades. Fjärrbackender läser i
        # stället den cachade statusen; fönstret kollar sedan anslutningen i
        # bakgrunden och uppdaterar sin egen statusrad. builtin är en billig
        # filkoll och görs direkt.
        if llm.is_remote(self.cfg):
            polish_ready = self.polishing.remote_reachable
            self.polishing.refresh_reachable_async()
        else:
            polish_ready = llm.installed(self.cfg)
        self._settings = SettingsWindow(
            self.root,
            self.cfg,
            self._apply_settings,
            gpu_panel=self._gpu_panel(),
            polish_panel=self._polish_panel(llm.installed_local(self.cfg)),
            polish_ready=polish_ready,
            # Testknappen använder appens egen poleringsväg — då provas exakt
            # det som kommer att köras, och Codex-processen som redan är varm
            # återanvänds i stället för att en andra startas.
            polish_fn=self.polishing.polish,
        )

    def _polish_panel(self, polish_ready: bool) -> dict:
        if self._llm_busy.is_set():
            return {"state": "Åtgärd pågår…", "kind": "warn", "action": None}
        # Panelen gäller den INBYGGDA motorn (nedladdning/borttagning).
        # Fjärrbackendernas status hör hemma i deras egna block i
        # Inställningar, som kollar anslutningen utan att blockera.
        key = (self.cfg["polish_hotkey"] or "f8").upper()
        if polish_ready:
            return {
                "state": f"installerad – markera text och tryck {key}",
                "kind": "ok",
                "action": "remove",
                "label": "Ta bort poleringen",
                "run": self._remove_llm_async,
            }
        return {
            "state": "ej installerad",
            "kind": "warn",
            "action": "download",
            "label": f"Ladda ner polering (~{llm.download_size_gb(self.cfg)} GB)",
            "run": self._download_llm_async,
        }

    def _download_llm_async(self, progress, done, error) -> None:
        if self._llm_busy.is_set():
            error("En åtgärd pågår redan.")
            return
        self._llm_busy.set()

        def work():
            try:
                llm.download_all(self.cfg, progress=progress)
                # Poleringen delar nu VRAM med Whisper (polish_shares_gpu
                # blir sann) – ladda om transcribern så den VRAM-medvetna
                # kvantiseringen (transcribe._auto_cuda_choice) hinner slå
                # till nu i stället för att vänta till nästa omstart.
                self.engine.load_model(self.language, force=True)
                done(f"Polering installerad – markera text och tryck "
                     f"{self.cfg['polish_hotkey'].upper()}.")
            except Exception as e:
                log.exception("Nedladdningen av poleringsmodellen misslyckades")
                error(f"Misslyckades: {e}")
            finally:
                self._llm_busy.clear()

        threading.Thread(target=work, daemon=True, name="viska-llm-dl").start()

    def _remove_llm_async(self, progress, done, error) -> None:
        if self._llm_busy.is_set():
            error("En åtgärd pågår redan.")
            return
        self._llm_busy.set()

        def work():
            try:
                self.polishing.forget_model()
                if llm.remove_model(self.cfg):
                    # Poleringen delar inte längre VRAM med Whisper – ladda
                    # om så den VRAM-medvetna kvantiseringen kan släppa
                    # int8_float16 och gå tillbaka till full kvalitet direkt.
                    self.engine.load_model(self.language, force=True)
                    done("Poleringsmodellen borttagen.")
                else:
                    done("Tas bort vid nästa start (modellen är laddad just nu).")
            except Exception as e:
                error(f"Misslyckades: {e}")
            finally:
                self._llm_busy.clear()

        threading.Thread(target=work, daemon=True, name="viska-llm-rm").start()

    def _gpu_panel(self) -> dict:
        if self._gpu_busy.is_set():
            return {"state": "GPU-åtgärd pågår…", "kind": "warn", "action": None}
        t = self.transcribers.get(self.language)
        if t is not None and t.device == "cuda":
            state = "aktivt (CUDA)"
        elif gpu.has_nvidia_gpu():
            state = "NVIDIA-GPU hittad men GPU-läget är inaktivt"
        else:
            return {"state": "ingen NVIDIA-GPU hittad", "kind": "warn", "action": None}
        kind = "ok" if "aktivt" in state else "warn"
        if gpu.downloaded_libs_installed():
            return {
                "state": state,
                "kind": kind,
                "action": "remove",
                "label": "Ta bort GPU-stöd",
                "run": self._remove_gpu_async,
            }
        if t is not None and t.device == "cuda":
            # CUDA körs redan via systemets/pip:ens bibliotek —
            # en nedladdning vore meningslös.
            return {
                "state": state + " – via systemets CUDA",
                "kind": "ok",
                "action": None,
            }
        if not gpu.gpu_support_present():
            return {
                "state": state,
                "kind": kind,
                "action": "download",
                "label": f"Ladda ner GPU-stöd (~{gpu.DOWNLOAD_SIZE_GB} GB)",
                "run": self._download_gpu_async,
            }
        return {"state": state, "kind": kind, "action": None}

    def _download_gpu_async(self, progress, done, error) -> None:
        if self._gpu_busy.is_set():
            error("En GPU-åtgärd pågår redan.")
            return
        self._gpu_busy.set()

        def work():
            try:
                gpu.download_gpu_libs(progress=progress)
                self.engine.load_model(self.language, force=True)
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
                    self.engine.load_model(self.language, force=True)
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
        if self.hotkey is not None and self.hotkey.force_stop():
            self.engine.stop()
        # Autostart är tillstånd (Startup-genvägen), inte config.
        wants_autostart = changes.pop("autostart", None)
        if wants_autostart is not None and wants_autostart != autostart.enabled():
            try:
                autostart.install() if wants_autostart else autostart.uninstall()
            except Exception:
                log.exception("Kunde inte ändra autostart")
                return "Kunde inte ändra Starta med Windows – se loggen"
        old = self.cfg
        if changes["hotkey"] == changes["polish_hotkey"]:
            return "Diktering och polering kan inte ha samma tangent."
        keys_changed = any(
            changes.get(k) != old.get(k)
            for k in (
                "hotkey",
                "hold_threshold_s",
                "polish_hotkey",
                "polish_menu_hotkey",
                "polish_double_tap",
            )
        )
        if keys_changed:
            # Atomiskt byte: släpp ALLA registreringar först — annars går det
            # inte att byta plats på två tangenter — ta de nya, och rulla
            # tillbaka till de gamla om någon inte går att ta.
            self._stop_keys()
            errors = self._bind_keys(changes)
            if errors:
                log.warning("Tangentbyte misslyckades: %s", errors[0])
                if self.icon is not None:
                    try:
                        self.icon.notify(errors[0], "Viska")
                    except Exception:
                        pass  # tray-notiser är kosmetiska
                self._stop_keys()
                for failed in self._bind_keys(old, keep_on_error=True):
                    log.error("Kunde inte återta den gamla tangenten: %s", failed)
                return errors[0]
        model_changed = (
            changes["model"] != old["model"]
            or changes["model_en"] != old["model_en"]
            or changes["device"] != old["device"]
            or changes["compute_type"] != old["compute_type"]
            or changes["beam_size"] != old["beam_size"]
            # polish_backend avgör polish_shares_gpu (bara "builtin" delar
            # VRAM med Whisper) – ett byte här kan därför ändra den
            # VRAM-medvetna kvantiseringen precis som ett modellbyte.
            or changes["polish_backend"] != old["polish_backend"]
        )
        mic_changed = (
            changes["mic_mode"] != old["mic_mode"]
            or changes["mic_idle_close_s"] != old["mic_idle_close_s"]
        )
        history_persist_changed = changes["history_persist"] != old["history_persist"]
        # Fångas före update(): en ny token gör att uppdateringskollen mot ett
        # privat repo kan lyckas — kör om den nu så hämta-raden dyker upp utan
        # omstart (annars först vid nästa start).
        token_changed = changes.get("github_token", "") != old.get("github_token", "")
        self.cfg.update(changes)
        save(self.cfg)
        if history_persist_changed:
            if changes["history_persist"]:
                # Slås på: spara det som redan finns i RAM direkt, annars
                # ser filen tom ut tills nästa diktering ändrar historiken.
                try:
                    save_history(self.history)
                except OSError:
                    log.exception("Kunde inte spara historiken till disk")
            else:
                try:
                    delete_history()
                except OSError:
                    log.exception("Kunde inte radera historikfilen")
        # Ollama-nåbarheten cachas (flow-beslutet på hotkey-tråden läser
        # cachen i stället för att blocka på nätverket, se __init__). Byter
        # man hit till Ollama-backend måste cachen räknas om NU — annars läser
        # den första flow-dikteringen den gamla flaggan (typiskt False sedan
        # appstart med builtin-backend) och körs som vanlig liveinskrivning
        # trots att Ollama svarar; först nästa diktering hann annars uppdatera
        # cachen. Sker i bakgrunden (2 s HTTP-timeout) så Spara aldrig blockeras.
        if llm.is_remote(self.cfg):
            self.polishing.refresh_reachable_async()
        if token_changed and self.cfg["check_updates"] and self.update_release is None:
            update.check_async(
                __version__,
                self._on_update_available,
                token=self.cfg.get("github_token", ""),
            )
        if changes["polish_backend"] != old["polish_backend"]:
            self.polishing.close_codex()
        self.sounds.enabled = bool(changes["sounds"])
        for t in self.transcribers.values():
            t.initial_prompt = changes["initial_prompt"]
        if mic_changed:
            # Egen tråd: att öppna eller stänga en WASAPI-ström tar hundratals
            # millisekunder och Spara-knappen ska inte frysa så länge.
            threading.Thread(
                target=self.recorder.set_mode,
                args=(changes["mic_mode"], changes["mic_idle_close_s"]),
                daemon=True,
            ).start()
        if changes["mic_avoid_bluetooth"] != old["mic_avoid_bluetooth"]:
            # En öppen ström behåller sin mikrofon; öppna om så att valet
            # gäller direkt och inte först vid nästa öppning.
            threading.Thread(target=self.recorder.restart, daemon=True).start()
        self.engine.language = changes["language"]
        if model_changed:
            # Byts inte synkront: pågående sessioner behåller sin referens,
            # nya modellen svajpas in under sessionslåset när den är klar.
            self.engine.load_model_async(self.language, force=True)
        elif self.language not in self.transcribers:
            self.engine.load_model_async(self.language)
        log.info("Inställningar sparade")
        return None

    # -- Polering (markera text, tryck tangenten) -----------------------------------

    def _on_polish_key(self) -> None:
        # Enkeltryck: polera direkt med den fokuserade appens ton (dagens
        # beteende). Fördröjs bara av dubbeltrycksfönstret i ActionKey.
        self._start_polish()

    def _on_polish_menu(self) -> None:
        """Dubbeltryck: öppna tonvalsmenyn nära pillen. Körs på hotkey-tråden
        och måste vara snabb — fånga målfönster och app NU (innan menyn tar
        fokus) och lämna själva menyn till huvudtråden via ToneMenu. Valet
        gäller bara den poleringen; ingen config ändras."""
        if self._polishing.is_set():
            # En polering pågår redan, ELLER tonvalsmenyn är redan öppen och
            # väntar på ett val. Utan den här spärren skulle ett extra
            # dubbeltryck (menyn har tagit tangentfokus) läsa menyns eget
            # fönster som målfönster, och ett extra enkeltryck starta en
            # parallell polering vars guard sedan tyst svalde menyvalet.
            return
        if foreground_is_terminal():
            # Ctrl+C utan markering blir SIGINT i konsoler — ingen meny där.
            self.overlay.show(
                "Polering stöds inte i terminalfönster", GRAY, auto_hide_ms=2500
            )
            return
        # Målfönstret måste fångas här: när menyn tagit fokus är förgrunden
        # menyn, inte appen markeringen finns i. _do_polish lämnar tillbaka
        # fokus dit innan markeringen läses.
        target_hwnd = foreground_window()
        fg = foreground_app()
        profile = self.cfg["app_profiles"].get(fg, {}) if fg else {}
        app_default = llm.resolve_polish_prompt(self.cfg, profile)
        lang = self.language
        # Markera upptaget REDAN NU — innan menyn ens hunnit visas. Då blockerar
        # guarden ovan varje ytterligare tryck så länge menyn står öppen; först
        # ett val (via _run_polish → _do_polish) eller ett avbrott (on_cancel)
        # släpper flaggan igen.
        self._polishing.set()

        def prepare():
            # llm.installed() kan för Ollama-backend vara ett synkront HTTP-
            # anrop (2 s timeout) — därför på egen tråd, aldrig på hotkey-
            # loopen. Samma tillgänglighetskoll som enkeltryck gör i _do_polish,
            # men här FÖRE menyn så beskedet blir detsamma direkt i stället för
            # först efter ett val.
            #
            # Hela kroppen är try/except-skyddad: _polishing sattes redan i
            # anroparen (rad ovan) och släpps här bara via on_choose→_do_polish
            # (finally) eller on_cancel. Kastar något oväntat INNAN menyn hunnit
            # visas — t.ex. en tillfälligt låst LLM_DIR under en pågående
            # modellborttagning — körs varken on_choose eller on_cancel, och då
            # skulle flaggan annars förbli satt för alltid och tyst blockera all
            # polering tills omstart. Fånga därför, släpp flaggan och säg till.
            try:
                if not llm.installed(self.cfg):
                    self._polishing.clear()
                    self.overlay.show(
                        "Polering ej installerad – Inställningar → Polering",
                        GRAY,
                        auto_hide_ms=3000,
                    )
                    return
                # Appens standard förmarkerad (style None = härleds i _do_polish,
                # exakt som enkeltryck); sedan mallbiblioteket i sin ordning som
                # tonval. Är biblioteket tomt faller vi tillbaka på de inbyggda
                # reservtonerna på aktivt språk.
                label = "Appens standard" if app_default else "Standard (allmän)"
                options = [(label, None, True)]
                templates = llm.polish_templates(self.cfg)
                if templates:
                    options += [
                        (t["namn"], t["prompt"], False) for t in templates
                    ]
                else:
                    options += [
                        (name, task, False) for name, task in llm.tone_presets(lang)
                    ]

                def on_choose(style):
                    # Menyval: _polishing är redan satt (togs när menyn öppnades),
                    # så gå via _run_polish som INTE guardar — annars skulle guarden
                    # i _start_polish tyst svälja valet.
                    self._run_polish(style, target_hwnd)

                def on_cancel():
                    # Menyn stängdes utan val (Esc, klick utanför eller timeout):
                    # släpp upptaget-flaggan så nästa tryck fungerar igen.
                    self._polishing.clear()

                self.tone_menu.show(options, on_choose, on_cancel)
            except Exception:
                # Släpp flaggan FÖRST så poleringen aldrig kan låsa sig, även om
                # själva aviseringen nedan också skulle krångla.
                self._polishing.clear()
                log.exception("Kunde inte öppna tonvalsmenyn")
                self.sounds.error()
                self.overlay.show(
                    "Polering misslyckades – se loggen", RED, auto_hide_ms=4000
                )

        threading.Thread(
            target=prepare, daemon=True, name="viska-polish-menu"
        ).start()

    def _start_polish(
        self, style: str | None = None, target_hwnd: int | None = None
    ) -> None:
        # Guardad ingång (enkeltryck): hoppa över om en polering pågår eller
        # tonvalsmenyn står öppen. Menyvalet går i stället via _run_polish.
        if self._polishing.is_set():
            return
        self._polishing.set()
        self._run_polish(style, target_hwnd)

    def _run_polish(
        self, style: str | None, target_hwnd: int | None
    ) -> None:
        """Startar själva poleringstråden. _polishing MÅSTE redan vara satt av
        anroparen (enkeltryck via _start_polish, eller ett menyval där flaggan
        togs redan när menyn öppnades). _do_polish nollställer den när den är
        klar."""
        threading.Thread(
            target=self._do_polish,
            args=(style, target_hwnd),
            daemon=True,
            name="viska-polish",
        ).start()

    def _do_polish(
        self, style: str | None = None, target_hwnd: int | None = None
    ) -> None:
        try:
            if not llm.installed(self.cfg):
                self.overlay.show(
                    "Polering ej installerad – Inställningar → Polering",
                    GRAY,
                    auto_hide_ms=3000,
                )
                return
            # Tog tonvalsmenyn fokus (dubbeltryck) lämnas det tillbaka till
            # fönstret markeringen finns i innan vi läser den. Enkeltryck
            # (target_hwnd None) rör aldrig fokus — ingen fokusstöld.
            if target_hwnd:
                focus_window(target_hwnd)
            if foreground_is_terminal():
                # Ctrl+C utan markering är SIGINT i konsoler — riskera
                # aldrig att döda användarens körande process.
                self.overlay.show(
                    "Polering stöds inte i terminalfönster", GRAY, auto_hide_ms=2500
                )
                return
            fg = foreground_app()
            profile = self.cfg["app_profiles"].get(fg, {}) if fg else {}
            if style is None:
                # Appens egen ton via den gemensamma upplösningen (mall/fritext
                # → global) — samma väg som flow och tonvalsmenyn väljer.
                style = llm.resolve_polish_prompt(self.cfg, profile)
            text = grab_selection()
            if not text or not text.strip():
                restore_original_clipboard()  # rensa ev. sparat original
                self.overlay.show("(ingen markering)", GRAY, auto_hide_ms=1500)
                return
            try:
                if llm.is_remote(self.cfg):
                    self.overlay.show(
                        f"Polerar via {llm.remote_label(self.cfg)}", BLUE
                    )
                elif not self.polishing.model_loaded:
                    self.overlay.show("Laddar poleringsmodellen…", GRAY)
                else:
                    self.overlay.show("Polerar", BLUE)
                result = self.polishing.polish(self.cfg, text, style, self.language)
            except Exception:
                log.exception("Poleringen misslyckades")
                restore_original_clipboard()
                self.sounds.error()
                self.overlay.show(
                    "Polering misslyckades – se loggen", RED, auto_hide_ms=4000
                )
                return
            # Originalet i historiken innan markeringen ersätts — en enkel
            # ångra-väg (traymenyn: klicka för att kopiera tillbaka det).
            self.engine.remember(text)
            inject(
                result,
                profile.get("inject_mode", self.cfg["inject_mode"]),
                self.cfg["restore_clipboard"],
            )
            self.engine.last_text = result
            self.engine.remember(result)  # polerat överst
            self._refresh_tray_menu()
            self.overlay.hide()
        finally:
            self._polishing.clear()

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
        self._stop_keys()
        self.polishing.close()
        self.engine.close()
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
            lambda icon, item: app._on_update_clicked(),
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
            "Rätta senaste dikteringen…",
            lambda icon, item: app.request_correction(),
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
    parser.add_argument(
        "--check-polish",
        nargs="?",
        const="",
        metavar="MOTOR",
        help="rapportera poleringsmotorns status och prova en omskrivning; "
        "ange builtin/codex/ollama/http för att prova en annan än den valda",
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
    llm.cleanup_pending_removal()
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
        for ep in mics.capture_endpoints():
            if ep.bluetooth:
                print(f"Bluetooth-headset: {ep.device}")
        cfg = load()
        print(mics.resolve(cfg["input_device"], cfg["mic_avoid_bluetooth"]).describe())
        return
    if args.install_autostart:
        print(f"Autostart installerad: {autostart.install()}")
        return
    if args.uninstall_autostart:
        print(
            "Autostart borttagen."
            if autostart.uninstall()
            else "Ingen autostart-genväg hittades."
        )
        return
    if args.check_polish is not None:
        if args.check_polish:
            cfg = {**cfg, "polish_backend": args.check_polish}
        backend = cfg["polish_backend"]
        print(f"Motor:   {backend}")
        if backend == "codex":
            ok, text = codex.status()
            print(f"Codex:   {text}")
            print(f"Modell:  {cfg['polish_codex_model']} (nivå {cfg['polish_codex_effort']})")
            if not ok:
                sys.exit(1)
        elif llm.is_remote(cfg):
            print(f"Tjänst:  {llm.remote_label(cfg)} – {llm.remote_model(cfg)}")
            print(f"Svarar:  {'ja' if llm.remote_reachable(cfg) else 'nej'}")
        else:
            print(f"Nedladdad: {'ja' if llm.installed_local(cfg) else 'nej'}")
        sample = "hej ehm kan vi flytta mötet på torsdag till fredag i stället"
        print(f"Provar:  {sample}")
        import time as _time

        started = _time.perf_counter()
        polishing = Polishing(cfg)
        try:
            out = polishing.polish(cfg, sample, llm.resolve_polish_prompt(cfg))
            print(f"Svar:    {out}")
            print(f"Tid:     {(_time.perf_counter() - started):.1f} s")
        except Exception as e:  # noqa: BLE001
            print(f"Fel:     {codex.friendly_error(e)}")
            sys.exit(1)
        finally:
            polishing.close()
        return
    if args.check or args.transcribe:
        lang = args.language or cfg["language"]
        model = cfg["model_en"] if lang == "en" else cfg["model"]
        t = Transcriber(
            model, cfg["device"], cfg["beam_size"], cfg["compute_type"],
            llm.polish_shares_gpu(cfg),
        )
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
    app.flush_startup_notices()  # tangentkonflikter upptäckta i App.__init__
    log.info("Viska %s igång – tangent: %s", __version__, cfg["hotkey"])
    root.mainloop()


if __name__ == "__main__":
    main()
