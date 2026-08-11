from datetime import datetime, timezone

import pytest

from src.knowledge_graph import (
    KnowledgeGraph,
    KnowledgeMemoryFact,
    KnowledgeMemorySnapshot,
    KnowledgeGraphSettings,
    render_knowledge_memory,
)
from src.runtime_secrets import RuntimeSecretError


NOW = datetime(2026, 8, 5, 8, 0, tzinfo=timezone.utc)


class _Record(dict):
    def data(self):
        return dict(self)


class _Driver:
    def __init__(self):
        self.calls = []
        self.closed = False
        self.verified = False

    def verify_connectivity(self):
        self.verified = True

    def execute_query(self, query, **parameters):
        self.calls.append((query, parameters))
        if "candidate_outcome_aggregate_v1" in query:
            if "'INSTRUMENT' AS scope" in query:
                return (
                    [
                        _Record(
                            scope="INSTRUMENT",
                            ticker="ATCO-A",
                            action="BUY",
                            horizon_minutes=60,
                            observations=7,
                            mean_return_bps=12.3456,
                            positive_rate_pct=71.4286,
                            latest_evaluated_at=NOW,
                        )
                    ],
                    None,
                    None,
                )
            return (
                [
                    _Record(
                        scope="MARKET",
                        ticker=None,
                        action="ABSTAIN",
                        horizon_minutes=120,
                        observations=42,
                        mean_return_bps=-3.25,
                        positive_rate_pct=47.619,
                        latest_evaluated_at=NOW,
                    )
                ],
                None,
                None,
            )
        if "RETURN s.last_decision_id" in query:
            return (
                [
                    _Record(
                        last_decision_id=0,
                        last_prediction_id=0,
                        last_outcome_id=0,
                    )
                ],
                None,
                None,
            )
        if "total_nodes" in query and "total_relationships" in query:
            return (
                [_Record(total_nodes=14, total_relationships=19)],
                None,
                None,
            )
        return ([], None, None)

    def close(self):
        self.closed = True


class _Database:
    def __init__(self):
        self.queries = []

    def query(self, sql, params=None):
        self.queries.append((sql, params or {}))
        if "FROM instruments instrument" in sql:
            return [
                {
                    "ticker": "ATCO-A",
                    "company_name": "Atlas Copco AB",
                    "sector": "Industrials",
                    "industry": "Machinery",
                    "isin": "SE0017486889",
                    "symbol": "ATCO A",
                    "instrument_name": "Atlas Copco AB ser. A",
                    "mic": "XSTO",
                    "currency": "SEK",
                    "instrument_type": "COMMON_STOCK",
                    "status": "ACTIVE",
                }
            ]
        if "FROM strategy_versions strategy" in sql:
            return [
                {
                    "version": "momentum-v1",
                    "status": "ACTIVE",
                    "config_hash": "a" * 64,
                    "parent_version": None,
                    "created_at": NOW,
                    "activated_at": NOW,
                }
            ]
        if "FROM candidate_policy_versions policy" in sql:
            return [
                {
                    "version": "xsto-momentum-v1",
                    "status": "ACTIVE",
                    "config_hash": "b" * 64,
                    "parent_version": None,
                    "created_at": NOW,
                    "activated_at": NOW,
                }
            ]
        if "FROM ai_decisions decision" in sql:
            return [
                {
                    "id": 4,
                    "timestamp": NOW,
                    "strategy_version": "momentum-v1",
                    "strategy_config_hash": "a" * 64,
                    "model_backend": "openai-compatible",
                    "model_name": "gpt-5.6",
                    "model_provider": "hermes",
                    "reasoning_effort": "high",
                    "response_model": "gpt-5.6",
                    "prompt_tokens": 300,
                    "response_tokens": 90,
                }
            ]
        if "FROM candidate_predictions prediction" in sql:
            return [
                {
                    "id": 8,
                    "ai_decision_id": 4,
                    "policy_version": "xsto-momentum-v1",
                    "strategy_version": "momentum-v1",
                    "ticker": "ATCO-A",
                    "observed_at": NOW,
                    "latest_price": 170.5,
                    "signal_rank": 1,
                    "signal_score": 73.2,
                    "eligible": True,
                    "reason_code": "ELIGIBLE",
                    "model_action": "BUY",
                    "model_confidence": 72.0,
                    "feature_checksum_sha256": "c" * 64,
                }
            ]
        if "FROM candidate_prediction_outcomes outcome" in sql:
            return [
                {
                    "id": 12,
                    "prediction_id": 8,
                    "horizon_minutes": 60,
                    "target_event_time": NOW,
                    "evaluated_at": NOW,
                    "observed_price": 172.0,
                    "return_bps": 87.98,
                }
            ]
        if "FROM trades trade" in sql:
            return [
                {
                    "id": 2,
                    "ticker": "ATCO-A",
                    "action": "BUY",
                    "shares": 5.0,
                    "price": 170.5,
                    "total_value": 852.5,
                    "confidence": 72.0,
                    "outcome_correct": None,
                    "pnl": None,
                    "executed_at": NOW,
                    "closed_at": None,
                    "target_price": 180.0,
                    "stop_loss": 165.0,
                    "strategy_version": "momentum-v1",
                    "decision_id": 4,
                    "decision_origin": "MODEL",
                    "idempotency_key": "trade:2",
                }
            ]
        if "FROM learnings learning" in sql:
            return []
        raise AssertionError(f"unexpected query: {sql}")


