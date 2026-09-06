# Viska 0.7.0

En omgång om gränssnittet: var saker bor, hur poleringen ser ut medan den
pågår, och vad som händer när man ändrar sig mitt i.

## Inställningarna är omorganiserade

Fem sidor i en sidomeny i stället för fyra flikar, uppdelade efter vad man
kommer hit för att göra:

- **Diktering** – språk, tangent, mikrofonläge, livebeteende och kvalitet.
- **Polering** – motor, anslutning, standardstil, stilbibliotek och testyta.
- **App-profiler** – egen sida. De styr språk, inskrivning, stil och
  automatisk polering, men låg tidigare inklämda under Ordlista.
- **Ord och ersättningar** – ordlista och rättningsregler.
- **Inställningar** – autostart, integritet, modeller och felsökning.

**Arv syns i klartext.** Varje fält i en app-profil står antingen på "Ärv
global (svenska)" med det faktiska värdet i parentes, eller på ett eget val.
Automatisk polering per app är därmed ett trelägesval i stället för en
kryssruta vars av-läge inte gick att skilja från "inte vald".

**Ett enda Spara.** "Spara mall" och "Spara profil" är borta. Det man skriver
i en redigerare följer med när man byter markering eller lämnar fältet, och
fönstrets Spara är det enda som rör config-filen. Har du ändringar som inte
sparats står det i foten, och du får frågan innan fönstret stängs.

## Poleringsvyn

Motorn heter numera något begripligt — inbyggd modell, Ollama eller egen
tjänst — och bara den valda motorns fält visas. Anslutningsstatusen hämtas i
bakgrunden, och när en motor skickar text från datorn står det i ett tydligt
kort ovanför fälten.

**Testytan** kör en mening du själv skriver genom motorn och stilen som är
vald just nu, även om de inte är sparade än. Resultatet stannar i rutan: det
skrivs inte in någonstans och rör inte klippbordet. Svarstiden mäts. Testet
körs bara när du trycker Testa, går att avbryta, och ett svar som kommer efter
avbrottet visas inte.

## Statusrutan

Tillstånden hänger ihop: **Spelar in** med ljudnivå och en klocka som räknar
upp, **Transkriberar**, **Polerar**, och en kort kvittens när dikteringen
hamnat i dokumentet.

Under automatisk polering finns två utvägar i rutan: **Infoga råtext**
(skriver in dikteringen som den var, utan att vänta klart) och **Avbryt**.
Reglerna bakom dem är entydiga:

- Text infogas högst en gång per diktering.
- Ett modellsvar som kommer efter att råtexten infogats, eller efter ett
  avbrott, skrivs inte in.
- Ett avbrott stoppar all inskrivning för den dikteringen. Text som redan
  skrivits in live står kvar — den städas inte bort.
- Har fönstret som dikteringen gällde försvunnit skrivs ingenting in.
  Texten finns kvar i historiken.

Rutan tar fortfarande inte tangentbordsfokus, så knapparna går att klicka
utan att appen du skriver i tappar markören.

## Uppgradering

Inga ändringar i config-formatet. Befintliga profiler, mallar och regler
läses som de är; en profil som ärvde något ärver det fortfarande.
