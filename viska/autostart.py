"""Starta med Windows: genväg i användarens Startup-mapp.

Tillståndet härleds ur genvägens existens — inget lagras i config, så
det kan aldrig komma i osynk med verkligheten.
"""

import os
import sys
from pathlib import Path


def startup_lnk() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / "Viska.lnk"
    )


def enabled() -> bool:
    return startup_lnk().exists()


def install() -> Path:
    import win32com.client

    if getattr(sys, "frozen", False):  # PyInstaller-exe
        target = Path(sys.executable)
        args = ""
        workdir = target.parent
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        target = pythonw if pythonw.exists() else Path(sys.executable)
        args = "-m viska"
        workdir = Path(__file__).resolve().parents[1]
    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortCut(str(startup_lnk()))
    lnk.TargetPath = str(target)
    lnk.Arguments = args
    lnk.WorkingDirectory = str(workdir)
    lnk.Description = "Viska – lokal diktering"
    lnk.Save()
    return startup_lnk()


def uninstall() -> bool:
    lnk = startup_lnk()
    if lnk.exists():
        lnk.unlink()
        return True
    return False
