# Viska — plan framåt

Skriven 2026-10-19 efter en genomgång av Viska 0.8.0 och Referat 0.11.0.

## Status

Bockas av allteftersom arbetet landar på `main`. Numren pekar på punkterna i faserna nedan.

**Fas 0 (Viska)**
- [x] 0.1 Svansen efter tangentsläpp
- [x] 0.2 Väntan vid inklistring — nästa inklistring och återställningen väntar bara tills
  målprogrammet har läst. Gäller inte när en klippbordsövervakare hinner läsa först; på
  din dator gör fjärrskrivbordsklienten (msrdc.exe) det, och då gäller 0,8 s som förut.
  Den läser dessutom dikterad text trots privat-märkningen, så diktat följer med till en
  ansluten fjärrsession.
- [x] 0.3a Bluetooth: slå upp den faktiska mikrofonen och välj datorns i stället för ett headset
  — valet görs vid varje öppning och öppnar via WASAPI. Inte verifierat med ett anslutet
  headset. Ett headset via USB-dongel (din Jabra Link 390) syns som USB för Windows och
  kan inte kännas igen här.
- [ ] 0.3b Kortare stängning av mikrofonen och WASAPI
- [ ] 0.3c Headsetet används fritt när det redan är i ett samtal
- [ ] 0.4 Grafikminne vid modellbyte
- [ ] 0.5 Klippbordet återställer mer än text
- [ ] 0.7 Tester för mikrofon, liveläge, klippbord och tangentstyrning
- [ ] 0.6 Mät utfyllnadsord — *väntar på dig: riktiga dikteringar*
- [ ] Mät Bluetooth-bytet på ditt headset — *väntar på dig*

**Fas 1 (Viska)**
- [ ] 1.1 Dela `app.py` i session, motor och gränssnitt (Tk fortsätter fungera ovanpå)
- [ ] 1.2 `viska --engine` med JSON över stdin/stdout

**Fas 2 (Referat)**
- [ ] 2.1 Motorn som komponent
- [ ] 2.2 Statusrutan
- [ ] 2.3 Inställningar → Diktering
- [ ] 2.4 Historik
- [ ] 2.5 Introduktion
- [ ] 2.6 Ordlistan till motorn
- [ ] 2.7 Polering som tillval
- [ ] 2.8 Flytt från Viska
- [ ] 2.9 Sista fristående Viska-versionen

**Fas 3 (Referat)**
- [ ] 3.1 En motor för möten och diktering
- [ ] 3.2 KB-Whisper large för möten
- [ ] 3.3 Motorn spelar in mötesmikrofonen

## Riktning

Viska blir dikteringen i Referat. Det tunga gränssnittet (inställningar, historik,
introduktion) flyttar till Referats Electron-app och designsystem. Viskas Python-kärna
(kortkommando, mikrofon, Whisper, inklistring, fokus) lever kvar som en hjälpprocess som
Referat laddar ner och startar på samma sätt som transkriberingskomponenten.

Varför inte bara bygga om Viska för sig:

- Referat har redan det Viska saknar: ett designsystem, ljust och mörkt tema,
  inställningar med krypterade nycklar, installer, uppdaterare, ikon i aktivitetsfältet,
  ordlista och leverantörer för AI.
- Viska har redan det Referat saknar: tryck-och-håll på en global tangent, musknappar,
  inklistring i andra program med skydd för klippbordshistoriken och val av program i
  förgrunden. Electron kan inget av det utan egen kod mot Windows, och Referats
  arkitektur förbjuder native Node-moduler. En separat hjälpprocess följer den regeln.
- Den tidskritiska vägen (tangent, mikrofon, Whisper, inklistring) stannar i en och
  samma process. Electron visar bara status, så latensen blir inte sämre.
- Samma målgrupp tjänar på båda: den som skriver protokoll skriver också mejl.
  En ordlista som rättas i ett möte gäller direkt vid diktering.

