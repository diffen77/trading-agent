from datetime import date, datetime, timedelta, timezone

from src.core.continuous_learning import (
    calibrate_candidate_policy,
)


NOW = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)


def _observations(*, sessions=13, per_session=100):
    rows = []
    first = date(2026, 7, 15)
    for session_index in range(sessions):
        session_date = first + timedelta(days=session_index)
        for index in range(per_session):
            high_score = index >= per_session // 2
            rows.append({
                "session_date": session_date,
                "signal_score": 65 if high_score else 45,
                "return_bps": 18 if high_score else -8,
            })
    return rows


def test_calibration_collects_until_forward_split_is_large_enough():
    result = calibrate_candidate_policy(
        _observations(sessions=12),
        policy_version="xsto-momentum-v1",
        active_min_signal_score=0,
        coverage_pct=100,
        evaluated_at=NOW,
    )

    assert result.status == "COLLECTING"
    assert result.reason_code == "INSUFFICIENT_SESSIONS"
    assert result.train_sessions == 9
    assert result.validation_sessions == 3


def test_calibration_promotes_only_a_forward_validated_challenger():
    result = calibrate_candidate_policy(
        _observations(),
        policy_version="xsto-momentum-v1",
        active_min_signal_score=0,
        coverage_pct=100,
        evaluated_at=NOW,
    )

    assert result.status == "CHALLENGER"
    assert result.reason_code == "FORWARD_GATE_PASSED"
    assert result.challenger_min_signal_score == 50
    assert result.train_sessions == 10
    assert result.validation_sessions == 3
    assert result.validation_improvement_bps > 0
    assert result.metrics["walk_forward"]["validation_sessions"] == 3


def test_calibration_rejects_incomplete_outcome_coverage():
    result = calibrate_candidate_policy(
        _observations(),
        policy_version="xsto-momentum-v1",
        active_min_signal_score=0,
        coverage_pct=98.5,
        evaluated_at=NOW,
    )

    assert result.status == "COLLECTING"
    assert result.reason_code == "OUTCOME_COVERAGE_INCOMPLETE"

