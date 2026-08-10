# Trading Agent

AI-assisterad papertrading för aktier på Nasdaq Stockholm.

## Vision

Agenten ska analysera marknad, bolag, nyheter och makrodata, fatta
spårbara köp-/säljbeslut och utvärdera resultatet mot ett relevant
benchmark. AI:n får föreslå beslut, men deterministisk kod ska alltid
upprätthålla datakvalitet, positionsgränser och exitregler.

Projektet är papertrading-first. Ingen riktig orderläggning eller riktiga
pengar ska aktiveras innan strategi, data, riskregler och drift har
verifierats över en tillräckligt lång period.

## Körning

Förutsättningar: Docker med Compose-stöd.

```bash
export DB_PASSWORD='use-a-strong-local-password'
export DASHBOARD_AUTH_USERNAME='operator'
export DASHBOARD_AUTH_PASSWORD='use-at-least-16-characters'
docker compose up -d
docker compose logs -f migrate agent
```

Dashboarden finns på `http://localhost:3020`.
Databasen och dashboarden binds endast till `127.0.0.1` som standard.
Använd HTTPS via en betrodd reverse proxy eller Tailscale om dashboarden
ska nås från en annan dator; HTTP Basic-uppgifter får inte skickas över
öppet nät.

Databasschemat uppgraderas av den separata engångstjänsten `migrate`.
Agenten och dashboarden startar bara om migreringen lyckas. Äldre
migrering `003_seed_companies.sql` innehåller en destruktiv truncate och
spelas därför aldrig om automatiskt.

Neo4j kan aktiveras som ett separat, härlett tradingminne:

```bash
docker compose --profile knowledge-graph up -d \
  knowledge-worker knowledge-shadow-worker
```

Lösenordet ska komma från en låst secret-fil eller BWS, aldrig från en
committad `.env`. Se
[trading knowledge graph](docs/trading-knowledge-graph.md).

Ett separat S3-kompatibelt rådataarkiv kan aktiveras utan att koppla
marknadsdataimporten till objektlagringens tillgänglighet:

```bash
docker compose --profile object-storage up -d object-archive-worker
docker compose exec object-archive-worker \
  python -m src.object_archive health
```

Arbetaren speglar endast redan validerade och checksummebundna
`market_data_files` och `reference_data_files`. PostgreSQL förblir
system of record och en S3-störning stoppar inte papertrading.
Credentials ska injiceras som runtime-secrets. Se
[objektlagring](docs/object-storage.md).

## Tester

Enhetstesterna kan köras utan databas:

```bash
cd agent
python -m pip install -r requirements-test.txt
python -m pytest tests/test_risk.py tests/test_brain_validation.py
```

Hela testsviten kräver en migrerad PostgreSQL-databas:

```bash
cd agent
TEST_DATABASE_URL=postgresql://... python -m pytest tests
```

CI startar en isolerad PostgreSQL 16, kör migreringarna och låter alla
testfel stoppa bygget. Dashboard-CI använder låsfil, kör `npm audit`
som blockerande gate och bygger produktionsbundlen. Container-images
byggs först efter att CI på `main` har lyckats.

## Arkitektur

```text
agent/
  src/core/       AI-beslut, deterministisk risk och papertrader
  src/data/       datakällor och atomisk PostgreSQL-ledger
  src/knowledge_graph.py  idempotent Neo4j-synk
  src/knowledge_worker.py kontinuerligt tradingminne
  src/knowledge_shadow_worker.py isolerad kontroll/graf-jämförelse
  src/object_archive.py  verifierad spegling till S3-kompatibelt arkiv
  tests/          enhets- och PostgreSQL-integrationstester
dashboard/        Next.js-dashboard
db/init/          bas-schema för en tom PostgreSQL-volym
db/migrations/    versionsstyrda framåtmigreringar
db/migrate.sh     migrations-runner
```

PostgreSQL är fortsatt system of record. Neo4j lagrar härledda samband
mellan bolag, instrument, beslut, kandidater, utfall, papertrades och
strategiversioner. En separat skuggarbetare jämför två isolerade analyser
av samma historiska beslutspunkt: en kontroll utan grafminne och en med
strikt strukturerade, tidsavgränsade utfallsaggregat. Resultaten sparas
append-only för utvärdering, men grafminnet påverkar ännu varken operativa
köp-/säljbeslut eller paperorders.