Beslutat 2026-10-19: Viska blir en del av Referat. Referats produktbild vidgas från
"mötesanteckningar" till "tal till text som stannar hos dig".

## Ordlistan i stället för polering

Det mesta som poleringen rättar i dag är felhörda termer, och det gör Referats ordlista
bättre: den ändrar bara det användaren själv har lagt in, och samma ordlista gäller både
möten och diktering. Viskas ersättningsregler ("hört" → "skrivet") är exakt samma sak som
ordlistans varianter och korrekta stavning.

- **Förvalt blir ingen AI alls:** KB-Whisper, sedan ordlistan. Det tar bort den lokala
  Qwen-modellen (2,4 GB och delat grafikminne), Codex-motorn och risken att dikterad text
  tolkas som en instruktion i det vanliga flödet.
- **Ordlistan används två gånger:** de korrekta stavningarna skickas till Whisper som
  ledtråd (`initial_prompt`/`hotwords`), så att termen oftare blir rätt från början, och
  varianterna byts ut efteråt. Referats transkriberingsserver skickar i dag inte med
  ordlistan till Whisper; det bör den göra för möten också.
- **Utfyllnadsord:** KB-Whisper finns i tre stilar: `subtitle` (mest förtätad),
  `standard` (förval) och `strict` (närmast ordagrann). Modellkortet säger inte hur de
  hanterar "eh" och "öh". Mät på riktiga dikteringar. Blir något kvar räcker en fast lista
  med ord som tas bort, utan språkmodell.
- **Polering blir ett tillval** för det ordlistan inte kan göra: skriva om till mejl,
  korta ned, commit-meddelanden. Den använder samma AI-leverantör som sammanfattningarna,
  och skyddet mot att texten tolkas som en instruktion behövs fortfarande där.

## Statusrutan i Referat

- **Motorn äger dikteringens tillstånd** och skickar händelser. Referats huvudprocess
  skickar dem vidare till ett eget litet fönster för statusrutan. Inspelningen och
  inklistringen fungerar även om fönstret skulle krascha.
- **Fönstret skapas dolt när Referat startar,** så att det visas direkt. Det är ramlöst,
  genomskinligt, syns inte i aktivitetsfältet, ligger överst, kan aldrig få fokus och
  visas med `showInactive()`. Musklick går igenom det, utom när det har knappar
  (polerar, klart, problem).
- **Placering:** motorn vet vilket fönster texten ska till. Huvudprocessen lägger rutan
  längst ned i mitten av den skärmens arbetsyta, så den hamnar ovanför aktivitetsfältet
  på rätt skärm.
- **Ett eget renderer-ingångsfönster** (`overlay.html`) i electron-vite, med samma
  färger och typsnitt som resten av Referat.
- **Ljuden stannar i motorn,** eftersom startljudet ska spelas exakt när mikrofonen
  faktiskt tar emot ljud.
- **En ikon i aktivitetsfältet:** Viskas egen försvinner. Referats ikon blir korallröd
  medan du dikterar, och menyn får Diktering på/av, språk och de senaste diktaten.
- **Referat måste vara igång** för att dikteringen ska fungera. Att slå på diktering slår
  därför också på "kör i bakgrunden" och föreslår start med Windows.

## Inställningarna i Referat

- **Referat äger allt:** ett nytt `dictation`-block i `settings.json` och den befintliga
  `glossary.json`. Motorn har ingen egen inställningsfil. Den får hela blocket och
  ordlistans globala termer när den startar och varje gång något sparas.
- **Mikrofonlistan för diktering kommer från motorn.** Referats mikrofonval för möten
  använder webbläsarens enhets-id, som inte går att översätta till Windows enhets-id.
  Motorn listar enheterna, markerar Bluetooth och skickar listan till inställningarna.
  Möten och diktering kan därför ha olika mikrofon tills fas 3.
- **Var det syns:** Inställningar → Diktering (se skissen), Diktat i huvudmenyn för
  historiken, och ett frivilligt sista kort i introduktionen: "Vill du också kunna
  diktera?". Den som bara spelar in möten ska inte mötas av diktering.
