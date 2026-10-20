# Viska 0.9.0

## Dikteringen finns nu i Referat

Viska utvecklas vidare som dikteringen i [Referat](https://github.com/sockulags/referat).
Referat kör samma dikteringsmotor, har en inställningssida, en historik och en ordlista
som gäller både diktering och möten, och spelar dessutom in och sammanfattar möten.
Första gången Referat startar flyttas dina Viska-inställningar och ersättningsregler dit.

Viska fungerar som förut men får inga nya funktioner. Appen säger det en gång vid start,
och menyn i aktivitetsfältet har raden *Dikteringen finns nu i Referat…*.

## Bluetooth-headset

- **Headsetets mikrofon används inte i onödan.** Är Windows standardmikrofon ett
  Bluetooth-headset lyssnar Viska med datorns egen mikrofon, så att headsetet inte byter
  till samtalsläge och musiken fortsätter i full kvalitet. Går att stänga av under
  Diktering.
- **Under ett samtal** i Teams, Zoom eller webbläsaren används headsetet ändå, eftersom
  det redan står i samtalsläge.
- **Väljer du headsetet** stängs dess mikrofon direkt efter dikteringen, och startsignalen
  väntar tills headsetet faktiskt levererar ljud.

## Mikrofonen och inklistringen

- Mikrofonen stängs 2 sekunder efter en diktering i stället för 20.
- Slutet av en mening klipps inte längre av när tangenten släpps.
- Mikrofonen öppnas via WASAPI.
- Klippbordet återställs med bilder, filer och formaterad text, inte bara text.
- Nästa inklistring väntar bara tills programmet har hämtat den förra, när det går att se.
- Grafikkortet behöver inte längre hålla två Whisper-modeller samtidigt vid modellbyte.

## För utvecklare

- `viska --engine` kör dikteringsmotorn utan fönster, styrd med JSON över stdin/stdout.
  Protokollet finns i [motorlage.md](motorlage.md).
- `build.py --engine` bygger motorn som eget program, `viska-engine.exe`.
- Dikteringen ligger i `engine.py` och poleringsmotorerna i `polishing.py`; `app.py` är
  gränssnittet.
