from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from zoneinfo import ZoneInfo

import pytest

from src.data.market_data import (
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketDataError,
    TopOfBookBatch,
    TopOfBookUpdate,
    _TOP_OF_BOOK_PARSER_PROOF,
)
from src.data.nasdaq_pretrade import parse_pre_trade_csv_lines
from src.data.nasdaq_reference import NasdaqInstrumentAlias
from src.data.top_of_book import apply_top_of_book_batch


REPORT_MINUTE = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
RECEIVED = datetime(2026, 7, 29, 8, 16, tzinfo=timezone.utc)
SOURCE_URL = (
    "https://tradereports.nasdaq.com/api/regulatory/"
    "trade-report/download?type=PRE_TRADE"
)
HEADER = (
    "Update date and time;Instrument identification code;Side;"
    "Market Marker;Price;Price currency;Price notation;Quantity;"
    "Quantity currency;Aggregated number of orders and quotes;Venue;"
    "Trading system;Trading system phase;Publication Time"
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


def _reference_snapshot() -> InstrumentUniverseSnapshot:
    return InstrumentUniverseSnapshot(
        mic="XSTO",
        instruments=(_instrument(),),
    )


def _update(
    *,
    side: str,
    price: str | None,
    sequence: int,
    seconds: int,
    report_minute: datetime = REPORT_MINUTE,
    received_at: datetime = RECEIVED,
    quantity: str = "1000",
    order_count: int = 4,
    trading_system: str = "CLOB",
    isin: str = "SE0000115446",
) -> TopOfBookUpdate:
    event_time = report_minute + timedelta(seconds=seconds)
    return TopOfBookUpdate(
        isin=isin,
        mic="XSTO",
        event_time=event_time,
        publication_time=event_time,
        received_at=received_at,
        source="nasdaq-nordic-delayed-pre-trade",
        side=side,
        currency="SEK",
        price=price,
        quantity=quantity if price is not None else None,
        order_count=order_count if price is not None else None,
        source_sequence=sequence,
        trading_system=trading_system,
        trading_phase="COTR",
    )


def _batch(
    *updates: TopOfBookUpdate,
    report_minute: datetime = REPORT_MINUTE,
    received_at: datetime = RECEIVED,
) -> TopOfBookBatch:
    local_minute = report_minute.astimezone(
        ZoneInfo("Europe/Stockholm")
    )
    rows = [_row(update) for update in updates]
    if not rows:
        timestamp = _utc_text(report_minute)
        rows.append(
            f"{timestamp};SE0000115446;BUY;;1;SEK;1;1;SEK;1;"
            f"XCSE;CLOB;COTR;{timestamp}"
        )
    content = (
        "\r\n".join(['"sep=;"', HEADER, *rows]) + "\r\n"
    ).encode("utf-8")
    return parse_pre_trade_csv_lines(
        iter(content.splitlines(keepends=True)),
        file_name=(
            "NordicEquity-pretrade-"
            f"{local_minute:%Y-%m-%dT%H%M}.csv"
        ),
        source_url=SOURCE_URL,
        source_checksum_sha256=hashlib.sha256(content).hexdigest(),
        reference_snapshot=_reference_snapshot(),
        aliases=(_alias(),),
        received_at=received_at,
    )


def _row(update: TopOfBookUpdate) -> str:
    price = "" if update.price is None else str(update.price)
    quantity = "" if update.quantity is None else str(update.quantity)
    orders = "" if update.order_count is None else str(update.order_count)
    return (
        f"{_utc_text(update.event_time)};{update.isin};{update.side};;"
        f"{price};{update.currency};1;{quantity};{update.currency};"
        f"{orders};{update.mic};{update.trading_system};"
        f"{update.trading_phase};{_utc_text(update.publication_time)}"
    )


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_reducer_builds_two_sided_state_in_source_order():
    states = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(side="BUY", price="321.10", sequence=1, seconds=1),
            _update(side="SELL", price="321.30", sequence=2, seconds=2),
        ),
    )

    assert len(states) == 1
    state = states[0]
    assert state.bid_price == Decimal("321.10")
    assert state.ask_price == Decimal("321.30")
    assert state.bid_quantity == Decimal("1000")
    assert state.ask_quantity == Decimal("1000")
    assert state.bid_order_count == 4
    assert state.ask_order_count == 4
    assert state.is_two_sided is True
    assert state.mid_price == Decimal("321.20")
    assert state.observed_delay == timedelta(minutes=15)
    assert (
        state.reference_checksum_sha256
        == _reference_snapshot().checksum_sha256
    )


