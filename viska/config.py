"""Konfiguration som läses från %APPDATA%\\viska\\config.json."""

import json
import os
from pathlib import Path

APP_NAME = "viska"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "viska.log"

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
    # språk byts ALDRIG automatiskt utan bara via uttrycklig profil).
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
    "polish_model_repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
    "polish_model_file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    # Dikteringshistorik i traymenyn (hålls enbart i RAM, aldrig på disk).
    "history_size": 20,
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
