# Trading knowledge graph

Neo4j is a dedicated, derived knowledge store for the trading agent.
PostgreSQL remains the source of truth for prices, order books, ledger,
risk controls and append-only outcome evidence.

## Current graph

The `knowledge-worker` runs every five minutes and synchronises:

- `Company` and `Instrument`;
- `TradingDecision`;
- `CandidatePrediction` and `CandidateOutcome`;
- `PaperTrade`;
- `StrategyVersion` and `CandidatePolicy`;
- `TradingLearning`;
- one `TradingGraphState` cursor.

Relationships describe listings, decision origins, strategy and policy use,
candidate subjects, measured outcomes and trade provenance. Writes use
`MERGE`, unique constraints and PostgreSQL high-water marks, so a retry is
idempotent. Mutable reference nodes and papertrades are refreshed on every
cycle; append-only decisions, predictions and outcomes advance by ID.

The graph deliberately excludes raw prompts, raw model responses,
`market_data_json`, trade reasoning and credentials. Model-generated
learning text is bounded to 4,000 characters and is not fed back into the
model by this first release.

## Runtime configuration

Required:

```text
NEO4J_URL=bolt://100.116.226.27:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD_FILE=/run/secrets/neo4j_password
NEO4J_DATABASE=neo4j
```

The password is injected from BWS. It must never be placed in `.env`,
Compose source, logs, Cortex or project documentation.

Optional:

```text
NEO4J_SYNC_BATCH_SIZE=1000
KNOWLEDGE_GRAPH_SYNC_INTERVAL_SECONDS=300
KNOWLEDGE_SHADOW_INTERVAL_SECONDS=300
KNOWLEDGE_SHADOW_MAX_RUNS_PER_CYCLE=2
```

## Verification

`knowledge_graph_sync_runs` in PostgreSQL is the append-only operational
journal. The dashboard reads its latest row and shows the last successful
sync plus graph node and relationship totals.

The worker healthcheck verifies Bolt connectivity:

```bash
python -m src.knowledge_worker health
```

One controlled sync can be run with:

```bash
python -m src.knowledge_worker once
```

## Shadow evaluation

The independent `knowledge-shadow-worker` processes earlier AI decisions
continuously, including outside market hours. For each eligible decision it:

1. loads the bounded source context and exact model/strategy identity from
   PostgreSQL;
2. retrieves only allow-listed numeric outcome aggregates whose
   `evaluated_at` is not later than the original decision;
3. calls the same configured model twice with alternating call order: one
   control prompt without graph memory and one prompt with graph memory;
4. validates both structured decision responses and stores only actions,
   confidences, counts, token usage, checksums and provenance.

Prompts, model reasoning and raw responses are not stored in the shadow
journal. Instrument aggregates require at least three observations and
market aggregates at least thirty. Retrieval is capped at 60 facts, 20
candidate tickers and 6,000 rendered characters.

`knowledge_shadow_runs` and `knowledge_shadow_decisions` are append-only.
The dashboard shows whether the latest comparison succeeded, how many
actions changed and how many graph facts were available. A single bounded
cycle can be run with:

```bash
python -m src.knowledge_shadow_worker once
```

## Deliberate boundary

Neo4j is now used in controlled shadow comparisons, but graph content still
cannot alter operational decisions or orders. Activation requires enough
out-of-sample comparisons to prove a defined improvement without degrading
risk, plus an explicit reviewed policy change. The S3-compatible object
archive now mirrors validated `market_data_files`, independently of this
structured shadow loop. Licensed news documents, filings and candle
archives still need their own authorized ingestion and retention contracts
before they may use the same storage.
