# Drift och releaser

## Säkerhetsgräns

En frisk deployment betyder inte att agenten får handla.

- `readiness` kräver schema 45 eller nyare, exakt en balansrad, att portföljantal
  stämmer med öppna FIFO-lots, en hashverifierad aktiv strategi och
  aktuell officiell XSTO-kalender.
- `trading-readiness` kräver dessutom minst 300 aktiva XSTO-instrument,
  aktiv operatörskontroll utan utlöst daglig förlustspärr,
  ett aktuellt godkänt provider-kontrakt, provider-validering,
  öppen XSTO-session och åtkomst till exakt konfigurerad AI-modell.
  För den publika pre-trade-produkten krävs minst en färsk, tvåsidig,
  exekverbar XSTO CLOB-bok för ett instrument; alla 416 instrument
  behöver inte uppdateras varje minut. Officiella tickeralias och
  OMXSGI blockerar inte intern ISIN-baserad papertrading på denna väg.
  Avtalsbaserade feeds behåller sina striktare fullcoverage-,
  entitlement- och indexkrav.

`trading-readiness` ska vara `NOT_READY` utanför öppen marknad och innan
den första 15–20 minuter fördröjda filen har validerats. Det är ett
korrekt säkerhetsläge, inte ett driftfel.

Kontrollerna ger en JSON-rad med stabilt eventnamn och exitkod:

```bash
python -m src.healthcheck readiness
python -m src.healthcheck trading-readiness
```

Inga lösenord, tokens, URL-credentials eller råa exceptions skrivs i
health-resultatet.

## Driftmonitor och larm

En separat `monitor`-tjänst kör samma fail-closed-evidens var 60:e
sekund, fasjusterad till sekund 30 som standard. Den fasta
väggklockefasen hindrar att kontrollen driver in framför pågående
minutimport. Schemalagda rutinresultat och larmövergångar lagras
append-only i `scheduled_routine_events` respektive
`operational_alert_events`. Samma larm öppnas därför bara en gång och
får en separat `RESOLVED`-övergång när symptomet försvinner.
Återkommande analys- och studietidsrutor leasas i
`scheduled_job_runs`. En utgången lease får återtas efter omstart,
medan redan avslutade tidsrutor inte kan köras dubbelt.

För fördröjda pre-trade-orderböcker används det validerade
leverantörskontraktets `max_transport_lag_seconds` som lokal
färskhetsgräns. Därmed skapar normal nedladdnings- och
transaktionstid inte ett falskt `NOT_READY`-fönster.

Monitorprocessen larmar på:

- avvisad eller otillgänglig schema 45-migrering;
- ledgerfel, inklusive avvikelse mellan portfölj och öppna FIFO-lots;
- stale eller ofullständig XSTO-data först 25 minuter efter öppning
  (högst 15 minuters feedfördröjning plus 10 minuters larmfönster);
- en utgången återstartslease, att ingen intradagsanalys avslutats inom
  35 minuter under öppen XSTO-session eller att ingen studietidsruta
  avslutats inom två timmar utanför sessionen;
- schemarutiner som saknar lyckad körning efter sin 20-minuters
  grace-period;
- kritiska incidenter i aktivt eller pausat forward-experiment;
- fel i monitorns egen evidensinsamling eller persistens.

Kör en manuell, maskinläsbar kontroll:

```bash
docker compose exec monitor python -m src.operational_monitor once
```

Exitkod `0` betyder `HEALTHY`; exitkod `1` betyder `ALERTING`.
Telegram används endast om både `TELEGRAM_BOT_TOKEN` och
`TELEGRAM_CHAT_ID` finns i runtime-konfigurationen. Saknade
notifieringsnycklar påverkar inte det beständiga larmtillståndet.
Råa exceptions skickas aldrig i larmtexten.

Aktiva larm kan granskas direkt utan att mutera historiken:

```sql
SELECT DISTINCT ON (alert_key)
    alert_key, code, severity, state, summary, observed_at
FROM operational_alert_events
ORDER BY alert_key, observed_at DESC, id DESC;
```

