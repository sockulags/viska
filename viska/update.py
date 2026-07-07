"""Versionskontroll och inbyggd uppdaterare mot GitHub releases.

Versionskollen hämtar bara senaste releasens metadata (ett anonymt GET) och
är helt ofarlig — den får aldrig störa dikteringen. Är repot privat krävs en
personlig token (config-nyckeln github_token) både för kollen och för att
ladda ner asseten; den skickas som Authorization-header.

Den inbyggda uppdateraren (bara i fryst exe-läge, se app._on_update_clicked)
laddar ner release-zippen till %TEMP%, packar upp den och startar en
fristående bat som speglar den nya versionen över installationskatalogen och
startar om Viska. Varje steg före bat-starten är rent additivt (temp-filer):
misslyckas något rörs den körande installationen aldrig, och appen faller
tillbaka på att öppna releases-sidan i webbläsaren precis som förr.
"""

import json
import logging
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

REPO = "sockulags/viska"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"


def _parse(version: str) -> tuple:
    return tuple(int(p) for p in version.lstrip("v").split(".") if p.isdigit())


class Release:
    """Ett GitHub-release: versionstagg och dess nedladdningsbara assets.

    Varje asset är en dict {"name", "url", "size"} där url är GitHubs
    API-URL för asseten (fungerar för både publika och privata repon med
    Accept: application/octet-stream; privata kräver dessutom token)."""

    def __init__(self, tag: str, assets: list[dict]):
        self.tag = tag
        self.assets = assets

    @property
    def version(self) -> str:
        return self.tag.lstrip("v")


