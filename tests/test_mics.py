"""Valet av mikrofon när ett Bluetooth-headset är Windows standard.

Enhetsnamnen är tagna från en riktig dator: två Bluetooth-headset, ett
USB-ljudkort och en inbyggd mikrofon.
"""

from viska import mics

ENDPOINTS = [
    mics.Endpoint("Jabra Evolve3 85 Hands-Free", "BTHHFENUM"),
    mics.Endpoint("JBL LIVE770NC Hands-Free", "BTHHFENUM"),
    mics.Endpoint("Realtek USB2.0 Audio", "USB"),
    mics.Endpoint("Realtek(R) Audio", "HDAUDIO"),
    mics.Endpoint("Jabra Link 390", "USB"),
]

HEADSET = (3, "Headset (Jabra Evolve3 85 Hands-Free)")
USB_MIC = (5, "Microphone (Realtek USB2.0 Audio)")
USB_LINE = (6, "Line (Realtek USB2.0 Audio)")
BUILT_IN = (8, "Mikrofonmatris (Realtek(R) Audio)")


def test_headset_som_standard_ersatts_av_inbyggd_mikrofon():
    choice = mics.choose([HEADSET, USB_MIC, BUILT_IN], 3, ENDPOINTS)
    assert choice.device == 8
    assert not choice.bluetooth
    assert choice.avoided == "Jabra Evolve3 85 Hands-Free"
    assert "samtalsläge" in choice.describe()


def test_usb_valjs_nar_inbyggd_saknas_men_aldrig_linjeingang():
    choice = mics.choose([HEADSET, USB_LINE, USB_MIC], 3, ENDPOINTS)
    assert choice.device == 5


def test_vanlig_standardmikrofon_rors_inte():
    choice = mics.choose([HEADSET, USB_MIC, BUILT_IN], 5, ENDPOINTS)
    assert choice.device == 5
    assert choice.avoided is None


def test_headset_anvands_om_det_ar_enda_mikrofonen():
    choice = mics.choose([HEADSET, USB_LINE], 3, ENDPOINTS)
    assert choice.device == 3
    assert choice.bluetooth


def test_headset_anvands_nar_anvandaren_vill():
    choice = mics.choose([HEADSET, BUILT_IN], 3, ENDPOINTS, avoid_bluetooth=False)
    assert choice.device == 3
    assert choice.bluetooth
    assert "samtalsläge" in choice.describe()


def test_ingen_standard_ger_inget_val():
    assert mics.choose([USB_MIC], None, ENDPOINTS) is None


def test_namnet_racker_nar_registret_inte_kanner_enheten():
    assert mics.is_bluetooth("Headset (Okänt märke Hands-Free)", [])
    assert not mics.is_bluetooth("Microphone (Okänt USB-kort)", [])


def test_usb_dongel_raknas_inte_som_bluetooth():
    # Jabra Link 390 talar Bluetooth med headsetet men är ett USB-ljudkort
    # för Windows; samtalsläget hanteras i dongeln och syns inte här.
    assert not mics.is_bluetooth("Headset Microphone (Jabra Link 390)", ENDPOINTS)


def test_registret_gar_att_lasa():
    # Alla Windows-datorer har nyckeln; poster utan egenskaperna hoppas över.
    for ep in mics.capture_endpoints():
        assert ep.device
        assert ep.enumerator
