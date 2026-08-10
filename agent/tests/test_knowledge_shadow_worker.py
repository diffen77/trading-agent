from datetime import datetime, timezone

from src.core.strategy import baseline_strategy
from src.knowledge_graph import (
    KnowledgeMemoryFact,
    KnowledgeMemorySnapshot,
)
from src.knowledge_shadow_worker import (
    compare_shadow_decisions,
    run_shadow_cycle,
)


NOW = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)


class _Database:
    def __init__(self, rows):
        self.rows = rows
        self.recorded = []

    def get_pending_knowledge_shadow_inputs(self, *, now, limit):
        assert now == NOW
        assert limit == 2
        return self.rows

    def get_strategy_version(self, version):
        assert version == "momentum-v1"
        return baseline_strategy()

    def record_knowledge_shadow_run(self, **values):
        self.recorded.append(values)
        return 901


class _Graph:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def get_decision_memory(self, *, tickers, as_of):
        self.calls.append((tickers, as_of))
        return self.snapshot


class _Model:
    backend = "hermes"
    model = "gpt-5.6"
    model_provider = "openai-codex"
    reasoning_effort = "high"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _call_llm(self, *, system, user_msg, max_tokens):
        self.calls.append({
            "system": system,
            "user_msg": user_msg,
            "max_tokens": max_tokens,
        })
        return self.responses.pop(0)


def _source(source_decision_id=74):
    return {
        "source_decision_id": source_decision_id,
        "decision_at": NOW,
        "market_context": "## PORTFÖLJ\nCash: 100000 SEK",
        "strategy_version": "momentum-v1",
        "model_backend": "hermes",
        "model_name": "gpt-5.6",
        "model_provider": "openai-codex",
        "reasoning_effort": "high",
        "tickers": ["VOLV-B"],
    }


def _snapshot(*, facts=True):
    values = ()
    if facts:
        values = (
            KnowledgeMemoryFact(
                scope="MARKET",
                ticker=None,
                action="BUY",
                horizon_minutes=60,
                observations=42,
                mean_return_bps=8.5,
                positive_rate_pct=61.9,
                latest_evaluated_at=NOW,
            ),
        )
    return KnowledgeMemorySnapshot.create(
        as_of=NOW,
        tickers=("VOLV-B",),
        facts=values,
    )


def test_shadow_cycle_compares_control_and_memory_without_trading():
    database = _Database([_source()])
    graph = _Graph(_snapshot())
    model = _Model([
        (
            '{"decisions":[{"action":"BUY","ticker":"VOLV-B",'
            '"reason":"momentum","confidence":70,"position_size_pct":10}],'
            '"market_outlook":"neutral","analysis_summary":"kontroll"}',
            100,
            20,
        ),
        (
            '{"decisions":[{"action":"HOLD","ticker":"VOLV-B",'
            '"reason":"historik","confidence":55,"position_size_pct":0}],'
            '"market_outlook":"neutral","analysis_summary":"grafminne"}',
            120,
            22,
        ),
    ])

    result = run_shadow_cycle(
        database,
        graph,
        model,
        evaluated_at=NOW,
        max_runs=2,
    )

    assert result.succeeded == 1
    assert result.failed == 0
    assert len(model.calls) == 2
    assert "GRAFMEMINNE" not in model.calls[0]["user_msg"]
    assert "GRAFMEMINNE" in model.calls[1]["user_msg"]
    assert graph.calls == [(["VOLV-B"], NOW)]

    recorded = database.recorded[0]
    assert recorded["status"] == "SUCCEEDED"
    assert recorded["reason_code"] == "COMPARED"
    assert recorded["source_decision_id"] == 74
    assert recorded["evidence_fact_count"] == 1
    assert recorded["comparison_count"] == 1
    assert recorded["changed_count"] == 1
    assert recorded["control_prompt_tokens"] == 100
    assert recorded["memory_prompt_tokens"] == 120
    assert recorded["comparisons"] == [{
        "ticker": "VOLV-B",
        "control_action": "BUY",
        "memory_action": "HOLD",
        "control_confidence": 70.0,
        "memory_confidence": 55.0,
        "changed": True,
    }]
    assert "raw_response" not in recorded
    assert "reason" not in recorded


def test_shadow_cycle_skips_model_when_graph_has_no_bounded_evidence():
    database = _Database([_source()])
    graph = _Graph(_snapshot(facts=False))
    model = _Model([])

    result = run_shadow_cycle(
        database,
        graph,
        model,
        evaluated_at=NOW,
        max_runs=2,
    )

    assert result.skipped == 1
    assert not model.calls
    assert database.recorded[0]["status"] == "SKIPPED"
    assert database.recorded[0]["reason_code"] == "NO_ELIGIBLE_EVIDENCE"
    assert database.recorded[0]["comparisons"] == []


def test_shadow_comparison_uses_abstain_for_missing_actions():
    comparisons = compare_shadow_decisions(
        {
            "decisions": [{
                "action": "BUY",
                "ticker": "ATCO-A",
                "reason": "kontroll",
                "confidence": 70,
                "position_size_pct": 10,
            }],
        },
        {"decisions": []},
        tickers=["ATCO-A"],
    )

    assert comparisons == [{
        "ticker": "ATCO-A",
        "control_action": "BUY",
        "memory_action": "ABSTAIN",
        "control_confidence": 70.0,
        "memory_confidence": None,
        "changed": True,
    }]


def test_shadow_cycle_skips_when_model_configuration_drifted():
    database = _Database([{
        **_source(),
        "model_name": "different-model",
    }])
    graph = _Graph(_snapshot())
    model = _Model([])

    result = run_shadow_cycle(
        database,
        graph,
        model,
        evaluated_at=NOW,
        max_runs=2,
    )

    assert result.skipped == 1
    assert not graph.calls
    assert not model.calls
    assert database.recorded[0]["reason_code"] == "MODEL_CONFIG_MISMATCH"
