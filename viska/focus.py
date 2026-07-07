"""Vilken app har fokus? Används av per-app-profilerna: ordlista och
inskrivningssätt väljs utifrån programmet som ska ta emot texten.
Avläses vid inspelningsstart — det är dit texten kommer att skrivas.
"""

import logging

import win32api
import win32con
import win32gui
import win32process

log = logging.getLogger(__name__)


def visible_apps() -> list[str]:
    """Processnamn för alla synliga fönster — till profilväljaren."""
    names: set[str] = set()

    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                handle = win32api.OpenProcess(
                    win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                try:
                    path = win32process.GetModuleFileNameEx(handle, 0)
                finally:
                    win32api.CloseHandle(handle)
                names.add(path.rsplit("\\", 1)[-1].lower())
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    names.discard("viska.exe")
    return sorted(names)


# Fönsterklasser för konsoler: Ctrl+C utan markering blir SIGINT där och
# kan döda användarens körande process — poleringen vägrar hellre.
_TERMINAL_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
_TERMINAL_APPS = {"mintty.exe", "alacritty.exe", "wezterm-gui.exe", "hyper.exe"}


def foreground_is_terminal() -> bool:
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd and win32gui.GetClassName(hwnd) in _TERMINAL_CLASSES:
            return True
    except Exception:
        pass
    return (foreground_app() or "") in _TERMINAL_APPS


def foreground_app() -> str | None:
    """Processnamn i gemener för fokuserat fönster, t.ex. "code.exe"."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        try:
            path = win32process.GetModuleFileNameEx(handle, 0)
        finally:
            win32api.CloseHandle(handle)
        return path.rsplit("\\", 1)[-1].lower()
    except Exception:
        # Upphöjda fönster m.m. kan neka insyn — profilen hoppar då bara över.
        return None


def foreground_window() -> int | None:
    """HWND för fokuserat fönster (eller None). Fryses vid inspelningsstart
    så flow-läget kan skriva tillbaka den polerade texten i rätt fönster
    även om användaren hunnit byta fokus medan poleringen pågick."""
    try:
        return win32gui.GetForegroundWindow() or None
    except Exception:
        return None


def focus_window(hwnd: int) -> bool:
    """Ser till att hwnd ligger i förgrunden inför en inskrivning. Har fokus
    inte flyttat (det vanliga i flow-läget, där dikteringen hålls kvar tills
    texten skrivits in) är det en no-op och ingen fokusstöld sker.

    SetForegroundWindow nekas normalt en bakgrundsapp; standardknepet är att
    tillfälligt koppla ihop inmatningskön med förgrundstråden så att bytet
    tillåts. Bäst ansträngning: misslyckas det skrivs texten ändå in i det
    fönster som råkar ha fokus (råversionen finns kvar i historiken)."""
    try:
        if win32gui.GetForegroundWindow() == hwnd:
            return True  # fokus kvar (det vanliga) — ingen fokusstöld
        if not win32gui.IsWindow(hwnd):
            return False  # fönstret stängt under poleringen
        cur_tid = win32api.GetCurrentThreadId()
        fg = win32gui.GetForegroundWindow()
        fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
        attached = bool(fg_tid) and fg_tid != cur_tid
        if attached:
            try:
                win32process.AttachThreadInput(cur_tid, fg_tid, True)
            except Exception:
                attached = False
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass  # nekat trots knepet — inskrivningen faller tillbaka på fokus
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(cur_tid, fg_tid, False)
                except Exception:
                    pass
        ok = win32gui.GetForegroundWindow() == hwnd
        if not ok:
            log.warning("Kunde inte återställa fokus till målfönstret före inskrivning")
        return ok
    except Exception:
        log.exception("Fel vid fokusåterställning")
        return False
