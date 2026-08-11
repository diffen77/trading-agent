"""Timezone-safe daemon scheduling for the Stockholm market."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import AbstractSet
from zoneinfo import ZoneInfo


STOCKHOLM = ZoneInfo("Europe/Stockholm")
BRAIN_CYCLE_INTERVAL_MINUTES = 15
_GRACE_PERIOD = timedelta(minutes=20)
_PROVIDER_PROCESSING_ALLOWANCE = timedelta(minutes=1)
_RECOVERY_WINDOWS = {
    "morning": timedelta(hours=2),
    "close": timedelta(hours=6),
    "evening": timedelta(hours=6),
}
_ROUTINE_TIMES = (
    ("morning", time(7, 0)),
    ("open", time(9, 0)),
    ("midday", time(12, 0)),
    ("close", time(17, 40)),
    ("evening", time(22, 0)),
)


@dataclass(frozen=True)
class DueRoutine:
    name: str
    scheduled_at: datetime
    recovery: bool = False

    @property
    def key(self) -> str:
        return f"{self.scheduled_at.date().isoformat()}:{self.name}"


def stockholm_now(now: datetime) -> datetime:
    """Convert an aware instant to Europe/Stockholm."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(STOCKHOLM)


def brain_cycle_slot(now: datetime) -> int:
    """Return the idempotency slot for a 15-minute intraday AI cycle."""
    stockholm_now(now)
    interval_seconds = BRAIN_CYCLE_INTERVAL_MINUTES * 60
    return int(now.timestamp() // interval_seconds)


def provider_routine_grace_periods(
    market_data_mode: Mapping[str, object],
) -> dict[str, timedelta]:
    """Derive bounded routine grace from the authorized provider contract."""
    if not isinstance(market_data_mode, Mapping):
        raise ValueError("market_data_mode must be a mapping")
    if market_data_mode.get("data_type") != "delayed-pre-trade-equity":
        return {}

    timing = []
    for key in ("nominal_delay_seconds", "max_transport_lag_seconds"):
        value = market_data_mode.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be a non-negative int")
        if not 0 <= value <= 24 * 60 * 60:
            raise ValueError(f"{key} must be a non-negative int")
        timing.append(value)

    provider_window = (
        timedelta(seconds=sum(timing)) + _PROVIDER_PROCESSING_ALLOWANCE
    )
    return {"open": max(_GRACE_PERIOD, provider_window)}


def provider_routine_start_delays(
    market_data_mode: Mapping[str, object],
) -> dict[str, timedelta]:
    """Delay open work until the provider can expose its first book."""
    if not isinstance(market_data_mode, Mapping):
        raise ValueError("market_data_mode must be a mapping")
    if market_data_mode.get("data_type") != "delayed-pre-trade-equity":
        return {}
    nominal_delay = market_data_mode.get("nominal_delay_seconds")
    if (
        isinstance(nominal_delay, bool)
        or not isinstance(nominal_delay, int)
        or not 0 <= nominal_delay <= 24 * 60 * 60
    ):
        raise ValueError(
            "nominal_delay_seconds must be a non-negative int"
        )
    return {
        "open": timedelta(seconds=nominal_delay)
        + _PROVIDER_PROCESSING_ALLOWANCE
    }


def _routine_grace_period(
    name: str,
    grace_periods: Mapping[str, timedelta] | None,
) -> timedelta:
    if grace_periods is None or name not in grace_periods:
        return _GRACE_PERIOD
    value = grace_periods[name]
    if not isinstance(value, timedelta) or not _GRACE_PERIOD <= value <= timedelta(
        days=1
    ):
        raise ValueError("routine grace period must be between 20 minutes and 1 day")
    return value


def _routine_start_delay(
    name: str,
    start_delays: Mapping[str, timedelta] | None,
) -> timedelta:
    if start_delays is None or name not in start_delays:
        return timedelta(0)
    value = start_delays[name]
    if (
        not isinstance(value, timedelta)
        or not timedelta(0) <= value <= timedelta(days=1)
    ):
        raise ValueError("routine start delay must be between 0 and 1 day")
    return value


def recovery_slots(
    now: datetime,
    *,
    interval_minutes: int,
    last_completed_at: datetime | None,
    max_backfill_slots: int,
) -> tuple[datetime, ...]:
    """Return bounded UTC slots after the latest durable completion."""
    stockholm_now(now)
    if not isinstance(interval_minutes, int) or not 1 <= interval_minutes <= 1440:
        raise ValueError("interval_minutes must be between 1 and 1440")
    if not isinstance(max_backfill_slots, int) or not 1 <= max_backfill_slots <= 96:
        raise ValueError("max_backfill_slots must be between 1 and 96")
    if last_completed_at is not None:
        stockholm_now(last_completed_at)

    interval_seconds = interval_minutes * 60

    def slot_at(value: datetime) -> datetime:
        slot = int(value.timestamp() // interval_seconds)
        return datetime.fromtimestamp(slot * interval_seconds, tz=timezone.utc)

    current = slot_at(now)
    if last_completed_at is None:
        return (current,)

    previous = slot_at(last_completed_at)
    if previous >= current:
        return ()

    slots = []
    candidate = previous + timedelta(minutes=interval_minutes)
    while candidate <= current:
        slots.append(candidate)
        candidate += timedelta(minutes=interval_minutes)
    return tuple(slots[-max_backfill_slots:])


def due_routines(
    now: datetime,
    *,
    completed: AbstractSet[str],
    grace_periods: Mapping[str, timedelta] | None = None,
    start_delays: Mapping[str, timedelta] | None = None,
) -> list[DueRoutine]:
    """Return routines due within a bounded local-time grace window."""
    local_now = stockholm_now(now)
    result = []
    for name, wall_clock in _ROUTINE_TIMES:
        scheduled_at = datetime.combine(
            local_now.date(),
            wall_clock,
            STOCKHOLM,
        )
        delay = local_now - scheduled_at
        routine = DueRoutine(name=name, scheduled_at=scheduled_at)
        starts_at = _routine_start_delay(name, start_delays)
        expires_at = _routine_grace_period(name, grace_periods)
        if starts_at <= delay <= expires_at:
            if routine.key not in completed:
                result.append(routine)
    return result


def missed_routines(
    now: datetime,
    *,
    completed: AbstractSet[str],
    grace_periods: Mapping[str, timedelta] | None = None,
) -> list[DueRoutine]:
    """Return today's routines whose completion grace period expired."""
    local_now = stockholm_now(now)
    result = []
    for name, wall_clock in _ROUTINE_TIMES:
        scheduled_at = datetime.combine(
            local_now.date(),
            wall_clock,
            STOCKHOLM,
        )
        routine = DueRoutine(name=name, scheduled_at=scheduled_at)
        if (
            local_now - scheduled_at
            > _routine_grace_period(name, grace_periods)
            and routine.key not in completed
        ):
            result.append(routine)
    return result


def recoverable_routines(
    now: datetime,
    *,
    completed: AbstractSet[str],
) -> list[DueRoutine]:
    """Return bounded, non-trading routines safe to replay after restart."""
    local_now = stockholm_now(now)
    result = []
    for days_ago in (1, 0):
        session_date = local_now.date() - timedelta(days=days_ago)
        for name, wall_clock in _ROUTINE_TIMES:
            recovery_window = _RECOVERY_WINDOWS.get(name)
            if recovery_window is None:
                continue
            scheduled_at = datetime.combine(
                session_date,
                wall_clock,
                STOCKHOLM,
            )
            delay = local_now - scheduled_at
            routine = DueRoutine(
                name=name,
                scheduled_at=scheduled_at,
                recovery=True,
            )
            if (
                _GRACE_PERIOD < delay <= recovery_window
                and routine.key not in completed
            ):
                result.append(routine)
    return sorted(result, key=lambda routine: routine.scheduled_at)
