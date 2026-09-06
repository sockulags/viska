"""http-backenden mot en påhittad server i samma process."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from viska import llm

SHIM_PATH = Path(__file__).resolve().parents[1] / "tools" / "polish_server.py"


class _Stub(BaseHTTPRequestHandler):
    """Svarar som poleringstjänsten. `mode` styrs per test."""

    mode = "ok"
    seen: dict = {}

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.mode == "down":
            self._send(500, {"error": {"message": "nere"}})
        else:
            self._send(200, {"status": "ok"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _Stub.seen = {
            "body": json.loads(self.rfile.read(length)),
            "auth": self.headers.get("Authorization"),
            "path": self.path,
        }
        if self.mode == "quota":
            self._send(429, {"error": {"message": "You've hit your usage limit."}})
        elif self.mode == "broken":
            self._send(502, {"error": {"message": "Codex svarade utan text"}})
        elif self.mode == "empty":
            self._send(200, {"choices": [{"message": {"content": "   "}}]})
        else:
            self._send(
                200,
                {"choices": [{"message": {"content": "Polerad text."}}]},
            )

    def log_message(self, *_a):
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Stub.mode = "ok"
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def cfg(url, **overrides):
    return {
        "polish_backend": "http",
        "polish_http_url": f"{url}/v1/chat/completions",
        "polish_http_model": "gpt-5.4-mini",
        "polish_http_token": "",
        "polish_http_timeout_s": 5,
        **overrides,
    }


def test_polering_gar_igenom(server):
    assert llm.polish_http(cfg(server), "hej hej", "skriv ett mejl") == "Polerad text."
    body = _Stub.seen["body"]
    assert body["model"] == "gpt-5.4-mini"
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "skriv ett mejl" in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == "hej hej"


def test_nyckeln_skickas_som_bearer(server):
    llm.polish_http(cfg(server, polish_http_token="hemlis"), "hej", "")
    assert _Stub.seen["auth"] == "Bearer hemlis"


def test_utan_nyckel_skickas_ingen_header(server):
    llm.polish_http(cfg(server), "hej", "")
    assert _Stub.seen["auth"] is None


def test_tomt_svar_ger_ratexten_tillbaka(server):
    _Stub.mode = "empty"
    assert llm.polish_http(cfg(server), "originaltexten", "") == "originaltexten"


def test_kvotstopp_sags_rakt_ut(server):
    _Stub.mode = "quota"
    with pytest.raises(RuntimeError) as e:
        llm.polish_http(cfg(server), "hej", "")
    assert "kvot" in str(e.value)
    assert "usage limit" in str(e.value)


def test_ovriga_fel_tar_med_statuskoden(server):
    _Stub.mode = "broken"
    with pytest.raises(RuntimeError) as e:
        llm.polish_http(cfg(server), "hej", "")
    assert "502" in str(e.value)


def test_nabarhetskollen_ser_health(server):
    assert llm.http_reachable(cfg(server)) is True


def test_nabarhetskollen_faller_ner_nar_tjansten_ar_nere(server):
    _Stub.mode = "down"
    assert llm.http_reachable(cfg(server)) is False


def test_nabarhetskollen_pa_stangd_port():
    assert llm.http_reachable(cfg("http://127.0.0.1:1")) is False


# -- Backendvalet -------------------------------------------------------------


def test_http_raknas_som_fjarrbackend():
    assert llm.is_remote({"polish_backend": "http"})
    assert llm.is_remote({"polish_backend": "ollama"})
    assert not llm.is_remote({"polish_backend": "builtin"})


def test_fjarrbackend_delar_inte_vram():
    assert not llm.polish_shares_gpu({"polish_backend": "http"})


def test_etikett_och_modell_foljer_backend():
    c = cfg("http://x", polish_ollama_model="qwen3.5:9b")
    assert llm.remote_model(c) == "gpt-5.4-mini"
    assert "moln" in llm.remote_label(c)
    c["polish_backend"] = "ollama"
    assert llm.remote_model(c) == "qwen3.5:9b"
    assert llm.remote_label(c) == "Ollama"


# -- Shimmens rena logik ------------------------------------------------------


@pytest.fixture(scope="module")
def shim():
    spec = importlib.util.spec_from_file_location("polish_server", SHIM_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shim_delar_upp_meddelandena(shim):
    task, text = shim._split_messages(
        [
            {"role": "system", "content": "skriv ett mejl"},
            {"role": "user", "content": "hej ehm"},
        ]
    )
    assert task == "skriv ett mejl"
    assert text == "hej ehm"


def test_shim_tar_sista_anvandarmeddelandet(shim):
    _task, text = shim._split_messages(
        [
            {"role": "user", "content": "gammalt"},
            {"role": "assistant", "content": "svar"},
            {"role": "user", "content": "nytt"},
        ]
    )
    assert text == "nytt"


def test_shim_tal_skrapmeddelanden(shim):
    task, text = shim._split_messages(["skräp", {"role": "user", "content": None}, None])
    assert (task, text) == ("", "")


def test_shim_klassar_kvotstopp_som_429(shim):
    status, _ = shim._classify(RuntimeError("You've hit your usage limit."))
    assert status == 429


def test_shim_klassar_ovrigt_som_502(shim):
    status, message = shim._classify(RuntimeError("nätverket dog"))
    assert status == 502
    assert message == "nätverket dog"


class _FakeStatus:
    """Som SDK:ns TurnStatus: en enum vars strängform inte är värdet."""

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return f"TurnStatus.{self.value}"


class _FakeResult:
    def __init__(self, status, error=""):
        self.status = status
        self.error = error


def test_shim_laser_status_ur_enumen(shim):
    assert shim._turn_status(_FakeResult(_FakeStatus("completed"))) == "completed"
    assert shim._turn_status(_FakeResult(_FakeStatus("failed"))) == "failed"


def test_shim_tar_ocksa_strangstatus(shim):
    assert shim._turn_status(_FakeResult("completed")) == "completed"


def test_shim_saknad_status_raknas_som_klar(shim):
    assert shim._turn_status(_FakeResult(None)) == "completed"
