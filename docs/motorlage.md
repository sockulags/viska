# Motorläget: `viska --engine`

Dikteringsmotorn kan köras utan fönster och trayikon, styrd av en annan app
(Referat) över stdin och stdout. Varje rad i båda riktningarna är ett
JSON-objekt i UTF-8. stdout används bara för protokollet. Loggen skrivs till
`%APPDATA%\viska\engine.log` och stderr.

```bash
viska --engine
```

Motorn startar med standardinställningarna. Den laddar ingen modell och tar
ingen tangent förrän värden skickat `configure`. Viskas egen
`config.json` läses inte, och historiken sparas inte på disk; det gör värden.

## Kommandon (värden → motorn)

Varje kommando kan ha ett `id`. Svaret har samma `id`.

| Kommando | Fält | Svar (`ok`) |
| --- | --- | --- |
| `configure` | `config`: inställningar med samma nycklar som Viskas `config.json` | `ignored`: okända nycklar, om några |
| `set_language` | `language`: `sv` eller `en` | |
| `start` | | `started`: om en inspelning startade |
| `stop` | | `stopped`: om något stoppades (utskriften fortsätter med händelser) |
| `cancel` | | `cancelled`: inspelning eller polering avbröts, inget skrivs in |
| `insert_raw` | | `inserted`: under en polering, skriv in råtexten nu |
| `history` | | `items`: senaste dikteringarna, nyast först; `last` |
| `list_mics` | | `mics`: `[{name, bluetooth, default}]`, där `name` kan sparas som `input_device` |
| `quit` | | motorn avslutar efter svaret |

Den första `configure` tar dikteringstangenten och laddar modellen. Senare
`configure` gör bara det som krävs: ny tangent binds om, ny modell laddas om,
mikrofonläget byts direkt.

Fel: `{"event": "error", "cmd": ..., "id": ..., "message": ...}`. En trasig
rad ger ett fel med `cmd: null`, och motorn fortsätter.

## Händelser (motorn → värden)

| Händelse | Fält | Betyder |
| --- | --- | --- |
| `hello` | `version`, `protocol` | Motorn har startat. Protokollversion 1. |
| `bye` | | Motorn avslutar. |
| `status` | `text` | Kort tillståndsrad. |
| `model_loading` | `language` | En modell laddas (första gången laddas den ner). |
| `model_ready` | `device` | `cuda` eller `cpu`. |
| `model_failed` | | Modellen kunde inte laddas. |
| `hotkey_failed` | `hotkey`, `message` | Tangenten var upptagen. `start`/`stop` fungerar ändå. |
| `mic` | `name`, `bluetooth`, `avoided`, `in_call`, `description` | Mikrofonen som används har bytts. `avoided` är headsetet som Windows hade som standard men som inte används. |
| `not_ready` | | Tangenten trycktes innan modellen var laddad. |
| `busy` | | Förra dikteringen skrivs fortfarande ut. |
| `mic_failed` | | Mikrofonen gick inte att öppna. |
| `recording` | `active` | Inspelningen började eller slutade. |
| `connecting` | | Headsetets mikrofon ansluter. Vänta med startsignalen. |
| `listening` | | Inspelningen tar emot ljud. |
| `level` | `value` | Ljudnivå (RMS), ungefär 20 gånger per sekund medan inspelningen pågår. |
| `partial` | `text` | Ofärdig livetext, bara för förhandsvisning. |
| `transcribing` | | Tangenten är släppt och ljudet skrivs ut. |
| `history` | `last`, `app` | En diktering är klar och finns i historiken. `app` är programmets namn ("Microsoft Outlook"), eller `null` om det inte gick att läsa. |
| `nothing_heard` | | Det fanns ljud men inget tal. |
| `idle` | | Klart; inget mer att visa. |
| `failed` | | Utskriften misslyckades. |
| `polishing` | | Flow-polering pågår (bara med en poleringsmotor). |
| `inserting` | | Poleringen är avgjord och texten skrivs in. |
| `flow_done` | `message` | Vad som hamnade i dokumentet efter flow-läget. |
| `notice` | `message` | Kort upplysning, till exempel "Avbruten". |

Händelserna kan komma från olika trådar men skrivs alltid som hela rader.

## Exempel

```
→ {"cmd": "configure", "id": 1, "config": {"hotkey": "f9", "language": "sv"}}
← {"event": "status", "text": "laddar svensk modell…"}
← {"event": "model_loading", "language": "svensk"}
← {"event": "ok", "cmd": "configure", "id": 1}
← {"event": "model_ready", "device": "cuda"}
   (användaren håller F9 och pratar)
← {"event": "mic", "name": "Mikrofonmatris (Realtek(R) Audio)", "bluetooth": false, ...}
← {"event": "recording", "active": true}
← {"event": "listening"}
← {"event": "level", "value": 0.0213}
← {"event": "recording", "active": false}
← {"event": "transcribing"}
← {"event": "history", "last": "Vi ses på torsdag.", "app": "Microsoft Outlook"}
← {"event": "idle"}
```
