# ADR-004: Realistiska kostnader för vanlig papertrading

## Status

Accepterad

## Datum

2026-08-11

## Kontext

Vanlig papertrading utanför ett aktivt forward-experiment bokförde
tidigare fills utan avgift, spread eller slippage. Det gjorde
portföljutfallet för optimistiskt och försvårade beviset att agenten
kan skapa nettoavkastning över tid. Projektet ska samtidigt kunna
köras med noll kronor i extern datakostnad och det strikta
OMXSGI-experimentet saknar ännu ett separat auktoriserat indexflöde.

Det publika pre-trade-flödet innehåller förseglad bid/ask-evidens. Den
kan därför bära en reproducerbar paperfill utan att skapa syntetisk
spread eller aktivera ett benchmark med ofullständiga datarättigheter.

## Beslut

1. En vanlig paperfill med förseglad orderbok belastas med faktisk
   halvspread från midpoint till exekverbar sida.
2. Exekveringspriset belastas dessutom med en deterministisk
   slippage på 5 baspunkter. Detta är ett konservativt internt
   antagande, inte leverantörsdata.
3. Courtage följer den publika svenska Mini-nivån: 0,25 procent med
   minst 1 SEK per affär.
4. Modellen lagras som en aktiv, versionsidentifierad policy i
   PostgreSQL. Varje berörd trade binds till exakt policy-ID och
   databasen räknar eller verifierar pris, avgift, spread, slippage
   och nettokassaflöde.
5. Policyn används bara när traden har en verifierad
   `source_book_state_id`. Legacy- och testvägar utan orderbok ändras
   inte och kan inte utge sig för att ha observerad spread.
6. Ett aktivt, förregistrerat benchmark fortsätter använda sin egen
   frysta kostnadsmodell. Standardpolicyn får aldrig skriva över eller
   kringgå benchmarkkontraktet.
7. Befintliga historiska trades skrivs inte om. Modellen gäller
   framåtriktat från schema 48.

## Alternativ som övervägdes

### Fortsatta nollkostnadsfills

Enkelt men systematiskt optimistiskt. Avvisat eftersom målet är
nettoavkastning efter kostnader.

### Starta det strikta OMXSGI-experimentet

Avvisat tills separat auktoriserad OMXSGI-data och nödvändig
provider-evidens finns. Ett benchmark får inte fabriceras för att
låsa upp paperkostnader.

### Fast syntetisk spread i baspunkter

Avvisat för orderboksfills eftersom bid/ask redan innehåller den
faktiska spreaden. Ett extra spreadpåslag skulle dubbelräkna kostnaden.

## Konsekvenser

Nya papertrades får lägre men mer trovärdig nettoavkastning. Små
affärer träffas av minimicourtaget och illikvida instrument av sin
faktiska spread. Resultatet blir jämförbart över tid eftersom samma
policy finns både i beräkningen och som append-only trade-evidens.

Slippageantagandet behöver omprövas när verkliga mäklarfills finns.
En sådan ändring ska skapa en ny policyversion; gamla trades ska
fortsätta peka på den modell som gällde när de skapades.

## Officiell kostnadskälla

- <https://www.nordnet.se/kundservice/prislista>
