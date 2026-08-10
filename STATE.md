# State

## 2026-08-05 — grafminne i kontrollerat skuggläge

- Staging kör schema 41 med både `knowledge-worker` och den separata
  `knowledge-shadow-worker` var femte minut.
- Neo4j på Sjöboden nås från Kajen över Tailscale/Bolt och använder en
  BWS-injicerad Trader-secret; inget lösenord finns i repositoryt eller
  `.env`.
- Skuggarbetaren återspelar varje lämpligt historiskt beslut med samma
  GPT-5.6-konfiguration två gånger: en kontroll utan grafminne och en analys
  med enbart tidskorrekta, strukturerade utfallsaggregat från Neo4j.
- Första liveverifieringen gav två lyckade grafjämförelser med totalt 100
  jämförda aktiebeslut och en ändrad handling. Resultaten är append-only och
  dashboarden visar både totalsiffror och senaste körningsstatus.
- Grafminnet påverkar ännu inte operativa beslut eller paperorders.
  Aktivering kräver tillräckligt många senare, oberoende utfall och en
  uttryckligen godkänd policyändring.
- Objektlagring är inte en blockerare för den strukturerade skuggloopen. Den
  behövs innan större råarkiv för nyheter, rapporter, candles och licensierade
  marknadsfiler ska behållas och återspelas.

## Current phase

Aktiv, autentiserad papertrading på staging med Nasdaqs publika
15-minutersfördröjda XSTO pre-trade-data. Riktiga pengar är fortsatt
blockerade.

## Senast verifierat 2026-08-03

- `https://trader.lediff.online` kör schema 35. Publik health svarar,
  medan dashboard och data-API kräver autentisering.
- Dashboardens autentiserade API visar `READY`, inga blockers och inga
  aktiva driftlarm.
- `market-sync` importerar en sammanhängande Nasdaq-minutström med
  start-till-start-kadens och kontrakt
  `nasdaq-public-pretrade-xsto-v2`.
- Driftmonitorn kör på en fast väggklockefas vid sekund 30 och den
  operativa färskheten följer leverantörskontraktets transportfönster.
  Tre raka minutcykler verifierades `HEALTHY`, inklusive det tidigare
  falska `NOT_READY`-fönstret strax efter minutskiftet.
- Den lokala modellen `qwen2.5-coder:14b` svarar via en privat
  Tailscale-adress. Agenten har sparat nio AI-beslut.
- Två spårbara paperköp har genomförts från exakta
  `PRE_TRADE_BOOK`-states: Anoto Group AB för 5 000,00 kr och Intrum AB
  för 1 989,68 kr.
- Portföljen har 13 010,32 kr i kassa. Senaste persistenta snapshot
  värderar två positioner med två exakta orderboksmarkeringar.
- Schema 35 gör portföljvärdering proveniensbunden till antingen
  `market_quotes` eller `pre_trade_book_states`; den misslyckade
  öppningsrutinen har körts om och både `open` och `midday` har
  append-only `SUCCEEDED`-evidens.
- 565 agenttester och dashboardens 53 testfall är gröna i sin
  avsedda miljö: 48 körda testfall passerar och fem
  databas-integrationstest hoppas över utan en lokal testdatabas.
  Dashboardens produktionsbygge går igenom.
- Agent, monitor, dashboard och market-sync är healthy. Agent och
  monitor kör `staging-20260803-readiness-window-v11-amd64`;
  dashboarden kör
  `staging-20260803-analysis-names-v6-amd64` med digest
  `sha256:51da2934789d6865f4e7e682ce0443db92089e9746d4453cc5e97abe93d927fe`.
- Positionskorten visar bolagsnamnet som rubrik. ISIN eller ticker
  visas endast som sekundär identifierare.
- Dashboarden är besökarprioriterad: aktuellt portföljresultat och
  senaste människoläsbara handling visas först, följt av resultat,
  positioner, affärsjournal och lärandeloop. Datakällor, risk,
  driftstatus och instrumentuniversum finns kvar i en stängd
  `Teknik och transparens`-sektion.
- Affärs-API:t kopplar papertrades till bolagsregistret. Både senaste
  handlingen och journalen visar därför `Intrum AB`/`Anoto Group AB`
  som primär text och behåller ISIN/ticker som sekundär
  revisionsidentifierare.
- Analys-API:t kopplar även varje identifierare i äldre AI-beslut till
  bolagsregistret. Analyskorten visar svenska handlingar och
  människoläsbara namn som `MedCap AB`, `Episurf Medical AB B`,
  `Intrum AB` och `Transtema Group AB`; ett omappat ISIN visas aldrig
  som bolagsnamn.
