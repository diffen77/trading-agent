"""Strict adapter for Nasdaq Nordic's 15-minute delayed post-trade files."""

from collections.abc import Callable, Sequence
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from io import StringIO
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from src.data.market_data import (
    InstrumentRecord,
    MarketDataError,
    QuoteRecord,
)
from src.data.nasdaq_reference import (
    NasdaqInstrumentAlias,
    trading_currencies_by_isin,
)


POST_TRADE_CATALOG_URL = (
    "https://tradereports.nasdaq.com/api/regulatory/trade-reports"
    "?type=POST_TRADE&assetClass=EQUITY"
)
_ORIGIN = "https://tradereports.nasdaq.com"
_DOWNLOAD_PATH = "/api/regulatory/trade-report/download"
_FILE_NAME_PATTERN = re.compile(
    r"^NordicEquity-posttrade-(\d{4}-\d{2}-\d{2}T\d{4})$"
)
_SOURCE = "nasdaq-nordic-delayed-post-trade"
_STOCKHOLM = ZoneInfo("Europe/Stockholm")
_MAX_PAGE_BYTES = 2_000_000
_MAX_CSV_BYTES = 20_000_000
_REQUIRED_COLUMNS = (
    "Trading date and time",
    "Instrument identification code",
    "Price",
    "Missing Price",
    "Price currency",
    "Price notation",
    "Quantity",
    "Venue of execution",
    "Trading system",
    "Publication date and time",
    "Venue of publication",
    "Transaction identification code",
    "Flags",
)


class _Response(Protocol):
    status_code: int
    headers: object

    def raise_for_status(self) -> None: ...

    def iter_content(self, chunk_size: int): ...

    def close(self) -> None: ...


class _Session(Protocol):
    def get(
        self,
        url: str,
        *,
        timeout: tuple[float, float],
        allow_redirects: bool,
    ) -> _Response: ...


@dataclass(frozen=True)
class NasdaqReportFile:
    file_name: str
    url: str
    report_minute: datetime


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def discover_post_trade_files(
    html: str,
    *,
    base_url: str = _ORIGIN,
) -> tuple[NasdaqReportFile, ...]:
    """Return validated post-trade file links, newest first."""
    if not isinstance(html, str) or not html.strip():
        raise MarketDataError("Nasdaq report page is empty")
    if len(html.encode("utf-8")) > _MAX_PAGE_BYTES:
        raise MarketDataError("Nasdaq report page exceeds size limit")

    parser = _LinkParser()
    parser.feed(html)
    files: dict[str, NasdaqReportFile] = {}

    for href in parser.hrefs:
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "tradereports.nasdaq.com"
            or parsed.path != _DOWNLOAD_PATH
            or parsed.fragment
        ):
            continue

        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"type", "assetClass", "fileName"}:
            continue
        if query["type"] != ["POST_TRADE"]:
            continue
        if query["assetClass"] != ["EQUITY"]:
            continue
        file_names = query["fileName"]
        if len(file_names) != 1:
            continue
        file_name = file_names[0]
        file_name_match = _FILE_NAME_PATTERN.fullmatch(file_name)
        if file_name_match is None:
            continue

        canonical_query = urlencode(
            (
                ("type", "POST_TRADE"),
                ("assetClass", "EQUITY"),
                ("fileName", file_name),
            )
        )
        files[file_name] = NasdaqReportFile(
            file_name=file_name,
            url=f"{_ORIGIN}{_DOWNLOAD_PATH}?{canonical_query}",
            report_minute=_parse_report_minute(file_name_match.group(1)),
        )

    return tuple(
        files[file_name]
        for file_name in sorted(files, reverse=True)
    )


def parse_post_trade_catalog(
    payload: str | bytes,
) -> tuple[NasdaqReportFile, ...]:
    """Parse Nasdaq's public JSON report catalog without trusting its order."""
    if isinstance(payload, bytes):
        if len(payload) > _MAX_PAGE_BYTES:
            raise MarketDataError("Nasdaq report catalog exceeds size limit")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MarketDataError(
                "Nasdaq report catalog is not valid UTF-8"
            ) from exc
    elif isinstance(payload, str):
        text = payload
        if len(text.encode("utf-8")) > _MAX_PAGE_BYTES:
            raise MarketDataError("Nasdaq report catalog exceeds size limit")
    else:
        raise MarketDataError("Nasdaq report catalog must be text or bytes")

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MarketDataError("Nasdaq report catalog is not valid JSON") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"message", "reports"}
        or not isinstance(document["reports"], list)
        or len(document["reports"]) > 2_000
    ):
        raise MarketDataError("Nasdaq report catalog schema does not match")

    files: dict[str, NasdaqReportFile] = {}
    for value in document["reports"]:
        if not isinstance(value, str):
            raise MarketDataError(
                "Nasdaq report catalog contains an invalid file name"
            )
        match = _FILE_NAME_PATTERN.fullmatch(value)
        if match is None or value in files:
            raise MarketDataError(
                "Nasdaq report catalog contains an invalid file name"
            )
        query = urlencode(
            (
                ("type", "POST_TRADE"),
                ("assetClass", "EQUITY"),
                ("fileName", value),
            )
        )
        files[value] = NasdaqReportFile(
            file_name=value,
            url=f"{_ORIGIN}{_DOWNLOAD_PATH}?{query}",
            report_minute=_parse_report_minute(match.group(1)),
        )

    return tuple(files[name] for name in sorted(files, reverse=True))


