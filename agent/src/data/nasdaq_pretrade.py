"""Strict streaming parser for Nasdaq Nordic delayed pre-trade data."""

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from itertools import chain
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo

from src.data.market_data import (
    InstrumentUniverseSnapshot,
    MarketDataError,
    TopOfBookBatch,
    TopOfBookUpdate,
    _TOP_OF_BOOK_PARSER_PROOF,
)
from src.data.nasdaq_delayed import build_nasdaq_http_session
from src.data.nasdaq_reference import (
    NasdaqInstrumentAlias,
    trading_currencies_by_isin,
)
from src.data.top_of_book import apply_top_of_book_batch


_PROVIDER = "nasdaq-nordic"
_DATA_TYPE = "delayed-pre-trade-equity"
_SOURCE = "nasdaq-nordic-delayed-pre-trade"
_MIC = "XSTO"
_SOURCE_HOST = "tradereports.nasdaq.com"
_MAX_INPUT_ROWS = 5_000_000
_MAX_XSTO_UPDATES = 250_000
_MAX_LINE_BYTES = 4_096
_POLICY_URL = (
    "https://tradereports.nasdaq.com/shares/trade-reports/pre-trade"
)
_CATALOG_URL = (
    "https://tradereports.nasdaq.com/api/regulatory/"
    "trade-reports?type=PRE_TRADE&assetClass=EQUITY"
)
_DOWNLOAD_URL = (
    "https://tradereports.nasdaq.com/api/regulatory/"
    "trade-report/download"
)
_MAX_POLICY_BYTES = 2 * 1024 * 1024
_MAX_CATALOG_BYTES = 2 * 1024 * 1024
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
_HTTP_CHUNK_BYTES = 64 * 1024
_REQUIRED_COLUMNS = (
    "Update date and time",
    "Instrument identification code",
    "Side",
    "Market Marker",
    "Price",
    "Price currency",
    "Price notation",
    "Quantity",
    "Quantity currency",
    "Aggregated number of orders and quotes",
    "Venue",
    "Trading system",
    "Trading system phase",
    "Publication Time",
)


@dataclass(frozen=True)
class NasdaqPublicPolicyEvidence:
    """Checksum and timestamp for verified public-use terms."""

    terms_url: str
    checksum_sha256: str
    verified_at: datetime


@dataclass(frozen=True)
class NasdaqPreTradeReportFile:
    """One immutable public Nasdaq pre-trade minute."""

    api_file_name: str
    file_name: str
    report_minute: datetime
    url: str


@dataclass(frozen=True)
class DownloadedPreTradeFile:
    """Ephemeral file evidence; the context owner deletes ``path``."""

    report: NasdaqPreTradeReportFile
    path: Path
    checksum_sha256: str
    received_at: datetime
    size_bytes: int


@dataclass(frozen=True)
class PublicPreTradeSyncResult:
    processed_files: int
    updates_inserted: int
    states_persisted: int


