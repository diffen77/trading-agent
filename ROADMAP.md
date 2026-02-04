# Roadmap

## Milestone 1: MVP

### Phase 1: Foundation
**Goal**: Basic infrastructure + data pipeline
- Setup Docker Compose (Python, PostgreSQL, Next.js)
- Yahoo Finance integration
- Fetch + store all Stockholm stocks
- Database schema for stocks, prices, trades
- CI/CD pipeline

### Phase 2: Intelligence Layer
**Goal**: Agent can understand companies and macro
- Bolagsdatabas (sektor, inputs, verksamhet)
- Omvärldsbevakning (råvaror, valutor)
- Kopplingslogik (input-price → company impact)
- News fetching + basic sentiment

### Phase 3: Trading Engine
**Goal**: Agent can make and track paper trades
- Papertrade engine (buy/sell simulation)
- Portfolio tracking
- Trade logging with reasoning
- Risk management rules

### Phase 4: Analysis & Decisions
**Goal**: Agent makes autonomous decisions
- Daily analysis routine
- Decision engine (buy/sell/hold)
- Reasoning generation ("varför")
- Confidence scoring

### Phase 5: Learning Loop
**Goal**: Agent improves over time
- Trade journal with outcome tracking
- Weekly self-review routine
- Knowledge base for learnings
- Strategy adjustment mechanism

### Phase 6: Dashboard
**Goal**: Visibility into agent's work
- Portfolio overview
- Trade history with reasoning
- Performance charts
- Company reports

### Phase 7: Polish & Deploy
**Goal**: Production-ready
- Error handling
- Monitoring
- Documentation
- Final deploy to docker server

---

## Status
- [x] Project initialized
- [ ] Phase 1: Foundation
- [ ] Phase 2: Intelligence Layer
- [ ] Phase 3: Trading Engine
- [ ] Phase 4: Analysis & Decisions
- [ ] Phase 5: Learning Loop
- [ ] Phase 6: Dashboard
- [ ] Phase 7: Polish & Deploy
