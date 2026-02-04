# Trading Agent

## Vision
En AI-agent som papertradar svenska aktier på Stockholmsbörsen, lär sig över tid, och visar sina beslut med tydliga motiveringar.

## Mål
- Papertrading med 20 000 kr simulerat kapital
- Sikta på att dubbla på 6 månader (ambitiöst lärandemål)
- Agenten ska förstå samband (makro → bolag)
- Transparens: varje trade har en "varför"-rapport

## Core Concept
Agenten är inte en teknisk robot som tittar på RSI. Den är en analytiker som:
1. Förstår vad bolag gör och vad deras inputs är
2. Bevakar omvärlden (råvaror, valutor, nyheter)
3. Ser kopplingar (guld ner + bolaget köper guld = bra)
4. Fattar beslut med motivering
5. Lär sig av sina trades

## Tech Stack
- **Data**: Yahoo Finance (gratis, 15 min delay)
- **Agent**: Python + OpenClaw sub-agent (Sonnet)
- **Database**: PostgreSQL (trades, lärdomar, bolagsdata)
- **Dashboard**: Next.js
- **Deploy**: Docker på 192.168.99.2

## Constraints
- Ingen broker-integration (papertrades simuleras)
- Hela Stockholmsbörsen (~400 aktier)
- Agenten körs på schema (07:00, 09:00, 12:00, 17:30, 22:00)

## Success Metrics
- Agenten fattar beslut med tydlig motivering
- Dashboard visar trades + performance
- Lärande-loop fungerar (veckovis granskning)
- Portföljutveckling synlig över tid

## Owner
Diffen (Härryda BBQ / lokalaproducenter.se)
