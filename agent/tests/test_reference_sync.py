from datetime import date, datetime, timezone
import hashlib
from io import BytesIO
import zipfile

import pytest

from src.data.esma_firds import EsmaFirdsFile, EsmaFirdsSnapshot
from src.data.market_data import (
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketDataError,
    ReferenceSyncResult,
)
from src.data.nasdaq_reference import NasdaqAliasSyncResult
from src.data.reference_sync import (
    EsmaReferenceSyncService,
    NasdaqReferenceSyncService,
)


def _snapshot_content(file_name: str, count: int) -> bytes:
    records = []
    isins = ["SE0000115446", "US0528001094"]
    cfis = ["ESVUFR", "EDSXDR"]
    names = ["Volvo AB ser. B", "Autoliv Inc. SDB"]
    for index in range(count):
        records.append(
            "<RefData>"
            "<FinInstrmGnlAttrbts>"
            f"<Id>{isins[index]}</Id>"
            f"<FullNm>{names[index]}</FullNm>"
            f"<ClssfctnTp>{cfis[index]}</ClssfctnTp>"
            "<NtnlCcy>SEK</NtnlCcy>"
            "</FinInstrmGnlAttrbts>"
            "<TradgVnRltdAttrbts>"
            "<Id>XSTO</Id>"
            "<FrstTradDt>2024-02-28T07:00:00Z</FrstTradDt>"
            "</TradgVnRltdAttrbts>"
            "</RefData>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:auth.017.001.02">'
        f"{''.join(records)}"
        "</Document>"
    ).encode()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(file_name.replace(".zip", ".xml"), xml)
    return output.getvalue()


class _Client:
    def __init__(
        self,
        file_record,
        content,
        *,
        expected_as_of=date(2026, 7, 29),
    ):
        self.snapshot = EsmaFirdsSnapshot(
            publication_date=file_record.publication_date,
            files=(file_record,),
        )
        self.content = content
        self.downloads = []
        self.expected_as_of = expected_as_of

    def latest_full_equity_snapshot(self, *, as_of):
        assert as_of == self.expected_as_of
        return self.snapshot

    def download(self, file_record):
        self.downloads.append(file_record.file_name)
        return self.content


class _Database:
    def __init__(self, latest=None):
        self.latest = latest
        self.calls = []

    def get_latest_reference_snapshot_date(self, *, provider, mic):
        assert provider == "esma-firds"
        assert mic == "XSTO"
        return self.latest

    def sync_instrument_snapshot(self, **kwargs):
        self.calls.append(kwargs)
        return ReferenceSyncResult(
            inserted=True,
            instruments_upserted=len(kwargs["instruments"]),
            instruments_deactivated=1,
            sync_run_id=42,
        )


def _client_with_records(count: int) -> _Client:
    file_name = "FULINS_E_20260725_01of01.zip"
    content = _snapshot_content(file_name, count)
    file_record = EsmaFirdsFile(
        file_name=file_name,
        publication_date=date(2026, 7, 25),
        download_url=f"https://firds.esma.europa.eu/firds/{file_name}",
        checksum_md5=hashlib.md5(content).hexdigest(),
    )
    return _Client(file_record, content)


def test_reference_sync_archives_and_commits_complete_snapshot():
    client = _client_with_records(2)
    database = _Database()
    now = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)
    service = EsmaReferenceSyncService(
        database=database,
        client=client,
        clock=lambda: now,
        minimum_instruments=2,
    )

    result = service.sync()

    assert result.inserted is True
    assert result.snapshot_date == date(2026, 7, 25)
    assert result.instrument_count == 2
    assert result.instruments_deactivated == 1
    assert client.downloads == ["FULINS_E_20260725_01of01.zip"]
    call = database.calls[0]
    assert len(call["archives"]) == 1
    assert call["archives"][0].upstream_checksum_algorithm == "md5"
    assert call["archives"][0].upstream_checksum is not None
    assert len(call["instruments"]) == 2


def test_reference_sync_uses_stockholm_date_around_local_midnight():
    client = _client_with_records(2)
    client.expected_as_of = date(2026, 7, 30)
    service = EsmaReferenceSyncService(
        database=_Database(),
        client=client,
        clock=lambda: datetime(
            2026,
            7,
            29,
            22,
            30,
            tzinfo=timezone.utc,
        ),
        minimum_instruments=2,
    )

    service.sync()


def test_reference_sync_skips_already_stored_snapshot_without_download():
    client = _client_with_records(2)
    database = _Database(latest=date(2026, 7, 25))
    service = EsmaReferenceSyncService(
        database=database,
        client=client,
        clock=lambda: datetime(
            2026,
            7,
            29,
            14,
            0,
            tzinfo=timezone.utc,
        ),
        minimum_instruments=2,
    )

    result = service.sync()

    assert result.inserted is False
    assert result.instrument_count == 0
    assert client.downloads == []
    assert database.calls == []


def test_reference_sync_rejects_snapshot_below_configured_floor():
    client = _client_with_records(1)
    database = _Database()
    service = EsmaReferenceSyncService(
        database=database,
        client=client,
        clock=lambda: datetime(
            2026,
            7,
            29,
            14,
            0,
            tzinfo=timezone.utc,
        ),
        minimum_instruments=2,
    )

    with pytest.raises(MarketDataError, match="too few"):
        service.sync()

    assert database.calls == []


def test_nasdaq_reference_sync_binds_file_to_latest_frozen_firds():
    reference = InstrumentUniverseSnapshot(
        mic="XSTO",
        instruments=(
            InstrumentRecord(
                isin="SE0000115446",
                symbol=None,
                name="Volvo AB ser. B",
                mic="XSTO",
                currency="SEK",
                instrument_type="COMMON_STOCK",
                source="esma-firds",
                cfi_code="ESVUFR",
            ),
        ),
    )

    class AliasDatabase:
        def __init__(self):
            self.calls = []

        def get_latest_reference_universe(self, *, provider, mic):
            assert provider == "esma-firds"
            assert mic == "XSTO"
            return 77, reference

        def sync_instrument_alias_snapshot(self, **kwargs):
            self.calls.append(kwargs)
            return NasdaqAliasSyncResult(
                inserted=True,
                aliases_inserted=1,
                sync_run_id=88,
                snapshot_id=99,
            )

    content = (
        "BDBu;Bd20260730;\n"
        "BDx;i10;SiXSTO;s1;SYmXSTO;NAmNasdaq Stockholm;"
        "CNySE;MIcXSTO;\n"
        "BDt;i101;SiVOLV-B;s1;Ex10;Mk20;INiVOLV-B;"
        "SYmVOLV B;NAmVolvo AB;ISnSE0000115446;"
        "CUiSEK;CUtSEK;LDa19840102;CFcESVUFR;PMIcXSTO;\n"
        "BDSh;i101;SiVOLV-B;s1;\n"
        "EOBd;s1;\n"
    ).encode("ascii")
    database = AliasDatabase()
    service = NasdaqReferenceSyncService(
        database=database,
        clock=lambda: datetime(
            2026,
            7,
            30,
            5,
            0,
            tzinfo=timezone.utc,
        ),
    )

    result = service.sync_file(
        content=content,
        source_path=(
            "INET_Ref_Data/20260730/Nordic_Equity_RefData.tip"
        ),
        entitlement_id=66,
    )

    assert result.snapshot_id == 99
    assert database.calls[0]["reference_snapshot_id"] == 77
    assert database.calls[0]["entitlement_id"] == 66
    assert database.calls[0]["content"] == content
    assert database.calls[0]["snapshot"].aliases[0].provider_symbol == (
        "VOLV B"
    )