Dashboardens `/api/operations` gör samma latest-state-urval och visar
endast aktiva `OPEN`-larm. Ett `PAGE`-larm läggs även till i listan över
handelsblockerare; ett `TICKET` visas för operatören men blockerar inte
ensamt. Vyn visar dessutom de tio senaste schemarutinerna, med endast
senaste append-only-utfallet per `routine_key`.

Första åtgärd:

- `SCHEMA_OR_MIGRATION_NOT_READY`: stoppa releaseförsök och kontrollera
  migratortjänstens exitstatus. Vid 26→27 måste varje redan startat
  forward-experiment ha en unik exakt `market_index_levels`-rad; saknad
  träff kräver manuell evidensgranskning och får inte kringgås;
- `LEDGER_INVARIANT_FAILED`: behåll nödstoppet aktivt och jämför
  `portfolio` med öppna `position_lots`;
- `MARKET_DATA_NOT_READY`: behåll handelsblockeringen och kontrollera
  provideravtal, senaste synk, gaps och quote-coverage;
- `SCHEDULED_ROUTINE_MISSED`: kontrollera senaste
  `scheduled_routine_events` och kör inte en orderproducerande rutin
  manuellt utan att först verifiera idempotensnyckeln;
- `SCHEDULED_JOB_RECOVERY_NOT_READY`: kontrollera
  `scheduled_job_runs`. En `CLAIMED`-rad vars `lease_expires_at` har
  passerat ska återtas automatiskt av nästa daemonvarv. Kör aldrig en
  äldre `brain_cycle` manuellt; den ska markeras `SKIPPED_STALE`, medan
  `student_study` får spelas ikapp inom den begränsade sex-timmarskön;
- `CRITICAL_BENCHMARK_INCIDENT`: pausa experimentet och bevara
  rådata/checksummor;
- `MONITOR_EVIDENCE_UNAVAILABLE`: kontrollera monitorlogg,
  databasanslutning och systemklocka.

## Nödstopp och daglig förlustgräns

Nya `BUY`-affärer kräver en aktiv operatörskontroll. Ett nödstopp eller
en utlöst daglig förlustgräns stoppar endast nya exponeringar;
`SELL`-affärer för att minska risk är fortsatt tillåtna.

Den dagliga gränsen är 3 procent som standard och mäts mark-to-market
mot senaste fullständigt prissatta föregående snapshot. Om ett sådant
snapshot saknas används portföljvärdet vid dagens första kontroll.
En överträdelse låses för resten av Stockholms handelsdatum även om
portföljen senare återhämtar sig. Nästa datum skapar en ny dagsbas.

Alla kontrolländringar och riskutvärderingar loggas append-only.
Endast en operatörsidentitet får ändra kontrollen:

```bash
docker compose exec agent python -m src.risk_admin status

docker compose exec agent python -m src.risk_admin halt \
  --reason 'Dataincident under utredning' \
  --operator 'operator:diffen'

docker compose exec agent python -m src.risk_admin resume \
  --reason 'Incident stängd och verifierad' \
  --operator 'operator:diffen'

docker compose exec agent python -m src.risk_admin set-limit 2.5 \
  --reason 'Godkänd lägre paper-risk' \
  --operator 'operator:diffen'
```

Dashboardens operationsstatus visar `TRADING_HALTED` respektive
`DAILY_LOSS_LIMIT_BREACHED`. Databasen kontrollerar nödstoppet igen
atomiskt när en köpaffär bokförs; en kontroll som ändras efter analys
men före order kan därför inte kringgås.

## Verifiera en paperaffär

Dashboardens Affärsjournal visar den operativa kedjan. En affär med
ursprung `AI_DECISION` ska ha beslut-ID, samma strategiversion som
föräldrabeslutet och, när providerdata används, quote-ID, quote-tid och
råfilschecksumma. Databasen stoppar AI-affärer som saknar länken eller
försöker använda ett beslut från fel strategi eller
benchmarkexperiment. `AUTOMATED_SCAN`, `MECHANICAL_EXIT`, `MANUAL` och
`LEGACY` är separata ursprung och får inte ha ett AI-beslut-ID.

