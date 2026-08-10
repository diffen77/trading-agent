"""Deterministic, non-sensitive operational alert evaluation."""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import time
from typing import Iterable

from .core.notifier import TelegramNotifier
from .core.schedule import missed_routines, stockholm_now
from .data.database import Database
from .healthcheck import (
    HealthMode,
    HealthReport,
    collect_health_report,
)


@dataclass(frozen=True)
class OperationalAlert:
    alert_key: str
    code: str
    severity: str
    summary: str
    runbook: str = "docs/operations.md"


@dataclass(frozen=True)
class MonitorRun:
    status: str
    observed_at: str
    alerts: tuple[OperationalAlert, ...]
    transitions: tuple[dict, ...]

    def to_dict(self) -> dict:
        return {
            "event": "operational_monitor_completed",
            "status": self.status,
            "observed_at": self.observed_at,
            "alerts": [asdict(alert) for alert in self.alerts],
            "transitions": list(self.transitions),
        }


_CHECK_ALERTS = {
    "database_schema": (
        "schema",
        "SCHEMA_OR_MIGRATION_NOT_READY",
        "Database or required schema is not ready",
    ),
    "ledger_balance": (
        "ledger",
        "LEDGER_INVARIANT_FAILED",
        "Paper ledger invariant is not satisfied",
    ),
    "operational_market_data": (
        "market-data",
        "MARKET_DATA_NOT_READY",
        "Authorized XSTO market data is stale or incomplete",
    ),
    "continuous_learning_worker": (
        "continuous-learning",
        "CONTINUOUS_LEARNING_NOT_READY",
        "Continuous learning worker is stale or failed",
    ),
    "scheduled_job_recovery": (
        "scheduled-jobs",
        "SCHEDULED_JOB_RECOVERY_NOT_READY",
        "Recurring analysis or study scheduler is stale after restart",
    ),
}


def evaluate_operational_alerts(
    *,
    readiness: HealthReport,
    trading_readiness: HealthReport,
    missed_routine_keys: Iterable[str],
    critical_incident_count: int,
    market_data_expected: bool = True,
) -> tuple[OperationalAlert, ...]:
    """Map bounded health evidence to stable, actionable alert identities."""
    if (
        isinstance(critical_incident_count, bool)
        or not isinstance(critical_incident_count, int)
        or critical_incident_count < 0
    ):
        raise ValueError("critical_incident_count must be a non-negative int")

    alerts: list[OperationalAlert] = []
    seen_codes: set[str] = set()
    for report in (readiness, trading_readiness):
        if not isinstance(report, HealthReport):
            raise ValueError("health evidence must be a HealthReport")
        for check in report.checks:
            definition = _CHECK_ALERTS.get(check.code)
            if check.ok or definition is None:
                continue
            if (
                check.code == "operational_market_data"
                and not market_data_expected
            ):
                continue
            alert_key, code, summary = definition
            if code in seen_codes:
                continue
            alerts.append(
                OperationalAlert(alert_key, code, "PAGE", summary)
            )
            seen_codes.add(code)

    for routine_key in missed_routine_keys:
        if not isinstance(routine_key, str) or not routine_key:
            raise ValueError("missed routine keys must be non-empty strings")
        alerts.append(
            OperationalAlert(
                f"scheduled:{routine_key}",
                "SCHEDULED_ROUTINE_MISSED",
                "PAGE",
                f"Scheduled routine {routine_key} did not complete",
            )
        )

    if critical_incident_count:
        alerts.append(
            OperationalAlert(
                "benchmark-critical-incident",
                "CRITICAL_BENCHMARK_INCIDENT",
                "PAGE",
                "Forward paper benchmark has a critical incident",
            )
        )
    return tuple(alerts)


def run_monitor_once(
    *,
    database,
    observed_at: datetime,
    notifier=None,
    environ=None,
    model_probe=None,
) -> MonitorRun:
    """Evaluate, persist and optionally deliver one monitor observation."""
    if (
        not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
        or observed_at.utcoffset() is None
    ):
        raise ValueError("observed_at must be timezone-aware")
    common = {
        "database_factory": lambda: database,
        "now": observed_at,
        "environ": {} if environ is None else environ,
    }
    readiness = collect_health_report(
        mode=HealthMode.READINESS,
        **common,
    )
    trading_readiness = collect_health_report(
        mode=HealthMode.TRADING_READINESS,
        model_probe=model_probe,
        **common,
    )
    local_date = stockholm_now(observed_at).date()
    completed = database.get_successful_scheduled_routine_keys(local_date)
    missed = missed_routines(observed_at, completed=set(completed))
    rows = database.query(
        """
        SELECT COUNT(*) AS critical_incident_count
        FROM paper_benchmark_incidents incident
        JOIN paper_benchmark_experiments experiment
          ON experiment.id = incident.experiment_id
        WHERE incident.severity = 'CRITICAL'
          AND experiment.status IN ('RUNNING', 'PAUSED')
        """
    )
    critical_count = int(rows[0]["critical_incident_count"])
    session = database.get_market_session("XSTO", local_date)
    market_data_expected = bool(
        session is not None
        and session.is_open(observed_at)
        and observed_at >= session.opens_at + timedelta(minutes=25)
    )
    alerts = evaluate_operational_alerts(
        readiness=readiness,
        trading_readiness=trading_readiness,
        missed_routine_keys=(routine.key for routine in missed),
        critical_incident_count=critical_count,
        market_data_expected=market_data_expected,
    )
    transitions = database.sync_operational_alerts(
        alerts,
        observed_at=observed_at,
    )
    if notifier is not None:
        for transition in transitions:
            if transition["state"] == "OPEN":
                notifier.notify_operational_alert(transition)
    return MonitorRun(
        status="ALERTING" if alerts else "HEALTHY",
        observed_at=observed_at.isoformat(),
        alerts=alerts,
        transitions=transitions,
    )


