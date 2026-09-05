"""Upplösningen av poleringsuppgiften: app-profil → mall/fritext → global."""

import pytest

from viska import llm, metrics, update

CFG = {
    "polish_templates": [
        {"namn": "Mejl", "prompt": "skriv ett mejl"},
        {"namn": "Commit-text", "prompt": "skriv en commit"},
    ],
    "polish_template": "Mejl",
    "polish_prompt": "global fritext",
}


def cfg(**overrides):
    return {**CFG, **overrides}


def test_global_mall_vinner_over_fritext():
    assert llm.resolve_polish_prompt(cfg()) == "skriv ett mejl"


def test_tom_global_mall_ger_fritexten():
    assert llm.resolve_polish_prompt(cfg(polish_template="")) == "global fritext"


def test_raderad_global_mall_faller_tillbaka_pa_fritexten():
    assert llm.resolve_polish_prompt(cfg(polish_template="Borta")) == "global fritext"


def test_profil_utan_eget_val_arver_globalt():
    assert llm.resolve_polish_prompt(cfg(), {"flow": True}) == "skriv ett mejl"


def test_profilens_mall_vinner():
    ut = llm.resolve_polish_prompt(cfg(), {"polish_template": "Commit-text"})
    assert ut == "skriv en commit"


def test_profilens_egen_fritext_vinner():
    ut = llm.resolve_polish_prompt(cfg(), {"polish_prompt": "kort och gott"})
    assert ut == "kort och gott"


def test_profilens_raderade_mall_faller_tillbaka_pa_global_standard():
    ut = llm.resolve_polish_prompt(cfg(), {"polish_template": "Borta"})
    assert ut == "skriv ett mejl"


def test_trasiga_mallposter_hoppas_over():
    ut = llm.polish_templates({"polish_templates": [{"namn": " "}, "skräp", None]})
    assert ut == []


# -- Versionsjämförelsen som styr uppdateringskollen --------------------------------


@pytest.mark.parametrize(
    "ny,gammal,nyare",
    [
        ("v0.4.1", "0.4.0", True),
        ("v0.5.0", "0.4.9", True),
        ("v0.10.0", "0.9.0", True),  # inte strängjämförelse
        ("v0.4.0", "0.4.0", False),
        ("v0.3.9", "0.4.0", False),
    ],
)
def test_versionsjamforelse(ny, gammal, nyare):
    assert (update._parse(ny) > update._parse(gammal)) is nyare


# -- Mätvärden ----------------------------------------------------------------------


def test_summary_ar_tom_utan_matningar():
    metrics.reset()
    assert metrics.summary() == ""


def test_median_over_matningar():
    metrics.reset()
    for total in (1000, 3000, 2000):
        metrics.record(1.0, total - 100, 50, total, 20)
    assert "2,0 s" in metrics.summary()
    metrics.reset()


def test_ringen_haller_sig_till_fonstret():
    metrics.reset()
    for _ in range(metrics.WINDOW + 10):
        metrics.record(1.0, 500, 20, 600, 10)
    assert len(metrics.runs()) == metrics.WINDOW
    metrics.reset()
