# Aktuellt läge

Senast verifierat: 2026-08-10.

Detta dokument är projektets korta, aktuella lägesbild. `STATE.md` är den
historiska journalen och ska inte användas som ensam källa för dagens drift.

## Sammanfattning

- Systemet är en fail-closed papertradingplattform. Riktiga pengar och
  brokerkoppling är blockerade.
- Kanonisk arbetsbranch är `recovery/schema44-2026-08-10` i
  `diffen77/trading-agent`.
- Draft-PR är [#10](https://github.com/diffen77/trading-agent/pull/10).
- `main` innehåller ännu inte recovery-arbetet och får därför inte användas
  som aktuell kodkälla före granskad merge.
- PostgreSQL är system of record. Neo4j och Cortex är härledda projekt- och
  tradingminnen, inte ersättning för Git eller ledgern.

## Verifierad kod och CI

GitHub Actions-körning `31416348008` passerade på recovery-branchen:

- 650 agenttester passerade mot PostgreSQL 16;
- migrationerna gick igenom till schema 44;
- 64 dashboardtester passerade utan skips;
- produktionsaudit rapporterade noll sårbarheter;
- Next.js-produktionsbygget passerade;
- GitGuardian godkände branchen.

Releasekontraktet kräver nu samma schema som senaste migrationsfilen. Ett
regressionstest stoppar framtida drift mellan migrationsnummer och
release-manifest.

## Verifierad staging

Staging kör på Kajen under `/srv/prod/staging/trader` och visas på
`https://trader.lediff.online`.

- publik root: `401`;
- publik health: `200`;
- operations-API utan auth: `401`;
- tio Compose-tjänster: `running` och `healthy`;
- intern readiness: `READY`;
- databasschema: 44;
- fyra centrala agentfiler har samma SHA-256 i stagingcontainern och i
  recovery-branchen.

De körande images saknar en OCI-label med Git-revision. Filchecksummorna ger
stark delverifiering, men exakt commit-till-image-spårbarhet är därför ännu
inte fullständig.

## Aktuell blockerare

Det autentiserade operations-API:t rapporterade `BLOCKED` med:

- `XSTO_SESSION_NOT_OPEN`, vilket är korrekt efter börsstängning;
- `SCHEDULED_ROUTINE_MISSED`, vilket är ett kvarstående riktigt driftfel.

Öppningsrutinen har 20 minuters grace-period. Den fördröjda Nasdaq-strömmen
skapade den första giltiga orderboken 09:20:45, efter att rutinen redan hade
markerats permanent missad 09:20:30. Nästa beteendeändring ska göra
öppningsrutinen provider-medveten och regressionstesta detta exakta fall.

## Källor i prioritetsordning

1. GitHub-commit och grön CI för kod och byggbarhet.
2. PostgreSQL-ledger, schema och append-only driftevidens för runtime.
3. Image-digest, revisionslabel och filchecksummor för deployment.
4. Neo4j Brain och Cortex för härledd kontext, beslut och tidigare lärdomar.
5. Daterade dokument och historiska taskloggar.

Vid konflikt ska den högre källan vinna och konflikten dokumenteras. Neo4j
och Cortex ska uppdateras efter bestående ändringar, men får aldrig innehålla
secrets eller `.env`-värden.

## Nästa säkra steg

1. Granska draft-PR #10 och behåll den omärkt som draft tills öppningsfelets
   omfattning är beslutad.
2. Laga öppningsrutinen test-first i en separat commit.
3. Lägg Git-revision och image-digest i releaseevidensen.
4. Kör full CI och staging-smoke igen.
5. Merg:a först därefter recovery-branchen till `main`.

