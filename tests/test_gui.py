"""Fönstren byggs och beter sig — med en påhittad poleringstjänst.

Kräver en fungerande Tk-display; annars hoppas hela filen över (så att en
körning över SSH eller i en tom container inte ser ut som ett fel).
"""

import json
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from viska import config
from viska.overlay import BLUE, Overlay
from viska.settings import _ENGINE_CHOICES, SettingsWindow

# Tkinters Variable.__del__ kan köras efter att dess master rivits när
# skräpsamlaren kommer ikapp mellan testerna. Det är ett upprivningsartefakt
# i Tk, inte något testerna kan påverka, och det säger inget om koden.
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError as e:  # pragma: no cover - beror på miljön
        pytest.skip(f"ingen Tk-display: {e}")
    window.withdraw()
    yield window
    window.destroy()


class _Stub(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._send(200, {"status": "ok"})

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._send(200, {"choices": [{"message": {"content": "Polerad mening."}}]})

    def log_message(self, *_a):
        pass


@pytest.fixture(scope="module")
def service_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def window(root, service_url):
    cfg = dict(config.DEFAULTS)
    cfg["polish_backend"] = "http"
    cfg["polish_http_url"] = f"{service_url}/v1/chat/completions"
    cfg["polish_http_command"] = "cmd /c echo hej"
    state = {"running": False, "command": None}
    service = {
        "running": lambda: state["running"],
        "start": lambda cmd: state.update(running=True, command=cmd) or None,
        "stop": lambda: state.update(running=False),
    }
    saved: dict = {}
    win = SettingsWindow(
        root, cfg, lambda changes: saved.update(changes) or None, service=service
    )
    root.update()
    yield win, saved, state
    if win.win.winfo_exists():
        win.win.destroy()


def _wait_for(root, predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        root.update()
        time.sleep(0.03)
    return predicate()


def test_fonstret_oppnar_utan_att_vanta_pa_tjansten(root, service_url):
    """Nåbarhetskollen får inte ligga på GUI-tråden: en tjänst som inte svarar
    tog annars 2 sekunder innan fönstret ens visades."""
    cfg = dict(config.DEFAULTS)
    cfg["polish_backend"] = "http"
    # Stängd port: kollen skulle blocka om den kördes synkront.
    cfg["polish_http_url"] = "http://127.0.0.1:1/v1/chat/completions"
    t0 = time.perf_counter()
    win = SettingsWindow(root, cfg, lambda _c: None)
    elapsed = time.perf_counter() - t0
    win.win.destroy()
    assert elapsed < 1.5


def test_bara_vald_motor_visas(window, root):
    win, _saved, _state = window
    assert _ENGINE_CHOICES[win._engine.get()] == "http"
    assert win._engine_blocks["http"].winfo_manager() == "pack"
    assert win._engine_blocks["builtin"].winfo_manager() == ""

    win._engine.set("Inbyggd modell (kräver GPU)")
    win._show_engine_block()
    root.update()
    assert win._engine_blocks["builtin"].winfo_manager() == "pack"
    assert win._engine_blocks["http"].winfo_manager() == ""


def test_anslutningsstatus_fylls_i_asynkront(window, root):
    win, _saved, _state = window
    status = win._remote_widgets["http"]["status"]
    assert _wait_for(root, lambda: "Ansluten" in status.cget("text")), status.cget(
        "text"
    )


def test_testpolering_visar_svar_och_svarstid(window, root):
    win, _saved, _state = window
    result = win._remote_widgets["http"]["result"]
    win._test_remote("http")
    assert _wait_for(root, lambda: "Polerad" in result.cget("text"))
    text = result.cget("text")
    # Punkten i den polerade texten ska vara kvar; bara sekunderna får komma.
    assert "Polerad mening." in text
    assert "Svarstid" in text and " s" in text


def test_tjansten_startas_och_stoppas(window, root):
    win, _saved, state = window
    win._toggle_service()
    root.update()
    assert state["running"] and state["command"] == "cmd /c echo hej"
    win._toggle_service()
    root.update()
    assert not state["running"]


def test_vardena_foljer_med(window):
    win, _saved, _state = window
    values = win.values()
    assert values["polish_backend"] == "http"
    assert values["polish_http_url"].endswith("/v1/chat/completions")
    assert values["polish_http_command"] == "cmd /c echo hej"
    assert values["polish_double_tap"] is False
    assert values["polish_menu_hotkey"] == "shift+f8"


def test_krockande_tangenter_avvisas(window):
    win, _saved, _state = window
    win._adv["polish_menu_hotkey"].set("f8")  # samma som poleringstangenten
    win._save()
    assert win.win.winfo_exists()
    assert "olika tangenter" in win._error.cget("text")
    win._adv["polish_menu_hotkey"].set("shift+f8")
    win._save()
    assert not win.win.winfo_exists()


def test_pillen_ritar_och_rensar_knappar(root):
    overlay = Overlay(root)
    picked = []
    overlay.show(
        "Polerar…",
        BLUE,
        actions=[("Skriv in råtext", lambda: picked.append("raw"))],
    )
    assert _wait_for(root, lambda: len(overlay._action_buttons) == 1)
    overlay._action_buttons[0].invoke()
    assert picked == ["raw"]
    overlay.show("Skriver…", BLUE)
    assert _wait_for(root, lambda: overlay._action_buttons == [])