- **Regler per program** (Viskas app-profiler) blir en lista under Diktering: program,
  språk, sätt att infoga och eventuell polering.
- **Dela upp `Settings.tsx`** (1 074 rader) när Diktering läggs till; den nya sektionen
  får en egen fil.
- **Flytt från Viska:** vid första start läses `%APPDATA%\viska\config.json`.
  Kortkommando och språk följer med, ersättningsreglerna blir ordlistetermer, orden i
  Whisper-ledtråden blir termer utan varianter och app-profilerna blir programregler.

## Fas 0 — rätta det som är fel nu (dagar, i Viska)

Bara det som följer med in i motorn. Rättningar av Codex-motorn, kontrollen av "Egen
tjänst" och den inbyggda Qwen-modellen görs inte, eftersom de delarna försvinner.

1. **Slutet klipps.** Spela in 100–150 ms efter att tangenten släppts innan inspelningen
   tas ([audio.py:104](../viska/audio.py)).
2. **Onödig väntan vid inklistring.** De 0,8 s mellan inklistringar ska bara gälla när en
   tidigare inklistring fortfarande pågår ([inject.py:271](../viska/inject.py)).
3. **Mikrofonen och Bluetooth**, se eget avsnitt nedan.
4. **Grafikminne:** släpp den gamla Whisper-modellen innan en ny laddas
   ([app.py:470](../viska/app.py)).
5. **Klippbordet:** återställ även bilder, filer och formaterad text, inte bara ren text
   ([inject.py:140](../viska/inject.py)).
6. **Mät utfyllnadsorden:** kör ett tjugotal riktiga dikteringar genom KB-Whisper
   `standard` och `subtitle` och räkna vad som blir kvar.
7. **Tester** för det som saknar dem: mikrofonen, livetranskriberingen, klippbordet och
   hur tangenten styr inspelningen. Med falska ljudströmmar och ett falskt klippbord
   behövs varken mikrofon eller grafikkort.

## Mikrofonen och Bluetooth-headset

Ett vanligt Bluetooth-headset byter till samtalsläge (mono, sämre ljud) så fort något
program öppnar headsetets mikrofon. Det byter tillbaka när mikrofonen stängs. Att öppna
en annan mikrofon påverkar inte headsetet. Bytet tar enligt Microsoft upp till flera
sekunder, vilket både stör musiken och klipper början av dikteringen. Viska håller i dag
mikrofonen öppen 20 s efter varje diktering, så headsetet står i samtalsläge nästan hela
tiden för den som dikterar ofta.

Det finns ingen programvarulösning som låter ett vanligt Bluetooth-headset spela stereo
och spela in samtidigt. Det enda som undviker bytet är att inte använda headsetets
mikrofon. Undantaget är LE Audio på Windows 11 24H2 eller senare, där headset, radio och
drivrutin alla stöder det.

Därför:

