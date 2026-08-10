from datetime import datetime, timezone
import logging

import pytest

from src.calendar_sync import sync_verified_calendars
from src.data.market_data import CalendarSyncResult, MarketDataError


class _Database:
    def __init__(self):
        self.snapshots = []

    def sync_market_calendar(self, snapshot):
        self.snapshots.append(snapshot)
        return CalendarSyncResult(
            inserted=True,
            sessions_upserted=len(snapshot.sessions),
            sync_run_id=len(self.snapshots),
        )


def test_calendar_sync_loads_every_verified_year():
    database = _Database()
    results = sync_verified_calendars(
        database=database,
        verified_at=datetime(
            2026,
            7,
            29,
            14,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert [snapshot.year for snapshot in database.snapshots] == [
        2024,
        2025,
        2026,
    ]
    assert len(results) == 3
    assert all(result.inserted for result in results)


def test_calendar_sync_rejects_naive_verification_time(caplog):
    with caplog.at_level(logging.ERROR):
        with pytest.raises(MarketDataError, match="timezone"):
            sync_verified_calendars(
                database=_Database(),
                verified_at=datetime(2026, 7, 29, 14, 0),
            )
