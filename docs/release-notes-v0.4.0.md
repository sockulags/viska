# Viska 0.4.0

## Nytt

- **Poleringsmallar.** Under Inställningar → Polering finns nu ett
  mallbibliotek med fem färdiga mallar (Mejl, Commit-text, Chattsvar,
  Formell text, Kortfattat) — redigera dem, ta bort eller lägg till egna.
  Både den globala standarden och varje app-profil kan peka på en mall i
  stället för att skriva egen fritext; ändrar du en mall slår det igenom
  överallt där den används.
- **Egen Polering-flik** i Inställningar samlar motorstatus, nedladdning,
  global standard, mallbiblioteket, globalt flow-läge och en översikt över
  vilka appar som har egen polering.
- **Tonvalsmenyn (F8 dubbeltryck) visar mallbiblioteket.** I stället för
  hårdkodade toner listas "Appens standard" följt av dina egna mallar, i
  samma ordning som i biblioteket. Blir listan lång går den att skrolla.
- **Inbyggd uppdaterare** i den fristående .exe:n: "Hämta version X" i
  traymenyn laddar nu ner, packar upp, validerar och startar om Viska på
  den nya versionen automatiskt — ingen manuell nedladdning längre.
  Misslyckas något rörs den installerade versionen aldrig och
  releases-sidan öppnas i stället som förr. Stöd för privata repon via en
  ny GitHub-token i Inställningar → Avancerat.

## Förbättrat

- **App-profiler visar riktiga programnamn** (t.ex. "Visual Studio Code"
  i stället för "code.exe") i profillistan och Polering-flikens översikt.
- **"Ny…" i profillistan** öppnar nu en väljare med körande program att
  klicka på, i stället för att bara skriva processnamnet för hand.

Uppgraderingen är bakåtkompatibel: befintlig poleringstext (global och
per app) fortsätter fungera oförändrat som "Egen text".