- OMXSGI-jämförelsen visar uttryckligen att verifierad data inväntas
  när benchmarkutfallet saknas; dashboarden räknar inte fram ett
  fabricerat jämförelsetal.
- Inga lärdomar har ännu skapats eftersom inga affärer har stängts och
  utvärderats. Ingen brokerkoppling eller handel med riktiga pengar
  finns.

## Senast verifierat 2026-07-30

- Runtime-schema kan migreras från repositoryts legacy-baseline.
- Migreringen är idempotent och den destruktiva seed-filen spelas inte om.
- 536 agenttester går igenom utan skips, varav 93 är
  PostgreSQL-integrationstester.
- 32 dashboard-/runtime-tester går igenom utan skips.
- Migrering 006–031 går igenom från helt tom databas med slutversion
  31; alla 30 registrerade schemaversioner finns exakt en gång.
  En andra körning är idempotent. Uppgradering 24→25 är
  provad med ett historiskt ändrat instrument och 25→26 med en
  befintlig legacy-förregistrering. En separat 26→27-uppgradering är
  också körd utan att skriva om legacy-förregistreringens hash.
  Uppgradering 29→30 är provad med ett äldre återkallat
  referensdata-entitlement och bevarar posten med tydlig
  migrationsattribution.
- Ledgern stoppar översäljning och loggar realiserad P&L atomiskt.
- Hypotesutvärdering använder den första auktoriserade officiella
  XSTO-sessionen efter den förregistrerade horisonten och får aldrig
  skriva över ledgerns P&L.
- Strategianalysen räknar endast helt stängda köp med realiserat
  nettoresultat; öppna köp och separata säljrader förvränger inte
  lärdomarna. Strategi-status listar användbara lärdoms-ID:n och
  orenderbar evidens stoppas före lagring eller länkning.
- Veckogranskningen använder realiseringsdatum och räknar samma
  FIFO-resultat exakt en gång via den stängda köpaffären.
- AI-beslut och exitregler valideras deterministiskt.
- Köp kräver en teknisk signal från aktuell XSTO-session. Minsta
  innehavstid för modellstyrd sälj mäts mot senaste öppna köpaffär med
  samma injicerade UTC-klocka som resten av beslutsgaten.
- Instrument-, intraday- och marknadskalenderkontrakt finns i schema och kod.
- Nasdaqs produktionsversion TIP 3.10.17.1 och den aktuella
  Nasdaq Data Link Files-sökvägen är kontraktstestade. `BDt` binds till
  `BDSh`, exakt `PMIc=XSTO`, XSTO-exchange, ISIN, valuta och CFI.
  Filen måste ge exakt full täckning mot en fryst FIRDS-snapshot;
  saknade, extra eller dubbla ISIN/tickers avvisar hela filen.
- Schema 28 arkiverar den officiella referensfilens råbytes och
  checksumma, fryser varje ISIN–ticker-rad append-only och aktiverar
  snapshoten atomiskt. Exakt omspelning är idempotent, ändrat innehåll
  för samma affärsdatum stoppas och dashboarden räknar alias endast
  när de pekar på exakt senaste frysta FIRDS-universum.
- Den separata Nasdaq Data Link Files/SFTP-adaptern är implementerad
  bakom en avstängd Docker-profil. Den låser värd/port, kräver
  provisionerad host key och key-only-auth, stänger av agent och
  standardnycklar, begränsar timeouter/råfil till 30 sekunder/20 MB
  och väljer senaste officiella XSTO-session efter 06:45
  `Europe/Stockholm`. Verklig anslutning är inte gjord.
- Schema 29 kräver ett append-only entitlement-bevis före varje ny
  Nasdaq-aliasimport. Beviset binder avtalsnyckel, intern paperanvändning,
  rå- och härledd lagringsrätt, giltighet, juridisk/teknisk granskning
  och exakt SFTP-host-key-fingerprint. Saknat, framtidsdaterat, utgånget
  eller återkallat bevis stoppar nätanslutning och gör alias operativt
  oanvändbara. Ingen validerad entitlement seedas.
- Schema 30 lägger till säker operatörsadministration utan manuell SQL:
  en strikt JSON-manifest och den exakta granskade avtalsfilen valideras
  lokalt, SHA-256 beräknas över avtalsbytes, fast intern paperanvändning
  och båda lagringsrätterna måste bekräftas explicit och credentials
  accepteras inte. Återkallelse lagrar tid, operatör och orsak
  append-only och stoppar omedelbart vidare användning.
