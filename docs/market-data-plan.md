# Marknadsdataplan för Nasdaq Stockholm

## Avgränsning

Första universet är aktiva stamaktier, preferensaktier och depåbevis som
ESMA FIRDS redovisar för Nasdaq Stockholm (`MIC XSTO`). First North,
Spotlight, NGM, ETF:er, fonder, warranter och derivat ingår inte. De kan
läggas till som separata universum senare.

Varje instrument ska minst ha:

- stabilt internt id;
- ISIN;
- ticker och aktieslag;
- MIC och valuta;
- namn, sektor och instrumenttyp;
- första/sista handelsdag och aktiv/inaktiv-status;
- leverantörens symbol, när källan faktiskt tillhandahåller den, och
  senaste synktid.

Ticker får aldrig vara ensam identitet eftersom symboler och aktieslag
kan ändras.

## Datakrav

Varje quote/trade/bar måste bära:

- `event_time` från handelsplatsen;
- `received_at` i systemet;
- `source`;
- `delay_seconds`;
- instrument-id, valuta och handelsplats;
- pris, volym och vilken typ av datapunkt det är.

Agenten får inte fatta nya köpbeslut om:

- datan är äldre än avtalad maxfördröjning plus en liten tolerans;
- marknaden ska vara öppen men uppdateringar saknas;
- instrumentmappningen är okänd;
- tidsstämpeln ligger i framtiden;
- källor motsäger varandra över en definierad tolerans.

Alla marknadstider räknas i `Europe/Stockholm`, inklusive sommar- och
vintertid. Den versionsstyrda XSTO-kalendern innehåller Nasdaqs
officiella helgdagar och halvdagar för 2024–2026. Ett år som inte är
verifierat avvisas fail-closed.

## Verifierade alternativ

### ESMA FIRDS för instrumentidentitet

ESMA FIRDS publicerar fulla aktiva referensfiler veckovis och
deltafiler dagligen. Systemet frågar ESMA:s maskinläsbara fillista,
kräver en komplett fullsnapshot, kontrollerar publiceringsdatum och MD5,
begränsar storlek och ZIP-innehåll, parsar XML strömmande samt
råarkiverar filerna med lokal SHA-256.

Den senaste verifierade snapshoten, daterad 2026-07-25, bestod av två
fullfiler. Efter CFI- och XSTO-filter innehöll den 416 instrument:

- 406 stamaktier;
- 6 preferensaktier;
- 4 depåbevis.

En riktig end-to-end-körning importerade snapshot, två råfiler och alla
instrument atomiskt i PostgreSQL. Nästa körning var idempotent och
hämtade inget igen. Synken avvisar gammal data, ofullständiga snapshot,
för små/stora universum, motstridiga dubletter och oförklarad krympning
över 20 procent.

FIRDS tillhandahåller ISIN, namn, CFI, valuta, handelsplats och
handelsdatum, men inte Nasdaqs orderbokssymbol. De 416 posterna har
därför avsiktligt `symbol = NULL` tills en licensierad Nasdaq-referensfil
eller avtalad motsvarighet kan kopplas via ISIN. Symbol får inte gissas.

ESMA:s registervillkor tillåter återanvändning med angivande av källa
och övriga villkor i den publicerade legal notice. Kontrollera alltid
villkoren igen innan extern distribution.

Källor:

- <https://www.esma.europa.eu/sites/default/files/library/esma65-8-5014_firds_-_instructions_for_download_of_full_and_delta_reference_files.pdf>
- <https://www.esma.europa.eu/data-reporting/mifir-reporting>
- <https://registers.esma.europa.eu/publication/legalNoticePage>

### Nasdaq Stockholm handelskalender

Normal aktiehandel är 09:00–17:30 lokal tid; officiella halvdagar är
09:00–13:00. Kalendern för 2024–2026 är verifierad mot Nasdaqs sida,
lagras som historiserade snapshots med checksumma och ersätter ett helt
år atomiskt. Samma snapshot är idempotent och en äldre verifiering får
inte skriva över en nyare. År efter 2026 stoppas tills de har verifierats
och lagts till.

Källa:

- <https://www.nasdaq.com/european-market-activity/trading-hours>

