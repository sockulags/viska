"""Konfiguration som läses från %APPDATA%\\viska\\config.json."""

import json
import os
from collections.abc import Iterable
from pathlib import Path

APP_NAME = "viska"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "viska.log"
HISTORY_PATH = CONFIG_DIR / "history.json"

DEFAULTS = {
    # Tangent som styr dikteringen. Kort tryck = starta/stoppa,
    # håll inne = prata så länge tangenten hålls (walkie-talkie).
    # T.ex. "f9", "f8", "scroll lock", "ctrl+alt+d".
    "hotkey": "f9",
    # Tryck kortare än så här räknas som "växla", längre som "håll".
    "hold_threshold_s": 0.35,
    # Whisper-modell för svenska. "auto" = KBLab/kb-whisper-large på GPU,
    # KBLab/kb-whisper-small på CPU. Kan även vara ett explicit namn som
    # "KBLab/kb-whisper-medium" eller ett vanligt som "large-v3".
    "model": "auto",
    # Separat modell för engelska (KB-Whisper är enbart svensktränad).
    # Laddas först när man växlar språk i traymenyn.
    "model_en": "large-v3-turbo",
    # Aktivt språk: "sv" eller "en". Kan växlas i traymenyn.
    "language": "sv",
    # Ordlista/kontext: namn och facktermer som modellen ska stava rätt
    # (Whispers initial_prompt). Ställs enklast in via Inställningar.
    "initial_prompt": "",
    # "auto" provar GPU först och faller tillbaka på CPU. Även "cuda"/"cpu".
    "device": "auto",
    # null = float16 på GPU, int8 på CPU. "int8_float16" halverar VRAM på
    # GPU (~2 GB för large i stället för ~4 GB) med minimal kvalitetsförlust.
    "compute_type": None,
    # null = 5 på GPU, 2 på CPU (lägre = snabbare, marginellt sämre).
    "beam_size": None,
    # "paste" klistrar in via klippbordet (snabbast, säkrast för åäö).
    # "type" simulerar Unicode-tangenttryck tecken för tecken.
    "inject_mode": "paste",
    "restore_clipboard": True,
    "append_space": True,
    "max_recording_s": 300,
    # Liveläge: färdiga fraser skrivs in medan du fortfarande pratar; den
    # pågående frasen förhandsvisas i overlayn. "auto" = på när GPU
    # används (CPU hinner inte transkribera om i realtid). Även true/false.
    "live": "auto",
    # Profiler per app: nyckel = processnamn (gemener, t.ex. "code.exe").
    # Värden: initial_prompt (ordlista utöver den globala), inject_mode
    # ("paste"/"type"), language ("sv"/"en"; utelämnad = aktivt språk —
    # språk byts ALDRIG automatiskt utan bara via uttrycklig profil),
    # polish_prompt (egen uppgift vid polering) och flow (true = polera
    # dikteringen automatiskt; utelämnad = ärver den globala "flow" nedan).
    "app_profiles": {},
    # Röstkommandon: talade fraser som blir formatering i stället för
    # text. Lägg till egna eller ta bort rader för att stänga av.
    "voice_commands": {
        "ny rad": "\n",
        "nytt stycke": "\n\n",
        "new line": "\n",
        "new paragraph": "\n\n",
    },
    # Polering: markera text var som helst, tryck tangenten, få den
    # omskriven av en lokal LLM. Modellen laddas ner via Inställningar.
    "polish_hotkey": "f8",
    # Global uppgiftsbeskrivning (tom = "skriv om till korrekt text").
    # Per-app-varianter läggs i app_profiles som polish_prompt.
    "polish_prompt": "",
    # "builtin" = inbyggd motor (llama.cpp, laddas ner via Inställningar).
    # "ollama" = återanvänd en körande Ollama och dess modeller — inget
    # extra laddas ner.
    "polish_backend": "builtin",
    "polish_ollama_model": "qwen3.5:9b",
    "polish_ollama_url": "http://localhost:11434",
    "polish_model_repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
    # Filnamn i %APPDATA%\viska\llm — eller en absolut sökväg till en egen
    # GGUF (t.ex. en Ollama-blob).
    "polish_model_file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    # Flow-läge (Wispr Flow-stil): polera hela dikteringen automatiskt
    # innan den skrivs in, i stället för att skriva in råtexten. Global
    # standard som profiler ärver; slås på per app via "flow" i
    # app_profiles. Är poleringen otillgänglig, kastar fel eller tar för
    # lång tid skrivs råtexten in precis som vanlig diktering.
    "flow": False,
    # Dikteringshistorik i traymenyn. Hålls enbart i RAM som standard —
    # "history_persist" (kryssruta i Inställningar → Avancerat) sparar den
    # i stället till history.json vid varje ändring och läser in den igen
    # vid start.
    "history_size": 20,
    "history_persist": False,
    # Kolla efter ny version mot GitHub releases vid start (endast en
    # versionssiffra hämtas; ingen data skickas).
    "check_updates": True,
    # Hur ofta ljudet omtranskriberas, och hur lång paus som gör en fras
    # färdig (färdig text skrivs in och kan inte ändras i efterhand).
    # Lägre = snabbare inskrivning men committar hellre vid korta pauser.
    "live_interval_s": 0.4,
    "live_commit_silence_s": 0.45,
    # null = systemets standardmikrofon. Annars namn eller index från
    # `viska --list-devices`.
    "input_device": None,
    # Håller mikrofonströmmen öppen mot en liten RAM-ringbuffert så att
    # första stavelsen inte klipps. Windows visar då mikrofonikonen
    # konstant; sätt false om det stör (ger lätt klippt start).
    "keep_mic_open": True,
    "preroll_s": 0.25,
    "sounds": True,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass  # trasig fil: kör vidare på defaults, skriv inte över
    else:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_history() -> list[str]:
    """Sparad historik (nyast först), eller tom lista om opt-in-läget är
    avstängt, filen saknas, eller är trasig."""
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []  # trasig fil: kör vidare utan historik, skriv inte över


def save_history(items: Iterable[str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(list(items), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def delete_history() -> None:
    """Körs när opt-in-läget stängs av — filen ska inte bli kvar på disk."""
    HISTORY_PATH.unlink(missing_ok=True)