- Schema 31 lägger till ett separat operatörsflöde för pris- och
  indexprovider utan manuell SQL. Utkastet sparar skapande operatör;
  valideringen hashar exakt avtalsfil och acceptansfil, kräver rå och
  härledd lagringsrätt, full coverage/symbolmappning och fem
  sammanhängande officiella XSTO-sessioner med verifierad transport,
  korrigering, omstart, gap recovery och kill switch. Aktieprodukter
  binds till exakt senaste frysta XSTO-snapshot, medan `OMXSGI`
  uttryckligen saknar falsk aktiesnapshot. Evidens och återkallelser är
  append-only och runtime, pre-trade och dashboard stoppar legacy- eller
  ofullständiga bevis.
- Schema 33 auktoriserar Nasdaqs publika, 15 minuter fördröjda
  pre-trade-produkt separat från avtalsbaserade providers. Verifierad
  policy, kontrakt och filvalideringar lagras append-only.
- Den officiella pre-trade-katalogen och senaste öppna XSTO-sessionens
  fil hämtas av `market-sync` via fast Nasdaq-host, fasta API-sökvägar,
  avstängda redirects, storleksgräns och hårda timeouter. Råfilen är
  temporär med läge `0600` och tas bort efter parsning.
- En riktig Nasdaq pre-trade-fil från 2026-07-29 är formatverifierad:
  cirka 165 MB och 1 389 267 rader för en nordisk minut, varav
  8 479 ordnade `XSTO`-siduppdateringar för 337 ISIN.
- En strikt strömparser bevarar `BUY`/`SELL`, källsekvens,
  event-/publication time, handelsfas och explicita sidoraderingar utan
  att läsa hela filen i minnet. En separat Level 1-reducerare bygger
  bid/ask från parserutfärdade minutbatcher och stoppar vid fel ordning,
  transient eller slutlig korsad bok, osäker täckning, mottagningstids-
  regression eller mer än en minuts gap. Filminuten avvisar tvetydig
  eller obefintlig lokal sommartid; levande nivåer kräver positiv
  kvantitet och positivt orderantal.
- Varje batch bär det exakta XSTO-ISIN-universum och den
  kanoniskt beräknade referenssnapshot-checksumma som parsern använde.
  Snapshoten accepterar endast aktiva primärnoterade stamaktier,
  preferensaktier och depåbevis på exakt MIC. Återinläst bokstatus
  måste tillhöra nästa batchs universum. Verifierad konstruktion sker
  inne i providerparsern utan en generell publik batchfabrik.
- En separat stream-cursor bär coverage, mottagningstid och
  referenschecksumma även när en giltig minut ger noll
  XSTO-uppdateringar. En tom bootstrap kan därför inte dölja en senare
  minutlucka eller mottagningstidsregression.
- Råindata måste bestå av exakt en fysisk rad per bytes-chunk; inbäddade
  CR/LF, tomma fysiska rader och försök att kringgå radtaket avvisas.
- Källfilens SHA-256 beräknas över mottagna råbytes och skyddar lokal
  innehållsintegritet. Den är inte en Nasdaq-signatur och bevisar inte
  ursprung utan den ännu ej avtalade transportens TLS-/manifestbevis.
- Schema 25 lagrar pre-trade-batch, ordnade siduppdateringar,
  stream-cursor och materialiserad bid/ask append-only. Exakt omspelning
  är idempotent, ändrat payload stoppas och restart återläser även en
  tom XSTO-minuts coverage.
- En batch får bara följa exakt föregående förseglade minut och måste
  använda senaste provider-validering. Befintliga snapshotmedlemskap
  från före schema 25 märks `LEGACY_UNVERIFIED` och kan inte användas
  som historiskt pre-trade-bevis.
- Ett pre-trade-paperfill måste använda senaste förseglade tvåsidiga
  CLOB/COTR-state, exekverbar ask/bid, öppen XSTO-session och
  återstående visad sidvolym. Oförändrad sida kan inte återkonsumeras
  genom att bara bära samma nivå till nästa minut.
- Schema 26 fryser exekveringspriskälla och exekveringsprovider separat
  från värderings- och OMXSGI-provider. Ett körande
  `TOP_OF_BOOK_PLUS_SLIPPAGE`-experiment binds till exakt samma
  provideravtal och referenssnapshot som den senaste förseglade
  orderboken.
- Level 1-köp utgår från ask och sälj från bid. Faktisk
  midpoint-till-sida-spread bokförs från boken, syntetisk
  `spread_bps` måste vara noll och endast fryst slippage läggs ovanpå
  exekverbar sida. Databastriggern verifierar samma uträkning vid
  direkta SQL-insättningar.
