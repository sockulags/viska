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

**Traymenyn** (högerklick på ikonen; röd under inspelning): Inställningar,
historik (senaste dikteringarna, klicka för att kopiera — hålls i RAM som
standard, se Integritet), växla svenska/engelska, byta mikrofon, avsluta.
Finns en ny version på GitHub visas en hämta-rad överst. I den fristående
.exe:n (se nedan) laddar hämta-raden ner, installerar och startar om Viska
direkt — se Uppdatering. I utvecklingsläge öppnar den releases-sidan.

**Inställningarna** har fyra flikar: *Allmänt* (språk, tangent med
tryck-för-att-välja, livekänsla Lugn/Normal/Snabb, kvalitet i klartext,
GPU-stöd), *Ordlista* (global ordlista + per app-profiler), *Polering*
(poleringsmotor, global standard, mallbibliotek och flow) och *Avancerat*
(råa tekniska värden för den som vill).

**Ordlista:** skriv in namn och facktermer (t.ex. "Avanza, Smask") så
stavar modellen dem rätt. **Per app-profiler:** ge en app egen extra
ordlista, eget inskrivningssätt (t.ex. tecken för tecken i terminaler),
egen poleringsuppgift (en mall eller egen text) och — om du uttryckligen
väljer det — eget språk. Språk byts aldrig automatiskt.

