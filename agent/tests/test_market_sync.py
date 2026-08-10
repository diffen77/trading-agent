from datetime import date, datetime, timezone
import logging
import sys

import pytest

from src.data.market_data import (
    InstrumentRecord,
    MarketDataError,
    MarketSession,
    SyncCycleResult,
)
from src.data.nasdaq_pretrade import NasdaqPublicPreTradeSyncService
from src.data.nasdaq_reference import NasdaqInstrumentAlias
from src.market_sync import (
    build_market_sync_service,
    main,
    run_daemon,
    run_market_sync_cycle,
)


class _Database:
    def __init__(
        self,
        market_session=None,
        instruments=(),
        aliases=(),
        provider_ready=True,
    ):
        self.market_session = market_session
        self.instruments = instruments
        self.aliases = aliases
        self.provider_ready = provider_ready
        self.session_requests = []
        self.provider_requests = []

    def get_market_session(self, mic, session_date):
        self.session_requests.append((mic, session_date))
        return self.market_session

    def get_active_instruments(self, *, mic):
        assert mic == "XSTO"
        return self.instruments

    def get_active_provider_aliases(self, *, provider, mic):
        assert provider == "nasdaq-nordic"
        assert mic == "XSTO"
        return self.aliases

    def require_runtime_market_data_provider(
        self,
        *,
        contract_key,
        provider,
        data_type,
        mic,
        delivery_mode,
        transport,
        usage_scope,
        now,
        expected_instruments,
    ):
        self.provider_requests.append(
            (
                contract_key,
                provider,
                data_type,
                mic,
                delivery_mode,
                transport,
                usage_scope,
                now,
                expected_instruments,
            )
        )
        if not self.provider_ready:
            raise MarketDataError("market-data provider is not validated")


def _instrument():
    return InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name="Volvo B",
        mic="XSTO",
        currency="EUR",
        instrument_type="COMMON_STOCK",
        source="esma-firds",
    )


def _alias():
    return NasdaqInstrumentAlias(
        isin="SE0000115446",
        provider_symbol="VOLV B",
        trading_currency="SEK",
        tradable_id="101",
        source_id="VOLV-B",
        valid_from=None,
        valid_to=None,
    )


class _Service:
    def __init__(self, result=None):
        self.calls = 0
        self.result = result or SyncCycleResult(1, 2, 0, 0)

    def run_once(self):
        self.calls += 1
        return self.result


def test_market_sync_is_disabled_by_default():
    with pytest.raises(MarketDataError, match="disabled"):
        build_market_sync_service(
            database=_Database(),
            environ={},
        )


def test_market_sync_requires_populated_instrument_universe():
    with pytest.raises(MarketDataError, match="instrument"):
        build_market_sync_service(
            database=_Database(instruments=()),
            environ={"ENABLE_NASDAQ_DELAYED_INGESTION": "true"},
        )


def test_market_sync_requires_complete_verified_provider_aliases():
    with pytest.raises(MarketDataError, match="exact instrument coverage"):
        build_market_sync_service(
            database=_Database(
                instruments=(_instrument(),),
                aliases=(),
            ),
            environ={"ENABLE_NASDAQ_DELAYED_INGESTION": "true"},
        )


def test_market_sync_requires_validated_provider_contract():
    now = datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc)
    database = _Database(
        instruments=(_instrument(),),
        aliases=(_alias(),),
        provider_ready=False,
    )

    with pytest.raises(MarketDataError, match="not validated"):
        build_market_sync_service(
            database=database,
            environ={"ENABLE_NASDAQ_DELAYED_INGESTION": "true"},
            clock=lambda: now,
        )

    assert database.provider_requests == [
        (
            "nasdaq-nordic-delayed-xsto-v1",
            "nasdaq-nordic",
            "delayed-post-trade-equity",
            "XSTO",
            "DELAYED_15M",
            "PUBLIC_CSV",
            "INTERNAL_ANALYSIS_AND_PAPER",
            now,
            1,
        )
    ]