def _database_failure_run(
    observed_at: datetime,
    notifier,
    *,
    deliver: bool,
) -> MonitorRun:
    alert = OperationalAlert(
        "schema",
        "SCHEMA_OR_MIGRATION_NOT_READY",
        "PAGE",
        "Database or required schema is not ready",
    )
    event = {**asdict(alert), "state": "OPEN"}
    if deliver:
        notifier.notify_operational_alert(event)
    return MonitorRun(
        status="ALERTING",
        observed_at=observed_at.isoformat(),
        alerts=(alert,),
        transitions=(),
    )


def _monitor_failure_run(
    observed_at: datetime,
    notifier,
    *,
    deliver: bool,
) -> MonitorRun:
    alert = OperationalAlert(
        "monitor-evidence",
        "MONITOR_EVIDENCE_UNAVAILABLE",
        "PAGE",
        "Operational monitor could not evaluate or persist evidence",
    )
    event = {**asdict(alert), "state": "OPEN"}
    if deliver:
        notifier.notify_operational_alert(event)
    return MonitorRun(
        status="ALERTING",
        observed_at=observed_at.isoformat(),
        alerts=(alert,),
        transitions=(),
    )


def _seconds_until_next_phase(
    *,
    now_epoch: float,
    interval_seconds: int,
    phase_seconds: int,
) -> float:
    """Return the wait to the next fixed wall-clock phase."""
    elapsed_since_phase = (now_epoch - phase_seconds) % interval_seconds
    return float(interval_seconds - elapsed_since_phase)


def main(
    argv=None,
    *,
    now_factory=lambda: datetime.now(timezone.utc),
    database_factory=Database,
    notifier_factory=TelegramNotifier,
    wall_clock=None,
    sleeper=None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        nargs="?",
        default="once",
        choices=("once", "daemon"),
    )
    args = parser.parse_args(argv)
    try:
        interval = int(os.getenv("OPERATIONAL_MONITOR_INTERVAL_SECONDS", "60"))
    except ValueError:
        interval = 0
    if not 30 <= interval <= 3600:
        parser.error(
            "OPERATIONAL_MONITOR_INTERVAL_SECONDS must be 30..3600"
        )
    try:
        phase = int(os.getenv("OPERATIONAL_MONITOR_PHASE_SECONDS", "30"))
    except ValueError:
        phase = -1
    if not 0 <= phase < interval:
        parser.error(
            "OPERATIONAL_MONITOR_PHASE_SECONDS must be 0..interval-1"
        )

    notifier = notifier_factory()
    wall_clock = wall_clock or time.time
    sleeper = sleeper or time.sleep
    database_failure_notified = False
    monitor_failure_notified = False
    if args.mode == "daemon":
        sleeper(
            _seconds_until_next_phase(
                now_epoch=wall_clock(),
                interval_seconds=interval,
                phase_seconds=phase,
            )
        )
    while True:
        observed_at = now_factory()
        try:
            database = database_factory()
        except Exception:
            result = _database_failure_run(
                observed_at,
                notifier,
                deliver=not database_failure_notified,
            )
            database_failure_notified = True
            monitor_failure_notified = False
        else:
            database_failure_notified = False
            try:
                result = run_monitor_once(
                    database=database,
                    observed_at=observed_at,
                    notifier=notifier,
                    environ=os.environ,
                )
            except Exception:
                result = _monitor_failure_run(
                    observed_at,
                    notifier,
                    deliver=not monitor_failure_notified,
                )
                monitor_failure_notified = True
            else:
                monitor_failure_notified = False
        print(
            json.dumps(
                result.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            flush=True,
        )
        if args.mode == "once":
            return 0 if result.status == "HEALTHY" else 1
        sleeper(
            _seconds_until_next_phase(
                now_epoch=wall_clock(),
                interval_seconds=interval,
                phase_seconds=phase,
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