def test_reducer_carries_unchanged_side_across_exact_next_file_minute():
    initial = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(side="BUY", price="321.10", sequence=1, seconds=1),
            _update(side="SELL", price="321.30", sequence=2, seconds=2),
        ),
    )
    next_minute = REPORT_MINUTE + timedelta(minutes=1)
    next_received = RECEIVED + timedelta(minutes=1)

    states = apply_top_of_book_batch(
        previous=initial,
        batch=_batch(
            _update(
                side="BUY",
                price="321.20",
                sequence=1,
                seconds=30,
                report_minute=next_minute,
                received_at=next_received,
            ),
            report_minute=next_minute,
            received_at=next_received,
        ),
    )

    assert states[0].bid_price == Decimal("321.20")
    assert states[0].ask_price == Decimal("321.30")
    assert states[0].covered_through == datetime(
        2026,
        7,
        29,
        8,
        2,
        tzinfo=timezone.utc,
    )


def test_reducer_preserves_an_explicit_empty_side_without_fallback():
    initial = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(side="BUY", price="321.10", sequence=1, seconds=1),
            _update(side="SELL", price="321.30", sequence=2, seconds=2),
        ),
    )
    next_minute = REPORT_MINUTE + timedelta(minutes=1)
    next_received = RECEIVED + timedelta(minutes=1)

    states = apply_top_of_book_batch(
        previous=initial,
        batch=_batch(
            _update(
                side="SELL",
                price=None,
                sequence=1,
                seconds=3,
                report_minute=next_minute,
                received_at=next_received,
            ),
            report_minute=next_minute,
            received_at=next_received,
        ),
    )

    assert states[0].bid_price == Decimal("321.10")
    assert states[0].ask_price is None
    assert states[0].is_two_sided is False
    assert states[0].mid_price is None
    assert states[0].ask_event_time == datetime(
        2026,
        7,
        29,
        8,
        1,
        3,
        tzinfo=timezone.utc,
    )


def test_reducer_refuses_to_carry_state_across_a_missing_file_minute():
    initial = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(side="BUY", price="321.10", sequence=1, seconds=1),
            _update(side="SELL", price="321.30", sequence=2, seconds=2),
        ),
    )
    future_minute = REPORT_MINUTE + timedelta(minutes=2)
    future_received = RECEIVED + timedelta(minutes=2)

    with pytest.raises(MarketDataError, match="gap"):
        apply_top_of_book_batch(
            previous=initial,
            batch=_batch(
                report_minute=future_minute,
                received_at=future_received,
            ),
        )


def test_reducer_accepts_an_evidenced_empty_exact_next_file():
    initial = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(side="BUY", price="321.10", sequence=1, seconds=1),
            _update(side="SELL", price="321.30", sequence=2, seconds=2),
        ),
    )
    next_minute = REPORT_MINUTE + timedelta(minutes=1)
    next_received = RECEIVED + timedelta(minutes=1)

    states = apply_top_of_book_batch(
        previous=initial,
        batch=_batch(
            report_minute=next_minute,
            received_at=next_received,
        ),
    )

    assert states[0].bid_price == Decimal("321.10")
    assert states[0].ask_price == Decimal("321.30")
    assert states[0].covered_through == next_minute + timedelta(minutes=1)


