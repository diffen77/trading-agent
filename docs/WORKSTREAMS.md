# Arbetsströmmar

Denna lista visar vad som är klart, vad som pågår och vad som medvetet väntar.
Genomförandeordning och acceptanskriterier finns i
[ROADMAP.md](../ROADMAP.md).

| Prioritet | Arbetsström | Status | Nästa verifierbara resultat |
| --- | --- | --- | --- |
| P0 | GitHub, release och staging | `edd6bd2` har grön CI, immutable release och lyckad deploy | Bevara commit→digest→runtime-bevis vid varje ändring |
| P0 | Säker driftinsyn | Implementerad och lokalt verifierad | Provisionera operations-token och verifiera efter release |
| P0 | Sann jobbhälsa | Stabil felkod, retry, ledgerbevis och larm är lokalt verifierade | Verifiera nästa modellfel i staging utan att framkalla fel |
| P0 | Neo4j | Backloglarm och smal central statusbrygga är lokalt verifierade | Aktivera bryggan efter token och release |
| P1 | Beslutstratt | Full tratt och stabila reason codes är lokalt verifierade | Följ första kompletta stagingdygnet |
| P1 | Aktivt lärande | Exploration, segmentering, parent-jämförelse och rollback finns | Samla forward-utfall per segment |
| P1 | Kapitalrotation | Opportunity cost och rotationskvalitet mäts | Utvärdera efter fler faktiska rotationer |
| P1 | Kostnadsfri forward-data | Fördröjd Nasdaq-ström samlas | Bygg sammanhängande återspelbar sessionshistorik |
| P1 | Löpande jämförelse | Kassa och toppsignal ingår som kostnadsfria baslinjer | Fortsätt separat slutligt OMXSGI-bevis |
| P1 | Automatisk rapport | Daglig och veckovis evidensrapport körs i kvällsrutinen | Verifiera första stagingrapporterna |
| P2 | Dashboard | Kontrollrummet är implementerat och lokalt byggverifierat | Visuell kontroll efter release med riktig data |
| P2 | Dokumentation | Runtime-SHA exponeras, deploy verifierar SHA och Brain speglar den | Aktivera statusbrygga och larma på stale data |
| P3 | Betald data, USA och broker | Avsiktligt senarelagt | Separata beslut först när paperbeviset motiverar dem |

## Arbetsregel

Varje beteendeändring ska landa som en liten separat commit med:

1. Cortex- och Neo4j-kontext läst före antaganden;
2. ett reproducerande test före buggrättning;
3. grön lokal relevant test/build;
4. push till GitHub och grön CI;
5. staging-smoke när ändringen påverkar runtime;
6. uppdaterat `CURRENT_STATE.md` när verifierat nuläge ändras.

Stora historiska dokument ska inte byggas på med ännu en odaterad
"current"-sektion. Verifierat nuläge hör hemma i `CURRENT_STATE.md`, framtida
arbete i `ROADMAP.md`, beslut i ADR:er och kronologi i `STATE.md`.