def _settings(**overrides):
    values = {
        "url": "bolt://100.116.226.27:7687",
        "user": "neo4j",
        "password": "secret",
        "database": "neo4j",
        "batch_size": 1000,
    }
    values.update(overrides)
    return KnowledgeGraphSettings(**values)


def test_settings_require_a_bounded_bolt_url_without_embedded_credentials():
    settings = KnowledgeGraphSettings.from_environment(
        {
            "NEO4J_URL": "bolt://100.116.226.27:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "secret",
        }
    )

    assert settings.database == "neo4j"
    assert settings.batch_size == 1000

    with pytest.raises(RuntimeSecretError):
        KnowledgeGraphSettings.from_environment(
            {
                "NEO4J_URL": "bolt://neo4j:secret@100.116.226.27:7687",
                "NEO4J_PASSWORD": "secret",
            }
        )


def test_sync_is_idempotent_and_only_copies_bounded_structured_evidence():
    driver = _Driver()
    database = _Database()
    graph = KnowledgeGraph(_settings(), driver=driver)

    graph.verify_connectivity()
    graph.ensure_schema()
    result = graph.sync_once(database, synced_at=NOW)

    assert driver.verified
    assert result.status == "SUCCEEDED"
    assert result.total_nodes == 14
    assert result.total_relationships == 19
    assert result.synced["instruments"] == 1
    assert result.synced["predictions"] == 1
    assert result.synced["outcomes"] == 1
    assert result.synced["trades"] == 1
    assert all(
        parameters["database_"] == "neo4j"
        for _, parameters in driver.calls
    )
    graph_queries = "\n".join(query for query, _ in driver.calls)
    assert "MERGE (prediction:CandidatePrediction" in graph_queries
    assert "MERGE (outcome:CandidateOutcome" in graph_queries
    assert "MERGE (trade:PaperTrade" in graph_queries
    assert "raw_response" not in graph_queries
    assert "trade.reasoning" not in graph_queries
    assert "market_data_json" not in graph_queries
    source_queries = "\n".join(sql for sql, _ in database.queries)
    assert "raw_response" not in source_queries
    assert "trade.reasoning" not in source_queries
    assert "market_data_json" not in source_queries


def test_close_closes_the_shared_driver():
    driver = _Driver()
    graph = KnowledgeGraph(_settings(), driver=driver)

    graph.close()

    assert driver.closed


def test_decision_memory_is_bounded_structured_and_point_in_time():
    driver = _Driver()
    graph = KnowledgeGraph(_settings(), driver=driver)

    snapshot = graph.get_decision_memory(
        tickers=["atco-a", "ATCO-A", "VOLV-B"],
        as_of=NOW,
    )

    assert snapshot.as_of == NOW
    assert snapshot.tickers == ("ATCO-A", "VOLV-B")
    assert snapshot.provenance == "neo4j:candidate-outcome-aggregate-v1"
    assert len(snapshot.facts) == 2
    assert snapshot.facts[0].scope == "INSTRUMENT"
    assert snapshot.facts[0].ticker == "ATCO-A"
    assert snapshot.facts[0].observations == 7
    assert snapshot.facts[1].scope == "MARKET"
    assert snapshot.facts[1].ticker is None
    assert len(snapshot.checksum_sha256) == 64

    memory_calls = [
        (query, parameters)
        for query, parameters in driver.calls
        if "candidate_outcome_aggregate_v1" in query
    ]
    assert len(memory_calls) == 2
    assert all(
        parameters["database_"] == "neo4j"
        for _, parameters in memory_calls
    )
    assert all(
        parameters["parameters_"]["as_of"] == NOW
        for _, parameters in memory_calls
    )
    assert memory_calls[0][1]["parameters_"]["tickers"] == [
        "ATCO-A",
        "VOLV-B",
    ]
    assert all(
        "outcome.evaluated_at <= $as_of" in query
        for query, _ in memory_calls
    )


def test_rendered_decision_memory_contains_no_free_form_graph_text():
    snapshot = KnowledgeMemorySnapshot.create(
        as_of=NOW,
        tickers=("ATCO-A",),
        facts=(
            KnowledgeMemoryFact(
                scope="INSTRUMENT",
                ticker="ATCO-A",
                action="BUY",
                horizon_minutes=60,
                observations=7,
                mean_return_bps=12.3456,
                positive_rate_pct=71.4286,
                latest_evaluated_at=NOW,
            ),
        ),
    )

    rendered = render_knowledge_memory(snapshot)

    assert "neo4j:candidate-outcome-aggregate-v1" in rendered
    assert "ATCO-A BUY 60m" in rendered
    assert "7 utfall" in rendered
    assert "+12.3 bp" in rendered
    assert "opålitlig evidensdata" in rendered
    assert len(rendered) <= 6_000