- Schema 27 kräver att benchmarkens startnivå pekar på exakt
  append-only `market_index_levels`-evidens från det frysta
  OMXSGI-avtalet. Utkastets hela förregistrering är immutabel redan
  från skapandet; en ändring kräver en ny registrering. Experiment får
  bara skapas som `DRAFT`; en direkt SQL-insättning som `APPROVED` eller
  `RUNNING` stoppas.
- Uppgradering 26→27 matchar historiska startfält mot exakt indexrad.
  Migrationen avbryts och rullas tillbaka om ett redan startat
  experiment saknar en unik träff, i stället för att lämna en osäker
  körning aktiv under det nya schemat.
- Refererade provideravtals identitet och referenssnapshot är
  immutabla. Ett provideravtal får slutföra den avsedda
  `DRAFT`→`VALIDATED`-övergången och därefter endast återkallas; det
  får inte återaktiveras eller få villkor, produkt eller giltighet
  omskrivna efter bindning.
- Last-trade-paperfills kräver en quote vars arkiverade fil är bunden
  till exakt exekveringsavtal och vars instrument ingår i experimentets
  frysta referenssnapshot. Benchmarkaffärernas exekveringsfält kan
  varken ändras eller raderas efter insättning.
- Samma exakta fil-till-avtalsbindning gäller alla operativa
  pris-, bar-, readiness-, portfölj- och dashboardfrågor. Ett
  leverantörsliknande `source`-namn utan rätt arkiverad fil och
  `provider_contract_id` kan inte auktorisera en quote.
- Filbundna quotes är själva append-only; last/bid/ask, volym, valuta,
  råpayload, tidsstämplar och källfil kan inte ändras eller raderas i
  efterhand.
- Den publika pre-trade-vägen är kopplad till Nasdaqs officiella
  HTTP-transport och har ett verifierat policykontrakt. Betalda
  provideravtal och benchmarkdata har fortsatt separata grindar.
- Råfiler, checksummor, quotes, synkaudit och gaps lagras atomiskt och
  omstartssäkert via migration 008.
- Gap stängs först när varje saknad rapportminut har backfillats.
- En separat `market-sync`-profil är opt-in och pollar endast under en
  explicit öppen XSTO-session.
- `market-sync` kräver ett aktuellt auktoriserat provider-kontrakt med
  rätt produkt/MIC/transport/användningsfall och aktuell
  leveransmätning. Den publika vägen använder ISIN och kräver en färsk
  exekverbar bok; den avtalsbaserade vägen behåller fullcoverage- och
  tickerkraven.
- Schema 15 förregistrerar forward-paper mot OMXSGI med minst 252
  sessioner, 30 stängda affärer, positiv netto- och överavkastning,
  drawdown högst 15 procent, minst 99,5 procents datatäckning och noll
  kritiska incidenter.
- Preregistreringshashen binder strategihash, release-manifest,
  agent-image-digest, modellevidens, universum och provideravtal till
  samma kanoniska JSON som PostgreSQL verifierar.
- Providerroller och instrumentantal kontrolleras vid godkännande,
  start och återupptagning. OMXSGI-startnivån kräver dessutom exakt
  nivå-ID, provideravtal, symbol, MIC, händelsetid,
  tillgänglighetstid och källchecksumma.
- Paperfills använder den frysta avgifts-, spread- och
  slippagemodellen för exekveringspris, kassaflöde och netto-FIFO-P&L.
  Stängda positioner hämtas från ledgern och kritiska incidenter från
  en separat append-only incidentlogg.
- Varje benchmark-affär binds till ett exakt `market_quotes`-ID och
  stoppas vid fel aktie, provider eller pris, för gammal eller framtida
  offert samt exekvering utanför en öppen XSTO-session.
- Agentens kontovärde, positionsexits, AI-beslut, dagssnapshots och
  dashboardvärden använder nu endast quotes från ett aktuellt,
  fullvaliderat provideravtal. Ett saknat pris gör totalvärdet
  uttryckligen ovärderat; legacy-tabellen `prices`, `current_price` och
  `avg_price` används aldrig som fallback.
- Schema 16 lagrar varje snapshotposition med ticker, antal, pris,
  marknadsvärde och exakt `market_quotes`-ID i
  `portfolio_valuation_marks`.
- Schema 17 lagrar append-only OMXSGI-nivåer med exakt provideravtal,
  checksumma och freshness-gate mot föregående officiella XSTO-session.
