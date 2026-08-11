# Roadmap

## Målbild

En verifierbar papertrading-agent för Nasdaq Stockholm där datakvalitet
och riskregler är deterministiska, alla beslut är spårbara och strategin
mäts mot benchmark innan riktig handel ens övervägs.

## P0 — Kapital- och dataintegritet

- [x] Sammanhängande migrationsflöde för tom och befintlig databas
- [x] Stoppa destruktiv legacy-seed från automatisk replay
- [x] Atomisk ledger med översäljningsskydd och realiserad P&L
- [x] Databasconstraints för kritiska handelsinvarianter
- [x] Strict validering av AI-svar och ordervärden
- [x] Deterministisk stop-loss, take-profit, trailing stop och tidsstopp
- [x] Operatörsägt nödstopp och dagslåst mark-to-market-förlustgräns
  för nya paperköp; riskreducerande sälj är fortsatt tillåtna
- [x] Hårda position-, sektor- och trendgränser
- [x] PostgreSQL-integrationstester och CI som faktiskt kan bli röd
- [x] Idempotency key och payload-fingerprint per beslut/order
- [x] FIFO-lot-modell och allokeringar för partiella försäljningar
- [x] Generativa egenskapstester för ledger och riskregler i
  deterministiskt CI-läge

## P0 — Marknadsdata och instrument

- [x] Officiell referensdatakälla för XSTO-identitet via ESMA FIRDS
- [x] Automatisk, idempotent snapshotimport med
  ISIN/MIC/valuta/status/instrumenttyp
- [ ] Komplettera instrumentregistret med licensierade Nasdaq-tickeralias
- [x] Bygg avstängd key-only Nasdaq Data Link Files/SFTP-adapter med
  pinnad host key, begränsad filstorlek och officiell XSTO-kalender
- [x] Bind varje Nasdaq-aliasimport till append-only avtals-,
  lagrings- och host-key-entitlement; återkallelse stoppar användning
- [x] Säker operatörs-CLI för validering, status och attribuerad
  återkallelse utan manuell SQL eller credentialimport
- [x] Välj officiell 15-minutersfeed: Nasdaq Nordic public delayed
- [x] Validera första publika Nasdaq-filen under öppen XSTO-session
- [x] Frys föreslagen inköpsväg, offertfrågor och acceptanstest för
  XSTO Level 1, `OMXSGI`, referensdata och historik
- [x] Verifiera verkligt Nasdaq pre-trade-format och implementera
  strikt strömparser samt gap- och sekvenssäker Level 1-reducerare
- [x] Bind varje Level 1-minut till parserutfärdad filidentitet,
  råbytesintegritet och kanoniskt hashat referensuniversum; bevara
  separat cursor även för tom minut och stoppa tidsregression,
  DST-tvetydighet, nollikviditet och transient korsad bok
- [x] Lagra pre-trade-batcher, ordnade siduppdateringar, cursor och
  bästa bid/ask append-only med exakt fil- och referensproveniens
- [x] Bind pre-trade-paperfills till senaste förseglade exekverbara
  bid/ask, öppen session och kumulativt återstående visad volym
- [x] Värdera öppna pre-trade-positioner från exakt auktoriserad
  orderboksstate och bevara samma proveniens i portföljsnapshot
- [x] Koppla Level 1-bid/ask till det körande forward-benchmarkets
  frysta kostnadsmodell
- [x] Fail-closed provider-gate för licens, servertransport, coverage
  och observerad leveranstid
- [x] Strikt provider-CLI med exakt avtalsfil, lagringsrätt,
  operatörsattribution, append-only återkallelse och fem sammanhängande
  officiella acceptanssessioner
- [x] Lagra event time, received time och source per datapunkt
- [x] Freshness-gate som blockerar beslut på gammal/ofullständig data
- [x] Atomiskt rådataarkiv, checksumma, synkaudit och gap tracking
- [x] Opt-in minutpollning bakom explicit XSTO-session
- [x] Europe/Stockholm och officiell handels-/helgdagskalender 2024–2026
- [ ] Versionssätt officiell kalender för 2027 innan årsskiftet
- [ ] Corporate actions och symbolbyten
- [x] Ta bort hårdkodad tickerlista och Yahoo som auktoritativ källa
- [x] Fail-closed, kontraktsbunden OMXSGI-signal för nya köp

