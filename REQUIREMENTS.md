# Requirements

## V1 - MVP (This Milestone)

### Data Layer
- [ ] Hämta kursdata för hela Stockholmsbörsen via Yahoo Finance
- [ ] Lagra historiska kurser i databas
- [ ] Hämta fundamentala data (P/E, P/B, EPS, utdelning)
- [ ] Daglig uppdatering av data

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
- [ ] Daglig analysrutin
- [ ] Köp/sälj-beslut med motivering
- [ ] Risk management (max position, stop-loss)
- [ ] Trade-loggning med full kontext

### Papertrade Engine
- [ ] Simulerad portfölj (20 000 kr start)
- [ ] Köp/sälj-execution (logga till DB)
- [ ] Beräkna P&L per trade och totalt
- [ ] Track open positions

### Lärande
- [ ] Trade journal (hypotes → resultat → korrekt?)
- [ ] Veckovis självgranskning
- [ ] Kunskapsbas med lärdomar
- [ ] Strategijustering baserat på data

### Dashboard
- [ ] Portföljöversikt (värde, förändring)
- [ ] Lista alla trades med motivering
- [ ] Grafer (portföljutveckling, win/loss)
- [ ] Bolagsrapporter (varför agenten gillar/ogillar)

### Infrastructure
- [ ] Docker Compose setup
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Auto-deploy till 192.168.99.2
- [ ] Cron-jobb för agentens schema

## V2 - Future
- [ ] Saxo Bank integration (riktig orderbok)
- [ ] Realtidsdata
- [ ] Mer avancerade strategier
- [ ] Backtesting på historisk data
- [ ] Alerts/notifikationer vid trades

## Out of Scope
- Riktig trading (V1 är 100% papertrading)
- Options/derivat
- Internationella börser
- High-frequency trading
