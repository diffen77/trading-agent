# Morgonöverlämning 2026-08-11

## Resultat

Allt säkert internt arbete som kunde göras utan köp, avtal, KYC, riktiga
pengar eller operatörens finansiella beslut är mergat till `main`. CI och den
immutabla releasebyggnaden är gröna. Staging kör schema 48 från den verifierade
releasen `7bbf9f6971c14e0e317df1ea170a21345d58f805`.

Det innebär:

- provider-medveten öppningsgrace med regression för den observerade
  09:20:45-leveransen;
- commit-till-image-proveniens som verifieras före databasmigrering;
- fail-closed benchmark-preflight med maskinläsbara blockerare och
  operatörsinput;
- schema 45 med starkare bindning mellan datavalidering och exakt
  referenssnapshot;
- checksummebunden leveranskontroll för all historik som krävs före backtest;
- automatisk aktivering av framåttestade kandidatpolicyer i paper trading,
  med automatisk återställning efter verifierad försämring;
- 99,04 procents verifierad sektortäckning för det aktiva XSTO-universumet;
- realistiska standardkostnader för nya paper-affärer;
- plattformsägd automatisk stagingdeploy med isolerad dispatchnyckel i
  GitHub och Bitwarden.

## Beslut och externa åtgärder, i ordning

### 1. GitHub- och releasekedjan — klart

PR #20, PR #21 och PR #23 mergades till `main`. Den deployade staging-releasen
är `7bbf9f6971c14e0e317df1ea170a21345d58f805`; agent- och dashboard-images
samt release-manifestet byggdes från exakt denna revision. Plattformens PR #27
mergades och deploykörning `31538805321` passerade.

### 2. Schema-48-transition och plattformsdeploy — klart

Den befintliga ledgern och de namngivna volymerna bevarades. Schema 45
migrerades framåt till schema 48, samtliga långlivade tjänster startades från
digest-låsta images och OCI-revisionen verifierades mot GitHub-releasen.

Efterkontrollen visar intern readiness `READY`, elva friska tjänster, inga
öppna driftlarm och korrekt tradingblockering när XSTO-sessionen är stängd.

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

### 5. Frys återstående benchmarkantaganden

Tekniska standardvärden för courtage, slippage och exekveringsprisregel finns
nu i schema 48. Kvar att besluta när benchmarkstarten närmar sig är vald
OMXSGI-källa, slutliga godkännandekriterier och incidentregler.

### 6. Frys modellbeviset inför benchmark

Staging kör Hermes med `gpt-5.6-sol` och endpointen är verifierat nåbar. Vid
benchmarkstart ska det exakta modellbeviset bindas i förregistreringen;
modellbyte efter start får inte ske tyst.

### 7. Godkänn experimentidentiteten

Beslut: välj experimentnyckel, operatörsidentitet och godkänn den kompletta
förregistreringen först när preflight rapporterar noll blockerare.

### 8. Vänta med broker och riktiga pengar

Ingen brokerkontakt, KYC eller real-money-aktivering behövs nu. Det blir ett
separat beslut först efter minst 252 genomförda XSTO-sessioner, minst 30
stängda affärer och godkänt benchmark utan kritiska incidenter.

## Körordning efter besluten

1. Skaffa och verifiera data; bygg och testa formatadaptern mot samplefiler.
2. Skapa ren benchmarkmiljö och frys förregistreringen.
3. Starta forward-benchmarket först när alla maskinella gates är gröna.