## Releasekedja

1. Pull request eller push till `main` måste klara PostgreSQL-tester,
   dashboardtester, dependency-kontroll, audit och produktionsbuild.
2. Efter grön CI bygger ett separat jobb agent och dashboard från exakt
   samma commit.
3. Bilderna publiceras med commit-taggar och provenance-attestering.
4. Ett immutabelt release-manifest fryser båda image-digestarna,
   commit-SHA och kompatibelt databasschemaintervall.
5. Plattformens poller på Kajen ser endast ett lyckat, CI-utlöst
   `build-push.yml` på `main` med ett entydigt digestbevis för agentimagen.
6. `diffen77/plattform-deploy` validerar repo, SHA, digest och en isolerad
   appnyckel innan någonting byts.
7. Migreringar och kalendersynk körs ur den nya agentimagen före byte.
8. Alla långlivade agenttjänster och dashboarden byts till samma SHA och
   verifieras mot de images som faktiskt hämtades.
9. Releasen är godkänd först när `https://trader.lediff.online/api/health`
   svarar med exakt `{"status":"ok"}`. Vid fel återställs samtliga utbytta
   tjänster till de images som körde före försöket.

Release-smoken använder vanlig `readiness`, eftersom en deploy måste
kunna ske när börsen är stängd. Den innebär inte att handel är tillåten;
`trading-readiness` fortsätter att blockera tills en öppen session har
komplett, licensierad aktie- och OMXSGI-data.

Det finns inga `latest`-taggar eller Watchtower i releasekedjan.

## GitHub- och Bitwarden-konfiguration för staging

Den aktiva stagingvägen ägs av `diffen77/plattform-deploy`. Apprepot har
inga SSH-nycklar och ingen PAT. Plattformsrepot innehåller den versionerade
stackdeklarationen, kör på runneretiketterna `self-hosted`, `kajen` och
`staging`, och hämtar endast trading-agentens appspecifika dispatchnyckel ur
Bitwarden-projektet Prod. Runtime-hemligheterna hämtas ur Bitwarden-projektet
Staging och mappas till Compose utan att skrivas till Git eller logg.

Compose-projektet måste förbli `trading-agent-staging`, katalogen måste förbli
`/srv/prod/staging/trader` och volymerna `postgres_data` samt `agent_data` får
inte byta namn. Det är identitetskontraktet som bevarar ledger och agentdata
över releaser.

## Fristående produktionsprofil, ännu inte aktiv

`ops/release/` innehåller fortfarande den hårdare fristående profilen för en
framtida separat produktionsmiljö. Den används inte av Kajens staging och får
inte aktiveras genom att återinföra en direkt SSH-workflow i apprepot.

Om profilen tas i bruk ska `${DEPLOY_PATH}/runtime.env` skapas och förvaltas
utanför Git/release-artefakter. Filen måste vara en absolut, vanlig,
icke-symlinkad fil där grupp och övriga saknar all åtkomst, exempelvis
läge `0600`. Secret-värden får inte ligga direkt i filen. Minimikrav:

- `POSTGRES_PASSWORD_FILE`
- `DATABASE_URL_FILE`
- `DASHBOARD_AUTH_USERNAME_FILE`
- `DASHBOARD_AUTH_PASSWORD_FILE`
- `POSTGRES_IMAGE` som full `repository@sha256:digest`
- `LLM_BACKEND`
- `LLM_MODEL`
- `OLLAMA_URL` för `openai-compatible`

De fyra obligatoriska `*_FILE`-värdena ska vara absoluta sökvägar.
Secret-filerna ska vara vanliga, får inte vara symlänkar, ska ägas av
deployanvändaren och ha läge `0400` eller `0600`. Varje fil får innehålla
exakt ett UTF-8-värde på högst 16 KiB, med en valfri avslutande
radbrytning. Databaslösenordet i `DATABASE_URL_FILE` måste motsvara
`POSTGRES_PASSWORD_FILE`.

Valfria credentials anges på samma sätt:

