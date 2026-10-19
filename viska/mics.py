"""Vilken mikrofon Viska ska lyssna med.

Ett vanligt Bluetooth-headset byter till samtalsläge (mono, sämre ljud) så
fort något program öppnar dess mikrofon, och tillbaka först när den stängs.
Att öppna en annan mikrofon påverkar inte headsetet. Är Windows
standardmikrofon ett sådant headset väljer Viska därför datorns egen
mikrofon i stället, om det finns någon — musiken i headsetet fortsätter då
som vanligt.

Vad som är Bluetooth läses ur registrets beskrivning av ljudenheterna
(MMDevices). Där står varje enhets uppräknare: BTHHFENUM för ett headset i
samtalsläge, USB, HDAUDIO och så vidare. Strukturen är odokumenterad men
har varit densamma sedan Windows 7 och används av många ljudverktyg; går
den inte att läsa faller valet tillbaka på namnet.
"""

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

_CAPTURE_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
# PKEY_Device_FriendlyName för själva hårdvaran ("Jabra Evolve3 85 Hands-Free").
_PROP_DEVICE_NAME = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
# Uppräknaren ("BTHHFENUM", "USB", "HDAUDIO").
_PROP_ENUMERATOR = "{a45c254e-df1c-4efd-8020-67d146a850e0},24"

# Ingångar som inte är mikrofoner: linjeingång och "det datorn spelar".
_NOT_A_MIC = re.compile(r"stereo ?mix|what u hear|\blin(e|je)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Endpoint:
    device: str  # hårdvarans namn, det som står inom parentes i enhetsnamnet
    enumerator: str

    @property
    def bluetooth(self) -> bool:
        # BTHHFENUM (samtalsläge) och BTHENUM; LE Audio har okänt namn men
        # börjar enligt samma mönster.
        return self.enumerator.upper().startswith("BTH")


@dataclass(frozen=True)
class Choice:
    device: int | None  # PortAudio-index; None = PortAudios standard
    name: str
    bluetooth: bool
    # Headsetet som Windows hade som standard men som Viska gick förbi.
    avoided: str | None = None

    def describe(self) -> str:
        if not self.name:
            return "Ingen mikrofon hittades."
        if self.avoided:
            return (
                f"Lyssnar med {self.name}. {self.avoided} används inte, "
                "eftersom headsetet då byter till samtalsläge."
            )
        if self.bluetooth:
            return (
                f"Lyssnar med {self.name}. Headsetet byter till samtalsläge "
                "medan du dikterar."
            )
        return f"Lyssnar med {self.name}."


def capture_endpoints() -> list[Endpoint]:
    """Alla inspelningsenheter Windows känner till, ur registret."""
    try:
        import winreg
    except ImportError:
        return []
    result = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CAPTURE_KEY)
    except OSError:
        return []
    with root:
        index = 0
        while True:
            try:
                guid = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(root, guid) as key:
                    with winreg.OpenKey(key, "Properties") as props:
                        device = winreg.QueryValueEx(props, _PROP_DEVICE_NAME)[0]
                        enumerator = winreg.QueryValueEx(props, _PROP_ENUMERATOR)[0]
            except OSError:
                continue
            result.append(Endpoint(device=str(device), enumerator=str(enumerator)))
    return result


def _hardware(name: str) -> str:
    """'Headset (Jabra Evolve3 85 Hands-Free)' -> 'Jabra Evolve3 85 Hands-Free'."""
    match = re.search(r"\(([^()]*)\)\s*$", name)
    return match.group(1).strip() if match else name


def _endpoint_for(name: str, endpoints: list[Endpoint]) -> Endpoint | None:
    hardware = _hardware(name).casefold()
    for ep in endpoints:
        if ep.device.casefold() == hardware:
            return ep
    return None


def is_bluetooth(name: str, endpoints: list[Endpoint]) -> bool:
    ep = _endpoint_for(name, endpoints)
    if ep is not None:
        return ep.bluetooth
    lowered = name.casefold()
    return "hands-free" in lowered or "bthhfenum" in lowered


def choose(
    inputs: list[tuple[int, str]],
    default: int | None,
    endpoints: list[Endpoint],
    avoid_bluetooth: bool = True,
) -> Choice | None:
    """Ren beslutslogik.

    inputs är WASAPI-ingångarna som (PortAudio-index, namn), default är
    Windows standardmikrofon bland dem. None betyder att det inte finns
    något att välja mellan och att PortAudios standard får gälla.
    """
    by_index = dict(inputs)
    if default not in by_index:
        return None
    default_name = by_index[default]
    if not is_bluetooth(default_name, endpoints):
        return Choice(default, default_name, bluetooth=False)
    if not avoid_bluetooth:
        return Choice(default, default_name, bluetooth=True)

    candidates = [
        (index, name)
        for index, name in inputs
        if not is_bluetooth(name, endpoints) and not _NOT_A_MIC.search(name)
    ]
    if not candidates:
        log.info("Bara Bluetooth-mikrofoner finns – använder %s", default_name)
        return Choice(default, default_name, bluetooth=True)

    def rank(item):
        ep = _endpoint_for(item[1], endpoints)
        usb = ep is not None and ep.enumerator.upper() == "USB"
        return (usb, item[0])  # datorns inbyggda före USB, sedan Windows ordning

    index, name = min(candidates, key=rank)
    return Choice(index, name, bluetooth=False, avoided=_hardware(default_name))


def resolve(input_device, avoid_bluetooth: bool = True) -> Choice:
    """Mikrofonen att öppna nu. input_device är användarens uttryckliga val
    (namn eller index) och respekteras alltid; None betyder "Windows
    standard, men inte ett Bluetooth-headset om det går att undvika"."""
    import sounddevice as sd

    if input_device not in (None, ""):
        try:
            info = sd.query_devices(input_device, kind="input")
            name = info["name"]
        except Exception:
            name = str(input_device)
        return Choice(input_device, name, is_bluetooth(name, capture_endpoints()))

    try:
        wasapi = next(
            i for i, api in enumerate(sd.query_hostapis()) if "WASAPI" in api["name"]
        )
        api = sd.query_hostapis(wasapi)
        inputs = [
            (i, sd.query_devices(i)["name"])
            for i in api["devices"]
            if sd.query_devices(i)["max_input_channels"] > 0
        ]
        default = api["default_input_device"]
    except Exception:
        log.exception("Kunde inte läsa WASAPI-enheterna")
        inputs, default = [], None

    choice = choose(inputs, default, capture_endpoints(), avoid_bluetooth)
    if choice is not None:
        return choice
    try:
        name = sd.query_devices(kind="input")["name"]
    except Exception:
        name = ""
    return Choice(None, name, bluetooth=False)