class NasdaqPublicPreTradeProvider:
    """Bounded client for Nasdaq's free delayed MiFID pre-trade files."""

    def __init__(
        self,
        *,
        session=None,
        clock=None,
        timeout_seconds: float = 30.0,
        temp_directory: str | os.PathLike[str] | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 1 <= float(timeout_seconds) <= 120
        ):
            raise MarketDataError(
                "Nasdaq pre-trade timeout must be between 1 and 120 seconds"
            )
        self._session = session or build_nasdaq_http_session()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._timeout_seconds = float(timeout_seconds)
        if temp_directory is None:
            temp_directory = tempfile.gettempdir()
        directory = Path(temp_directory)
        if not directory.is_dir():
            raise MarketDataError(
                "Nasdaq pre-trade temporary directory is unavailable"
            )
        self._temp_directory = directory

    def verify_policy_evidence(self) -> NasdaqPublicPolicyEvidence:
        response = self._session.get(
            _POLICY_URL,
            timeout=self._timeout_seconds,
            stream=True,
            allow_redirects=False,
        )
        try:
            response.raise_for_status()
            content_type = str(
                response.headers.get("Content-Type", "")
            ).split(";", 1)[0].strip().lower()
            if content_type != "text/html":
                raise MarketDataError(
                    "Nasdaq pre-trade policy has an unexpected content type"
                )
            content = _read_bounded_response(
                response,
                maximum_bytes=_MAX_POLICY_BYTES,
                label="policy",
            )
        finally:
            response.close()
        try:
            text_content = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MarketDataError(
                "Nasdaq pre-trade policy is not UTF-8"
            ) from exc
        required_phrases = (
            "time delayed at 15 minutes",
            "machine-readable format (csv)",
            "Non-commercial use of the data is free.",
        )
        if any(phrase not in text_content for phrase in required_phrases):
            raise MarketDataError(
                "Nasdaq pre-trade public policy wording is not recognized"
            )
        verified_at = self._clock()
        _require_aware(verified_at, "provider clock")
        canonical_evidence = "\n".join(
            (_POLICY_URL, *required_phrases)
        ).encode("utf-8")
        return NasdaqPublicPolicyEvidence(
            terms_url=_POLICY_URL,
            checksum_sha256=hashlib.sha256(canonical_evidence).hexdigest(),
            verified_at=verified_at,
        )

    def report_files(self) -> tuple[NasdaqPreTradeReportFile, ...]:
        response = self._session.get(
            _CATALOG_URL,
            timeout=self._timeout_seconds,
            stream=True,
            allow_redirects=False,
        )
        try:
            response.raise_for_status()
            content_type = str(
                response.headers.get("Content-Type", "")
            ).split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise MarketDataError(
                    "Nasdaq pre-trade catalog has an unexpected content type"
                )
            content = _read_bounded_response(
                response,
                maximum_bytes=_MAX_CATALOG_BYTES,
                label="catalog",
            )
        finally:
            response.close()

        reports = _decode_catalog(content)
        now = self._clock()
        _require_aware(now, "provider clock")
        oldest = now - timedelta(hours=49)
        newest = now + timedelta(minutes=5)
        result = []
        for api_file_name in reports:
            file_name = f"{api_file_name}.csv"
            report_minute = _report_minute_from_file_name(file_name)
            if report_minute < oldest or report_minute > newest:
                continue
            query = urlencode(
                {
                    "type": "PRE_TRADE",
                    "assetClass": "EQUITY",
                    "fileName": api_file_name,
                }
            )
            result.append(
                NasdaqPreTradeReportFile(
                    api_file_name=api_file_name,
                    file_name=file_name,
                    report_minute=report_minute,
                    url=f"{_DOWNLOAD_URL}?{query}",
                )
            )
        return tuple(sorted(result, key=lambda item: item.report_minute))

    @contextmanager
    def open_report_file(
        self,
        report: NasdaqPreTradeReportFile,
    ) -> Iterator[DownloadedPreTradeFile]:
        if not isinstance(report, NasdaqPreTradeReportFile):
            raise TypeError("report must be a NasdaqPreTradeReportFile")
        parsed = urlparse(report.url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != _SOURCE_HOST
            or parsed.path != "/api/regulatory/trade-report/download"
        ):
            raise MarketDataError(
                "Nasdaq pre-trade download URL is not authorized"
            )

        response = self._session.get(
            report.url,
            timeout=self._timeout_seconds,
            stream=True,
            allow_redirects=False,
            headers={
                "Referer": (
                    "https://tradereports.nasdaq.com/shares/"
                    "trade-reports/pre-trade"
                )
            },
        )
        path: Path | None = None
        try:
            response.raise_for_status()
            content_type = str(
                response.headers.get("Content-Type", "")
            ).split(";", 1)[0].strip().lower()
            if content_type != "application/octet-stream":
                raise MarketDataError(
                    "Nasdaq pre-trade file has an unexpected content type"
                )
            disposition = str(
                response.headers.get("Content-Disposition", "")
            )
            if f'filename="{report.file_name}"' not in disposition:
                raise MarketDataError(
                    "Nasdaq pre-trade file name does not match the catalog"
                )
            declared_length = response.headers.get("Content-Length")
            if declared_length is not None:
                try:
                    declared_size = int(declared_length)
                except (TypeError, ValueError) as exc:
                    raise MarketDataError(
                        "Nasdaq pre-trade file size is invalid"
                    ) from exc
                if declared_size < 1 or declared_size > _MAX_DOWNLOAD_BYTES:
                    raise MarketDataError(
                        "Nasdaq pre-trade file exceeds size limit"
                    )

            hasher = hashlib.sha256()
            size_bytes = 0
            handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="nasdaq-pretrade-",
                suffix=".csv",
                dir=self._temp_directory,
                delete=False,
            )
            path = Path(handle.name)
            os.chmod(path, 0o600)
            try:
                for chunk in response.iter_content(_HTTP_CHUNK_BYTES):
                    if not isinstance(chunk, bytes):
                        raise MarketDataError(
                            "Nasdaq pre-trade response yielded non-bytes"
                        )
                    if not chunk:
                        continue
                    size_bytes += len(chunk)
                    if size_bytes > _MAX_DOWNLOAD_BYTES:
                        raise MarketDataError(
                            "Nasdaq pre-trade file exceeds size limit"
                        )
                    hasher.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            if size_bytes == 0:
                raise MarketDataError("Nasdaq pre-trade file is empty")
            received_at = self._clock()
            _require_aware(received_at, "provider clock")
            yield DownloadedPreTradeFile(
                report=report,
                path=path,
                checksum_sha256=hasher.hexdigest(),
                received_at=received_at,
                size_bytes=size_bytes,
            )
        finally:
            response.close()
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    raise MarketDataError(
                        "Nasdaq pre-trade temporary file could not be removed"
                    ) from exc


