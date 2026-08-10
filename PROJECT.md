# Trading Agent

## Vision
En AI-agent som papertradar svenska aktier på Stockholmsbörsen, lär sig över tid, och visar sina beslut med tydliga motiveringar.

## Mål
- Papertrading med 20 000 kr simulerat kapital
- Mäta nettoresultat och risk mot ett förutbestämt XSTO-benchmark
- Prioritera kapitalbevarande och reproducerbarhet framför ett
  orealistiskt avkastningsmål
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
- **Data**: kontraktsstyrda provider-interface för XSTO och OMXSGI
- **Agent**: Python + lokal OpenAI-kompatibel modell eller Anthropic
- **Database**: PostgreSQL (trades, lärdomar, bolagsdata)
- **Dashboard**: Next.js
- **Deploy**: Docker på Kajen; staging visas på
  `https://trader.lediff.online`

## Constraints
- Ingen broker-integration (papertrades simuleras)
- Alla relevanta stamaktier, preferensaktier och depåbevis på Nasdaq
  Stockholm (`XSTO`); övriga svenska handelsplatser kräver separat beslut
- Marknadsdata måste vara licensierad realtid eller högst 15 minuter
  fördröjd och freshness-validerad
- Nyheter, rapportkalender, fundamenta och övrig makro får inte påverka
  AI-beslut utan auktoriserad källa och spårbar proveniens
- Agenten följer `Europe/Stockholm` och officiell handelskalender
- Strategi- och riskregler är versionsstyrda; AI får endast föreslå
  beslut och observationer, aldrig godkänna eller aktivera nya regler
- Riktiga pengar är blockerade tills papertrading och benchmark är
  verifierade
- Dashboarden är lokal och autentiserad; extern åtkomst kräver TLS

## Success Metrics
- Agenten fattar beslut med tydlig motivering
- Dashboard visar trades + performance
- Lärande-loop fungerar (veckovis granskning)
- Portföljutveckling synlig över tid

## Owner
Diffen (Härryda BBQ / lokalaproducenter.se)