- Schema 18 fryser snapshotens exakta instrumentmedlemskap. Schema 19
  binder varje filinläst quote till exakt arkiverad råfil. Schema 20
  binder portföljsnapshots till aktivt forward-experiment.
- Schema 21 härleder den dagliga post-close-observationen automatiskt
  från portföljsnapshot, ledger, fryst universum, källfilsbundna quotes
  och exakt OMXSGI-stängningsnivå. Körningen är idempotent och stöder
  både ordinarie XSTO-sessioner och officiella halvdagar.
- Daglig quote-täckning måste motsvara exakt det frysta universet,
  kostnader måste stämma med ledgern och drawdown använder sessionens
  högsta och lägsta NAV.
- Backtestet återköper inte på avnoteringsdagen, använder inte nästa
  sessions universum för att sälja vid dagens stängning och bevarar
  drawdown över första sessionen och mellan walk-forward-folds.
- Godkänd registrering, dagliga observationer, livscykelhändelser och
  slututvärdering är append-only. Ett pass tillåter endast manuell
  brokergranskning, aldrig riktiga pengar automatiskt.
- Analys och handel stoppas om XSTO-sessionen saknas eller priset är för gammalt.
- Positionsexits, prisfärskhet, hypotesvalidering och veckogranskning delar
  ett injicerat tidszonsmedvetet UTC-ögonblick per rutin.
- Studentstudier använder den officiella XSTO-sessionen och blockeras
  fail-closed om kalendern inte kan läsas.
- Europe/Stockholm-schemat hanterar sommar- och vintertid. Ett misslyckat
  schemalagt jobb markeras inte som färdigt utan kan återförsökas inom sin
  20-minuters grace-period.
- Schema 24 lagrar lyckade och misslyckade schemarutiner samt
  deduplicerade `OPEN`/`RESOLVED`-larm append-only. En separat
  monitorprocess larmar på schema, ledger, stale data efter
  15+10-minutersfönstret, missade rutiner, kritiska
  benchmarkincidenter och fel i monitorns egen evidensväg.
- Dashboardens handelsberedskap visar endast senaste tillståndet per
  driftlarm och senaste utfallet per schemarutin. Öppna `PAGE`-larm
  ingår i handelsblockerarna; `TICKET` är synliga utan att ensamt
  blockera.
- Telegramleverans är valfri. Larmtillstånd och en maskinläsbar JSON-rad
  finns kvar utan notifieringsnycklar, och råa exceptions ingår inte i
  larmtexten.
- Docker Compose-konfigurationen är giltig.
- CI kör riktig PostgreSQL och maskerar inte längre testfel.
- Image-build är gated på lyckad CI för push till `main`.
- Agent- och dashboard-images byggs i samma releasejobb, provenance-
  attesteras och fryses med exakta digestar i ett strikt manifest.
- Produktionsdeploy kräver manuellt godkännande i GitHub Environment,
  känd SSH-hostnyckel och exakt build-run/commit; `latest` och
  Watchtower används inte. Releasen startar även de digest-pinnade
  `universe-sync`- och `market-sync`-tjänsterna.
- Dockerfilerna kör agent och dashboard som icke-root (`agent`
  UID/GID 10001 respektive `node` UID/GID 1000). Schema 33-imager för
  `linux/amd64` är byggda, testade och driftsatta på staging.
- `readiness` verifierar schema, ledger, aktiv strategi och kalender.
  `trading-readiness` kräver dessutom komplett universum, provider,
  synk, gapfri data, öppen session och exakt konfigurerad AI-modell.
  Publik pre-trade kräver minst en färsk exekverbar tvåsidig CLOB-bok;
  den licensierade last-trade-vägen kräver full quote-coverage och
  auktoriserad OMXSGI-signal.
- Modellbackend väljs explicit mellan `openai-compatible` och
  `anthropic`; saknad modell/nyckel, ogiltig URL eller okänd backend
  avvisas utan tyst fallback.
- Automatisk image-rollback är schema-gated och verifierad mot en
  simulerad Docker-värd. Releasepekaren flyttas först efter godkänd
  agent- och dashboard-smoke.
- Officiella ESMA FIRDS-fullfiler hämtas, checksummeverifieras, parsas
  strömmande, råarkiveras och synkas atomiskt.
- ESMA-manifestets `as_of` beräknas i `Europe/Stockholm`, så synken
  ligger inte en dag efter under svensk midnatt när UTC-datumet ännu
  är föregående dag.
- FIRDS-snapshoten 2026-07-25 gav 416 XSTO-aktieinstrument:
  406 stamaktier, 6 preferensaktier och 4 depåbevis.
