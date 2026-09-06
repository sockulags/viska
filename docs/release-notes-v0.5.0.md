# Viska 0.5.0

## Nytt

- **Mikrofonen är bara på när du dikterar.** Strömmen öppnas vid
  tangenttrycket och stängs 20 sekunder efter den sista dikteringen, så
  mikrofonikonen i aktivitetsfältet lyser bara när Viska faktiskt lyssnar.
  Vilotiden gör att diktering på diktering fortfarande är igång direkt och
  behåller förstarten som skyddar första stavelsen — det är bara den första
  i varje omgång som betalar de 100–300 ms det tar att öppna strömmen.
  Lägena "Alltid på" (gamla beteendet) och "Stäng direkt" finns kvar under
  Inställningar → Allmänt → Mikrofon.
- **Musknappar som dikteringstangent.** Tumknapparna (Mus 4 och Mus 5) och
  hjulklicket går nu att binda dikteringen till, med samma håll- och
  växlingslogik som tangenterna. Knappen svälts från appen under pekaren, så
  Mus 4 slutar backa i webbläsaren medan den styr Viska. Välj den via
  "Mus…"-knappen bredvid tangentväljaren. Vänster och höger musknapp går
  medvetet inte att välja.
- **Ersättningsregler.** Ord som Whisper hör fel skrivs nu alltid rätt.
  Till skillnad från ordlistan, som bara är ett tips till modellen och ofta
  ignoreras, körs en regel efter transkriberingen och slår alltid igenom.
- **"Rätta senaste dikteringen…"** i traymenyn visar den senaste texten i en
  ruta. Rätta den som vanligt, så räknas skillnaden ut och föreslås som
  regler du kan ta bort innan du sparar. Reglerna går också att redigera för
  hand under Inställningar → Ordlista.
- **Molnpolering för datorer utan GPU.** Poleringsbackenden `http` talar
  OpenAI-formatets `/v1/chat/completions` mot en adress i inställningarna och
  vet ingenting om vad som svarar. `tools/polish_server.py` är ett färdigt
  alternativ som håller en Codex-process varm och kör varje omskrivning i en
  egen efemär tråd, utan verktyg och utan din vanliga Codex-uppsättning.
  Backenden är avstängd som standard — den är det enda i Viska som skickar
  text från datorn.

## Förbättrat

- **Tidtagning.** Varje diktering mäts nu (transkribering, polering,
  inskrivning och den totala väntan från stopp till text). Medianen visas
  under Inställningar → Avancerat → Prestanda och varje diktering loggas.
- **Configfilen skrivs atomiskt.** Ett strömavbrott mitt i en sparning kan
  inte längre lämna en halv fil efter sig, och en fil som inte går att läsa
  flyttas till `config.json.bak` i stället för att tyst skrivas över.
  Nycklar som byter betydelse migreras via `config_version`.
- **Tester.** 73 tester över den rena logiken, utan krav på GPU eller
  mikrofon: `uv run --group dev pytest`.

## Uppgradering

Bakåtkompatibel. `keep_mic_open` migreras automatiskt till det nya
`mic_mode`: hade du kvar standardvärdet får du det nya "Vid diktering",
medan ett uttryckligt `false` blir "Stäng direkt". Ingen annan inställning
rörs, och en borttagen rad i `voice_commands` förblir borttagen.