Den bindande datagränsen och varför Yahoo, hårdkodade tickers och
webbskrapning inte får vara operativa fallbacks beskrivs i
[`ADR-001`](docs/decisions/ADR-001-authorized-market-data-boundary.md).

## Marknadsdata och aktieuniversum

Den tidigare hårdkodade listan och Yahoo-integrationen är borttagna ur
alla driftvägar eftersom de inte uppfyllde kraven på komplett,
verifierbart aktieuniversum eller avtalad färskhet.
Det nya referensdataflödet hämtar officiella ESMA FIRDS-fullfiler,
verifierar överföringschecksumma, arkiverar råfilerna och uppdaterar
instrumentregistret atomiskt. Det filtrerar aktier, preferensaktier och
depåbevis som är upptagna till handel på Nasdaq Stockholm (`MIC XSTO`);
ETF:er och andra produkter ingår inte.

- alla relevanta aktier som FIRDS redovisar för Nasdaq Stockholm
  (`MIC XSTO`);
- separat hantering av aktieslag via ISIN och stabilt internt
  instrument-id;
- källa, marknadstid, mottagningstid och fördröjning på varje datapunkt;
- `Europe/Stockholm`, officiell handelskalender och automatisk
  stale-data-spärr;
- realtid endast med uttrycklig licens, annars högst 15 minuters
  fördröjning.

Den fulla FIRDS-snapshoten daterad 2026-07-25 gav 416 instrument:
406 stamaktier, 6 preferensaktier och 4 depåbevis. ESMA-filerna saknar
Nasdaqs handelssymboler, så ISIN är stabil identitet medan symbolerna
kan kompletteras senare från en licensierad Nasdaq-referensfil eller
annan avtalad källa. Avsaknad av tickeralias blockerar inte intern
papertrading med ISIN som uttryckligt märkt instrumentnyckel.

Nasdaqs officiella XSTO-kalender för 2024–2026 är versionsstyrd,
inklusive helgdagar och halvdagar. Okända år, saknad session eller
för gammal prisdata stoppar handel fail-closed.

Databasschema 44, public-policy-kontrakt, freshness-gate och en
aktiverbar `market-sync` finns på plats. Systemet handlar inte förrän
instrument, marknadssession och en färsk exekverbar orderbok faktiskt
har importerats.

Det verkliga publika pre-trade-formatet är verifierat och har en
strömmande parser samt Level 1-reducerare för ordnade bid/ask-
uppdateringar, explicita sidoraderingar, ensidig bok och minutluckor.
Parsern filtrerar strikt till XSTO CLOB; PATS, andra parallella
handelssystem och rader utan CLOB-identitet blir inte exekverbara
orderböcker. Råfilen raderas efter parser- och checksummevalidering.
Reduceraren tar endast parserutfärdade minutbatcher med exakt
filminut, lokalt beräknad råbytes-SHA-256 och ett kanoniskt hashat,
validerat referensuniversum. En separat stream-cursor bevarar
minutkontinuitet även när filen saknar XSTO-uppdateringar. Reduceringen
stoppar bland annat vid mottagningstidsregression, okänd ISIN,
nollikviditet, mellanliggande korsad bok och otydlig
sommartidsminut.
Migration 025 lagrar parserresultatet append-only som exakt filbatch,
ordnade siduppdateringar, stream-cursor och materialiserad bästa
bid/ask. Lagringen är idempotent, återställs efter omstart och
förseglar inte en minut förrän alla förväntade rader och state finns.
Ett pre-trade-paperfill måste använda senaste förseglade state, rätt
exekverbara sida, återstående visad volym och en öppen XSTO-session.
Äldre referensmedlemskap märks `LEGACY_UNVERIFIED` och kan inte användas
som historiskt pre-trade-bevis.

Nasdaqs publicerade icke-kommersiella villkor registreras separat från
filacceptansen. Varje fil måste därefter visa 15–20 minuters verklig
leveranstid; en gammal fil får parservalideras men aldrig märkas färsk i
efterhand. Vid första synk och återstart väljs bara filer som ligger
inom en officiell öppen XSTO-session.

