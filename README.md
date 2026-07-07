# Viska

Lokal diktering för Windows — tryck en tangent, prata, och texten skrivs
där markören är, i vilken app som helst. Som Wispr Flow, fast **allt körs
på din egen dator**: ljudet lämnar aldrig maskinen.

Taligenkänningen görs av [KB-Whisper](https://huggingface.co/KBLab/kb-whisper-large)
— Kungliga bibliotekets svensktränade Whisper-modeller (50 000+ timmar
svenskt tal, ca 47 % lägre felprocent på svenska än OpenAI:s största
Whisper-modell) — via [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Krav

- Windows 11 (10 fungerar sannolikt också)
- Python 3.11+ och [uv](https://docs.astral.sh/uv/)
- Mikrofon
- NVIDIA-GPU rekommenderas (transkribering på under en sekund); utan GPU
  används CPU med en mindre modell — fortfarande snabbt nog för diktering

## Installation

```
git clone <repo> viska
cd viska
uv sync --extra gpu     # utan NVIDIA-GPU: uv sync
uv run viska --check    # laddar ner modellen (~3 GB på GPU, ~0,5 GB på CPU)
```

`--check` ska sluta med `Enhet: cuda` (eller `cpu`). Starta sedan appen:

```
uv run viska
```

En mikrofonikon dyker upp i systemfältet och en grön remsa visar när
Viska är redo.

## Användning

Standardtangenten är **F9**:

- **Håll inne** och prata — släpp när du är klar (walkie-talkie).
- **Tryck kort** för att börja — prata så länge du vill — tryck igen för
  att stoppa.

Texten klistras in där markören är: i mejl, chattar, Word, webbläsare,
kodredigerare. En remsa nertill på skärmen visar läget (spelar in /
transkriberar) och korta pip markerar start och stopp.

**Traymenyn** (högerklick på ikonen): växla svenska/engelska, kopiera
senaste dikteringen igen (om den hamnade fel), byta till ny
standardmikrofon, öppna inställningar/logg, avsluta.

### Liveläge

Med GPU skrivs texten in **medan du pratar**: varje fras blir färdig när
du gör en kort paus (~0,6 s) och skrivs in direkt, medan frasen du håller
på med förhandsvisas i remsan nertill. Whisper är ingen streamingmodell,
så redan inskriven text kan inte ändras i efterhand — det är därför bara
färdiga fraser skrivs in, aldrig den pågående. Stäng av med `"live":
false` i config om du hellre får hela dikteringen på en gång.

## Konfiguration

`%APPDATA%\viska\config.json` (skapas vid första start; starta om Viska
efter ändringar):

| Nyckel | Standard | Betydelse |
|---|---|---|
| `hotkey` | `"f9"` | Dikteringstangent, t.ex. `"f8"`, `"scroll lock"`, `"ctrl+alt+d"` |
| `hold_threshold_s` | `0.35` | Kortare tryck = växla, längre = håll-och-prata |
| `model` | `"auto"` | `auto` = kb-whisper-large på GPU, kb-whisper-small på CPU |
| `model_en` | `"large-v3-turbo"` | Modell för engelska (laddas vid språkbyte) |
| `language` | `"sv"` | Aktivt språk; växlas enklast i traymenyn |
| `device` | `"auto"` | `auto`/`cuda`/`cpu` |
| `compute_type` | `null` | `null` = float16 på GPU, int8 på CPU. `"int8_float16"` halverar VRAM på GPU |
| `beam_size` | `null` | `null` = 5 på GPU, 2 på CPU |
| `inject_mode` | `"paste"` | `paste` (klippbord+Ctrl+V) eller `type` (tecken för tecken) |
| `restore_clipboard` | `true` | Återställ gamla klippbordet efter inklistring |
| `append_space` | `true` | Mellanslag efter varje diktering |
| `max_recording_s` | `300` | Säkerhetsstopp för inspelning |
| `live` | `"auto"` | Liveläge: `auto` = på med GPU, av på CPU. Även `true`/`false` |
| `live_interval_s` | `0.7` | Hur ofta ljudet omtranskriberas i liveläget |
| `live_commit_silence_s` | `0.6` | Paus som gör en fras färdig (skrivs in, ändras aldrig) |
| `input_device` | `null` | `null` = standardmikrofon, annars index från `--list-devices` |
| `keep_mic_open` | `true` | Öppen mikrofonström = ingen klippt första stavelse |
| `sounds` | `true` | Pip vid start/stopp |

## Autostart

```
uv run viska --install-autostart
```

Skapar en genväg i Startup-mappen som kör Viska tyst (pythonw, inget
konsolfönster) vid inloggning. Ta bort med `--uninstall-autostart`.

## Engelska

KB-Whisper är enbart tränad på svenska. Växla till engelska i traymenyn —
då laddas en separat flerspråkig modell (`model_en`) första gången, och
dikteringen körs mot den tills du växlar tillbaka.

## Integritet

- Ljud och text lämnar aldrig datorn. Nätverk används bara första gången
  en modell laddas ner från Hugging Face.
- Dikteringar som klistras in via klippbordet märks med Windows
  undantagsformat så att de **inte** hamnar i Win+V-historiken och
  **inte** synkas via molnklippbordet.
- Med `keep_mic_open` lyssnar mikrofonströmmen bara in i en ringbuffert
  på ~0,5 sekunder i RAM som ständigt skrivs över; inget sparas.

## Felsökning

- **Loggen** finns i `%APPDATA%\viska\viska.log`.
- **"Modellen kunde inte laddas" med GPU**: kontrollera att `uv sync
  --extra gpu` kördes (cuBLAS/cuDNN-paketen krävs för CUDA). Viska faller
  automatiskt tillbaka på CPU.
- **Texten skrivs inte in i admin-fönster**: Windows (UIPI) blockerar
  inmatning från vanliga program till upphöjda fönster. Kör Viska som
  administratör om du behöver diktera där.
- **Ctrl+V fungerar inte i vissa terminaler/TUI-program**: sätt
  `inject_mode` till `"type"`.
- **Tangenten reagerar inte**: något annat program äger genvägen
  (RegisterHotKey). Byt `hotkey` i config.
- **Fel mikrofon efter dockning/headsetbyte**: traymenyn → "Byt till ny
  standardmikrofon".

## Verktyg för test

```
uv run viska --list-devices          # lista ljudenheter
uv run viska --transcribe fil.wav    # transkribera en fil (även .ogg/.mp3)
```
