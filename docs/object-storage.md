# Objektlagring

## Syfte och gräns

Objektlagringen bevarar stora, validerade marknadsdatafiler utanför
PostgreSQL utan att göra S3 till en del av den orderkritiska vägen.
PostgreSQL är system of record och innehåller både källfilens bytes,
checksummor och append-only-evidens om varje objekt.

Arbetaren läser endast filer som redan har accepterats och bevarats i
`market_data_files` eller `reference_data_files`. Den laddar upp deras
befintliga gzip-bytes och verifierar sedan storlek och metadata med
`HEAD`. Ett befintligt objekt med annan checksumma eller storlek
behandlas som konflikt och skrivs inte över.

## Objektformat

Alla nycklar ligger under det fasta prefixet `trading-agent/`:

```text
trading-agent/market-data/{provider}/{data-type}/{YYYY}/{MM}/{DD}/
  {market_data_file_id}-{raw_sha256}.gz

trading-agent/reference-data/{provider}/reference-universe/{YYYY}/{MM}/{DD}/
  {reference_data_file_id}-{raw_sha256}.gz
```

Objektmetadata innehåller:

- råfilens SHA-256;
- gzip-objektets SHA-256;
- databasens käll-id.

Prober använder ett separat `trading-agent/probes/`-prefix och tar
alltid bort exakt det testobjekt de skapade.

## Runtime

Lokalt:

```bash
export S3_ENDPOINT='http://garage:3900'
export S3_BUCKET='orders'
export S3_REGION='garage'
export S3_ACCESS_KEY_ID='local-secret-source-only'
export S3_SECRET_ACCESS_KEY='local-secret-source-only'
docker compose --profile object-storage up -d object-archive-worker
```

I staging och produktion ska credentials inte ligga i `.env`,
Compose-filen eller processmiljön. Använd låsta secret-filer:

```text
ENABLE_OBJECT_ARCHIVE=true
S3_ENDPOINT=http://garage:3900
S3_BUCKET=orders
S3_REGION=garage
S3_ACCESS_KEY_ID_FILE=/secure/path/s3-access-key-id
S3_SECRET_ACCESS_KEY_FILE=/secure/path/s3-secret-access-key
```

En värd utanför Garage-stacken måste använda Garage-värdens privata
nätadress i stället för Compose-namnet `garage`.

Använd en egen, återkallningsbar applikationsnyckel med enbart
read/write till den avsedda bucketen. Återanvänd inte en okänd äldre
nyckel och ge inte delete/admin-rättigheter utöver vad den verifierade
proben och retentionsrutinen faktiskt behöver.

## Driftbeteende

Daemonen kör en begränsad cykel var femte minut som standard:

```bash
python -m src.object_archive daemon
```

Varje cykel:

1. väljer högst det konfigurerade antalet oarkiverade filer;
2. väljer mellan bevarad marknadsrådata och ESMA-referensrådata;
3. kontrollerar om deterministisk objektnyckel redan finns;
4. laddar upp eller verifierar det identiska befintliga objektet;
5. kontrollerar storlek och checksumme-metadata;
6. skriver körning och objektbevis atomiskt i PostgreSQL.

S3-fel fångas per cykel. Arbetaren fortsätter leva och försöker igen,
medan marknadsdataimport och papertrading fortsätter oberoende.

## Begränsning

Att objektlagringen är frisk innebär inte att data finns att arkivera.
Om både `market_data_files` och `reference_data_files` är tomma blir
resultatet `NO_PENDING` och dashboarden visar `0/0`. Publika Nasdaq
pre-trade-rapporters råa kroppar får enligt nuvarande policy inte
bevaras och ingår därför inte; deras härledda orderboksevidens ligger
kvar i PostgreSQL. Nya permanenta objekt skapas endast för källor där
rålagring redan är tillåten och validerad.
