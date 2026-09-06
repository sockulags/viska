#!/usr/bin/env python
# /// script
# requires-python = ">=3.11"
# dependencies = ["openai-codex>=0.147"]
# ///
"""Poleringstjänst för Viska ovanpå Codex-prenumerationen.

Viska talar bara OpenAI-formatets /v1/chat/completions och vet ingenting om
vad som står bakom. Den här servern är det som står bakom när man inte kan
köra en poleringsmodell lokalt: den håller en Codex-process varm och kör
varje omskrivning som en egen tråd i den.

    uv run tools/polish_server.py

Sedan i Viska: Inställningar → Avancerat → Poleringsbackend = "http".

Designval värda att känna till:

- EN CODEX-PROCESS, EN TRÅD PER ANROP. Processen (app-servern) är dyr att
  starta men billig att ha igång; en tråd är tvärtom billig att skapa men
  bär med sig hela sin historik. Två dikteringar har inget med varandra att
  göra, så varje anrop får en egen efemär tråd. Annars hade tionde
  poleringen släpat med sig nio tidigare mejl i kontexten — dyrare, tröge
  och med risk att modellen blandar ihop dem.

- ANVÄNDARENS EGEN CODEX-KONFIGURATION ÖVERSTYRS. En vanlig config.toml för
  kodning kan innehålla stora developer_instructions, en resonerande modell
  och en tillåtande sandlåda. Allt det skulle skickas med vid varje
  omskrivning av ett dikterat mejl. Därför sätts base_instructions,
  developer_instructions, modell, sandlåda och resonemangsnivå explicit här.

- INGA VERKTYG. Sandlådan är read-only och godkännanden nekas, så en modell
  som ändå försöker köra något får nej. Uppgiften är text in, text ut.

- FEL SKA SYNAS, INTE GÖMMAS. Slut kvot ger HTTP 429 med serverns egen
  förklaring, så Viska kan logga varför poleringen slutade fungera i stället
  för att bara tyst falla tillbaka på råtexten.
"""

import argparse
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("polish-server")

# Räcker för en diktering; längre än så är det inte en diktering.
MAX_BODY_BYTES = 256 * 1024

DEFAULT_INSTRUCTIONS = (
    "You are a text rewriting engine embedded in a dictation tool. "
    "You receive a system message describing the rewriting task and a user "
    "message containing dictated text. Apply the task and reply with the "
    "rewritten text only: no explanation, no preamble, no quotes around it, "
    "no tool use, no questions. Never invent facts, and never change dates, "
    "names or numbers."
)


