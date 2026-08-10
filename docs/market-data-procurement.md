# Inköps- och acceptansunderlag för XSTO-marknadsdata

## Syfte och avgränsning

Underlaget gäller en intern, ensam operatörs papertrading-agent för:

- 416 aktiva stamaktier, preferensaktier och depåbevis på Nasdaq
  Stockholm (`MIC XSTO`) enligt FIRDS-snapshot 2026-07-25;
- högst 15 minuters nominell fördröjning under öppen marknad;
- `OMXSGI` som separat total-return-benchmark;
- intern lagring av rådata, härledda värden och revisionsbevis;
- historiskt backtest och därefter minst 252 forward-sessioner.

Extern distribution, kundvisning, orderrouting och riktiga pengar
ingår inte.

## Rekommenderad inköpsordning

| Prioritet | Behov | Förstahandsväg | Reservväg | Status |
|---|---|---|---|---|
| 1 | ISIN–ticker och daglig referensdata | Nordic Equity Reference data files | Auktoriserad distributör med Nasdaq-alias | Offert krävs |
| 2 | Level 1 med högst 15 minuters fördröjning för hela XSTO | Nordic Equity Web API Level 1, intern non-display | Skriftligt serverstödd leverans av Nasdaqs fria 15-minutersfiler | All-in-offert och testspecifikation krävs |
| 3 | `OMXSGI` total-return-benchmark | Skriftlig `OMXSGI`-rätt i Nordic Equity Level 1 om den faktiskt ingår | Nordic Index Web API med explicit symbolrätt | Symbolrätt/coverage/offert krävs |
| 4 | Point-in-time-historik | Nasdaq HistoricalView plus separata referens-/corporate-action-/indexserier | Auktoriserad historikleverantör | Produktpaket/offert krävs |

Köp inte någon produkt innan Nasdaq har bekräftat att den täcker exakt
användningsfall, instrument, transport och lagringsrätt.

Web API är förstahandsvägen eftersom Nasdaq beskriver den som en
serverstödd REST-tjänst över HTTPS med JSON/XML och stöd för quote,
orderbook, trades och historik. Den publika 15-minuterssidan är fortsatt
en kostnadsreserv: den får bara väljas om Nasdaq lämnar ett stabilt,
dokumenterat serverendpoint-/filkontrakt. Web API:s publicerade
anslutningsavgift är inte ett all-in-pris; data-, non-display-, index-
och andra användningsavgifter måste stå i offerten.

## Varför både pre- och post-trade behövs

Post-trade visar genomförda affärer. Ett illikvidt instrument kan
därför sakna en färsk affär långt efter att marknaden öppnat.
Pre-trade Level 1 ger bästa köp/sälj och volym och behövs för:

- en aktuell, verifierbar värdering av varje handelsbart instrument;
- spreadbaserad paperfill i stället för senaste avslut;
- upptäckt av ensidig eller tom orderbok;
- att skilja verklig inaktivitet från transportfel;
- att undvika att hela 416-instrumentsuniverset blir stale bara för att
  en aktie inte har handlats den senaste minuten.

Leveransen behöver också marknadsstatus för handelsstopp, auktion,
stängd orderbok och instrument utan köp- eller säljkurs. Ett sådant
tillstånd ska uttryckas som data, aldrig döljas med föregående pris.

## Minsta datakontrakt

### Referensdata

- ISIN, ticker, namn, MIC, valuta och instrumenttyp;
- aktieslag, segment, listnings- och avnoteringsdatum;
- lot size, prisnotation och handelstillstånd;
- dagliga förändringar, symbolbyten och corporate-action-nycklar;
- stable provider instrument id och publiceringstid.

### Intradag

- ISIN och provider instrument id;
- event-, publication- och received-timestamp;
- bästa köp/sälj med respektive volym;
- senaste affär med pris och volym;
- valuta, MIC, handelsfas och instrumentstatus;
- trade-/MMT-flaggor och prisnotation;
- sequence/file id, källa och rådatachecksumma;
- nominell delay och uppmätt transportfördröjning.

