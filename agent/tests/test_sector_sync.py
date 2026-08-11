import pytest

from src.data.market_data import MarketDataError
from src.sector_sync import build_sector_sync_service, run_daemon


class _Database:
    pass


class _Service:
    def __init__(self):
        self.calls = 0

    def sync(self):
        self.calls += 1
        raise KeyboardInterrupt


def test_sector_sync_is_disabled_by_default():
    with pytest.raises(MarketDataError, match="disabled"):
        build_sector_sync_service(database=_Database(), environ={})


def test_sector_sync_requires_bounded_coverage_threshold():
    with pytest.raises(MarketDataError, match="coverage"):
        build_sector_sync_service(
            database=_Database(),
            environ={
                "ENABLE_NASDAQ_SECTOR_SYNC": "true",
                "NASDAQ_SECTOR_MINIMUM_COVERAGE": "0.20",
            },
        )


def test_sector_daemon_runs_immediately_and_stops_cleanly():
    service = _Service()

    run_daemon(service=service, interval_seconds=86400)

    assert service.calls == 1
