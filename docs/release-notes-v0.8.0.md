# Viska 0.8.0

## Codex är en motor i appen nu

Har du Codex installerad och inloggad räcker det att välja **Codex (använder
din inloggning)** under Polering. Ingen adress, ingen server att starta,
ingen nyckel. Viska startar en egen Codex-process i bakgrunden, håller den
varm mellan poleringarna och stänger den när appen avslutas.

Det är svaret för datorer som inte kan köra en poleringsmodell lokalt.
Tidigare krävdes ett separat serverskript, en localhost-adress och ett
startkommando — och skriptet följde inte ens med i den färdiga exe:n.
Nu är det ett val i en lista.

Under motluckan är det samma sak som mätningarna i 0.5 landade i:

- **Ett eget, tomt CODEX_HOME** i `%APPDATA%\viska\codex-home` med bara vår
  config och en kopia av inloggningen. Plugins, MCP-servrar, skills och
  AGENTS.md läses per tråd ur CODEX_HOME, och på en vanlig kodningsuppsättning
  gjorde de en dikterad mening till 20 653 input-tokens. Med ett tomt hem:
  11 559, och rundturen 5,2 → 3,5 sekunder.
- **En ny efemär tråd per omskrivning**, så att den tionde dikteringen inte
  släpar med sig de nio föregående som kontext.
- **Inga verktyg.** Sandlådan är read-only och godkännanden nekas.
- **Din egen Codex-binär.** SDK:n vill helst använda en medföljande kopia på
  354 MB; den bundlas inte, utan Viska pekar på den Codex du redan har.

Tankenivån står på `low` — det är lägsta nivån modellerna faktiskt stöder,
och det som avgör om en omskrivning tar tre sekunder eller tio.

Motorn är aldrig vald som standard: den skickar texten från datorn, och det
står i klartext i ett kort ovanför fälten.

## Enklare därtill

- **Motorlistan** heter numera Inbyggd modell, Codex, Ollama och Egen tjänst.
- **Egen tjänst** är kvar för färdiga endpoints — en företagsgateway eller en
  egen server. Fältet för startkommando och start/stopp-knappen är borta
  tillsammans med serverskriptet de fanns till för.
- **Testknappen** går genom appens egen poleringsväg, så den provar exakt det
  som kommer att köras och återanvänder motorn som redan är varm.
- Statusraden för Codex säger vad som saknas när något saknas: att Codex inte
  är installerad, eller att den inte är inloggad.

## Uppgradering

Inga ändringar som rör befintliga inställningar. Kör du `builtin` eller
`ollama` märks ingenting. Den som satt upp den gamla http-shimmen kan byta
till Codex-motorn och ta bort serverskriptet.
