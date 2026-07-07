"""Global dikteringstangent via Win32 RegisterHotKey.

RegisterHotKey i stället för tangentbordshookar ('keyboard'/pynput) för att:
- den fungerar även när ett admin-fönster har fokus,
- tangenten konsumeras av OS:et så att appen i fokus aldrig ser den,
- den är opåverkad av svenskt tangentbordslayout (kända buggar i
  'keyboard'-biblioteket på icke-USA-layouter).

RegisterHotKey ger bara nedtryck; släpp detekteras genom att polla
GetAsyncKeyState — standardmönstret för push-to-talk på Windows.

Trådning: on_start/on_stop anropas alltid med det interna låset hållet,
vilket gör start/stopp exakt-en-gång och omöjliggör omkastad ordning
(stopp för session A efter start av session B). Callbackarna får därför
aldrig anropa tillbaka in i PushToTalk (utom force_stop, som är designad
för det via sitt returvärde i stället för återinträde).
"""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODS = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
}

_VKS = {
    **{f"f{i}": 0x6F + i for i in range(1, 25)},  # f1=0x70 … f24=0x87
    **{chr(c).lower(): c for c in range(0x41, 0x5B)},  # a–z
    **{chr(c): c for c in range(0x30, 0x3A)},  # 0–9
    "space": 0x20,
    "pause": 0x13,
    "caps lock": 0x14,
    "capslock": 0x14,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "page up": 0x21,
    "page down": 0x22,
    "scroll lock": 0x91,
    "scrolllock": 0x91,
    **{f"num {i}": 0x60 + i for i in range(10)},
}


class HotkeyError(Exception):
    pass


def parse_hotkey(hotkey: str) -> tuple[int, int]:
    mods = 0
    vk = None
    for part in hotkey.lower().split("+"):
        part = part.strip()
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _VKS:
            if vk is not None:
                raise HotkeyError(f"Flera huvudtangenter i '{hotkey}'")
            vk = _VKS[part]
        else:
            raise HotkeyError(f"Okänd tangent '{part}' i '{hotkey}'")
    if vk is None:
        raise HotkeyError(f"Ingen huvudtangent i '{hotkey}'")
    return mods, vk


class PushToTalk:
    """En och samma tangent styr allt:

    - Kort tryck: börja spela in; nästa tryck stoppar.
    - Håll inne (längre än hold_threshold_s): spela in tills tangenten släpps.

    on_start ska returnera sant om en inspelning faktiskt startade;
    annars förblir tangenten "inaktiv" och nästa tryck blir ett nytt
    startförsök i stället för ett stopp.
    """

    _HOTKEY_ID = 1

    def __init__(self, hotkey: str, hold_threshold_s: float, on_start, on_stop):
        self._mods, self._vk = parse_hotkey(hotkey)
        self.hold_threshold_s = hold_threshold_s
        self.on_start = on_start
        self.on_stop = on_stop
        self._lock = threading.Lock()
        self._active = False
        self._tid = None
        self._ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(
            target=self._message_loop, daemon=True, name="viska-hotkey"
        )
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._error:
            raise HotkeyError(self._error)

    def _message_loop(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(
            None, self._HOTKEY_ID, self._mods | MOD_NOREPEAT, self._vk
        ):
            self._error = (
                "Tangenten är upptagen av ett annat program "
                f"(Win32-fel {ctypes.get_last_error()}). "
                "Byt 'hotkey' i config.json."
            )
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == self._HOTKEY_ID:
                    try:
                        self._on_hotkey_down()
                    except Exception:
                        # Får aldrig döda meddelandeloopen — då slutar
                        # tangenten fungera tyst (osynligt under pythonw).
                        log.exception("Fel i tangent-callback")
        finally:
            user32.UnregisterHotKey(None, self._HOTKEY_ID)

    def _on_hotkey_down(self) -> None:
        watch_from = None
        with self._lock:
            if self._active:
                # Andra trycket i växlingsläge: stoppa.
                self._active = False
                self.on_stop()
                return
            # Mät hålltiden från nedtrycket — inte från när on_start är
            # klar (att öppna mikrofonen kan ta flera hundra ms).
            t0 = time.monotonic()
            started = False
            try:
                started = bool(self.on_start())
            finally:
                self._active = started
            if started:
                watch_from = t0
        if watch_from is not None:
            threading.Thread(
                target=self._watch_release,
                args=(watch_from,),
                daemon=True,
                name="viska-release",
            ).start()

    def _watch_release(self, t0: float) -> None:
        while user32.GetAsyncKeyState(self._vk) & 0x8000:
            time.sleep(0.015)
        if time.monotonic() - t0 < self.hold_threshold_s:
            return  # kort tryck: växlingsläge, inspelningen fortsätter
        with self._lock:
            if not self._active:
                return  # redan stoppad (maxtid eller andra tryck)
            self._active = False
            self.on_stop()

    def force_stop(self) -> bool:
        """Avaktivera utifrån (t.ex. maxtid). Returnerar True om vi tog
        över en aktiv session — då, och bara då, äger anroparen stoppet."""
        with self._lock:
            was_active, self._active = self._active, False
            return was_active

    def stop(self) -> None:
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            # Vänta in avregistreringen — annars kan en ny PushToTalk med
            # samma tangent få "upptagen" av vår egen gamla registrering.
            self._thread.join(timeout=2)