class PolishBackend:
    """Codex-klienten och dess livscykel. Trådsäker: Viska gör ett anrop i
    taget, men hälsokollen kan komma parallellt."""

    def __init__(self, model: str, effort: str, service_tier: str | None):
        self.model = model
        self.effort = effort
        self.service_tier = service_tier
        self._codex = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Startar app-servern nu i stället för vid första dikteringen —
        annars betalar den första poleringen för processtarten."""
        self._ensure_client()

    def _ensure_client(self):
        from openai_codex import Codex

        with self._lock:
            if self._codex is None:
                log.info("Startar Codex app-server…")
                t0 = time.perf_counter()
                self._codex = Codex()
                log.info("Codex redo på %.2f s", time.perf_counter() - t0)
            return self._codex

    def close(self) -> None:
        with self._lock:
            codex, self._codex = self._codex, None
        if codex is not None:
            try:
                codex.close()
            except Exception:
                log.exception("Kunde inte stänga Codex-klienten")

    def polish(self, system_prompt: str, text: str, model: str | None = None) -> str:
        from openai_codex import ApprovalMode, Sandbox

        codex = self._ensure_client()
        # Uppgiften från Viska blir trådens developer_instructions och
        # ersätter därmed användarens egna — se modulens huvud.
        thread = codex.thread_start(
            model=model or self.model,
            sandbox=Sandbox.read_only,
            approval_mode=ApprovalMode.deny_all,
            base_instructions=DEFAULT_INSTRUCTIONS,
            developer_instructions=system_prompt,
            ephemeral=True,
            service_tier=self.service_tier,
        )
        result = thread.run(text, effort=self.effort)
        status = _turn_status(result)
        if status != "completed":
            raise BackendError(
                f"Codex avslutade turen som {status}: "
                f"{getattr(result, 'error', '') or 'ingen förklaring'}"
            )
        answer = (getattr(result, "final_response", "") or "").strip()
        if not answer:
            raise BackendError("Codex svarade utan text")
        return answer


class FakeBackend:
    """--fake: kör hela vägen Viska → HTTP → svar utan att röra kvoten.
    Används för att prova inkopplingen, aldrig för riktig polering."""

    def __init__(self, model: str, *_a, **_kw):
        self.model = model

    def start(self) -> None:
        log.warning("Kör i FAKE-läge: ingen riktig modell anropas")

    def close(self) -> None:
        pass

    def polish(self, system_prompt: str, text: str, model: str | None = None) -> str:
        return " ".join(text.split()).capitalize()


class BackendError(RuntimeError):
    pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    backend: object = None
    token: str = ""

    # -- HTTP ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/health":
            self._send(404, {"error": {"message": "okänd väg"}})
            return
        if not self._authorized():
            return
        self._send(
            200,
            {
                "status": "ok",
                "backend": type(self.backend).__name__,
                "model": self.backend.model,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": "okänd väg"}})
            return
        if not self._authorized():
            return
        try:
            payload = self._read_json()
        except ValueError as e:
            self._send(400, {"error": {"message": str(e)}})
            return

        system_prompt, text = _split_messages(payload.get("messages") or [])
        if not text:
            self._send(400, {"error": {"message": "ingen text att polera"}})
            return
        if payload.get("stream"):
            # Strömning finns inte än; säg det rakt ut i stället för att
            # skicka ett svar klienten inte kan tolka.
            self._send(400, {"error": {"message": "stream stöds inte"}})
            return

        t0 = time.perf_counter()
        try:
            answer = self.backend.polish(system_prompt, text, payload.get("model"))
        except Exception as e:  # noqa: BLE001
            status, message = _classify(e)
            log.warning("Polering misslyckades (%s): %s", status, message)
            self._send(status, {"error": {"message": message, "type": "backend"}})
            return
        ms = (time.perf_counter() - t0) * 1000
        log.info("Polerade %d → %d tecken på %.0f ms", len(text), len(answer), ms)
        self._send(
            200,
            {
                "object": "chat.completion",
                "model": payload.get("model") or self.backend.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "viska": {"duration_ms": round(ms)},
            },
        )

    # -- Internt ---------------------------------------------------------------

    def _authorized(self) -> bool:
        if not self.token:
            return True
        header = self.headers.get("Authorization", "")
        if header.strip() == f"Bearer {self.token}":
            return True
        self._send(401, {"error": {"message": "fel eller saknad nyckel"}})
        return False

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("ogiltig Content-Length") from None
        if length <= 0:
            raise ValueError("tom förfrågan")
        if length > MAX_BODY_BYTES:
            raise ValueError("förfrågan är för stor")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"ogiltig JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError("förväntade ett JSON-objekt")
        return data

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        log.debug(fmt, *args)


def _turn_status(result) -> str:
    """Turens status som ren sträng.

    SDK:n returnerar en enum (TurnStatus.completed), inte "completed" — en
    rak strängjämförelse gör att varje LYCKAD tur ser ut som ett fel.
    """
    status = getattr(result, "status", None)
    return str(getattr(status, "value", status) or "completed")


def _split_messages(messages: list) -> tuple[str, str]:
    """OpenAI-formatets meddelanden → (uppgift, text). Sista user-meddelandet
    är dikteringen; system-meddelandet är poleringsuppgiften från Viska."""
    system, user = [], []
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        if m.get("role") == "system":
            system.append(content)
        elif m.get("role") == "user":
            user.append(content)
    return "\n\n".join(system).strip(), (user[-1].strip() if user else "")


def _classify(e: Exception) -> tuple[int, str]:
    """Kvotstopp ska nå Viskas logg som just kvotstopp — det är skillnaden
    mellan "tjänsten är trasig" och "vänta till imorgon"."""
    message = str(e) or e.__class__.__name__
    lowered = message.lower()
    if "usage limit" in lowered or "quota" in lowered or "rate limit" in lowered:
        return 429, message
    return 502, message


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OpenAI-kompatibel poleringstjänst ovanpå Codex."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8756)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument(
        "--effort",
        default="none",
        help="resonemangsnivå; none är det som gör skillnad på latensen",
    )
    parser.add_argument("--service-tier", default=None)
    parser.add_argument(
        "--token", default="", help="krävs som Bearer-nyckel om den sätts"
    )
    parser.add_argument(
        "--fake", action="store_true", help="svara utan att anropa Codex"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "Lyssnar på %s — tjänsten har ingen kryptering, kör bara lokalt "
            "om du inte vet precis vad du gör",
            args.host,
        )

    backend_cls = FakeBackend if args.fake else PolishBackend
    backend = backend_cls(args.model, args.effort, args.service_tier)
    try:
        backend.start()
    except Exception:
        log.exception("Kunde inte starta backenden")
        return 1

    Handler.backend = backend
    Handler.token = args.token
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    log.info(
        "Lyssnar på http://%s:%d/v1/chat/completions (modell %s, effort %s)",
        args.host, args.port, args.model, args.effort,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Avslutar…")
    finally:
        server.server_close()
        backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
