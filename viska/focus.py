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