### Benchmark

- exakt symbol `OMXSGI`;
- total-return-nivå, valuta och indexstatus;
- event-, publication- och received-timestamp;
- officiell stängningsnivå per XSTO-session;
- historisk serie med korrigerings- och revisionspolicy.

### Historik

- point-in-time-medlemskap och tickerhistorik;
- råa eller justerbara OHLCV/tickdata med availability time;
- split, utdelning, emission, inlösen, fusion och avnotering;
- historisk `OMXSGI` total-return-serie;
- rätt att spara rådata och reproducera backtest efter abonnemangets
  slut.

## Publicerade priskomponenter

Beloppen nedan kommer från Nasdaqs prislistor som gäller från
1 april till 31 augusti 2026. De är planeringsvärden före moms, inte
en all-in-offert.

| Komponent | Publicerat pris per månad | Kommentar |
|---|---:|---|
| Nordic Equity Reference data files | EUR 236 | Separat filprodukt |
| Fri MiFID/MiFIR delayed data | Ingen dataavgift för intern användning enligt policy | Stödd automatiserad transport är inte bekräftad |
| Nordic Equity Web API access | EUR 1 260 | Anslutning; användnings-/dataavgifter tillkommer |
| Nordic Equity Level 1, Non-Display Category 1 | EUR 583 | Realtidsanalys, risk och värdering; offertklassificering krävs |
| Nordic Equity Level 1, Non-Display Category 2 | EUR 2 330 | Trading/order routing; ingår inte i paperfasen |
| Nordic Internal Distributor – data feed | EUR 708 | Kan tillkomma beroende på hur den interna applikationen klassificeras |
| Nordic Index Web API access | EUR 600 | `OMXSGI`-rätt och indexavgifter måste bekräftas |
| Nordic HistoricalView subscription per asset class | EUR 1 144 | Löpande historikåtkomst |
| Nordic HistoricalView database per asset class | EUR 436 | Exakt innehåll och lagringsrätt måste bekräftas |

Till och med 31 augusti 2026 är den synliga bassumman för referensdata
plus realtime Equity Web API
Category 1 plus Index Web API är EUR 2 679/månad. Den möjliga
intern-distributörsavgiften skulle höja de uppräknade komponenterna
till EUR 3 387/månad. Ingen av summorna är ett offertpris:
indexrättigheter, teknisk anslutning, historik, corporate actions,
support, andra användningsavgifter, minimiavgifter och moms kan
tillkomma. `Nordic Equity Level 1` beskrivs även som innehållande
indexvärden och komponenter; offerten måste därför uttryckligen svara
om exakt `OMXSGI` redan ingår så att en onödig separat Index Web
API-anslutning inte köps.

Nasdaq har redan publicerat en ny Exchange Data Price List som gäller
från 1 september 2026. Den ersätter bland annat de tidigare interna
non-display-kategorierna med `Internal Basic`, `Internal Standard`
och `Internal Premium`. `Internal Standard` beskrivs som intern
distribution, härledd data och non-display non-trading;
`Internal Premium` lägger till non-display trading. I samma
Internal Distributor-tabell finns Nordic Equity Level 1 angivet till
EUR 3 438/månad, men offerten måste uttryckligen ange vilken
underkategori och vilka tillägg som gäller för denna paperfas.
Planeringssumman EUR 2 679 får därför inte användas för en avtalsperiod
som går in i september utan skriftlig omklassificering och nytt
all-in-pris.

Other Data Products-listan som gäller från 1 oktober 2026 anger fortsatt
EUR 236 för Nordic Equity Reference data files via Nasdaq Data Link
Files/SFTP, EUR 1 260 för Nordic Equity Web API, EUR 600 för Nordic
Index Web API samt EUR 1 144/436 för HistoricalView
subscription/database per asset class. Web API-avgifterna är endast
anslutning; display- eller non-display-avgifter tillkommer.

## Frågor som offerten måste besvara

### Rättigheter och klassificering

