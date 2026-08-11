# Point-in-time walk-forward-backtest

## Syfte och avgränsning

Motorn testar den deterministiska kandidat-, risk- och exitpolicyn. Den
låtsas inte kunna återskapa historiska LLM-bedömningar. Den delen ska
mätas som faktisk forward papertrading med sparad modell-, prompt-,
strategi- och dataversion.

Ett backtestresultat får bara status `SUCCEEDED` när det bygger på ett
operatörsvaliderat, checksummefryst dataset. Studentagenten får inte
starta eller validera backtests.

## Obligatoriskt datasetkontrakt

Ett dataset måste ha:

- historiskt XSTO-universum med `valid_from`/`valid_to`, även avnoterade
  och inaktiva instrument;
- sektorhistorik för de perioder där sektorgränser testas;
- råa, ojusterade dagliga OHLCV-bars med `event_time`, `available_at`
  och `received_at`;
- fullständig split-, kontantutdelnings- och avnoteringshistorik;
- ett total-return-benchmark;
- ett separat riskindex om benchmark inte är samma index som
  strategins risk-off-regel;
- officiella XSTO-sessioner för hela perioden;
- en datacutoff och en kanonisk SHA-256-checksumma.

Valideringen stoppar dataset där en aktiv historisk medlem saknar bar,
en bar inte var tillgänglig före nästa öppning, medlemskapet publicerades
för sent, benchmark/riskindex saknas eller någon obligatorisk
fullständighetsdeklaration är falsk.

## Exekveringsmodell

- Signal beräknas efter en sessions stängning.
- En order kan tidigast exekveras till nästa sessions öppning.
- Pris över SMA beräknas endast från bars som var tillgängliga då.
- Historiska medlemsperioder används; dagens bolagslista återanvänds
  aldrig bakåt i tiden.
- Pre-split-priser räknas om till aktuell aktiebas när signaler beräknas.
- Öppna positioner justeras för split och kontantutdelning på ex-dagen.
- Avgift, halva spreaden, slippage, minsta dagsomsättning och maximal
  volymandel är obligatoriska körparametrar.
- Om stop-loss och target båda träffas i samma dagsbar väljs stop-loss.
- Trailing stop, tidsstopp, universumutträde, avnotering och
  periodslut loggas med separat exitorsak.
- Universumutträde exekveras först vid den första session där
  instrumentet inte längre ingår, till den sessionens öppning; nästa
  sessions medlemskap används aldrig för en försäljning vid dagens
  stängning.
- En avnoterad aktie kan inte återöppnas av en väntande order samma dag.
- Portföljavkastning kapitalviktas och compunderas; enskilda
  tradeprocent summeras aldrig.

## Walk-forward och resultat

Träningsperioden slutar före testperioden och out-of-sample-fönster får
inte överlappa. Varje fold sparar datum, metrics, trades och daglig
equity/benchmark-kurva. Hela körningen lagrar:

- nettoavkastning;
- benchmark- och excess return;
- max drawdown;
- Sharpe;
- turnover;
- antal trades och win rate;
- engine-, strategi-, dataset- och input-checksumma.

Samma dataset, strategi och körkonfiguration får samma `run_key`.
Omstart skapar därför inte dubbla resultat.
Drawdown börjar vid foldens startkapital och fortsätter över
walk-forward-gränser, så en förlust första dagen eller en fortsatt
nedgång efter föregående folds topp inte nollställs.

## Operatörsflöde

När leverantören har lämnat råfilerna kopieras
`historical-delivery-manifest.example.json` till leveransmappen och
fylls med exakt produkt, avtalsnyckel, rättighetsintyg, filnamn och
SHA-256. Kontrollera leveransen innan en formatspecifik adapter skrivs:

```bash
docker compose exec agent python -m src.historical_data_preflight \
  --manifest /app/data/history-delivery/delivery.json
```

Preflighten är read-only och kräver separata, checksummebundna
artefakter för villkor, point-in-time-universum, rå OHLCV, corporate
actions, OMXSGI total return och XSTO-kalender. Ett godkänt svar betyder
bara `ready_for_adapter_mapping`; det betyder uttryckligen inte att
datasetet är importerat, validerat eller redo för backtest. Exakt
ISO/XML/TIP/API-mappning implementeras först när vald produkt och ett
verkligt provuttag finns.

När en historikimport har skapat ett `DRAFT`-dataset:

```bash
docker compose exec agent python -m src.backtest_runner checksum 42

docker compose exec agent python -m src.backtest_runner validate 42 \
  --validated-by 'operator:diffen'
```

Körningen kräver explicita, dokumenterade kostnadsantaganden:

```bash
docker compose exec agent python -m src.backtest_runner run 42 \
  --strategy-version momentum-report-swing-v1 \
  --fee-bps 5 \
  --spread-bps 10 \
  --slippage-bps 10 \
  --max-volume-participation 0.01 \
  --min-daily-turnover 1000000
```

Värdena ovan är endast ett format-exempel, inte godkända antaganden.
Kostnaderna ska bestämmas från vald dataleverantör, tänkt broker och
instrumentens faktiska likviditet.

## Nuvarande blockerare

Motorn och dess syntetiska/integrerade tester är verifierade. Det finns
ännu inget licensierat fullhistoriskt XSTO-dataset i projektet som
uppfyller kontraktet. Inget verkligt avkastningsresultat eller påstående
om att strategin slår benchmark finns därför ännu.
