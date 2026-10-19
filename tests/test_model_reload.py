"""Modellbyte: den gamla Whisper-modellen ska vara släppt innan den nya
laddas, så att grafikkortet aldrig behöver hålla båda samtidigt."""

import threading
import weakref

from viska import app as app_module


class FakeTranscriber:
    alive: "weakref.WeakSet[FakeTranscriber]" = weakref.WeakSet()
    alive_at_construction: list[int] = []

    def __init__(self, model, *_args, **_kwargs):
        FakeTranscriber.alive_at_construction.append(len(FakeTranscriber.alive))
        FakeTranscriber.alive.add(self)
        self.model_name = model
        self.device = "cuda"
        self.initial_prompt = ""


class _Quiet:
    def show(self, *_a, **_k):
        pass


class _Recorder:
    recording = False


def _app(monkeypatch):
    monkeypatch.setattr(app_module, "Transcriber", FakeTranscriber)
    monkeypatch.setattr(app_module.llm, "polish_shares_gpu", lambda cfg: False)
    a = app_module.App.__new__(app_module.App)
    a.cfg = {
        "model": "large",
        "model_en": "en",
        "device": "auto",
        "beam_size": 5,
        "compute_type": "auto",
        "initial_prompt": "",
        "hotkey": "f9",
    }
    a.transcribers = {}
    a._loading_lock = threading.Lock()
    a._loading_langs = set()
    a._session_lock = threading.Lock()
    a.overlay = _Quiet()
    a.recorder = _Recorder()
    a.status = ""
    a._refresh_tray_menu = lambda: None
    return a


def test_gamla_modellen_slapps_fore_den_nya(monkeypatch):
    FakeTranscriber.alive_at_construction.clear()
    a = _app(monkeypatch)
    a._load_model("sv")
    a._load_model("en")
    assert len(FakeTranscriber.alive) == 2

    a.cfg["model"] = "medium"
    a._load_model("sv", force=True)

    # Den tredje modellen skapades när inga andra levde längre.
    assert FakeTranscriber.alive_at_construction[-1] == 0
    assert list(a.transcribers) == ["sv"]
    assert a.transcribers["sv"].model_name == "medium"


def test_vanlig_laddning_behaller_andra_spraket(monkeypatch):
    a = _app(monkeypatch)
    a._load_model("sv")
    a._load_model("en")
    assert set(a.transcribers) == {"sv", "en"}
