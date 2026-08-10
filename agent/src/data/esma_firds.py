"""Strict ESMA FIRDS reference-data contracts for the XSTO universe."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
from io import BytesIO
import json
import re
from typing import BinaryIO, Sequence
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .market_data import (
    InstrumentRecord,
    MarketDataArchive,
    MarketDataError,
)


_FILE_PATTERN = re.compile(
    r"^FULINS_E_(?P<date>\d{8})_"
    r"(?P<part>\d{2})of(?P<total>\d{2})\.zip$"
)
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_INSTRUMENT_TYPE_BY_CFI_PREFIX = {
    "ES": "COMMON_STOCK",
    "EP": "PREFERRED_STOCK",
    "ED": "DEPOSITARY_RECEIPT",
}
_MAX_ZIP_BYTES = 25_000_000
_MAX_XML_BYTES = 600_000_000
_MAX_COMPRESSION_RATIO = 80
_MAX_MANIFEST_BYTES = 2_000_000
_MAX_MANIFEST_AGE = timedelta(days=10)
_HTTP_TIMEOUT = (5.0, 30.0)
_MANIFEST_URL = (
    "https://registers.esma.europa.eu/solr/"
    "esma_registers_firds_files/select"
)
_STOCKHOLM = ZoneInfo("Europe/Stockholm")


@dataclass(frozen=True)
class EsmaFirdsFile:
    file_name: str
    publication_date: date
    download_url: str
    checksum_md5: str

    def __post_init__(self) -> None:
        if not isinstance(self.file_name, str):
            raise MarketDataError("FIRDS file_name is required")
        match = _FILE_PATTERN.fullmatch(self.file_name)
        if match is None:
            raise MarketDataError("FIRDS equity file name is invalid")
        try:
            file_date = date.fromisoformat(
                datetime.strptime(match.group("date"), "%Y%m%d").date().isoformat()
            )
        except ValueError as exc:
            raise MarketDataError("FIRDS file date is invalid") from exc
        if not isinstance(self.publication_date, date):
            raise MarketDataError("FIRDS publication_date must be a date")
        if file_date != self.publication_date:
            raise MarketDataError(
                "FIRDS publication date does not match file name"
            )
        if int(match.group("part")) < 1:
            raise MarketDataError("FIRDS file part must be positive")
        if int(match.group("total")) < int(match.group("part")):
            raise MarketDataError("FIRDS file part exceeds total")

        parsed_url = urlparse(self.download_url)
        expected_path = f"/firds/{self.file_name}"
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "firds.esma.europa.eu"
            or parsed_url.port is not None
            or parsed_url.path != expected_path
            or parsed_url.params
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.username
            or parsed_url.password
        ):
            raise MarketDataError("FIRDS download URL is not allowed")
        if not isinstance(
            self.checksum_md5,
            str,
        ) or not _CHECKSUM_PATTERN.fullmatch(self.checksum_md5):
            raise MarketDataError("FIRDS MD5 checksum is invalid")

    @property
    def part(self) -> int:
        match = _FILE_PATTERN.fullmatch(self.file_name)
        assert match is not None
        return int(match.group("part"))

    @property
    def total_parts(self) -> int:
        match = _FILE_PATTERN.fullmatch(self.file_name)
        assert match is not None
        return int(match.group("total"))


@dataclass(frozen=True)
class EsmaFirdsSnapshot:
    publication_date: date
    files: tuple[EsmaFirdsFile, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.publication_date, date):
            raise MarketDataError("FIRDS publication_date must be a date")
        if not isinstance(self.files, tuple) or not self.files:
            raise MarketDataError("FIRDS snapshot requires files")
        if any(
            item.publication_date != self.publication_date
            for item in self.files
        ):
            raise MarketDataError("FIRDS snapshot dates do not match")
        totals = {item.total_parts for item in self.files}
        if len(totals) != 1:
            raise MarketDataError("FIRDS snapshot has conflicting totals")
        expected_parts = set(range(1, totals.pop() + 1))
        actual_parts = {item.part for item in self.files}
        if (
            len(actual_parts) != len(self.files)
            or actual_parts != expected_parts
        ):
            raise MarketDataError("FIRDS snapshot is not complete")
        object.__setattr__(
            self,
            "files",
            tuple(sorted(self.files, key=lambda item: item.part)),
        )


class EsmaFirdsClient:
    """Discover and download official public FIRDS equity snapshots."""

    def __init__(self, *, session: requests.Session | None = None):
        self._session = session or build_esma_http_session()

    def latest_full_equity_snapshot(
        self,
        *,
        as_of: date,
    ) -> EsmaFirdsSnapshot:
        if not isinstance(as_of, date):
            raise MarketDataError("as_of must be a date")
        start = as_of - timedelta(days=14)
        params = {
            "q": "*",
            "fq": (
                "publication_date:"
                f"[{start.isoformat()}T00:00:00Z TO "
                f"{as_of.isoformat()}T23:59:59Z]"
            ),
            "wt": "json",
            "start": "0",
            "rows": "1000",
        }
        payload = self._get_bytes(
            _MANIFEST_URL,
            params=params,
            limit=_MAX_MANIFEST_BYTES,
        )
        try:
            document = json.loads(payload)
            response = document["response"]
            docs = response["docs"]
            num_found = response["numFound"]
        except (
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as exc:
            raise MarketDataError("FIRDS manifest is invalid") from exc
        if (
            not isinstance(docs, list)
            or not isinstance(num_found, int)
            or isinstance(num_found, bool)
            or num_found < 0
            or num_found > 1000
            or len(docs) != num_found
        ):
            raise MarketDataError("FIRDS manifest is incomplete")

        candidates = []
        for item in docs:
            if (
                not isinstance(item, dict)
                or item.get("file_type") != "FULINS"
                or not isinstance(item.get("file_name"), str)
                or _FILE_PATTERN.fullmatch(item["file_name"]) is None
            ):
                continue
            publication_text = item.get("publication_date")
            if not isinstance(publication_text, str):
                raise MarketDataError("FIRDS publication date is invalid")
            try:
                publication_date = date.fromisoformat(
                    publication_text[:10],
                )
            except ValueError as exc:
                raise MarketDataError(
                    "FIRDS publication date is invalid"
                ) from exc
            candidates.append(
                EsmaFirdsFile(
                    file_name=item["file_name"],
                    publication_date=publication_date,
                    download_url=item.get("download_link"),
                    checksum_md5=item.get("checksum"),
                )
            )
        if not candidates:
            raise MarketDataError("FIRDS manifest has no equity full files")

        latest_date = max(item.publication_date for item in candidates)
        if as_of - latest_date > _MAX_MANIFEST_AGE:
            raise MarketDataError("latest FIRDS equity snapshot is stale")
        return EsmaFirdsSnapshot(
            publication_date=latest_date,
            files=tuple(
                item
                for item in candidates
                if item.publication_date == latest_date
            ),
        )

    def download(self, file_record: EsmaFirdsFile) -> bytes:
        if not isinstance(file_record, EsmaFirdsFile):
            raise MarketDataError("file_record must be EsmaFirdsFile")
        content = self._get_bytes(
            file_record.download_url,
            params=None,
            limit=_MAX_ZIP_BYTES,
        )
        _validate_download(file_record, content)
        return content

    def download_archive(
        self,
        file_record: EsmaFirdsFile,
        *,
        received_at: datetime,
    ) -> MarketDataArchive:
        content = self.download(file_record)
        return archive_firds_download(
            file_record,
            content,
            received_at=received_at,
        )

    def _get_bytes(
        self,
        url: str,
        *,
        params: dict[str, str] | None,
        limit: int,
    ) -> bytes:
        try:
            response = self._session.get(
                url,
                params=params,
                timeout=_HTTP_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            try:
                response.raise_for_status()
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise MarketDataError(
                            "HTTP Content-Length is invalid"
                        ) from exc
                    if declared_size < 0 or declared_size > limit:
                        raise MarketDataError(
                            "FIRDS response exceeds size limit"
                        )
                output = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    output.extend(chunk)
                    if len(output) > limit:
                        raise MarketDataError(
                            "FIRDS response exceeds size limit"
                        )
                if not output:
                    raise MarketDataError("FIRDS response is empty")
                return bytes(output)
            finally:
                response.close()
        except MarketDataError:
            raise
        except requests.RequestException as exc:
            raise MarketDataError("FIRDS request failed") from exc


def build_esma_http_session() -> requests.Session:
    """Create a bounded, identified HTTP session for safe ESMA GETs."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "TradingAgent/0.1 "
            "(https://github.com/diffen77/trading-agent)"
        ),
        "Accept": "application/json, application/octet-stream",
    })
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        other=0,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=(429, 500, 502, 503, 504),
        backoff_factor=0.5,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def archive_firds_download(
    file_record: EsmaFirdsFile,
    content: bytes,
    *,
    received_at: datetime,
) -> MarketDataArchive:
    _validate_download(file_record, content)
    return MarketDataArchive.from_bytes(
        provider="esma-firds",
        data_type="full-equity-reference",
        file_name=file_record.file_name,
        report_minute=datetime.combine(
            file_record.publication_date,
            time.min,
            timezone.utc,
        ),
        received_at=received_at,
        source_url=file_record.download_url,
        content=content,
        upstream_checksum_algorithm="md5",
        upstream_checksum=file_record.checksum_md5,
    )