- Identiteten bygger på ISIN; FIRDS innehåller inte Nasdaqs
  handelssymbol. Samtliga 416 instrument har en sanningsenlig
  ISIN-baserad intern bolagskoppling; officiell ticker visas endast när
  separat auktoritativ symboldata finns.
- `market-sync` använder nu Nasdaqs publika 15-minuters pre-trade-CSV,
  filtrerar mot officiell XSTO-session och CLOB och validerar varje fil
  kontinuerligt. OMXSGI, point-in-time-historik och officiella
  tickeralias är separata framtida datakontrakt.
- De gamla webbskraporna för Börskollen, Avanza och DI är borttagna
  tillsammans med sina parserberoenden. Legacy-rader i `news` och
  `report_calendar` når inte AI-kontexten; nyheter och rapportkalender
  förblir avstängda tills en auktoriserad källa med proveniens finns.
  Studentens schemalagda `news_research` och `self_study` är också
  fail-closed blockerade, kan inte få en godtycklig webbsökfunktion
  injicerad och redovisas som blockerade i stället för slutförda.
- XSTO-kalendern 2024–2026 är verifierad mot Nasdaqs officiella
  handelskalender, versionsstyrd och idempotent.
- Kalenderår efter 2026 avvisas fail-closed tills ett nytt officiellt
  underlag har verifierats och versionssatts.
- Referenssynken gick end-to-end två gånger mot isolerad PostgreSQL:
  första körningen importerade 416 instrument och andra körningen gjorde
  noll nedladdningar och noll ändringar.
- Dashboarden kräver runtime-konfigurerad Basic-auth med minst 16 byte
  lösenord och skyddar både sida och data-API.
- Runtime-smoke verifierar 200 för publik minimal health, 401 utan eller
  med fel uppgifter, 200 med rätt testuppgifter samt 503 vid databasfel.
- Dashboarden visar inte längre fabricerad portfölj eller “agent aktiv”
  när API-data inte kan verifieras.
- Dashboarden har en samlad Handelsberedskap-vy och ett
  `/api/operations`-kontrakt för schema, strategi, universum/alias,
  provider/freshness, marknadssession, synk/gaps, risk/sektor och
  forward-benchmark. Förväntade blockerare visas som `200 BLOCKED`;
  databasfel förblir `503`.
- Next.js har uppgraderats från sårbara/EOL 14.2.25 till Maintenance LTS
  15.5.21 med React 19, package-lock, `npm ci` och 0 auditfynd.
- Databas och dashboard binds endast till localhost som standard.
- Varje ny order kräver en idempotensnyckel och ett kanoniskt
  SHA-256-fingerprint. Samma nyckel och payload returnerar ursprungligt
  trade-id utan ny bokning; ändrad payload avvisas.
- Parallella försök med samma ordernyckel serialiseras i PostgreSQL och
  verifierades bokföra exakt en affär.
- Operatörens nödstopp blockerar nya köp atomiskt i ledgern men lämnar
  riskreducerande sälj öppna.
- Den dagliga mark-to-market-förlustgränsen använder föregående
  verifierade snapshot eller dagens första värdering och låser nya köp
  resten av Stockholmsdatumet efter en överträdelse.
- Kontrolländringar och riskutvärderingar är append-only-auditerade;
  dashboard och trading-readiness visar den exakta spärren.
- Hypothesis testar dagsförlustgaten, exitprisrummet,
  beslutnormalisering och FIFO-ledgerns kontanter, lots, positioner och
  realiserade P&L över hundratals genererade fall. CI-profilen är
  deterministisk och testverktygen exkluderas från runtime-imagen.
- Produktionsdeployen tolkar två strikta literalflaggor och validerar
  namngivna absoluta `*_FILE`-sökvägar ur en vanlig, icke-symlinkad och
  ägarlåst `runtime.env`; filen sourcas aldrig. Inline-lösenord, tokens
  och API-nycklar avvisas före Docker. Secret-filer måste ägas av
  deployanvändaren, vara `0400`/`0600`, innehålla exakt en begränsad
  UTF-8-rad och monteras tjänstespecifikt som `0400`.
  `agent`, `dashboard` och `monitor` startas alltid. `market-data` och
  `nasdaq-reference` aktiveras endast av respektive explicita `true`,
  och tidigare profiltjänster stoppas när flaggan stängs av.
- Köp skapar FIFO-lots och sälj skapar explicita lot-allokeringar med
  antal, entry, exit och realiserad P&L.
