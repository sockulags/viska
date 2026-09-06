# Viska 0.6.0

Den här versionen handlar om väntan: den som fanns utan att göra nytta, och
den som gick att göra begriplig.

## Snabbare

- **Poleringen börjar direkt.** Tonvalsmenyn låg på dubbeltryck, vilket
  tvingade varje enkeltryck att vänta 0,4 sekunder bara för att kunna skilja
  ett tryck från två. Menyn har nu en egen tangent (**Shift+F8**) och
  poleringen startar på 41 ms i stället för drygt 400. Vill du ha kvar
  dubbeltrycket finns det som val under Avancerat — då återkommer
  fördröjningen, och menytangenten stängs av.
- **Mikrofonen släpps när du släpper tangenten.** Tidigare väntade Viska in
  livearbetaren innan inspelningen stoppades, vilket kunde hålla mikrofonen
  öppen i upp till tio sekunder efter att du slutat prata — och allt som
  sades under tiden hamnade i inspelningen. Nu fryses ljudet direkt och
  arbetaren avvecklas i bakgrunden, med de redan inskrivna fraserna
  avräknade som förut.
- **Inställningar öppnas direkt.** Anslutningskollen mot en poleringstjänst
  låg på GUI-tråden med två sekunders timeout, så en avstängd tjänst frös
  fönstret innan det ens syntes. Kollen görs nu i bakgrunden och statusraden
  fylls i när svaret kommer.
- **Flow-lägets förhandsvisning kostar mindre GPU.** Den skrivs aldrig in
  utan ersätts av den riktiga transkriberingen vid stopp, så den kör med
  smalare sökbredd. Slutresultatet har kvar full kvalitet. Livepassen startar
  dessutom aldrig tätare än föregående pass hann bli klart.

## Väntan som går att göra något åt

- **Statuspillen har knappar under poleringen.** "Skriv in råtext" hoppar
  över väntan och skriver in dikteringen som den var, "Avbryt" lägger den i
  historiken utan att skriva in något. Knapparna tar inte fokus från appen du
  skriver i.

## Polering utan GPU blev ett vanligt val

- **Poleringsmotorn väljs i Polering-fliken** med begripliga namn i stället
  för `builtin`/`ollama`/`http` under Avancerat. Varje motor har sina egna
  fält, en anslutningsstatus som uppdateras i bakgrunden, och **Testa
  polering** med en redigerbar mening som visar både resultatet och
  svarstiden.
- **Viska kan starta och stoppa den lokala poleringstjänsten.** Fyll i ett
  startkommando så sköts servern från Inställningar i stället för att dras
  igång för hand vid varje omstart. Den stoppas med hela sitt processträd, så
  den app-server den i sin tur startat blir inte kvar.

## Uppgradering

Bakåtkompatibel. Tonvalsmenyn flyttar från dubbeltryck till Shift+F8; hade du
egna tangentval rörs de inte. Poleringsbackenden behåller sitt värde och
dyker upp på sin nya plats under Polering.
