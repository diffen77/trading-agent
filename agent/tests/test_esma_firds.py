from datetime import date, datetime, timezone
import hashlib
from io import BytesIO
import json
import zipfile

import pytest

from src.data.esma_firds import (
    EsmaFirdsClient,
    EsmaFirdsFile,
    EsmaFirdsSnapshot,
    build_esma_http_session,
    parse_firds_equity_snapshot,
)
from src.data.market_data import MarketDataError


def _full_file(
    file_name: str,
    records: list[dict[str, str]],
) -> bytes:
    rows = []
    for record in records:
        termination = (
            f"<TermntnDt>{record['termination']}</TermntnDt>"
            if record.get("termination")
            else ""
        )
        rows.append(
            "<RefData>"
            "<FinInstrmGnlAttrbts>"
            f"<Id>{record['isin']}</Id>"
            f"<FullNm>{record['name']}</FullNm>"
            f"<ShrtNm>{record['short_name']}</ShrtNm>"
            f"<ClssfctnTp>{record['cfi']}</ClssfctnTp>"
            f"<NtnlCcy>{record['currency']}</NtnlCcy>"
            "</FinInstrmGnlAttrbts>"
            "<TradgVnRltdAttrbts>"
            f"<Id>{record['mic']}</Id>"
            f"<FrstTradDt>{record['first_trade']}</FrstTradDt>"
            f"{termination}"
            "</TradgVnRltdAttrbts>"
            "</RefData>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:auth.017.001.02">'
        "<FinInstrmRptgRefDataRpt>"
        f"{''.join(rows)}"
        "</FinInstrmRptgRefDataRpt>"
        "</Document>"
    ).encode()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(file_name.replace(".zip", ".xml"), xml)
    return output.getvalue()


def test_snapshot_requires_every_equity_file_part():
    first = EsmaFirdsFile(
        file_name="FULINS_E_20260725_01of02.zip",
        publication_date=date(2026, 7, 25),
        download_url=(
            "https://firds.esma.europa.eu/firds/"
            "FULINS_E_20260725_01of02.zip"
        ),
        checksum_md5="00012df51602242712805f011fdde954",
    )

    with pytest.raises(MarketDataError, match="complete"):
        EsmaFirdsSnapshot(
            publication_date=date(2026, 7, 25),
            files=(first,),
        )


def test_file_metadata_rejects_missing_or_untrusted_download_url():
    values = {
        "file_name": "FULINS_E_20260725_01of01.zip",
        "publication_date": date(2026, 7, 25),
        "checksum_md5": "0" * 32,
    }
    with pytest.raises(MarketDataError, match="URL"):
        EsmaFirdsFile(download_url=None, **values)
    with pytest.raises(MarketDataError, match="allowed"):
        EsmaFirdsFile(
            download_url=(
                "https://example.com/firds/"
                "FULINS_E_20260725_01of01.zip"
            ),
            **values,
        )


def test_parser_keeps_xsto_shares_and_excludes_etps_and_other_venues():
    file_name = "FULINS_E_20260725_01of01.zip"
    content = _full_file(
        file_name,
        [
            {
                "isin": "SE0000115446",
                "name": "Volvo AB ser. B",
                "short_name": "VOLVO/SH B",
                "cfi": "ESVUFR",
                "currency": "SEK",
                "mic": "XSTO",
                "first_trade": "1984-01-02T08:00:00Z",
            },
            {
                "isin": "US0528001094",
                "name": "Autoliv Inc. SDB",
                "short_name": "AUTOLIVINC/SDR",
                "cfi": "EDSXDR",
                "currency": "SEK",
                "mic": "XSTO",
                "first_trade": "2024-02-28T07:00:00Z",
            },
            {
                "isin": "SE0020052207",
                "name": "Virtune Crypto Top 10 Index ETP",
                "short_name": "VIRTUNE/STRUCT TRKR CTF",
                "cfi": "EYAYFI",
                "currency": "SEK",
                "mic": "XSTO",
                "first_trade": "2024-01-01T08:00:00Z",
            },
            {
                "isin": "SE0000115446",
                "name": "Volvo AB ser. B",
                "short_name": "VOLVO/SH B",
                "cfi": "ESVUFR",
                "currency": "SEK",
                "mic": "XHEL",
                "first_trade": "1984-01-02T08:00:00Z",
            },
        ],
    )
    file_record = EsmaFirdsFile(
        file_name=file_name,
        publication_date=date(2026, 7, 25),
        download_url=f"https://firds.esma.europa.eu/firds/{file_name}",
        checksum_md5=hashlib.md5(content).hexdigest(),
    )

    instruments = parse_firds_equity_snapshot(
        ((file_record, content),),
        mic="XSTO",
        received_at=datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc),
    )

    assert [instrument.isin for instrument in instruments] == [
        "SE0000115446",
        "US0528001094",
    ]
    assert instruments[0].symbol is None
    assert instruments[0].instrument_type == "COMMON_STOCK"
    assert instruments[0].first_trade_date == date(1984, 1, 2)
    assert instruments[1].instrument_type == "DEPOSITARY_RECEIPT"
    assert all(instrument.source == "esma-firds" for instrument in instruments)