- `OPENAI_COMPATIBLE_API_KEY_FILE`
- `ANTHROPIC_API_KEY_FILE`
- `TELEGRAM_BOT_TOKEN_FILE`
- `TELEGRAM_CHAT_ID_FILE`
- `NEO4J_PASSWORD_FILE`
- `S3_ACCESS_KEY_ID_FILE`
- `S3_SECRET_ACCESS_KEY_FILE`

Utelämnade Telegram-filer stänger av notifieringen. Lokal
`openai-compatible` utan autentisering använder den interna
`local-no-auth`-markören om dess fil utelämnas. Anthropic-backend
stoppar om dess nyckelfil saknas. Inline-varianter som `DB_PASSWORD`,
`DATABASE_URL`, dashboarduppgifter, tokens eller API-nycklar avvisas
innan Docker anropas.

Deployprocessen läser filerna först efter validering och överför
värdena till Docker Compose secrets för just den processen. Tjänsterna
får endast `*_FILE=/run/secrets/...`: agenten kör som UID/GID 10001,
dashboarden som UID/GID 1000 och filerna monteras med läge `0400`.
Värdena finns därför inte i tjänsternas vanliga containermiljö eller
image.

När marknadsdata är avtalad och validerad ska filen även sätta
`ENABLE_NASDAQ_DELAYED_INGESTION=true` och rätt
`MARKET_DATA_CONTRACT_KEY`. Att endast sätta flaggan kan inte passera
provider-, alias- eller coverage-gaterna. Profilflaggorna får bara
förekomma en gång vardera och värdet måste vara exakt `true` eller
`false`, utan variabelexpansion eller kommentar på samma rad.

Deployen startar alltid `agent`, `dashboard` och `monitor`. Profilen
`market-data` startas när antingen
`ENABLE_NASDAQ_PUBLIC_PRETRADE=true` eller
`ENABLE_NASDAQ_DELAYED_INGESTION=true`; profilen `nasdaq-reference`
startas endast när `ENABLE_NASDAQ_REFERENCE_SYNC=true`. Profilerna
`knowledge-graph` och `object-storage` styrs separat av
`ENABLE_KNOWLEDGE_GRAPH=true` respektive
`ENABLE_OBJECT_ARCHIVE=true`. När en flagga stängs av stoppas den
tidigare profiltjänsten, så en gammal container kan inte fortsätta
hämta eller spegla data efter konfigurationsändringen.

### S3-kompatibelt rådataarkiv

Objektlagringen är en asynkron, verifierad spegling av redan validerade
`market_data_files` och `reference_data_files`. PostgreSQL är fortsatt
system of record. Import, analys och papertrading fortsätter därför om
S3 är tillfälligt otillgängligt; felet sparas som append-only
driftevidens och nästa cykel försöker igen.

Följande icke-hemliga runtimevärden krävs:

- `ENABLE_OBJECT_ARCHIVE=true`
- `S3_ENDPOINT`, utan inbäddade credentials, query eller path
- `S3_BUCKET`
- `S3_REGION`, normalt `garage` för Garage

Credentials anges endast genom `S3_ACCESS_KEY_ID_FILE` och
`S3_SECRET_ACCESS_KEY_FILE`. Arbetaren använder alltid det isolerade
prefixet `trading-agent/` och kan inte konfigureras att skriva i
bucketens rot.

Kör en riktig, ofarlig verifiering av write/head/read/checksum/delete:

```bash
docker compose --profile object-storage run --rm --no-deps \
  object-archive-worker python -m src.object_archive probe
```

Kör en begränsad arkiveringscykel:

```bash
docker compose --profile object-storage run --rm --no-deps \
  object-archive-worker python -m src.object_archive once
```

Kontrollera därefter hälsa och append-only-resultat:

```bash
docker compose exec object-archive-worker \
  python -m src.object_archive health
```

```sql
SELECT status, selected_count, archived_count, failed_count, finished_at
FROM object_archive_runs
ORDER BY finished_at DESC, id DESC
LIMIT 20;

SELECT object_key, raw_sha256, compressed_sha256, compressed_size, archived_at
FROM market_data_object_archives
ORDER BY archived_at DESC, id DESC
LIMIT 20;
```

