"""Atomic synchronization of the official ESMA XSTO share universe."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Protocol, Sequence
from zoneinfo import ZoneInfo

from .esma_firds import (
    EsmaFirdsClient,
    EsmaFirdsFile,
    EsmaFirdsSnapshot,
    archive_firds_download,
    parse_firds_equity_snapshot,
)
from .market_data import (
    InstrumentRecord,
    InstrumentUniverseSnapshot,
    MarketDataArchive,
    MarketDataError,
    ReferenceSyncResult,
)
from .nasdaq_reference import (
    NasdaqAliasSyncResult,
    parse_nasdaq_reference_file,
)


_STOCKHOLM = ZoneInfo("Europe/Stockholm")


class _ReferenceDatabase(Protocol):
    def get_latest_reference_snapshot_date(
        self,
        *,
        provider: str,
        mic: str,
    ) -> date | None: ...

    def sync_instrument_snapshot(
        self,
        *,
        provider: str,
        mic: str,
        snapshot_date: date,
        received_at: datetime,
        archives: Sequence[MarketDataArchive],
        instruments: Sequence[InstrumentRecord],
    ) -> ReferenceSyncResult: ...


class _FirdsClient(Protocol):
    def latest_full_equity_snapshot(
        self,
        *,
        as_of: date,
    ) -> EsmaFirdsSnapshot: ...

    def download(self, file_record: EsmaFirdsFile) -> bytes: ...


class _NasdaqAliasDatabase(Protocol):
    def get_latest_reference_universe(
        self,
        *,
        provider: str,
        mic: str,
    ) -> tuple[int, InstrumentUniverseSnapshot]: ...

    def sync_instrument_alias_snapshot(
        self,
        *,
        entitlement_id: int,
        reference_snapshot_id: int,
        snapshot,
        content: bytes,
    ) -> NasdaqAliasSyncResult: ...


@dataclass(frozen=True)
class UniverseSyncResult:
    snapshot_date: date
    inserted: bool
    downloaded_files: int
    instrument_count: int
    instruments_deactivated: int
    sync_run_id: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_date, date):
            raise MarketDataError("snapshot_date must be a date")
        if not isinstance(self.inserted, bool):
            raise MarketDataError("inserted must be boolean")
        for field_name in (
            "downloaded_files",
            "instrument_count",
            "instruments_deactivated",
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise MarketDataError(f"{field_name} cannot be negative")
        if self.sync_run_id is not None and (
            not isinstance(self.sync_run_id, int)
            or isinstance(self.sync_run_id, bool)
            or self.sync_run_id <= 0
        ):
            raise MarketDataError("sync_run_id must be positive")


class EsmaReferenceSyncService:
    """Fetch, validate, archive, and atomically apply one FIRDS snapshot."""

    def __init__(
        self,
        *,
        database: _ReferenceDatabase,
        client: _FirdsClient | None = None,
        clock: Callable[[], datetime] | None = None,
        minimum_instruments: int = 300,
        maximum_instruments: int = 1000,
    ):
        if (
            not isinstance(minimum_instruments, int)
            or isinstance(minimum_instruments, bool)
            or minimum_instruments < 1
        ):
            raise MarketDataError("minimum_instruments must be positive")
        if (
            not isinstance(maximum_instruments, int)
            or isinstance(maximum_instruments, bool)
            or maximum_instruments < minimum_instruments
        ):
            raise MarketDataError(
                "maximum_instruments must cover the minimum"
            )
        self._database = database
        self._client = client or EsmaFirdsClient()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._minimum_instruments = minimum_instruments
        self._maximum_instruments = maximum_instruments

    def sync(self) -> UniverseSyncResult:
        received_at = self._clock()
        if (
            not isinstance(received_at, datetime)
            or received_at.tzinfo is None
            or received_at.utcoffset() is None
        ):
            raise MarketDataError("sync clock must be timezone-aware")
        snapshot = self._client.latest_full_equity_snapshot(
            as_of=received_at.astimezone(_STOCKHOLM).date(),
        )
        stored_date = self._database.get_latest_reference_snapshot_date(
            provider="esma-firds",
            mic="XSTO",
        )
        if stored_date is not None and stored_date > snapshot.publication_date:
            raise MarketDataError(
                "stored reference snapshot is newer than ESMA manifest"
            )
        if stored_date == snapshot.publication_date:
            return UniverseSyncResult(
                snapshot_date=snapshot.publication_date,
                inserted=False,
                downloaded_files=0,
                instrument_count=0,
                instruments_deactivated=0,
                sync_run_id=None,
            )

        downloaded = tuple(
            (file_record, self._client.download(file_record))
            for file_record in snapshot.files
        )
        instruments = parse_firds_equity_snapshot(
            downloaded,
            mic="XSTO",
            received_at=received_at,
        )
        if len(instruments) < self._minimum_instruments:
            raise MarketDataError(
                "FIRDS XSTO snapshot contains too few instruments"
            )
        if len(instruments) > self._maximum_instruments:
            raise MarketDataError(
                "FIRDS XSTO snapshot contains too many instruments"
            )
        archives = tuple(
            archive_firds_download(
                file_record,
                content,
                received_at=received_at,
            )
            for file_record, content in downloaded
        )
        database_result = self._database.sync_instrument_snapshot(
            provider="esma-firds",
            mic="XSTO",
            snapshot_date=snapshot.publication_date,
            received_at=received_at,
            archives=archives,
            instruments=instruments,
        )
        return UniverseSyncResult(
            snapshot_date=snapshot.publication_date,
            inserted=database_result.inserted,
            downloaded_files=len(downloaded),
            instrument_count=len(instruments),
            instruments_deactivated=(
                database_result.instruments_deactivated
            ),
            sync_run_id=database_result.sync_run_id,
        )


class NasdaqReferenceSyncService:
    """Validate and activate one entitlement-delivered Nasdaq TIP file."""

    def __init__(
        self,
        *,
        database: _NasdaqAliasDatabase,
        clock: Callable[[], datetime] | None = None,
        provider: str = "nasdaq-nordic",
        tip_version: str = "3.10.17.1",
    ) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._provider = provider
        self._tip_version = tip_version

    def sync_file(
        self,
        *,
        content: bytes,
        source_path: str,
        entitlement_id: int,
    ) -> NasdaqAliasSyncResult:
        received_at = self._clock()
        if (
            not isinstance(received_at, datetime)
            or received_at.tzinfo is None
            or received_at.utcoffset() is None
        ):
            raise MarketDataError("sync clock must be timezone-aware")
        reference_snapshot_id, reference_snapshot = (
            self._database.get_latest_reference_universe(
                provider="esma-firds",
                mic="XSTO",
            )
        )
        snapshot = parse_nasdaq_reference_file(
            content,
            source_path=source_path,
            received_at=received_at,
            reference_snapshot=reference_snapshot,
            provider=self._provider,
            tip_version=self._tip_version,
        )
        return self._database.sync_instrument_alias_snapshot(
            entitlement_id=entitlement_id,
            reference_snapshot_id=reference_snapshot_id,
            snapshot=snapshot,
            content=content,
        )