- Partiell försäljning över två lots verifierades till 300 + 50 kronor
  i P&L, korrekt kvarvarande kostnadsbas och korrekt slutstängning.
- Befintliga pre-011-positioner migreras utan gissad tradehistorik till
  en tydligt märkt `MIGRATED_AVERAGE_COST`-lot.
- Basstrategin `momentum-report-swing-v1` är en fullständig validerad
  konfiguration med verifierad SHA-256-hash.
- Modellprompt, deterministiska entry-/exitgränser, AI-beslut och
  trade-audit använder samma aktiva strategiversion.
- Varje AI-initierad paperorder måste länka till sitt beständiga
  föräldrabeslut. Databasen avvisar saknat beslut, fel strategiversion
  och fel benchmarkexperiment; manuella, scanner- och mekaniska
  affärer har egna ursprung.
- Affärsjournalen visar motivering, ursprung, beslut, strategi,
  exakt quote, råfilschecksumma och realiserat ledgerutfall.
- En strategiändring måste länka aktiva lärdomar, godkännas till en
  separat inaktiv version och därefter aktiveras i ett eget atomiskt
  operatörssteg.
- Student-/modellidentiteter kan inte godkänna strategiändringar och
  studentens insikter blir inaktiva observationer, inte aktiva regler.
- Ett komplett testflöde verifierade förslag, avvisat AI-godkännande,
  operatörsgodkännande, oförändrad aktiv version före aktivering och
  korrekt atomiskt versionsbyte.
- Det osäkra legacy-backtestet är blockerat i studentagenten.
- Point-in-time-motorn exekverar signaler tidigast nästa öppning och
  använder endast bars som då var tillgängliga.
- Historiskt universum, sektor, råa bars, total-return-benchmark,
  riskindex och corporate actions är obligatoriska datasetdelar.
- Splitjusterade signaler, kontantutdelning, konservativ same-bar
  stop/target, trailing stop, tidsstopp, avnotering och universumutträde
  täcks av motorn.
- Avgift, spread, slippage, likviditetsgolv och maximal volymandel är
  explicita och hashade körparametrar.
- Walk-forward-folds får inte överlappa; portfölj- och
  benchmarkavkastning compunderas och drawdown, Sharpe, turnover,
  trades och win rate lagras.
- Ett syntetiskt dataset validerades, checksummefrystes, kördes och
  sparades end-to-end i PostgreSQL. Identisk omkörning återanvände samma
  run i stället för att skapa dubletter.
- Python-koden kompilerar och requirements-baserad `pip-audit` samt
  blockerande `npm audit` är gröna. 556 agenttester och 42
  dashboardtester passerar mot en isolerad schema 33-databas utan skips.
  Next.js-produktionsbygget passerar.
  Dashboard-imagens runtime-smoke är `healthy`
  som användaren `node`, ger 200 på health, 401 utan auth och
  `200 BLOCKED` utanför öppen XSTO-session. Efter datumskiftet
  stängde monitorn automatiskt fredagens två historiska rutinlarm;
  inga aktiva driftlarm återstår.
  Riktig webbläsare visar samtliga åtta data-API:er som 200, noll
  konsolfel/varningar och ingen horisontell overflow vid 390 px.
- Lokala schema 30-images är byggda som icke-root:
  `trading-agent-agent:schema30-secrets` som fast UID/GID 10001 och
  digest
  `sha256:fa437ede4bc2c8a05902328ea0a2b9e073d69af4af42a41c15b1af9d9020b600`,
  samt `trading-agent-dashboard:schema30-secrets` som `node` UID/GID
  1000 och digest
  `sha256:e3dda233e5e6857f6a8d47f1d08c5ae2cb9aef54c526524b003a945fd827494d`.
  En ren produktionslik stack migrerade tom PostgreSQL till schema 30,
  synkade 751 kalenderdagar och blev `HEALTHY` för agent, dashboard
  och monitor. Agentens `readiness` blev `READY`; dashboard-smoke gav
  200/401/200 för health och portfölj utan/med auth.
  Databas-, modell- och dashboarduppgifter monterades som
  ägarspecifika `0400`-filer under `/run/secrets`; avsiktligt
  utelämnade Telegram-filer stängde notifieringen utan startfel.
  Ingen syntetisk secret återfanns i agentens eller dashboardens
  containerkonfiguration. Den isolerade smokestacken och dess volymer
  togs därefter bort.

Alla ändringar är ännu okommittade.

## Återstående arbete och avgränsningar

1. Nuvarande universum är strikt Nasdaq Stockholm `XSTO`. First North,
   Spotlight och NGM är separata marknader och ingår inte ännu.
