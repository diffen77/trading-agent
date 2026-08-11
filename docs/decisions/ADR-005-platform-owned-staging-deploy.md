# ADR-005: Plattformen äger stagingdeployen

- Status: accepterad
- Datum: 2026-08-11

## Kontext

Apprepots direkta SSH-workflow krävde fyra tomma GitHub-secrets och försökte
nå Kajen på publik SSH. Kajen exponerar avsiktligt inte port 22 publikt och
har redan en repo-bunden GitHub-runner, Bitwarden-injektion, imageverifiering
och samordnad rollback i `diffen77/plattform-deploy`.

Den befintliga trading-stagingen måste dessutom behålla Compose-projektet
`trading-agent-staging`, katalogen `/srv/prod/staging/trader` och sina namngivna
volymer. Ett byte av dessa identiteter skulle se ut som en tom installation
även om den gamla ledgern fanns kvar på disken.

## Beslut

Staging deployas endast genom `diffen77/plattform-deploy` och dess self-hosted
runner på Kajen.

- Apprepot bygger och attesterar agent- och dashboardimages från samma SHA.
- Byggjobbet publicerar ett entydigt digestbevis för agentimagen.
- Agentimagen innehåller de versionsbundna SQL-migreringarna och
  `postgresql-client`, så plattformen migrerar med exakt samma artifact som
  ska köras.
- Plattformen lagrar Compose-kontraktet, läser runtime-hemligheter från
  Bitwarden och byter alla långlivade tjänster till samma SHA.
- Health måste svara med exakt applikationsidentitet, inte bara HTTP 200.
- Vid fel återställs alla utbytta tjänster tillsammans. Databasen migreras
  endast framåt.
- Apprepot lagrar varken serverns privata nyckel eller en bred GitHub-token.

Den tidigare `.github/workflows/deploy.yml` tas bort. Den fristående profilen i
`ops/release/` behålls som en testad grund för en framtida separat produktion,
men är inte Kajens deployväg.

## Konsekvenser

Deployen kan inte längre fastna på tomma SSH-secrets eller kringgå Kajens
Bitwarden-wrapper. Plattformens och appens PR:er måste båda vara gröna innan en
ny app kan onboardas. Automatiken är stagingbegränsad; riktiga pengar,
brokerkoppling och produktion aktiveras inte av detta beslut.
