"""Modellbyte: den gamla Whisper-modellen ska vara släppt innan den nya
laddas, så att grafikkortet aldrig behöver hålla båda samtidigt."""

import copy
import weakref

from viska import config, engine


class FakeTranscriber:
    alive: "weakref.WeakSet[FakeTranscriber]" = weakref.WeakSet()
    alive_at_construction: list[int] = []

    def __init__(self, model, *_args, **_kwargs):
        FakeTranscriber.alive_at_construction.append(len(FakeTranscriber.alive))
        FakeTranscriber.alive.add(self)
        self.model_name = model
        self.device = "cuda"
        self.initial_prompt = ""


class Recorded(engine.Feedback):
    def __init__(self):
        self.events = []

    def status(self, text):
        self.events.append(("status", text))

    def model_ready(self, device):
        self.events.append(("ready", device))


def _engine(monkeypatch):
    monkeypatch.setattr(engine, "Transcriber", FakeTranscriber)
    monkeypatch.setattr(engine.llm, "polish_shares_gpu", lambda cfg: False)
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg.update(model="large", model_en="en", history_persist=False)
    feedback = Recorded()
    return engine.DictationEngine(cfg, feedback), feedback


def test_gamla_modellen_slapps_fore_den_nya(monkeypatch):
    FakeTranscriber.alive_at_construction.clear()
    e, _ = _engine(monkeypatch)
    e.load_model("sv")
    e.load_model("en")
    assert len(FakeTranscriber.alive) == 2

    e.cfg["model"] = "medium"
    e.load_model("sv", force=True)

    # Den tredje modellen skapades när inga andra levde längre.
    assert FakeTranscriber.alive_at_construction[-1] == 0
    assert list(e.transcribers) == ["sv"]
    assert e.transcribers["sv"].model_name == "medium"


def test_vanlig_laddning_behaller_andra_spraket(monkeypatch):
    e, _ = _engine(monkeypatch)
    e.load_model("sv")
    e.load_model("en")
    assert set(e.transcribers) == {"sv", "en"}


def test_motorn_berattar_att_den_ar_redo(monkeypatch):
    e, feedback = _engine(monkeypatch)
    e.load_model("sv")
    assert ("ready", "cuda") in feedback.events
    assert ("status", "redo – large (cuda)") in feedback.events


def test_motorn_klarar_sig_utan_mottagare(monkeypatch):
    monkeypatch.setattr(engine, "Transcriber", FakeTranscriber)
    monkeypatch.setattr(engine.llm, "polish_shares_gpu", lambda cfg: False)
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["history_persist"] = False
    e = engine.DictationEngine(cfg)  # ingen Feedback, ingen polering
    e.load_model("sv")
    assert "sv" in e.transcribers
    assert e.polishing is None
