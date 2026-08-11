from datetime import date, datetime, timezone

import pytest

from src.data.market_data import (
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketDataError,
)
from src.data.nasdaq_reference import parse_nasdaq_reference_file


RECEIVED_AT = datetime(2026, 7, 30, 5, 0, tzinfo=timezone.utc)
SOURCE_PATH = (
    "INET_Ref_Data/20260730/Nordic_Equity_RefData.tip"
)


def _instrument(
    *,
    isin: str,
    currency: str,
    instrument_type: str,
    cfi_code: str,
) -> InstrumentRecord:
    return InstrumentRecord(
        isin=isin,
        symbol=None,
        name=f"FIRDS {isin}",
        mic="XSTO",
        currency=currency,
        instrument_type=instrument_type,
        source="esma-firds",
        cfi_code=cfi_code,
    )


def _reference_snapshot() -> InstrumentUniverseSnapshot:
    return InstrumentUniverseSnapshot(
        mic="XSTO",
        instruments=(
            _instrument(
                isin="SE0000115446",
                currency="SEK",
                instrument_type="COMMON_STOCK",
                cfi_code="ESVUFR",
            ),
            _instrument(
                isin="US0528001094",
                currency="SEK",
                instrument_type="DEPOSITARY_RECEIPT",
                cfi_code="EDSXDR",
            ),
        ),
    )


def _tradable(
    *,
    tradable_id: str,
    source_id: str,
    symbol: str,
    isin: str,
    cfi_code: str,
    currency: str = "SEK",
    primary_mic: str = "XSTO",
    exchange_id: str = "10",
    extra: str = "",
) -> str:
    return (
        f"BDt;i{tradable_id};Si{source_id};s1;Ex{exchange_id};Mk20;"
        f"INi{source_id};SYm{symbol};NAm{symbol};ISn{isin};"
        f"CUi{currency};CUt{currency};LDa20200101;CFc{cfi_code};"
        f"PMIc{primary_mic};{extra}"
    )


def _share(*, tradable_id: str, source_id: str) -> str:
    return f"BDSh;i{tradable_id};Si{source_id};s1;"


def _payload(*messages: str) -> bytes:
    common = (
        "BDBu;Bd20260730;",
        (
            "BDx;i10;SiXSTO;s1;SYmXSTO;"
            "NAmNasdaq Stockholm;CNySE;MIcXSTO;"
        ),
        "BDm;i20;SiSTOCKS;s1;Ex10;NAmShares;SYmSTO;MIcXSTO;",
    )
    return ("\n".join((*common, *messages, "EOBd;s1;")) + "\n").encode(
        "ascii"
    )


def _valid_payload() -> bytes:
    return _payload(
        _share(tradable_id="101", source_id="VOLV-B"),
        _tradable(
            tradable_id="102",
            source_id="ALIV-SDB",
            symbol="ALIV SDB",
            isin="US0528001094",
            cfi_code="EDSXDR",
        ),
        _tradable(
            tradable_id="101",
            source_id="VOLV-B",
            symbol="VOLV B",
            isin="SE0000115446",
            cfi_code="ESVUFR",
        ),
        _share(tradable_id="102", source_id="ALIV-SDB"),
    )


def _parse(
    content: bytes,
    *,
    reference_snapshot: InstrumentUniverseSnapshot | None = None,
):
    return parse_nasdaq_reference_file(
        content,
        source_path=SOURCE_PATH,
        received_at=RECEIVED_AT,
        reference_snapshot=reference_snapshot or _reference_snapshot(),
        provider="nasdaq-nordic",
        tip_version="3.10.17.1",
    )


def test_parser_binds_every_firds_isin_to_exact_official_ticker():
    snapshot = _parse(_valid_payload())

    assert snapshot.provider == "nasdaq-nordic"
    assert snapshot.mic == "XSTO"
    assert snapshot.business_date == date(2026, 7, 30)
    assert snapshot.tip_version == "3.10.17.1"
    assert snapshot.source_path == SOURCE_PATH
    assert snapshot.reference_checksum_sha256 == (
        _reference_snapshot().checksum_sha256
    )
    assert [
        (alias.isin, alias.provider_symbol, alias.tradable_id)
        for alias in snapshot.aliases
    ] == [
        ("SE0000115446", "VOLV B", "101"),
        ("US0528001094", "ALIV SDB", "102"),
    ]
    assert len(snapshot.checksum_sha256) == 64


def test_parser_keeps_trading_currency_separate_from_firds_notional_currency():
    reference_snapshot = InstrumentUniverseSnapshot(
        mic="XSTO",
        instruments=(
            _instrument(
                isin="SE0000115446",
                currency="EUR",
                instrument_type="COMMON_STOCK",
                cfi_code="ESVUFR",
            ),
            _instrument(
                isin="US0528001094",
                currency="USD",
                instrument_type="DEPOSITARY_RECEIPT",
                cfi_code="EDSXDR",
            ),
        ),
    )

    snapshot = _parse(
        _valid_payload(),
        reference_snapshot=reference_snapshot,
    )

    assert [alias.trading_currency for alias in snapshot.aliases] == [
        "SEK",
        "SEK",
    ]
    assert [
        instrument.currency for instrument in reference_snapshot.instruments
    ] == ["EUR", "USD"]


