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

        def rollback_candidate_policy_automatically(self, *, evaluated_at):
            return {
                "status": "KEEP",
                "reason_code": "ACTIVE_POLICY_REMAINS_COMPETITIVE",
                "policy_version": "xsto-momentum-v1",
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


def test_learning_cycle_automatically_activates_forward_challenger():
    activations = []
    events = []

    class Database:
        def record_candidate_prediction_outcomes(self, *, evaluated_at):
            return 3

        def run_candidate_policy_calibration(self, *, evaluated_at):
            return {
                "id": 9,
                "status": "CHALLENGER",
                "reason_code": "FORWARD_GATE_PASSED",
                "challenger_policy_version": "xsto-challenger-9",
            }

        def activate_candidate_policy_automatically(
            self,
            version,
            *,
            activated_at,
        ):
            activations.append((version, activated_at))
            return True

        def record_continuous_learning_run(self, **values):
            events.append(values)
            return 7

    result = run_learning_cycle(Database(), evaluated_at=NOW)

    assert result.status == "SUCCEEDED"
    assert result.policy_action == "ACTIVATED"
    assert result.policy_version == "xsto-challenger-9"
    assert activations == [("xsto-challenger-9", NOW)]
    assert events[0]["status"] == "SUCCEEDED"


def test_learning_cycle_automatically_rolls_back_regressed_policy():
    events = []

    class Database:
        def record_candidate_prediction_outcomes(self, *, evaluated_at):
            return 8

        def run_candidate_policy_calibration(self, *, evaluated_at):
            return {
                "id": 10,
                "status": "COLLECTING",
                "reason_code": "INSUFFICIENT_SESSIONS",
            }

        def rollback_candidate_policy_automatically(self, *, evaluated_at):
            assert evaluated_at == NOW
            return {
                "status": "ROLLBACK",
                "reason_code": "PARENT_FORWARD_PERFORMANCE_RECOVERED",
                "policy_version": "xsto-rollback-2",
            }

        def record_continuous_learning_run(self, **values):
            events.append(values)
            return 8

    result = run_learning_cycle(Database(), evaluated_at=NOW)

    assert result.status == "SUCCEEDED"
    assert result.policy_action == "ROLLED_BACK"
    assert result.policy_version == "xsto-rollback-2"
    assert events[0]["status"] == "SUCCEEDED"
