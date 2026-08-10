# Forward paper mot OMXSGI

Detta kontrakt är sista mätspärren före en eventuell diskussion om
brokerintegration. Ett godkänt resultat tillåter inte riktiga pengar;
det gör bara experimentet berättigat till manuell granskning.

## Förregistrerade minimikrav

Följande villkor kan göras striktare men inte svagare:

- benchmark: `OMXSGI`, OMX Stockholm All-Share Gross Index;
- startkapital: 20 000 SEK;
- minst 252 officiella XSTO-handelssessioner;
- minst 30 stängda affärer;
- positiv nettoavkastning efter avgift, spread och slippage;
- positiv överavkastning mot OMXSGI;
- maximal drawdown högst 15 procent;
- minst 99,5 procents sammanlagd datatäckning;
- noll kritiska data-, ledger- eller riskincidenter.

Strategiversion och hash, release-SHA, release-manifestets checksumma,
agent-image-digest, exakt modellbackend/modell och dess evidenschecksumma,
referenssnapshot, värderings-, exekverings- och benchmarkprovider,
exekveringspriskälla, kostnadsmodell och godkännandekriterier fryses
redan när utkastet skapas. En ändring kräver en ny registrering;
godkännande får inte skriva om utkastet. Benchmarkprovidern måste vara
separat; en Level 1-produkt får vara både värderings- och
exekveringsprovider om avtalet och valideringen täcker båda rollerna.
Den kanoniska JSON-representationen och dess SHA-256 verifieras även av
databasen. Databasen stoppar retroaktiva ändringar.

## Externa startvillkor

Experimentet kan skapas som `DRAFT`, men kan inte godkännas innan:

1. XSTO-snapshotens checksumma stämmer och omfattar minst 300 instrument;
2. värderings- och exekveringsfeedens senaste validering täcker exakt
   snapshotens instrumentantal och OMXSGI-valideringen exakt ett index;
3. värdering, exekvering och OMXSGI har aktuella
   `VALIDATED`-kontrakt med rätt datatyp för sina roller;
4. senaste providerbevis har full produkt- och symboltäckning, färska
   provdata och uppmätt leveranstid inom avtalet;
5. strategiversionen fortfarande är aktiv och dess hash stämmer;
6. release-, image- och modellbevis avser det som faktiskt ska köras.

Providerbevisen kontrolleras på nytt vid start och återupptagning.
Databasen accepterar endast `DRAFT` vid själva skapandet; ett direkt
SQL-försök att hoppa till `APPROVED` eller `RUNNING` avvisas.
Provideravtalets identitet och det refererade universums snapshot
fryses när de binds. Ett bundet avtals governance-status får endast gå
`DRAFT`→`VALIDATED` och därefter `VALIDATED`→`REVOKED`; återaktivering
eller retroaktiv omskrivning kräver ett nytt avtal och ett nytt
experiment.

Den nuvarande Nasdaq-konfigurationen är `DRAFT`. Skapa därför inte ett
låtsasgodkännande för att komma förbi spärren.

## Operatörsflöde

Skapa en JSON-fil med hela registreringen:

```json
{
  "experiment_key": "xsto-forward-2026",
  "strategy_version": "momentum-report-swing-v1",
  "strategy_config_hash": "64-teckens-sha256",
  "release_sha": "40-teckens-git-sha",
  "release_manifest_sha256": "64-teckens-sha256",
  "agent_image_digest_sha256": "64-teckens-sha256",
  "model_backend": "openai-compatible",
  "model_name": "exakt-modellnamn",
  "model_evidence_sha256": "64-teckens-sha256",
  "reference_snapshot_id": 1,
  "universe_checksum_sha256": "64-teckens-sha256",
  "quote_provider_contract_key": "validerat-quote-kontrakt",
  "execution_price_source": "TOP_OF_BOOK_PLUS_SLIPPAGE",
  "execution_provider_contract_key": "validerat-level1-kontrakt",
  "benchmark_provider_contract_key": "validerat-omxsgi-kontrakt",
  "benchmark_source_url": "https://indexes.nasdaqomx.com/Index/Overview/OMXSGI",
  "benchmark_terms_url": "https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026",
  "fee_bps": 5,
  "spread_bps": 0,
  "slippage_bps": 5,
  "proposed_by": "operator:diffen"
}
```

Kostnaderna ovan är exempel, inte ett rekommenderat antagande. De ska
ersättas med dokumenterade värden innan registreringen godkänns.
`TOP_OF_BOOK_PLUS_SLIPPAGE` kräver `spread_bps: 0`: köpet utgår från
ask, försäljningen från bid, faktisk midpoint-till-sida-spread bokförs
från orderboken och endast den frysta slippagen läggs ovanpå sidan.
Alternativet `LAST_TRADE_PLUS_BPS` kräver att exekveringsprovidern är
samma som värderingsprovidern och använder den äldre syntetiska
halvspread-plus-slippage-modellen.

