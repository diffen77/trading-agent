# ADR-002: Inköpsväg för XSTO-marknadsdata

## Status

Delvis ersatt av
[ADR-003](ADR-003-nasdaq-public-delayed-data.md). Den betalda
referens-, Level 1-, index- och historikvägen kvarstår som reserv och
för de datamängder som den publika feeden inte löser.

## Datum

2026-07-30

## Kontext

Projektet har ett verifierat identitetsuniversum med 416 aktiva
aktieinstrument på Nasdaq Stockholm (`MIC XSTO`), men saknar
Nasdaq-tickeralias och ett driftgodkänt prisflöde. Papertrading kräver
realtidsdata eller data med exakt 15 minuters nominell fördröjning,
full provenance och ett separat benchmarkflöde för `OMXSGI`.

Den fria MiFID/MiFIR-sidan publicerar pre- och post-trade-filer, men
direkta serveranrop har två dagar i rad återställts eller
tidsgränsats. Webbläsaren använder ett observerat endpointmönster bakom
webbapplikationen, men det är inte ett publicerat eller versionsstyrt
serverkontrakt.

Post-trade-data räcker inte som ensam prisfeed för hela universet.
Illikvida aktier kan sakna en färsk affär trots att marknaden är öppen.
Paperexekvering och freshness behöver därför minst Level 1-pre-trade
med bästa köp/sälj, tillsammans med post-trade, instrumentstatus och
marknadstidsstämplar.

En riktig pre-trade-minut har nu verifierats. Den nordiska filen var
cirka 165 MB med 1,39 miljoner rader och är en ordnad förändringsström,
inte en komplett snapshot. En strikt strömparser och
orderboksreducerare finns, men de är avsiktligt frikopplade från den
ännu oavtalade webbtransporten. Reduceraren tar endast
parserutfärdade, exakt minutbundna batcher med lokalt råbyteshash och
kanoniskt hashat referensuniversum. En separat cursor bevarar
kontinuitet genom en minut utan XSTO-uppdateringar. Råfilshashen är
innehållsintegritet, inte en
Nasdaq-signatur eller autentiserad transportproveniens.

## Föreslaget beslut

1. Beställ **Nordic Equity Reference data files** för officiell
   ISIN–ticker-mappning.
2. Be Nasdaq i första hand bekräfta en dokumenterad, automatiserbar
   servertransport för intern användning av de fria
   15-minutersfilerna, inklusive både pre- och post-trade.
3. Om en sådan transport inte kan avtalas eller klara acceptanstestet,
   använd **Nordic Equity Web API Level 1** för intern papertrading
   klassificerad som `Non-Display Category 1`.
4. Beställ `OMXSGI` som ett separat indexavtal. **Nordic Index Web
   API** är första offertvägen, men får bara väljas efter skriftlig
   bekräftelse att exakt symbol och total-return-serie ingår.
5. Begär en separat historikoffert för point-in-time-universum,
   OHLCV/tickdata, corporate actions och historisk `OMXSGI`. Ett
   HistoricalView-abonnemang får inte antas täcka allt detta.
6. Behåll orderrouting och riktiga pengar utanför avtalet och
   implementationen. En sådan ändring kräver en ny ADR, ny
   licensklassificering och ett godkänt forward-benchmark.

Det färdiga inköps- och acceptansunderlaget finns i
[`docs/market-data-procurement.md`](../market-data-procurement.md).

## Varför beslutet fortfarande är föreslaget

Det finns ännu ingen bindande offert, skriftlig transportbekräftelse
eller provleverans. Därför får inget provideravtal ändras från `DRAFT`
till `VALIDATED` och ingen prisfeed aktiveras.

## Tekniska konsekvenser

- Nuvarande `market-sync` är hårdkopplad till
  `nasdaq-nordic`, `delayed-post-trade-equity` och `PUBLIC_CSV`.
  Vald Web API-, SFTP- eller distributörsväg kräver en ny adapter och
  provider-factory innan den kan köras.
- Pre-trade och post-trade måste normaliseras till samma
  provideroberoende datakontrakt utan att tradinglogiken känner till
  leverantörens transport.
- Migration 025 ger pre-trade-siduppdateringar ett eget
  transportoberoende append-only-schema som bevarar radsekvens,
  explicita raderingar, filproveniens, restart-cursor och materialiserad
  bästa bid/ask. `market_quotes` återanvänds inte.
- Paperfills mot Level 1 kräver senaste förseglade state, senaste
  PASSED-validering, öppen XSTO-session, exakt exekverbar sida och
  kvarvarande visad volym. Ett körande forward-experiment fortsätter
  fail-closed på den befintliga quote-/kostnadsmodellen tills en separat
  Level 1-kostnadspolicy godkänts.
- Den valda transportadaptern måste binda filnamn, rapportminut,
  råbyteshash och referenssnapshot till TLS-/manifestmetadata som inte
  självrapporteras av parseranroparen.
- Den fulla nordiska 165 MB-filen får inte lagras som ett PostgreSQL-
  `BYTEA` varje minut. Full källchecksumma och en kanonisk strömmad
  `XSTO`-delmängd eller avtalad objektlagring ska användas först när
  lagringsrätten är bekräftad.
- Nuvarande all-or-nothing-gate för färska quotes över hela universet
  måste verifieras mot verklig Level 1-data. Om handelsstopp, auktion
  eller tom orderbok gör full samtidighet omöjlig ska dessa tillstånd
  modelleras explicit; det instrument som saknar verifierbart pris får
  aldrig handlas eller värderas med fallback.
- Den valda lösningen måste fortsätta använda provider-gaten för
  avtalsstatus, coverage, alias, provdata och observerad leveranstid.

## Alternativ som inte väljs nu

### Browser-impersonation

Avvisas eftersom webbflödets interna endpoint, cookies och
bot-skydd inte utgör ett stabilt eller avtalat server-API.

### Enbart post-trade

Avvisas eftersom senaste affär kan vara äldre än freshnessgränsen för
illikvida instrument och inte ger en verifierbar spread.

### Direkt ITCH/GCF

Kan ge officiell realtid men innebär större protokoll-, drift- och
licensbörda än papertrading-fasen behöver. Det är en senare reservväg.

### Tredjepartsdistributör

Tillåts som reserv om distributören skriftligen bekräftar full XSTO-,
`OMXSGI`-, lagrings- och non-display-rätt och klarar samma
acceptanskriterier.

## Omprövning

ADR:n kan ändras till `Accepterad` först när operatören har godkänt en
all-in-offert och en provleverans har klarat acceptanstestet. Den ska
omprövas vid extern distribution, fleranvändardrift, brokerintegration,
produktbyte eller ändrade Nasdaq-policyer.

## Officiella källor

- <https://www.nasdaq.com/solutions/data/european-pricing-policies>
- <https://www.nasdaq.com/market-regulation/nordic/mifid-ii>
- <https://tradereports.nasdaq.com/shares/trade-reports/pre-trade>
- <https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Exchange_Market_Data_Price_List_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Market_Other_Data_Products_Price_List_April_2026>
- <https://www.nasdaq.com/solutions/data/nasdaq-nordic-reference-data-files>
- <https://www.nasdaq.com/solutions/data/nasdaq-nordic-baltic-historicalview>
- <https://indexes.nasdaqomx.com/Index/Breakdown/OMXSGI>
