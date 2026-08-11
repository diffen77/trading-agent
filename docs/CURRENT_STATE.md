# Aktuellt läge

Senast verifierat: 2026-08-11.

Detta dokument är projektets korta, aktuella lägesbild. `STATE.md` är den
historiska journalen och ska inte användas som ensam källa för dagens drift.

## Sammanfattning

- Systemet är en fail-closed papertradingplattform. Riktiga pengar och
  brokerkoppling är blockerade.
- Kanonisk kod finns på `main` i `diffen77/trading-agent`. Automatisk
  paper-policyaktivering och återställning finns i
  [PR #14](https://github.com/diffen77/trading-agent/pull/14). Fri
  sektorallokering, robust quote-inläsning och likviditetsanpassade
  paper-fills landade i
  [PR #15](https://github.com/diffen77/trading-agent/pull/15),
  [PR #16](https://github.com/diffen77/trading-agent/pull/16),
  [PR #17](https://github.com/diffen77/trading-agent/pull/17) och
  [PR #18](https://github.com/diffen77/trading-agent/pull/18).
- Deployad staging-release är
  `45ad55de6848b94dc1cd090c0a396cee2d80ce85`.
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
- `ca31f33`: automatisk aktivering av framåttestade kandidatpolicyer och
  automatisk, evidensstyrd återställning i paper trading;
- `e76bc6e`: sektorkoncentration blev analysinformation i stället för en hård
  ordergate; saknad sektorklassning blockerar inte längre en i övrigt giltig
  XSTO-order i paper trading;
- `dcfa52a`: exekveringsquoten läses om i ett kort, begränsat fönster när en
  pågående marknadsdatabatch ännu inte är committad;
- `b079334`: driftstatus behandlar sektorkoncentration och äldre sektorlarm
  som information, inte som paper-tradingblockerare;
- `45ad55d`: köp och sälj fylls delvis upp till verifierad toppvolym i stället
  för att hela ordern nekas när önskad storlek är större än orderboken.

På ett nybyggt PostgreSQL 16-schema 45 passerade 679 agenttester och samtliga
66 dashboardtester. Dashboardens typkontroll och produktionsbygge passerade.
GitHub Actions-körning `31491482519` passerade på mergecommiten. Den
immutabla releasekörningen `31491657173` byggde och pushade
revisionsmärkta agent- och dashboard-images, attesterade deras proveniens och
publicerade release-manifestet.

## Verifierad staging

Staging kör på Kajen under `/srv/prod/staging/trader` och visas på
`https://trader.lediff.online`.

Senaste läsverifieringen visade:

- publik root: `401`;
- publik health: `200`;
- operations-API utan auth: `401`;
- tio långlivade Compose-tjänster: `running` och `healthy`;
- intern readiness: `READY`;
- tradingstatus: `READY` utan blockerare;
- databasschema: 45;
- agent-image:
  `sha256:5db4a2946003fb3ef3e9f904b265f91dd0a0ac46533e965794fc135fd349e8d8`;
- dashboard-image:
  `sha256:590f1cb0143b52b6b64d9ff02007e0e62a2be3b837ba69809062e08da767e166`;
- båda images har OCI-revision
  `45ad55de6848b94dc1cd090c0a396cee2d80ce85`.

Varje deployment har en separat Compose-backup. Ett försök med `dcfa52a`
återställdes automatiskt när den gamla dashboardregeln fortfarande blockerade
en tredje oklassificerad sektorposition; data och paper-ledger bevarades.
Kandidatfilens checksumma, schema-intervall och image-revisionerna verifierades
före den lyckade `45ad55d`-starten. Smoke-testet verifierade publik health
`200`, root och operations utan auth `401`, operations med auth `200`,
readiness `READY` och tom blockerlista.

Paper-ledgern innehåller nio affärer. Agenten köpte Attendo AB automatiskt
2026-08-11 11:50:33 UTC: 21,5312 aktier à 108,30 SEK, totalt 2 331,83 SEK,
med konfidens 56. Det var den tredje öppna positionen med sektorn
`Unclassified` och är livebevis på att den borttagna sektorgaten faktiskt
gäller. Efter `45ad55d`-starten valde hjärnan Isofol Medical och Atlas Copco,
validerade 2 av 2 beslut och genomförde båda köpen. Senaste verifierade
portföljsnapshot hade 4 419,53 SEK i kassa, totalvärde 19 437,67 SEK och fem
öppna positioner: Attendo, Orrön Energy, Intrum, Isofol Medical och Atlas
Copco.

Benchmark-readiness körs nu i staging och är fortsatt fail-closed. De
kvarvarande blockerarna gäller extern data, ren benchmark-ledger och godkänd
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

Agentens hjärncykel körs var femtonde minut under marknadsdrift och den
kontinuerliga lärandearbetaren var femte minut. Slotten 12:30 UTC genomförde
två av två AI-beslut på slutreleasen. Slotten 12:45 UTC analyserade 158
kandidater, valde ingen ny order eftersom portföljen redan hade fem positioner
och avslutades `SUCCEEDED`. Senaste lärandekörningen märkte 139 utfall och
avslutades också `SUCCEEDED`.

Målet för den pågående paperperioden är 30 procent avkastning på startkapitalet
20 000 SEK över 6–12 månader. Det är ett mått och en ambition, inte en garanti.
Första fasen använder kostnadsfri cirka 15 minuter fördröjd XSTO-data; USA,
betald realtidsdata och riktiga pengar är separata framtida beslut.

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