def parse_post_trade_csv(
    payload: str | bytes,
    *,
    received_at: datetime,
    instruments: Sequence[InstrumentRecord],
    aliases: Sequence[NasdaqInstrumentAlias],
) -> tuple[QuoteRecord, ...]:
    """Parse one Nordic equity file and keep the latest XSTO trade per ISIN."""
    _require_aware(received_at, "received_at")
    text = _decode_payload(payload)
    lines = text.splitlines()
    if lines and lines[0].strip() in {'"sep=;"', "sep=;"}:
        lines = lines[1:]
    if not lines:
        raise MarketDataError("Nasdaq CSV is empty")

    reader = csv.DictReader(StringIO("\n".join(lines)), delimiter=";")
    if tuple(reader.fieldnames or ()) != _REQUIRED_COLUMNS:
        raise MarketDataError("Nasdaq CSV schema does not match contract")

    normalized_instruments = tuple(instruments)
    trading_currencies = trading_currencies_by_isin(
        instruments=normalized_instruments,
        aliases=aliases,
    )
    allowed = {
        instrument.isin: instrument
        for instrument in normalized_instruments
    }
    latest: dict[str, QuoteRecord] = {}

    for row_number, row in enumerate(reader, start=3):
        venue = _field(row, "Venue of execution", row_number).upper()
        if venue != "XSTO":
            continue

        isin = _field(
            row,
            "Instrument identification code",
            row_number,
        ).upper()
        instrument = allowed.get(isin)
        if instrument is None:
            continue

        currency = _field(row, "Price currency", row_number).upper()
        if currency != trading_currencies[isin]:
            raise MarketDataError(
                f"Nasdaq CSV currency contradicts official alias at row {row_number}"
            )
        if _field(row, "Price notation", row_number).upper() != "MONE":
            raise MarketDataError(
                f"Nasdaq CSV price notation is unsupported at row {row_number}"
            )

        missing_price = (row.get("Missing Price") or "").strip()
        raw_price = (row.get("Price") or "").strip()
        if missing_price or not raw_price:
            continue

        event_time = _parse_timestamp(
            _field(row, "Trading date and time", row_number),
            row_number,
        )
        publication_time = _parse_timestamp(
            _field(row, "Publication date and time", row_number),
            row_number,
        )
        if publication_time < event_time or publication_time > received_at:
            raise MarketDataError(
                f"Nasdaq CSV publication timestamp is invalid at row {row_number}"
            )

        quote = QuoteRecord(
            isin=isin,
            mic=venue,
            event_time=event_time,
            received_at=received_at,
            source=_SOURCE,
            last_price=raw_price,
            currency=currency,
            volume=_field(row, "Quantity", row_number),
        )
        previous = latest.get(isin)
        if previous is None or quote.event_time >= previous.event_time:
            latest[isin] = quote

    return tuple(latest[isin] for isin in sorted(latest))


