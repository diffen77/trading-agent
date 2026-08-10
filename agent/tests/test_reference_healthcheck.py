from datetime import date, datetime, timezone

from src.reference_healthcheck import reference_universe_is_fresh


class _Database:
    def __init__(self, snapshot_date):
        self.snapshot_date = snapshot_date

    def get_latest_reference_snapshot_date(self, *, provider, mic):
        assert provider == "esma-firds"
        assert mic == "XSTO"
        return self.snapshot_date


def test_reference_healthcheck_requires_recent_snapshot():
    now = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)

    assert reference_universe_is_fresh(
        _Database(date(2026, 7, 25)),
        now=now,
    )
    assert not reference_universe_is_fresh(
        _Database(date(2026, 7, 18)),
        now=now,
    )
    assert not reference_universe_is_fresh(
        _Database(None),
        now=now,
    )
