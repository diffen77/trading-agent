#!/usr/bin/env python3
"""Fail-closed, machine-readable operational health checks."""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import argparse
import json
import math
import os
import sys
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from src.data.database import Database
from src.model_config import (
    validate_hermes_model,
    validate_hermes_provider,
    validate_hermes_url,
)
from src.runtime_secrets import RuntimeSecretError, read_runtime_secret


_STOCKHOLM = ZoneInfo("Europe/Stockholm")


class HealthMode(str, Enum):
    READINESS = "readiness"
    TRADING_READINESS = "trading-readiness"


@dataclass(frozen=True)
class HealthCheck:
    code: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HealthReport:
    mode: str
    status: str
    observed_at: str
    checks: tuple[HealthCheck, ...]

    def to_dict(self) -> dict:
        return {
            "event": "healthcheck_completed",
            "mode": self.mode,
            "status": self.status,
            "observed_at": self.observed_at,
            "checks": [asdict(check) for check in self.checks],
        }


def collect_health_report(
    *,
    mode: HealthMode,
    database_factory: Callable[[], object] | None = None,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    model_probe: Callable[[Mapping[str, str]], bool] | None = None,
) -> HealthReport:
    """Collect bounded health evidence without exposing exception details."""
    if not isinstance(mode, HealthMode):
        raise ValueError("mode must be a HealthMode")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    environ = os.environ if environ is None else environ
    database_factory = database_factory or Database
    checks: list[HealthCheck] = []

    try:
        database = database_factory()
    except Exception:
        checks.append(
            HealthCheck(
                code="database_schema",
                ok=False,
                detail="database unavailable or schema below version 45",
            )
        )
        return _report(mode, now, checks)

    checks.append(
        HealthCheck(
            code="database_schema",
            ok=True,
            detail="database reachable with schema version 45 or newer",
        )
    )
    _check_ledger(database, checks)
    _check_strategy(database, checks)
    _check_calendar(database, now, checks)
    _check_continuous_learning(database, now, checks)
    _check_scheduled_job_recovery(database, now, checks)

    if mode is HealthMode.TRADING_READINESS:
        _check_trading_readiness(
            database,
            now=now,
            environ=environ,
            model_probe=model_probe or _probe_model,
            checks=checks,
        )
    return _report(mode, now, checks)


def _check_ledger(database, checks: list[HealthCheck]) -> None:
    try:
        rows = database.query(
            "SELECT COUNT(*) AS balance_count FROM balance"
        )
        count = int(rows[0]["balance_count"])
        mismatch_rows = database.query(
            """
            WITH portfolio_totals AS (
                SELECT ticker, SUM(shares) AS shares
                FROM portfolio
                GROUP BY ticker
            ),
            lot_totals AS (
                SELECT ticker, SUM(remaining_shares) AS shares
                FROM position_lots
                WHERE remaining_shares > 0
                GROUP BY ticker
            )
            SELECT COUNT(*) AS lot_mismatch_count
            FROM portfolio_totals portfolio
            FULL OUTER JOIN lot_totals lots USING (ticker)
            WHERE COALESCE(portfolio.shares, 0)
                != COALESCE(lots.shares, 0)
            """
        )
        mismatch_count = int(
            mismatch_rows[0]["lot_mismatch_count"]
        )
        ok = count == 1 and mismatch_count == 0
    except Exception:
        count = 0
        ok = False
    checks.append(
        HealthCheck(
            code="ledger_balance",
            ok=ok,
            detail=(
                "one balance row and portfolio shares match open lots"
                if ok
                else "ledger balance invariant is not satisfied"
            ),
        )
    )


def _check_strategy(database, checks: list[HealthCheck]) -> None:
    try:
        strategy = database.get_active_strategy()
        version = strategy.version
        ok = bool(version)
    except Exception:
        version = ""
        ok = False
    checks.append(
        HealthCheck(
            code="active_strategy",
            ok=ok,
            detail=(
                f"active strategy {version} is hash-verified"
                if ok
                else "one valid active strategy is required"
            ),
        )
    )


def _check_calendar(
    database,
    now: datetime,
    checks: list[HealthCheck],
) -> None:
    year = now.astimezone(_STOCKHOLM).year
    try:
        rows = database.query(
            """
            SELECT COUNT(*) AS calendar_count
            FROM market_calendar_snapshots
            WHERE mic = 'XSTO' AND year = :year AND is_current
            """,
            {"year": year},
        )
        ok = int(rows[0]["calendar_count"]) == 1
    except Exception:
        ok = False
    checks.append(
        HealthCheck(
            code="market_calendar",
            ok=ok,
            detail=(
                f"current official XSTO calendar exists for {year}"
                if ok
                else f"current official XSTO calendar is missing for {year}"
            ),
        )
    )