`NO_PENDING` är korrekt när det inte finns nya `market_data_files`.
Det bevisar inte att marknadsdatapipelinen har producerat rådata; den
kontrollen görs separat i dashboardens mått `Rådataarkiv`.

### Publik Nasdaq pre-trade för intern papertrading

Den kostnadsfria standardvägen använder
`ENABLE_NASDAQ_PUBLIC_PRETRADE=true` och kontraktsnyckeln
`nasdaq-public-pretrade-xsto-v1`. Ingen credential eller betald
referensdatafil behövs. `market-sync` gör följande under en explicit
öppen XSTO-session:

1. verifierar den officiella policiesidan och registrerar ett
   append-only publikt policykontrakt;
2. väljer i strikt minutordning upp till
   `NASDAQ_PRETRADE_MAX_FILES_PER_RUN` filer från den
   sessionsfiltrerade katalogen (standard 10);
3. strömmar filen till en låst temporär fil och verifierar checksumma,
   storlek, MIME, schema och tidsstämplar;
4. behåller endast `XSTO` CLOB och reducerar ordnade siduppdateringar
   till en förseglad bid/ask-bok;
5. accepterar filen som live-evidens endast vid 15–20 minuters
   observerad leveranstid och tar därefter bort råfilen.

Daemonens intervall mäts från cykelstart till cykelstart. Tiden för
nedladdning och reducering läggs alltså inte ovanpå intervallet, och
den begränsade flerfilsimporten kan hämta igen en kort tillfällig
eftersläpning.

En historisk eller eftermarknadsfil får aldrig märkas om som färsk.
Första provider-valideringen kan därför bara skapas under en verklig
öppen session. Om ett avbrott redan har passerat 20-minutersgränsen
ska den gamla append-only-strömmen bevaras och en ny versionssatt
`NASDAQ_PRETRADE_CONTRACT_KEY` användas för nästa färska minut. Försök
inte fylla gapet med sent mottagna filer. Kontrollera status utan att
mutera data:

```sql
SELECT
    contract.status,
    contract.authorization_basis,
    validation.status AS validation_status,
    validation.validation_basis
FROM market_data_provider_contracts contract
LEFT JOIN market_data_provider_validations validation
  ON validation.contract_id = contract.id
WHERE contract.contract_key = 'nasdaq-public-pretrade-xsto-v1'
ORDER BY validation.created_at DESC NULLS LAST
LIMIT 1;
```

### Pris- och indexprovider: avtal och femsessionersacceptans

Betalda pris- och `OMXSGI`-leveranser provisioneras med
`provider_contract_admin`; använd inte manuell SQL. Flödet består av
ett utkast, fem verkliga acceptanssessioner och ett separat
valideringssteg. Utkastet lagrar operatören som skapade det.

Skapa först en strikt kontraktsfil med exakt följande fält:

```json
{
  "contract_key": "licensed-xsto-level1-v1",
  "provider": "nasdaq-nordic",
  "product_name": "Nordic Equity Level 1 delayed",
  "data_type": "delayed-pre-trade-equity",
  "mic": "XSTO",
  "delivery_mode": "DELAYED_15M",
  "transport": "AUTHORIZED_VENDOR",
  "nominal_delay_seconds": 900,
  "max_transport_lag_seconds": 30,
  "non_display_category": "NONE",
  "reference_symbols_included": true,
  "terms_url": "https://avtalssystem.example/godkant-avtal",
  "valid_from": "2026-07-01",
  "valid_until": "2027-06-30"
}
```

Filen måste vara absolut, vanlig och icke-symlinkad. Verktyget låser
användningen till intern papertrading och extern distribution till
`false`; de värdena kan inte väljas i manifestet.

```bash
docker compose run --rm \
  -v /absolut/värdsökväg/provider-contract.json:/secure/provider-contract.json:ro \
  agent \
  python -m src.provider_contract_admin draft \
  --contract-json /secure/provider-contract.json \
  --operator operator:diffen
```

