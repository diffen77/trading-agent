"""Independent 24/7 outcome evaluation and candidate-policy calibration."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
import time

from .data.database import Database


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LearningCycleResult:
    status: str
    evaluated_at: str
    outcomes_labelled: int
    calibration_run_id: int | None
    calibration_status: str | None
    error_code: str | None

    def to_dict(self) -> dict:
        return {
            "event": "continuous_learning_cycle_completed",
            **asdict(self),
        }


def _run_key(evaluated_at: datetime) -> str:
    slot = int(evaluated_at.timestamp() // 300)
    return f"continuous-learning:{slot}"


def run_learning_cycle(
    database,
    *,
    evaluated_at: datetime,
) -> LearningCycleResult:
    """Run once without any market-hours gate and persist bounded evidence."""
    if (
        not isinstance(evaluated_at, datetime)
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError("evaluated_at must be timezone-aware")
    checked_at = evaluated_at.astimezone(timezone.utc)
    run_key = _run_key(checked_at)
    outcomes_labelled = 0
    calibration = None
    error_code = None
    try:
        outcomes_labelled = (
            database.record_candidate_prediction_outcomes(
                evaluated_at=checked_at,
            )
        )
    except Exception:
        logger.exception(
            "continuous_learning_failed stage=outcome_evaluation"
        )
        error_code = "OUTCOME_EVALUATION_FAILED"

    if error_code is None:
        try:
            calibration = database.run_candidate_policy_calibration(
                evaluated_at=checked_at,
            )
        except Exception:
            logger.exception(
                "continuous_learning_failed stage=policy_calibration"
            )
            error_code = "POLICY_CALIBRATION_FAILED"

    status = "FAILED" if error_code else "SUCCEEDED"
    calibration_run_id = (
        int(calibration["id"])
        if calibration and calibration.get("id") is not None
        else None
    )
    calibration_status = (
        str(calibration.get("status"))
        if calibration and calibration.get("status") is not None
        else None
    )
    try:
        database.record_continuous_learning_run(
            run_key=run_key,
            evaluated_at=checked_at,
            status=status,
            outcomes_labelled=outcomes_labelled,
            calibration_run_id=calibration_run_id,
            error_code=error_code,
        )
    except Exception:
        logger.exception(
            "continuous_learning_run_evidence_persistence_failed"
        )
        return LearningCycleResult(
            status="FAILED",
            evaluated_at=checked_at.isoformat(),
            outcomes_labelled=outcomes_labelled,
            calibration_run_id=calibration_run_id,
            calibration_status=calibration_status,
            error_code="RUN_EVIDENCE_PERSISTENCE_FAILED",
        )

    return LearningCycleResult(
        status=status,
        evaluated_at=checked_at.isoformat(),
        outcomes_labelled=outcomes_labelled,
        calibration_run_id=calibration_run_id,
        calibration_status=calibration_status,
        error_code=error_code,
    )


def _interval_seconds(environ) -> int:
    raw = environ.get("LEARNING_WORKER_INTERVAL_SECONDS", "300")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "LEARNING_WORKER_INTERVAL_SECONDS must be an integer"
        ) from exc
    if not 60 <= value <= 3600:
        raise ValueError(
            "LEARNING_WORKER_INTERVAL_SECONDS must be 60-3600"
        )
    return value


def main(
    argv: list[str] | None = None,
    *,
    database_factory=Database,
    now_factory=lambda: datetime.now(timezone.utc),
    sleeper=time.sleep,
    environ=None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate continuous paper-learning outcomes.",
    )
    parser.add_argument(
        "mode",
        choices=("once", "daemon"),
        nargs="?",
        default="once",
    )
    args = parser.parse_args(argv)
    environment = os.environ if environ is None else environ
    database = database_factory()

    if args.mode == "once":
        result = run_learning_cycle(
            database,
            evaluated_at=now_factory(),
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0 if result.status == "SUCCEEDED" else 1

    interval = _interval_seconds(environment)
    while True:
        result = run_learning_cycle(
            database,
            evaluated_at=now_factory(),
        )
        logger.info(
            "continuous_learning_cycle status=%s outcomes=%d "
            "calibration_status=%s error_code=%s",
            result.status,
            result.outcomes_labelled,
            result.calibration_status,
            result.error_code,
        )
        sleeper(interval)


if __name__ == "__main__":
    raise SystemExit(main())
