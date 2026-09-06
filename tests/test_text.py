"""Efterbehandling av transkriberad text: röstkommandon, ersättningsregler
och reglerna som härleds ur en rättad diktering."""

from viska import commands
from viska.corrections import derive_rules

VOICE = {"ny rad": "\n", "nytt stycke": "\n\n"}


# -- Röstkommandon ---------------------------------------------------------------


def test_rostkommando_blir_radbrytning():
    assert commands.apply("första raden ny rad andra raden", VOICE) == (
        "första raden\nandra raden"
    )


def test_rostkommando_med_versal_och_punkt():
    assert commands.apply("klart. Ny rad. sedan", VOICE) == "klart.\nsedan"


def test_langsta_frasen_vinner():
    assert commands.apply("ett nytt stycke två", VOICE) == "ett\n\ntvå"


def test_ord_som_bara_borjar_som_kommando_rors_inte():
    assert commands.apply("nyheter om nyrad", VOICE) == "nyheter om nyrad"


# -- Ersättningsregler -----------------------------------------------------------


def test_ersattning_matchar_hela_ord():
    ut = commands.apply_replacements("vi kör på avansa idag", {"avansa": "Avanza"})
    assert ut == "vi kör på Avanza idag"


def test_ersattning_matchar_inte_inuti_ord():
    assert commands.apply_replacements("kavansat", {"avansa": "Avanza"}) == "kavansat"


def test_flerordsfras_ersatts():
    ut = commands.apply_replacements(
        "det ligger i socker lags repot", {"socker lags": "sockulags"}
    )
    assert ut == "det ligger i sockulags repot"


def test_gemen_ersattning_foljer_meningens_skiftlage():
    ut = commands.apply_replacements("Sockulags och sockulags", {"sockulags": "sockelag"})
    assert ut == "Sockelag och sockelag"


def test_egna_versaler_i_ersattningen_lamnas_i_fred():
    ut = commands.apply_replacements("en ifon och en Ifon", {"ifon": "iPhone"})
    assert ut == "en iPhone och en iPhone"


def test_langsta_regeln_provas_forst():
    regler = {"avanza": "Avanza", "avanza auto": "Avanza Auto"}
    assert commands.apply_replacements("kör avanza auto", regler) == "kör Avanza Auto"


def test_tomma_och_trasiga_regler_ignoreras():
    assert commands.apply_replacements("text", {"": "x", "a": None}) == "text"


# -- Regler ur en rättad diktering -------------------------------------------------


def test_ett_rattat_ord_blir_en_regel():
    assert derive_rules("jag heter lukas", "jag heter Lucas") == [("lukas", "Lucas")]


def test_flera_rattade_ord_i_rad_blir_var_sin_regel():
    assert derive_rules("hej lukas skoog", "hej Lucas Skog") == [
        ("lukas", "Lucas"),
        ("skoog", "Skog"),
    ]


def test_ingen_andring_ger_inga_regler():
    assert derive_rules("samma text", "samma text") == []


def test_tillagd_text_blir_ingen_regel():
    """Ett tillagt ord beror på vad som sades, inte på hur Whisper hör."""
    assert derive_rules("det är bra", "det är mycket bra") == []


def test_skiljetecken_i_kanten_hamnar_inte_i_regeln():
    assert derive_rules("hej avansa!", "hej Avanza!") == [("avansa", "Avanza")]


def test_lang_omformulering_blir_ingen_regel():
    lang = "ett två tre fyra fem sex"
    assert derive_rules(lang, "helt annan mening med andra ord här") == []


def test_samma_fel_tva_ganger_ger_en_regel():
    assert derive_rules("avansa och avansa", "Avanza och Avanza") == [
        ("avansa", "Avanza")
    ]
