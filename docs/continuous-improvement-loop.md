# Continuous improvement loop

Status: approved for implementation by the operator on 2026-08-04.

## Objective

Make the paper-trading agent improve from measured forward outcomes without
allowing the model to rewrite its own rules, contaminate the frozen benchmark,
or infer results from unauthorised data.

The first useful loop is:

1. derive deterministic, multi-horizon candidate features from the authorised
   Nasdaq XSTO pre-trade stream;
2. show the same structured candidates to the model;
3. record every candidate considered, including `HOLD` and `ABSTAIN`;
4. label each prediction from later, independently persisted book evidence;
5. report coverage and forward returns by action and score band;
6. let the model propose observations, while strategy activation remains an
   explicit operator action.

## Non-goals and safety boundaries

- Paper trading only. No broker connection or real-money order path.
- No strategy mutates itself. Candidate policy and active trading strategy are
  immutable identifiers in every prediction.
- No historical claim is mixed into the pre-registered forward benchmark.
- No news, reports, fundamentals, macro data or post-trade data influences a
  decision before its source, usage right, event time and checksum are stored.
- No paid provider, account, credential or new external publication is created
  without a separate operator decision.
- Model text is untrusted data. It never becomes SQL, configuration or an
  executable rule.
- Existing paper positions and the active strategy remain unchanged.

## Initial vertical slice

### Candidate features

For every instrument with a continuous, fresh, two-sided CLOB/COTR book:

- midpoint momentum over 5, 20 and 60 minutes;
- current spread in basis points;
- displayed bid/ask imbalance;
- realised midpoint range over 20 and 60 minutes;
- exact latest book-state id, source and event-time window.

A pure, versioned policy computes a bounded `signal_score`. Missing windows do
not receive neutral bonuses; they make the feature unavailable. The policy
rejects non-finite values, crossed books, non-positive quantities and excessive
spread.

### Prediction journal

One append-only row is stored per AI decision and ranked candidate. It binds:

- AI decision id and model provenance;
- strategy version and configuration hash;
- candidate policy version;
- ticker, rank, score and exact feature JSON checksum;
- source book-state id and feature interval;
- model action (`BUY`, `SELL`, `HOLD` or `ABSTAIN`) and confidence.

The journal must be idempotent on `(ai_decision_id, policy_version, ticker)`.

### Outcome labels

The signal book is delayed and is therefore not a truthful fill price. An
append-only paper-entry row is first locked to the exact XSTO report minute in
which the AI decision was made, once that delayed book becomes available. An
idempotent evaluator then records 30-, 60- and 120-minute outcomes from that
decision-time entry when later sealed states exist in the same XSTO session.
Each label binds the entry, future book-state id, observed price and market
return in basis points. Missing or ambiguous evidence stays pending; it is
never filled from a current price.

### Runtime and product evidence

The authenticated learning API and dashboard expose:

- prediction count, matured count, labelled count and coverage;
- scheduled and overdue outcomes;
- mean market return and price-rise rate by action and horizon;
- current policy version and most recent evaluation time;
- an explicit message while the sample is too small for a conclusion.

Structured logs contain bounded identifiers, counts, policy version and stable
reason codes. They contain no raw model prompts, credentials or market payloads.

## Follow-on slices

1. Register shadow candidate policies and evaluate them on the same evidence;
   only an operator may promote a winner.
2. Add authorised Nasdaq post-trade volume evidence as a separate feature
   contract, without replacing pre-trade execution prices.
3. Add official Riksbank and SCB observations with publication-time
   provenance.
4. Procure authorised report calendar, fundamentals, news and OMXSGI total
   return data.
5. Start the already pre-registered 252-session benchmark only after all its
   data gates are satisfied.

## Files and interfaces

- `agent/src/core/candidates.py`: pure feature validation, scoring and rendering.
- `agent/src/data/database.py`: authorised feature query, prediction journal,
  outcome evaluator and learning metrics.
- `agent/src/core/brain.py`: load one candidate snapshot, render it and record
  the model response against the same snapshot.
- `agent/src/main.py`: evaluate matured outcomes before each open-session brain
  cycle and during the evening routine.
- `db/migrations/038_continuous_improvement_loop.sql`: append-only schema.
- `dashboard/app/api/learning/route.ts`: authenticated, fail-closed metrics.
- `dashboard/app/page.tsx`: human-readable continuous-improvement status.

## Verification commands

- Focused pure Python tests for scoring and rendering.
- Focused PostgreSQL integration tests for journal idempotency, append-only
  enforcement, evidence binding and outcome timing.
- Brain tests proving `ABSTAIN` is recorded and the exact candidate snapshot is
  reused.
- Full agent test suite against a freshly migrated PostgreSQL 16 database.
- Python compile check and dependency check.
- Dashboard tests, production dependency audit and production build.
- Migration replay, schema-gate consistency, Compose/workflow/shell validation,
  whitespace validation and confirmation that no `.env` file changed.
- Staging migration and digest-pinned deployment with health, operations API,
  authenticated browser and rollback evidence.

## Acceptance criteria

- Every successful open-session model decision records its ranked candidate
  snapshot, including candidates the model ignores.
- The candidate scan ranks the complete bounded XSTO universe before selecting
  the top 20 rows shown to the model.
- A repeated cycle or evaluator retry creates no duplicate journal or outcome
  rows.
- No paper entry is backfilled from the delayed signal price, and no outcome is
  stored before its horizon or from another trading session.
- Dashboard states how many observations exist and never calls an unevaluated
  observation a learning.
- Current paper trading continues with the unchanged active strategy.
- All tests and staging readiness checks pass with schema 40.
