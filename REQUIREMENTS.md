# Requirements

## V1 - MVP (This Milestone)

### Data Layer
- [x] Synka XSTO-aktier, preferensaktier och depåbevis från verifierbar
  ESMA FIRDS-källa med ISIN som identitet
- [ ] Komplettera instrumenten med licensierade Nasdaq-tickeralias
- [x] Fail-closed key-only SFTP-transport för den licensierade
  Nasdaq-referensfilen, avstängd tills avtal och host key är verifierade
- [x] Append-only entitlement-gate för avtal, intern användning,
  lagringsrätt, giltighet och exakt SFTP-host-key-fingerprint
- [x] Operatörsattribuerad validering och återkallelse av entitlement
  med lokal SHA-256-verifiering och utan manuell SQL
- [ ] Hämta licensierad realtidsdata eller officiell data med högst
  15 minuters fördröjning
- [x] Administrera pris- och indexavtal utan manuell SQL med exakt
  avtalschecksumma, lagringsrätt, operatörsattribution, återkallelse
  och fem sammanhängande officiella acceptanssessioner
- [x] Blockera trading vid stale, ofullständig eller felmappad data
- [x] Append-only pre-trade-lagring med exakt filproveniens,
  omstartscursor och paperfill mot verifierbar bid/ask
- [ ] Lagra historiska kurser i databas
- [ ] Hämta fundamentala data (P/E, P/B, EPS, utdelning)
- [x] Automatisk och idempotent referensdatasynk med freshness-gräns
- [x] Point-in-time-datakontrakt för historik, medlemskap och
  corporate actions

### Bolagsförståelse
- [ ] Databas över bolag med:
  - Vad bolaget gör (sektor, verksamhet)
  - Inputs (råvaror, valutor, etc)
  - Konkurrenter
- [ ] Manuell seed av top 50 bolag, sen utöka

### Omvärldsbevakning
- [ ] Råvarupriser (guld, olja, stål, koppar, etc)
- [ ] Valutor (EUR/SEK, USD/SEK)
- [ ] Nyhetsflöde (svenska finansnyheter)
- [ ] Makrodata (räntor, inflation)

### Agent Core
- [x] Daglig kalenderstyrd analysrutin med beständig kör- och
  fel-evidens
- [x] Strikt validerade köp/sälj-beslut med motivering och auditkedja
- [x] Deterministisk risk management (position, sektor, exits)
- [x] Operatörsnödstopp och dagslåst mark-to-market-förlustgräns
  för nya paperköp
- [x] Strict validering av AI-beslut
- [x] Trade-loggning med atomisk ledger

### Papertrade Engine
- [x] Simulerad portfölj (20 000 kr start)
- [x] Köp/sälj-execution med översäljningsskydd
- [x] Beräkna realiserad P&L och mark-to-market total
- [x] Track open positions
- [x] Exakt-en-gång-order och FIFO-lots för partiella försäljningar

### Lärande
- [x] Trade journal (hypotes → resultat → korrekt?)
- [x] Veckovis ledgerbaserad självgranskning
- [x] Kunskapsbas med validerade, renderbara lärdomar
- [x] Evidenslänkad strategijustering baserat på data
- [x] Versionsstyrd och hashverifierad strategi-/riskkonfiguration
- [x] Evidenslänkade ändringsförslag med separat operator approval
- [x] Atomisk aktivering; student/LLM kan inte ändra aktiva regler
- [x] Walk-forward-motor med nästa öppning, kostnader och benchmark
- [x] Förregistrerad forward-paper-gate mot OMXSGI med fryst release,
  modell, data, kostnader och godkännandekriterier
- [x] Ledgerstyrda paperfills med avgift, spread, slippage och
  netto-FIFO-P&L
- [x] Append-only incidentlogg, ledgerhärledda stängda affärer och
  intradags-drawdown i forward-utvärderingen
- [x] Automatisk post-close-observatör från validerad aktie- och
  OMXSGI-feed
- [ ] Slutför minst 252 XSTO-sessioner och 30 stängda paper-affärer
- [ ] Kör mot operatörsvaliderad licensierad XSTO-fullhistorik

### Dashboard
- [x] Autentiserad portföljöversikt utan fabricerade fallbackvärden
- [x] Lista trades med motivering och verifierbar auditkedja
- [x] Grafer (portföljutveckling, win/loss)
- [ ] Bolagsrapporter (varför agenten gillar/ogillar)

### Infrastructure
- [x] Docker Compose med explicit migrationssteg
- [x] CI med riktig PostgreSQL och blockerande tester
- [x] Manuellt godkänd, digest-pinnad deploypipeline med rollback
- [x] Säker, explicit runtime-profilaktivering utan shell-source av
  secrets
- [x] Produktionssecrets via validerade, ägarlåsta filer och
  tjänstespecifika read-only-mounts
- [ ] Aktivera och öva pipeline på avsedd server
- [x] Marknadskalenderstyrd schemaläggning i `Europe/Stockholm`

## V2 - Future
- [ ] Saxo Bank integration (riktig orderbok)
- [ ] Mer avancerade strategier
- [x] Point-in-time walk-forward-backtesting utan look-ahead
- [x] Trade-notifikationer med beständig audit; verklig
  mottagarleverans återstår att öva i staging

## Out of Scope
- Riktig trading (V1 är 100% papertrading)
- Options/derivat
- Internationella börser
- High-frequency trading