**Röstkommandon:** säg "ny rad" eller "nytt stycke" (engelska: "new
line", "new paragraph") så skrivs radbrytning i stället för orden.
Fraserna redigeras under `voice_commands` i config-filen — ta bort en
rad för att stänga av den. Obs: radbrytningar i terminaler kan trigga
"klistra in flera rader?"-varningen.

**Starta med Windows:** kryssruta i Inställningar → Allmänt (eller
`--install-autostart` från terminal).

### Polering (AI)

Markera text **var som helst** (även sånt du inte dikterat), tryck
**F8**, och texten skrivs om av en lokal språkmodell: utfyllnadsord
bort, grammatik rättad, innebörd bevarad.

Vad poleringen ska *göra* styrs av en **poleringsuppgift**. Under
Inställningar → Polering finns ett **mallbibliotek** med färdiga mallar
(Mejl, Commit-text, Chattsvar, Formell text, Kortfattat) som går att
redigera, ta bort eller komplettera med egna. Både den **globala
standarden** och varje **app-profil** (Ordlista-fliken) pekar antingen på
en mall eller på **Egen text** (egen fritext). Ändrar du en mall slår det
igenom överallt där den används; en app-profil kan dessutom ärva den
globala standarden. Så kan Outlook-profilen peka på mallen "Mejl" medan
kodredigeraren pekar på "Commit-text" — och båda uppdateras samtidigt om
du finslipar mallen. (Pekar en profil på en mall du senare raderar faller
den tillbaka på den globala standarden.)

**Välj ton för en enskild polering:** tryck **F8 två gånger snabbt** så
dyker en liten meny upp vid pillen med *Appens standard* (förvald) följd av
mallarna ur mallbiblioteket. Valet gäller bara den poleringen — inga
inställningar ändras. Esc eller klick utanför stänger menyn.

Originaltexten läggs i historiken innan den ersätts, så en polering du
inte gillar går att ångra: öppna traymenyns historik och klicka på
originalet för att kopiera tillbaka det. Poleringen följer aktivt språk —
är engelska valt (traymenyn) skrivs texten om på och behåller engelska.

Kräver GPU-läget samt en engångsnedladdning av modellen (~2,4 GB, Qwen3
4B) via Inställningar → Polering. Allt körs lokalt; första poleringen
efter start tar några sekunder extra (modellen laddas), därefter under
en sekund. Obs: en liten modell kan göra små språkliga missar — läs
igenom innan du skickar något viktigt. Modellen byts under
`polish_model_repo`/`polish_model_file` i config.

**Flow-läge** kör samma polering på *dikteringen* i stället för markerad
text: talet skrivs inte in medan du pratar (pillen förhandsvisar det
bara), och när du stoppar poleras hela transkriptet automatiskt och
skrivs in på en gång — precis som Wispr Flow. Slå på det globalt under
Inställningar → Polering eller per app i Ordlista-profilen (kryssrutan visas
när poleringen är nedladdad); en app-profil kan kombinera flow med sin egen
poleringsuppgift, t.ex. mallen "Mejl". Skulle poleringen vara otillgänglig, krångla eller
ta för lång tid skrivs råtexten in precis som vanligt — dikteringen går
aldrig förlorad. Både rå- och den polerade versionen hamnar i historiken
(råtexten går att hämta i traymenyn).

### Liveläge

Med GPU skrivs texten in **medan du pratar**: varje fras blir färdig när
du gör en kort paus och skrivs in direkt, medan frasen du håller på med
förhandsvisas i pillen nertill (med levande ljudnivåmätare). Slutar
frasen med punkt/frågetecken räcker en kortare paus — inskrivningen
följer meningsrytmen. Whisper är ingen streamingmodell, så redan
inskriven text kan inte ändras i efterhand — det är därför bara färdiga
fraser skrivs in, aldrig den pågående. Känslan justeras i Inställningar
(Livekänsla: Lugn/Normal/Snabb); stäng av liveläget helt där om du
hellre får hela dikteringen på en gång.

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
| `history_size` | `20` | Antal dikteringar i traymenyns historik |
| `history_persist` | `false` | Spara historiken till `history.json` (se Integritet) i stället för bara RAM |
| `polish_prompt` | `""` | Global poleringsuppgift som "Egen text"; används när `polish_template` är tom |
| `polish_template` | `""` | Namn på mallen i `polish_templates` som global standard pekar på (tom = Egen text) |
| `polish_templates` | *(5 mallar)* | Mallbiblioteket (`{namn, prompt}`); hanteras enklast i Inställningar → Polering |
| `flow` | `false` | Flow-läge globalt: polera hela dikteringen automatiskt före inskrivning |

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
- **Historiken hålls i RAM som standard** och försvinner vid omstart.
  Kryssrutan "Spara historik på disk" i Inställningar → Avancerat är ett
  uttryckligt val (av som standard): på skrivs den till
  `%APPDATA%\viska\history.json` vid varje ändring och läses in igen vid
  start; stängs den av raderas filen om den finns.

## Uppdatering

Vid start kollar Viska GitHub-releases efter en nyare version (bara ett
versionsnummer hämtas; ingen data skickas — stäng av under Inställningar →
Avancerat). Finns en nyare visas **Hämta version X** överst i traymenyn.

I den fristående .exe:n gör hämta-raden hela jobbet: efter en bekräftelse
laddas release-zippen ner till `%TEMP%`, packas upp och valideras, varefter
Viska startar om på den nya versionen. Allt före omstarten är rent additivt
(temp-filer) — misslyckas något (nätverksfel, ofullständig nedladdning) rörs
den installerade versionen aldrig, och releases-sidan öppnas i webbläsaren
i stället. Nedladdningen sker i bakgrunden; du kan diktera under tiden.

Är repot privat krävs en **GitHub-token** (personlig access-token med
läsrätt) i Inställningar → Avancerat → *GitHub-token*. Utan giltig token
faller uppdateringen tillbaka på att öppna releases-sidan för manuell
hämtning. I utvecklingsläge (`uv run viska`) öppnas alltid releases-sidan.

## Felsökning

- **Loggen** finns i `%APPDATA%\viska\viska.log`.
- **Uppdateringen startade inte om**: uppdaterarens egen logg finns i
  `%TEMP%\viska-update.log` (robocopy-utfall m.m.). Vid fel öppnas
  releases-sidan så du kan hämta zippen manuellt.
- **"Modellen kunde inte laddas" med GPU**: kontrollera att `uv sync
  --extra gpu` kördes (cuBLAS/cuDNN-paketen krävs för CUDA). Viska faller
  automatiskt tillbaka på CPU.
- **Texten skrivs inte in i admin-fönster**: Windows (UIPI) blockerar
  inmatning från vanliga program till upphöjda fönster. Kör Viska som
  administratör om du behöver diktera där.
- **Ctrl+V fungerar inte i vissa terminaler/TUI-program**: sätt
  `inject_mode` till `"type"`.
- **Tangenten reagerar inte**: något annat program äger genvägen
  (RegisterHotKey) — en traynotis och en rad i loggen talar om vilken
  tangent det gäller. Appen startar ändå; byt tangenten i Inställningar.
- **Fel mikrofon efter dockning/headsetbyte**: traymenyn → "Byt till ny
  standardmikrofon".

## Fristående .exe för andra datorer

För att köra Viska på en dator utan Python/uv (t.ex. jobbdatorn):

```
uv pip install pyinstaller
uv run python build.py          # universalpaket (~100 MB zip)
uv run python build.py --gpu    # buntar även CUDA-biblioteken (~+1,3 GB)
```

Kopiera zippen, packa upp var som helst, kör `Viska.exe` — ingen
installation, inga adminrättigheter.

**Första starten** känner Viska av datorns hårdvara:

- Finns en NVIDIA-GPU erbjuds valet att ladda ner CUDA-biblioteken
  (~1,4 GB, hämtas från PyPI till `%APPDATA%\viska`) för GPU-läge med
  bästa modellen och livetranskribering — eller att köra CPU-läge direkt.
- Har datorn redan komplett CUDA (toolkit + cuDNN) används det utan
  någon nedladdning.
- Utan NVIDIA-GPU körs CPU-läget utan frågor.

Valet går att ändra när som helst: Inställningar → GPU-stöd (ladda
ner/ta bort), eller `Viska.exe --setup-gpu` / `--remove-gpu`.
Whisper-modellen laddas ner första gången appen används (kb-whisper-small
~0,5 GB på CPU, kb-whisper-large ~3 GB på GPU). Autostart:
`Viska.exe --install-autostart`.

`--gpu`-bygget behövs alltså bara för datorer som ska köra GPU-läge helt
offline; annars räcker universalpaketet.

## Verktyg för test

```
uv run viska --list-devices          # lista ljudenheter
uv run viska --transcribe fil.wav    # transkribera en fil (även .ogg/.mp3)
```