def test_parser_accepts_one_end_marker_per_source_system():
    content = (
        "BDBu;Bd20260730;\n"
        "BDx;i10;SiXSTO;s1;SYmXSTO;NAmStockholm;MIcXSTO;\n"
        + _tradable(
            tradable_id="101",
            source_id="VOLV-B",
            symbol="VOLV B",
            isin="SE0000115446",
            cfi_code="ESVUFR",
        )
        + "\n"
        + _share(tradable_id="101", source_id="VOLV-B")
        + "\n"
        "EOBd;s1;\n"
        "BDx;i10;SiXSTO;s2;SYmXSTO;NAmStockholm;MIcXSTO;\n"
        "BDt;i102;SiALIV-SDB;s2;Ex10;Mk20;INiALIV-SDB;"
        "SYmALIV SDB;NAmAutoliv;ISnUS0528001094;"
        "CUiSEK;CUtSEK;LDa20200101;CFcEDSXDR;PMIcXSTO;\n"
        "BDSh;i102;SiALIV-SDB;s2;\n"
        "EOBd;s2;\n"
    ).encode("ascii")

    snapshot = _parse(content)

    assert len(snapshot.aliases) == 2


def test_parser_rejects_share_and_tradable_source_id_mismatch():
    content = _valid_payload().replace(
        b"BDSh;i101;SiVOLV-B;s1;",
        b"BDSh;i101;SiOTHER;s1;",
    )

    with pytest.raises(MarketDataError, match="source identity"):
        _parse(content)


def test_parser_rejects_partial_alias_coverage():
    content = _payload(
        _tradable(
            tradable_id="101",
            source_id="VOLV-B",
            symbol="VOLV B",
            isin="SE0000115446",
            cfi_code="ESVUFR",
        ),
        _share(tradable_id="101", source_id="VOLV-B"),
    )

    with pytest.raises(MarketDataError, match="exact FIRDS coverage"):
        _parse(content)


def test_parser_rejects_conflicting_isin_or_ticker_mapping():
    content = _payload(
        _tradable(
            tradable_id="101",
            source_id="VOLV-B",
            symbol="DUPLICATE",
            isin="SE0000115446",
            cfi_code="ESVUFR",
        ),
        _share(tradable_id="101", source_id="VOLV-B"),
        _tradable(
            tradable_id="102",
            source_id="ALIV-SDB",
            symbol="DUPLICATE",
            isin="US0528001094",
            cfi_code="EDSXDR",
        ),
        _share(tradable_id="102", source_id="ALIV-SDB"),
    )

    with pytest.raises(MarketDataError, match="ticker is not unique"):
        _parse(content)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("primary_mic", "XHEL", "exact FIRDS coverage"),
        ("currency", "SE1", "currency"),
        ("cfi_code", "ETVUFR", "CFI"),
        ("exchange_id", "99", "exchange"),
    ],
)
def test_parser_rejects_wrong_scope_or_reference_identity(
    field,
    value,
    message,
):
    values = {
        "tradable_id": "101",
        "source_id": "VOLV-B",
        "symbol": "VOLV B",
        "isin": "SE0000115446",
        "cfi_code": "ESVUFR",
    }
    values[field] = value
    content = _payload(
        _tradable(**values),
        _share(tradable_id="101", source_id="VOLV-B"),
        _tradable(
            tradable_id="102",
            source_id="ALIV-SDB",
            symbol="ALIV SDB",
            isin="US0528001094",
            cfi_code="EDSXDR",
        ),
        _share(tradable_id="102", source_id="ALIV-SDB"),
    )

    with pytest.raises(MarketDataError, match=message):
        _parse(content)


def test_parser_requires_business_date_to_match_delivery_path():
    with pytest.raises(MarketDataError, match="source path date"):
        parse_nasdaq_reference_file(
            _valid_payload(),
            source_path=(
                "INET_Ref_Data/20260729/Nordic_Equity_RefData.tip"
            ),
            received_at=RECEIVED_AT,
            reference_snapshot=_reference_snapshot(),
            provider="nasdaq-nordic",
            tip_version="3.10.17.1",
        )


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"BDBu;Bd20260730;\n", "EndOfBasicData"),
        (b"BDBu;Bd20260730;\nEOBd;s1", "terminate"),
        (
            b"BDBu;Bd20260730;Bd20260731;\nEOBd;s1;\n",
            "duplicate TIP tag",
        ),
        (
            b"BDBu;Bd20260730;\nBDt;i1;NAmBad\\;\nEOBd;s1;\n",
            "escape",
        ),
        (
            "BDBu;Bd20260730;\nBDt;i1;NAmBörs;\nEOBd;s1;\n".encode(),
            "ASCII",
        ),
    ],
)
def test_parser_rejects_malformed_or_incomplete_tip(content, message):
    with pytest.raises(MarketDataError, match=message):
        _parse(content)


def test_parser_rejects_unapproved_tip_version_before_reading_content():
    with pytest.raises(MarketDataError, match="TIP version"):
        parse_nasdaq_reference_file(
            _valid_payload(),
            source_path=SOURCE_PATH,
            received_at=RECEIVED_AT,
            reference_snapshot=_reference_snapshot(),
            provider="nasdaq-nordic",
            tip_version="3.10.18.7",
        )
