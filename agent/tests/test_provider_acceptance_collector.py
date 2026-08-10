from datetime import date, datetime, timezone

import pytest

from src.data.market_data import (
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketDataError,
)
from src.data.nasdaq_reference import NasdaqInstrumentAlias
from src.provider_acceptance_collector import (
    AcceptanceSampleFile,
    collect_acceptance_session,
)


HEADER = (
    "Trading date and time;Instrument identification code;Price;"
    "Missing Price;Price currency;Price notation;Quantity;"
    "Venue of execution;Trading system;Publication date and time;"
    "Venue of publication;Transaction identification code;Flags"
)


def _reference() -> InstrumentUniverseSnapshot:
    return InstrumentUniverseSnapshot(
        mic="XSTO",
        instruments=(
            InstrumentRecord(
                isin="SE0000115446",
                symbol=None,
                name="Volvo AB ser. B",
                mic="XSTO",
                currency="EUR",
                instrument_type="COMMON_STOCK",
                source="esma-firds",
                cfi_code="ESVUFR",
            ),
        ),
    )


def _alias() -> NasdaqInstrumentAlias:
    return NasdaqInstrumentAlias(
        isin="SE0000115446",
        provider_symbol="VOLV B",
        trading_currency="SEK",
        tradable_id="101",
        source_id="VOLV-B",
        valid_from=date(1984, 1, 2),
        valid_to=None,
    )


def _sample(*, local_minute: str, event_time: str, price: str):
    content = "\r\n".join(
        [
            '"sep=;"',
            HEADER,
            (
                f"{event_time};SE0000115446;{price};;SEK;MONE;100;"
                f"XSTO;CLOB;{event_time};XSTO;trade-{price};---"
            ),
        ]
    ).encode("utf-8")
    return AcceptanceSampleFile(
        file_name=f"NordicEquity-posttrade-{local_minute}",
        received_at=datetime(
            2026,
            7,
            29,
            13,
            13,
            tzinfo=timezone.utc,
        ),
        content=content,
    )


def test_collects_one_non_trading_session_with_separate_currency_evidence():
    session = collect_acceptance_session(
        reference_snapshot=_reference(),
        aliases=(_alias(),),
        product_covered_isins=("SE0000115446",),
        samples=(
            _sample(
                local_minute="2026-07-29T1455",
                event_time="2026-07-29T12:55:00.000000Z",
                price="320.10",
            ),
            _sample(
                local_minute="2026-07-29T1456",
                event_time="2026-07-29T12:56:00.000000Z",
                price="321.20",
            ),
        ),
    )

    assert session.session_date == date(2026, 7, 29)
    assert session.expected_instruments == 1
    assert session.product_covered_instruments == 1
    assert session.symbol_mapped_instruments == 1
    assert session.sample_file_count == 2
    assert session.sample_quote_count == 2
    assert session.max_observed_delivery_seconds == 1_080
    assert len(session.evidence_checksum_sha256) == 64


@pytest.mark.parametrize(
    ("aliases", "coverage", "message"),
    [
        ((), ("SE0000115446",), "exact instrument coverage"),
        ((_alias(),), (), "product coverage"),
    ],
)
def test_collector_fails_closed_without_verified_full_coverage(
    aliases,
    coverage,
    message,
):
    with pytest.raises(MarketDataError, match=message):
        collect_acceptance_session(
            reference_snapshot=_reference(),
            aliases=aliases,
            product_covered_isins=coverage,
            samples=(
                _sample(
                    local_minute="2026-07-29T1456",
                    event_time="2026-07-29T12:56:00.000000Z",
                    price="321.20",
                ),
            ),
        )