Kör sedan leveransen under exakt fem sammanhängande officiella
XSTO-sessioner med status `OPEN` eller `HALF_DAY`. Bevara de
underliggande filerna/loggarna och skapa ett manifest med en SHA-256
per sessions bevispaket. För en aktieprodukt måste snapshot-id,
checksumma och instrumentantal vara exakt den senaste frysta
XSTO-snapshoten:

```json
{
  "contract_key": "licensed-xsto-level1-v1",
  "reference_snapshot_id": 123,
  "reference_checksum_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
  "validation_valid_until": "2026-08-31T21:59:59+02:00",
  "retention_policy": "Rådata och härledda resultat lagras internt enligt avtalet.",
  "raw_storage_allowed": true,
  "derived_storage_allowed": true,
  "transport_verified": true,
  "correction_handling_verified": true,
  "restart_verified": true,
  "gap_recovery_verified": true,
  "kill_switch_verified": true,
  "sessions": [
    {
      "session_date": "2026-07-20",
      "expected_instruments": 416,
      "product_covered_instruments": 416,
      "symbol_mapped_instruments": 416,
      "sample_file_count": 1,
      "sample_quote_count": 416,
      "max_observed_delivery_seconds": 918,
      "evidence_checksum_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    },
    {
      "session_date": "2026-07-21",
      "expected_instruments": 416,
      "product_covered_instruments": 416,
      "symbol_mapped_instruments": 416,
      "sample_file_count": 1,
      "sample_quote_count": 416,
      "max_observed_delivery_seconds": 916,
      "evidence_checksum_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    },
    {
      "session_date": "2026-07-22",
      "expected_instruments": 416,
      "product_covered_instruments": 416,
      "symbol_mapped_instruments": 416,
      "sample_file_count": 1,
      "sample_quote_count": 416,
      "max_observed_delivery_seconds": 919,
      "evidence_checksum_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    },
    {
      "session_date": "2026-07-23",
      "expected_instruments": 416,
      "product_covered_instruments": 416,
      "symbol_mapped_instruments": 416,
      "sample_file_count": 1,
      "sample_quote_count": 416,
      "max_observed_delivery_seconds": 920,
      "evidence_checksum_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    },
    {
      "session_date": "2026-07-24",
      "expected_instruments": 416,
      "product_covered_instruments": 416,
      "symbol_mapped_instruments": 416,
      "sample_file_count": 1,
      "sample_quote_count": 416,
      "max_observed_delivery_seconds": 917,
      "evidence_checksum_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    }
  ]
}
```

Exempelvärdena för snapshot och datum ska ersättas med den faktiska
acceptansen. `max_observed_delivery_seconds` måste ligga mellan
nominell delay och nominell delay plus avtalad maximal transporttid.
För `data_type=delayed-index-level` ska
`reference_snapshot_id` och `reference_checksum_sha256` vara `null`
och samtliga tre instrumentantal vara exakt `1`; indexbeviset får inte
låtsas vara bundet till en aktiesnapshot.

Validera manifestet mot den exakt granskade avtalsfilen:

```bash
docker compose run --rm \
  -v /absolut/värdsökväg/provider-acceptance.json:/secure/provider-acceptance.json:ro \
  -v /absolut/värdsökväg/reviewed-agreement.pdf:/secure/reviewed-agreement.pdf:ro \
  agent \
  python -m src.provider_contract_admin validate \
  --acceptance-json /secure/provider-acceptance.json \
  --terms-file /secure/reviewed-agreement.pdf \
  --operator operator:diffen \
  --confirm-internal-paper-use \
  --confirm-no-external-distribution \
  --confirm-raw-storage \
  --confirm-derived-storage \
  --confirm-transport-tested \
  --confirm-five-consecutive-sessions \
  --confirm-restart-and-gap-recovery \
  --confirm-kill-switch

docker compose run --rm agent \
  python -m src.provider_contract_admin status \
  --contract-key licensed-xsto-level1-v1
```

Verktyget hashar avtalsfilens och acceptansfilens exakta bytes,
verifierar sessionerna mot kalendern och binder aktieprodukten till
senaste frysta referenssnapshot. En validering får inte leva längre än
avtalet. Samma evidens kan återköras idempotent men inte skrivas om.