class NasdaqDelayedPostTradeProvider:
    """Fetch recent delayed files for a pre-approved instrument universe."""

    def __init__(
        self,
        *,
        instruments: Sequence[InstrumentRecord],
        aliases: Sequence[NasdaqInstrumentAlias],
        session: _Session | None = None,
        clock: Callable[[], datetime] | None = None,
        timeout_seconds: float | tuple[float, float] = (5.0, 20.0),
        max_files: int = 3,
    ) -> None:
        timeout = _normalized_timeout(timeout_seconds)
        if max_files < 1 or max_files > 10:
            raise MarketDataError("max_files must be between 1 and 10")

        normalized = tuple(instruments)
        normalized_aliases = tuple(aliases)
        trading_currencies_by_isin(
            instruments=normalized,
            aliases=normalized_aliases,
        )

        self._instruments = normalized
        self._aliases = normalized_aliases
        self._by_isin = {item.isin: item for item in normalized}
        self._alias_by_isin = {
            alias.isin.strip().upper(): alias
            for alias in normalized_aliases
        }
        self._session = session
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._timeout = timeout
        self._max_files = max_files

    def list_instruments(self) -> tuple[InstrumentRecord, ...]:
        return self._instruments


    def list_aliases(self) -> tuple[NasdaqInstrumentAlias, ...]:
        return self._aliases

    def report_files(self) -> tuple[NasdaqReportFile, ...]:
        content = self._get_bytes(
            POST_TRADE_CATALOG_URL,
            max_bytes=_MAX_PAGE_BYTES,
        )
        report_files = parse_post_trade_catalog(content)
        if not report_files:
            raise MarketDataError(
                "Nasdaq report catalog has no valid post-trade files"
            )
        return report_files

    def download_report_file(self, report_file: NasdaqReportFile) -> bytes:
        link_html = (
            f'<a href="{escape(report_file.url, quote=True)}">file</a>'
        )
        validated = discover_post_trade_files(link_html)
        if validated != (report_file,):
            raise MarketDataError("Nasdaq report file reference is invalid")
        return self._get_bytes(
            report_file.url,
            max_bytes=_MAX_CSV_BYTES,
        )

    def latest_quotes(
        self,
        instruments: Sequence[InstrumentRecord],
    ) -> tuple[QuoteRecord, ...]:
        requested = tuple(instruments)
        unknown = {
            item.isin
            for item in requested
            if self._by_isin.get(item.isin) != item
        }
        if unknown:
            raise MarketDataError("requested instrument is not configured")
        if not requested:
            return ()

        requested_aliases = tuple(
            self._alias_by_isin[item.isin]
            for item in requested
        )
        report_files = self.report_files()

        requested_isins = {item.isin for item in requested}
        latest: dict[str, QuoteRecord] = {}
        for report_file in report_files[: self._max_files]:
            content = self.download_report_file(report_file)
            received_at = self._clock()
            quotes = parse_post_trade_csv(
                content,
                received_at=received_at,
                instruments=requested,
                aliases=requested_aliases,
            )
            for quote in quotes:
                previous = latest.get(quote.isin)
                if previous is None or quote.event_time > previous.event_time:
                    latest[quote.isin] = quote
            if requested_isins.issubset(latest):
                break

        return tuple(latest[isin] for isin in sorted(latest))

    def _get_bytes(self, url: str, *, max_bytes: int) -> bytes:
        if self._session is None:
            return _download_with_curl(
                url,
                max_bytes=max_bytes,
                timeout=self._timeout,
            )
        response = None
        try:
            response = self._session.get(
                url,
                timeout=self._timeout,
                allow_redirects=False,
                stream=True,
            )
            if 300 <= response.status_code < 400:
                raise MarketDataError(
                    "Nasdaq delayed data redirect is not allowed"
                )
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_bytes = int(content_length)
                except (TypeError, ValueError) as exc:
                    raise MarketDataError(
                        "Nasdaq response has invalid Content-Length"
                    ) from exc
                if declared_bytes < 0 or declared_bytes > max_bytes:
                    raise MarketDataError(
                        "Nasdaq response exceeds size limit"
                    )

            chunks = []
            received_bytes = 0
            for chunk in response.iter_content(chunk_size=65_536):
                if not chunk:
                    continue
                received_bytes += len(chunk)
                if received_bytes > max_bytes:
                    raise MarketDataError(
                        "Nasdaq response exceeds size limit"
                    )
                chunks.append(chunk)
            return b"".join(chunks)
        except requests.RequestException as exc:
            raise MarketDataError("Nasdaq delayed data request failed") from exc
        finally:
            if response is not None:
                response.close()