### Nasdaq 15-minuters delayed data

Nasdaq uppger att nordisk pre- och post-trade-data publiceras med
15 minuters fördröjning som maskinläsbara CSV-filer. En ny fil skapas
varje minut under öppettid. Nasdaqs allmänna MiFID II-sida anger
24 timmars retention, medan den produktspecifika pre-trade-sidan anger
48 timmar. Detta är motstridiga officiella uppgifter. Tills ett avtal
anger annat ska ingestion, larm och backfill därför dimensioneras efter
den kortare perioden 24 timmar.

Den 29–30 juli 2026 verifierades också att rapportsidan exponerar
faktiska minutvisa filnamn och nedladdningslänkar för både pre-trade
och post-trade. Post-trade-länkarna följer mönstret
`/api/regulatory/trade-report/download?type=POST_TRADE&assetClass=EQUITY&fileName=...`.
Webbapplikationen hämtar fillistan från
`/api/regulatory/trade-reports?type=POST_TRADE&assetClass=EQUITY`.
Operatören valde den 30 juli Nasdaq Nordic public delayed data som
första kostnadsfria paperfeed. Endpoint och filhämtning gav HTTP 200 i
värdmiljön och via vanlig `curl` inifrån agentcontainern. Eftersom
URL:erna fortfarande är observerat officiellt webbeteende och inte ett
versionsstyrt API ska schema- eller transportdrift stoppa importen
fail-closed.

Den strikta CSV-parsern och provider-adaptern är nu verifierade mot en
riktig post-trade-fil. Adaptern tillåter endast HTTPS på Nasdaqs
rapportdomän, `POST_TRADE`, `EQUITY`, `XSTO`, konfigurerade ISIN,
instrumentets valuta och prisnotationen `MONE`. Den är kopplad till den
separata, avstängda `market-sync`-tjänsten; runtime-gaten för
leverantörsacceptans är fortfarande stängd.

Den 30 juli verifierades även formatet mot den verkliga filen
`NordicEquity-pretrade-2026-07-29T1000.csv`:

- SHA-256:
  `ae1d4408c3a0ddc9571252ba5368e7656ad597b06d0ef49b822adde27cf6a012`;
- 164 854 680 byte och 1 389 267 datarader för en enda nordisk minut;
- 8 479 `XSTO`-rader för 337 unika ISIN;
- 3 577 `BUY`- och 4 902 `SELL`-uppdateringar;
- kolumner för event- och publication time, sida, pris, kvantitet,
  orderantal, venue, handelssystem och handelsfas;
- explicita raderingar där pris, kvantitet och orderantal är tomma.

Filen är en ordnad ström av siduppdateringar, inte en komplett
minutsnapshot. Flera uppdateringar kan ha samma tidsstämpel. Radordningen
måste därför bevaras och en oförändrad köp- eller säljsida får bara
föras vidare när varje mellanliggande minut är verifierad. Vid en
minutlucka ska orderboksstatus kasseras eller byggas om från en
auktoritativ snapshot; den får inte fyllas med ett äldre pris.

Kodbasen har nu en strikt strömmande pre-trade-parser och en separat
Level 1-reducerare. De:

- filtrerar `XSTO` utan att läsa hela 165 MB-filen i minnet;
- kräver känt ISIN, rätt valuta, prisnotation `1` och exakt observerat
  schema;
- bevarar källans radsekvens och explicita sidoraderingar i en
  parserutfärdad batch för exakt filminut;
- binder batchen till lokalt beräknad SHA-256 över samtliga råbytes,
  en kanoniskt beräknad checksumma över den validerade
  referenssnapshoten och dess sorterade XSTO-ISIN;
- bevarar en separat coverage-/receipt-cursor även när minuten saknar
  XSTO-uppdateringar;
- stoppar flerradiga bytes-chunks, tomma fysiska rader, delvis tomma
  värden, nollikviditet,
  tids- eller mottagningstidsregression, okänd sida/ISIN, blandade
  handelssystem, varje transient korsad orderbok och minuttäckningsgap;
- avvisar filnamn med obefintlig eller tvetydig lokal Stockholmstid;
- uttrycker tom eller ensidig bok utan fallback.