def test_parser_rejects_xml_with_document_type_declaration():
    file_name = "FULINS_E_20260725_01of01.zip"
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "FULINS_E_20260725_01of01.xml",
            b'<?xml version="1.0"?><!DOCTYPE x><Document/>',
        )
    content = output.getvalue()
    file_record = EsmaFirdsFile(
        file_name=file_name,
        publication_date=date(2026, 7, 25),
        download_url=f"https://firds.esma.europa.eu/firds/{file_name}",
        checksum_md5=hashlib.md5(content).hexdigest(),
    )

    with pytest.raises(MarketDataError, match="DTD"):
        parse_firds_equity_snapshot(
            ((file_record, content),),
            mic="XSTO",
            received_at=datetime(
                2026,
                7,
                29,
                14,
                0,
                tzinfo=timezone.utc,
            ),
        )


class _Response:
    def __init__(self, content: bytes):
        self.content = content
        self.headers = {"Content-Length": str(len(content))}
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.response_objects = []

    def get(
        self,
        url,
        *,
        params=None,
        timeout,
        allow_redirects,
        stream,
    ):
        self.calls.append((url, params, timeout, allow_redirects, stream))
        response = _Response(self.responses[url])
        self.response_objects.append(response)
        return response


def test_client_discovers_latest_complete_equity_snapshot():
    api_url = (
        "https://registers.esma.europa.eu/solr/"
        "esma_registers_firds_files/select"
    )
    payload = {
        "response": {
            "numFound": 3,
            "docs": [
                {
                    "file_name": "FULINS_E_20260718_01of01.zip",
                    "file_type": "FULINS",
                    "publication_date": "2026-07-18T00:00:00Z",
                    "download_link": (
                        "https://firds.esma.europa.eu/firds/"
                        "FULINS_E_20260718_01of01.zip"
                    ),
                    "checksum": "1" * 32,
                },
                {
                    "file_name": "FULINS_E_20260725_02of02.zip",
                    "file_type": "FULINS",
                    "publication_date": "2026-07-25T00:00:00Z",
                    "download_link": (
                        "https://firds.esma.europa.eu/firds/"
                        "FULINS_E_20260725_02of02.zip"
                    ),
                    "checksum": "2" * 32,
                },
                {
                    "file_name": "FULINS_E_20260725_01of02.zip",
                    "file_type": "FULINS",
                    "publication_date": "2026-07-25T00:00:00Z",
                    "download_link": (
                        "https://firds.esma.europa.eu/firds/"
                        "FULINS_E_20260725_01of02.zip"
                    ),
                    "checksum": "3" * 32,
                },
            ],
        },
    }
    session = _Session({api_url: json.dumps(payload).encode()})
    client = EsmaFirdsClient(session=session)

    snapshot = client.latest_full_equity_snapshot(
        as_of=date(2026, 7, 29),
    )

    assert snapshot.publication_date == date(2026, 7, 25)
    assert [item.part for item in snapshot.files] == [1, 2]
    assert session.calls[0][2:] == ((5.0, 30.0), False, True)
    assert session.response_objects[0].closed


def test_client_downloads_with_checksum_and_bounded_http_settings():
    file_name = "FULINS_E_20260725_01of01.zip"
    content = _full_file(
        file_name,
        [{
            "isin": "SE0000115446",
            "name": "Volvo AB ser. B",
            "short_name": "VOLVO/SH B",
            "cfi": "ESVUFR",
            "currency": "SEK",
            "mic": "XSTO",
            "first_trade": "1984-01-02T08:00:00Z",
        }],
    )
    url = f"https://firds.esma.europa.eu/firds/{file_name}"
    file_record = EsmaFirdsFile(
        file_name=file_name,
        publication_date=date(2026, 7, 25),
        download_url=url,
        checksum_md5=hashlib.md5(content).hexdigest(),
    )
    session = _Session({url: content})
    client = EsmaFirdsClient(session=session)

    assert client.download(file_record) == content
    archive = client.download_archive(
        file_record,
        received_at=datetime(
            2026,
            7,
            29,
            14,
            0,
            tzinfo=timezone.utc,
        ),
    )
    assert archive.checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert archive.upstream_checksum_algorithm == "md5"
    assert archive.upstream_checksum == file_record.checksum_md5
    assert session.calls == [
        (url, None, (5.0, 30.0), False, True),
        (url, None, (5.0, 30.0), False, True),
    ]
    assert session.response_objects[0].closed


def test_default_esma_session_has_identification_and_bounded_retries():
    session = build_esma_http_session()
    retries = session.get_adapter("https://").max_retries

    assert session.headers["User-Agent"].startswith("TradingAgent/")
    assert retries.total == 1
    assert retries.connect == 1
    assert retries.read == 1
    assert retries.allowed_methods == frozenset({"GET"})
