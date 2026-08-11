from datetime import datetime, timedelta, timezone

import pytest

from src.core.schedule import (
    BRAIN_CYCLE_INTERVAL_MINUTES,
    brain_cycle_slot,
    due_routines,
    missed_routines,
    provider_routine_grace_periods,
    recoverable_routines,
    recovery_slots,
    stockholm_now,
)


def test_brain_cycle_slot_advances_every_fifteen_minutes():
    assert BRAIN_CYCLE_INTERVAL_MINUTES == 15

    first = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)
    same_slot = datetime(2026, 8, 6, 8, 14, 59, tzinfo=timezone.utc)
    next_slot = datetime(2026, 8, 6, 8, 15, tzinfo=timezone.utc)

    assert brain_cycle_slot(first) == brain_cycle_slot(same_slot)
    assert brain_cycle_slot(next_slot) == brain_cycle_slot(first) + 1


def test_brain_cycle_slot_rejects_naive_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        brain_cycle_slot(datetime(2026, 8, 6, 8, 0))


def test_stockholm_now_handles_summer_and_winter_time():
    summer = stockholm_now(
        datetime(2026, 7, 29, 5, 0, tzinfo=timezone.utc)
    )
    winter = stockholm_now(
        datetime(2026, 1, 29, 6, 0, tzinfo=timezone.utc)
    )

    assert (summer.hour, summer.utcoffset().total_seconds()) == (7, 7200)
    assert (winter.hour, winter.utcoffset().total_seconds()) == (7, 3600)


def test_due_routine_uses_local_time_and_is_returned_once():
    now = datetime(2026, 7, 29, 5, 5, tzinfo=timezone.utc)

    due = due_routines(now, completed=set())

    assert [routine.name for routine in due] == ["morning"]
    completed = {due[0].key}
    assert due_routines(now, completed=completed) == []


def test_close_summary_runs_after_market_close_not_at_fixed_utc_hour():
    before = datetime(2026, 7, 29, 15, 35, tzinfo=timezone.utc)
    after = datetime(2026, 7, 29, 15, 45, tzinfo=timezone.utc)

    assert "close" not in {
        routine.name for routine in due_routines(before, completed=set())
    }
    assert "close" in {
        routine.name for routine in due_routines(after, completed=set())
    }


def test_missed_routine_is_not_replayed_hours_late():
    late = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)

    assert due_routines(late, completed=set()) == []


def test_missed_routines_are_reported_after_grace_period():
    now = datetime(2026, 7, 29, 10, 30, tzinfo=timezone.utc)

    missed = missed_routines(
        now,
        completed={"2026-07-29:open"},
    )

    assert [routine.key for routine in missed] == [
        "2026-07-29:morning",
        "2026-07-29:midday",
    ]


def test_routine_is_not_missed_during_its_grace_period():
    now = datetime(2026, 7, 29, 10, 15, tzinfo=timezone.utc)

    assert "midday" not in {
        routine.name
        for routine in missed_routines(now, completed=set())
    }


def test_delayed_provider_extends_open_grace_through_delivery_window():
    grace_periods = provider_routine_grace_periods({
        "data_type": "delayed-pre-trade-equity",
        "nominal_delay_seconds": 900,
        "max_transport_lag_seconds": 300,
    })
    first_executable_book = datetime(
        2026,
        8,
        10,
        7,
        20,
        45,
        tzinfo=timezone.utc,
    )

    assert "open" in {
        routine.name
        for routine in due_routines(
            first_executable_book,
            completed=set(),
            grace_periods=grace_periods,
        )
    }
    assert "open" not in {
        routine.name
        for routine in missed_routines(
            first_executable_book,
            completed=set(),
            grace_periods=grace_periods,
        )
    }


def test_delayed_provider_open_grace_remains_bounded():
    grace_periods = provider_routine_grace_periods({
        "data_type": "delayed-pre-trade-equity",
        "nominal_delay_seconds": 900,
        "max_transport_lag_seconds": 300,
    })
    after_delivery_window = datetime(
        2026,
        8,
        10,
        7,
        21,
        1,
        tzinfo=timezone.utc,
    )

    assert "open" in {
        routine.name
        for routine in missed_routines(
            after_delivery_window,
            completed=set(),
            grace_periods=grace_periods,
        )
    }


def test_naive_utc_input_is_rejected():
    with pytest.raises(ValueError):
        stockholm_now(datetime(2026, 7, 29, 5, 0))


def test_recovery_slots_backfill_a_bounded_gap_and_include_current_slot():
    last_completed = datetime(2026, 8, 7, 7, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 7, 11, 34, tzinfo=timezone.utc)

    assert recovery_slots(
        now,
        interval_minutes=60,
        last_completed_at=last_completed,
        max_backfill_slots=3,
    ) == (
        datetime(2026, 8, 7, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 11, 0, tzinfo=timezone.utc),
    )


def test_recovery_slots_do_not_invent_history_on_first_start():
    now = datetime(2026, 8, 7, 11, 34, tzinfo=timezone.utc)

    assert recovery_slots(
        now,
        interval_minutes=15,
        last_completed_at=None,
        max_backfill_slots=8,
    ) == (datetime(2026, 8, 7, 11, 30, tzinfo=timezone.utc),)


def test_morning_routine_is_recoverable_after_normal_grace_window():
    now = datetime(2026, 8, 7, 6, 30, tzinfo=timezone.utc)

    routines = recoverable_routines(now, completed=set())

    assert [(routine.name, routine.recovery) for routine in routines] == [
        ("morning", True),
    ]


def test_open_and_midday_routines_are_never_replayed_as_stale_trades():
    now = datetime(2026, 8, 7, 13, 45, tzinfo=timezone.utc)

    assert all(
        routine.name not in {"open", "midday"}
        for routine in recoverable_routines(now, completed=set())
    )