SHA-256-värdet är ett lokalt integritetsbevis för exakt mottaget
innehåll. Eftersom parseranroparen ännu lämnar både bytes och förväntad
checksumma är värdet inte en signatur och autentiserar inte Nasdaq som
avsändare. Driftproveniens måste senare bindas till vald transports
TLS-identitet samt avtalad fil-/manifestmetadata.

Migration 025 kopplar parsern och reduceraren till ett eget
transportoberoende append-only-schema. Det lagrar exakt filbatch,
ordnade siduppdateringar, separat stream-cursor och materialiserad
bästa bid/ask. Batchen förseglas först när rad- och state-antal stämmer,
och nästa batch måste vara exakt nästa minut. Omstart återläser senaste
förseglade cursor/state utan att använda `market_quotes`.

Pre-trade-paperfills kan bindas till senaste förseglade exekverbara
bid/ask, men bara utanför ett körande forward-experiment tills dess
frysta kostnadskontrakt har fått en uttrycklig Level 1-policy. De kräver
även öppen XSTO-session, senaste PASSED-provider-validering och
kumulativt återstående visad sidvolym. Parsern/reduceraren är
fortfarande inte kopplade till en godkänd nättransport.

Post-trade ensam räcker inte som freshnesskälla för hela universet.
Illikvida aktier kan sakna ett färskt avslut trots att marknaden är
öppen. En driftgodkänd lösning måste därför kombinera Level 1-pre-trade
med senaste avslut och uttrycklig status för handelsstopp, auktion och
tom eller ensidig orderbok. Ett instrument utan verifierbart pris får
inte handlas eller fallbackvärderas.

Den fulla nordiska pre-trade-filen ska inte läggas som ett 165 MB
`BYTEA` varje minut i PostgreSQL. Efter avtalsbekräftelse ska
ingestionen strömma filen, beräkna checksumma på hela källfilen och
arkivera en kanonisk `XSTO`-delmängd eller använda avtalad objektlagring.
Exakt rådata- och retentionsrätt måste vara klar innan valet fryses.
De nuvarande fail-closed-taken är 5 000 000 fysiska datarader och
250 000 materialiserade XSTO-uppdateringar per minut; provleveransen
måste bekräfta eller justera dessa kontraktsgränser före aktivering.

Migration 008 och den separata `market-sync`-tjänsten ger nu:

- gzip-komprimerat rådataarkiv med SHA-256;
- unik filidentitet per provider, datatyp och rapportminut;
- atomisk lagring av fil, quotes, gaps och synkaudit;
- checksummefel om samma filnamn senare får annat innehåll;
- idempotent omstart och automatisk upplösning av backfillade gaps;
- minutpollning endast under en explicit öppen XSTO-session;
- avstängd opt-in-profil som kräver både flagga, instrumentregister och
  marknadskalender.

Migration 014 lägger en oberoende provider-gate framför tjänsten. Ett
runtimekontrakt godtas bara när:

- kontraktet är `VALIDATED`, giltigt och kopplat till exakt rätt
  provider, produkt, MIC, transport och användningsfall;
- juridiska villkor och servertransport är verifierade;
- produktcoverage och tickeralias täcker hela det aktiva universet;
- en aktuell provkörning innehåller filer och quotes;
- observerad nominell delay plus transporttid håller kontraktet.

Migration 031 gör detta till ett strikt, körbart
operatörsgodkännande. `provider_contract_admin` skapar ett
operatörsattribuerat utkast och hashar sedan den exakt granskade
avtalsfilen samt ett separat acceptansmanifest. Validering kräver rå
och härledd lagringsrätt, fem sammanhängande officiella XSTO-sessioner,
full produktcoverage och symbolmappning, leveranstid inom avtalet samt
bevisad korrigering, omstart, gap recovery och kill switch.
Aktieprodukter binds till senaste frysta XSTO-snapshot; en
`delayed-index-level`-produkt täcker exakt ett index och får inte bära
en aktiesnapshot. Evidens och återkallelser är append-only.

Nasdaq-delayed-kontraktet ligger initialt som `DRAFT`. Detta gör att
`ENABLE_NASDAQ_DELAYED_INGESTION=true` ensam inte kan kringgå
licens-/transportgränsen.