def parse_firds_equity_snapshot(
    downloaded_files: Sequence[tuple[EsmaFirdsFile, bytes]],
    *,
    mic: str,
    received_at: datetime,
) -> tuple[InstrumentRecord, ...]:
    """Parse a complete FIRDS equity snapshot without loading XML into memory."""
    if (
        received_at.tzinfo is None
        or received_at.utcoffset() is None
    ):
        raise MarketDataError("received_at must be timezone-aware")
    if not re.fullmatch(r"[A-Z0-9]{4}", mic):
        raise MarketDataError("mic must be a four-character MIC")
    if not downloaded_files:
        raise MarketDataError("FIRDS snapshot requires downloaded files")

    snapshot = EsmaFirdsSnapshot(
        publication_date=downloaded_files[0][0].publication_date,
        files=tuple(item[0] for item in downloaded_files),
    )
    if received_at.date() < snapshot.publication_date:
        raise MarketDataError("received_at precedes FIRDS publication")

    instruments: dict[str, InstrumentRecord] = {}
    content_by_name = {
        file_record.file_name: content
        for file_record, content in downloaded_files
    }
    if len(content_by_name) != len(downloaded_files):
        raise MarketDataError("FIRDS snapshot contains duplicate files")

    for file_record in snapshot.files:
        content = content_by_name[file_record.file_name]
        _validate_download(file_record, content)
        for instrument in _parse_zip_file(
            file_record,
            content,
            mic=mic,
            snapshot_date=snapshot.publication_date,
        ):
            existing = instruments.get(instrument.isin)
            if existing is not None and existing != instrument:
                raise MarketDataError(
                    f"conflicting FIRDS records for {instrument.isin}"
                )
            instruments[instrument.isin] = instrument

    if not instruments:
        raise MarketDataError(f"FIRDS snapshot contains no {mic} shares")
    return tuple(instruments[isin] for isin in sorted(instruments))


