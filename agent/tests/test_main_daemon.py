from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.core.schedule import brain_cycle_slot
from src.main import run_daemon


NOW = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)


class FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        assert tz is timezone.utc
        return NOW


class ClosedMarketDatabase:
    def __init__(
        self,
        completed=(),
        *,
        completed_job_keys=(),
        latest_job_slots=None,
    ):
        self.completed = tuple(completed)
        self.routine_events = []
        self.completed_job_keys = set(completed_job_keys)
        self.latest_job_slots = dict(latest_job_slots or {})
        self.job_claims = []
        self.job_completions = []

    def get_successful_scheduled_routine_keys(self, session_date):
        assert session_date in {
            NOW.date() - timedelta(days=1),
            NOW.date(),
        }
        return tuple(
            key for key in self.completed if key.startswith(session_date.isoformat())
        )

    def record_scheduled_routine_event(self, **event):
        self.routine_events.append(event)

    def get_latest_terminal_scheduled_job_at(self, job_name):
        return self.latest_job_slots.get(job_name)

    def claim_scheduled_job_run(self, **claim):
        self.job_claims.append(claim)
        job_key = claim["job_key"]
        if job_key in self.completed_job_keys:
            return False
        self.completed_job_keys.add(job_key)
        return True

    def complete_scheduled_job_run(self, **completion):
        self.job_completions.append(completion)
        if completion["status"] in {"SUCCEEDED", "SKIPPED_STALE"}:
            claim = next(
                item
                for item in reversed(self.job_claims)
                if item["job_key"] == completion["job_key"]
            )
            self.latest_job_slots[claim["job_name"]] = claim["scheduled_at"]
        return True

    def get_market_session(
        self,
        mic,
        session_date,
    ) -> SimpleNamespace | None:
        assert mic == "XSTO"
        assert session_date == NOW.date()
        return None


class OpenMarketDatabase(ClosedMarketDatabase):
    def get_market_session(self, mic, session_date):
        assert mic == "XSTO"
        assert session_date == NOW.date()
        return SimpleNamespace(is_open=lambda now: now == NOW)

    def require_operational_market_data(self, now):
        assert now == NOW

    def save_portfolio_snapshot(self):
        pass


def test_brain_cycle_refreshes_prospects_before_analysis(monkeypatch):
    calls = []

    class Analyzer:
        def run_technical_analysis(self):
            calls.append("technical-analysis")
            return []

        def update_prospects(self, *, now):
            assert now == NOW
            calls.append("prospects")
            return 3

    class Trader:
        def check_positions(self, *, now):
            assert now == NOW
            calls.append("positions")

    class Brain:
        def run_cycle(self, trader, *, deep, cycle_key):
            assert deep is False
            assert cycle_key.startswith("scheduled-brain:")
            calls.append("brain")
            return {
                "outlook": "neutral",
                "decisions_raw": 0,
                "decisions_validated": 0,
                "decisions_executed": 0,
            }

    monkeypatch.setattr("src.main.datetime", FixedDateTime)
    monkeypatch.setattr(
        "src.main.due_routines",
        lambda _now, *, completed: (),
    )
    monkeypatch.setattr(
        "src.main.time.sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    run_daemon(
        OpenMarketDatabase(),
        analyzer=Analyzer(),
        trader=Trader(),
        brain=Brain(),
    )

    assert calls == [
        "technical-analysis",
        "positions",
        "prospects",
        "brain",
    ]