Administrations- och acceptansflödet är implementerat och verifierat,
men inget faktiskt pris- eller `OMXSGI`-avtal och inga fem verkliga
acceptanssessioner är provisionerade. Driftgaten ska därför förbli
stängd.

Tidigare server-till-server-körningar tidsgränsade eller återställdes.
Rotorsaken bestod av två delar: runtime läste den renderade HTML-sidan
i stället för JSON-katalogen, och Nasdaqs CDN svarade inte på
Pythonklienternas transportprofil i Docker. Den 30 juli rättades
katalogvägen och en vanlig, öppet identifierad `curl`-transport
verifierades inifrån samma container mot både katalog och fil. Den
använder ingen browser-impersonation och får inte följa redirects eller
hämta andra hosts.

Den tekniska post-trade-transporten är därmed användbar för
acceptansfasen. Provider-gaten för officiella villkor, lagringsrätt,
coverage och fem verkliga sessioner förblir stängd tills faktisk
evidens har provisionerats.
Nasdaqs aktuella policy anger att delayed-användning i allmänhet är
gratis och att non-display-avgifter inte gäller delayed-use-cases.
Avgifter och förhandsgodkännande kan däremot krävas vid kommersiell
vidaredistribution eller avgiftsbelagda value-added-tjänster.

Källor:

- <https://www.nasdaq.com/market-regulation/nordic/mifid-ii>
- <https://tradereports.nasdaq.com/shares/trade-reports/pre-trade>
- <https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Exchange_Market_Data_Price_List_April_2026>

### Nasdaq Nordic Reference Data Files

Nasdaqs officiella referensdata innehåller bland annat ISIN, ticker,
referenspris och lot size. Filerna skapas dagligen och levereras via
Nasdaq Data Link Files/SFTP med separat entitlement.

Nasdaq flyttade denna leverans från FDS till Nasdaq Data Link Files
under 2026. Den aktuella vägen kräver Nasdaq Data Link-konto,
filprenumeration, MFA och SSH-nyckel; själva nedladdningen kan
automatiseras.

Detta är den robustaste instrumentkällan. Den offline-del som inte
kräver credentials är nu implementerad mot produktionens TIP
3.10.17.1: `BDt` och `BDSh` kopplas via stabilt tradable-id, endast
exakt `XSTO` accepteras och ISIN, valuta och CFI måste stämma mot den
frysta FIRDS-snapshoten. Råfil, checksumma och komplett
ISIN–ticker-medlemskap lagras append-only i schema 28 och aktiveras
atomiskt. Schema 29 binder varje ny snapshot till ett separat,
append-only entitlement-bevis för avtalsperiod, intern användning,
rå- och härledd lagring samt en oberoende verifierad SFTP-värdnyckel.
Ingen entitlement skapas automatiskt.

En separat SFTP-adapter och daemon finns nu bakom
`ENABLE_NASDAQ_REFERENCE_SYNC=true` och Docker-profilen
`nasdaq-reference`. Den kan endast ansluta till
`sftp.data.nasdaq.com:22`, kräver en i förväg provisionerad
`known_hosts`, använder endast angiven privat nyckel och stänger av
ssh-agent, standardnyckelsökning och lösenordsinloggning. Filstorlek,
timeouter, dagssökväg och TIP-version är hårt begränsade. Före
nätanslutning krävs dessutom att runtime-avtalsnyckeln matchar ett
aktuellt `VALIDATED` schema-30-bevis och att `known_hosts` innehåller
exakt samma algoritm och SHA-256-fingerprint. Adapter och schema är
lokalt testade, men de ska inte aktiveras innan åtkomst,
host-key-fingerprint, lagringsrätt och kostnad är godkända.

Källor:

- <https://www.nasdaq.com/solutions/data/nasdaq-nordic-reference-data-files>
- <https://www.nasdaq.com/solutions/data/nasdaq-nordic-file-delivery-service>
- <https://www.nasdaq.com/docs/INET-Reference-data%20files-in-FDS-v3.0>
- <https://www.nasdaq.com/docs/NDL-files-SFTP-guidelines>
- <https://view.news.eu.nasdaq.com/view?id=b8cce52d889b395d9030aa9b4d07a929a&lang=en&src=rss>

