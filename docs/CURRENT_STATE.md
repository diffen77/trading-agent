# Aktuellt läge

Senast verifierat: 2026-08-16.

Detta dokument är projektets korta, aktuella lägesbild. `STATE.md` är den
historiska journalen och ska inte användas som ensam källa för dagens drift.

## Sammanfattning

- Systemet är en fail-closed papertradingplattform. Riktiga pengar och
  brokerkoppling är blockerade.
- Kanonisk kod och GitHub `main` är
  `edd6bd2d90abd2829cea5537de16732f26d89924`.
- Samma revision har grön CI, immutable release och lyckad stagingdeploy.
- Agentens mål är 20 000 → 26 000 SEK, motsvarande 30 procents
  nettoavkastning på 6–12 månader. Målet och aktuell målprogress ingår i varje
  modellbeslut; det är ett styrmål, inte en garanti eller en affärskvot.
- PostgreSQL är system of record. Neo4j och Cortex är härledda projekt- och
  tradingminnen, inte ersättning för Git eller ledgern.
- Central Neo4j Brain på Neptun är nåbar och färsk. Tradinggrafen på Sjöboden
  är avsiktligt en separat, PostgreSQL-härledd graf.

## Lokalt verifierat, ännu inte release

Roadmap P0–P2 är implementerad lokalt ovanpå den verifierade stagingreleasen;
P3 är avsiktligt orörd. Den lokala leveransen kräver schema 52 och innehåller:

- autentiserad read-only driftstatus med färskhetsbevis, releaseidentitet,
  dagens aktivitet, beslutstratt och härledd grafstatus;
- sann felstatus och retry för modell-, tomsvars- och JSON-fel;
- separat, versionsstyrd exploration med högst en position per cykel och
  högst fem procent av portföljen;
- segmenterade utfall, kassa-/toppsignalbaslinjer, opportunity cost,
  rotationskvalitet samt automatiska dags- och veckorapporter;
- ett första kontrollrum för målprogress, värdering, puls, aktivitet,
  färskhet, lärande och blockerare;
- en smal central Brain-brygga som speglar aggregerad status och aktiv
  release, aldrig rå ledger eller modellsvar.

Verifieringen består av 714 godkända agenttester, 72 godkända dashboardtester
mot ett nybyggt PostgreSQL-schema 52, lyckat dashboardbygge, exekverad
dagsrapport och fyra godkända statusbryggetester. Detta är inte deploybevis.
GitHub `main` och staging är fortsatt den ovan angivna revisionen tills en
separat commit, push och release har genomförts.

## Verifierad kod och release

Följande dag-ett-förbättringar ingår nu i `main`:

- `dd77180`: Nasdaq-baserad XSTO-sektorklassificering med validerad täckning,
  källproveniens och separat synktjänst;
- `7d9214f`: kandidatutfall märks mot den faktiska tillgängliga fördröjda
  orderboksminuten inom ett strikt tvåminutersfönster;
- `0a26681`: kontinuerligt paper-lärande, automatisk policyprövning och
  realistiska standardkostnader för nya affärer i schema 48;
- `8805c15`: stagingdeploy ägs av `diffen77/plattform-deploy`, använder
  self-hosted runner på Kajen och kräver ett CI-utlöst digestbevis;
- `edd6bd2`: avkastningsmålet, aktuell equity, återstående målgap och
  kontrollerad `SELL → BUY`-rotation ingår i den faktiska beslutsloopen.

På ett nybyggt PostgreSQL 16-schema 48 passerade 692 agenttester, 67
dashboardtester och 27 fokuserade releasetester. Dashboardens
produktionsbygge och en lokal kontroll av den slutliga agentimagen passerade.
GitHub Actions-körning `31538423956` passerade på aktuellt main-head. Den
immutabla releasekörningen `31538592727` byggde och pushade revisionsmärkta
agent- och dashboard-images, attesterade deras proveniens och publicerade
plattformens digestbevis.

För nuvarande main-head passerade CI-körning `31638367795` och immutable
release `31638530991`. Plattformsdeploy `31638793647` migrerade, startade om
och verifierade staging utan rollback. Publik health svarade fortsatt exakt
`{"status":"ok"}` med HTTP 200 den 2026-08-16. Health bevisar tjänstens
tillgänglighet, men inte dagens affärer eller aktuell ledgerstatus.

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
  `sha256:ddeabf52fa24824c3da0d74ee6cd7f6cfc3283b7fad0fb7f7abfb1707e58e087`;
- dashboard-image:
  `sha256:d3574375864d7c9255e27e10cc6f57c35d44b6756520b332ed448de867d91436`;
- båda images har OCI-revision
  `edd6bd2d90abd2829cea5537de16732f26d89924`.

Plattformsdeploy `31638793647` validerade nyttolasten, sparade föregående
imagefamiljer för rollback, hämtade digestlåsta images, migrerade, startade om
och verifierade extern health. Efterkontrollen visade inga öppna driftlarm.
Trading-readiness var korrekt `NOT_READY` enbart därför att XSTO-sessionen var
stängd; monitorn kräver inte marknadsdata utanför en öppen session.

Den senaste autentiserade ledgerverifieringen den 2026-08-11 visade följande;
värdena ska inte behandlas som dagens ledgerstatus. Den aktiva XSTO-mappningen
innehöll 416 bolag. Alla hade en sektoretikett och 412 hade färsk, verifierad
Nasdaq-proveniens, vilket motsvarar 99,04 procent.
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
