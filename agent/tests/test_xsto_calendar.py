from datetime import date, datetime, time, timezone

import pytest

from src.data.market_data import MarketDataError
from src.data.xsto_calendar import nasdaq_stockholm_calendar


def test_official_2026_xsto_calendar_covers_every_weekday():
    snapshot = nasdaq_stockholm_calendar(
        2026,
        verified_at=datetime(
            2026,
            7,
            29,
            14,
            0,
            tzinfo=timezone.utc,
        ),
    )
    sessions = {
        session.session_date: session
        for session in snapshot.sessions
    }

    assert snapshot.mic == "XSTO"
    assert snapshot.year == 2026
    assert date(2026, 1, 1) in snapshot.closed_dates
    assert date(2026, 1, 6) in snapshot.closed_dates
    assert date(2026, 6, 19) in snapshot.closed_dates
    assert date(2026, 7, 29) in sessions
    assert sessions[date(2026, 7, 29)].status == "OPEN"
    assert sessions[date(2026, 7, 29)].opens_at.timetz().replace(
        tzinfo=None,
    ) == time(9, 0)
    assert sessions[date(2026, 7, 29)].closes_at.timetz().replace(
        tzinfo=None,
    ) == time(17, 30)

    half_day = sessions[date(2026, 1, 5)]
    assert half_day.status == "HALF_DAY"
    assert half_day.closes_at.timetz().replace(tzinfo=None) == time(13, 0)
    assert not half_day.is_open(
        datetime(2026, 1, 5, 13, 0, tzinfo=half_day.closes_at.tzinfo)
    )
    assert date(2026, 8, 1) not in sessions
    assert date(2026, 8, 1) not in snapshot.closed_dates

    covered_weekdays = {
        session.session_date for session in snapshot.sessions
    } | set(snapshot.closed_dates)
    assert len(covered_weekdays) == 261


def test_xsto_calendar_refuses_unverified_year():
    with pytest.raises(MarketDataError, match="verified"):
        nasdaq_stockholm_calendar(
            2027,
            verified_at=datetime(
                2026,
                7,
                29,
                14,
                0,
                tzinfo=timezone.utc,
            ),
        )
