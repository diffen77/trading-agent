"""Idempotent ingestion orchestration for Nasdaq delayed post-trade files."""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from src.data.market_data import (
    FileIngestionResult,
    InstrumentRecord,
    MarketDataArchive,
    MarketDataError,
    MarketDataGap,
    QuoteRecord,
    SyncCycleResult,
)
from src.data.nasdaq_delayed import (
    NasdaqReportFile,
    parse_post_trade_csv,
)
from src.data.nasdaq_reference import NasdaqInstrumentAlias


_PROVIDER = "nasdaq-nordic"
_DATA_TYPE = "delayed-post-trade-equity"
_DEFAULT_CONTRACT_KEY = "nasdaq-nordic-delayed-xsto-v1"
_STOCKHOLM = ZoneInfo("Europe/Stockholm")


class _Provider(Protocol):
    def list_instruments(self) -> Sequence[InstrumentRecord]: ...

    def list_aliases(self) -> Sequence[NasdaqInstrumentAlias]: ...

    def report_files(self) -> Sequence[NasdaqReportFile]: ...

    def download_report_file(self, report_file: NasdaqReportFile) -> bytes: ...


class _Database(Protocol):
    def get_latest_data_file_report_minute(
        self,
        *,
        provider: str,
        data_type: str,
    ) -> datetime | None: ...

    def ingest_market_data_file(
        self,
        archive: MarketDataArchive,
        quotes: Sequence[QuoteRecord],
        gaps: Sequence[MarketDataGap],
    ) -> FileIngestionResult: ...

    def record_data_sync_failure(
        self,
        *,
        provider: str,
        data_type: str,
        started_at: datetime,
        finished_at: datetime,
        error_message: str,
    ) -> int: ...


class NasdaqDelayedIngestionService:
    """Download and atomically persist only unseen report minutes."""

    def __init__(
        self,
        *,
        database: _Database,
        provider: _Provider,
        clock: Callable[[], datetime] | None = None,
        max_files_per_run: int = 10,
        contract_key: str = _DEFAULT_CONTRACT_KEY,
    ) -> None:
        if (
            not isinstance(max_files_per_run, int)
            or isinstance(max_files_per_run, bool)
            or not 1 <= max_files_per_run <= 120
        ):
            raise MarketDataError(
                "max_files_per_run must be between 1 and 120"
            )
        self._database = database
        self._provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_files_per_run = max_files_per_run
        if (
            not isinstance(contract_key, str)
            or not contract_key.strip()
        ):
            raise MarketDataError("contract_key is required")
        self._contract_key = contract_key.strip().lower()

    def run_once(self) -> SyncCycleResult:
        started_at = self._clock()
        try:
            return self._run_once()
        except Exception as exc:
            error_message = (
                str(exc).strip()
                if isinstance(exc, MarketDataError)
                else f"Unexpected {type(exc).__name__}"
            )
            try:
                self._database.record_data_sync_failure(
                    provider=_PROVIDER,
                    data_type=_DATA_TYPE,
                    started_at=started_at,
                    finished_at=self._clock(),
                    error_message=error_message[:500],
                )
            except Exception as audit_error:
                raise MarketDataError(
                    "Nasdaq sync failed and could not be audited"
                ) from audit_error
            raise

    def _run_once(self) -> SyncCycleResult:
        instruments = tuple(self._provider.list_instruments())
        if not instruments:
            raise MarketDataError(
                "Nasdaq ingestion requires a configured instrument universe"
            )
        aliases = tuple(self._provider.list_aliases())

        available = tuple(self._provider.report_files())
        latest = self._database.get_latest_data_file_report_minute(
            provider=_PROVIDER,
            data_type=_DATA_TYPE,
        )
        pending = _select_pending_files(
            available,
            latest=latest,
            limit=self._max_files_per_run,
        )
        if not pending:
            return SyncCycleResult(
                processed_files=0,
                inserted_quotes=0,
                duplicate_files=0,
                gaps_detected=0,
            )

        gaps = _detect_gaps(latest=latest, pending=pending)
        gaps_by_next_minute = {
            gap.gap_end + timedelta(minutes=1): gap
            for gap in gaps
        }
        inserted_quotes = 0
        duplicate_files = 0

        for report_file in pending:
            content = self._provider.download_report_file(report_file)
            received_at = self._clock()
            archive = MarketDataArchive.from_bytes(
                provider=_PROVIDER,
                data_type=_DATA_TYPE,
                file_name=report_file.file_name,
                report_minute=report_file.report_minute,
                received_at=received_at,
                source_url=report_file.url,
                content=content,
                provider_contract_key=self._contract_key,
            )
            quotes = parse_post_trade_csv(
                content,
                received_at=received_at,
                instruments=instruments,
                aliases=aliases,
            )
            result = self._database.ingest_market_data_file(
                archive,
                quotes,
                tuple(
                    gap
                    for next_minute, gap in gaps_by_next_minute.items()
                    if next_minute == report_file.report_minute
                ),
            )
            inserted_quotes += result.quotes_inserted
            if not result.inserted:
                duplicate_files += 1

        return SyncCycleResult(
            processed_files=len(pending),
            inserted_quotes=inserted_quotes,
            duplicate_files=duplicate_files,
            gaps_detected=len(gaps),
        )


def _select_pending_files(
    available: Sequence[NasdaqReportFile],
    *,
    latest: datetime | None,
    limit: int,
) -> tuple[NasdaqReportFile, ...]:
    ordered = sorted(available, key=lambda item: item.report_minute)
    if latest is None:
        return tuple(ordered[-limit:])
    _require_aware(latest)
    return tuple(
        item
        for item in ordered
        if item.report_minute > latest
    )[:limit]


def _detect_gaps(
    *,
    latest: datetime | None,
    pending: Sequence[NasdaqReportFile],
) -> tuple[MarketDataGap, ...]:
    if latest is None:
        return ()

    previous = latest
    gaps = []
    for report_file in pending:
        expected = previous + timedelta(minutes=1)
        same_session_date = (
            previous.astimezone(_STOCKHOLM).date()
            == report_file.report_minute.astimezone(_STOCKHOLM).date()
        )
        if same_session_date and report_file.report_minute > expected:
            gaps.append(
                MarketDataGap(
                    provider=_PROVIDER,
                    data_type=_DATA_TYPE,
                    gap_start=expected,
                    gap_end=report_file.report_minute - timedelta(minutes=1),
                    reason="missing report minute on Nasdaq report page",
                )
            )
        previous = report_file.report_minute
    return tuple(gaps)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError(
            "latest report minute must be timezone-aware"
        )