class NasdaqPublicPreTradeSyncService:
    """Persist current public XSTO Level 1 minutes with no raw retention."""

    def __init__(
        self,
        *,
        database,
        provider: NasdaqPublicPreTradeProvider,
        contract_key: str = "nasdaq-public-pretrade-xsto-v1",
        max_files_per_run: int = 1,
        clock=None,
    ) -> None:
        if not isinstance(provider, NasdaqPublicPreTradeProvider):
            raise TypeError("provider must be NasdaqPublicPreTradeProvider")
        if (
            isinstance(max_files_per_run, bool)
            or not isinstance(max_files_per_run, int)
            or not 1 <= max_files_per_run <= 10
        ):
            raise MarketDataError(
                "max_files_per_run must be between 1 and 10"
            )
        if (
            not isinstance(contract_key, str)
            or re.fullmatch(
                r"[a-z0-9][a-z0-9-]{1,99}",
                contract_key,
            ) is None
        ):
            raise MarketDataError("contract_key has invalid format")
        self._database = database
        self._provider = provider
        self._contract_key = contract_key
        self._max_files_per_run = max_files_per_run
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> PublicPreTradeSyncResult:
        policy_evidence = self._provider.verify_policy_evidence()
        reference_snapshot_id, reference_snapshot = (
            self._database.get_latest_reference_universe(
                provider="esma-firds",
                mic="XSTO",
            )
        )
        self._database.ensure_isin_company_mappings(
            reference_snapshot_id=reference_snapshot_id,
            reference_snapshot=reference_snapshot,
        )
        self._database.ensure_public_pretrade_contract(
            policy_evidence=policy_evidence,
            contract_key=self._contract_key,
        )
        reports = self._provider.report_files()
        if not reports:
            return PublicPreTradeSyncResult(0, 0, 0)

        try:
            previous = self._database.load_latest_top_of_book_snapshot(
                contract_key=self._contract_key,
            )
        except MarketDataError as exc:
            if "provider contract is missing" not in str(exc):
                raise
            previous = None
        latest_minute = (
            previous.cursor.covered_through - timedelta(minutes=1)
            if previous is not None
            else None
        )
        sessions_by_date = {}

        def is_open_session_report(report):
            local_date = report.report_minute.astimezone(
                ZoneInfo("Europe/Stockholm")
            ).date()
            if local_date not in sessions_by_date:
                sessions_by_date[local_date] = (
                    self._database.get_market_session(_MIC, local_date)
                )
            session = sessions_by_date[local_date]
            return bool(
                session is not None
                and session.is_open(report.report_minute)
            )

        pending = tuple(
            report
            for report in reports
            if (
                (latest_minute is None or report.report_minute > latest_minute)
                and is_open_session_report(report)
            )
        )
        now = self._clock()
        _require_aware(now, "sync clock")
        stream_reset_required = False
        if latest_minute is None:
            pending = pending[-1:]
        elif (
            pending
            and (now - pending[0].report_minute).total_seconds() > 1200
        ):
            pending = tuple(
                report
                for report in pending
                if 900 <= (
                    now - report.report_minute
                ).total_seconds() <= 1200
            )[-1:]
            stream_reset_required = bool(pending)
        else:
            pending = pending[: self._max_files_per_run]
        if not pending:
            return PublicPreTradeSyncResult(0, 0, 0)

        processed_files = 0
        updates_inserted = 0
        states_persisted = 0
        for report in pending:
            with self._provider.open_report_file(report) as downloaded:
                with downloaded.path.open("rb") as handle:
                    batch = parse_pre_trade_csv_lines(
                        handle,
                        file_name=report.file_name,
                        source_url=report.url,
                        source_checksum_sha256=(
                            downloaded.checksum_sha256
                        ),
                        reference_snapshot=reference_snapshot,
                        aliases=(),
                        received_at=downloaded.received_at,
                    )
            self._database.ensure_public_pretrade_authorization(
                reference_snapshot_id=reference_snapshot_id,
                batch=batch,
                policy_evidence=policy_evidence,
                contract_key=self._contract_key,
            )
            stream_reset_id = None
            if stream_reset_required:
                stream_reset_id = (
                    self._database.authorize_top_of_book_stream_reset(
                        contract_key=self._contract_key,
                        reference_snapshot_id=reference_snapshot_id,
                        batch=batch,
                    )
                )
                reduction_base = ()
            else:
                if previous is None:
                    previous = (
                        self._database.load_latest_top_of_book_snapshot(
                            contract_key=self._contract_key,
                        )
                    )
                reduction_base = (
                    previous if previous is not None else ()
                )
            reduced = apply_top_of_book_batch(
                previous=reduction_base,
                batch=batch,
            )
            self._database.persist_top_of_book_batch(
                contract_key=self._contract_key,
                reference_snapshot_id=reference_snapshot_id,
                batch=batch,
                snapshot=reduced,
                stream_reset_id=stream_reset_id,
            )
            previous = reduced
            processed_files += 1
            updates_inserted += len(batch.updates)
            states_persisted += len(reduced.states)
        return PublicPreTradeSyncResult(
            processed_files=processed_files,
            updates_inserted=updates_inserted,
            states_persisted=states_persisted,
        )


