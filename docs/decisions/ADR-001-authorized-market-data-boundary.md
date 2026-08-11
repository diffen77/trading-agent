# ADR-001: Auktoriserad marknadsdatagräns

## Status

Accepterad

## Datum

2026-07-29

## Kontext

Trading Agent ska täcka alla relevanta aktier på Nasdaq Stockholm
(`XSTO`) och använda realtidsdata eller högst 15 minuter fördröjd data.
Beslut, paperfills, portföljvärdering och benchmark måste vara
reproducerbara. Projektet får inte förutsätta att en tekniskt åtkomlig
webbsida eller ett inofficiellt tickerformat också innebär rätt att
använda, lagra eller automatisera data.

ESMA FIRDS ger stabil instrumentidentitet med ISIN och MIC, men inte
Nasdaqs handelssymbol. Nasdaqs fria MiFID/MiFIR-filer ger 15 minuter
fördröjd pre- och post-trade-data, men den observerade webbtransporten
är inte ett publicerat eller driftstabilt server-API. `OMXSGI` är en
separat indexprodukt och får inte antas ingå i ett generellt
aktieabonnemang.

## Beslut

1. ISIN är primär instrumentidentitet. Det aktiva universet begränsas
   initialt till `XSTO`; First North, Spotlight och NGM är separata
   framtida beslut.
2. Nasdaqs tickeralias ska importeras från en licensierad Nordic Equity
   Reference-fil via Nasdaq Data Link Files/SFTP och kopplas till FIRDS
   med ISIN.
3. Intradagsdata får endast användas genom ett versionsstyrt
   provideravtal som är godkänt för exakt produkt, MIC, transport,
   användningsfall, lagring, delay och hela universets coverage.
4. Första alternativet är Nasdaqs 15-minutersfiler, men endast efter
   skriftlig bekräftelse av automatiserad serveråtkomst. Nordic Equity
   Web API Level 1 är den officiella reservvägen om delayed-transporten
   inte kan driftgodkännas.
5. `OMXSGI` kräver ett separat validerat indexavtal och en explicit
   coveragebekräftelse. Nordic Index Web API är första offertvägen.
6. Yahoo, hårdkodade tickers, gamla `prices`/`macro`-tabeller,
   browser-impersonation och lagrade fallbackpriser är inte tillåtna
   operativa datakällor.
7. Analys, ordervalidering, paperfills, exits, portföljvärdering och
   benchmark stoppar fail-closed när avtal, transport, börssession,
   coverage, provenance eller freshness inte kan bevisas.
8. Realtidsanalys och automatisk orderrouting behandlas som olika
   licensfall. Brokerkoppling får inte aktiveras innan rättigheter för
   `Non-Display Category 2` och det förregistrerade forward-benchmarket
   är operatörsgodkända.

## Alternativ som övervägdes

### Yahoo Finance

- Fördel: enkelt och billigt att prova.
- Nackdel: saknar avtalad full XSTO-coverage, tydlig server-SLA och den
  provenance som krävs för paperfills och revisionsbarhet.
- Avvisat som operativ källa och fallback.

### Hårdkodad tickerlista

- Fördel: liten initial implementation.
- Nackdel: missar noteringar, avnoteringar, namnbyten och
  instrumentklasser; kan inte bevisa point-in-time-universum.
- Avvisat.

### Automatisering av Nasdaqs publika rapportsida utan avtal

- Fördel: gratis 15-minutersfiler.
- Nackdel: observerad servertransport är instabil och URL-mönstret är
  inte ett publicerat API-kontrakt.
- Avvisat tills Nasdaq bekräftar en stödd serverväg och användningsrätt.

### Direkt ITCH/GCF

- Fördel: officiell realtid och bred data.
- Nackdel: större protokoll-, anslutnings-, drift- och licensbörda än
  papertrading-fasen behöver.
- Senare alternativ, inte första val.

### Tredjepartsdistributör

- Fördel: kan ge enklare API och samlad aktie-/indexhistorik.
- Nackdel: coverage, primär handelsplats, delay, lagringsrätt och
  non-display-rättigheter måste fortfarande bekräftas skriftligt.
- Tillåtet endast om samma provider-gate kan fyllas med verifierbar
  evidens.

## Konsekvenser

- Systemet kan vara korrekt implementerat men ändå visa `BLOCKED`
  tills riktiga avtal och filer finns. Det är avsiktligt.
- Referensdata, aktiekurser, indexdata, historik och corporate actions
  kan komma från olika avtal, men varje observation måste ha entydig
  provider- och kontraktsprovenance.
- Nordic Web API:s anslutningsavgifter är separata från data- och
  användningsavgifter. Prislistan som gäller från 1 april 2026 är ett
  planeringsunderlag, inte en bindande offert.
- Bytet av provider ska kunna göras utan att ändra tradinglogik; endast
  en ny adapter och ett nytt validerat provideravtal ska behövas.

## Officiella källor

- <https://www.nasdaq.com/market-regulation/nordic/mifid-ii>
- <https://www.nasdaq.com/solutions/data/nasdaq-nordic-file-delivery-service>
- <https://www.nasdaq.com/docs/Nasdaq_European_Data_Policies_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Exchange_Market_Data_Price_List_April_2026>
- <https://www.nasdaq.com/docs/Nasdaq_European_Market_Other_Data_Products_Price_List_April_2026>
- <https://www.nasdaq.com/solutions/nasdaq-genium-consolidated-feed>