def _check_continuous_learning(
    database,
    now: datetime,
    checks: list[HealthCheck],
) -> None:
    try:
        status = database.get_continuous_learning_runtime_status(
            now=now,
        )
        run = status.get("run") or {}
        age_seconds = int(run.get("age_seconds") or 0)
        ok = (
            run.get("status") == "SUCCEEDED"
            and 0 <= age_seconds <= 900
        )
    except Exception:
        ok = False
    checks.append(
        HealthCheck(
            code="continuous_learning_worker",
            ok=ok,
            detail=(
                "continuous learning worker completed within 15 minutes"
                if ok
                else "continuous learning worker is stale or failed"
            ),
        )
    )


def _check_scheduled_job_recovery(
    database,
    now: datetime,
    checks: list[HealthCheck],
) -> None:
    try:
        status = database.get_scheduled_job_runtime_status(now=now)
        expired_claims = int(status.get("expired_claim_count") or 0)
        session_open = status.get("session_open") is True
        age_key = (
            "latest_brain_age_seconds"
            if session_open
            else "latest_study_age_seconds"
        )
        age = status.get(age_key)
        age_seconds = int(age) if age is not None else None
        maximum_age = 2100 if session_open else 7200
        ok = (
            expired_claims == 0
            and age_seconds is not None
            and 0 <= age_seconds <= maximum_age
        )
    except Exception:
        ok = False
        session_open = False
    checks.append(
        HealthCheck(
            code="scheduled_job_recovery",
            ok=ok,
            detail=(
                "intraday brain scheduler completed a durable slot within 35 minutes and has no expired leases"
                if ok and session_open
                else (
                    "off-hours study scheduler completed a durable slot within two hours and has no expired leases"
                    if ok
                    else "recurring scheduler is stale or has an expired restart lease"
                )
            ),
        )
    )


def _check_trading_readiness(
    database,
    *,
    now: datetime,
    environ: Mapping[str, str],
    model_probe: Callable[[Mapping[str, str]], bool],
    checks: list[HealthCheck],
) -> None:
    _check_entry_risk_control(database, now=now, checks=checks)

    try:
        minimum = _bounded_integer(
            environ.get("XSTO_MINIMUM_INSTRUMENTS", "300"),
            "XSTO_MINIMUM_INSTRUMENTS",
            minimum=1,
            maximum=1000,
        )
        universe_config_ok = True
    except ValueError:
        minimum = 1001
        universe_config_ok = False
    try:
        instruments = database.get_active_instruments(mic="XSTO")
    except Exception:
        instruments = ()
    instrument_count = len(instruments)
    universe_ok = universe_config_ok and instrument_count >= minimum
    checks.append(
        HealthCheck(
            code="xsto_universe",
            ok=universe_ok,
            detail=(
                f"{instrument_count} active XSTO equity instruments"
                if universe_ok
                else "active XSTO universe or its configuration is incomplete"
            ),
        )
    )

    market_data_type = None
    market_mode = None
    try:
        market_status = database.require_operational_market_data(now)
        market_data_type = market_status.get("data_type")
        expected_count_ok = int(
            market_status.get("expected_instrument_count", -1)
        ) == instrument_count
        if market_data_type == "delayed-pre-trade-equity":
            evidence_ok = int(
                market_status.get("eligible_instrument_count", 0)
            ) > 0
        else:
            evidence_ok = int(
                market_status.get("fresh_quote_count", -1)
            ) == instrument_count
        provider_ok = (
            universe_ok
            and bool(market_status.get("provider"))
            and expected_count_ok
            and evidence_ok
        )
    except Exception:
        provider_ok = False

    if not provider_ok:
        mode_reader = getattr(
            database,
            "get_authorized_market_data_mode",
            None,
        )
        if callable(mode_reader):
            try:
                market_mode = mode_reader(now)
            except Exception:
                market_mode = None

    public_paper_mode = (
        market_data_type == "delayed-pre-trade-equity"
        and provider_ok
    ) or (
        market_mode is not None
        and market_mode.get("data_type") == "delayed-pre-trade-equity"
        and market_mode.get("usage_scope")
        == "INTERNAL_ANALYSIS_AND_PAPER"
    )

    checks.append(
        HealthCheck(
            code="operational_market_data",
            ok=provider_ok,
            detail=(
                "authorized delayed order books are executable for the open XSTO session"
                if provider_ok and market_data_type == "delayed-pre-trade-equity"
                else (
                    "authorized equity feed covers the full open XSTO session"
                    if provider_ok
                    else (
                        "authorized public paper feed is configured but not operational for the current XSTO session"
                        if public_paper_mode
                        else "authorized operational equity data is not ready"
                    )
                )
            ),
        )
    )

    if public_paper_mode:
        index_ok = True
        index_detail = (
            "separate OMXSGI data is not required for internal public-feed "
            "paper trading"
        )
    else:
        try:
            index_signal = database.get_current_authorized_index_change(now)
            index_change = float(index_signal.get("change_pct"))
            index_ok = (
                index_signal.get("symbol") == "OMXSGI"
                and bool(index_signal.get("provider"))
                and math.isfinite(index_change)
            )
        except Exception:
            index_ok = False
        index_detail = (
            "authorized current OMXSGI signal is available"
            if index_ok
            else "authorized current OMXSGI signal is not ready"
        )
    checks.append(
        HealthCheck(
            code="market_index_signal",
            ok=index_ok,
            detail=index_detail,
        )
    )

    _check_ai_model(
        environ=environ,
        model_probe=model_probe,
        checks=checks,
    )


