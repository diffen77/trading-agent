import logging
import sys

import pytest

from src.data.market_data import MarketDataError
from src.universe_sync import build_universe_sync_service, main


class _Database:
    pass


class _Service:
    def sync(self):
        raise MarketDataError("FIRDS request failed")


def test_universe_sync_is_disabled_by_default():
    with pytest.raises(MarketDataError, match="disabled"):
        build_universe_sync_service(
            database=_Database(),
            environ={},
        )


def test_universe_sync_once_exits_without_traceback_on_safe_error(
    monkeypatch,
    caplog,
):
    import src.universe_sync as universe_sync

    monkeypatch.setattr(universe_sync, "Database", lambda: _Database())
    monkeypatch.setattr(
        universe_sync,
        "build_universe_sync_service",
        lambda **_values: _Service(),
    )
    monkeypatch.setattr(sys, "argv", ["universe-sync", "once"])

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as error:
            main()

    assert error.value.code == 1
    assert "FIRDS request failed" in caplog.text
    assert "Traceback" not in caplog.text