1. Är intern modellanalys, riskberäkning, portföljvärdering,
   paperfills, backtest och benchmark tillåtna?
2. Hur klassificeras paperfasen enligt både nuvarande regler och
   `Internal Basic/Standard/Premium` från 1 september 2026 när ingen
   order skickas till broker?
3. Vilka nya avtal och avgifter krävs senare för orderrouting,
   automatisk handel eller manuell intervention?
4. Får rådata, normaliserade quotes, features, modellresultat,
   auditloggar och backtestresultat lagras internt?
5. Vad får behållas och användas efter att abonnemanget avslutas?
6. Krävs Global Data Agreement eller annan rapportering trots att
   användningen är intern och inte distribueras?

### XSTO och referensdata

7. Täcker produkten samtliga aktiva stamaktier, preferensaktier och
   depåbevis på `XSTO`, med ISIN och Nasdaq-ticker?
8. Levereras dagliga listningar, avnoteringar, tickerbyten, segment,
   handelsstatus och corporate actions?
9. Vilken leveransplattform gäller under avtalsperioden: FDS, Nasdaq
   Data Link Files/SFTP eller annan kanal? Nasdaqs produktsida och
   migrationsinformation måste avstämmas i offerten.

### 15-minutersdata och realtime-reserv

10. Finns ett dokumenterat, automatiserbart serverendpoint eller
    filprotokoll för de fria pre- och post-trade-filerna utan
    webbläsarautomation?
11. Vilka SLA, rate limits, IP-regler, autentiseringsmetoder,
    schema/versioner, ändringsnotiser och supportkanaler gäller?
12. Är delay exakt 900 sekunder från publication time och vilken
    maximal transportlag garanteras?
13. Ingår bästa köp/sälj, respektive volym, senaste affär,
    handelsfas/status, MMT-flaggor, prisnotation och källans
    timestamps?
14. Hur representeras handelsstopp, auktion, tom eller ensidig
    orderbok och korrigerade/cancelled trades?
15. Vilken backfill-retention och korrigeringspolicy är avtalad?
    Nasdaqs allmänna MiFID II-sida anger 24 timmar och den
    produktspecifika pre-trade-sidan 48 timmar. Offerten måste lösa
    motsägelsen; systemet dimensioneras efter 24 timmar tills dess.

### OMXSGI

16. Ingår exakt `OMXSGI`, inklusive realtime/delayed intradagsnivå och
    officiell daglig total-return-stängning?
17. Ingår `OMXSGI` redan med Nordic Equity Level 1 för den avsedda
    interna användningen, eller krävs Nordic Index Web API?
18. Är Nordic Index Web API-avgiften endast anslutning och vilka
    index-, användnings- eller non-display-avgifter tillkommer?
19. Får serien lagras och användas i internt backtest och forward
    paper-benchmark?

### Historik

20. Vilket paket ger den önskade perioden med XSTO-order/trade/OHLCV,
    point-in-time-referensdata, corporate actions och historisk
    `OMXSGI`?
21. Levereras availability timestamps, korrigeringar och råfiler som
    gör look-ahead- och survivorship-kontroll möjlig?
22. Är leveransen HistoricalView, separat databas, Data Link
    Files/SFTP eller annan aktuell transport?
23. Vad blir all-in månadspris, startkostnad, minsta avtalsperiod,
    moms, supportnivå och uppsägningstid för båda alternativen?

## Tekniskt basbevis före avtal

En verklig pre-trade-minut från 29 juli 2026 har verifierats manuellt
mot Nasdaqs produktsida. Filen var cirka 165 MB med 1 389 267 rader,
varav 8 479 `XSTO`-uppdateringar för 337 ISIN. Den innehöll både
`BUY`/`SELL`, flera uppdateringar per instrument och explicita
sidoraderingar.

