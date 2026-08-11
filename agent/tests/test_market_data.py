from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
from zoneinfo import ZoneInfo

import pytest

from src.data.market_data import (
    FreshnessPolicy,
    IndexLevelRecord,
    InstrumentRecord,
    MarketDataArchive,
    MarketDataError,
    MarketSession,
    QuoteRecord,
    assert_fresh_quote,
)


STOCKHOLM = ZoneInfo("Europe/Stockholm")


def test_xsto_instrument_requires_valid_isin_and_currency():
    instrument = InstrumentRecord(
        isin="SE0000115446",
        symbol="VOLV B",
        name="Volvo B",
        mic="XSTO",
        currency="SEK",
        instrument_type="COMMON_STOCK",
        source="nasdaq-reference",
    )

    assert instrument.isin == "SE0000115446"
    assert instrument.mic == "XSTO"


@pytest.mark.parametrize(
    "changes",
    [
        {"isin": "SE0000115447"},
        {"mic": "STO"},
        {"currency": "KR"},
        {"symbol": ""},
        {"source": ""},
    ],
)
def test_instrument_rejects_invalid_reference_data(changes):
    values = {
        "isin": "SE0000115446",
        "symbol": "VOLV B",
        "name": "Volvo B",
        "mic": "XSTO",
        "currency": "SEK",
        "instrument_type": "COMMON_STOCK",
        "source": "nasdaq-reference",
    }
    values.update(changes)

    with pytest.raises(MarketDataError):
        InstrumentRecord(**values)


def test_fifteen_minute_quote_is_fresh_with_two_minute_tolerance():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    quote = QuoteRecord(
        isin="SE0000115446",
        mic="XSTO",
        event_time=now - timedelta(minutes=15),
        received_at=now,
        source="nasdaq-delayed",
        last_price=Decimal("321.50"),
        currency="SEK",
    )

    assert_fresh_quote(
        quote,
        now=now,
        policy=FreshnessPolicy(
            max_delay=timedelta(minutes=15),
            tolerance=timedelta(minutes=2),
        ),
    )


def test_quote_record_accepts_only_one_persisted_price_evidence_id():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    book_mark = QuoteRecord(
        book_state_id=73,
        isin="SE0000115446",
        mic="XSTO",
        event_time=now - timedelta(minutes=15),
        received_at=now,
        source="nasdaq-nordic-public-pretrade",
        last_price=Decimal("321.50"),
        currency="SEK",
    )

    assert book_mark.book_state_id == 73
    assert book_mark.quote_id is None

    with pytest.raises(MarketDataError, match="one persisted"):
        replace(book_mark, quote_id=41)


def test_quote_older_than_delay_and_tolerance_is_rejected():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    quote = QuoteRecord(
        isin="SE0000115446",
        mic="XSTO",
        event_time=now - timedelta(minutes=17, seconds=1),
        received_at=now,
        source="nasdaq-delayed",
        last_price=Decimal("321.50"),
        currency="SEK",
    )

    with pytest.raises(MarketDataError, match="stale"):
        assert_fresh_quote(
            quote,
            now=now,
            policy=FreshnessPolicy(
                max_delay=timedelta(minutes=15),
                tolerance=timedelta(minutes=2),
            ),
        )


def test_pretrade_book_freshness_uses_sealed_coverage_time():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
    quote = QuoteRecord(
        book_state_id=73,
        isin="SE0000115446",
        mic="XSTO",
        event_time=now - timedelta(minutes=19),
        covered_through=now - timedelta(minutes=15),
        received_at=now,
        source="nasdaq-nordic-public-pretrade",
        last_price=Decimal("321.50"),
        currency="SEK",
    )

    assert_fresh_quote(
        quote,
        now=now,
        policy=FreshnessPolicy(
            max_delay=timedelta(minutes=15),
            tolerance=timedelta(minutes=2),
        ),
    )