def _validate_download(file_record: EsmaFirdsFile, content: bytes) -> None:
    if not isinstance(content, bytes) or not content:
        raise MarketDataError("FIRDS download must be non-empty bytes")
    if len(content) > _MAX_ZIP_BYTES:
        raise MarketDataError("FIRDS ZIP exceeds size limit")
    checksum = hashlib.md5(content, usedforsecurity=False).hexdigest()
    if checksum != file_record.checksum_md5:
        raise MarketDataError("FIRDS download checksum mismatch")


def _parse_zip_file(
    file_record: EsmaFirdsFile,
    content: bytes,
    *,
    mic: str,
    snapshot_date: date,
) -> tuple[InstrumentRecord, ...]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            expected_name = file_record.file_name.removesuffix(
                ".zip"
            ) + ".xml"
            if (
                len(members) != 1
                or members[0].filename != expected_name
                or "/" in members[0].filename
                or "\\" in members[0].filename
            ):
                raise MarketDataError(
                    "FIRDS ZIP must contain exactly its named XML file"
                )
            member = members[0]
            if member.flag_bits & 0x1:
                raise MarketDataError("encrypted FIRDS ZIP is not supported")
            if member.file_size <= 0 or member.file_size > _MAX_XML_BYTES:
                raise MarketDataError("FIRDS XML exceeds size limit")
            compressed_size = max(member.compress_size, 1)
            if member.file_size / compressed_size > _MAX_COMPRESSION_RATIO:
                raise MarketDataError(
                    "FIRDS ZIP compression ratio exceeds limit"
                )
            with archive.open(member) as xml_stream:
                return _parse_xml(
                    _RejectDtdReader(xml_stream),
                    mic=mic,
                    snapshot_date=snapshot_date,
                )
    except MarketDataError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise MarketDataError("FIRDS download is not a valid ZIP") from exc