def _download_with_curl(
    url: str,
    *,
    max_bytes: int,
    timeout: tuple[float, float],
    runner=subprocess.run,
) -> bytes:
    """Fetch one fixed-host Nasdaq resource with bounded plain curl."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    authorized_catalog = (
        parsed.path == "/api/regulatory/trade-reports"
        and query
        == {
            "type": ["POST_TRADE"],
            "assetClass": ["EQUITY"],
        }
    )
    authorized_download = (
        parsed.path == _DOWNLOAD_PATH
        and set(query) == {"type", "assetClass", "fileName"}
        and query["type"] == ["POST_TRADE"]
        and query["assetClass"] == ["EQUITY"]
        and len(query["fileName"]) == 1
        and _FILE_NAME_PATTERN.fullmatch(query["fileName"][0]) is not None
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "tradereports.nasdaq.com"
        or parsed.fragment
        or not (authorized_catalog or authorized_download)
    ):
        raise MarketDataError(
            "Nasdaq delayed data URL is not an authorized HTTPS resource"
        )
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < 1
        or max_bytes > _MAX_CSV_BYTES
    ):
        raise MarketDataError("Nasdaq response size limit is invalid")
    connect_timeout, read_timeout = _normalized_timeout(timeout)

    try:
        with tempfile.TemporaryDirectory(
            prefix="trading-agent-nasdaq-"
        ) as temporary_directory:
            output_path = Path(temporary_directory) / "response.bin"
            result = runner(
                [
                    "/usr/bin/curl",
                    "--silent",
                    "--show-error",
                    "--http1.1",
                    "--proto",
                    "=https",
                    "--max-redirs",
                    "0",
                    "--connect-timeout",
                    str(connect_timeout),
                    "--max-time",
                    str(connect_timeout + read_timeout),
                    "--max-filesize",
                    str(max_bytes),
                    "--user-agent",
                    "TradingAgent/1.0",
                    "--header",
                    (
                        "Accept: application/json,text/csv,"
                        "application/octet-stream;q=0.9"
                    ),
                    "--output",
                    str(output_path),
                    "--write-out",
                    "%{http_code}",
                    url,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=connect_timeout + read_timeout + 5,
                check=False,
                shell=False,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
            if result.returncode != 0 or result.stdout.strip() != b"200":
                raise MarketDataError("Nasdaq delayed data request failed")
            if not output_path.is_file():
                raise MarketDataError("Nasdaq delayed data response is missing")
            payload = output_path.read_bytes()
            if len(payload) > max_bytes:
                raise MarketDataError("Nasdaq response exceeds size limit")
            return payload
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MarketDataError("Nasdaq delayed data request failed") from exc


def build_nasdaq_http_session() -> requests.Session:
    """Create an identified session with bounded retries for safe GETs."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "TradingAgent/1.0"
            ),
            "Accept": (
                "application/json,text/csv,"
                "application/octet-stream;q=0.9,text/html;q=0.8"
            ),
        }
    )
    retry = Retry(
        total=1,
        connect=1,
        read=1,
        status=1,
        other=0,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=(429, 500, 502, 503, 504),
        backoff_factor=0.5,
        respect_retry_after_header=True,
        raise_on_status=True,
    )
    session.mount(
        "https://",
        HTTPAdapter(
            max_retries=retry,
            pool_connections=2,
            pool_maxsize=2,
        ),
    )
    return session


def _normalized_timeout(
    value: float | tuple[float, float],
) -> tuple[float, float]:
    if isinstance(value, tuple) and len(value) == 2:
        connect_timeout, read_timeout = value
    else:
        connect_timeout = value
        read_timeout = value
    try:
        normalized = (
            float(connect_timeout),
            float(read_timeout),
        )
    except (TypeError, ValueError) as exc:
        raise MarketDataError("timeout_seconds must be numeric") from exc
    if any(timeout <= 0 or timeout > 60 for timeout in normalized):
        raise MarketDataError(
            "timeout_seconds values must be greater than 0 and at most 60"
        )
    return normalized


def _parse_report_minute(value: str) -> datetime:
    try:
        local_minute = datetime.strptime(value, "%Y-%m-%dT%H%M")
    except ValueError as exc:
        raise MarketDataError("Nasdaq report file minute is invalid") from exc
    return local_minute.replace(tzinfo=_STOCKHOLM).astimezone(timezone.utc)


def _decode_payload(payload: str | bytes) -> str:
    if isinstance(payload, bytes):
        if len(payload) > _MAX_CSV_BYTES:
            raise MarketDataError("Nasdaq CSV exceeds size limit")
        try:
            return payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MarketDataError("Nasdaq CSV is not valid UTF-8") from exc
    if not isinstance(payload, str):
        raise MarketDataError("Nasdaq CSV must be text or bytes")
    if len(payload.encode("utf-8")) > _MAX_CSV_BYTES:
        raise MarketDataError("Nasdaq CSV exceeds size limit")
    return payload.lstrip("\ufeff")


def _field(row: dict[str, str | None], name: str, row_number: int) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MarketDataError(
            f"Nasdaq CSV field {name} is missing at row {row_number}"
        )
    return value.strip()


def _parse_timestamp(value: str, row_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError(
            f"Nasdaq CSV timestamp is invalid at row {row_number}"
        ) from exc
    _require_aware(parsed, "Nasdaq CSV timestamp")
    return parsed


def _require_aware(value: datetime, field: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise MarketDataError(f"{field} must be timezone-aware")