2. Officiella börstickeralias är en separat visningsförbättring, inte
   en blocker för ISIN-baserad intern papertrading. Parser,
   append-only-lagring, atomisk aktivering, schema-30-gate och
   den avstängda key-only SFTP-adaptern för Nasdaqs tickeralias är
   klara. Själva Nordic Equity Reference data files-entitlementen,
   oberoende verifierad host key och en verklig leverans saknas ännu;
   prislistan från
   1 april 2026 anger EUR 236/månad före moms och listan från
   1 oktober anger samma produktpris. Offerten måste ändå bekräfta
   all-in-pris, rättigheter och transport.
3. Nasdaq Nordic public delayed pre-trade är driftsatt som första
   kostnadsfria paperfeed. Katalog, riktig CSV, full strömparsning och
   den låsta transporten är liveverifierade från agentcontainern.
   Staging har ett publikt policykontrakt och 416 ISIN-mappningar.
   Första filvalideringen väntar till nästa öppna XSTO-session eftersom
   en gammal fil avsiktligt inte får göras färsk i efterhand. Den stora
   minutfilen råarkiveras inte; endast härledd checksummebunden evidens
   sparas.
4. OMXSGI behöver en separat delayed- eller realtidsleverans. Nordic
   Index Web API har EUR 600/månad i anslutningsavgift enligt den
   gällande prislistan, men indexcoverage och användningsavgifter måste
   bekräftas i offert.
5. Basic-auth är avsiktligt enkel för en intern operatör. Extern eller
   fleranvändaråtkomst kräver TLS, sessionsauth, roller och CSRF-skydd.
6. Den automatiska post-close-observatören är implementerad och testad,
   men ett benchmarkexperiment kan inte startas innan en laglig
   benchmarkfeed har valts.
7. Motorn är verifierad, men ett licensierat komplett historiskt
   XSTO-dataset och ett verkligt benchmarkresultat saknas.
8. Stagingdeployen är utförd med schema 33, databasbackup,
   healthchecks, autentiserad API-smoke och extern TLS-kontroll.
   `market-sync` är aktivt och väntar på nästa öppna XSTO-session.
9. Första provideracceptansen och exekverbara liveboken kan bara
   uppstå under nästa öppna XSTO-session; historisk data märks inte om
   som färsk.
10. Nyheter, rapportkalender, fundamenta och makro utöver OMXSGI saknar
    ännu auktoriserad källa och används därför inte i AI-besluten.

## Nästa steg

1. Låt den aktiverade Nasdaq pre-trade-synken acceptera första
   15–20-minutersfilen under nästa öppna XSTO-session och verifiera
   därefter kontinuitet, orderbok, beslut och paperfill.
2. Begär offert på Nordic Equity Reference data files via Nasdaq Data
   Link Files, Nordic Index Web API med uttrycklig `OMXSGI`-coverage
   och Nordic Equity Web API Level 1 som reservväg, med
   [`docs/market-data-procurement.md`](docs/market-data-procurement.md)
   som underlag.
3. Skaffa entitlementen, verifiera Nasdaqs host key via en oberoende
   kanal och kör SFTP-adaptern one-shot utan att aktivera daemonen.
4. Kör den färdiga aliasimporten med ISIN som
   join-nyckel; godkänn inte snapshoten förrän alla 416 identiteter
   matchas.
5. Skapa providerutkast med `provider_contract_admin`, kör fem
   sammanhängande officiella acceptanssessioner och validera vald
   pris- respektive `OMXSGI`-väg först när avtal, transport, delay,
   full coverage, lagringsrätt och non-display-klassificering är
   dokumenterade.
6. Importera licensierad fullhistorik, corporate actions, historiskt
   universum, total-return-benchmark och riskindex till ett validerat
   backtestdataset.
7. Kör walk-forward med förhandsbestämda kostnadsantaganden.
8. Koppla Level 1-bid/ask och den dagliga observatören till det
   validerade forward-benchmarkets frysta kostnadsmodell, kör fem
   sammanhängande acceptanssessioner och starta därefter experimentet.
9. Konfigurera GitHub Environment/SSH/runtime på avsedd server och gör
   en kontrollerad deploy- och rollbackövning.
10. Byt Basic-auth mot sessions-/rollmodell först om fler användare ska
   få åtkomst.
11. Välj auktoriserade källor och provenienskontrakt för nyheter,
    rapportkalender, fundamenta och övrig makro innan dessa datatyper
    åter släpps in i AI-kontexten.