def test_future_or_naive_quote_timestamp_is_rejected():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    with pytest.raises(MarketDataError):
        QuoteRecord(
            isin="SE0000115446",
            mic="XSTO",
            event_time=now + timedelta(seconds=10),
            received_at=now,
            source="nasdaq-delayed",
            last_price=Decimal("321.50"),
            currency="SEK",
        )

    with pytest.raises(MarketDataError):
        QuoteRecord(
            isin="SE0000115446",
            mic="XSTO",
            event_time=datetime(2026, 7, 29, 10, 0),
            received_at=now,
            source="nasdaq-delayed",
            last_price=Decimal("321.50"),
            currency="SEK",
        )


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_quote_rejects_invalid_price(price):
    now = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)

    with pytest.raises(MarketDataError):
        QuoteRecord(
            isin="SE0000115446",
            mic="XSTO",
            event_time=now - timedelta(minutes=15),
            received_at=now,
            source="nasdaq-delayed",
            last_price=price,
            currency="SEK",
        )


def test_index_level_normalizes_and_validates_provenance():
    record = IndexLevelRecord(
        contract_key="licensed-index-xsto-v1",
        symbol="omxsgi",
        mic="xsto",
        event_time=datetime(
            2026,
            7,
            29,
            9,
            45,
            tzinfo=timezone.utc,
        ),
        received_at=datetime(
            2026,
            7,
            29,
            10,
            0,
            tzinfo=timezone.utc,
        ),
        source="licensed-index-delayed",
        level=Decimal("245.50"),
        source_checksum_sha256="a" * 64,
    )

    assert record.symbol == "OMXSGI"
    assert record.mic == "XSTO"
    assert record.level == Decimal("245.50")
    assert record.observed_delay == timedelta(minutes=15)

    with pytest.raises(MarketDataError):
        replace(record, source_checksum_sha256="not-a-checksum")


def test_market_session_uses_stockholm_timezone_and_exact_boundaries():
    session = MarketSession.for_local_date(
        mic="XSTO",
        session_date=date(2026, 7, 29),
        opens_at="09:00",
        closes_at="17:30",
        timezone_name="Europe/Stockholm",
        source="official-calendar",
    )

    assert session.opens_at.utcoffset() == timedelta(hours=2)
    assert session.is_open(
        datetime(2026, 7, 29, 9, 0, tzinfo=STOCKHOLM)
    )
    assert session.is_open(
        datetime(2026, 7, 29, 17, 29, 59, tzinfo=STOCKHOLM)
    )
    assert not session.is_open(
        datetime(2026, 7, 29, 17, 30, tzinfo=STOCKHOLM)
    )


def test_market_session_rejects_weekend_and_invalid_hours():
    with pytest.raises(MarketDataError):
        MarketSession.for_local_date(
            mic="XSTO",
            session_date=date(2026, 8, 1),
            opens_at="09:00",
            closes_at="17:30",
            timezone_name="Europe/Stockholm",
            source="official-calendar",
        )

    with pytest.raises(MarketDataError):
        MarketSession.for_local_date(
            mic="XSTO",
            session_date=date(2026, 7, 29),
            opens_at="17:30",
            closes_at="09:00",
            timezone_name="Europe/Stockholm",
            source="official-calendar",
        )


def test_market_data_archive_rejects_oversized_raw_and_gzip_payloads(
    monkeypatch,
):
    import src.data.market_data as market_data

    monkeypatch.setattr(market_data, "_MAX_RAW_ARCHIVE_BYTES", 16)
    content = b"x" * 17

    with pytest.raises(MarketDataError, match="size limit"):
        MarketDataArchive.from_bytes(
            provider="test",
            data_type="quotes",
            file_name="quotes.csv",
            report_minute=datetime(
                2026,
                7,
                29,
                10,
                0,
                tzinfo=timezone.utc,
            ),
            received_at=datetime(
                2026,
                7,
                29,
                10,
                1,
                tzinfo=timezone.utc,
            ),
            source_url="https://example.test/quotes.csv",
            content=content,
        )

    with pytest.raises(MarketDataError, match="size limit"):
        MarketDataArchive(
            provider="test",
            data_type="quotes",
            file_name="quotes.csv",
            report_minute=datetime(
                2026,
                7,
                29,
                10,
                0,
                tzinfo=timezone.utc,
            ),
            received_at=datetime(
                2026,
                7,
                29,
                10,
                1,
                tzinfo=timezone.utc,
            ),
            source_url="https://example.test/quotes.csv",
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            content_encoding="gzip",
            compressed_content=gzip.compress(content, mtime=0),
            uncompressed_bytes=1,
        )