def _read_bounded_response(response, *, maximum_bytes: int, label: str) -> bytes:
    chunks = []
    size = 0
    for chunk in response.iter_content(_HTTP_CHUNK_BYTES):
        if not isinstance(chunk, bytes):
            raise MarketDataError(
                f"Nasdaq pre-trade {label} yielded non-bytes"
            )
        if not chunk:
            continue
        size += len(chunk)
        if size > maximum_bytes:
            raise MarketDataError(
                f"Nasdaq pre-trade {label} exceeds size limit"
            )
        chunks.append(chunk)
    if size == 0:
        raise MarketDataError(f"Nasdaq pre-trade {label} is empty")
    return b"".join(chunks)


def _decode_catalog(content: bytes) -> tuple[str, ...]:
    try:
        decoded = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataError(
            "Nasdaq pre-trade catalog is invalid JSON"
        ) from exc
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"message", "reports"}
        or decoded["message"] is not None
        or not isinstance(decoded["reports"], list)
    ):
        raise MarketDataError(
            "Nasdaq pre-trade catalog schema does not match"
        )
    reports = tuple(decoded["reports"])
    pattern = re.compile(
        r"NordicEquity-pretrade-\d{4}-\d{2}-\d{2}T\d{4}"
    )
    if (
        any(
            not isinstance(item, str)
            or pattern.fullmatch(item) is None
            for item in reports
        )
        or len(reports) != len(set(reports))
    ):
        raise MarketDataError(
            "Nasdaq pre-trade catalog contains an invalid report name"
        )
    return reports


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MarketDataError(
                "Nasdaq pre-trade catalog contains duplicate keys"
            )
        result[key] = value
    return result


