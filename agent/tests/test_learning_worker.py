from datetime import datetime, timezone

from src.learning_worker import run_learning_cycle


NOW = datetime(2026, 8, 4, 20, 0, tzinfo=timezone.utc)


def test_learning_cycle_labels_outcomes_and_calibrates_when_market_is_closed():
    events = []

    class Database:
        def record_candidate_prediction_outcomes(self, *, evaluated_at):
            assert evaluated_at == NOW
            return 17

        def run_candidate_policy_calibration(self, *, evaluated_at):
            assert evaluated_at == NOW
            return {
                "id": 8,
                "status": "COLLECTING",
                "reason_code": "INSUFFICIENT_SESSIONS",
            }

        def record_continuous_learning_run(self, **values):
            events.append(values)
            return 5

    result = run_learning_cycle(Database(), evaluated_at=NOW)

    assert result.status == "SUCCEEDED"
    assert result.outcomes_labelled == 17
    assert result.calibration_run_id == 8
    assert events[0]["status"] == "SUCCEEDED"
    assert "market" not in events[0]


def test_learning_cycle_records_bounded_failure_evidence():
    events = []

    class Database:
        def record_candidate_prediction_outcomes(self, *, evaluated_at):
            raise RuntimeError("private database detail")

        def record_continuous_learning_run(self, **values):
            events.append(values)
            return 6

    result = run_learning_cycle(Database(), evaluated_at=NOW)

    assert result.status == "FAILED"
    assert result.error_code == "OUTCOME_EVALUATION_FAILED"
    assert events[0]["status"] == "FAILED"
    assert "private database detail" not in str(events)

