from datetime import date, datetime, timedelta, timezone
import json

import pytest

from src.core.forward_benchmark import (
    BenchmarkCriteria,
    BenchmarkIncident,
    BenchmarkMetrics,
    BenchmarkObservation,
    BenchmarkRegistration,
    BenchmarkStartEvidence,
    evaluate_forward_benchmark,
    preregistration_hash,
)


def test_completed_forward_period_passes_only_when_every_gate_passes():
    evaluation = evaluate_forward_benchmark(
        BenchmarkMetrics(
            trading_sessions=252,
            closed_trades=30,
            net_return_pct=8.5,
            benchmark_return_pct=6.0,
            max_drawdown_pct=-9.0,
            data_coverage_pct=99.8,
            critical_incidents=0,
        ),
        BenchmarkCriteria(),
    )

    assert evaluation.passed is True
    assert evaluation.excess_return_pct == pytest.approx(2.5)
    assert evaluation.reason_codes == ()


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"trading_sessions": 251}, "INSUFFICIENT_TRADING_SESSIONS"),
        ({"closed_trades": 29}, "INSUFFICIENT_CLOSED_TRADES"),
        ({"net_return_pct": 0}, "NET_RETURN_NOT_POSITIVE"),
        (
            {"net_return_pct": 5, "benchmark_return_pct": 5},
            "BENCHMARK_NOT_OUTPERFORMED",
        ),
        ({"max_drawdown_pct": -15.01}, "MAX_DRAWDOWN_EXCEEDED"),
        ({"data_coverage_pct": 99.49}, "DATA_COVERAGE_BELOW_MINIMUM"),
        ({"critical_incidents": 1}, "CRITICAL_INCIDENTS_PRESENT"),
    ],
)
def test_each_failed_gate_has_a_stable_reason_code(overrides, reason_code):
    values = {
        "trading_sessions": 252,
        "closed_trades": 30,
        "net_return_pct": 8.5,
        "benchmark_return_pct": 6.0,
        "max_drawdown_pct": -9.0,
        "data_coverage_pct": 99.8,
        "critical_incidents": 0,
    }
    values.update(overrides)

    evaluation = evaluate_forward_benchmark(
        BenchmarkMetrics(**values),
        BenchmarkCriteria(),
    )

    assert evaluation.passed is False
    assert reason_code in evaluation.reason_codes


def test_all_failed_gates_are_reported_in_deterministic_order():
    evaluation = evaluate_forward_benchmark(
        BenchmarkMetrics(
            trading_sessions=1,
            closed_trades=0,
            net_return_pct=-5,
            benchmark_return_pct=2,
            max_drawdown_pct=-30,
            data_coverage_pct=90,
            critical_incidents=2,
        ),
        BenchmarkCriteria(),
    )

    assert evaluation.reason_codes == (
        "INSUFFICIENT_TRADING_SESSIONS",
        "INSUFFICIENT_CLOSED_TRADES",
        "NET_RETURN_NOT_POSITIVE",
        "BENCHMARK_NOT_OUTPERFORMED",
        "MAX_DRAWDOWN_EXCEEDED",
        "DATA_COVERAGE_BELOW_MINIMUM",
        "CRITICAL_INCIDENTS_PRESENT",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("net_return_pct", float("nan")),
        ("benchmark_return_pct", float("inf")),
        ("max_drawdown_pct", 1),
        ("max_drawdown_pct", -101),
        ("data_coverage_pct", -0.1),
        ("data_coverage_pct", 100.1),
        ("trading_sessions", -1),
        ("closed_trades", -1),
        ("critical_incidents", -1),
    ],
)
def test_metrics_reject_non_finite_or_impossible_values(field, value):
    values = {
        "trading_sessions": 252,
        "closed_trades": 30,
        "net_return_pct": 8.5,
        "benchmark_return_pct": 6.0,
        "max_drawdown_pct": -9.0,
        "data_coverage_pct": 99.8,
        "critical_incidents": 0,
    }
    values[field] = value

    with pytest.raises(ValueError):
        BenchmarkMetrics(**values)


def test_default_criteria_cannot_be_weakened_below_the_safety_floor():
    with pytest.raises(ValueError):
        BenchmarkCriteria(min_trading_sessions=251)
    with pytest.raises(ValueError):
        BenchmarkCriteria(min_closed_trades=29)
    with pytest.raises(ValueError):
        BenchmarkCriteria(max_drawdown_pct=15.01)
    with pytest.raises(ValueError):
        BenchmarkCriteria(min_data_coverage_pct=99.49)


