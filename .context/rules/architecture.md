# Architecture Rules — Trading Agent

## Stack
- Python backend, SQLite DB
- Dashboard: Next.js på 192.168.99.4:3020
- DB container: trading-agent-db :5435
- LLM: LM Studio på 192.168.99.19:1234 (qwen3-coder) — Mac måste vara på

## Mål
- 20 000 → 40 000 kr till 31 juli 2026
- Dashboard: https://trading.lediff.se

## Schema (CET)
- 07:00 morning analysis, 09:00 market open, 12:00 midday, 17:00 close, 22:00 evening

## Sell-regler
- Min 24h hålltid
- Stop-loss -5%, take-profit +10%
- Neutral outlook = håll kvar

## Kända problem
- Yahoo blockerar prod-IP → data via Mac-script scripts/trading-data-sync.py
- Telegram-notiser funkar inte från prod (nätverksproblem)
