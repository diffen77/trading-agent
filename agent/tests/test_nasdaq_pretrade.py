from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib

import pytest

from src.data.market_data import (
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketDataError,
)
from src.data.nasdaq_pretrade import (
    NasdaqPublicPreTradeProvider,
    NasdaqPublicPreTradeSyncService,
    parse_pre_trade_csv_lines,
)
from src.data.nasdaq_reference import NasdaqInstrumentAlias
from src.data.top_of_book import apply_top_of_book_batch


HEADER = (
    "Update date and time;Instrument identification code;Side;"
    "Market Marker;Price;Price currency;Price notation;Quantity;"
    "Quantity currency;Aggregated number of orders and quotes;Venue;"
    "Trading system;Trading system phase;Publication Time"
)
FILE_NAME = "NordicEquity-pretrade-2026-07-29T1000.csv"
SOURCE_URL = (
    "https://tradereports.nasdaq.com/api/regulatory/"
    "trade-report/download?type=PRE_TRADE"
)
RECEIVED_AT = datetime(
    2026,
    7,
    29,
    8,
    15,
    5,
    tzinfo=timezone.utc,
)


class _Response:
    def __init__(self, content, *, headers=None):
        self.content = content
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _SyncDatabase:
    def __init__(self, *, latest_snapshot=None):
        self.mapping_calls = []
        self.contract_calls = []
        self.authorization_calls = []
        self.reset_calls = []
        self.persisted = []
        self.latest_snapshot = latest_snapshot

    def get_latest_reference_universe(self, *, provider, mic):
        assert provider == "esma-firds"
        assert mic == "XSTO"
        return 42, _reference_snapshot()

    def get_market_session(self, mic, session_date):
        assert mic == "XSTO"

        class _MarketSession:
            @staticmethod
            def is_open(instant):
                return instant.hour == 8

        return _MarketSession()

    def ensure_public_pretrade_contract(self, **kwargs):
        self.contract_calls.append(kwargs)
        return "nasdaq-public-pretrade-xsto-v1"

    def ensure_public_pretrade_authorization(self, **kwargs):
        self.authorization_calls.append(kwargs)
        return "nasdaq-public-pretrade-xsto-v1"

    def ensure_isin_company_mappings(self, **kwargs):
        self.mapping_calls.append(kwargs)
        return 1

    def load_latest_top_of_book_snapshot(self, *, contract_key):
        assert contract_key == "nasdaq-public-pretrade-xsto-v1"
        return self.latest_snapshot

    def authorize_top_of_book_stream_reset(self, **kwargs):
        self.reset_calls.append(kwargs)
        return 17

    def persist_top_of_book_batch(self, **kwargs):
        self.persisted.append(kwargs)
        return object()


def _instrument() -> InstrumentRecord:
    return InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name="Volvo B",
        mic="XSTO",
        currency="EUR",
        instrument_type="COMMON_STOCK",
        source="esma-firds",
    )


def _alias() -> NasdaqInstrumentAlias:
    return NasdaqInstrumentAlias(
        isin="SE0000115446",
        provider_symbol="VOLV B",
        trading_currency="SEK",
        tradable_id="101",
        source_id="VOLV-B",
        valid_from=None,
        valid_to=None,
    )


def _reference_snapshot(
    instruments=None,
) -> InstrumentUniverseSnapshot:
    return InstrumentUniverseSnapshot(
        mic="XSTO",
        instruments=tuple(instruments or [_instrument()]),
    )


def _row(
    *,
    side: str,
    price: str,
    quantity: str,
    orders: str,
    timestamp: str = "2026-07-29T08:00:00.013000Z",
    publication_time: str | None = None,
    isin: str = "SE0000115446",
    venue: str = "XSTO",
    currency: str = "SEK",
    price_notation: str = "1",
    market_maker: str = "",
    trading_system: str = "CLOB",
) -> str:
    publication_time = publication_time or timestamp
    return (
        f"{timestamp};{isin};{side};{market_maker};{price};{currency};"
        f"{price_notation};{quantity};{currency};{orders};{venue};"
        f"{trading_system};COTR;{publication_time}"
    )