Återkalla avtalet direkt om rättighet, transport eller datakvalitet
inte längre gäller:

```bash
docker compose run --rm agent \
  python -m src.provider_contract_admin revoke \
  licensed-xsto-level1-v1 \
  --operator operator:diffen \
  --reason 'Leveransen eller användningsrätten gäller inte längre.'
```

Återkallelsen sparar tid, operatör och orsak append-only och stoppar
providerdata i runtime, pre-trade och dashboard.

### Nasdaq-referensfil via SFTP

Den licensierade referensfilen har en separat, avstängd
`nasdaq-reference`-profil. Följande krävs först efter att Nasdaq har
godkänt produkt, användning och lagring:

- `ENABLE_NASDAQ_REFERENCE_SYNC=true`;
- `NASDAQ_REFERENCE_CONTRACT_KEY`, exakt avtalsnyckel från ett
  aktuellt `VALIDATED` schema-30-bevis;
- `NASDAQ_NDL_USERNAME`, vilket är kontots e-postadress;
- `NASDAQ_NDL_PRIVATE_KEY_HOST_FILE`, absolut sökväg till privat nyckel;
- `NASDAQ_NDL_KNOWN_HOSTS_HOST_FILE`, absolut sökväg till en host key
  som verifierats mot Nasdaq via en oberoende kanal;
- `NASDAQ_NDL_RUN_AS_UID` och `NASDAQ_NDL_RUN_AS_GID`, numeriska ägare
  som kan läsa båda mountade filerna.

Entitlement-posten provisioneras först efter juridisk och teknisk
granskning; repositoryt seedar ingen sådan post. Använd inte manuell
SQL. Skapa i stället en strikt JSON-fil med exakt dessa fält:

```json
{
  "contract_key": "avtalsnyckel-från-nasdaq",
  "provider": "nasdaq-nordic",
  "product_name": "Nordic Equity Reference Data Files",
  "mic": "XSTO",
  "retention_policy": "Godkänd intern lagringsregel enligt avtalet.",
  "terms_url": "https://avtalssystem.example/godkant-avtal",
  "valid_from": "2026-07-01",
  "valid_until": "2027-06-30",
  "host_key_algorithm": "ssh-ed25519",
  "host_key_fingerprint_sha256": "SHA256:ersatt-med-verifierat-fingerprint"
}
```

JSON-filen och den exakt granskade avtalsfilen måste vara absoluta,
vanliga, icke-symlinkade filer. Kommandot beräknar SHA-256 över
avtalsfilens exakta bytes och accepterar varken credentials eller
privata nycklar:

```bash
docker compose run --rm \
  -v /absolut/värdsökväg/reference-approval.json:/secure/reference-approval.json:ro \
  -v /absolut/värdsökväg/reviewed-agreement.pdf:/secure/reviewed-agreement.pdf:ro \
  agent \
  python -m src.reference_entitlement_admin validate \
  --evidence-json /secure/reference-approval.json \
  --terms-file /secure/reviewed-agreement.pdf \
  --operator operator:diffen \
  --confirm-internal-paper-use \
  --confirm-raw-storage \
  --confirm-derived-storage \
  --confirm-host-key-independent

docker compose run --rm agent \
  python -m src.reference_entitlement_admin status \
  --contract-key avtalsnyckel-från-nasdaq
```

De fyra bekräftelserna är operatörens uttryckliga attest att avtalet
tillåter den fasta användningen
`INTERNAL_ANALYSIS_AND_PAPER`, rå och härledd lagring samt att
värdnyckeln verifierats oberoende. En avtalsnyckel kan inte skrivas
över eller återanvändas.

Återkalla omedelbart när avtal, lagringsrätt eller host key inte längre
gäller:

```bash
docker compose run --rm agent \
  python -m src.reference_entitlement_admin revoke \
  avtalsnyckel-från-nasdaq \
  --operator operator:diffen \
  --reason 'Avtalet har avslutats och datan får inte längre användas.'
```

Återkallelsen är append-only och lagrar tid, operatör och orsak. Den
stoppar nätanslutning och gör tidigare alias operativt oanvändbara.

