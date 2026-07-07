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
    # Hur ofta ljudet omtranskriberas, och hur lång paus som gör en fras
    # färdig (färdig text skrivs in och kan inte ändras i efterhand).
    "live_interval_s": 0.7,
    "live_commit_silence_s": 0.6,
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