def test_reducer_rejects_side_time_regression():
    with pytest.raises(MarketDataError, match="event time regression"):
        apply_top_of_book_batch(
            previous=(),
            batch=_batch(
                _update(
                    side="BUY",
                    price="321.10",
                    sequence=1,
                    seconds=50,
                ),
                _update(
                    side="BUY",
                    price="321.20",
                    sequence=2,
                    seconds=10,
                ),
            ),
        )


def test_reducer_rejects_final_but_accepts_resolved_transient_cross():
    with pytest.raises(MarketDataError, match="crossed"):
        apply_top_of_book_batch(
            previous=(),
            batch=_batch(
                _update(
                    side="SELL",
                    price="321.30",
                    sequence=1,
                    seconds=1,
                ),
                _update(
                    side="BUY",
                    price="321.40",
                    sequence=2,
                    seconds=2,
                ),
            ),
        )

    snapshot = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(
                side="SELL",
                price="100",
                sequence=1,
                seconds=1,
            ),
            _update(
                side="BUY",
                price="101",
                sequence=2,
                seconds=2,
            ),
            _update(
                side="SELL",
                price="102",
                sequence=3,
                seconds=3,
            ),
        ),
    )

    assert snapshot.states[0].bid_price == Decimal("101")
    assert snapshot.states[0].ask_price == Decimal("102")


def test_reducer_omits_one_final_crossed_book_from_a_valid_batch():
    second_isin = "SE0000108656"
    updates = (
        _update(side="BUY", price="100", sequence=1, seconds=1),
        _update(side="SELL", price="101", sequence=2, seconds=2),
        _update(
            isin=second_isin,
            side="BUY",
            price="200",
            sequence=3,
            seconds=3,
        ),
        _update(
            isin=second_isin,
            side="SELL",
            price="199",
            sequence=4,
            seconds=4,
        ),
    )
    content = (
        "\r\n".join(['"sep=;"', HEADER, *(_row(item) for item in updates)])
        + "\r\n"
    ).encode("utf-8")
    reference = InstrumentUniverseSnapshot(
        mic="XSTO",
        instruments=(
            _instrument(),
            InstrumentRecord(
                isin=second_isin,
                symbol="ERIC B",
                name="Ericsson B",
                mic="XSTO",
                currency="SEK",
                instrument_type="COMMON_STOCK",
                source="esma-firds",
            ),
        ),
    )
    batch = parse_pre_trade_csv_lines(
        iter(content.splitlines(keepends=True)),
        file_name="NordicEquity-pretrade-2026-07-29T1000.csv",
        source_url=SOURCE_URL,
        source_checksum_sha256=hashlib.sha256(content).hexdigest(),
        reference_snapshot=reference,
        aliases=(
            _alias(),
            NasdaqInstrumentAlias(
                isin=second_isin,
                provider_symbol="ERIC B",
                trading_currency="SEK",
                tradable_id="102",
                source_id="ERIC-B",
                valid_from=None,
                valid_to=None,
            ),
        ),
        received_at=RECEIVED,
    )

    snapshot = apply_top_of_book_batch(previous=(), batch=batch)

    assert tuple(state.isin for state in snapshot.states) == (
        "SE0000115446",
    )


def test_reducer_rejects_mixed_trading_systems_for_one_book():
    clob_update = _update(
        side="BUY",
        price="321.10",
        sequence=1,
        seconds=1,
        trading_system="CLOB",
    )
    mixed_batch = replace(
        _batch(clob_update),
        _parser_proof=_TOP_OF_BOOK_PARSER_PROOF,
        input_row_count=2,
        updates=(
            clob_update,
            _update(
                side="SELL",
                price="321.30",
                sequence=2,
                seconds=2,
                trading_system="OTHER",
            ),
        ),
    )

    with pytest.raises(MarketDataError, match="trading system"):
        apply_top_of_book_batch(previous=(), batch=mixed_batch)