Nyckelfilen ska ha
högst `0600`, båda filerna ska vara vanliga filer och inte symlänkar,
och mountarna är read-only. `known_hosts` måste innehålla exakt en
värdnyckel för `sftp.data.nasdaq.com` och matcha algoritm och
SHA-256-fingerprint i entitlement-beviset. Kör först one-shot:

```bash
docker compose --profile nasdaq-reference run --rm \
  nasdaq-alias-sync python -m src.nasdaq_alias_sync once
```

Kontrollera därefter i Handelsberedskap att alias-snapshoten pekar på
exakt senaste FIRDS-snapshot, har full täckning och visar entitlement
som redo. Starta inte daemonen
förrän detta stämmer. Tjänsten har ingen lösenordsinloggning, använder
inte ssh-agent eller nycklar från hemkatalogen och accepterar inte en
okänd host key. Ett återkallat eller utgånget entitlement stoppar
hämtning före nätanslutning och gör tidigare alias oanvändbara. Den
automatiska körningen gör högst ett idempotent försök per timme mot
senaste officiella XSTO-session efter 06:45
`Europe/Stockholm`.

Releasejobbet tolkar de fem explicita profilflaggorna
`ENABLE_NASDAQ_PUBLIC_PRETRADE`,
`ENABLE_NASDAQ_DELAYED_INGESTION` och
`ENABLE_NASDAQ_REFERENCE_SYNC`, `ENABLE_KNOWLEDGE_GRAPH` och
`ENABLE_OBJECT_ARCHIVE` samt de namngivna
`*_FILE`-sökvägarna genom separata strikta parserar. Det sourcar,
skriver, loggar eller kopierar aldrig `runtime.env` och skriver aldrig
ut secret-innehåll. Serverns Docker måste separat ha read-access till
privata GHCR-images.

## Modellval

Backend väljs explicit:

- `LLM_BACKEND=openai-compatible` använder `OLLAMA_URL` och kräver att
  `LLM_MODEL` finns i `/v1/models`.
- `LLM_BACKEND=anthropic` kräver både `ANTHROPIC_API_KEY_FILE` och ett
  explicit `LLM_MODEL`.

Det finns ingen automatisk fallback mellan leverantörer. En felaktig
backend, URL med inbäddade credentials, saknad nyckel eller saknad
modell stoppar funktionen fail-closed.

Standardmodellen för en lokal Ollama-installation är
`qwen2.5-coder:14b` på port `11434`; deploymenten ska fortfarande ange
modell och privat nåbar URL explicit.

## Rollback

Servern behåller `current-release` och `previous-release`. Om smoke-test
för en ny release misslyckas försöker den återstarta föregående
image-digest. Databasen migreras aldrig bakåt.

Rollback tillåts endast om nuvarande databasversion ligger inom den
tidigare releasens deklarerade `SCHEMA_MIN`–`SCHEMA_MAX`. En
schemabrytande release måste därför ha en separat, prövad
expand/migrate/contract-plan.

Rollbacklogiken är verifierad mot en simulerad Docker-värd. Innan
produktionsstart återstår ett verkligt staging-/produktionsdrill på den
avsedda servern.

## Operatörsfrågor

Driftstatusen ska kunna svara på:

1. Är applikationen och dess ledger säkra att köra?
2. Är handel tillåten just nu, och vilken spärr stoppar annars?
3. Kör rätt commit och exakt vilka image-digestar?
4. Kan föregående release återstartas med nuvarande schema?

Nu finns maskinläsbara svar för de tre första i releasemanifest,
healthcheck och den separata monitorprocessen. Larmhistoriken är
beständig och deduplicerad. Före produktionsdrift återstår att
testskicka varje larm till den faktiska mottagaren och att välja extern
historisk metrics-/logglagring.

Kvarvarande symptomlarm utanför den nuvarande monitorprocessen:

- provider-kontrakt eller validering löper ut inom 14 dagar: ticket;
- misslyckad release med lyckad automatisk rollback: ticket;
- misslyckad release och misslyckad rollback: page.
