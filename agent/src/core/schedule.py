"""Timezone-safe daemon scheduling for the Stockholm market."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import AbstractSet
from zoneinfo import ZoneInfo


STOCKHOLM = ZoneInfo("Europe/Stockholm")
BRAIN_CYCLE_INTERVAL_MINUTES = 15
_GRACE_PERIOD = timedelta(minutes=20)
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
        if timedelta(0) <= delay <= _GRACE_PERIOD:
            if routine.key not in completed:
                result.append(routine)
    return result


def missed_routines(
    now: datetime,
    *,
    completed: AbstractSet[str],
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
            local_now - scheduled_at > _GRACE_PERIOD
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