def test_brain_cycle_runs_again_in_next_fifteen_minute_slot(monkeypatch):
    second_cycle = NOW + timedelta(minutes=15)
    instants = iter((NOW, second_cycle))
    cycle_keys = []
    sleeps = []

    class AdvancingDateTime:
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return next(instants)

    class Database(ClosedMarketDatabase):
        def get_market_session(self, mic, session_date):
            assert mic == "XSTO"
            assert session_date == NOW.date()
            return SimpleNamespace(is_open=lambda _now: True)

        def require_operational_market_data(self, now):
            assert now in (NOW, second_cycle)

        def save_portfolio_snapshot(self):
            pass

    class Analyzer:
        def run_technical_analysis(self):
            return []

        def update_prospects(self, *, now):
            assert now in (NOW, second_cycle)
            return 3

    class Trader:
        def check_positions(self, *, now):
            assert now in (NOW, second_cycle)

    class Brain:
        def run_cycle(self, trader, *, deep, cycle_key):
            assert deep is False
            cycle_keys.append(cycle_key)
            return {
                "outlook": "neutral",
                "decisions_raw": 3,
                "decisions_validated": 0,
                "decisions_executed": 0,
            }

    def bounded_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("src.main.datetime", AdvancingDateTime)
    monkeypatch.setattr(
        "src.main.due_routines",
        lambda _now, *, completed: (),
    )
    monkeypatch.setattr("src.main.time.sleep", bounded_sleep)

    run_daemon(
        Database(),
        analyzer=Analyzer(),
        trader=Trader(),
        brain=Brain(),
    )

    assert len(cycle_keys) == 2
    assert cycle_keys[0] != cycle_keys[1]


def test_failed_scheduled_routine_is_retried_within_grace_period(
    monkeypatch,
):
    routine = SimpleNamespace(
        name="midday",
        scheduled_at=NOW,
        key="2026-07-29:midday",
    )
    attempts = []
    sleeps = []

    def due(_now, *, completed):
        return [] if routine.key in completed else [routine]

    def flaky_run_mode(*_args, **kwargs):
        attempts.append(kwargs["now"])
        if len(attempts) == 1:
            raise RuntimeError("temporary provider error")

    def bounded_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("src.main.datetime", FixedDateTime)
    monkeypatch.setattr("src.main.due_routines", due)
    monkeypatch.setattr("src.main.run_mode", flaky_run_mode)
    monkeypatch.setattr("src.main.time.sleep", bounded_sleep)

    database = ClosedMarketDatabase()
    run_daemon(
        database,
        analyzer=object(),
        trader=object(),
    )

    assert attempts == [NOW, NOW]
    assert [
        event["status"] for event in database.routine_events
    ] == ["FAILED", "SUCCEEDED"]
    assert database.routine_events[0]["failure_code"] == (
        "ROUTINE_EXECUTION_ERROR"
    )
    assert database.routine_events[1]["failure_code"] is None


