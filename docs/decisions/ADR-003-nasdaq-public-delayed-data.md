# ADR-003: Nasdaq Nordic publik fördröjd pre-trade-data

## Status

Accepterad för intern papertrading

## Datum

2026-07-31

## Kontext

Trading Agent behöver verklig intradagsdata för Nasdaq Stockholm utan
inofficiella webbskrapor, gissade tickeralias eller riktiga
mäklarorder. Nasdaq publicerar maskinläsbara pre-trade-CSV-filer varje
minut med cirka 15 minuters fördröjning. Nasdaq anger att
icke-kommersiell användning är kostnadsfri.

Filerna identifierar instrument med ISIN och innehåller köp-/säljsida,
pris, valuta, kvantitet, orderantal, MIC, handelssystem, handelsfas,
marknadstid och publiceringstid. De innehåller inte en auktoritativ
börsticker.

## Beslut

1. `market-sync` använder Nasdaqs publika pre-trade-katalog och
   download-endpoint för `EQUITY`.
2. Användningen är begränsad till intern analys och papertrading.
   Råfiler sparas inte; endast härledd, checksummebunden XSTO-evidens
   lagras.
3. Transporten tillåter bara HTTPS mot
   `tradereports.nasdaq.com`, fasta API-sökvägar, inga redirects,
   hårda storleksgränser och tidsgränser.
4. ISIN är stabil intern instrumentnyckel. En officiell ticker visas
   bara när en separat auktoritativ symbolmappning finns.
5. Parsern läser endast `XSTO` och CLOB. Parallella auktionssystem och
   rader utan CLOB-identitet används inte som exekverbar orderbok.
   CLOB-faser utanför kontinuerlig handel bevaras och blockerar fills.
6. Ett paperköp använder senaste ask och en papersäljorder senaste bid
   från exakt förseglad orderbok. Visad volym, spread, session,
   dataleverans och filproveniens kontrolleras i databasen.
7. En fil accepteras som live-evidens endast när observerad leverans är
   15–20 minuter. En historisk fil får parservalideras men får inte
   märkas färsk i efterhand.
8. Första synken och återstart filtrerar Nasdaqs katalog mot den
   versionsstyrda XSTO-kalendern och hoppar över för-/eftermarknadsfiler.
9. Full universumtäckning per minut krävs inte eftersom illikvida
   instrument inte alltid uppdateras. Handel tillåts bara i instrument
   med en färsk, tvåsidig, exekverbar CLOB-bok.
10. OMXSGI och officiella tickeralias är separata datakontrakt. De
    blockerar inte starten av intern papertrading med det publika
    pre-trade-flödet, men en laglig benchmark ska väljas separat.

## Verifiering

- Schema 33 migrerar från en tom databas och från stagingens schema 32.
- En verklig nordisk minutfil från 2026-07-31 17:29 svensk tid
  parserades genom hela filen.
- Filen innehöll både CLOB, PATS och rader utan handelssystem;
  parsern behöll bara CLOB-evidens.
- En eftermarknads-backfill avvisades som live-evidens eftersom
  leveranstiden var äldre än 20 minuter.
- Staging har 416 aktiva XSTO-instrument, 416 ISIN-baserade
  bolagskopplingar och ett verifierat publikt policykontrakt.
- Första kontinuerliga livefilen och därmed provideracceptansen sker
  automatiskt under nästa öppna XSTO-session.

## Konsekvenser

Systemet kan starta papertrading utan betald marknadsdatatjänst, men
kan bara agera när en färsk tvåsidig orderbok faktiskt finns. Ingen
historisk data, benchmark, nyhetsfeed, fundamentaldata eller
brokerintegration följer automatiskt av beslutet.

Ändras katalogen, CSV-schemat, villkoren, fördröjningen, MIC,
handelssystemet eller filintegriteten stoppar importen fail-closed.
Systemet får inte falla tillbaka till Yahoo, skrapade mäklarsidor,
gamla priser eller syntetiska fills.

## Officiella källor

- <https://tradereports.nasdaq.com/shares/trade-reports/pre-trade>
- <https://tradereports.nasdaq.com/api/regulatory/trade-reports?type=PRE_TRADE&assetClass=EQUITY>
- <https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026>
