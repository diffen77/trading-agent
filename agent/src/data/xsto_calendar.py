"""Versioned Nasdaq Stockholm equity calendar verified against Nasdaq."""

from datetime import date, datetime

from .market_data import (
    MarketCalendarSnapshot,
    MarketDataError,
    MarketSession,
)


NASDAQ_TRADING_HOURS_URL = (
    "https://www.nasdaq.com/european-market-activity/trading-hours"
)
_SOURCE = "nasdaq-trading-hours-verified-2026-07-29"
_CLOSED = {
    2024: {
        date(2024, 1, 1),
        date(2024, 3, 29),
        date(2024, 4, 1),
        date(2024, 5, 1),
        date(2024, 5, 9),
        date(2024, 6, 6),
        date(2024, 6, 21),
        date(2024, 12, 24),
        date(2024, 12, 25),
        date(2024, 12, 26),
        date(2024, 12, 31),
    },
    2025: {
        date(2025, 1, 1),
        date(2025, 1, 6),
        date(2025, 4, 18),
        date(2025, 4, 21),
        date(2025, 5, 1),
        date(2025, 5, 29),
        date(2025, 6, 6),
        date(2025, 6, 20),
        date(2025, 12, 24),
        date(2025, 12, 25),
        date(2025, 12, 26),
        date(2025, 12, 31),
    },
    2026: {
        date(2026, 1, 1),
        date(2026, 1, 6),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 14),
        date(2026, 6, 19),
        date(2026, 12, 24),
        date(2026, 12, 25),
        date(2026, 12, 31),
    },
}
_HALF_DAYS = {
    2024: {
        date(2024, 1, 5),
        date(2024, 3, 28),
        date(2024, 4, 30),
        date(2024, 5, 8),
        date(2024, 11, 1),
    },
    2025: {
        date(2025, 4, 17),
        date(2025, 4, 30),
        date(2025, 5, 28),
        date(2025, 10, 31),
    },
    2026: {
        date(2026, 1, 5),
        date(2026, 4, 2),
        date(2026, 4, 30),
        date(2026, 5, 13),
        date(2026, 10, 30),
    },
}


def nasdaq_stockholm_calendar(
    year: int,
    *,
    verified_at: datetime,
) -> MarketCalendarSnapshot:
    """Return a fail-closed official calendar for a verified year."""
    if year not in _CLOSED or year not in _HALF_DAYS:
        raise MarketDataError(
            f"Nasdaq Stockholm calendar year {year} is not verified"
        )
    closed_dates = _CLOSED[year]
    half_days = _HALF_DAYS[year]
    sessions = []
    current = date(year, 1, 1)
    year_end = date(year + 1, 1, 1)
    while current < year_end:
        if current.weekday() < 5 and current not in closed_dates:
            is_half_day = current in half_days
            sessions.append(
                MarketSession.for_local_date(
                    mic="XSTO",
                    session_date=current,
                    opens_at="09:00",
                    closes_at="13:00" if is_half_day else "17:30",
                    timezone_name="Europe/Stockholm",
                    source=_SOURCE,
                    session_type=(
                        "HALF_DAY" if is_half_day else "REGULAR"
                    ),
                    status="HALF_DAY" if is_half_day else "OPEN",
                )
            )
        current = date.fromordinal(current.toordinal() + 1)
    return MarketCalendarSnapshot(
        mic="XSTO",
        year=year,
        source=_SOURCE,
        source_url=NASDAQ_TRADING_HOURS_URL,
        verified_at=verified_at,
        sessions=tuple(sessions),
        closed_dates=tuple(
            item for item in closed_dates if item.weekday() < 5
        ),
    )