Schema 026 binder det körande forward-benchmarket till en uttryckligt
förregistrerad exekveringspriskälla. I Level 1-läge måste varje fill
använda senaste förseglade orderbok från exakt samma provideravtal och
referenssnapshot som experimentet. Köp utgår från ask, sälj från bid,
faktisk spread mäts mot midpoint och endast fryst slippage läggs på
exekverbar sida; syntetisk `spread_bps` är då förbjuden. Vägen är ännu
inte ansluten till någon licensierad nättransport. Den cirka 165 MB
stora nordiska minutfilen lagras inte direkt i PostgreSQL; endast den
kanoniska XSTO-evidensen lagras medan full råfil kräver avtalad
objektlagring eller annan tillåten retention.
Den lokala SHA-256-kontrollen visar innehållsintegritet efter hämtning;
den är inte en signatur eller ett fristående bevis på Nasdaq-ursprung.

Schema 33 tillåter två uttryckliga providergrunder: förhandlat avtal
eller Nasdaqs publicerade villkor för icke-kommersiell användning. Den
publika vägen förbjuder rålagring och extern distribution, kräver
kontinuerlig filvalidering och håller trading-gaten stängd tills en
färsk fil har accepterats. En feature-flagga ensam räcker alltså inte.

Schema 15 lägger dessutom ett förregistrerat forward-paper-kontrakt mot
OMXSGI. Strategi, release-manifest, image-digest, modellbevis,
datakällor, kostnader och godkännandekriterier fryses före start.
Paperfills belastas automatiskt med fryst avgift, spread och slippage;
stängda affärer och kritiska incidenter räknas från append-only-loggar
och drawdown använder sessionens högsta/lägsta NAV. Se
[forward-paper-kontraktet](docs/forward-paper-benchmark.md).

Schema 16 gör även den löpande portföljvärderingen spårbar. Öppna
positioner får endast värderas med quotes från den aktuella,
fullvaliderade leverantören. Varje sparad snapshot binder ticker, antal,
pris och marknadsvärde till exakt `market_quotes`-ID. Saknas ett enda
godkänt pris markeras totalvärdet som ej värderat och beslut eller
snapshot stoppas; äldre dagskurser och lagrade fallbackpriser används
inte.

Schema 17 lagrar append-only OMXSGI-nivåer bundna till ett uttryckligt
indexleverantörsavtal. Den licensierade last-trade-vägen kräver en färsk
nivå och föregående sessions verifierade stängningsnivå. Den publika
pre-trade-vägen startar intern papertrading med neutral indexinput;
benchmark väljs och auktoriseras separat.

Schema 18 fryser exakt vilka ISIN som ingår i varje referenssnapshot,
inte bara antal och checksumma. Schema 19 binder varje filinläst quote
till den exakta arkiverade råfilens checksumma. Schema 20 binder varje
portföljsnapshot till det aktiva forward-experimentet. Schema 21 låter
den automatiska efterstängningsrutinen härleda NAV, dagsintervall,
ledgerkostnader, faktisk quote-täckning och OMXSGI-nivå för både
ordinarie XSTO-dagar och officiella halvdagar. Manuell observations-JSON
är inte längre en operativ väg.

Schema 025 lagrar pre-trade-filer och deras ordnade bid/ask-förändringar
append-only. En separat cursor bevarar även tomma XSTO-minuter, och
databasen kräver exakt nästa minut, senaste provider-validering,
verifierad historisk referensidentitet och komplett batch innan
försegling. Pre-trade-paperfills binds till senaste förseglade sida och
kan inte återanvända mer av en oförändrad visad nivå än dess kvantitet.
Schema 026 skiljer värderingsprovider från exekveringsprovider och
fryser `LAST_TRADE_PLUS_BPS` eller `TOP_OF_BOOK_PLUS_SLIPPAGE` i samma
kanoniskt hashade förregistrering som strategi, release och övriga
kostnader. En schema-25-förregistrering uppgraderas konservativt till
det tidigare last-trade-läget utan att dess ursprungliga hash skrivs om.

Schema 027 fryser hela förregistreringen redan när utkastet skapas och
kräver ett exakt `market_index_levels`-ID för OMXSGI-startnivån.
Last-trade-paperfills måste komma från en arkiverad fil bunden till
experimentets exakta provideravtal och ett instrument i dess frysta
referenssnapshot. Benchmarkaffärernas exekverings- och källevidens är
append-only och kan inte frigöras genom SQL-update eller delete.
Filbundna quotes är också append-only, inklusive pris, volym, valuta,
tider och källfil.