def test_preregistration_hash_is_canonical_and_rejects_non_finite_values():
    first = {
        "benchmark_symbol": "OMXSGI",
        "cost_model": {"fee_bps": 5, "slippage_bps": 10},
        "strategy_version": "momentum-report-swing-v1",
    }
    reordered = {
        "strategy_version": "momentum-report-swing-v1",
        "cost_model": {"slippage_bps": 10, "fee_bps": 5},
        "benchmark_symbol": "OMXSGI",
    }

    assert preregistration_hash(first) == preregistration_hash(reordered)
    assert len(preregistration_hash(first)) == 64

    with pytest.raises(ValueError):
        preregistration_hash({"fee_bps": float("nan")})


def registration(**overrides):
    values = {
        "experiment_key": "xsto-forward-2026",
        "strategy_version": "momentum-report-swing-v1",
        "strategy_config_hash": "1" * 64,
        "release_sha": "a" * 40,
        "release_manifest_sha256": "2" * 64,
        "agent_image_digest_sha256": "3" * 64,
        "model_backend": "openai-compatible",
        "model_name": "frozen-model",
        "model_evidence_sha256": "4" * 64,
        "reference_snapshot_id": 7,
        "universe_checksum_sha256": "b" * 64,
        "quote_provider_contract_key": "xsto-quotes-v1",
        "execution_price_source": "LAST_TRADE_PLUS_BPS",
        "execution_provider_contract_key": "xsto-quotes-v1",
        "benchmark_provider_contract_key": "omxsgi-index-v1",
        "benchmark_source_url": (
            "https://indexes.nasdaqomx.com/Index/Overview/OMXSGI"
        ),
        "benchmark_terms_url": "https://example.test/terms",
        "fee_bps": 5,
        "spread_bps": 10,
        "slippage_bps": 5,
        "proposed_by": "operator:diffen",
    }
    values.update(overrides)
    return BenchmarkRegistration(**values)


def test_registration_freezes_the_complete_forward_experiment_contract():
    value = registration()
    payload = value.to_payload()

    assert payload["benchmark_symbol"] == "OMXSGI"
    assert payload["benchmark_kind"] == "TOTAL_RETURN_GROSS"
    assert payload["execution_price_source"] == "LAST_TRADE_PLUS_BPS"
    assert (
        payload["execution_provider_contract_key"]
        == payload["quote_provider_contract_key"]
    )
    assert payload["initial_capital"] == 20_000
    assert payload["criteria"] == {
        "min_trading_sessions": 252,
        "min_closed_trades": 30,
        "max_drawdown_pct": 15.0,
        "min_data_coverage_pct": 99.5,
    }
    assert value.payload_hash == preregistration_hash(payload)
    assert value.payload_hash == preregistration_hash(
        json.loads(value.canonical_json)
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"release_sha": "main"},
        {"strategy_config_hash": "invalid"},
        {"release_manifest_sha256": "invalid"},
        {"agent_image_digest_sha256": "invalid"},
        {"model_backend": "auto"},
        {"model_name": ""},
        {"model_evidence_sha256": "invalid"},
        {"reference_snapshot_id": 0},
        {"universe_checksum_sha256": "ABC"},
        {
            "benchmark_provider_contract_key": "xsto-quotes-v1",
        },
        {"execution_price_source": "UNSPECIFIED"},
        {
            "execution_price_source": "LAST_TRADE_PLUS_BPS",
            "execution_provider_contract_key": "other-execution-v1",
        },
        {
            "execution_price_source": "TOP_OF_BOOK_PLUS_SLIPPAGE",
            "spread_bps": 1,
        },
        {"benchmark_source_url": "http://example.test/index"},
        {"fee_bps": -1},
        {"spread_bps": float("nan")},
        {"proposed_by": "model:student"},
    ],
)
def test_registration_rejects_ambiguous_or_weakened_inputs(overrides):
    with pytest.raises(ValueError):
        registration(**overrides)


def test_registration_accepts_explicit_top_of_book_cost_contract():
    value = registration(
        execution_price_source="TOP_OF_BOOK_PLUS_SLIPPAGE",
        execution_provider_contract_key="xsto-pre-trade-v1",
        spread_bps=0,
    )

    assert value.to_payload()["costs"] == {
        "fee_bps": 5.0,
        "spread_bps": 0.0,
        "slippage_bps": 5.0,
    }