Det observerade formatet är kontraktstestat i en strömmande parser.
En separat reducerare bevarar radsekvens, bygger bid/ask, för vidare
oförändrad sida över högst en verifierad minut och stoppar vid
minutlucka, tidsregression, nollikviditet, transient korsad bok eller
osäkra värden. Batchen binds till filminut, lokalt råbyteshash och det
kanoniskt hashade, validerade referensuniversum som parsern använde.
En separat cursor bevarar kontinuitetsbevis även för en minut utan
XSTO-rader. Detta bevisar format och lokal innehållsintegritet, men
inte Nasdaq-ursprung, rätten eller stabiliteten att automatisera
webbtransporten.

Referensfilens lokala kontrakt är separat färdigt mot Nasdaqs
produktionstyp `Nordic_Equity_RefData.tip` och TIP 3.10.17.1.
Importen kräver `BDt` + `BDSh`, exakt XSTO-exchange och Primary MIC,
giltigt ISIN, valuta och CFI samt 100 procent av den frysta
FIRDS-snapshoten. Schema 28 bevarar råfil/checksumma och varje
ISIN–ticker-rad append-only; endast en komplett snapshot kan bli aktiv.
Schema 29 kräver för varje ny snapshot ett separat append-only-bevis
för avtalsnyckel, giltighet, intern paperanvändning, rå- och härledd
lagringsrätt och verifierad SFTP-värdnyckel. Ett återkallat eller
utgånget bevis gör snapshoten operativt oanvändbar.

Den key-only SFTP-transport som ska leverera filen är också
implementerad men avstängd. Värd och port kan inte styras av
miljövariabler, okänd host key avvisas, ssh-agent och
standardnyckelsökning är avstängda, filen läses under ett hårt
20 MB-tak och ändrad storlek under överföringen stoppar importen.
Credential-filer måste vara absoluta vanliga filer, privata nyckeln får
inte vara en symlink eller grupp-/världsläsbar och transportfel lämnar
inte filvägar eller underliggande feltext i operatörsloggen.
Före anslutning jämförs `known_hosts` mot exakt algoritm och
SHA-256-fingerprint i det godkända schema-30-beviset.

Detta är fortfarande ett lokalt kontrakts-, transport- och
lagringsramverk, inte ett verkligt Nasdaq-entitlement, verklig
filcoverage, verifierad Nasdaq-host key eller rätt att lagra
Nasdaq-data. Databasen seedar därför avsiktligt ingen validerad post.

Följande återstår innan ett providerbevis kan bli `PASSED`:

- skriftligt servertransport- och användningsavtal;
- transportbevis som binder TLS-/manifestidentitet, filnamn,
  rapportminut och checksumma utan självattestering;
- uttrycklig instrument-/handelsstatus utöver observerad handelsfas;
- fem hela sessioners coverage-, gap-, leverans- och restarttest;
- samtidig verifiering av pre-trade, post-trade och `OMXSGI`.

Efter faktisk leverans ska råpaketet först passera den lokala,
read-only kontrollen i `historical_data_preflight`. Den verifierar
avtalsfil, rättighets- och fullständighetsintyg, obligatoriska
artefaktroller och varje SHA-256 utan att importera data. En
formatspecifik adapter får inte implementeras mot en gissad
leverantörsstruktur; den tas fram mot det verkliga provuttaget efter
produktvalet.

## Acceptanstest före aktivering

### Juridik och produkt

- [ ] Skriftligt produktnamn, användningsrätt, lagringsrätt och
  transport finns.
- [ ] Ett `VALIDATED` schema-30-bevis har skapats med
  `reference_entitlement_admin` och har granskade villkor,
  retention, avtalsdatum och oberoende verifierad host-key-fingerprint.
- [ ] Full XSTO-coverage och exakt `OMXSGI`-coverage är bekräftad.
- [ ] Intern papertrading är uttryckligen separerad från orderrouting.
- [ ] Avtalsperiod, kostnad, kommande prisändringar och uppsägning är
  godkända av operatören.

### Referensdata

- [ ] Samtliga 416 förväntade instrument matchas entydigt via ISIN.
- [ ] Samtliga får provider-id och ticker utan gissning.
- [ ] Dubletter, saknade alias, fel MIC/valuta och oförklarad
  universumförändring avvisas.
