# Aktuellt läge

Senast verifierat: 2026-08-16.

Detta dokument är projektets korta, aktuella lägesbild. `STATE.md` är den
historiska journalen och ska inte användas som ensam källa för dagens drift.

## Sammanfattning

- Systemet är en fail-closed papertradingplattform. Riktiga pengar och
  brokerkoppling är blockerade.
- Kanonisk kod är GitHub `main`. Aktiv fullständig runtime-SHA visas av den
  autentiserade operations-API:n och måste motsvara senaste lyckade deploy.
- Varje aktiv runtime-revision måste ha grön CI, immutable release, lyckad
  stagingdeploy och autentiserad live-smoke.
- Agentens mål är 20 000 → 26 000 SEK, motsvarande 30 procents
  nettoavkastning på 6–12 månader. Målet och aktuell målprogress ingår i varje
  modellbeslut; det är ett styrmål, inte en garanti eller en affärskvot.
- PostgreSQL är system of record. Neo4j och Cortex är härledda projekt- och
  tradingminnen, inte ersättning för Git eller ledgern.
- Central Neo4j Brain på Neptun är nåbar och färsk. Tradinggrafen på Sjöboden
  är avsiktligt en separat, PostgreSQL-härledd graf.

## P0–P2 deployat och verifierat

Roadmap P0–P2 är deployad på den verifierade papertradingmiljön;
P3 är avsiktligt orörd. Releasen kräver schema 52 och innehåller:

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
dagsrapport och fyra godkända statusbryggetester. GitHub CI `31965952223`,
immutable release `31966056152` och plattformsdeploy `31966233936` passerade.
Den hemlighetssäkra live-smoken `31966528157` krävde PostgreSQL `CURRENT`,
exakt release-SHA med status `VERIFIED`, schema 52 och HTTP 200 från den
autentiserade operations-API:n. Operations-token skapades direkt i Bitwarden
av Kajenkörning `31965889647` utan att värdet skrevs till Git eller logg.

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
  kontrollerad `SELL → BUY`-rotation ingår i den faktiska beslutsloopen;
- `2e4a3cb`: sann brain-felstatus, beslutstratt, styrd exploration,
  segmenterad evidens, automatiska rapporter, grafbacklog, autentiserad
  operationsstatus och kontrollrum i schema 52.

På ett nybyggt PostgreSQL 16-schema 48 passerade 692 agenttester, 67
dashboardtester och 27 fokuserade releasetester. Dashboardens
produktionsbygge och en lokal kontroll av den slutliga agentimagen passerade.
GitHub Actions-körning `31538423956` passerade på aktuellt main-head. Den
immutabla releasekörningen `31538592727` byggde och pushade revisionsmärkta
agent- och dashboard-images, attesterade deras proveniens och publicerade
plattformens digestbevis.

För den första P0–P2-releasen passerade CI-körning `31965952223` och immutable
release `31966056152`. Plattformsdeploy `31966233936` migrerade, startade om
och verifierade staging utan rollback. Publik health svarade fortsatt exakt
`{"status":"ok"}` med HTTP 200 den 2026-08-16. Den separata autentiserade
live-smoken `31966528157` verifierade dessutom aktuell ledgerkälla,
releaseidentitet och schema utan att exponera token eller rå ledger.

## Verifierad staging

Staging kör på Kajen under `/srv/prod/staging/trader` och visas på
`https://trader.lediff.online`.

Senaste läsverifieringen visade:

- publik root: `401`;
- publik health: `200`;
- operations-API utan auth: `401`;
- elva långlivade Compose-tjänster: `running` och `healthy`;
- autentiserad operationskälla: `CURRENT`;
- databasschema: 52;
- samtliga utbytta imagefamiljer verifierades mot de digestlåsta images som
  hämtades av plattformen;
- aktiv runtime-release har status `VERIFIED` och fullständig SHA måste
  motsvara den senaste lyckade plattformsdeployens begärda SHA.

Plattformsdeploy `31966233936` validerade nyttolasten, sparade föregående
imagefamiljer för rollback, hämtade digestlåsta images, migrerade, startade om
och verifierade extern health. Operations-smoken verifierade databasläsning,
release och schema separat; dess `overall_status` får fortsatt vara blockerad
av saknad handels- eller benchmarkevidens utan att deploymenten är trasig.

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
