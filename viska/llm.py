"""Lokal poleringsmodell (llama.cpp + Qwen) — "markera, tryck F8, få
omskriven text". Allt körs på egna GPU:n; ingen text lämnar datorn.

Modellen (~2,4 GB GGUF) laddas ner vid behov till %APPDATA%\\viska\\llm,
samma eftermonteringsmönster som GPU-stödet. Kräver i praktiken GPU:
4B-modellen på CPU tar tiotals sekunder per omskrivning.
"""

import logging
import shutil
import threading
import urllib.request
from pathlib import Path

from viska.config import CONFIG_DIR

log = logging.getLogger(__name__)

LLM_DIR = CONFIG_DIR / "llm"
_REMOVE_MARKER = CONFIG_DIR / "llm.remove"
DOWNLOAD_SIZE_GB = 2.4


def model_path(cfg: dict) -> Path:
    return LLM_DIR / cfg["polish_model_file"]


def model_installed(cfg: dict) -> bool:
    if _REMOVE_MARKER.exists():
        return False
    return model_path(cfg).is_file()


def download_model(cfg: dict, progress=None, cancelled=None) -> None:
    url = (
        f"https://huggingface.co/{cfg['polish_model_repo']}"
        f"/resolve/main/{cfg['polish_model_file']}"
    )
    LLM_DIR.mkdir(parents=True, exist_ok=True)
    _REMOVE_MARKER.unlink(missing_ok=True)
    dest = model_path(cfg)
    tmp = dest.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            header = r.headers.get("Content-Length")
            expected = int(header) if header else None
            total = expected or 1
            done = 0
            while chunk := r.read(1 << 20):
                if cancelled and cancelled():
                    raise InterruptedError("Nedladdningen avbröts")
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress("Laddar ner poleringsmodellen…", min(done / total, 0.99))
        # http.client sväljer avbrutna anslutningar (read() returnerar bara
        # b"") — utan längdkontrollen skulle en halv fil installeras som
        # giltig modell och krascha varje polering.
        if expected is not None and done != expected:
            raise RuntimeError(
                f"Nedladdningen avbröts i förtid ({done} av {expected} byte)"
            )
        tmp.replace(dest)
        log.info("Poleringsmodell installerad: %s", dest)
        if progress:
            progress("Klart", 1.0)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def remove_model(cfg: dict) -> bool:
    """True om filen försvann nu; False = låst (laddad) — tas bort vid nästa start."""
    try:
        shutil.rmtree(LLM_DIR)
        _REMOVE_MARKER.unlink(missing_ok=True)
        return True
    except FileNotFoundError:
        _REMOVE_MARKER.unlink(missing_ok=True)
        return True
    except OSError:
        _REMOVE_MARKER.touch()
        return False


def cleanup_pending_removal() -> None:
    if _REMOVE_MARKER.exists():
        shutil.rmtree(LLM_DIR, ignore_errors=True)
        _REMOVE_MARKER.unlink(missing_ok=True)


def _system_prompt(style: str) -> str:
    task = (
        style.strip()
        or "Skriv om texten till korrekt, naturlig text med samma innebörd och ton."
    )
    return (
        "Du är en textredigerare.\n"
        f"UPPGIFT: {task}\n"
        "Regler som alltid gäller:\n"
        "- Ta bort alla utfyllnadsord (eh, öh, alltså, liksom, typ, ju).\n"
        "- Rätta grammatik och stavning. Behåll textens språk.\n"
        "- Ändra ALDRIG tidsangivelser, namn, siffror eller fakta — "
        "'imorgon' förblir 'imorgon'.\n"
        "- Hitta inte på ny information och använd aldrig platshållare.\n"
        "- Svara enbart med resultatet, ingen förklaring."
    )


class Polisher:
    """Lat laddning: modellen tas in i VRAM först vid första poleringen
    (eller explicit warmup) och bor sedan kvar — omskrivningar därefter
    tar under en sekund."""

    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._llm = None
        self._lock = threading.Lock()

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._llm is not None:
                return
            # ctranslate2:s DLL-uppslag återanvänds — llama.cpp:s CUDA-bygge
            # behöver samma kataloger (cublas/cudart).
            from viska.transcribe import _add_nvidia_dll_dirs

            _add_nvidia_dll_dirs()
            from llama_cpp import Llama

            path = model_path(self._cfg)
            log.info("Laddar poleringsmodellen %s…", path.name)
            self._llm = Llama(
                model_path=str(path), n_gpu_layers=-1, n_ctx=4096, verbose=False
            )
            # Absorbera förstagångens kernel-JIT här i stället för vid
            # användarens första F8.
            self._llm.create_chat_completion(
                messages=[{"role": "user", "content": "ok"}], max_tokens=1
            )
            log.info("Poleringsmodellen redo")

    def polish(self, text: str, style: str = "") -> str:
        self.ensure_loaded()
        with self._lock:
            out = self._llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": _system_prompt(style)},
                    {"role": "user", "content": text},
                ],
                # Girig avkodning: mest texttrogna omskrivningen — 4B-modeller
                # börjar annars gärna "förbättra" fakta och tidsangivelser.
                temperature=0.0,
                # Teckenbaserat (split() räknar minifierad kod/URL:er som
                # ETT ord); llama.cpp klämmer tyst mot kontextfönstret,
                # därför avslöjas trunkering via finish_reason nedan.
                max_tokens=min(len(text) // 2 + 400, 3072),
            )
        choice = out["choices"][0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError(
                "texten är för lång för modellens kontextfönster — "
                "polera en mindre markering"
            )
        result = choice["message"]["content"].strip()
        return result or text