def test_batch_cannot_be_constructed_without_verified_parser():
    parsed = _batch(
        _update(side="BUY", price="321.10", sequence=1, seconds=1),
    )

    with pytest.raises(MarketDataError, match="verified parser"):
        TopOfBookBatch(
            provider=parsed.provider,
            data_type=parsed.data_type,
            source=parsed.source,
            mic=parsed.mic,
            file_name=parsed.file_name,
            source_url=parsed.source_url,
            source_checksum_sha256=parsed.source_checksum_sha256,
            reference_checksum_sha256=parsed.reference_checksum_sha256,
            instrument_isins=parsed.instrument_isins,
            report_minute=parsed.report_minute,
            received_at=parsed.received_at,
            input_row_count=parsed.input_row_count,
            updates=parsed.updates,
        )
    with pytest.raises(MarketDataError, match="verified parser"):
        replace(
            parsed,
            report_minute=parsed.report_minute + timedelta(minutes=1),
        )
    assert not hasattr(TopOfBookBatch, "_from_verified_parser")


def test_reducer_rejects_received_at_regression():
    initial = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(side="BUY", price="321.10", sequence=1, seconds=1),
            _update(side="SELL", price="321.30", sequence=2, seconds=2),
        ),
    )
    next_minute = REPORT_MINUTE + timedelta(minutes=1)
    regressed_receipt = RECEIVED - timedelta(seconds=30)

    with pytest.raises(MarketDataError, match="received_at regression"):
        apply_top_of_book_batch(
            previous=initial,
            batch=_batch(
                report_minute=next_minute,
                received_at=regressed_receipt,
            ),
        )


def test_state_rejects_zero_liquidity():
    initial = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(side="BUY", price="321.10", sequence=1, seconds=1),
        ),
    )

    with pytest.raises(MarketDataError, match="bid_quantity must be positive"):
        replace(initial[0], bid_quantity=Decimal("0"))
    with pytest.raises(
        MarketDataError,
        match="bid_order_count must be a positive integer",
    ):
        replace(initial[0], bid_order_count=0)


def test_reducer_rejects_previous_book_outside_reference_universe():
    initial = apply_top_of_book_batch(
        previous=(),
        batch=_batch(
            _update(side="BUY", price="321.10", sequence=1, seconds=1),
        ),
    )
    unknown = replace(initial[0], isin="SE0000108656")
    next_minute = REPORT_MINUTE + timedelta(minutes=1)

    with pytest.raises(MarketDataError, match="reference universe"):
        apply_top_of_book_batch(
            previous=(unknown,),
            batch=_batch(
                report_minute=next_minute,
                received_at=RECEIVED + timedelta(minutes=1),
            ),
        )


def test_empty_bootstrap_preserves_cursor_and_rejects_a_gap():
    empty = apply_top_of_book_batch(
        previous=(),
        batch=_batch(),
    )

    assert len(empty) == 0
    assert empty.cursor.covered_through == REPORT_MINUTE + timedelta(
        minutes=1
    )
    with pytest.raises(MarketDataError, match="gap"):
        apply_top_of_book_batch(
            previous=empty,
            batch=_batch(
                report_minute=REPORT_MINUTE + timedelta(minutes=2),
                received_at=RECEIVED + timedelta(minutes=2),
            ),
        )


def test_empty_bootstrap_preserves_received_at_monotonicity():
    empty = apply_top_of_book_batch(
        previous=(),
        batch=_batch(),
    )
    next_minute = REPORT_MINUTE + timedelta(minutes=1)

    with pytest.raises(MarketDataError, match="received_at regression"):
        apply_top_of_book_batch(
            previous=empty,
            batch=_batch(
                report_minute=next_minute,
                received_at=RECEIVED - timedelta(seconds=30),
            ),
        )
