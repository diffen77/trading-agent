# Arbetsströmmar

Denna lista visar vad som är klart, vad som blockerar nästa steg och vad som
medvetet väntar. Detaljerade framtidskrav ligger kvar i `ROADMAP.md`.

| Prioritet | Arbetsström | Status | Nästa verifierbara resultat |
| --- | --- | --- | --- |
| P0 | Recovery och GitHub | Mergad till `main`, CI och release gröna | Bevara PR #15–18, `45ad55d` och releasekörning `31491657173` |
| P0 | Öppningsrutin | Fixad och regressionstestad | Verifiera nästa öppna XSTO-session efter release |
| P0 | Releaseproveniens | Verifierad i staging | Bevara digest, OCI-revision, manifest och compose-checksumma |
| P0 | Schema 45-transition | Genomförd och verifierad | Behåll validerad dump och compose-snapshot |
| P0 | Paper-agentens sektorfrihet | Hård sektorgate borttagen och livebevisad av Attendo-köpet | Följ faktisk avkastning och koncentration som analysinformation |
| P0 | Paper-exekvering | Quote-retry och likviditetsanpassad delvis fyllnad deployade; 2 av 2 efterföljande AI-köp genomförda | Följ nästa order där önskad storlek faktiskt överstiger toppvolymen |
| P1 | Benchmark-preflight | Aktiv i staging | Lös rapporterade externa data- och förregistreringsblockerare |
| P1 | Kostnadsfri XSTO-period | Aktiv med cirka 15 minuter fördröjd data | Samla 6–12 månaders paper-evidens mot målet 30 procent |
| P1 | Historisk data | Leveransgate klar, inköp uppskjutet | Ta upp licens/produkt först när nyttan motiverar kostnaden |
| P1 | Kontinuerligt lärande | Automatisk evidens, aktivering och återställning i paper trading | Fortsätt samla sessioner och följ policyövergångarnas evidens |
| P1 | Dokumentation och projektminne | Cortex uppdaterat; Git/Neo4j synkas med slutbevis | Uppdatera efter nästa bestående driftbeslut |
| P1 | Forward benchmark | Blockerad av data och beslut | Ren ledger, frysta antaganden och godkänd förregistrering |
| P1 | Tradinggraf | Shadow-only | Oberoende evidens före eventuell operativ aktivering |
| P2 | USA-marknad | Avsiktligt uppskjuten | Separat beslut när XSTO-evidensen känns tillräcklig |
| P2 | Broker och riktiga pengar | Avsiktligt blockerad | Separat uttryckligt beslut efter paperutvärdering |

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