def _content(*rows: str) -> bytes:
    return (
        "\r\n".join(['"sep=;"', HEADER, *rows]) + "\r\n"
    ).encode("utf-8")


def _parse(
    *rows: str,
    instruments=None,
    file_name=FILE_NAME,
    aliases=None,
):
    content = _content(*rows)
    return parse_pre_trade_csv_lines(
        iter(content.splitlines(keepends=True)),
        file_name=file_name,
        source_url=SOURCE_URL,
        source_checksum_sha256=hashlib.sha256(content).hexdigest(),
        reference_snapshot=_reference_snapshot(instruments),
        aliases=(_alias(),) if aliases is None else aliases,
        received_at=RECEIVED_AT,
    )


def test_parser_binds_ordered_updates_to_verified_file_minute():
    rows = (
        _row(side="BUY", price="321.10", quantity="1000", orders="4.0"),
        _row(
            side="SELL",
            price="321.30",
            quantity="800",
            orders="3.0",
            timestamp="2026-07-29T08:00:00.014000Z",
        ),
        _row(
            side="BUY",
            price="999",
            quantity="1",
            orders="1",
            venue="XCSE",
        ),
    )
    batch = _parse(*rows)
    updates = batch.updates

    assert [update.side for update in updates] == ["BUY", "SELL"]
    assert batch.file_name == FILE_NAME
    assert batch.source_url == SOURCE_URL
    assert batch.source_checksum_sha256 == hashlib.sha256(
        _content(*rows)
    ).hexdigest()
    assert (
        batch.reference_checksum_sha256
        == _reference_snapshot().checksum_sha256
    )
    assert batch.instrument_isins == ("SE0000115446",)
    assert batch.report_minute == datetime(
        2026,
        7,
        29,
        8,
        0,
        tzinfo=timezone.utc,
    )
    assert batch.covered_through == batch.report_minute + timedelta(minutes=1)
    assert batch.received_at == RECEIVED_AT
    assert batch.input_row_count == 3
    assert updates[0].isin == "SE0000115446"
    assert updates[0].mic == "XSTO"
    assert updates[0].currency == "SEK"
    assert _instrument().notional_currency == "EUR"
    assert updates[0].price == Decimal("321.10")
    assert updates[0].quantity == Decimal("1000")
    assert updates[0].order_count == 4
    assert updates[0].source_sequence == 1
    assert updates[1].source_sequence == 2
    assert updates[0].trading_system == "CLOB"
    assert updates[0].trading_phase == "COTR"
    assert updates[0].event_time == datetime(
        2026,
        7,
        29,
        8,
        0,
        0,
        13_000,
        tzinfo=timezone.utc,
    )
    assert updates[0].publication_time == updates[0].event_time
    assert updates[0].observed_delay.total_seconds() == pytest.approx(
        905 - 0.013
    )


def test_parser_uses_feed_currency_when_provider_aliases_are_unavailable():
    batch = _parse(
        _row(
            side="BUY",
            price="321.10",
            quantity="1000",
            orders="4.0",
        ),
        aliases=(),
    )

    assert batch.updates[0].currency == "SEK"


def test_parser_ignores_non_clob_xsto_rows_but_keeps_executable_book():
    batch = _parse(
        _row(
            side="SELL",
            price="11.98",
            quantity="4991",
            orders="2.0",
            trading_system="",
        ),
        _row(
            side="SELL",
            price="321.40",
            quantity="500",
            orders="2.0",
            trading_system="PATS",
        ),
        _row(
            side="BUY",
            price="321.10",
            quantity="1000",
            orders="4.0",
        ),
        _row(
            side="SELL",
            price="321.30",
            quantity="900",
            orders="3.0",
            timestamp="2026-07-29T08:00:01.000000Z",
        ),
    )

    assert len(batch.updates) == 2
    assert {update.trading_system for update in batch.updates} == {"CLOB"}
    assert [update.source_sequence for update in batch.updates] == [3, 4]