def test_delayed_provider_can_complete_open_at_first_executable_book(
    monkeypatch,
):
    observed_at = datetime(
        2026,
        8,
        10,
        7,
        20,
        45,
        tzinfo=timezone.utc,
    )

    class OpenDelayDateTime:
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return observed_at

    class Database:
        def __init__(self):
            self.routine_events = []

        def get_successful_scheduled_routine_keys(self, session_date):
            if session_date == observed_at.date():
                return ("2026-08-10:morning",)
            return ()

        def get_authorized_market_data_mode(self, checked_at):
            assert checked_at == observed_at
            return {
                "data_type": "delayed-pre-trade-equity",
                "nominal_delay_seconds": 900,
                "max_transport_lag_seconds": 300,
            }

        def record_scheduled_routine_event(self, **event):
            self.routine_events.append(event)

        def get_market_session(self, mic, session_date):
            assert mic == "XSTO"
            assert session_date == observed_at.date()
            return SimpleNamespace(is_open=lambda _now: True)

        def require_operational_market_data(self, now):
            assert now == observed_at

        def save_portfolio_snapshot(self):
            pass

    class Analyzer:
        def find_opportunities(self, *, now):
            assert now == observed_at
            return []

        def update_prospects(self, *, now):
            assert now == observed_at

        def run_technical_analysis(self):
            return []

    class Trader:
        def check_positions(self, *, now):
            assert now == observed_at

    monkeypatch.setattr("src.main.datetime", OpenDelayDateTime)
    monkeypatch.setattr(
        "src.main.time.sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    database = Database()
    run_daemon(database, Analyzer(), Trader())

    assert [
        (event["routine_name"], event["status"])
        for event in database.routine_events
    ] == [("open", "SUCCEEDED")]


def test_restart_does_not_repeat_a_completed_brain_slot(monkeypatch):
    current_key = f"scheduled-brain:{brain_cycle_slot(NOW)}"
    database = OpenMarketDatabase(completed_job_keys=(current_key,))
    brain_calls = []

    class Analyzer:
        def run_technical_analysis(self):
            return []

        def update_prospects(self, *, now):
            raise AssertionError("completed brain slot must not be refreshed")

    class Trader:
        def check_positions(self, *, now):
            assert now == NOW

    class Brain:
        def run_cycle(self, trader, *, deep, cycle_key):
            brain_calls.append(cycle_key)
            raise AssertionError("completed brain slot must not run again")

    monkeypatch.setattr("src.main.datetime", FixedDateTime)
    monkeypatch.setattr("src.main.due_routines", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        "src.main.time.sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    run_daemon(database, Analyzer(), Trader(), brain=Brain())

    assert brain_calls == []


def test_restart_marks_old_brain_slots_stale_and_runs_only_current(monkeypatch):
    database = OpenMarketDatabase(
        latest_job_slots={"brain_cycle": NOW - timedelta(minutes=45)},
    )
    brain_calls = []

    class Analyzer:
        def run_technical_analysis(self):
            return []

        def update_prospects(self, *, now):
            assert now == NOW
            return 1

    class Trader:
        def check_positions(self, *, now):
            assert now == NOW

    class Brain:
        def run_cycle(self, trader, *, deep, cycle_key):
            brain_calls.append(cycle_key)
            return {
                "outlook": "neutral",
                "decisions_raw": 0,
                "decisions_validated": 0,
                "decisions_executed": 0,
            }

    monkeypatch.setattr("src.main.datetime", FixedDateTime)
    monkeypatch.setattr("src.main.due_routines", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        "src.main.time.sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    run_daemon(database, Analyzer(), Trader(), brain=Brain())

    assert brain_calls == [f"scheduled-brain:{brain_cycle_slot(NOW)}"]
    assert [item["status"] for item in database.job_completions] == [
        "SKIPPED_STALE",
        "SKIPPED_STALE",
        "SUCCEEDED",
    ]


def test_restart_backfills_missed_closed_market_study_slots(monkeypatch):
    database = ClosedMarketDatabase(
        latest_job_slots={"student_study": NOW - timedelta(hours=3)},
    )
    study_times = []

    class Student:
        def study_cycle(self, *, now):
            study_times.append(now)
            return {
                "studies_completed": [],
                "insights_generated": 0,
                "learnings_added": 0,
            }

    monkeypatch.setattr("src.main.datetime", FixedDateTime)
    monkeypatch.setattr("src.main.due_routines", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        "src.main.time.sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    run_daemon(
        database,
        analyzer=SimpleNamespace(),
        trader=SimpleNamespace(),
        student=Student(),
    )

    assert study_times == [
        NOW - timedelta(hours=2),
        NOW - timedelta(hours=1),
        NOW,
    ]
    assert all(
        item["status"] == "SUCCEEDED"
        for item in database.job_completions
    )


def test_recovered_morning_runs_analysis_without_replaying_stale_trade_cycle(
    monkeypatch,
):
    recovered_now = datetime(2026, 7, 29, 6, 30, tzinfo=timezone.utc)
    morning_calls = []

    class RecoveredDateTime:
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc
            return recovered_now

    class Brain:
        def run_cycle(self, *_args, **_kwargs):
            raise AssertionError("recovered morning must not execute stale trades")

    database = ClosedMarketDatabase()
    monkeypatch.setattr("src.main.datetime", RecoveredDateTime)
    monkeypatch.setattr("src.main.due_routines", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        "src.main.run_morning_routine",
        lambda db, analyzer: morning_calls.append((db, analyzer)),
    )
    monkeypatch.setattr(
        "src.main.time.sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    analyzer = SimpleNamespace()

    run_daemon(
        database,
        analyzer=analyzer,
        trader=SimpleNamespace(),
        brain=Brain(),
    )

    assert morning_calls == [(database, analyzer)]
    assert database.routine_events[-1]["status"] == "SUCCEEDED"