def observation(**overrides):
    cutoff = datetime(2026, 7, 29, 15, 30, tzinfo=timezone.utc)
    values = {
        "session_date": date(2026, 7, 29),
        "net_asset_value": 20_100,
        "session_high_nav": 20_200,
        "session_low_nav": 19_800,
        "cash": 5_000,
        "gross_exposure": 15_100,
        "benchmark_level": 102.5,
        "fees": 5,
        "spread_cost": 10,
        "slippage_cost": 5,
        "expected_quote_points": 10_000,
        "received_quote_points": 9_999,
        "event_cutoff_at": cutoff,
        "data_available_at": cutoff + timedelta(minutes=15),
        "source_checksum_sha256": "d" * 64,
    }
    values.update(overrides)
    return BenchmarkObservation(**values)


def test_observation_accepts_a_complete_after_close_audit_record():
    value = observation()

    assert value.net_asset_value == pytest.approx(20_100)
    assert value.received_quote_points == 9_999


@pytest.mark.parametrize(
    "overrides",
    [
        {"session_date": "2026-07-29"},
        {"net_asset_value": 0},
        {"session_high_nav": 20_000},
        {"session_low_nav": 20_101},
        {"cash": -1},
        {"gross_exposure": -1},
        {"benchmark_level": float("nan")},
        {"fees": -1},
        {"expected_quote_points": 0},
        {"received_quote_points": 10_001},
        {"event_cutoff_at": datetime(2026, 7, 29, 15, 30)},
        {
            "data_available_at": datetime(
                2026,
                7,
                29,
                15,
                29,
                tzinfo=timezone.utc,
            )
        },
        {"source_checksum_sha256": "invalid"},
    ],
)
def test_observation_rejects_incomplete_or_impossible_values(overrides):
    with pytest.raises(ValueError):
        observation(**overrides)


def test_start_evidence_binds_level_to_point_in_time_provenance():
    event_time = datetime(2026, 7, 29, 15, 30, tzinfo=timezone.utc)
    value = BenchmarkStartEvidence(
        level_id=42,
        benchmark_level=100,
        event_time=event_time,
        available_at=event_time + timedelta(minutes=15),
        source_checksum_sha256="e" * 64,
    )

    assert value.level_id == 42
    assert value.benchmark_level == pytest.approx(100)
    assert value.available_at > value.event_time


@pytest.mark.parametrize(
    "overrides",
    [
        {"level_id": 0},
        {"level_id": True},
        {"benchmark_level": 0},
        {"benchmark_level": float("nan")},
        {"event_time": datetime(2026, 7, 29, 15, 30)},
        {
            "available_at": datetime(
                2026,
                7,
                29,
                15,
                29,
                tzinfo=timezone.utc,
            )
        },
        {"source_checksum_sha256": "invalid"},
    ],
)
def test_start_evidence_rejects_unverifiable_values(overrides):
    event_time = datetime(2026, 7, 29, 15, 30, tzinfo=timezone.utc)
    values = {
        "level_id": 42,
        "benchmark_level": 100,
        "event_time": event_time,
        "available_at": event_time + timedelta(minutes=15),
        "source_checksum_sha256": "e" * 64,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        BenchmarkStartEvidence(**values)


def test_critical_incident_has_append_only_source_evidence():
    incident = BenchmarkIncident(
        incident_key="feed-gap-2026-07-29",
        session_date=date(2026, 7, 29),
        severity="CRITICAL",
        description="Quote coverage fell below the frozen threshold.",
        detected_at=datetime(
            2026,
            7,
            29,
            15,
            45,
            tzinfo=timezone.utc,
        ),
        source_checksum_sha256="f" * 64,
    )

    assert incident.severity == "CRITICAL"


@pytest.mark.parametrize(
    "overrides",
    [
        {"incident_key": "INVALID KEY"},
        {"session_date": "2026-07-29"},
        {"severity": "INFO"},
        {"description": ""},
        {"detected_at": datetime(2026, 7, 29, 15, 45)},
        {"source_checksum_sha256": "invalid"},
    ],
)
def test_incident_rejects_ambiguous_or_unverifiable_values(overrides):
    values = {
        "incident_key": "feed-gap-2026-07-29",
        "session_date": date(2026, 7, 29),
        "severity": "CRITICAL",
        "description": "Quote coverage fell below the threshold.",
        "detected_at": datetime(
            2026,
            7,
            29,
            15,
            45,
            tzinfo=timezone.utc,
        ),
        "source_checksum_sha256": "f" * 64,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        BenchmarkIncident(**values)