def parse_pre_trade_csv_lines(
    lines: Iterable[bytes],
    *,
    file_name: str,
    source_url: str,
    source_checksum_sha256: str,
    reference_snapshot: InstrumentUniverseSnapshot,
    aliases: Sequence[NasdaqInstrumentAlias],
    received_at: datetime,
) -> TopOfBookBatch:
    """Parse ordered XSTO Level 1 updates without loading the Nordic file."""
    _require_aware(received_at, "received_at")
    report_minute = _report_minute_from_file_name(file_name)
    parsed_url = urlparse(source_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != _SOURCE_HOST
    ):
        raise MarketDataError(
            "Nasdaq pre-trade source_url is not an authorized HTTPS host"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", source_checksum_sha256):
        raise MarketDataError(
            "Nasdaq pre-trade source checksum must be lowercase SHA-256"
        )
    if not isinstance(reference_snapshot, InstrumentUniverseSnapshot):
        raise MarketDataError(
            "Nasdaq pre-trade requires an instrument universe snapshot"
        )
    if reference_snapshot.mic != _MIC:
        raise MarketDataError(
            "Nasdaq pre-trade reference snapshot has the wrong MIC"
        )
    if isinstance(lines, (str, bytes)):
        raise TypeError("lines must be an iterable of raw CSV lines")

    hasher = hashlib.sha256()
    iterator = iter(_decoded_lines(lines, hasher))
    try:
        first_line = _line(next(iterator))
    except StopIteration as exc:
        raise MarketDataError("Nasdaq pre-trade CSV is empty") from exc
    if first_line.strip() in {'"sep=;"', "sep=;"}:
        try:
            first_line = _line(next(iterator))
        except StopIteration as exc:
            raise MarketDataError("Nasdaq pre-trade CSV has no header") from exc

    reader = csv.DictReader(
        chain((first_line,), iterator),
        delimiter=";",
    )
    if tuple(reader.fieldnames or ()) != _REQUIRED_COLUMNS:
        raise MarketDataError(
            "Nasdaq pre-trade CSV schema does not match contract"
        )

    allowed = {
        instrument.isin: instrument
        for instrument in reference_snapshot.instruments
    }
    trading_currencies = (
        trading_currencies_by_isin(
            instruments=reference_snapshot.instruments,
            aliases=aliases,
        )
        if aliases
        else {}
    )
    observed_currencies: dict[str, str] = {}

    updates: list[TopOfBookUpdate] = []
    input_row_count = 0
    for input_row_count, row in enumerate(reader, start=1):
        row_number = input_row_count + 2
        if input_row_count > _MAX_INPUT_ROWS:
            raise MarketDataError(
                "Nasdaq pre-trade CSV exceeds row limit"
            )
        if None in row:
            raise MarketDataError(
                f"Nasdaq pre-trade CSV has extra columns at row {row_number}"
            )

        venue = _field(row, "Venue", row_number).upper()
        if venue != _MIC:
            continue

        isin = _field(
            row,
            "Instrument identification code",
            row_number,
        ).upper()
        if isin not in allowed:
            continue

        trading_system = _optional_field(row, "Trading system").upper()
        if trading_system != "CLOB":
            continue
        trading_phase = _field(
            row,
            "Trading system phase",
            row_number,
        ).upper()

        price_currency = _field(
            row,
            "Price currency",
            row_number,
        ).upper()
        quantity_currency = _field(
            row,
            "Quantity currency",
            row_number,
        ).upper()
        expected_currency = trading_currencies.get(isin)
        if expected_currency is not None and (
            price_currency != expected_currency
            or quantity_currency != expected_currency
        ):
            raise MarketDataError(
                f"Nasdaq pre-trade CSV currency contradicts official alias at "
                f"row {row_number}"
            )
        if price_currency != quantity_currency:
            raise MarketDataError(
                f"Nasdaq pre-trade CSV currencies disagree at row {row_number}"
            )
        observed_currency = observed_currencies.setdefault(
            isin,
            price_currency,
        )
        if observed_currency != price_currency:
            raise MarketDataError(
                f"Nasdaq pre-trade CSV currency changes for an ISIN at row "
                f"{row_number}"
            )
        if _field(row, "Price notation", row_number) != "1":
            raise MarketDataError(
                f"Nasdaq pre-trade CSV price notation is unsupported at row "
                f"{row_number}"
            )
        if _optional_field(row, "Market Marker"):
            raise MarketDataError(
                f"Nasdaq pre-trade CSV Market Marker is unsupported at row "
                f"{row_number}"
            )

        raw_price = _optional_field(row, "Price")
        raw_quantity = _optional_field(row, "Quantity")
        raw_order_count = _optional_field(
            row,
            "Aggregated number of orders and quotes",
        )
        present = (
            bool(raw_price),
            bool(raw_quantity),
            bool(raw_order_count),
        )
        if any(present) and not all(present):
            raise MarketDataError(
                "price, quantity and order_count must be all present or all "
                f"empty at row {row_number}"
            )

        event_time = _parse_timestamp(
            _field(row, "Update date and time", row_number),
            row_number,
        )
        publication_time = _parse_timestamp(
            _field(row, "Publication Time", row_number),
            row_number,
        )
        if publication_time < event_time or publication_time > received_at:
            raise MarketDataError(
                f"Nasdaq pre-trade CSV publication timestamp is invalid at "
                f"row {row_number}"
            )

        updates.append(
            TopOfBookUpdate(
                isin=isin,
                mic=venue,
                event_time=event_time,
                publication_time=publication_time,
                received_at=received_at,
                source=_SOURCE,
                side=_field(row, "Side", row_number),
                currency=price_currency,
                price=(
                    _parse_decimal(raw_price, "price", row_number)
                    if raw_price
                    else None
                ),
                quantity=(
                    _parse_decimal(raw_quantity, "quantity", row_number)
                    if raw_quantity
                    else None
                ),
                order_count=(
                    _parse_order_count(raw_order_count, row_number)
                    if raw_order_count
                    else None
                ),
                source_sequence=input_row_count,
                trading_system=trading_system,
                trading_phase=trading_phase,
            )
        )
        if len(updates) > _MAX_XSTO_UPDATES:
            raise MarketDataError(
                "Nasdaq pre-trade CSV exceeds XSTO update limit"
            )

    if hasher.hexdigest() != source_checksum_sha256:
        raise MarketDataError(
            "Nasdaq pre-trade source checksum mismatch"
        )
    return TopOfBookBatch(
        provider=_PROVIDER,
        data_type=_DATA_TYPE,
        source=_SOURCE,
        mic=_MIC,
        file_name=file_name,
        source_url=source_url,
        source_checksum_sha256=source_checksum_sha256,
        reference_checksum_sha256=reference_snapshot.checksum_sha256,
        instrument_isins=reference_snapshot.instrument_isins,
        report_minute=report_minute,
        received_at=received_at,
        input_row_count=input_row_count,
        updates=tuple(updates),
        _parser_proof=_TOP_OF_BOOK_PARSER_PROOF,
    )


def _report_minute_from_file_name(file_name: object) -> datetime:
    if not isinstance(file_name, str):
        raise MarketDataError(
            "Nasdaq pre-trade file_name must be text"
        )
    match = re.fullmatch(
        r"NordicEquity-pretrade-(\d{4}-\d{2}-\d{2}T\d{4})\.csv",
        file_name,
    )
    if match is None:
        raise MarketDataError(
            "Nasdaq pre-trade file_name does not match the contract"
        )
    try:
        naive_local_minute = datetime.strptime(
            match.group(1),
            "%Y-%m-%dT%H%M",
        )
    except ValueError as exc:
        raise MarketDataError(
            "Nasdaq pre-trade file_name timestamp is invalid"
        ) from exc
    zone = ZoneInfo("Europe/Stockholm")
    candidates: set[datetime] = set()
    for fold in (0, 1):
        local_minute = naive_local_minute.replace(
            tzinfo=zone,
            fold=fold,
        )
        utc_minute = local_minute.astimezone(timezone.utc)
        round_trip = utc_minute.astimezone(zone)
        if round_trip.replace(tzinfo=None) == naive_local_minute:
            candidates.add(utc_minute)
    if len(candidates) != 1:
        raise MarketDataError(
            "Nasdaq pre-trade file_name minute is ambiguous or nonexistent"
        )
    return candidates.pop()


def _decoded_lines(
    lines: Iterable[bytes],
    hasher,
) -> Iterable[str]:
    for physical_line_number, value in enumerate(lines, start=1):
        if not isinstance(value, bytes):
            raise MarketDataError(
                "Nasdaq pre-trade CSV lines must be raw bytes"
            )
        if physical_line_number > _MAX_INPUT_ROWS + 2:
            raise MarketDataError(
                "Nasdaq pre-trade CSV exceeds physical row limit"
            )
        if not value or len(value) > _MAX_LINE_BYTES:
            raise MarketDataError(
                "Nasdaq pre-trade CSV line is empty or exceeds size limit"
            )
        content = value
        if content.endswith(b"\r\n"):
            content = content[:-2]
        elif content.endswith((b"\r", b"\n")):
            content = content[:-1]
        if b"\r" in content or b"\n" in content:
            raise MarketDataError(
                "Nasdaq pre-trade CSV chunk contains multiple physical lines"
            )
        if not content:
            raise MarketDataError(
                "Nasdaq pre-trade CSV contains a blank physical line"
            )
        hasher.update(value)
        try:
            yield value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MarketDataError(
                "Nasdaq pre-trade CSV is not valid UTF-8"
            ) from exc


def _line(value: object) -> str:
    if not isinstance(value, str):
        raise MarketDataError("Nasdaq pre-trade CSV line is invalid")
    return value.rstrip("\r\n")


def _field(
    row: dict[str | None, str | list[str] | None],
    name: str,
    row_number: int,
) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise MarketDataError(
            f"Nasdaq pre-trade CSV field {name!r} is empty at row {row_number}"
        )
    return value.strip()


def _optional_field(
    row: dict[str | None, str | list[str] | None],
    name: str,
) -> str:
    value = row.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MarketDataError(
            f"Nasdaq pre-trade CSV field {name!r} is invalid"
        )
    return value.strip()


def _parse_timestamp(value: str, row_number: int) -> datetime:
    if not value.endswith("Z"):
        raise MarketDataError(
            f"Nasdaq pre-trade CSV timestamp must be UTC at row {row_number}"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise MarketDataError(
            f"Nasdaq pre-trade CSV timestamp is invalid at row {row_number}"
        ) from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise MarketDataError(
            f"Nasdaq pre-trade CSV timestamp must be UTC at row {row_number}"
        )
    return parsed


def _parse_decimal(
    value: str,
    field: str,
    row_number: int,
) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise MarketDataError(
            f"Nasdaq pre-trade CSV {field} is invalid at row {row_number}"
        ) from exc
    if not parsed.is_finite():
        raise MarketDataError(
            f"Nasdaq pre-trade CSV {field} is invalid at row {row_number}"
        )
    return parsed


def _parse_order_count(value: str, row_number: int) -> int:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise MarketDataError(
            f"Nasdaq pre-trade CSV order count is invalid at row {row_number}"
        ) from exc
    if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral():
        raise MarketDataError(
            f"Nasdaq pre-trade CSV order count is invalid at row {row_number}"
        )
    return int(parsed)


def _require_aware(value: datetime, field: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise MarketDataError(f"{field} must be timezone-aware")
