from datetime import datetime, timezone

import pytest

from src.main import run_evening_routine


FRIDAY = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)


class EveningDatabase:
    def __init__(self):
        self.snapshots = 0
        self.snapshot_times = []
        self.events = []
        self.observations = []

    def save_portfolio_snapshot(self, *, recorded_at):
        self.snapshots += 1
        self.snapshot_times.append(recorded_at)
        self.events.append("snapshot")

    def record_post_close_benchmark_observation(self, *, observed_at):
        self.observations.append(observed_at)
        self.events.append("observation")
        return 42


class EveningTrader:
    def __init__(self):
        self.validations = []
        self.reviews = []
        self.extractions = 0

    def validate_hypotheses(self, *, days_to_check, now):
        self.validations.append((days_to_check, now))
        return [{"ticker": "VOLV-B"}]

    def run_weekly_review(self, *, now):
        self.reviews.append(now)

    def extract_learnings(self):
        self.extractions += 1


def test_evening_routine_uses_one_injected_clock_for_learning_and_review():
    database = EveningDatabase()
    trader = EveningTrader()

    result = run_evening_routine(
        database,
        analyzer=None,
        trader=trader,
        now=FRIDAY,
    )

    assert result == [{"ticker": "VOLV-B"}]
    assert trader.validations == [(14, FRIDAY)]
    assert trader.reviews == [FRIDAY]
    assert trader.extractions == 1
    assert database.snapshots == 1
    assert database.snapshot_times == [FRIDAY]
    assert database.observations == [FRIDAY]
    assert database.events == ["snapshot", "observation"]


def test_evening_routine_rejects_naive_clock_before_side_effects():
    database = EveningDatabase()
    trader = EveningTrader()

    with pytest.raises(ValueError, match="timezone-aware"):
        run_evening_routine(
            database,
            analyzer=None,
            trader=trader,
            now=datetime(2026, 7, 31, 20, 0),
        )

    assert trader.validations == []
    assert trader.reviews == []
    assert trader.extractions == 0
    assert database.snapshots == 0
    assert database.snapshot_times == []
    assert database.observations == []
