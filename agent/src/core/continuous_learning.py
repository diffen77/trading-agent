"""Pure, forward-only calibration for versioned candidate policies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from statistics import fmean, stdev
from typing import Any, Iterable, Mapping


_THRESHOLDS = (40, 45, 50, 55, 60, 65, 70, 75, 80)
_TRAIN_SESSIONS = 10
_VALIDATION_SESSIONS = 3
_MIN_SAMPLE = 100
_MIN_COVERAGE_PCT = 99.0
_MIN_FORWARD_IMPROVEMENT_BPS = 2.0
_ROLLBACK_SESSIONS = 3


@dataclass(frozen=True)
class CandidateCalibrationResult:
    policy_version: str
    evaluated_at: datetime
    status: str
    reason_code: str
    train_sessions: int
    validation_sessions: int
    train_outcomes: int
    validation_outcomes: int
    coverage_pct: float
    active_min_signal_score: float
    challenger_min_signal_score: float | None
    baseline_validation_mean_bps: float | None
    challenger_validation_mean_bps: float | None
    validation_improvement_bps: float | None
    metrics: dict[str, Any]


@dataclass(frozen=True)
class CandidateRollbackResult:
    status: str
    reason_code: str
    completed_sessions: int
    coverage_pct: float
    active_outcomes: int
    parent_outcomes: int
    active_mean_bps: float | None
    parent_mean_bps: float | None
    parent_positive_rate_pct: float | None
    parent_advantage_bps: float | None


def evaluate_candidate_policy_rollback(
    *,
    completed_sessions: int,
    coverage_pct: float,
    active_outcomes: int,
    parent_outcomes: int,
    active_mean_bps: float | None,
    parent_mean_bps: float | None,
    parent_positive_rate_pct: float | None,
) -> CandidateRollbackResult:
    """Gate rollback on bounded post-activation forward evidence."""
    counts = {
        "completed_sessions": completed_sessions,
        "active_outcomes": active_outcomes,
        "parent_outcomes": parent_outcomes,
    }
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for value in counts.values()
    ):
        raise ValueError("rollback counts must be non-negative integers")
    coverage = _bounded_number(coverage_pct, "coverage_pct")
    if not 0 <= coverage <= 100:
        raise ValueError("coverage_pct must be between 0 and 100")

    common = {
        **counts,
        "coverage_pct": round(coverage, 2),
        "active_mean_bps": None,
        "parent_mean_bps": None,
        "parent_positive_rate_pct": None,
        "parent_advantage_bps": None,
    }
    if coverage < _MIN_COVERAGE_PCT:
        return CandidateRollbackResult(
            status="COLLECTING",
            reason_code="ROLLBACK_OUTCOME_COVERAGE_INCOMPLETE",
            **common,
        )
    if completed_sessions < _ROLLBACK_SESSIONS:
        return CandidateRollbackResult(
            status="COLLECTING",
            reason_code="INSUFFICIENT_ROLLBACK_SESSIONS",
            **common,
        )
    if active_outcomes < _MIN_SAMPLE or parent_outcomes < _MIN_SAMPLE:
        return CandidateRollbackResult(
            status="COLLECTING",
            reason_code="INSUFFICIENT_ROLLBACK_OUTCOMES",
            **common,
        )
    active_mean = _bounded_number(active_mean_bps, "active_mean_bps")
    parent_mean = _bounded_number(parent_mean_bps, "parent_mean_bps")
    parent_positive_rate = _bounded_number(
        parent_positive_rate_pct,
        "parent_positive_rate_pct",
    )
    if not 0 <= parent_positive_rate <= 100:
        raise ValueError(
            "parent_positive_rate_pct must be between 0 and 100"
        )
    advantage = parent_mean - active_mean
    rolled_back = (
        advantage >= _MIN_FORWARD_IMPROVEMENT_BPS
        and parent_positive_rate >= 50
    )
    return CandidateRollbackResult(
        status="ROLLBACK" if rolled_back else "KEEP",
        reason_code=(
            "PARENT_FORWARD_PERFORMANCE_RECOVERED"
            if rolled_back
            else "ACTIVE_POLICY_REMAINS_COMPETITIVE"
        ),
        completed_sessions=completed_sessions,
        coverage_pct=round(coverage, 2),
        active_outcomes=active_outcomes,
        parent_outcomes=parent_outcomes,
        active_mean_bps=round(active_mean, 4),
        parent_mean_bps=round(parent_mean, 4),
        parent_positive_rate_pct=round(parent_positive_rate, 2),
        parent_advantage_bps=round(advantage, 4),
    )


def _bounded_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def _lower_confidence_bound(values: list[float]) -> float:
    if len(values) < 2:
        return -math.inf
    return fmean(values) - 1.96 * stdev(values) / math.sqrt(len(values))


def _result(
    *,
    policy_version: str,
    evaluated_at: datetime,
    status: str,
    reason_code: str,
    train_dates: list[date],
    validation_dates: list[date],
    train_outcomes: int,
    validation_outcomes: int,
    coverage_pct: float,
    active_min_signal_score: float,
    challenger_min_signal_score: float | None = None,
    baseline_validation_mean_bps: float | None = None,
    challenger_validation_mean_bps: float | None = None,
    validation_improvement_bps: float | None = None,
    metrics: dict[str, Any] | None = None,
) -> CandidateCalibrationResult:
    return CandidateCalibrationResult(
        policy_version=policy_version,
        evaluated_at=evaluated_at,
        status=status,
        reason_code=reason_code,
        train_sessions=len(train_dates),
        validation_sessions=len(validation_dates),
        train_outcomes=train_outcomes,
        validation_outcomes=validation_outcomes,
        coverage_pct=round(coverage_pct, 2),
        active_min_signal_score=active_min_signal_score,
        challenger_min_signal_score=challenger_min_signal_score,
        baseline_validation_mean_bps=baseline_validation_mean_bps,
        challenger_validation_mean_bps=challenger_validation_mean_bps,
        validation_improvement_bps=validation_improvement_bps,
        metrics=metrics or {},
    )


def calibrate_candidate_policy(
    observations: Iterable[Mapping[str, Any]],
    *,
    policy_version: str,
    active_min_signal_score: float,
    coverage_pct: float,
    evaluated_at: datetime,
) -> CandidateCalibrationResult:
    """Select a threshold on training sessions and gate it forward in time."""
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError("evaluated_at must be timezone-aware")
    active_threshold = _bounded_number(
        active_min_signal_score,
        "active_min_signal_score",
    )
    coverage = _bounded_number(coverage_pct, "coverage_pct")
    if not 0 <= active_threshold <= 100 or not 0 <= coverage <= 100:
        raise ValueError("calibration percentages are out of range")

    normalized = []
    for row in observations:
        session_date = row.get("session_date")
        if not isinstance(session_date, date):
            raise ValueError("session_date must be a date")
        score = _bounded_number(row.get("signal_score"), "signal_score")
        return_bps = _bounded_number(row.get("return_bps"), "return_bps")
        if not 0 <= score <= 100:
            raise ValueError("signal_score must be between 0 and 100")
        normalized.append((session_date, score, return_bps))

    session_dates = sorted({row[0] for row in normalized})
    validation_dates = session_dates[-_VALIDATION_SESSIONS:]
    train_dates = session_dates[
        max(0, len(session_dates) - _VALIDATION_SESSIONS - _TRAIN_SESSIONS):
        max(0, len(session_dates) - _VALIDATION_SESSIONS)
    ]
    train_set = set(train_dates)
    validation_set = set(validation_dates)
    train = [row for row in normalized if row[0] in train_set]
    validation = [row for row in normalized if row[0] in validation_set]

    common = {
        "policy_version": policy_version,
        "evaluated_at": evaluated_at,
        "train_dates": train_dates,
        "validation_dates": validation_dates,
        "train_outcomes": len(train),
        "validation_outcomes": len(validation),
        "coverage_pct": coverage,
        "active_min_signal_score": active_threshold,
    }
    if coverage < _MIN_COVERAGE_PCT:
        return _result(
            **common,
            status="COLLECTING",
            reason_code="OUTCOME_COVERAGE_INCOMPLETE",
        )
    if (
        len(train_dates) < _TRAIN_SESSIONS
        or len(validation_dates) < _VALIDATION_SESSIONS
    ):
        return _result(
            **common,
            status="COLLECTING",
            reason_code="INSUFFICIENT_SESSIONS",
        )
    if len(train) < _MIN_SAMPLE or len(validation) < _MIN_SAMPLE:
        return _result(
            **common,
            status="COLLECTING",
            reason_code="INSUFFICIENT_OUTCOMES",
        )

    baseline_train = [
        return_bps
        for _, score, return_bps in train
        if score >= active_threshold
    ]
    baseline_validation = [
        return_bps
        for _, score, return_bps in validation
        if score >= active_threshold
    ]
    threshold_metrics = []
    for threshold in _THRESHOLDS:
        if threshold <= active_threshold:
            continue
        train_returns = [
            return_bps
            for _, score, return_bps in train
            if score >= threshold
        ]
        validation_returns = [
            return_bps
            for _, score, return_bps in validation
            if score >= threshold
        ]
        if (
            len(train_returns) < _MIN_SAMPLE
            or len(validation_returns) < _MIN_SAMPLE
        ):
            continue
        threshold_metrics.append({
            "threshold": threshold,
            "train_outcomes": len(train_returns),
            "validation_outcomes": len(validation_returns),
            "train_mean_bps": _mean(train_returns),
            "train_lower_95_bps": _lower_confidence_bound(
                train_returns
            ),
            "validation_mean_bps": _mean(validation_returns),
            "validation_positive_rate_pct": (
                100
                * sum(value > 0 for value in validation_returns)
                / len(validation_returns)
            ),
        })

    metrics = {
        "walk_forward": {
            "train_sessions": len(train_dates),
            "validation_sessions": len(validation_dates),
            "train_start": train_dates[0].isoformat(),
            "train_end": train_dates[-1].isoformat(),
            "validation_start": validation_dates[0].isoformat(),
            "validation_end": validation_dates[-1].isoformat(),
        },
        "thresholds": threshold_metrics,
    }
    if not threshold_metrics or not baseline_validation:
        return _result(
            **common,
            status="COLLECTING",
            reason_code="INSUFFICIENT_THRESHOLD_SAMPLES",
            metrics=metrics,
        )

    challenger = max(
        threshold_metrics,
        key=lambda item: (
            item["train_lower_95_bps"],
            item["train_mean_bps"],
            -item["threshold"],
        ),
    )
    baseline_validation_mean = _mean(baseline_validation)
    challenger_validation_mean = float(
        challenger["validation_mean_bps"]
    )
    improvement = (
        challenger_validation_mean - baseline_validation_mean
    )
    train_improvement = (
        float(challenger["train_mean_bps"]) - _mean(baseline_train)
    )
    passed = (
        train_improvement >= _MIN_FORWARD_IMPROVEMENT_BPS
        and improvement >= _MIN_FORWARD_IMPROVEMENT_BPS
        and challenger["validation_positive_rate_pct"] >= 50
    )
    return _result(
        **common,
        status="CHALLENGER" if passed else "REJECTED",
        reason_code=(
            "FORWARD_GATE_PASSED"
            if passed
            else "FORWARD_GATE_FAILED"
        ),
        challenger_min_signal_score=float(challenger["threshold"]),
        baseline_validation_mean_bps=round(
            baseline_validation_mean,
            4,
        ),
        challenger_validation_mean_bps=round(
            challenger_validation_mean,
            4,
        ),
        validation_improvement_bps=round(improvement, 4),
        metrics=metrics,
    )
