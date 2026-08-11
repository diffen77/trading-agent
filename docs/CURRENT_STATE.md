# Aktuellt läge

Senast verifierat: 2026-08-11.

Detta dokument är projektets korta, aktuella lägesbild. `STATE.md` är den
historiska journalen och ska inte användas som ensam källa för dagens drift.

## Sammanfattning

- Systemet är en fail-closed papertradingplattform. Riktiga pengar och
  brokerkoppling är blockerade.
- Kanonisk kod finns på `main` i `diffen77/trading-agent` efter merge av
  [PR #10](https://github.com/diffen77/trading-agent/pull/10).
- Mergecommit är `6be95b57f472c5393b01044d52562fb17d7372c5`.
- PostgreSQL är system of record. Neo4j och Cortex är härledda projekt- och
  tradingminnen, inte ersättning för Git eller ledgern.

## Verifierad kod och release

Följande ändringar ingår nu i `main`:

- `dd8edb7`: öppningsrutinens grace beräknas från leverantörens nominella
  fördröjning och tillåtna lagg; fallet 09:20:45 är regressionstestat;
- `079400b`: images får OCI-labels för källa och Git-revision, och deploy
  verifierar dem före migrering;
- `f55ec2e`: fail-closed benchmark-preflight och schema 45, som binder
  leverantörsvalidering till exakt referenssnapshot och checksumma;
- `9f0439c`: checksummebunden leveranskontroll för licensierad historik,
  corporate actions, OMXSGI, kalender och användningsrätt.

På ett nybyggt PostgreSQL 16-schema 45 passerade 664 agenttester och 65
dashboardtester. Dashboardens produktionsbygge passerade. Den historiska
leveranskontrollens fem fokustester passerade efter den fulla körningen.
GitHub Actions-körning `31464024257` passerade på mergecommitten. Den
immutabla releasekörningen `31464131315` byggde och pushade revisionsmärkta
agent- och dashboard-images, attesterade deras proveniens och publicerade
release-manifestet. Ingen deployment startades.

## Verifierad staging

Staging kör på Kajen under `/srv/prod/staging/trader` och visas på
`https://trader.lediff.online`.

Senaste läsverifieringen visade:

- publik root: `401`;
- publik health: `200`;
- operations-API utan auth: `401`;
- tio Compose-tjänster: `running` och `healthy`;
- intern readiness: `READY`;
- databasschema: 44.

Staging har alltså inte nattens schema-45-kod. De nuvarande images saknar
OCI-revisionslabel. En första schema-45-release får därför inte göras som en
vanlig automatisk deploy: gammal schema-44-kod är inte en säker rollback efter
migreringen, och de gamla images kan inte styrkas med den nya provenienskedjan.

## Benchmark och lärandeloop

Benchmark-preflighten skiljer nu på kodmässig beredskap och operatörens
förregistrering. Den blockerar start vid bland annat saknad aktiv strategi,
ofärdig referenssnapshot, saknade separata quote-/OMXSGI-avtal och nivåer samt
en paper-ledger som inte är ren.

Den kontinuerliga kandidatloopen är redan aktivt säkerhetsdelad:

- utfall och kalibrering kan köras automatiskt dygnet runt;
- en statistiskt godkänd utmanarpolicy skapas endast som `DRAFT`;
- godkännande och aktivering kräver två uttryckliga operatörskommandon;
- den äldre textbaserade trade-reviewn skapar inga `learnings` innan styrd
  historisk marknadsdata har importerats.

Noll rader i den äldre `learnings`-tabellen innebär därför inte att
kandidatutfallen eller kalibreringen saknas. Observationer ska inte kallas
lärdomar innan de har korrekt historisk evidens.

## Källor i prioritetsordning

1. GitHub-commit och grön CI för kod och byggbarhet.
2. PostgreSQL-ledger, schema och append-only driftevidens för runtime.
3. Image-digest, revisionslabel och filchecksummor för deployment.
4. Neo4j Brain och Cortex för härledd kontext, beslut och tidigare lärdomar.
5. Daterade dokument och historiska taskloggar.

Vid konflikt ska den högre källan vinna och konflikten dokumenteras. Neo4j
och Cortex ska uppdateras efter bestående ändringar, men får aldrig innehålla
secrets eller `.env`-värden.

## Nästa beslut

Den prioriterade listan över det som kräver operatören eller en extern part
finns i [morgonens överlämning](MORNING_HANDOFF.md).
