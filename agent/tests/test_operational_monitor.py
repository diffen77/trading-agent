from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from src.healthcheck import HealthCheck, HealthReport
from src.operational_monitor import (
    MonitorRun,
    _seconds_until_next_phase,
    evaluate_operational_alerts,
    main,
    run_monitor_once,
)


NOW = datetime(2026, 7, 30, 10, 30, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


def _report(mode: str, *checks: HealthCheck) -> HealthReport:
    return HealthReport(
        mode=mode,
        status=(
            "READY"
            if checks and all(check.ok for check in checks)
            else "NOT_READY"
        ),
        observed_at=NOW.isoformat(),
        checks=checks,
    )


def test_monitor_maps_fail_closed_evidence_to_actionable_alerts():
    readiness = _report(
        "readiness",
        HealthCheck("database_schema", False, "secret schema detail"),
        HealthCheck("ledger_balance", False, "secret ledger detail"),
    )
    trading = _report(
        "trading-readiness",
        HealthCheck("database_schema", False, "duplicate failure"),
        HealthCheck("operational_market_data", False, "secret feed detail"),
    )

    alerts = evaluate_operational_alerts(
        readiness=readiness,
        trading_readiness=trading,
        missed_routine_keys=("2026-07-30:open",),
        critical_incident_count=2,
    )

    assert [alert.code for alert in alerts] == [
        "SCHEMA_OR_MIGRATION_NOT_READY",
        "LEDGER_INVARIANT_FAILED",
        "MARKET_DATA_NOT_READY",
        "SCHEDULED_ROUTINE_MISSED",
        "CRITICAL_BENCHMARK_INCIDENT",
    ]
    assert all(alert.severity == "PAGE" for alert in alerts)
    assert all(alert.runbook == "docs/operations.md" for alert in alerts)
    assert all("secret" not in alert.summary for alert in alerts)
    assert alerts[3].alert_key == "scheduled:2026-07-30:open"


def test_monitor_is_quiet_when_all_evidence_is_healthy():
    readiness = _report(
        "readiness",
        HealthCheck("database_schema", True, "schema ready"),
        HealthCheck("ledger_balance", True, "ledger ready"),
    )
    trading = _report(
        "trading-readiness",
        HealthCheck("operational_market_data", True, "feed ready"),
    )

    assert evaluate_operational_alerts(
        readiness=readiness,
        trading_readiness=trading,
        missed_routine_keys=(),
        critical_incident_count=0,
    ) == ()


def test_closed_market_suppresses_market_data_alert():
    healthy = _report(
        "readiness",
        HealthCheck("database_schema", True, "schema ready"),
    )
    trading = _report(
        "trading-readiness",
        HealthCheck("operational_market_data", False, "feed unavailable"),
        HealthCheck("market_index_signal", False, "index unavailable"),
    )

    assert evaluate_operational_alerts(
        readiness=healthy,
        trading_readiness=trading,
        missed_routine_keys=(),
        critical_incident_count=0,
        market_data_expected=False,
    ) == ()


def test_monitor_pages_when_continuous_learning_is_stale():
    readiness = _report(
        "readiness",
        HealthCheck(
            "continuous_learning_worker",
            False,
            "private worker detail",
        ),
    )

    alerts = evaluate_operational_alerts(
        readiness=readiness,
        trading_readiness=_report(
            "trading-readiness",
            HealthCheck("operational_market_data", True, "ready"),
        ),
        missed_routine_keys=(),
        critical_incident_count=0,
    )

    assert [alert.code for alert in alerts] == [
        "CONTINUOUS_LEARNING_NOT_READY"
    ]
    assert "private" not in alerts[0].summary


def test_monitor_rejects_ambiguous_or_unbounded_inputs():
    healthy = _report(
        "readiness",
        HealthCheck("database_schema", True, "schema ready"),
    )

    for invalid_count in (-1, True, 1.5):
        try:
            evaluate_operational_alerts(
                readiness=healthy,
                trading_readiness=healthy,
                missed_routine_keys=(),
                critical_incident_count=invalid_count,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid incident count was accepted")


def test_monitoring_migration_persists_append_only_evidence():
    migration = (
        ROOT / "db/migrations/024_operational_alerting.sql"
    ).read_text()

    assert "CREATE TABLE IF NOT EXISTS scheduled_routine_events" in migration
    assert "CREATE TABLE IF NOT EXISTS operational_alert_events" in migration
    assert "scheduled_routine_events_append_only" in migration
    assert "operational_alert_events_append_only" in migration


def test_monitor_run_persists_transitions_and_notifies_only_opened(
    monkeypatch,
):
    readiness = _report(
        "readiness",
        HealthCheck("ledger_balance", False, "private ledger detail"),
    )
    trading = _report(
        "trading-readiness",
        HealthCheck(
            "operational_market_data",
            False,
            "private provider detail",
        ),
    )

    def collect(*, mode, **_values):
        return readiness if mode.value == "readiness" else trading

    class Session:
        opens_at = datetime(2026, 7, 30, 7, 0, tzinfo=timezone.utc)
        closes_at = datetime(2026, 7, 30, 15, 30, tzinfo=timezone.utc)

        def is_open(self, now):
            return self.opens_at <= now <= self.closes_at

    class Database:
        def __init__(self):
            self.synced = ()

        def get_successful_scheduled_routine_keys(self, _date):
            return (
                "2026-07-30:morning",
                "2026-07-30:open",
                "2026-07-30:midday",
            )

        def get_market_session(self, mic, _date):
            assert mic == "XSTO"
            return Session()

        def query(self, sql, _params=None):
            assert "paper_benchmark_incidents" in sql
            return [{"critical_incident_count": 1}]

        def sync_operational_alerts(self, alerts, *, observed_at):
            assert observed_at == NOW
            self.synced = alerts
            return (
                {
                    "alert_key": alerts[0].alert_key,
                    "code": alerts[0].code,
                    "severity": alerts[0].severity,
                    "state": "OPEN",
                    "summary": alerts[0].summary,
                    "runbook": alerts[0].runbook,
                },
                {
                    "alert_key": "old-alert",
                    "code": "OLD_ALERT",
                    "severity": "PAGE",
                    "state": "RESOLVED",
                    "summary": "Resolved",
                    "runbook": "docs/operations.md",
                },
            )

    class Notifier:
        def __init__(self):
            self.events = []

        def notify_operational_alert(self, event):
            self.events.append(event)
            return True

    monkeypatch.setattr(
        "src.operational_monitor.collect_health_report",
        collect,
    )
    database = Database()
    notifier = Notifier()

    result = run_monitor_once(
        database=database,
        observed_at=NOW,
        notifier=notifier,
        model_probe=lambda _environ: True,
        environ={},
    )

    assert result.status == "ALERTING"
    assert [alert.code for alert in database.synced] == [
        "LEDGER_INVARIANT_FAILED",
        "MARKET_DATA_NOT_READY",
        "CRITICAL_BENCHMARK_INCIDENT",
    ]
    assert [event["state"] for event in notifier.events] == ["OPEN"]


def test_monitor_cli_emits_bounded_json(monkeypatch, capsys):
    alert = evaluate_operational_alerts(
        readiness=_report(
            "readiness",
            HealthCheck("ledger_balance", False, "private detail"),
        ),
        trading_readiness=_report(
            "trading-readiness",
            HealthCheck("operational_market_data", True, "ready"),
        ),
        missed_routine_keys=(),
        critical_incident_count=0,
    )[0]
    result = MonitorRun(
        status="ALERTING",
        observed_at=NOW.isoformat(),
        alerts=(alert,),
        transitions=(),
    )
    monkeypatch.setattr(
        "src.operational_monitor.run_monitor_once",
        lambda **_values: result,
    )

    exit_code = main(
        [],
        now_factory=lambda: NOW,
        database_factory=lambda: object(),
        notifier_factory=lambda: object(),
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 1
    assert payload["event"] == "operational_monitor_completed"
    assert payload["alerts"][0]["code"] == "LEDGER_INVARIANT_FAILED"
    assert "private detail" not in output


def test_monitor_daemon_deduplicates_database_outage_notifications(
    monkeypatch,
):
    class Notifier:
        def __init__(self):
            self.events = []

        def notify_operational_alert(self, event):
            self.events.append(event)
            return True

    notifier = Notifier()
    sleeps = []

    def sleep(_seconds):
        sleeps.append(True)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("src.operational_monitor.time.sleep", sleep)

    with pytest.raises(KeyboardInterrupt):
        main(
            ["daemon"],
            now_factory=lambda: NOW,
            database_factory=lambda: (_ for _ in ()).throw(
                RuntimeError("private database error")
            ),
            notifier_factory=lambda: notifier,
        )

    assert len(notifier.events) == 1


def test_monitor_phase_wait_is_wall_clock_aligned():
    assert _seconds_until_next_phase(
        now_epoch=5.0,
        interval_seconds=60,
        phase_seconds=30,
    ) == pytest.approx(25.0)
    assert _seconds_until_next_phase(
        now_epoch=35.0,
        interval_seconds=60,
        phase_seconds=30,
    ) == pytest.approx(55.0)
    assert _seconds_until_next_phase(
        now_epoch=90.0,
        interval_seconds=60,
        phase_seconds=30,
    ) == pytest.approx(60.0)


def test_monitor_daemon_realigns_after_processing_without_cadence_drift(
    monkeypatch,
):
    class Notifier:
        def notify_operational_alert(self, _event):
            return True

    monkeypatch.setenv("OPERATIONAL_MONITOR_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("OPERATIONAL_MONITOR_PHASE_SECONDS", "30")

    wall_times = iter((5.0, 35.0))
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        main(
            ["daemon"],
            now_factory=lambda: NOW,
            database_factory=lambda: (_ for _ in ()).throw(
                RuntimeError("private database error")
            ),
            notifier_factory=Notifier,
            wall_clock=lambda: next(wall_times),
            sleeper=sleep,
        )

    assert sleeps == pytest.approx([25.0, 55.0])


def test_monitor_cli_bounds_internal_monitor_failures(
    monkeypatch,
    capsys,
):
    class Notifier:
        def __init__(self):
            self.events = []

        def notify_operational_alert(self, event):
            self.events.append(event)
            return True

    notifier = Notifier()
    monkeypatch.setattr(
        "src.operational_monitor.run_monitor_once",
        lambda **_values: (_ for _ in ()).throw(
            RuntimeError("private monitor failure")
        ),
    )

    exit_code = main(
        [],
        now_factory=lambda: NOW,
        database_factory=lambda: object(),
        notifier_factory=lambda: notifier,
    )

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 1
    assert payload["alerts"][0]["code"] == (
        "MONITOR_EVIDENCE_UNAVAILABLE"
    )
    assert "private monitor failure" not in output
    assert len(notifier.events) == 1