## P0 — Säkerhet och drift

- [x] Fail-closed enkeloperatörs-auth för dashboard och data-API
- [x] Säkra databasfel utan fabricerad fallback-data
- [x] Lokal standardbindning för dashboard och PostgreSQL
- [ ] Sessionsauth, roller, CSRF och rate limits före fleranvändardrift
- [x] Produktionssecrets endast via låsta runtime-filer och
  tjänstespecifika Docker secret-mounts
- [x] Health/readiness för databas, migration, agent, modell och datakälla
- [x] Dashboard-readiness mot schema version 35 med synligt
  referensdata-entitlement
- [x] Gated deployment av exakt CI-testade, digest-pinnade images
- [x] Schema-gated image-rollback verifierad mot simulerad Docker-värd
- [x] Strikt runtime-profilval utan shell-source; avstängda
  dataprofiler stoppas och monitorn är alltid aktiv
- [x] Verklig stagingdeploy med databasbackup, schema 35, healthchecks
  och återställningsbara image-/compose-artefakter
- [ ] Genomför separat rollbackövning och produktionsbeslut
- [x] Separat monitor med append-only, deduplicerade larm för stale
  data, missad körning, avvisad migration, ledgerfel och monitorfel
- [x] Aktiva driftlarm och senaste schemarutiner synliga i dashboardens
  handelsberedskap
- [x] Människoläsbara positionskort med bolagsnamn som rubrik och
  ISIN/ticker som sekundär identifierare

## P1 — Strategi och lärande

- [x] Starta löpande staging-papertrading och genomför de första
  proveniensbundna AI-köpen
- [x] Versionsstyrd strategi- och riskkonfiguration
- [x] Lärdomar måste konsumeras explicit av nästa strategiversion
- [x] Godkännandeflöde för strategiändring; modellen får inte ändra regler
  direkt
- [x] Point-in-time walk-forward-motor utan look-ahead eller
  survivorship bias
- [x] Exekveringsmodell för avgifter, spread, slippage, likviditet och
  corporate actions
- [ ] Importera och operatörsvalidera verklig fullhistorik för XSTO
- [x] Förregistrerat, append-only forward-paper-kontrakt mot OMXSGI
- [x] Fryst paperfill-kostnadsmodell, ledgerhärledd netto-P&L och
  append-only kritiska incidenter
- [x] Automatisk post-close-observatör från validerad aktie- och
  OMXSGI-feed
- [ ] Kör minst 252 XSTO-sessioner och 30 stängda affärer mot OMXSGI
- [x] Mätning av drawdown, turnover, hit rate och riskjusterad avkastning

## P1 — Produkt

- [x] Dashboard visar datakälla och freshness
- [x] Fullständig paper-audit från råfil och quote till AI-beslut,
  bokförd order och ledgerutfall
- [x] Portfölj, riskexponering, sektor och benchmark i samma vy
- [x] Driftstatus utan att maskera fel som lyckade svar
- [x] Besökarprioriterad informationsordning med portföljresultat och
  senaste handling först samt teknisk evidens utfällbar
- [x] Människoläsbara bolagsnamn i senaste handling, positioner och
  affärsjournal med ISIN/ticker kvar som sekundär revisionsidentitet
- [x] Människoläsbara bolagsnamn och svenska handlingar även i äldre
  AI-analyser; råa ISIN får inte visas som bolagsnamn
- [x] Separat och sanningsenlig lärandesammanfattning som inte hävdar
  lärdomar innan en stängd affär har utvärderats
- [ ] Auktoriserade och proveniensbundna källor för nyheter,
  rapportkalender, fundamenta och övrig makro innan de används av AI

## P2 — Broker och riktiga pengar

Blockerad tills alla P0/P1-kriterier är uppfyllda och papertrading har
slagit förutbestämt benchmark under en i förväg bestämd period.

- [ ] Read-only broker reconciliation
- [ ] Manuell orderbekräftelse
- [ ] Brokerkopplad kill switch och daglig förlustgräns
- [ ] Först därefter eventuell begränsad automatisk orderläggning