- [ ] Daglig delta/snapshot är idempotent och råfilen checksummefryses.

### Transport och datakvalitet

- [ ] Fem sammanhängande öppna XSTO-sessioner kan hämtas utan
  webbläsarautomation.
- [ ] Varje fil/svar har stabil identitet, schema och
  publiceringstidsstämpel.
- [ ] Observerad leverans håller `nominal_delay + avtalad maxlag`.
- [ ] Pre- och post-trade kan förenas utan dubbla eller felordnade
  observationer.
- [ ] Backfill efter simulerat avbrott stänger varje rapporterad lucka.
- [ ] Korrigerade/cancelled trades och handelsstatus hanteras
  deterministiskt.
- [ ] Inga secrets, tokens eller personuppgifter skrivs i råarkiv,
  loggar eller providerbevis.

### Coverage och fail-closed

- [ ] Produktcoverage och aliascoverage är 100 procent av det aktiva
  universet.
- [ ] Verklig Level 1-provdata visar hur många instrument som samtidigt
  har tvåsidig, ensidig eller tom orderbok.
- [ ] Instrument med handelsstopp eller otillräckligt pris får ett
  explicit tillstånd och kan inte handlas eller fallbackvärderas.
- [ ] Om full samtidig quote-coverage är marknadsmässigt omöjlig ändras
  runtime-gaten till instrumentnivå plus ett förregistrerat
  marknadstäckningsgolv; den får aldrig mjukas upp för den aktie som
  faktiskt handlas.
- [ ] `market_data_provider_contracts` är kvar som `DRAFT` tills
  juridik, transport och provdata har passerat.
- [ ] Utkast och slutlig validering görs endast med schema 31-verktyget
  `provider_contract_admin`; utkastets operatör, exakt avtalsfil och
  fem sammanhängande officiella acceptanssessioner ska vara
  append-only-spårbara.
- [ ] Ett `PASSED`-bevis innehåller förväntade/täckta/mappade
  instrument, fil- och quoteantal, maximal leveranstid och SHA-256.

### Benchmark och återställning

- [ ] `OMXSGI`-nivåer kan bindas till exakt provideravtal och
  källchecksumma.
- [ ] Officiell stängning finns för varje verifierad XSTO-session.
- [ ] Providerfel gör trading-readiness `BLOCKED`, inte grönt med
  gammal cache.
- [ ] Kill switch, restart, gap-backfill och avtalsutgång provas.

## Implementation efter godkänd offert

1. Skapa ett operatörsattribuerat `DRAFT` med
   `provider_contract_admin`; frys avtalsmetadata utan credentials.
2. Implementera adapter för vald transport och en provider-factory;
   nuvarande daemon är hårdkopplad till `PUBLIC_CSV` post-trade.
3. Koppla den redan kontraktstestade pre-trade-parsern och
   orderboksreduceraren till vald transport. Append-only lagring för
   XSTO-siduppdateringar, cursor och materialiserad Level 1 finns i
   schema 25; full råfil ska fortfarande ligga i avtalad objektlagring
   eller annan tillåten retention, inte som 165 MB `BYTEA` per minut.
4. Lägg till kontraktstest för status, korrigeringar och indexdata.
5. Kör fem sammanhängande officiella acceptanssessioner mot en isolerad
   databas och spara exakta checksummebevis, aldrig nycklar.
6. Validera avtal och acceptans med `provider_contract_admin` först
   efter operatörens granskning; använd inte manuell SQL.
7. Importera historik och kör walk-forward.
8. Starta det förregistrerade forward-experimentet först därefter.

## Officiella källor kontrollerade 2026-07-30

- Pris- och policynav:
  <https://www.nasdaq.com/solutions/data/european-pricing-policies>
- Exchange Data Price List, giltig från 2026-04-01:
  <https://www.nasdaq.com/docs/Nasdaq_European_Exchange_Market_Data_Price_List_April_2026>
- Other Data Products Price List, giltig från 2026-04-01:
  <https://www.nasdaq.com/docs/Nasdaq_European_Market_Other_Data_Products_Price_List_April_2026>