def test_parser_ignores_xsto_rows_outside_reference_universe():
    batch = _parse(
        _row(
            side="BUY",
            price="145.20",
            quantity="250",
            orders="3",
            isin="SE0003051010",
        ),
        _row(
            side="BUY",
            price="321.10",
            quantity="1000",
            orders="4",
        ),
        _row(
            side="SELL",
            price="321.30",
            quantity="900",
            orders="3",
            timestamp="2026-07-29T08:00:01.000000Z",
        ),
    )

    assert batch.input_row_count == 3
    assert len(batch.updates) == 2
    assert {update.isin for update in batch.updates} == {"SE0000115446"}
    assert [update.source_sequence for update in batch.updates] == [2, 3]


def test_parser_represents_an_empty_side_as_an_explicit_delete():
    batch = _parse(
        _row(side="SELL", price="", quantity="", orders=""),
    )
    updates = batch.updates

    assert len(updates) == 1
    assert updates[0].side == "SELL"
    assert updates[0].price is None
    assert updates[0].quantity is None
    assert updates[0].order_count is None
    assert updates[0].is_delete is True


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="",
                orders="4",
            ),
            "all present or all empty",
        ),
        (
            _row(
                side="UNKNOWN",
                price="321.10",
                quantity="100",
                orders="4",
            ),
            "side",
        ),
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="100",
                orders="4",
                currency="EUR",
            ),
            "currency",
        ),
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="100",
                orders="4",
                price_notation="MONE",
            ),
            "notation",
        ),
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="100",
                orders="4",
                publication_time="2026-07-29T07:59:59.000000Z",
            ),
            "publication",
        ),
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="0",
                orders="4",
            ),
            "quantity",
        ),
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="100",
                orders="0",
            ),
            "order_count",
        ),
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="100",
                orders="4",
                market_maker="UNVERIFIED",
            ),
            "Market Marker",
        ),
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="100",
                orders="4",
                timestamp="2026-07-29T07:59:59.999999Z",
            ),
            "file minute",
        ),
        (
            _row(
                side="BUY",
                price="321.10",
                quantity="100",
                orders="4",
                timestamp="2026-07-29T08:01:00.000000Z",
            ),
            "file minute",
        ),
    ],
)
def test_parser_rejects_unsafe_xsto_rows(row, message):
    with pytest.raises(MarketDataError, match=message):
        _parse(row)


def test_parser_rejects_schema_drift():
    with pytest.raises(MarketDataError, match="schema"):
        invalid_content = b"wrong;header\r\nvalue;value\r\n"
        parse_pre_trade_csv_lines(
            iter(invalid_content.splitlines(keepends=True)),
            file_name=FILE_NAME,
            source_url=SOURCE_URL,
            source_checksum_sha256=hashlib.sha256(
                invalid_content
            ).hexdigest(),
            reference_snapshot=_reference_snapshot(),
            aliases=(_alias(),),
            received_at=RECEIVED_AT,
        )


def test_parser_rejects_duplicate_reference_isin_and_invalid_file_evidence():
    duplicate = InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV X",
        name="Conflicting Volvo",
        mic="XSTO",
        currency="EUR",
        instrument_type="COMMON_STOCK",
        source="conflicting-reference",
    )
    row = _row(
        side="BUY",
        price="321.10",
        quantity="100",
        orders="4",
    )
    content = (HEADER + "\r\n" + row + "\r\n").encode("utf-8")
    checksum = hashlib.sha256(content).hexdigest()

    with pytest.raises(MarketDataError, match="duplicate XSTO instrument"):
        _parse(row, instruments=[_instrument(), duplicate])

    for field, value, message in (
        ("file_name", "../pretrade.csv", "file_name"),
        ("source_url", "http://example.test/file.csv", "source_url"),
        ("source_checksum_sha256", "not-a-checksum", "checksum"),
    ):
        values = {
            "file_name": FILE_NAME,
            "source_url": SOURCE_URL,
            "source_checksum_sha256": checksum,
            "reference_snapshot": _reference_snapshot(),
            "aliases": (_alias(),),
        }
        values[field] = value
        with pytest.raises(MarketDataError, match=message):
            parse_pre_trade_csv_lines(
                iter(content.splitlines(keepends=True)),
                received_at=RECEIVED_AT,
                **values,
            )

    with pytest.raises(MarketDataError, match="checksum mismatch"):
        parse_pre_trade_csv_lines(
            iter(content.splitlines(keepends=True)),
            file_name=FILE_NAME,
            source_url=SOURCE_URL,
            source_checksum_sha256="b" * 64,
            reference_snapshot=_reference_snapshot(),
            aliases=(_alias(),),
            received_at=RECEIVED_AT,
        )


