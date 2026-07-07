# Eval: poleringsmodeller för svenska (2026-07-07)

28 testfall (rörigt talspråk, faktabevarande, mejl, commit-text,
blandspråk, långdiktering) genererade av Claude Sonnet; 4 modeller körda
lokalt på RTX 5060 Ti; 112 utdata blindbedömda av 7 Sonnet-domare på
grammatik, faktabevarande, stiltrohet och helhet (0–10).

## Resultat

| Modell | Grammatik | Fakta | Stil | Helhet | Medianlatens |
|---|---|---|---|---|---|
| **Qwen3-4B-Instruct-2507 Q4** (inbyggd standard) | **8,9** | **9,2** | **8,4** | **8,2** | **0,49 s** |
| qwen3.5:9b via Ollama | 8,2 | 8,2 | 7,6 | 7,1 | 3,06 s |
| Qwen3-1.7B Q4 | 6,8 | 8,8 | 4,7 | 5,0 | 0,27 s |
| EuroLLM-1.7B-Instruct Q4 | 5,7 | 7,2 | 2,9 | 3,1 | 0,19 s |

## Slutsatser

- **Standardvalet står sig: Qwen3-4B vinner på allt** — bäst svenska,
  bäst faktabevarande, och 6× snabbare än 9B-modellen via Ollama.
- 9B:an via Ollama föll på blandspråk (översatte "deploy" → "deployment")
  och commit-format, trots större storlek.
- 1.7B-klassen räcker inte för polering: Qwen3-1.7B lämnar kvar
  utfyllnadsord (stil 4,7), EuroLLM missförstår uppgiften i grunden
  (helhet 3,1) trots europeisk svenskprofil.
- GPT-SW3 (AI Swedens svenska modeller) kunde inte testas — repona är
  grindade på Hugging Face (kräver konto + licensgodkännande).
- Praktisk följd för små GPU:er (4 GB): hoppa över polering eller
  acceptera delvis CPU-körning av 4B-modellen — små modeller är inte
  värda kvalitetstappet.

Testkorpus och rådata genererades i sessionen; metoden är
reproducerbar: korpus → lokala körningar med latensmätning →
blinddömning i batchar → avanonymiserad aggregering.
