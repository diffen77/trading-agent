# 🤖 Trading Agent

AI-driven papertrading på Stockholmsbörsen.

## Vision

En agent som:
- Förstår samband mellan makro och bolag
- Fattar köp/sälj-beslut med motivering
- Lär sig av sina trades över tid
- Visar allt transparent på en dashboard

## Quick Start

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f agent

# Dashboard
open http://localhost:3001
```

## Architecture

```
├── agent/           # Python trading agent
│   └── src/
│       ├── data/    # Yahoo Finance + Database
│       └── core/    # Analyzer + Trader
├── dashboard/       # Next.js dashboard
└── db/              # PostgreSQL schema
```

## Status

🚧 Under active development

- [x] Project structure
- [x] Database schema
- [x] Yahoo Finance integration
- [x] Basic dashboard
- [ ] Full analysis engine
- [ ] Learning loop
- [ ] Company reports

## Stack

- **Data**: Yahoo Finance (free, 15 min delay)
- **Agent**: Python + OpenClaw
- **Database**: PostgreSQL
- **Dashboard**: Next.js + Tailwind
- **Deploy**: Docker Compose

## Owner

Built for Härryda BBQ / Diffen
