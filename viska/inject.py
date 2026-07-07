"""Skriver in text i det fönster som har fokus.

Primär metod: klippbordet + Ctrl+V. Snabbast oavsett textlängd och helt
säkert för svenska tecken. Dikteringen märks med Windows undantagsformat
så att den varken hamnar i Win+V-historiken eller synkas till
molnklippbordet — texten ska aldrig lämna datorn. Det gamla klippbordet
(text) återställs efteråt.

Reservmetod ("type" i config): pynput skriver tecken för tecken via
SendInput/KEYEVENTF_UNICODE — för appar där Ctrl+V inte fungerar
(vissa terminaler och TUI-program).
"""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

import win32clipboard
import win32con

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

_clipboard_lock = threading.Lock()

# -- SendInput: Ctrl+V med layoutriktiga scankoder -----------------------------

ULONG_PTR = ctypes.c_size_t

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56
VK_C = 0x43


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _INPUTUNION))


def _key_input(vk: int, up: bool, scancode: bool = True) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    flags = KEYEVENTF_KEYUP if up else 0
    if scancode:
        inp.ki = KEYBDINPUT(
            0, user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC),
            flags | KEYEVENTF_SCANCODE, 0, 0,
        )
    else:
        inp.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
    return inp


def _send_inputs(inputs: list[INPUT]) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput skickade %d av %d händelser", sent, len(inputs))


def _send_ctrl_combo(vk: int) -> None:
    # Fysiskt nedtryckta modifierare (t.ex. om dikteringstangenten är en
    # kombination) skulle förvandla genvägen till en annan — släpp dem.
    for held in (VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN, VK_RWIN):
        if user32.GetAsyncKeyState(held) & 0x8000:
            _send_inputs([_key_input(held, up=True, scancode=False)])
    _send_inputs(
        [
            _key_input(VK_CONTROL, up=False),
            _key_input(vk, up=False),
            _key_input(vk, up=True),
            _key_input(VK_CONTROL, up=True),
        ]
    )


def _send_ctrl_v() -> None:
    _send_ctrl_combo(VK_V)


# -- Klippbordet ----------------------------------------------------------------

# Microsoft-dokumenterade format som håller innehållet borta från
# Win+V-historiken och molnklippbordssynk.
_PRIVACY_FORMATS = (
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
)


def _open_clipboard(retries: int = 50, delay: float = 0.01) -> bool:
    # Klippbordet är delat; cbdhsvc och andra appar håller det låst ibland.
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(delay)
    return False


def _get_clipboard_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return None
    except Exception:
        return None
    finally:
        win32clipboard.CloseClipboard()


def _clear_clipboard() -> None:
    if not _open_clipboard():
        return
    try:
        win32clipboard.EmptyClipboard()
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard_text(text: str, private: bool = False) -> bool:
    if not _open_clipboard():
        log.warning("Kunde inte öppna klippbordet")
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        if private:
            zero = (0).to_bytes(4, "little")
            for fmt in _PRIVACY_FORMATS:
                try:
                    fmt_id = win32clipboard.RegisterClipboardFormat(fmt)
                    win32clipboard.SetClipboardData(fmt_id, zero)
                except Exception:
                    pass  # bäst ansträngning; inklistringen påverkas inte
        return True
    finally:
        win32clipboard.CloseClipboard()


# Generationsräknare för klippbordsåterställning: vid snabba dikteringar
# i följd ska bara den SISTA väntande återställningen köras, och den ska
# återställa användarens ursprungliga klippbord (sparat en gång) — inte en
# tidigare diktering.
_generation = 0
_saved_previous: str | None = None
# Minsta tid mellan klippbordsskrivningar: långsamma appar (Electron, RDP)
# läser klippbordet asynkront efter Ctrl+V — skrivs det över för snabbt
# klistras fel text in. Samma marginal som återställningen använder.
_PASTE_SPACING_S = 0.8
_last_paste_t = 0.0


# Osynlig avskiljare (U+2063) — dyker aldrig upp i riktig text.
_SENTINEL = "⁣viska-markering⁣"