1. **Välj aldrig "standardmikrofon" blint.** Slå upp den faktiska enheten. Är den ett
   Bluetooth-headset (enhetens instans-id börjar med `BTHHFENUM\`), använd den inbyggda
   mikrofonen i stället, därefter USB. Visa vilken mikrofon som används.
2. **Fråga en gång när ett Bluetooth-headset dyker upp:** "Ditt headset byter till
   samtalsläge när dess mikrofon används. Ska Viska använda datorns mikrofon i stället?"
   med valen *Datorns mikrofon (rekommenderas)* och *Headsetets mikrofon*.
3. **Väljer användaren headsetets mikrofon:** öppna den bara medan tangenten hålls, ingen
   efterhållning. Visa "Ansluter mikrofon…" och spela startljudet först när verkligt ljud
   kommer, så att början inte klipps.
4. **Är headsetet redan i ett samtal** (ett annat program har en aktiv session på samma
   mikrofon) kostar det inget att använda det. Då kan headsetets mikrofon användas utan
   att fråga.
5. **För trådbundna och inbyggda mikrofoner:** stäng efter 1–2 s i stället för 20 s.
   Öppna via WASAPI uttryckligen, som är snabbare än MME.
6. Spela aldrig upp ljud märkt som kommunikation (till exempel startljuden), eftersom
   det också tvingar fram samtalsläget.
7. Lyssna på enhetsändringar, så att kontrollen görs när headsetet ansluts och inte bara
   vid start.

Att testa på ditt headset innan vi bygger: hur lång tid bytet faktiskt tar, och att det
inte räcker att lista enheterna för att trigga det.

## Fas 1 — gör kärnan fristående (1–2 veckor, i Viska)

1. Dela [app.py](../viska/app.py) (1 850 rader) i en dikteringssession, en motor
   (modeller, polering) och ett gränssnittslager.
2. Lägg till `viska --engine`: ingen Tk, ingen ikon i aktivitetsfältet, styrning och
   händelser som JSON över stdin/stdout. Händelser: `recording`, `level`, `partial`,
   `transcribing`, `polishing`, `inserted`, `error`, `mic_changed`. Kommandon:
   `configure`, `set_language`, `cancel`, `insert_raw`, `history`.
3. Det nuvarande Tk-gränssnittet blir en klient till motorn och fungerar under hela
   övergången.

## Fas 2 — diktering i Referat (2–3 veckor för en första version)

1. **Komponent:** motorn byggs med PyInstaller och laddas ner, kontrolleras och startas
   av Referat som transkriberingskomponenten ([localAiComponents.ts](../../referat/src/main/localAiComponents.ts)).
   Motorn byggs utan llama.cpp och Codex.
2. **Statusrutan** enligt avsnittet ovan.
3. **Inställningar → Diktering** enligt avsnittet ovan.
4. **Historik:** de senaste dikteringarna med det som sades och det som infogades sida
   vid sida, kopiera, rätta. En rättning blir en term i ordlistan.
5. **Introduktion:** ett frivilligt steg "Prova att diktera" med mikrofontest.
6. **Ordlistan** skickas till motorn, både som ledtråd till Whisper och som ersättningar.
7. **Polering som tillval** via sammanfattningens AI-leverantör, med skydd mot att texten
   tolkas som en instruktion och en tidsgräns.
8. **Flytt från Viska** av inställningar och ersättningsregler.
9. **Det fristående Viska** får en sista version som pekar till Referat.

## Fas 3 — en motor för både möten och diktering

1. Slå ihop motorn och Referats transkriberingsserver till en process med en varm modell.
   Diktering går före mötestranskribering; i dag kan en diktering hamna bakom tio
   minuters mötesljud.
2. Låt användare med grafikkort välja KB-Whisper large även för möten (servern kör i dag
   alltid small).
3. Låt motorn spela in mikrofonen även för möten, så att möten och diktering delar
   mikrofonval och Bluetooth-hanteringen.

## Modeller

- **Tal till text:** behåll KB-Whisper. Inget öppet alternativ är bättre på svenska.
  NVIDIAs Parakeet och Nemotron gör ungefär 20 % fel på svenska, och Mistrals Voxtral
  Realtime stöder inte svenska.
- **Polering (tillval):** går via sammanfattningens leverantör. Lokalt via Ollama passar
  Qwen3.5-9B med tänkandet avstängt eller Ministral-3-8B; Qwen3.5-4B är sämre på svenska
  än dagens Qwen3-4B. I molnet ger Claude Haiku 5.5 utan tänkande eller gpt-oss via Groq
  svar på under en sekund, mot Codex 3,5 s.
- **Utvärdering av polering:** gör om den med riktig KB-Whisper-utdata, mänsklig
  bedömning, testfall med frågor och instruktioner i dikteringen, och spara rådata.

## Beslut som behövs

1. Vilken KB-Whisper-stil ska vara förval för diktering: `standard` eller `subtitle`?
   Avgörs av mätningen i fas 0.
2. Hur länge ska diktat sparas i historiken, och ska det gå att stänga av helt?