def test_parser_rejects_blank_physical_rows_and_dst_ambiguous_file_minutes():
    row = _row(
        side="BUY",
        price="321.10",
        quantity="100",
        orders="4",
    )
    blank_content = (
        ('"sep=;"\r\n' + HEADER + "\r\n\r\n" + row + "\r\n")
        .encode("utf-8")
    )
    with pytest.raises(MarketDataError, match="blank physical line"):
        parse_pre_trade_csv_lines(
            iter(blank_content.splitlines(keepends=True)),
            file_name=FILE_NAME,
            source_url=SOURCE_URL,
            source_checksum_sha256=hashlib.sha256(
                blank_content
            ).hexdigest(),
            reference_snapshot=_reference_snapshot(),
            aliases=(_alias(),),
            received_at=RECEIVED_AT,
        )

    content = _content(row)
    for file_name in (
        "NordicEquity-pretrade-2026-03-29T0230.csv",
        "NordicEquity-pretrade-2026-10-25T0230.csv",
    ):
        with pytest.raises(
            MarketDataError,
            match="ambiguous or nonexistent",
        ):
            parse_pre_trade_csv_lines(
                iter(content.splitlines(keepends=True)),
                file_name=file_name,
                source_url=SOURCE_URL,
                source_checksum_sha256=hashlib.sha256(content).hexdigest(),
                reference_snapshot=_reference_snapshot(),
                aliases=(_alias(),),
                received_at=RECEIVED_AT,
            )


def test_parser_rejects_multiline_chunks_that_hide_physical_rows():
    content = _content(
        _row(
            side="BUY",
            price="321.10",
            quantity="100",
            orders="4",
        )
    )

    with pytest.raises(MarketDataError, match="multiple physical lines"):
        parse_pre_trade_csv_lines(
            iter((content,)),
            file_name=FILE_NAME,
            source_url=SOURCE_URL,
            source_checksum_sha256=hashlib.sha256(content).hexdigest(),
            reference_snapshot=_reference_snapshot(),
            aliases=(_alias(),),
            received_at=RECEIVED_AT,
        )


def test_reference_snapshot_canonically_binds_eligible_membership():
    snapshot = _reference_snapshot()
    assert snapshot.instrument_isins == ("SE0000115446",)
    assert snapshot.checksum_sha256 == _reference_snapshot().checksum_sha256
    changed_currency = _reference_snapshot(
        [replace(_instrument(), currency="SEK")]
    )
    assert changed_currency.checksum_sha256 != snapshot.checksum_sha256

    for invalid, message in (
        (replace(_instrument(), status="INACTIVE"), "active"),
        (replace(_instrument(), primary_listing=False), "primary"),
        (replace(_instrument(), instrument_type="ETF"), "equity type"),
    ):
        with pytest.raises(MarketDataError, match=message):
            _reference_snapshot([invalid])


def test_public_provider_lists_and_streams_strict_ephemeral_csv(tmp_path):
    payload = _content(
        _row(
            side="BUY",
            price="321.10",
            quantity="1000",
            orders="4.0",
        )
    )
    session = _Session((
        _Response(
            b'{"message":null,"reports":['
            b'"NordicEquity-pretrade-2026-07-29T1000"]}',
            headers={"Content-Type": "application/json"},
        ),
        _Response(
            payload,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": (
                    'attachment; filename="NordicEquity-pretrade-'
                    '2026-07-29T1000.csv"'
                ),
            },
        ),
    ))
    provider = NasdaqPublicPreTradeProvider(
        session=session,
        clock=lambda: RECEIVED_AT,
        temp_directory=tmp_path,
    )

    reports = provider.report_files()
    assert [item.file_name for item in reports] == [FILE_NAME]

    with provider.open_report_file(reports[0]) as downloaded:
        assert downloaded.path.read_bytes() == payload
        assert downloaded.checksum_sha256 == hashlib.sha256(payload).hexdigest()
        assert downloaded.received_at == RECEIVED_AT
        ephemeral_path = downloaded.path

    assert not ephemeral_path.exists()
    assert session.calls[0][1]["allow_redirects"] is False
    assert session.calls[1][1]["stream"] is True