Schema 028 lagrar Nasdaqs dagliga `Nordic_Equity_RefData.tip`
append-only och binder varje provider-ticker till exakt ISIN i en fryst
FIRDS-snapshot. Parsern kräver aktuell TIP-version, `BDt` + `BDSh`,
`PMIc=XSTO`, rätt exchange, valuta och CFI samt exakt 100 procents
täckning. En komplett snapshot aktiveras med en atomisk head-pekare;
delimporter, äldre filer och ändrat innehåll för samma datum stoppas.
Den autentiserade Nasdaq Data Link Files/SFTP-transporten är avsiktligt
avstängd innan entitlement och lagringsrätt finns. Själva adaptern är
implementerad fail-closed: värden är låst till
`sftp.data.nasdaq.com:22`, okänd host key avvisas, endast den explicit
angivna privata nyckeln får användas och filen strömmas under ett hårt
20 MB-tak. Inga credentials finns i repositoryt.

Schema 029 kräver dessutom ett separat, append-only
referensdata-entitlement för varje ny alias-snapshot. Beviset binder
avtalsnyckel, produkt, XSTO, intern paperanvändning, rå- och
härledd lagringsrätt, giltighetstid, granskningstidpunkter samt exakt
algoritm och SHA-256-fingerprint för den oberoende verifierade
SFTP-värdnyckeln. Ingen entitlement seedas. Saknat, utgånget eller
återkallat bevis stoppar hämtning före nätanslutning, gör befintliga
alias inaktiva och visas som `REFERENCE_ENTITLEMENT_NOT_READY`.

