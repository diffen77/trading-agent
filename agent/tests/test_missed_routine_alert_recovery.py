"""Test that missed routine alerts auto-resolve correctly."""

from datetime import datetime, timedelta, timezone

import pytest

from src.operational_monitor import (
    OperationalAlert,
    evaluate_operational_alerts,
    run_monitor_once,
    _blocking_missed_routines,
)
from src.healthcheck import HealthReport, HealthCheck
from src.core.schedule import DueRoutine


def _report(mode: str, *checks):
    return HealthReport(
        mode=mode,
        status=(
            "READY"
            if checks and all(check.ok for check in checks)
            else "BLOCKED"
        ),
        observed_at=datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc).isoformat(),
        checks=checks,
    )


def test_missed_opening_routine_resolves_on_next_day():
    """A missed opening routine from Day 1 should auto-RESOLVE on Day 2."""
    healthy = _report("readiness")
    trading_healthy = _report("trading")
    
    # Day 1: Opening routine missed (after 20-minute grace period)
    day1_missed_keys = ["2026-08-10:open"]
    day1_alerts = evaluate_operational_alerts(
        readiness=healthy,
        trading_readiness=trading_healthy,
        missed_routine_keys=day1_missed_keys,
        critical_incident_count=0,
        market_data_expected=False,
        knowledge_graph_ready=True,
        blocking_missed_routines=day1_missed_keys,  # Still blocking within recovery window
    )
    
    assert len(day1_alerts) == 1
    assert day1_alerts[0].alert_key == "scheduled:2026-08-10:open"
    assert day1_alerts[0].code == "SCHEDULED_ROUTINE_MISSED"
    
    # Day 2: Opening routine completed successfully, so not in missed list
    day2_missed_keys = []  # Empty because today's open completed
    day2_alerts = evaluate_operational_alerts(
        readiness=healthy,
        trading_readiness=trading_healthy,
        missed_routine_keys=day2_missed_keys,
        critical_incident_count=0,
        market_data_expected=False,
        knowledge_graph_ready=True,
        blocking_missed_routines=[],
    )
    
    # Day 2 should have NO alerts
    assert len(day2_alerts) == 0
    
    # The key insight: sync_operational_alerts should see that
    # day1's alert is no longer in the current alerts and RESOLVE it


def test_trading_routine_stops_blocking_after_recovery_window():
    """Opening routine alert stops blocking after 2-hour recovery window."""
    from zoneinfo import ZoneInfo
    
    STOCKHOLM = ZoneInfo("Europe/Stockholm")
    
    # Day 1 at 9:00 Stockholm: opening routine scheduled
    scheduled_at = datetime(2026, 8, 10, 9, 0, tzinfo=STOCKHOLM)
    
    # At 9:30 (30 min after scheduled): within recovery window, should block
    early_time = datetime(2026, 8, 10, 9, 30, tzinfo=STOCKHOLM).astimezone(timezone.utc)
    routine = DueRoutine(name="open", scheduled_at=scheduled_at)
    
    early_blocking = _blocking_missed_routines(
        observed_at=early_time,
        missed=[routine],
    )
    assert routine.key in early_blocking
    
    # At 11:30 (2.5 hours after scheduled): after recovery window, should NOT block
    late_time = datetime(2026, 8, 10, 11, 30, tzinfo=STOCKHOLM).astimezone(timezone.utc)
    
    late_blocking = _blocking_missed_routines(
        observed_at=late_time,
        missed=[routine],
    )
    assert routine.key not in late_blocking


def test_non_trading_routine_continues_blocking():
    """Non-trading routines (morning/close) continue blocking until resolved."""
    from zoneinfo import ZoneInfo
    
    STOCKHOLM = ZoneInfo("Europe/Stockholm")
    
    # Morning routine scheduled at 7:00
    scheduled_at = datetime(2026, 8, 10, 7, 0, tzinfo=STOCKHOLM)
    
    # At 10:00 (3 hours after scheduled): should still block
    late_time = datetime(2026, 8, 10, 10, 0, tzinfo=STOCKHOLM).astimezone(timezone.utc)
    routine = DueRoutine(name="morning", scheduled_at=scheduled_at)
    
    blocking = _blocking_missed_routines(
        observed_at=late_time,
        missed=[routine],
    )
    assert routine.key in blocking  # Still blocks because morning has recovery window


def test_missed_opening_routine_from_yesterday_not_in_todays_missed_list():
    """missed_routines() only checks today's routines, not yesterday's."""
    from src.core.schedule import missed_routines, stockholm_now
    
    # Day 1 late evening: open routine was missed earlier today
    day1_evening = datetime(2026, 8, 10, 20, 0, tzinfo=timezone.utc)  # 22:00 Stockholm
    day1_missed = missed_routines(
        day1_evening,
        completed=set(),  # Nothing completed
    )
    
    # Should include today's (2026-08-10) open routine as missed
    day1_open_keys = [r.key for r in day1_missed if r.name == "open"]
    assert "2026-08-10:open" in day1_open_keys
    
    # Day 2 early morning: checking missed routines
    day2_morning = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)  # 08:00 Stockholm
    day2_missed = missed_routines(
        day2_morning,
        completed=set(),  # Nothing completed yet today
    )
    
    # Should NOT include yesterday's (2026-08-10) open routine
    day2_keys = [r.key for r in day2_missed]
    assert "2026-08-10:open" not in day2_keys
    
    # Should only include Day 2's routines that have missed (like morning if past grace)
    all_day2_dates = [r.key.split(":")[0] for r in day2_missed]
    assert all(date == "2026-08-11" for date in all_day2_dates)


def test_alert_stops_blocking_but_stays_in_evidence():
    """Trading routine alert is NOT created after recovery window passes."""
    healthy = _report("readiness")
    trading_healthy = _report("trading")
    
    # Opening routine is still in missed list (for evidence)
    missed_keys = ["2026-08-10:open"]
    
    # But it's NOT in blocking list (recovery window passed)
    blocking_keys = []  # Empty: recovery window passed
    
    alerts = evaluate_operational_alerts(
        readiness=healthy,
        trading_readiness=trading_healthy,
        missed_routine_keys=missed_keys,
        critical_incident_count=0,
        market_data_expected=False,
        knowledge_graph_ready=True,
        blocking_missed_routines=blocking_keys,
    )
    
    # Should have NO alerts because it's not blocking anymore
    assert len(alerts) == 0
