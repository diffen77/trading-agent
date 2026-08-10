from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib

import pytest

from src.data.market_data import (
    FileIngestionResult,
    InstrumentRecord,
    MarketDataError,
)
from src.data.nasdaq_delayed import discover_post_trade_files
from src.data.nasdaq_reference import NasdaqInstrumentAlias
from src.data.nasdaq_ingestion import NasdaqDelayedIngestionService


HEADER = (
    "Trading date and time;Instrument identification code;Price;"
    "Missing Price;Price currency;Price notation;Quantity;"
    "Venue of execution;Trading system;Publication date and time;"
    "Venue of publication;Transaction identification code;Flags"
)


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


def _report_file(local_minute: str):
    file_name = f"NordicEquity-posttrade-{local_minute}"
    html = (
        '<a href="/api/regulatory/trade-report/download'
        f'?type=POST_TRADE&amp;assetClass=EQUITY&amp;fileName={file_name}">'
        "file</a>"
    )
    return discover_post_trade_files(html)[0]


def _payload(event_time: str, price: str) -> bytes:
    row = (
        f"{event_time};SE0000115446;{price};;SEK;MONE;100;"
        f"XSTO;CLOB;{event_time};XSTO;trade-{price};---"
    )
    return "\r\n".join(['"sep=;"', HEADER, row]).encode("utf-8")


class _Provider:
    def __init__(self, report_files, payloads):
        self._report_files = tuple(report_files)
        self._payloads = payloads
        self.downloaded = []

    def list_instruments(self):
        return (_instrument(),)

    def list_aliases(self):
        return (_alias(),)

    def report_files(self):
        return self._report_files

    def download_report_file(self, report_file):
        self.downloaded.append(report_file.file_name)
        return self._payloads[report_file.file_name]


class _Database:
    def __init__(self, latest=None):
        self.latest = latest
        self.ingested = []
        self.failures = []

    def get_latest_data_file_report_minute(self, *, provider, data_type):
        return self.latest

    def ingest_market_data_file(self, archive, quotes, gaps):
        self.ingested.append((archive, tuple(quotes), tuple(gaps)))
        self.latest = archive.report_minute
        return FileIngestionResult(
            inserted=True,
            quotes_inserted=len(quotes),
            sync_run_id=len(self.ingested),
        )

    def record_data_sync_failure(self, **values):
        self.failures.append(values)
        return 100 + len(self.failures)


def test_ingestion_archives_raw_bytes_checksum_quotes_and_detected_gap():
    report_1455 = _report_file("2026-07-29T1455")
    report_1457 = _report_file("2026-07-29T1457")
    payload_1455 = _payload("2026-07-29T12:55:00.000000Z", "320.10")
    payload_1457 = _payload("2026-07-29T12:57:00.000000Z", "321.20")
    provider = _Provider(
        [report_1457, report_1455],
        {
            report_1455.file_name: payload_1455,
            report_1457.file_name: payload_1457,
        },
    )
    database = _Database(
        latest=datetime(2026, 7, 29, 12, 54, tzinfo=timezone.utc)
    )
    received_at = datetime(2026, 7, 29, 13, 13, tzinfo=timezone.utc)
    service = NasdaqDelayedIngestionService(
        database=database,
        provider=provider,
        clock=lambda: received_at,
        max_files_per_run=10,
    )

    result = service.run_once()

    assert result.processed_files == 2
    assert result.inserted_quotes == 2
    assert result.duplicate_files == 0
    assert result.gaps_detected == 1
    assert provider.downloaded == [
        report_1455.file_name,
        report_1457.file_name,
    ]

    first_archive, first_quotes, first_gaps = database.ingested[0]
    _, _, second_gaps = database.ingested[1]
    assert first_archive.checksum_sha256 == hashlib.sha256(
        payload_1455
    ).hexdigest()
    assert gzip.decompress(first_archive.compressed_content) == payload_1455
    assert first_archive.uncompressed_bytes == len(payload_1455)
    assert first_quotes[0].last_price == Decimal("320.10")
    assert first_gaps == ()
    assert len(second_gaps) == 1
    assert second_gaps[0].gap_start == datetime(
        2026,
        7,
        29,
        12,
        56,
        tzinfo=timezone.utc,
    )
    assert second_gaps[0].gap_end == second_gaps[0].gap_start


def test_ingestion_restart_is_a_noop_when_latest_file_is_already_stored():
    report_file = _report_file("2026-07-29T1456")
    provider = _Provider(
        [report_file],
        {
            report_file.file_name: _payload(
                "2026-07-29T12:56:00.000000Z",
                "321.20",
            )
        },
    )
    database = _Database(latest=report_file.report_minute)
    service = NasdaqDelayedIngestionService(
        database=database,
        provider=provider,
        clock=lambda: datetime(
            2026,
            7,
            29,
            13,
            13,
            tzinfo=timezone.utc,
        ),
    )

    result = service.run_once()

    assert result.processed_files == 0
    assert result.inserted_quotes == 0
    assert provider.downloaded == []
    assert database.ingested == []


def test_ingestion_records_failed_sync_before_reraising_safe_error():
    class FailingProvider(_Provider):
        def report_files(self):
            raise MarketDataError("Nasdaq delayed data request failed")

    provider = FailingProvider([], {})
    database = _Database()
    now = datetime(2026, 7, 29, 13, 13, tzinfo=timezone.utc)
    service = NasdaqDelayedIngestionService(
        database=database,
        provider=provider,
        clock=lambda: now,
    )

    with pytest.raises(MarketDataError, match="request failed"):
        service.run_once()

    assert len(database.failures) == 1
    assert database.failures[0] == {
        "provider": "nasdaq-nordic",
        "data_type": "delayed-post-trade-equity",
        "started_at": now,
        "finished_at": now,
        "error_message": "Nasdaq delayed data request failed",
    }