def _check_entry_risk_control(
    database,
    *,
    now: datetime,
    checks: list[HealthCheck],
) -> None:
    try:
        control = database.get_trading_control()
        rows = database.query(
            """
            SELECT limit_breached
            FROM trading_daily_risk
            WHERE session_date = :session_date
            """,
            {
                "session_date": now.astimezone(_STOCKHOLM).date(),
            },
        )
        daily_breached = bool(rows and rows[0]["limit_breached"])
        ok = control.status == "ACTIVE" and not daily_breached
        detail = (
            f"entries active with {control.max_daily_loss_pct:g}% "
            "daily loss limit"
            if ok
            else "operator halt or daily loss latch blocks new entries"
        )
    except Exception:
        ok = False
        detail = "entry risk control is unavailable"
    checks.append(
        HealthCheck(
            code="entry_risk_control",
            ok=ok,
            detail=detail,
        )
    )


def _check_ai_model(
    *,
    environ: Mapping[str, str],
    model_probe: Callable[[Mapping[str, str]], bool],
    checks: list[HealthCheck],
) -> None:
    try:
        ok = bool(model_probe(environ))
    except Exception:
        ok = False
    checks.append(
        HealthCheck(
            code="ai_model",
            ok=ok,
            detail=(
                "configured AI model endpoint is reachable"
                if ok
                else "configured AI model endpoint is not reachable"
            ),
        )
    )


def _probe_model(environ: Mapping[str, str]) -> bool:
    backend = environ.get("LLM_BACKEND", "openai-compatible")
    if backend == "openai-compatible":
        base_url = environ.get(
            "OLLAMA_URL",
            "http://host.docker.internal:11434",
        ).rstrip("/")
        parsed = urlparse(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        response = requests.get(f"{base_url}/v1/models", timeout=5)
        if response.status_code != 200:
            return False
        payload = response.json()
        models = payload.get("data")
        if not isinstance(models, list) or not models:
            return False
        expected_model = environ.get(
            "LLM_MODEL",
            "qwen2.5-coder:14b",
        )
        return any(
            isinstance(model, dict) and model.get("id") == expected_model
            for model in models
        )
    if backend == "anthropic":
        try:
            api_key = read_runtime_secret(
                "ANTHROPIC_API_KEY",
                environ=environ,
                required=True,
            )
        except RuntimeSecretError:
            return False
        expected_model = environ.get("LLM_MODEL")
        if not api_key or not expected_model:
            return False
        response = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=5,
        )
        if response.status_code != 200:
            return False
        payload = response.json()
        models = payload.get("data")
        if not isinstance(models, list):
            return False
        return any(
            isinstance(model, dict) and model.get("id") == expected_model
            for model in models
        )
    if backend == "hermes":
        try:
            base_url = validate_hermes_url(
                environ.get("HERMES_URL", "")
            )
            expected_model = validate_hermes_model(
                environ.get("LLM_MODEL", "")
            )
            expected_provider = validate_hermes_provider(
                environ.get("HERMES_PROVIDER", "openai-codex")
            )
            api_key = read_runtime_secret(
                "HERMES_API_KEY",
                environ=environ,
                required=True,
            )
        except (RuntimeSecretError, ValueError):
            return False
        response = requests.get(
            f"{base_url}/api/model/options",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        if response.status_code != 200:
            return False
        payload = response.json()
        providers = payload.get("providers")
        if not isinstance(providers, list):
            return False
        for provider in providers:
            if (
                not isinstance(provider, dict)
                or (
                    provider.get("id")
                    or provider.get("slug")
                    or provider.get("provider")
                )
                != expected_provider
                or provider.get("authenticated") is not True
            ):
                continue
            models = provider.get("models")
            if not isinstance(models, list):
                return False
            return any(
                (
                    isinstance(model, dict)
                    and model.get("id") == expected_model
                )
                or model == expected_model
                for model in models
            )
        return False
    return False


def _bounded_integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return parsed


def _report(
    mode: HealthMode,
    now: datetime,
    checks: list[HealthCheck],
) -> HealthReport:
    status = "READY" if checks and all(check.ok for check in checks) else "NOT_READY"
    return HealthReport(
        mode=mode.value,
        status=status,
        observed_at=now.isoformat(),
        checks=tuple(checks),
    )


def main(
    argv: list[str] | None = None,
    *,
    now_factory: Callable[[], datetime] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[mode.value for mode in HealthMode],
        nargs="?",
        default=HealthMode.READINESS.value,
    )
    args = parser.parse_args(argv)
    report = collect_health_report(
        mode=HealthMode(args.mode),
        now=(now_factory or (lambda: datetime.now(timezone.utc)))(),
        environ=environ,
    )
    print(json.dumps(report.to_dict(), separators=(",", ":"), sort_keys=True))
    return 0 if report.status == "READY" else 1


if __name__ == "__main__":
    sys.exit(main())
