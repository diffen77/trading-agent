# Arbetsströmmar

Denna lista visar vad som är klart, vad som blockerar nästa steg och vad som
medvetet väntar. Detaljerade framtidskrav ligger kvar i `ROADMAP.md`.

| Prioritet | Arbetsström | Status | Nästa verifierbara resultat |
| --- | --- | --- | --- |
| P0 | Recovery och GitHub | Klart på draft-branch | Granska PR #10 före merge till `main` |
| P0 | Öppningsrutin | Blockerad av timingfel | Provider-medveten grace/gate med test för 09:20:45 |
| P0 | Releaseproveniens | Delvis klar | Git-revision som OCI-label och verifierad commit→digest-kedja |
| P0 | Driftstatus | Healthy grundsystem, aktiv blockerare | Operations ska inte bära kvar ett falskt permanent missat öppningslarm |
| P1 | Dokumentation och projektminne | Pågår | Aktuellt läge, arkitektur, workstreams, Cortex och Neo4j i synk |
| P1 | Forward benchmark | Pågår långsiktigt | 252 XSTO-sessioner och minst 30 stängda affärer enligt kontrakt |
| P1 | Tradinggraf | Shadow-only | Oberoende evidens före eventuell operativ aktivering |
| P2 | Broker och riktiga pengar | Blockerat | Separat beslut först efter uppfyllda P0/P1-kriterier |

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