Prislistan som gäller från 1 april 2026 anger EUR 236/månad för Nordic
Equity Reference data files. Det är en filprodukt och priset ska
bekräftas i offert innan beställning. Den gamla FDS-plattformen
stängdes för ordinarie användning den 1 juli 2026; ny integration ska
därför byggas mot Nasdaq Data Link Files via SFTP, inte mot FDS.

Källa:

- <https://www.nasdaq.com/docs/Nasdaq_European_Market_Other_Data_Products_Price_List_April_2026>

### Tredjeparts-API

Twelve Data listar Nasdaq Stockholm (`XSTO`) och erbjuder en dagligen
uppdaterad stock-lista på Pro+-nivå. Deras publika exchange-sida anger
ingen tydlig fördröjning för XSTO. Det kan därför vara en möjlig
referens-/fundamentalkälla, men ska inte godtas som intradagskälla förrän
coverage, primär handelsplats, licens och faktisk delay är skriftligt
bekräftade.

Källa:

- <https://twelvedata.com/exchanges/xsto>

Yahoo Finance-integrationen har tagits bort. Styrkt historik ska
importeras från en separat, avtalad källa och får inte återinföras som
fallback för intradag, analys, dashboard eller backtest.

### Realtid

Realtid är inte samma licensfall som 15-minutersdata. Nasdaqs policy
klassar maskinell åtkomst som non-display. Analys, risk och
portföljvärdering ligger i kategori 1; orderrouting och fullt
automatiserad handel ligger i kategori 2. Prislistan som gäller från
1 april 2026 anger för Nordic Equity Level 1 EUR 583/månad för kategori
1 och EUR 2 330/månad för kategori 2, före eventuella produkt-,
distributörs-, anslutnings- och skatteavgifter. En faktisk offert och
klassificering från Nasdaq eller auktoriserad distributör krävs före
beställning.

Om realtid köps rekommenderas Nordic Equity Level 1 framför en ren
last-sale-feed, eftersom Level 1 innehåller bästa köp/sälj och därmed
ger en verifierbar spread till paperexekveringen. Officiella
referensfiler behövs fortfarande för stabil ISIN-ticker-mappning.

Källor:

- <https://www.nasdaq.com/solutions/data/market-data-catalog>
- <https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Exchange_Market_Data_Price_List_April_2026>

### Officiella leveransalternativ och aktuell prisbild

Nasdaqs prislista som gäller från 1 april 2026 ger tre relevanta
leveransvägar:

1. **MiFID/MiFIR delayed CSV**: exakt 15 minuter för nordisk pre- och
   post-trade. Nasdaq publicerar nya filer varje minut under öppettid.
   Användningen är gratis. Den observerade JSON-/filtransporten fungerar
   nu från agentcontainern, men är inte ett versionsstyrt
   API-kontrakt. Drift, lagring och automatisk serveranvändning måste
   därför bindas till det granskade villkors- och acceptansbeviset.
2. **Nordic Web API**: HTTP/HTTPS och REST med XML/JSON/JSONP för
   quotes, orderbok, trades och historik. Anslutningsavgiften är
   EUR 1 260/månad för Nordic Equity och EUR 600/månad för Nordic
   Index. Display- eller non-display-avgifter tillkommer. Det måste
   bekräftas skriftligt att indexprodukten innehåller `OMXSGI`.
3. **Direkta feeds**: ITCH för aktier och GCF/TIP för bland annat
   nordiska index. GCF anger realtidstäckning för fler än 500 nordiska
   och baltiska index, men kräver separat data-, användnings- och
   anslutningsavtal. Detta är mer komplext än projektet behöver före en
   godkänd papertrading-period.

Nuvarande körbara `market-sync` är trots det generella
providerkontraktet hårdkopplad till provider `nasdaq-nordic`, datatyp
`delayed-post-trade-equity`, leveransläge `DELAYED_15M` och transport
`PUBLIC_CSV`. Web API, SFTP eller en auktoriserad distributör kräver
en ny adapter och provider-factory efter att produkt- och
transportkontraktet har valts. Det är en känd implementationslucka,
inte något som ska döljas genom att bara ändra databasraden.

