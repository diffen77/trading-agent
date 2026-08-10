# Morgonöverlämning 2026-08-11

## Resultat

Allt säkert internt arbete som kunde göras utan köp, avtal, KYC, riktiga
pengar, merge till `main` eller operatörens finansiella beslut är genomfört på
draft-PR #10.

Det innebär:

- provider-medveten öppningsgrace med regression för den observerade
  09:20:45-leveransen;
- commit-till-image-proveniens som verifieras före databasmigrering;
- fail-closed benchmark-preflight med maskinläsbara blockerare och
  operatörsinput;
- schema 45 med starkare bindning mellan datavalidering och exakt
  referenssnapshot;
- checksummebunden leveranskontroll för all historik som krävs före backtest;
- verifierad separation mellan automatisk kalibrering och manuell
  policyaktivering.

## Beslut och externa åtgärder, i ordning

### 1. Granska PR #10

Beslut: godkänn eller begär ändringar. Merge till `main` är medvetet inte
gjord automatiskt.

### 2. Godkänn första schema-45-releasens transition

Beslut: välj ett underhållsfönster och godkänn en engångsplan med
databassnapshot, verifierad schema-45-deploy och framåtriktad backout.

Skäl: staging kör schema 44 och images utan revisionslabel. Efter migrationen
är automatisk rollback till den gamla koden inte säker. Ingen sådan deploy har
gjorts under natten.

### 3. Skaffa den styrda historikleveransen

Extern åtgärd: välj och licensiera produkter som tillsammans innehåller:

- full XSTO-universumhistorik;
- daglig OHLCV med definierad justeringspolicy;
- corporate actions;
- OMXSGI total-return-serie;
- officiell marknadskalender;
- dokumenterade rättigheter för lagring, intern analys och härledda resultat.

Lägg därefter leveransmanifestet och samplefilerna genom
`python -m src.historical_data_preflight --manifest <manifest>`. Kontrollen kan
godkänna leveransen för adapterkartläggning, men säger avsiktligt inte att
backtest är redo innan den formatspecifika importern finns och har verifierats.

### 4. Välj en ren benchmark-ledger

Beslut: använd en separat tom paper-ledger eller godkänn en kontrollerad
arkivering/återställning av befintlig staging-ledger. Benchmarkkontraktet kräver
exakt 20 000 SEK, inga positioner och inga tidigare benchmarkaffärer.

Rekommendation: separat isolerad benchmarkmiljö. Det bevarar nuvarande
paperhistorik och ger renare evidens.

### 5. Frys benchmarkantagandena

Beslut krävs för:

- courtage/avgifter;
- slippage;
- exekveringsprisregel;
- vald quote-provider och OMXSGI-provider;
- godkännandekriterier och incidentregler.

### 6. Frys modellbeviset

Beslut: välj exakt modellbackend/modellnamn och spara det verifierade bevis som
förregistreringen ska binda. Modellbyte efter start får inte ske tyst.

### 7. Godkänn experimentidentiteten

Beslut: välj experimentnyckel, operatörsidentitet och godkänn den kompletta
förregistreringen först när preflight rapporterar noll blockerare.

### 8. Vänta med broker och riktiga pengar

Ingen brokerkontakt, KYC eller real-money-aktivering behövs nu. Det blir ett
separat beslut först efter minst 252 genomförda XSTO-sessioner, minst 30
stängda affärer och godkänt benchmark utan kritiska incidenter.

## Körordning efter besluten

1. Merge av granskad PR.
2. Bygg och verifiera officiella revisionsmärkta images.
3. Ta snapshot och genomför den godkända schema-45-transitionen.
4. Kör staging-smoke och benchmark `readiness`.
5. Skaffa och verifiera data; bygg och testa formatadaptern mot samplefiler.
6. Skapa ren benchmarkmiljö och frys förregistreringen.
7. Starta forward-benchmarket först när alla maskinella gates är gröna.
