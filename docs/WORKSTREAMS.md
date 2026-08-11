# Arbetsströmmar

Denna lista visar vad som är klart, vad som blockerar nästa steg och vad som
medvetet väntar. Detaljerade framtidskrav ligger kvar i `ROADMAP.md`.

| Prioritet | Arbetsström | Status | Nästa verifierbara resultat |
| --- | --- | --- | --- |
| P0 | Recovery och GitHub | Mergad till `main`, CI och release gröna | Bevara mergecommit och releasekörning i nästa deploymentbevis |
| P0 | Öppningsrutin | Fixad och regressionstestad | Verifiera nästa öppna XSTO-session efter release |
| P0 | Releaseproveniens | Implementerad | Första officiella image verifieras commit→digest i staging |
| P0 | Schema 45-transition | Beslut krävs | Snapshot, underhållsfönster och explicit backout för första release |
| P1 | Benchmark-preflight | Implementerad lokalt | Kör mot staging efter schema-45-release och lös rapporterade blockerare |
| P1 | Historisk data | Leveransgate klar, data saknas | Välj licens/produkt, lägg sampleverans och bygg formatadapter |
| P1 | Kontinuerligt lärande | Automatisk evidens, aktivering och återställning i paper trading | Fortsätt samla sessioner och följ policyövergångarnas evidens |
| P1 | Dokumentation och projektminne | Synkat efter merge | Uppdatera efter nästa bestående driftbeslut |
| P1 | Forward benchmark | Blockerad av data och beslut | Ren ledger, frysta antaganden och godkänd förregistrering |
| P1 | Tradinggraf | Shadow-only | Oberoende evidens före eventuell operativ aktivering |
| P2 | Broker och riktiga pengar | Avsiktligt blockerad | Separat beslut först efter godkänt forward-benchmark |

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