Nasdaq definierar data med mindre än 15 minuters fördröjning som
realtid. För realtidsdata är intern analys/risk `Non-Display Category
1`, medan orderrouting, automatisk handel och handel med manuell
intervention är `Non-Display Category 2`. Den gällande prislistan anger
EUR 583/månad respektive EUR 2 330/månad för Nordic Equity Level 1.
Detta är användningsavgifter; Web API-, produkt-, index-, anslutnings-,
distributörs- och momsavgifter kan tillkomma.

Källor:

- <https://www.nasdaq.com/docs/Nasdaq_European_Market_Other_Data_Products_Price_List_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Exchange_Market_Data_Price_List_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026>
- <https://www.nasdaq.com/solutions/nasdaq-genium-consolidated-feed>

### Rekommenderat leveransbeslut

För den första interna papertrading-versionen är den rekommenderade
ordningen:

1. Kör den kostnadsfria Nasdaq Nordic delayed post-trade-feeden genom
   fem sammanhängande XSTO-sessioner och godkänn den endast om
   transport, delay, coverage, omstart och gap recovery håller.
2. Behåll ESMA FIRDS och ISIN som aktieuniversumets identitet. Köp
   Nordic Equity Reference data files först när officiella
   tickeralias behövs för presentation eller en senare datakälla.
3. Begär Nordic Equity Web API Level 1 endast om femsessionersprovet
   visar att post-trade inte ger tillräcklig färskhet eller coverage
   för det avsedda paperflödet.
4. Be Nasdaq skriftligen ange om exakt `OMXSGI`, inklusive
   total-return-nivå och officiell stängning, ingår i denna Level
   1-rätt. Begär annars separat offert på Nordic Index Web API.
5. Ange uttryckligen att orderrouting och riktiga pengar inte ska
   aktiveras under utvärderingen. En senare brokerintegration kräver
   ny klassificering för `Non-Display Category 2`.

Offerten ska svara på:

- exakt produktnamn, symbol och leveransväg för `OMXSGI`;
- full `XSTO`-coverage, inklusive stamaktier, preferensaktier och
  depåbevis, samt om First North/Spotlight/NGM ingår eller är separata;
- nominell och maximal observerbar delay samt börsens
  event-/publication-timestamps;
- om intern papertrading, modellanalys, riskberäkning och benchmark är
  tillåtna utan handelslicens;
- vad som ändras i licens och avgift vid orderrouting eller automatisk
  exekvering;
- historik, corporate actions, point-in-time-instrumentregister och
  total-return-index för minst den period som backtestet behöver;
- SLA, rate limits, testmiljö, support, ändringsnotiser och rätt att
  lagra rådata och härledda resultat internt.

Det fullständiga inköpsunderlaget, offertbrevet och acceptanstestet
finns i [`market-data-procurement.md`](market-data-procurement.md).

## Rekommenderad ordning

1. Behåll strikt `XSTO` som första universum; behandla First North,
   Spotlight och NGM som separata senare beslut.
2. Kör Nasdaq delayed post-trade genom femsessionersacceptansen med
   FIRDS-ISIN som direkt join-nyckel.
3. Skaffa Nasdaqs tickeralias via en licensierad referensfil först när
   aliasen behövs; provisionera då den färdiga key-only SFTP-adaptern
   efter avtal och verifierad host key.
4. Kontraktstesta att den befintliga freshness-gaten stoppar saknade,
   sena och motsägande leverantörssvar.
5. Backfilla historik med point-in-time-universum.
6. Versionssätt Nasdaqs kalender för 2027 innan nuvarande horisont löper
   ut.
7. Byt till realtid endast om licens, pris och non-display-användning är
   uttryckligen godkända.

## Beslut som återstår

- Behöver gränssnittet officiella tickeralias nu, eller räcker namn och
  ISIN under den första acceptansperioden?
- Ska vi begära offert på Nasdaq Nordic Index Web API för `OMXSGI`?
- Är användningen enbart privat/intern eller ska data/resultat visas för
  externa användare?