def grab_selection(timeout_s: float = 0.6) -> str | None:
    """Hämtar markerad text i fokuserat fönster via Ctrl+C.

    Klippbordet sätts först till en vaktpost; ändras den inte inom
    tidsgränsen fanns ingen markering (originalet läggs då tillbaka
    direkt). Användarens ursprungliga klippbord sparas för den vanliga
    fördröjda återställningen efter inklistring.
    """
    global _generation, _saved_previous
    with _clipboard_lock:
        original = _get_clipboard_text()
        if _saved_previous is None:
            _saved_previous = original
        _generation += 1
        if not _set_clipboard_text(_SENTINEL, private=True):
            return None
        time.sleep(0.05)
        _send_ctrl_combo(VK_C)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            time.sleep(0.05)
            text = _get_clipboard_text()
            if text is not None and text != _SENTINEL:
                return text
        # Ingen markering: lägg tillbaka det SPARADE originalet — inte
        # ögonblicksbilden från nyss, som kan vara en diktering vars
        # fördröjda återställning vi just avbröt via generationsräknaren.
        previous, _saved_previous = _saved_previous, None
        if previous is not None:
            _set_clipboard_text(previous)
        else:
            _clear_clipboard()  # lämna aldrig kvar vaktposten
        return None


def restore_original_clipboard() -> None:
    """Läggs originalet tillbaka direkt (poleringen misslyckades)."""
    global _saved_previous
    with _clipboard_lock:
        previous, _saved_previous = _saved_previous, None
        if previous is not None:
            _set_clipboard_text(previous)


def copy_to_clipboard(text: str) -> None:
    """Vanlig kopiering (traymenyns "Kopiera senaste diktering")."""
    global _generation, _saved_previous
    if text:
        with _clipboard_lock:
            _generation += 1  # avbryt väntande återställning
            _saved_previous = None
            _set_clipboard_text(text)


# -- Publikt API ------------------------------------------------------------------

def inject(text: str, mode: str = "paste", restore_clipboard: bool = True) -> None:
    if not text:
        return
    if mode == "type":
        _type(text)
        # Poleringen kan ha sparat originalet via grab_selection — typläget
        # rör inte klippbordet så det ska tillbaka nu (annars "återställer"
        # en senare diktering ett urgammalt värde). No-op vid diktering.
        restore_original_clipboard()
    else:
        _paste(text, restore_clipboard)


def _paste(text: str, restore: bool) -> None:
    global _generation, _saved_previous, _last_paste_t
    with _clipboard_lock:
        wait = _last_paste_t + _PASTE_SPACING_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _generation += 1
        generation = _generation
        if restore and _saved_previous is None:
            _saved_previous = _get_clipboard_text()
        if not _set_clipboard_text(text, private=True):
            _type(text)  # klippbordet gick inte att ta – skriv i stället
            # Inklistringen uteblev: konsumera ev. sparat original direkt
            # (inuti låset — anropa inte restore_original_clipboard här).
            previous, _saved_previous = _saved_previous, None
            if previous is not None:
                _set_clipboard_text(previous)
            return
        time.sleep(0.05)  # låt klippbordet sätta sig innan Ctrl+V
        _send_ctrl_v()
        _last_paste_t = time.monotonic()
    if restore:
        threading.Thread(
            target=_restore_later, args=(generation,), daemon=True
        ).start()


def _restore_later(generation: int) -> None:
    global _saved_previous
    # Återställs klippbordet för tidigt hinner långsamma appar (Electron,
    # RDP) inte läsa det och inklistringen blir tom — vänta rejält.
    time.sleep(0.8)
    with _clipboard_lock:
        if generation != _generation:
            return  # en nyare diktering äger klippbordet nu
        previous, _saved_previous = _saved_previous, None
        if previous is not None:
            _set_clipboard_text(previous)


def _type(text: str) -> None:
    # SendInput med KEYEVENTF_UNICODE via pynput: layoutoberoende, klarar åäö.
    from pynput.keyboard import Controller

    Controller().type(text)