def _request(
    url: str, token: str = "", accept: str = "application/vnd.github+json"
) -> urllib.request.Request:
    headers = {"Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def check_async(current: str, on_newer, token: str = "") -> None:
    """on_newer(release: Release) anropas från en bakgrundstråd om en nyare
    version finns. Misslyckas tyst (offline, privat repo utan token, rate
    limit) — kollen får aldrig störa dikteringen."""

    def work():
        try:
            with urllib.request.urlopen(
                _request(RELEASES_API, token), timeout=8
            ) as r:
                data = json.load(r)
            tag = data.get("tag_name", "")
            if tag and _parse(tag) > _parse(current):
                assets = [
                    {
                        "name": a.get("name", ""),
                        "url": a.get("url", ""),
                        "size": a.get("size", 0),
                    }
                    for a in data.get("assets", [])
                    if a.get("name") and a.get("url")
                ]
                log.info("Ny version finns: %s (nuvarande %s)", tag, current)
                on_newer(Release(tag, assets))
        except Exception:
            pass  # offline, privat repo utan token, rate limit — ofarligt

    threading.Thread(target=work, daemon=True, name="viska-update").start()


def select_asset(release: Release, prefer_gpu: bool = False) -> dict | None:
    """Väljer rätt zip-asset för uppdateringen. Föredrar samma variant
    (gpu/universal) som den körande installationen så en /MIR-spegling inte
    tar bort t.ex. bundlade CUDA-DLL:er. Faller tillbaka på valfri
    Viska-*.zip om de förväntade namnen saknas."""
    version = release.version
    plain = f"Viska-{version}.zip"
    gpu = f"Viska-{version}-gpu.zip"
    by_name = {a["name"]: a for a in release.assets}
    for name in ([gpu, plain] if prefer_gpu else [plain, gpu]):
        if name in by_name:
            return by_name[name]
    for a in release.assets:
        if a["name"].startswith("Viska-") and a["name"].endswith(".zip"):
            return a
    return None


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHubs asset-API svarar 302 till en signerad S3-URL. Authorization-
    headern får INTE följa med dit: S3 avvisar dubbel autentisering, och
    dessutom ska aldrig en token läcka till en annan host. Ta därför bort
    den vid omdirigering."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def download_asset(asset: dict, dest, token: str = "", progress=None, cancelled=None) -> None:
    """Laddar ner en release-asset till dest. progress(andel 0..1) anropas
    löpande; cancelled() -> bool kollas mellan block. Kastar
    urllib.error.HTTPError vid 401/403/404 (privat repo utan giltig token) —
    anroparen faller då tillbaka på releases-sidan."""
    opener = urllib.request.build_opener(_StripAuthOnRedirect)
    req = _request(asset["url"], token, accept="application/octet-stream")
    with opener.open(req, timeout=60) as r, open(dest, "wb") as f:
        expected = asset.get("size") or int(r.headers.get("Content-Length") or 0)
        total = expected or 1
        done = 0
        while chunk := r.read(1 << 20):
            if cancelled and cancelled():
                raise InterruptedError("Nedladdningen avbröts")
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(min(done / total, 0.99))
    # http.client sväljer avbrutna anslutningar (read() returnerar b"") —
    # utan längdkontrollen skulle en trunkerad zip packas upp som giltig.
    if expected and done != expected:
        raise RuntimeError(
            f"Nedladdningen avbröts i förtid ({done} av {expected} byte)"
        )
    if progress:
        progress(1.0)


def fresh_workdir() -> Path:
    """Ren arbetskatalog i %TEMP% för nedladdning och uppackning. Rensar
    eventuella rester från en tidigare, avbruten uppdatering först."""
    root = Path(tempfile.gettempdir()) / "viska-update"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def extract_update(zip_path, dest_dir) -> Path | None:
    """Packar upp zippen och returnerar katalogen som innehåller Viska.exe +
    _internal/ (release-zippen har dem under en Viska/-mapp), eller None om
    de saknas i uppackningen."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            target = (root / member).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError(f"Osäker sökväg i zip: {member}")  # zip-slip
        z.extractall(dest_dir)
    return _find_app_dir(dest_dir)


def _find_app_dir(root: Path) -> Path | None:
    candidates = [root] + [p for p in root.iterdir() if p.is_dir()]
    for c in candidates:
        if (c / "Viska.exe").is_file() and (c / "_internal").is_dir():
            return c
    return None


def write_updater_bat(pid: int, app_dir, install_dir, work_root) -> Path:
    """Skriver uppdaterings-baten till %TEMP% och returnerar dess sökväg.

    Baten väntar tills Viskas PID försvunnit (loop med tasklist + timeout),
    speglar app_dir över install_dir med robocopy /MIR, startar nya
    Viska.exe, städar work_root och raderar sig själv. Den RÖR INTE
    install_dir förrän den körande Viska avslutats, och startar appen igen
    även om speglingen delvis misslyckas — en halvspeglad katalog är sämre
    än ett omstartsförsök, och robocopy återupptar mot samma mål nästa gång.

    Baten ligger i %TEMP% (inte i work_root) så att den kan radera work_root
    utan att låsa sig själv. Loggen skrivs bredvid för felsökning."""
    tmp = Path(tempfile.gettempdir())
    bat = tmp / "viska-update.bat"
    log_path = tmp / "viska-update.log"
    exe = Path(install_dir) / "Viska.exe"
    # chcp 65001 + UTF-8 så åäö i temp-/installationssökvägar (användarnamn)
    # tolkas rätt. ping i stället för timeout: timeout kräver konsol och
    # kraschar i en fristående (konsollös) process. Alla externa verktyg
    # anropas med full System32-sökväg så de inte kan skuggas av något annat
    # på PATH (t.ex. ett Unix-"find" från Git Bash). Självraderingen sker med
    # "(goto) 2>nul & del" så inget "batch file cannot be found" spills ut.
    script = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "setlocal enableextensions\r\n"
        'set "SYS=%SystemRoot%\\System32"\r\n'
        f'set "PID={pid}"\r\n'
        f'set "SRC={app_dir}"\r\n'
        f'set "DST={install_dir}"\r\n'
        f'set "EXE={exe}"\r\n'
        f'set "WORK={work_root}"\r\n'
        f'set "LOG={log_path}"\r\n'
        'echo Viska-uppdatering %DATE% %TIME% PID %PID%> "%LOG%"\r\n'
        "set /a n=0\r\n"
        ":wait\r\n"
        '"%SYS%\\tasklist.exe" /FI "PID eq %PID%" 2>nul | "%SYS%\\find.exe" "%PID%" >nul\r\n'
        "if errorlevel 1 goto go\r\n"
        "set /a n+=1\r\n"
        "if %n% GEQ 60 goto go\r\n"
        '"%SYS%\\ping.exe" -n 2 127.0.0.1 >nul\r\n'
        "goto wait\r\n"
        ":go\r\n"
        '"%SYS%\\robocopy.exe" "%SRC%" "%DST%" /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1\r\n'
        "if %ERRORLEVEL% GEQ 8 goto err\r\n"
        'start "" "%EXE%"\r\n'
        "goto done\r\n"
        ":err\r\n"
        'echo robocopy misslyckades med kod %ERRORLEVEL%>> "%LOG%"\r\n'
        'if exist "%EXE%" start "" "%EXE%"\r\n'
        ":done\r\n"
        'rmdir /S /Q "%WORK%" >nul 2>&1\r\n'
        '(goto) 2>nul & del /F /Q "%~f0"\r\n'
    )
    bat.write_text(script, encoding="utf-8")
    return bat


def launch_updater(bat) -> None:
    """Startar baten fristående: ingen konsol och överlever appens exit."""
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