- Exchange Data Price List, giltig från 2026-09-01:
  <https://www.nasdaq.com/docs/data/Nasdaq_European_Markets_Data_Price_List_September_2026>
- Other Data Products Price List, giltig från 2026-10-01:
  <https://www.nasdaq.com/docs/data/Nasdaq-European-Other-Markets-Data-Products-Price-List-October-2026>
- Nordic Equity Reference Data Files:
  <https://www.nasdaq.com/solutions/data/nasdaq-nordic-reference-data-files>
- 15-minuters pre-trade:
  <https://tradereports.nasdaq.com/shares/trade-reports/pre-trade>
- 15-minuters post-trade:
  <https://tradereports.nasdaq.com/shares/trade-reports/post-trade>
- HistoricalView:
  <https://www.nasdaq.com/solutions/data/nasdaq-nordic-baltic-historicalview>

## Färdig offertförfrågan

Skicka inte med API-nycklar, `.env`-innehåll eller annan
driftkonfiguration.

**Till:** `EUDataSales@nasdaq.com`

**Kopia:** `DataEurope@nasdaq.com`

**Ämne:** Quote request – internal XSTO paper-trading data, OMXSGI and historical data

> Hello,
>
> I am requesting a quote and product confirmation for a private,
> internal paper-trading and research system. There is no external
> distribution and no broker order routing or live trading in this
> phase.
>
> Scope:
>
> - All active ordinary shares, preference shares and depositary
>   receipts on Nasdaq Stockholm (MIC XSTO), currently 416 instruments.
> - Official ISIN-to-ticker reference data and daily listing changes.
> - A quote and technical test specification for Nordic Equity Web API
>   Level 1 for internal non-display paper/research use, covering all
>   XSTO instruments with real-time data or an explicitly supported
>   delay of no more than 15 minutes.
> - As a lower-cost alternative, confirmation of an officially
>   supported automated server delivery contract for the public
>   15-minute delayed Nordic Equity pre- and post-trade files.
> - Best bid/offer and volumes, last trade, market/instrument status,
>   event/publication timestamps, corrections and backfill.
> - Exact OMXSGI total-return index coverage, intraday or delayed
>   levels and official daily close. Please state whether this exact
>   symbol and usage right are included in Nordic Equity Level 1; if
>   not, quote Nordic Index Web API separately.
> - Historical XSTO data, point-in-time reference membership,
>   corporate actions and historical OMXSGI for reproducible backtests.
>
> Please confirm the supported delivery method, product names,
> licensing classification, internal storage/retention rights, full
> XSTO and OMXSGI coverage, SLA/rate limits, test access, change
> notifications and an all-in price including connection, data,
> non-display, index, historical, setup and any other applicable fees.
> Please classify and price the use under both the currently effective
> rules and the Internal Basic/Standard/Premium structure effective
> September 1, 2026, because the agreement will cross that date.
> Please also state what would change if broker order routing is added
> in a later, separately approved phase.
>
> Nasdaq's public delayed-data page is accessible in a browser, but
> direct server requests have not been reliable. We will not use
> browser impersonation, so we need a documented server-supported
> endpoint or file delivery contract.
>
> Kind regards

## Källor

- <https://www.nasdaq.com/solutions/data/european-pricing-policies>
- <https://www.nasdaq.com/market-regulation/nordic/mifid-ii>
- <https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Exchange_Market_Data_Price_List_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Market_Other_Data_Products_Price_List_April_2026>
- <https://www.nasdaq.com/docs/data/Nasdaq_European_Markets_Data_Price_List_September_2026>
- <https://www.nasdaq.com/docs/data/Nasdaq-European-Other-Markets-Data-Products-Price-List-October-2026>
- <https://www.nasdaq.com/solutions/data/nasdaq-nordic-reference-data-files>
- <https://www.nasdaq.com/solutions/data/nasdaq-nordic-baltic-historicalview>
- <https://indexes.nasdaqomx.com/Index/Breakdown/OMXSGI>
