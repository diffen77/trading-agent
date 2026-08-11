# Aktuellt läge

Senast verifierat: 2026-08-11.

Detta dokument är projektets korta, aktuella lägesbild. `STATE.md` är den
historiska journalen och ska inte användas som ensam källa för dagens drift.

## Sammanfattning

- Systemet är en fail-closed papertradingplattform. Riktiga pengar och
  brokerkoppling är blockerade.
- Kanonisk kod finns på `main` i `diffen77/trading-agent` efter merge av
  [PR #20](https://github.com/diffen77/trading-agent/pull/20) och
  [PR #21](https://github.com/diffen77/trading-agent/pull/21).
- Deployad staging-release är
  `7bbf9f6971c14e0e317df1ea170a21345d58f805`.
- PostgreSQL är system of record. Neo4j och Cortex är härledda projekt- och
  tradingminnen, inte ersättning för Git eller ledgern.

## Verifierad kod och release

Följande dag-ett-förbättringar ingår nu i `main`:

- `dd77180`: Nasdaq-baserad XSTO-sektorklassificering med validerad täckning,
  källproveniens och separat synktjänst;
- `7d9214f`: kandidatutfall märks mot den faktiska tillgängliga fördröjda
  orderboksminuten inom ett strikt tvåminutersfönster;
- `0a26681`: kontinuerligt paper-lärande, automatisk policyprövning och
  realistiska standardkostnader för nya affärer i schema 48;
- `8805c15`: stagingdeploy ägs av `diffen77/plattform-deploy`, använder
  self-hosted runner på Kajen och kräver ett CI-utlöst digestbevis.

På ett nybyggt PostgreSQL 16-schema 48 passerade 692 agenttester, 67
dashboardtester och 27 fokuserade releasetester. Dashboardens
produktionsbygge och en lokal kontroll av den slutliga agentimagen passerade.
GitHub Actions-körning `31538423956` passerade på aktuellt main-head. Den
immutabla releasekörningen `31538592727` byggde och pushade revisionsmärkta
agent- och dashboard-images, attesterade deras proveniens och publicerade
plattformens digestbevis.

## Verifierad staging

Staging kör på Kajen under `/srv/prod/staging/trader` och visas på
`https://trader.lediff.online`.

Senaste läsverifieringen visade:

- publik root: `401`;
- publik health: `200`;
- operations-API utan auth: `401`;
- elva långlivade Compose-tjänster: `running` och `healthy`;
- intern readiness: `READY`;
- databasschema: 48;
- agent-image:
  `sha256:34bf65bf6c50eada1c5f1864a2db25123dd146d00f5c9c8612ea00702c95d00f`;
- dashboard-image:
  `sha256:8833ebb6c3f8edfcc05d83c58fef6d10ec9cac2122fbf43ba27bb36f888922d5`;
- båda images har OCI-revision
  `7bbf9f6971c14e0e317df1ea170a21345d58f805`.

Plattformsdeploy `31538805321` validerade nyttolasten, sparade föregående
imagefamiljer för rollback, hämtade digestlåsta images, migrerade, startade om
och verifierade extern health. Efterkontrollen visade inga öppna driftlarm.
Trading-readiness var korrekt `NOT_READY` enbart därför att XSTO-sessionen var
stängd; monitorn kräver inte marknadsdata utanför en öppen session.

Den aktiva XSTO-mappningen innehåller 416 bolag. Alla har en sektoretikett och
412 har färsk, verifierad Nasdaq-proveniens, vilket motsvarar 99,04 procent.
Den kontinuerliga lärandeloopen hade 2 026 lyckade körningar; den senaste
slutfördes efter deploymenten. Ledgern innehöll 8 876 kandidatprediktioner,
8 355 tidsbundna entries, 21 350 märkta utfall och nio paper-affärer.

Benchmark-readiness körs fortsatt fail-closed. Standardmodellen för nya
paper-affärer använder faktisk top-of-book, 25 baspunkters avgift, minst en
krona och fem baspunkters konservativ slippage. De kvarvarande blockerarna
gäller främst styrd historisk data, en ren benchmark-ledger och godkänd
förregistrering; de är inte driftfel i releasen.

## Benchmark och lärandeloop

Benchmark-preflighten skiljer nu på kodmässig beredskap och operatörens
förregistrering. Den blockerar start vid bland annat saknad aktiv strategi,
ofärdig referenssnapshot, saknade separata quote-/OMXSGI-avtal och nivåer samt
en paper-ledger som inte är ren.

Den kontinuerliga kandidatloopen är redan aktivt säkerhetsdelad:

- utfall och kalibrering kan köras automatiskt dygnet runt;
- en utmanarpolicy aktiveras automatiskt i paper trading först när den har
  klarat det tidsordnade framåttestet;
- efter aktivering följs den mot moderpolicyn under minst tre fullständiga
  handelssessioner och minst 100 märkta utfall per policy;
- moderpolicyn återställs automatiskt som en ny, spårbar policyversion om den
  slår den aktiva policyn med minst två baspunkter och minst hälften av dess
  utfall är positiva;
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