Schema 030 lägger till operatörsattribuerad, append-only återkallelse.
Godkännanden skapas med `python -m src.reference_entitlement_admin`
från en strikt JSON-fil och den exakt granskade avtalsfilen. Verktyget
accepterar inte privata nycklar eller credentials, kräver fyra
uttryckliga användnings- och lagringsbekräftelser och beräknar
avtalsfilens SHA-256 lokalt. Status och återkallelse görs med samma
verktyg; manuell SQL behövs inte. Exakta kommandon finns i
[drift- och releasehandboken](docs/operations.md#nasdaq-referensfil-via-sftp).

Schema 031 inför ett strikt operatörsflöde för avtalsbaserade pris- och
indexleverantörer. Det flödet finns kvar för betalda produkter och
benchmarkdata.

Schema 033 inför en separat, fail-closed auktorisering för Nasdaqs
publika, cirka 15 minuter fördröjda pre-trade-data. `market-sync`
läser den officiella JSON-katalogen varje minut under öppen
XSTO-session, strömmar en CSV till en låst temporär fil, behåller
endast `XSTO` CLOB, lagrar härledd checksummebunden orderboksevidens
och tar därefter bort råfilen. ISIN används som intern identitet;
systemet hittar inte på tickeralias som den publika filen saknar.

En fil får bara aktivera feeden när leveransen observeras 15–20 minuter
efter filens marknadsminut. Gamla backfills kan parserprovas men får
inte märkas färska. Paperköp använder senaste ask och papersälj senaste
bid från exakt förseglad tvåsidig bok. Den publika feeden kräver inte
fem avtalsbaserade acceptanssessioner, men minst en verklig fil måste
valideras under öppen marknad innan handel blir beredd. Se
[ADR-003](docs/decisions/ADR-003-nasdaq-public-delayed-data.md).

Referensregistret kan synkas separat utan att prisimporten aktiveras:

```bash
export DB_PASSWORD='use-a-strong-local-password'
docker compose --profile market-data up -d universe-sync
docker compose logs -f universe-sync
```

`universe-sync` är idempotent, kontrollerar snapshotens ålder och vägrar
orimliga krympningar. `calendar-sync` körs automatiskt före agenten.

Efter skriftligt godkänd entitlement kan den separata
`nasdaq-reference`-profilen provas med en enda fil. Nyckelfilens och
`known_hosts`-filens absoluta sökvägar mountas read-only. Host key ska
verifieras mot Nasdaq genom en oberoende kanal; den hämtas aldrig
automatiskt vid anslutning.

```bash
export ENABLE_NASDAQ_REFERENCE_SYNC=true
export NASDAQ_REFERENCE_CONTRACT_KEY='avtalsnyckel-från-godkänt-bevis'
export NASDAQ_NDL_USERNAME='konto-adress-från-nasdaq'
export NASDAQ_NDL_PRIVATE_KEY_HOST_FILE='/absolut/sökväg/ndl_key'
export NASDAQ_NDL_KNOWN_HOSTS_HOST_FILE='/absolut/sökväg/known_hosts'
export NASDAQ_NDL_RUN_AS_UID='numeriskt-filägar-id'
export NASDAQ_NDL_RUN_AS_GID='numeriskt-filgrupps-id'

docker compose --profile nasdaq-reference run --rm \
  nasdaq-alias-sync python -m src.nasdaq_alias_sync once
```

Den angivna avtalsnyckeln måste redan finnas som ett `VALIDATED`
schema-30-bevis i databasen. Nyckelfilen måste vara en vanlig,
icke-symlinkad fil utan grupp- eller övrig åtkomst. `known_hosts` måste
innehålla exakt den värdnyckel som beviset anger. Tjänsten försöker inte
ansluta före 06:45
`Europe/Stockholm`, använder senaste öppna/halvöppna XSTO-session i den
officiella kalendern och stoppar hela importen om alla förväntade alias
inte matchar.

Se [marknadsdataplanen](docs/market-data-plan.md) innan en leverantör
eller ett abonnemang väljs.

## Modell och driftstatus

Modellbackend väljs explicit med `LLM_BACKEND`. Den
OpenAI-kompatibla vägen använder `OLLAMA_URL`; Anthropic-vägen kräver
en separat nyckel och ett explicit `LLM_MODEL`. Det finns ingen tyst
fallback. `trading-readiness` verifierar att exakt konfigurerad modell
faktiskt finns hos vald endpoint.

Operativ hälsa är uppdelad i vanlig `readiness` och striktare
`trading-readiness`. Den senare ska fortsätta vara röd tills
instrumentalias, provideravtal, synk och full quote-coverage är
verifierade. Se [drift- och releasehandboken](docs/operations.md).

Dashboardens `/api/operations` och vyn **Handelsberedskap** visar samma
fail-closed-läge: schema, strategi, universum och alias, provideravtal,
session, quote-färskhet, synk/gaps, positions- och sektorgränser samt
forward-benchmark. Ett förväntat driftblock är `200 BLOCKED` med
maskinläsbara orsaker; ett verkligt databasfel är fortfarande `503`.

## Release och rollback

Grön CI producerar två provenance-attesterade images och ett gemensamt
release-manifest med exakta SHA-256-digestar. Produktion är ett manuellt
GitHub Environment-steg med känd SSH-hostnyckel och utan `latest` eller
Watchtower. Servern flyttar releasepekaren först efter smoke-test och
försöker kompatibel image-rollback vid fel; databasen rullas aldrig
bakåt. Samma digest-pinnade release startar även `monitor`,
`universe-sync` och `market-sync`; prisimporten förblir fail-closed
tills feature-flagga, ISIN-mappning, policy och filacceptans är
validerade.
Deployen tolkar inte `runtime.env` som shellkod: en separat strikt
parser tolkar literalflaggorna för publik pre-trade, avtalsbaserad
delayed-data respektive referensdatasynk och en separat parser validerar endast absoluta
`*_FILE`-sökvägar till låsta runtime-secrets. Inline-lösenord, tokens
och API-nycklar i `runtime.env` avvisas. Secret-värdena monteras som
`0400` under `/run/secrets` och exponeras inte som tjänsternas vanliga
miljövariabler. Avstängda profiltjänster stoppas och `monitor` körs
alltid.

Pipeline, manifest, icke-root-konfiguration och simulerad rollback är
verifierade. Releasemanifestet kräver schema 41. Schema 41, agent,
dashboard, grafarbetarna och den separata market-sync-tjänsten är driftsatta och
smoketestade på staging; automatisk rollback är testad i den isolerade
releaseharnessen.

## Strategiversioner och lärloop

Den aktiva strategin ligger i PostgreSQL som en fullständig, validerad
konfiguration med versionsnamn och SHA-256-hash. Samma version styr
modellprompt, positionsgränser och deterministiska exits. AI-beslut och
affärer märks med strategiversionen.

En strategiändring kräver alltid:

1. en ändringspatch mot den aktiva versionen;
2. minst en aktiv, identifierad lärdom som evidens;
3. explicit operatörsgodkännande till en ny, ännu inaktiv version;
4. en separat operatörsåtgärd för atomisk aktivering.

Studenten kan lagra observationer men kan inte godkänna eller aktivera
regler. Den gamla strategin pensioneras först när den nya godkända
versionen aktiveras. Status och förslag kan granskas i den körande
agentcontainern:

```bash
docker compose exec agent python -m src.strategy_admin status
```

Statusutskriften visar både aktiv strategiversion och de aktiva,
renderbara lärdoms-ID:n som kan användas i ett nytt förslag.

Exempel på det separata flödet:

```bash
docker compose exec agent python -m src.strategy_admin propose \
  --patch-json '{"min_confidence":60}' \
  --learning 7 \
  --rationale 'Höj tröskeln enligt validerad lärdom 7.' \
  --proposed-by 'operator:diffen'

docker compose exec agent python -m src.strategy_admin approve 1 \
  --version momentum-report-swing-v2 \
  --reviewed-by 'operator:diffen'

docker compose exec agent python -m src.strategy_admin activate \
  momentum-report-swing-v2 \
  --activated-by 'operator:diffen'
```

## Nödstopp och daglig förlustgräns

Papertrading har en beständig operatörskontroll för nya köp. Det
manuella nödstoppet och en dagslåst mark-to-market-förlustgräns stoppar
`BUY`, medan `SELL` fortsatt är tillåtet för att minska risk. Status och
append-only audit visas även i dashboardens driftvy.

```bash
docker compose exec agent python -m src.risk_admin status

docker compose exec agent python -m src.risk_admin halt \
  --reason 'Dataincident under utredning' \
  --operator 'operator:diffen'

docker compose exec agent python -m src.risk_admin resume \
  --reason 'Incident verifierad och stängd' \
  --operator 'operator:diffen'
```

Standardgränsen är 3 procent per Stockholmsdatum och kan ändras med
`src.risk_admin set-limit`. Se [driftdokumentet](docs/operations.md)
för exakta regler och kommandon.

## Auditkedja för paperhandel

Varje AI-initierad affär måste länka till det exakt sparade
AI-beslutet och samma aktiva strategiversion. Databasen avvisar en
AI-affär utan föräldrabeslut eller med fel strategiversion. För
providerbaserade priser går kedjan vidare från affären till exakt
quote, event-/mottagningstid och råfilens SHA-256-checksumma.

Dashboardens Affärsjournal visar motivering, ursprung, beslut-ID,
strategiversion, quote-ID, quote-tid, råfilschecksumma och realiserat
ledgerutfall. Automatiska scanners, mekaniska exits och manuella
paperaffärer märks separat och får inte utge sig för att vara
AI-initierade.

## Backtesting

Det gamla student-backtestet är spärrat eftersom det använde dagens
bolagslista, samma dags stängningspris och felaktigt summerade
tradeprocent. Ersättaren är en point-in-time walk-forward-motor med
historiska medlemsperioder, next-open-exekvering, explicit data-
tillgänglighet, split/utdelning/avnotering, spread, avgift, slippage,
likviditet, benchmark och reproducerbar kör-checksumma.

Motorn är verifierad på syntetiska data och end-to-end mot PostgreSQL.
Den får inte producera ett godkänt verkligt resultat förrän ett
licensierat historiskt XSTO-dataset har importerats och
operatörsvaliderats. Se [backtestkontraktet](docs/backtesting.md).

## Nuvarande säkerhetsnivå

På plats:

- versionsstyrd, idempotent runtime-migrering;
- constraints mot negativa affärer och dubbla portföljrader;
- atomiska köp/sälj med radlås, översäljningsskydd och realiserad P&L;
- exakt-en-gång-bokföring med idempotensnyckel och payload-fingerprint;
- databasverifierad auditkedja från råfil/quote via sparat AI-beslut
  till paperorder och ledgerutfall;
- generativa egenskapstester för dagsförlustgate, exitprisrum,
  beslutnormalisering och FIFO-ledgerns kontant-, lots-, positions- och
  P&L-invarianter;
- FIFO-lots och explicita allokeringar för partiella försäljningar;
- migrerade legacy-positioner bevaras som märkta
  `MIGRATED_AVERAGE_COST`-lots i stället för gissad historik;
- strict validering av AI-svar, ticker, confidence och positionsstorlek;
- versionsstyrd och hashverifierad strategi- och riskkonfiguration;
- evidenslänkade strategiändringar med separata steg för godkännande
  och aktivering;
- fail-closed point-in-time walk-forward-motor med nästa öppning som
  tidigaste exekvering;
- historiskt universum, corporate actions, kostnader, likviditet,
  benchmark och reproducerbar run-audit i backtestkontraktet;
- basstrategins hårda maxgräns på fem positioner, två per sektor och
  25 procent per position;
- fungerande stop-loss, take-profit, trailing stop och tidsstopp;
- fail-closed vid ogiltig AI-JSON;
- fail-closed vid saknad/stängd XSTO-session eller för gammal quote;
- fail-closed leverantörsgate för licens, transport, universum,
  tickeralias och observerad leveranstid;
- providerauktoriserad portföljvärdering utan fallback till dagskurser,
  med exakt quote-ID per sparad positionsmarkering;
- samlad Handelsberedskap-vy för datakälla, freshness, risk, sektor,
  benchmark och aktiva blockerare;
- fail-closed dashboard-auth för alla sidor och data-API:er;
- minimal publik readiness-route, säkerhetsheaders och lokal portbindning;
- databasfel returnerar 503 och visas aldrig som fabricerade nollvärden;
- Next.js 15.5.21/React 19 med deterministisk `npm ci` och ren audit;
- instrument-, quote-, bar- och kalendergrund med provider-interface;
- automatisk, checksummeverifierad ESMA FIRDS-import för XSTO;
- atomiska, historiserade referenssnapshots och råfilsarkiv;
- officiellt verifierad XSTO-kalender 2024–2026 med helg- och halvdagar;
- strikt parser/provider för Nasdaq Nordic public delayed pre-trade;
- omstartssäker append-only-lagring av pre-trade-batcher,
  siduppdateringar, cursor och bästa bid/ask med exakt filproveniens;
- atomiskt gzip-rådataarkiv med SHA-256, synkaudit och gap tracking;
- opt-in minutpollning som endast kör under explicit öppen XSTO-session;
- DST-säkert körschema i `Europe/Stockholm`, där handel och studentstudier
  använder samma officiella XSTO-kalender;
- ett enda tidszonsmedvetet beslutsögonblick per rutin samt återförsök av
  schemalagda jobb som misslyckas inom sin grace-period;
- append-only körresultat och deduplicerade driftlarm för schema,
  ledger, stale marknadsdata, missade rutiner och kritiska
  benchmarkincidenter i en separat monitorprocess;
- en dashboardvy som visar aktuella driftlarm och senaste utfall per
  schemarutin; öppna `PAGE`-larm blockerar handel medan `TICKET` visas
  utan att ensamt stoppa den;
- valfri Telegramleverans för nya sidlarm; beständigt larmtillstånd och
  maskinläsbar JSON finns kvar även utan notifieringsnycklar;
- riktiga tester i CI;
- maskinläsbar readiness och separat trading-readiness;
- explicit, verifierbart modellbackendval utan tyst fallback;
- digest-pinnad, manuellt godkänd releasepipeline med provenance;
- schema-gated automatisk image-rollback och oföränderligt
  release-manifest.

Inte klart:

- Nasdaqs handelssymboler/tickeralias för de 416 FIRDS-instrumenten;
- licensierad och driftstabil intradagskälla;
- dokumenterat serverstödd och licensierad transport till prisfeeden;
- koppling av verifierad Level 1-bid/ask till det förregistrerade
  forward-benchmarkets frysta exekveringskostnader;
- automatisk kalenderuppdatering för 2027 och senare;
- fleranvändarautentisering, roller och sessioner om produkten öppnas
  för fler än en intern operatör;
- licensierad fullhistorik för att köra den verifierade backtestmotorn
  på verkliga XSTO-data;
- auktoriserade, proveniensbundna källor för nyheter, rapportkalender,
  fundamenta och övrig makro; de gamla offentliga webbskraporna är
  borttagna, legacy-rader används inte av AI:n och studentens externa
  webbsökning är fail-closed blockerad;
- ett operatörsgodkänt benchmarkresultat från den historiken;
- verklig staging-/produktionsdeploy och rollbackövning på avsedd server;
- verklig stagingverifiering av monitorleverans och mottagare;
- lång benchmarkperiod som motiverar någon eventuell brokerkoppling.

## Stack

- Python
- PostgreSQL 16
- Next.js
- Docker Compose
- lokal OpenAI-kompatibel modell eller Anthropic, beroende på konfiguration

## Status

Aktiv upprustning. Systemet ska betraktas som utvecklingsmiljö och
papertrading, inte som investeringsrådgivning eller produktionsklar
automatisk handel.
