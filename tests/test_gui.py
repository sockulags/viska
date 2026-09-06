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
from viska.settings import _ENGINE_CHOICES, _PAGES, SettingsWindow

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
    delay = 0.0

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
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        _Stub.last_system = body["messages"][0]["content"]
        if _Stub.delay:
            time.sleep(_Stub.delay)
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
    _Stub.delay = 0.0
    cfg = dict(config.DEFAULTS)
    cfg["polish_backend"] = "http"
    cfg["polish_http_url"] = f"{service_url}/v1/chat/completions"
    cfg["polish_http_command"] = "cmd /c echo hej"
    cfg["app_profiles"] = {"code.exe": {"flow": False, "language": "en"}}
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
        win._closing = True
        win.win.destroy()


def _wait_for(root, predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        root.update()
        time.sleep(0.03)
    return predicate()


# -- Struktur och navigering ---------------------------------------------------


def test_alla_sidor_finns(window):
    win, _saved, _state = window
    assert list(win._pages) == list(_PAGES)
    assert "App-profiler" in win._pages  # egen sida, inte gömd under Ordlista


def test_navigering_visar_en_sida_i_taget(window, root):
    win, _saved, _state = window
    win._select_page("Polering")
    root.update()
    assert win._pages["Polering"].outer.winfo_manager() == "pack"
    assert win._pages["Diktering"].outer.winfo_manager() == ""


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
    win._closing = True
    win.win.destroy()
    assert elapsed < 1.5


# -- Poleringsvyn --------------------------------------------------------------


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


def test_testet_kors_bara_pa_begaran(window, root):
    win, _saved, _state = window
    root.update()
    assert win._test_result.get("1.0", "end").strip() == ""


def test_testpolering_visar_svar_och_svarstid(window, root):
    win, _saved, _state = window
    win._run_test()
    assert _wait_for(
        root, lambda: "Polerad" in win._test_result.get("1.0", "end")
    )
    # Punkten i den polerade texten ska vara kvar; bara sekunderna får komma.
    assert "Polerad mening." in win._test_result.get("1.0", "end")
    assert "Svarstid" in win._test_time.cget("text")


def test_testet_anvander_osparad_mall(window, root):
    """Redigerar man stilen ska testet köra DEN, inte den sparade."""
    win, _saved, _state = window
    win._template_list.selection_set(0)
    win._on_template_select(None)
    win._template_name.set("Mejl")
    win._set_text(win._template_prompt, "SKRIV SOM EN PIRAT")
    win._global_polish_choice.set("Mejl")
    win._run_test()
    assert _wait_for(root, lambda: "Polerad" in win._test_result.get("1.0", "end"))
    assert "PIRAT" in _Stub.last_system


def test_avbrutet_test_visar_inte_sent_svar(window, root):
    win, _saved, _state = window
    _Stub.delay = 0.6
    win._run_test()
    root.update()
    win._cancel_test()
    assert "avbrutet" in win._test_result.get("1.0", "end")
    time.sleep(1.0)
    root.update()
    assert "Polerad" not in win._test_result.get("1.0", "end")
    assert win._test_time.cget("text") == ""


def test_testresultatet_gar_inte_att_redigera(window):
    win, _saved, _state = window
    assert str(win._test_result.cget("state")) == "disabled"


def test_tjansten_startas_och_stoppas(window, root):
    win, _saved, state = window
    win._toggle_service()
    root.update()
    assert state["running"] and state["command"] == "cmd /c echo hej"
    win._toggle_service()
    root.update()
    assert not state["running"]


# -- Sparande ------------------------------------------------------------------


def test_vardena_foljer_med(window):
    win, _saved, _state = window
    values = win.values()
    assert values["polish_backend"] == "http"
    assert values["polish_http_url"].endswith("/v1/chat/completions")
    assert values["polish_http_command"] == "cmd /c echo hej"
    assert values["polish_double_tap"] is False
    assert values["polish_menu_hotkey"] == "shift+f8"


def test_osparad_mall_kastas_inte_bort(window):
    """Den gamla oklarheten: en ifylld mall försvann om man tryckte fönstrets
    Spara i stället för mallens egen sparaknapp."""
    win, _saved, _state = window
    win._new_template()
    win._template_name.set("Anteckning")
    win._set_text(win._template_prompt, "Skriv som en kort anteckning.")
    win._commit_editors()
    values = win.values()
    assert any(t["namn"] == "Anteckning" for t in values["polish_templates"])


def test_namnbyte_pa_mall_foljer_med_till_profiler(window):
    win, _saved, _state = window
    win._templates.append({"namn": "Gammal", "prompt": "x"})
    win._profiles["kod.exe"] = {"polish_template": "Gammal"}
    win._selected_template = "Gammal"
    win._template_name.set("Ny")
    win._set_text(win._template_prompt, "x")
    win._commit_template()
    assert win._profiles["kod.exe"]["polish_template"] == "Ny"


def test_osparade_andringar_markeras(window, root):
    win, _saved, _state = window
    assert win._dirty_label.cget("text") == ""
    win._sounds.set(not win._sounds.get())
    root.update()
    assert "Osparade" in win._dirty_label.cget("text")


def test_krockande_tangenter_avvisas(window):
    win, _saved, _state = window
    win._adv["polish_menu_hotkey"].set("f8")  # samma som poleringstangenten
    win._save()
    assert win.win.winfo_exists()
    assert "olika tangenter" in win._error.cget("text")
    win._adv["polish_menu_hotkey"].set("shift+f8")
    win._save()
    assert not win.win.winfo_exists()


# -- App-profiler och arv ------------------------------------------------------


def test_profilens_arv_visas_i_klartext(window, root):
    win, _saved, _state = window
    win._select_page("App-profiler")
    root.update()
    values = win._profile_flow_combo.cget("values")
    assert values[0].startswith("Ärv global (")


def test_profil_med_overstyrning_lases_och_skrivs_tillbaka(window):
    win, _saved, _state = window
    win._set_profile_editor("code.exe")
    assert win._profile_flow.get() == "Av"  # uttryckligt override i configen
    assert win._profile_lang.get() == "Engelska"
    win._commit_profile()
    assert win._profiles["code.exe"] == {"flow": False, "language": "en"}


def test_arv_skriver_ingen_nyckel(window):
    win, _saved, _state = window
    win._set_profile_editor("code.exe")
    win._profile_flow.set(win._profile_flow_combo.cget("values")[0])  # ärv
    win._profile_lang.set(win._profile_lang_combo.cget("values")[0])
    win._commit_profile()
    assert win._profiles["code.exe"] == {}


# -- Statuspillen --------------------------------------------------------------


def test_pillen_ritar_och_rensar_knappar(root):
    overlay = Overlay(root)
    picked = []
    overlay.show(
        "Polerar",
        BLUE,
        actions=[("Infoga råtext", lambda: picked.append("raw"))],
    )
    assert _wait_for(root, lambda: len(overlay._action_buttons) == 1)
    overlay._action_buttons[0].invoke()
    assert picked == ["raw"]
    overlay.show("Skriver in", BLUE)
    assert _wait_for(root, lambda: overlay._action_buttons == [])


def test_pillen_raknar_tiden(root):
    overlay = Overlay(root)
    overlay.show("Spelar in", BLUE, timer=True)
    assert _wait_for(root, lambda: overlay._timer_from is not None)
    assert _wait_for(root, lambda: "0:0" in overlay._label.cget("text"), timeout=3)


def test_sidbyte_ar_ingen_andring(window, root):
    """Att bara titta ska inte tända "Osparade ändringar"."""
    win, _saved, _state = window
    for page in _PAGES:
        win._select_page(page)
        root.update()
    win._set_profile_editor("code.exe")
    root.update()
    assert win._dirty_label.cget("text") == ""


def test_arvsetiketten_undviker_parentes_i_parentes(window, root):
    win, _saved, _state = window
    win._select_page("App-profiler")
    root.update()
    inherit = win._profile_inject_combo.cget("values")[0]
    assert inherit == "Ärv global (klistra in)"


# -- Ytterlägen ---------------------------------------------------------------


def test_tomt_mallbibliotek_gar_att_anvanda(root, service_url):
    """Tömmer man biblioteket ska sidan fungera och falla till Egen text."""
    cfg = dict(config.DEFAULTS)
    cfg["polish_templates"] = []
    cfg["polish_template"] = "Mejl"  # pekar på något som inte finns
    win = SettingsWindow(root, cfg, lambda _c: None)
    root.update()
    assert win._template_list.size() == 0
    assert win.values()["polish_template"] == ""  # fallback, inte krasch
    win._closing = True
    win.win.destroy()


def test_lang_malltext_behalls_i_sin_helhet(window, root):
    win, _saved, _state = window
    long_text = "Skriv om texten. " * 200
    win._template_list.selection_set(0)
    win._on_template_select(None)
    win._set_text(win._template_prompt, long_text)
    win._commit_template()
    saved = next(t for t in win._templates if t["namn"] == win._template_name.get())
    assert saved["prompt"] == long_text.strip()


def test_frankopplad_backend_sager_till(root):
    cfg = dict(config.DEFAULTS)
    cfg["polish_backend"] = "http"
    cfg["polish_http_url"] = "http://127.0.0.1:1/v1/chat/completions"
    win = SettingsWindow(root, cfg, lambda _c: None)
    status = win._remote_widgets["http"]["status"]
    assert _wait_for(root, lambda: "svarar inte" in status.cget("text"))
    win._closing = True
    win.win.destroy()


def test_misslyckat_test_visas_i_rutan(root):
    cfg = dict(config.DEFAULTS)
    cfg["polish_backend"] = "http"
    cfg["polish_http_url"] = "http://127.0.0.1:1/v1/chat/completions"
    win = SettingsWindow(root, cfg, lambda _c: None)
    win._run_test()
    assert _wait_for(
        root, lambda: "misslyckades" in win._test_result.get("1.0", "end")
    )
    win._closing = True
    win.win.destroy()