def _parse_xml(
    stream: BinaryIO,
    *,
    mic: str,
    snapshot_date: date,
) -> tuple[InstrumentRecord, ...]:
    instruments = []
    try:
        for _, element in ET.iterparse(stream, events=("end",)):
            if _local_name(element.tag) != "RefData":
                continue
            general = _direct_child(element, "FinInstrmGnlAttrbts")
            venue = _direct_child(element, "TradgVnRltdAttrbts")
            if general is None or venue is None:
                element.clear()
                continue
            venue_mic = _child_text(venue, "Id")
            cfi_code = _child_text(general, "ClssfctnTp")
            instrument_type = _INSTRUMENT_TYPE_BY_CFI_PREFIX.get(
                cfi_code[:2],
            )
            if venue_mic == mic and instrument_type is not None:
                first_trade_date = _local_date(
                    _child_text(venue, "FrstTradDt"),
                    field="FrstTradDt",
                )
                termination_text = _optional_child_text(
                    venue,
                    "TermntnDt",
                )
                last_trade_date = (
                    _local_date(termination_text, field="TermntnDt")
                    if termination_text is not None
                    else None
                )
                instruments.append(
                    InstrumentRecord(
                        isin=_child_text(general, "Id"),
                        symbol=None,
                        name=_child_text(general, "FullNm"),
                        mic=venue_mic,
                        currency=_child_text(general, "NtnlCcy"),
                        instrument_type=instrument_type,
                        source="esma-firds",
                        primary_listing=True,
                        status=(
                            "DELISTED"
                            if last_trade_date is not None
                            and last_trade_date < snapshot_date
                            else "ACTIVE"
                        ),
                        first_trade_date=first_trade_date,
                        last_trade_date=last_trade_date,
                        cfi_code=cfi_code,
                    )
                )
            element.clear()
    except MarketDataError:
        raise
    except ET.ParseError as exc:
        raise MarketDataError("FIRDS XML is malformed") from exc
    return tuple(instruments)


def _direct_child(
    parent: ET.Element,
    name: str,
) -> ET.Element | None:
    return next(
        (
            child
            for child in parent
            if _local_name(child.tag) == name
        ),
        None,
    )


def _child_text(parent: ET.Element, name: str) -> str:
    value = _optional_child_text(parent, name)
    if value is None:
        raise MarketDataError(f"FIRDS field {name} is required")
    return value


def _optional_child_text(
    parent: ET.Element,
    name: str,
) -> str | None:
    for child in parent.iter():
        if _local_name(child.tag) == name:
            if child.text is None or not child.text.strip():
                return None
            return child.text.strip()
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _local_date(value: str, *, field: str) -> date:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError(f"FIRDS field {field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MarketDataError(f"FIRDS field {field} lacks timezone")
    return parsed.astimezone(_STOCKHOLM).date()


class _RejectDtdReader:
    """Reject entity-capable XML declarations before Expat can consume them."""

    def __init__(self, raw: BinaryIO):
        self._raw = raw
        self._tail = b""

    def read(self, size: int = -1) -> bytes:
        chunk = self._raw.read(size)
        probe = (self._tail + chunk).upper()
        if b"<!DOCTYPE" in probe or b"<!ENTITY" in probe:
            raise MarketDataError("FIRDS XML DTD and entities are forbidden")
        self._tail = probe[-16:]
        return chunk