def test_public_provider_verifies_current_noncommercial_policy(tmp_path):
    policy = (
        b"time delayed at 15 minutes; machine-readable format (csv); "
        b"Non-commercial use of the data is free."
    )
    session = _Session((
        _Response(policy, headers={"Content-Type": "text/html; charset=utf-8"}),
    ))
    provider = NasdaqPublicPreTradeProvider(
        session=session,
        clock=lambda: RECEIVED_AT,
        temp_directory=tmp_path,
    )

    evidence = provider.verify_policy_evidence()

    canonical = (
        "https://tradereports.nasdaq.com/shares/trade-reports/pre-trade\n"
        "time delayed at 15 minutes\n"
        "machine-readable format (csv)\n"
        "Non-commercial use of the data is free."
    ).encode("utf-8")
    assert evidence.checksum_sha256 == hashlib.sha256(canonical).hexdigest()
    assert evidence.verified_at == RECEIVED_AT
    assert evidence.terms_url.endswith("/shares/trade-reports/pre-trade")


def test_public_sync_parses_authorizes_reduces_and_persists_one_minute(tmp_path):
    policy = (
        b"time delayed at 15 minutes; machine-readable format (csv); "
        b"Non-commercial use of the data is free."
    )
    payload = _content(
        _row(
            side="BUY",
            price="321.10",
            quantity="1000",
            orders="4.0",
        ),
        _row(
            side="SELL",
            price="321.30",
            quantity="900",
            orders="3.0",
            timestamp="2026-07-29T08:00:01.000000Z",
        ),
    )
    session = _Session((
        _Response(policy, headers={"Content-Type": "text/html"}),
        _Response(
            b'{"message":null,"reports":['
            b'\"NordicEquity-pretrade-2026-07-29T1000\",'
            b'\"NordicEquity-pretrade-2026-07-29T2200\"]}',
            headers={"Content-Type": "application/json"},
        ),
        _Response(
            payload,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": (
                    'attachment; filename="NordicEquity-pretrade-'
                    '2026-07-29T1000.csv"'
                ),
            },
        ),
    ))
    provider = NasdaqPublicPreTradeProvider(
        session=session,
        clock=lambda: RECEIVED_AT + timedelta(hours=14),
        temp_directory=tmp_path,
    )
    database = _SyncDatabase()
    service = NasdaqPublicPreTradeSyncService(
        database=database,
        provider=provider,
    )

    result = service.run_once()

    assert result.processed_files == 1
    assert result.updates_inserted == 2
    assert result.states_persisted == 1
    assert database.mapping_calls == [{
        "reference_snapshot_id": 42,
        "reference_snapshot": _reference_snapshot(),
    }]
    assert len(database.contract_calls) == 1
    assert len(database.authorization_calls) == 1
    assert database.persisted[0]["reference_snapshot_id"] == 42
    assert database.persisted[0]["snapshot"].states[0].is_two_sided