```bash
docker compose exec agent python -m src.benchmark_admin create \
  --registration-json /app/data/forward-registration.json

docker compose exec agent python -m src.benchmark_admin approve \
  --experiment xsto-forward-2026 \
  --operator operator:diffen

docker compose exec agent python -m src.benchmark_admin start \
  --experiment xsto-forward-2026 \
  --operator operator:diffen \
  --benchmark-level-id 123 \
  --benchmark-start-level 1234.56 \
  --benchmark-event-time 2026-07-29T15:30:00Z \
  --benchmark-available-at 2026-07-29T15:45:00Z \
  --benchmark-checksum-sha256 64-teckens-sha256
```

`--benchmark-level-id` måste peka på exakt den append-only-rad i
`market_index_levels` som kommer från experimentets frysta
OMXSGI-avtal. Nivå, MIC, symbol, händelsetid, tillgänglighetstid och
källchecksumma måste alla stämma; operatörsangivna värden räcker inte.
Startbeviset blir därefter immutabelt.
Vid uppgradering från schema 26 måste varje redan startat experiment
kunna matchas till exakt en sådan indexrad. Annars avbryts hela
migrationen, så att en äldre körning utan verifierbart startbevis inte
felaktigt fortsätter som schema 27.
Statusbyten loggas append-only med operatör och tid:

```bash
docker compose exec agent python -m src.benchmark_admin status \
  --experiment xsto-forward-2026

docker compose exec agent python -m src.benchmark_admin pause \
  --experiment xsto-forward-2026 \
  --operator operator:diffen

docker compose exec agent python -m src.benchmark_admin resume \
  --experiment xsto-forward-2026 \
  --operator operator:diffen
```

Varje daglig observation måste avse en officiell ordinarie XSTO-session
eller halvdag vid exakt stängningstid. Kvällsrutinen skapar den
automatiskt efter portföljsnapshoten med samma injicerade UTC-tid. Den
härleder slut-NAV, sessionens högsta och lägsta NAV, kassa, exponering,
OMXSGI-nivå, faktisk quote-täckning, cutoff-tider och källchecksumma.
Förväntat quote-antal är exakt det frysta universets storlek och data
måste anlända inom respektive leveransavtal.

Avgift, spread och slippage i observationen måste stämma exakt med den
immutabla handelsloggen. Paperfills använder det frysta kostnadskontraktet
för exekveringspris, kassaflöde och netto-FIFO-P&L. Antalet stängda
affärer räknas enbart från `SELL`-poster som faktiskt stänger en
position; det kan inte rapporteras via en observationsfil.
Varje benchmark-affär måste använda exakt den förregistrerade
priskällan. `LAST_TRADE_PLUS_BPS` pekar på en verifierad
`market_quotes`-rad vars arkiverade källfil är bunden till exakt samma
provideravtal och vars instrument ingår i den frysta
referenssnapshoten. `TOP_OF_BOOK_PLUS_SLIPPAGE` pekar på senaste
förseglade, tvåsidiga `pre_trade_book_states` för exakt samma
provideravtal och referenssnapshot som experimentet. Databasen
verifierar aktie, provider, snapshot, pris, händelse- och
mottagningstid, öppen XSTO-session samt återstående visad sidvolym.
Benchmarkaffärens exekverings- och källevidens är append-only och
affären kan inte raderas; senare outcome-, P&L- och
trailing-stopfält får fortfarande uppdateras av sina separata flöden.
Den manuella `benchmark_admin observe`-vägen är borttagen;
observationer skapas av kvällsrutinen och kan varken ändras eller tas
bort.

Kritiska händelser registreras separat i en append-only incidentlogg:

```json
{
  "incident_key": "feed-gap-2026-07-29",
  "session_date": "2026-07-29",
  "severity": "CRITICAL",
  "description": "Quote coverage föll under den frysta gränsen.",
  "detected_at": "2026-07-29T15:45:00Z",
  "source_checksum_sha256": "64-teckens-sha256"
}
```

```bash
docker compose exec agent python -m src.benchmark_admin incident \
  --experiment xsto-forward-2026 \
  --operator operator:diffen \
  --incident-json /app/data/forward-incident.json
```

Slututvärderingen är blockerad tills både minsta sessionsantal och
minsta antal stängda affärer är uppnådda:

```bash
docker compose exec agent python -m src.benchmark_admin evaluate \
  --experiment xsto-forward-2026 \
  --operator operator:diffen
```

Databasen räknar själv nettoavkastning, benchmarkavkastning,
överavkastning, intradags-drawdown, datatäckning, stängda affärer och
kritiska incidenter. Den lagrade utvärderingen är append-only och
stänger experimentet.
