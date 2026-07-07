"""Versionskontroll mot GitHub releases.

Hämtar bara senaste versionsnumret (ett anonymt GET); ingen data om
användaren skickas. Misslyckas tyst — uppdateringskollen får aldrig
störa dikteringen.
"""

import json
import logging
import threading
import urllib.request

log = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/sockulags/viska/releases/latest"
RELEASES_URL = "https://github.com/sockulags/viska/releases/latest"


def _parse(version: str) -> tuple:
    return tuple(int(p) for p in version.lstrip("v").split(".") if p.isdigit())


def check_async(current: str, on_newer) -> None:
    """on_newer(tag: str) anropas från en bakgrundstråd om nyare finns."""

    def work():
        try:
            req = urllib.request.Request(
                RELEASES_API, headers={"Accept": "application/vnd.github+json"}
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                tag = json.load(r).get("tag_name", "")
            if tag and _parse(tag) > _parse(current):
                log.info("Ny version finns: %s (nuvarande %s)", tag, current)
                on_newer(tag)
        except Exception:
            pass  # offline, privat repo, rate limit — helt ofarligt

    threading.Thread(target=work, daemon=True, name="viska-update").start()
