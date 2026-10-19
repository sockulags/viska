"""Poleringsmotorerna Viska har i dag: den inbyggda modellen, Codex och
fjärrtjänster (Ollama, egen tjänst).

Samlade här, utanför dikteringsmotorn, eftersom de inte följer med när
dikteringen flyttar in i Referat: där sker polering via Referats egna
AI-leverantörer. Både flow-läget (via motorn) och F8 i Tk-appen går hit.
"""

import logging
import threading

from viska import codex, llm

log = logging.getLogger(__name__)


class Polishing:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._codex: codex.CodexPolisher | None = None
        self._polisher: llm.Polisher | None = None
        # Skapandet av poleringsmodellen serialiseras: både F8-flödet, flow-
        # lägets förladdning och själva flow-poleringen kan annars försöka
        # instansiera Polisher samtidigt.
        self._lock = threading.Lock()
        # Fjärrbackendens nåbarhet cachas: flow-beslutet vid start (live
        # eller inte) behöver veta om poleringen är tillgänglig, men ett
        # synkront HTTP-anrop på hotkey-tråden skulle fördröja "Spelar in"
        # upp till 2 s när tjänsten är nere. Cachen läses där och uppdateras
        # i bakgrunden.
        self.remote_reachable = False
        if llm.is_remote(cfg):
            self.refresh_reachable_async()

    @property
    def model_loaded(self) -> bool:
        return self._polisher is not None

    def polish(self, cfg: dict, text: str, style: str, lang: str = "sv") -> str:
        """All polering går genom den här: Codex, annan fjärrtjänst eller den
        inbyggda modellen. cfg skickas med så att Inställningarnas testknapp
        kan prova ett osparat val utan att starta en egen motor."""
        backend = cfg["polish_backend"]
        if backend == "codex":
            return self._get_codex().polish(
                text,
                style,
                lang,
                model=cfg.get("polish_codex_model"),
                effort=cfg.get("polish_codex_effort"),
            )
        if llm.is_remote(cfg):
            return llm.polish_remote(cfg, text, style, lang)
        polisher = self._get_polisher()
        polisher.ensure_loaded()
        return polisher.polish(text, style, lang)

    def _get_codex(self) -> codex.CodexPolisher:
        with self._lock:
            if self._codex is None:
                self._codex = codex.CodexPolisher(self.cfg)
            return self._codex

    def _get_polisher(self) -> llm.Polisher:
        with self._lock:
            if self._polisher is None:
                self._polisher = llm.Polisher(self.cfg)
            return self._polisher

    def warmup(self) -> None:
        """Börjar ladda in poleringsmodellen i bakgrunden när en flow-session
        startar, så att den är varm när dikteringen stoppas. Idempotent –
        ensure_loaded returnerar direkt om modellen redan är laddad."""
        backend = self.cfg["polish_backend"]
        if backend != "codex" and (llm.is_remote(self.cfg) or not llm.installed(self.cfg)):
            return

        def work():
            try:
                # Codex startar en process (~0,3 s) och de andra laddar en
                # modell i VRAM — båda är värda att ha gjort innan dikteringen
                # stoppas. Fjärrtjänster har inget att värma.
                if backend == "codex":
                    self._get_codex().ensure_loaded()
                else:
                    self._get_polisher().ensure_loaded()
            except Exception:
                log.exception("Kunde inte förbereda poleringen")

        threading.Thread(
            target=work, daemon=True, name="viska-flow-warmup"
        ).start()

    def available(self) -> bool:
        """Icke-blockerande koll vid start om poleringen kan användas för flow
        (annars körs sessionen som vanlig diktering med liveinskrivning). För
        fjärrbackender läses den bakgrundsuppdaterade cachen — ett synkront
        HTTP-anrop på hotkey-tråden skulle fördröja "Spelar in". builtin är en
        billig filkoll."""
        if llm.is_remote(self.cfg):
            return self.remote_reachable
        return llm.installed(self.cfg)

    def refresh_reachable(self) -> None:
        try:
            self.remote_reachable = llm.remote_reachable(self.cfg)
        except Exception:
            self.remote_reachable = False

    def refresh_reachable_async(self) -> None:
        threading.Thread(target=self.refresh_reachable, daemon=True).start()

    def forget_model(self) -> None:
        """Poleringsmodellen tas bort: nästa polering skapar en ny."""
        self._polisher = None

    def close_codex(self) -> None:
        """Bytt bort från Codex: låt inte processen ligga kvar och ta minne."""
        codex_client, self._codex = self._codex, None
        if codex_client is not None:
            threading.Thread(target=codex_client.close, daemon=True).start()

    def close(self) -> None:
        if self._codex is not None:
            self._codex.close()