def test_public_sync_reseeds_from_latest_fresh_report_when_cursor_is_stale(
    tmp_path,
):
    policy = (
        b"time delayed at 15 minutes; machine-readable format (csv); "
        b"Non-commercial use of the data is free."
    )
    payload = _content(
        _row(
            side="BUY",
            price="321.10",
            quantity="1000",
            orders="4.0",
        ),
        _row(
            side="SELL",
            price="321.30",
            quantity="900",
            orders="3.0",
            timestamp="2026-07-29T08:00:01.000000Z",
        ),
    )
    previous_file_name = "NordicEquity-pretrade-2026-07-28T1000.csv"
    previous_payload = _content(
        _row(
            side="BUY",
            price="320.10",
            quantity="1000",
            orders="4.0",
            timestamp="2026-07-28T08:00:00.000000Z",
        ),
        _row(
            side="SELL",
            price="320.30",
            quantity="900",
            orders="3.0",
            timestamp="2026-07-28T08:00:01.000000Z",
        ),
    )
    previous_batch = parse_pre_trade_csv_lines(
        iter(previous_payload.splitlines(keepends=True)),
        file_name=previous_file_name,
        source_url=SOURCE_URL,
        source_checksum_sha256=hashlib.sha256(previous_payload).hexdigest(),
        reference_snapshot=_reference_snapshot(),
        aliases=(),
        received_at=RECEIVED_AT - timedelta(days=1),
    )
    previous_snapshot = apply_top_of_book_batch(
        previous=(),
        batch=previous_batch,
    )
    session = _Session((
        _Response(policy, headers={"Content-Type": "text/html"}),
        _Response(
            b'{"message":null,"reports":['
            b'"NordicEquity-pretrade-2026-07-28T1001",'
            b'"NordicEquity-pretrade-2026-07-29T1000"]}',
            headers={"Content-Type": "application/json"},
        ),
        _Response(
            payload,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": (
                    'attachment; filename="NordicEquity-pretrade-'
                    '2026-07-29T1000.csv"'
                ),
            },
        ),
    ))
    provider = NasdaqPublicPreTradeProvider(
        session=session,
        clock=lambda: RECEIVED_AT,
        temp_directory=tmp_path,
    )
    database = _SyncDatabase(latest_snapshot=previous_snapshot)
    service = NasdaqPublicPreTradeSyncService(
        database=database,
        provider=provider,
        clock=lambda: RECEIVED_AT,
    )

    result = service.run_once()

    assert result.processed_files == 1
    assert len(database.reset_calls) == 1
    assert database.reset_calls[0]["batch"].file_name == FILE_NAME
    assert database.persisted[0]["stream_reset_id"] == 17
    assert database.persisted[0]["snapshot"].states[0].is_two_sided
    assert len(session.calls) == 3


def test_public_provider_rejects_catalog_schema_and_oversized_download(tmp_path):
    invalid_catalog = _Session((
        _Response(
            b'{"reports":["../../secret"]}',
            headers={"Content-Type": "application/json"},
        ),
    ))
    provider = NasdaqPublicPreTradeProvider(
        session=invalid_catalog,
        clock=lambda: RECEIVED_AT,
        temp_directory=tmp_path,
    )
    with pytest.raises(MarketDataError, match="catalog"):
        provider.report_files()

    observed_large = _Session((
        _Response(
            b'{"message":null,"reports":['
            b'"NordicEquity-pretrade-2026-07-29T1000"]}',
            headers={"Content-Type": "application/json"},
        ),
        _Response(
            b"observed large file",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(300 * 1024 * 1024),
                "Content-Disposition": (
                    'attachment; filename="NordicEquity-pretrade-'
                    '2026-07-29T1000.csv"'
                ),
            },
        ),
    ))
    provider = NasdaqPublicPreTradeProvider(
        session=observed_large,
        clock=lambda: RECEIVED_AT,
        temp_directory=tmp_path,
    )
    report = provider.report_files()[0]
    with provider.open_report_file(report) as downloaded:
        assert downloaded.size_bytes == len(b"observed large file")

    oversized = _Session((
        _Response(
            b'{"message":null,"reports":['
            b'"NordicEquity-pretrade-2026-07-29T1000"]}',
            headers={"Content-Type": "application/json"},
        ),
        _Response(
            b"too large",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(600 * 1024 * 1024),
                "Content-Disposition": (
                    'attachment; filename="NordicEquity-pretrade-'
                    '2026-07-29T1000.csv"'
                ),
            },
        ),
    ))
    provider = NasdaqPublicPreTradeProvider(
        session=oversized,
        clock=lambda: RECEIVED_AT,
        temp_directory=tmp_path,
    )
    report = provider.report_files()[0]
    with pytest.raises(MarketDataError, match="size limit"):
        with provider.open_report_file(report):
            pass