def test_market_sync_builds_after_provider_contract_gate_passes():
    now = datetime(2026, 7, 29, 13, 0, tzinfo=timezone.utc)
    database = _Database(
        instruments=(_instrument(),),
        aliases=(_alias(),),
    )

    service = build_market_sync_service(
        database=database,
        environ={"ENABLE_NASDAQ_DELAYED_INGESTION": "true"},
        clock=lambda: now,
    )

    assert service is not None
    assert database.provider_requests == [
        (
            "nasdaq-nordic-delayed-xsto-v1",
            "nasdaq-nordic",
            "delayed-post-trade-equity",
            "XSTO",
            "DELAYED_15M",
            "PUBLIC_CSV",
            "INTERNAL_ANALYSIS_AND_PAPER",
            now,
            1,
        )
    ]


def test_market_sync_builds_public_pretrade_without_paid_aliases(tmp_path):
    service = build_market_sync_service(
        database=_Database(instruments=(_instrument(),), aliases=()),
        environ={
            "ENABLE_NASDAQ_PUBLIC_PRETRADE": "true",
            "NASDAQ_PRETRADE_TEMP_DIRECTORY": str(tmp_path),
        },
        http_session=object(),
    )

    assert isinstance(service, NasdaqPublicPreTradeSyncService)
    assert service._max_files_per_run == 10


def test_market_sync_does_not_fetch_without_open_explicit_session():
    database = _Database(market_session=None)
    service = _Service()
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    result = run_market_sync_cycle(
        database=database,
        service=service,
        now=now,
    )

    assert result == SyncCycleResult(0, 0, 0, 0)
    assert service.calls == 0
    assert database.session_requests == [("XSTO", date(2026, 7, 29))]


def test_market_sync_runs_during_open_explicit_session():
    market_session = MarketSession.for_local_date(
        mic="XSTO",
        session_date=date(2026, 7, 29),
        opens_at="09:00",
        closes_at="17:30",
        timezone_name="Europe/Stockholm",
        source="test-calendar",
    )
    database = _Database(market_session=market_session)
    service = _Service(SyncCycleResult(2, 4, 0, 1))
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    result = run_market_sync_cycle(
        database=database,
        service=service,
        now=now,
    )

    assert result == SyncCycleResult(2, 4, 0, 1)
    assert service.calls == 1


def test_market_sync_daemon_maintains_start_to_start_poll_cadence():
    class StopDaemon(Exception):
        pass

    market_session = MarketSession.for_local_date(
        mic="XSTO",
        session_date=date(2026, 7, 29),
        opens_at="09:00",
        closes_at="17:30",
        timezone_name="Europe/Stockholm",
        source="test-calendar",
    )
    database = _Database(market_session=market_session)
    service = _Service()
    sleeps = []
    monotonic_values = iter((100.0, 112.0))

    def sleeper(seconds):
        sleeps.append(seconds)
        raise StopDaemon

    with pytest.raises(StopDaemon):
        run_daemon(
            database=database,
            service=service,
            interval_seconds=60,
            clock=lambda: datetime(
                2026,
                7,
                29,
                10,
                0,
                tzinfo=timezone.utc,
            ),
            sleeper=sleeper,
            monotonic=lambda: next(monotonic_values),
        )

    assert service.calls == 1
    assert sleeps == [48.0]


def test_once_mode_exits_cleanly_with_safe_market_data_error(
    monkeypatch,
    caplog,
):
    import src.market_sync as market_sync

    monkeypatch.setattr(market_sync, "Database", lambda: _Database())
    monkeypatch.setattr(
        market_sync,
        "build_market_sync_service",
        lambda **_values: _Service(),
    )
    monkeypatch.setattr(
        market_sync,
        "run_market_sync_cycle",
        lambda **_values: (_ for _ in ()).throw(
            MarketDataError("Nasdaq delayed data request failed")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["market-sync", "once"])

    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as error:
            main()

    assert error.value.code == 1
    assert "Nasdaq delayed data request failed" in caplog.text
    assert "Traceback" not in caplog.text
